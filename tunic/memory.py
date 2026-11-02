"""Agent-facing memory sessions sharing one database across processes.

A Memory is a thin, explicit wrapper over GraphStore and the compiler
queries. Sessions are separate Memory instances (possibly in separate
processes) over the same database path: whatever one session ingests and
records, another session retrieves. Nothing is inferred; every write is a
timestamped file append or an explicit claim block awaiting ingest.
"""

from datetime import datetime, timezone
import json
import platform
import subprocess
from pathlib import Path

from . import compiler
from .baselines import BM25, TextNotes
from .extract import CLAIM_RELATIONS, is_iso_date
from .identity import digest as _digest
from .store import GraphStore

FRONTIER_KINDS = ("unresolved", "novel", "contradictory", "unsupported", "irrelevant")
FRONTIER_BASE_PRIORITY = {"contradictory": 0.6, "unsupported": 0.5, "novel": 0.4,
                          "unresolved": 0.3, "irrelevant": 0.05}


def _dedup_dicts(rows):
    """Collapse byte-identical rows (every field equal): the same evidence
    discovered twice. Rows differing in any field -- authority, status,
    spans -- are distinct sources and survive."""
    unique, markers = [], set()
    for row in rows:
        marker = json.dumps(row, sort_keys=True, default=str)
        if marker not in markers:
            markers.add(marker)
            unique.append(row)
    return unique


def _container_of(symbols_by_key, key):
    """Verified containing class of a method symbol, else None.

    A dotted symbol whose dotted prefix resolves to a class symbol is a
    method of that class (e.g. shop.py::Gadget.start lives in
    shop.py::Gadget). Module functions, unknown prefixes, and missing
    class symbols yield None. Single definition shared by retrieval-time
    container expansion and candidate annotation.
    """
    path, _, name = key.partition("::")
    if "." not in name:
        return None
    container_key = "%s::%s" % (path, name.rsplit(".", 1)[0])
    if symbols_by_key.get(container_key, {}).get("kind") == "class":
        return container_key
    return None


def authorize(principal, action):
    """Single-principal gate: the explicit authorization boundary.

    Only the local principal (or an unset one, which defaults to local)
    may act. Anything else is denied loudly instead of silently scoped.
    Multi-principal operation stays out of scope; this interface is the
    seam it would plug into.
    """
    if principal in (None, "local"):
        return {"principal": "local", "action": action, "decision": "allow"}
    raise PermissionError("denied: unknown principal %r for %s" % (principal, action))


def _dispatch_covering(view, table, as_of):
    """Split registry candidates for one table into covered/uncovered.

    Returns (candidates, covering) where candidates are the proposed
    registry_dispatch assertions for the table and covering is the subset
    backed by a live asserted_in_source depends_on edge with the same
    (caller, callee). Liveness uses the compiler's current-policy
    arithmetic: asserted supports minus asserted retractions must be at
    least one, honoring effective dates. Shared by live replay and
    regression replay so the two paths cannot disagree on what counts as
    resolved — a resolved-then-retracted edge must fail, not pass.
    """
    assertions = view.get("assertions", [])
    candidates = [a for a in assertions
                  if (a.get("proposition", {}).get("via") == "registry_dispatch"
                      and (a.get("derivation") or {}).get("table") == table)]
    supports, support_ids, retracted = {}, {}, {}
    for a in assertions:
        if a.get("status") != "asserted_in_source":
            continue
        proposition = a.get("proposition", {})
        if (proposition.get("effective") or as_of) > as_of:
            continue
        if proposition.get("relation") == "depends_on":
            key = (proposition.get("caller"), proposition.get("callee"))
            supports[key] = supports.get(key, 0) + 1
            support_ids.setdefault(key, []).append(a.get("id"))
        elif proposition.get("relation") == "retracts":
            key = (proposition.get("retract"), proposition.get("caller"),
                   proposition.get("callee"))
            retracted[key] = retracted.get(key, 0) + 1
    covering = []
    for c in candidates:
        proposition = c.get("proposition", {})
        pair = (proposition.get("caller"), proposition.get("callee"))
        if supports.get(pair, 0) - retracted.get(("depends_on",) + pair, 0) >= 1:
            covering.append(dict(c, covering_assertion=support_ids[pair]))
    return candidates, covering


PROOF_FIELDS = ("snapshot", "assertions", "spans", "tests", "reviewer", "note")


def _export_environment():
    """Best-effort provenance for regression cases: interpreter plus repo.

    Returns {"python": ..., "tunic_commit": ...}; unknown values are None
    rather than errors, so export works from installed copies and scratch
    checkouts alike. This records where the case was produced, not a
    reproducibility guarantee — rerunning needs the same corpus revision
    (see the closures README).
    """
    commit = None
    try:
        here = Path(__file__).resolve().parents[1]
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            cwd=str(here), timeout=10)
        commit = out.stdout.strip() or None
    except Exception:
        commit = None
    return {"python": platform.python_version(), "tunic_commit": commit}


def replay_regression_case(memory, case, config_override=None):
    """Re-evaluate an exported frontier regression case against live state.

    Returns (passed, detail). Predicates mirror frontier_replay rules:
    answer_nonempty re-runs the recorded question, dispatch_covered
    requires an asserted edge over a registry candidate for the table,
    symbols_present requires symbols at the recorded path, target_rank_1
    requires the verified target to rank first under the recorded
    retrieval config, candidate_present requires a live proposed
    depends_on candidate with the recorded (via, caller, callee) — the
    fail-before/pass-after shape for extraction-gap closures. config_override replaces that config for
    break-testing in scratch environments only. Unknown predicate types
    and malformed cases raise; a resolved-then-retracted evidence set
    must fail, not pass vacuously.
    """
    predicate = (case or {}).get("predicate") or {}
    kind = predicate.get("type")
    if kind == "answer_nonempty":
        question = predicate.get("question")
        if not question:
            raise ValueError("answer_nonempty predicate needs a question")
        outcome = memory.answer(question).get("outcome")
        return outcome != "empty", "question %r -> %s" % (question, outcome)
    if kind == "target_rank_1":
        question, target = predicate.get("question"), predicate.get("target")
        if not question or not target:
            raise ValueError("target_rank_1 predicate needs a question and target")
        config = (dict(config_override) if config_override is not None
                  else dict((case or {}).get("config") or {}))
        rank, recall, rr, _top = Memory._miss_rank(
            memory, question, target, config)
        return rank == 1, ("target %s ranks %s (recall@10 %.1f, RR %.3f)"
                           % (target, rank, recall, rr))
    snapshot, view = memory.store.view()
    if kind == "dispatch_covered":
        table = predicate.get("table")
        if not table:
            raise ValueError("dispatch_covered predicate needs a table")
        as_of = memory.store.committed_on(snapshot)
        _candidates, covering = _dispatch_covering(view, table, as_of)
        pairs = sorted({(c.get("proposition", {}).get("caller"),
                         c.get("proposition", {}).get("callee")) for c in covering})
        return bool(covering), "table %s covered by %s" % (table, pairs)
    if kind == "symbols_present":
        path = predicate.get("path")
        if not path:
            raise ValueError("symbols_present predicate needs a path")
        found = sorted({s.get("key") for s in view.get("symbols", [])
                        if s.get("path") == path})
        return bool(found), "path %s symbols %s" % (path, found)
    if kind == "candidate_present":
        via = predicate.get("via")
        caller = predicate.get("caller")
        callee = predicate.get("callee")
        if not via or not caller or not callee:
            raise ValueError("candidate_present predicate needs via, caller, callee")
        matches = [a for a in view.get("assertions", [])
                   if a.get("status") == "proposed"
                   and (a.get("proposition") or {}).get("relation") == "depends_on"
                   and a.get("proposition", {}).get("via") == via
                   and a.get("proposition", {}).get("caller") == caller
                   and a.get("proposition", {}).get("callee") == callee]
        return bool(matches), "%s -> %s via %s: %d proposed candidate(s)" % (
            caller, callee, via, len(matches))
    raise ValueError("unknown regression predicate: %r" % (kind,))


class Memory:
    def __init__(self, db, root=".", principal="local"):
        self.root = Path(root)
        self.store = GraphStore(db)
        self.principal = principal
        self._discovery = None
        self._discovery_key = None

    def _ensure_discovery(self, snapshot):
        """Cached code + notes indexes; rebuilt only when the snapshot or
        any source file changes. A stat walk is far cheaper than re-parsing
        1600+ files per answer() call, and the key guarantees the cached
        indexes reflect exactly what fresh builds would see."""
        newest, count = 0, 0
        for path in self.root.rglob("*"):
            if ("__pycache__" in path.parts or path.is_symlink()
                    or not path.is_file()
                    or path.suffix.lower() not in (".py", ".md", ".txt")):
                continue
            try:
                newest = max(newest, path.stat().st_mtime_ns)
            except OSError:
                continue
            count += 1
        key = (snapshot, newest, count)
        if self._discovery is None or self._discovery_key != key:
            self._discovery = (BM25(self.root), TextNotes(self.root))
            self._discovery_key = key
        return self._discovery

    def _lexical_index(self, snapshot):
        """Cached BM25 over code; see _ensure_discovery."""
        return self._ensure_discovery(snapshot)[0]

    def _notes_index(self, snapshot):
        """Cached lexical index over prose notes; see _ensure_discovery."""
        return self._ensure_discovery(snapshot)[1]

    def close(self):
        self.store.close()

    def _workspace_path(self, document):
        """Resolve a document against the root; absolute paths, `..` escapes,
        and symlinks pointing outside are rejected before any write."""
        candidate = self.root / document
        try:
            root = self.root.resolve()
            resolved = candidate.resolve()
        except OSError as error:
            raise ValueError("unresolvable document path: " + str(document)) from error
        if resolved == root or root not in resolved.parents:
            raise ValueError("document must stay inside the workspace root: " + str(document))
        return resolved

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def note(self, text, document="notes/session.md"):
        """Append a timestamped session note; creates parent directories."""
        authorize(self.principal, "note")
        target = self._workspace_path(document)
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n\n## " + stamp + "\n\n" + text.rstrip("\n") + "\n")
        return str(target)

    def ingest(self):
        authorize(self.principal, "ingest")
        result = self.store.ingest(str(self.root))
        result["frontier_filed"] = self._file_ingest_diagnostics(
            result.get("diagnostics", []))
        return result

    def refresh(self):
        authorize(self.principal, "refresh")
        result = self.store.refresh(str(self.root))
        result["frontier_filed"] = self._file_ingest_diagnostics(
            result.get("diagnostics", []))
        return result

    def _file_ingest_diagnostics(self, diagnostics):
        """File extractor failures in the frontier ledger (v0 wiring).

        dispatch_unresolved and parse errors are capability gaps
        (unsupported); unresolved calls may resolve with more evidence.
        Dedupe keys anchor on source digest + line, so re-ingesting an
        unchanged tree bumps recurrence instead of duplicating.
        """
        wired = {"dispatch_unresolved": ("unsupported", "dispatch %s at %s:%s"),
                 "unresolved_call": ("unresolved", "resolve call %s at %s:%s"),
                 "parse_error": ("unsupported", "parse %s at %s:%s")}
        snapshot, view = self.store.view()
        digests = {}
        for source_id in view.get("sources", []):
            try:
                info = self.store.source(source_id, snapshot)
            except ValueError:
                continue
            digests[info["path"]] = info["digest"][:16]
        filed = 0
        for diagnostic in diagnostics:
            mapping = wired.get(diagnostic.get("kind"))
            if mapping is None:
                continue
            kind, template = mapping
            path = diagnostic.get("path", "")
            line = diagnostic.get("line")
            detail = (diagnostic.get("table") or diagnostic.get("reason")
                      or diagnostic.get("expression") or diagnostic.get("path", ""))
            task = template % (detail, path, line)
            # Anchor on digest + line + byte offset: two distinct calls on
            # the same line must file as two observations (PyQuifer found
            # three print() calls on one line collapsing into one entry
            # with recurrence 3 after a single ingest).
            ref = "%s:%s:%s" % (digests.get(path, path), line,
                                diagnostic.get("start"))
            target = (diagnostic.get("table") or diagnostic.get("expression")
                      or path or None)
            self._frontier_upsert(
                kind, task, "ingest_%s" % diagnostic.get("kind"),
                source_refs=(ref,), target=target, path=path or None,
                required_evidence=("supporting evidence",))
            filed += 1
        return filed

    def impact(self, symbol, **kwargs):
        authorize(self.principal, "impact")
        return compiler.compile_impact(self.store, symbol, **kwargs)

    def mentions(self, symbol, **kwargs):
        authorize(self.principal, "mentions")
        return compiler.compile_mentions(self.store, symbol, **kwargs)

    def claims(self, symbol, **kwargs):
        authorize(self.principal, "claims")
        return compiler.compile_claims(self.store, symbol, **kwargs)

    def related(self, symbol, **kwargs):
        authorize(self.principal, "related")
        return compiler.compile_related(self.store, symbol, **kwargs)

    def timeline(self, symbol, **kwargs):
        authorize(self.principal, "timeline")
        return compiler.compile_timeline(self.store, symbol, **kwargs)

    def draft(self, document, **kwargs):
        authorize(self.principal, "draft")
        return compiler.compile_drafts(self.store, document, **kwargs)

    def review(self, document, **kwargs):
        authorize(self.principal, "review")
        return compiler.compile_review(self.store, document, **kwargs)

    def candidates(self, **kwargs):
        """Proposed-but-unverified candidates with derivation chains.

        Read path for verification tooling: mechanical passes (registry
        dispatch, drafts) propose here; nothing returned can traverse or
        render as evidence until promoted.
        """
        authorize(self.principal, "review")
        return compiler.compile_candidates(self.store, **kwargs)

    def commit_candidate(self, candidate_id, justification, task=None):
        """Bounded candidate-aided acquisition path (ACQ v2).

        The agent selects ONE live proposed candidate and justifies it;
        the candidate's proposition is then committed through the SAME
        governed path as any proposal (reconcile first, promote clean
        ADDs only). Candidate evidence never promotes directly: inspect
        and selection are read-only, the reconcile gates stay
        authoritative, and DUPLICATE/CONFLICT/INVALID outcomes behave
        exactly as commit_proposal defines — including frontier filing
        for refusals. Abstention (candidate_id None) files the standard
        acquisition-abstention entry.

        Returns a receipt dict: candidate id, justification, the
        proposition attempted, and the resulting (committed, action,
        reason). Raises ValueError for empty justifications or
        candidate ids that are not live proposed candidates.
        """
        authorize(self.principal, "review")
        if not justification or not justification.strip():
            raise ValueError("commit_candidate requires an explicit justification")
        if candidate_id is None:
            committed, action, reason = self.commit_proposal(None, None, task=task)
            return {"candidate_id": None, "justification": justification,
                    "proposition": None, "committed": committed,
                    "action": action, "reason": reason}
        snapshot, view = self.store.view()
        matches = [a for a in view.get("assertions", [])
                   if a.get("id") == candidate_id
                   and a.get("status") == "proposed"]
        if len(matches) != 1:
            raise ValueError("candidate %r is not a live proposed candidate"
                             % (candidate_id,))
        proposition = matches[0]["proposition"]
        if proposition.get("relation") != "depends_on":
            raise ValueError("commit_candidate supports depends_on candidates only: %r"
                             % (proposition.get("relation"),))
        committed, action, reason = self.commit_proposal(
            proposition.get("caller"), proposition.get("callee"),
            rel="depends_on", task=task,
            note="candidate-aided: %s" % justification.strip())
        return {"candidate_id": candidate_id,
                "justification": justification.strip(),
                "proposition": {"caller": proposition.get("caller"),
                                "callee": proposition.get("callee"),
                                "via": proposition.get("via")},
                "committed": committed, "action": action, "reason": reason}

    def commit_proposal(self, subject, object, rel="depends_on", document="notes/proposed.md",
                        authority="agent-proposal", note=None, task=None):
        """One-shot propose, deterministic reconcile, explicit promote.

        Product form of the v4 condition-C path: reconcile first, promote
        only clean ADDs. DUPLICATE is an explicit NOOP (no write);
        CONFLICT and INVALID refuse and file a frontier entry instead of
        writing. Returns (committed, action, reason). The caller invoking
        this is the approver; pair with tests, a human, or a reviewer
        gate before autonomous use.
        """
        if subject is None or object is None:
            self._frontier_upsert(
                "unresolved", task or "acquisition abstained", "proposal_abstained",
                target=task or None,
                required_evidence=("supporting evidence", "re-run acquisition"))
            return False, "abstain", "model abstained; filed for future acquisition"
        action, reason = self.reconcile_proposal(subject, object, rel=rel)
        if action != "add":
            if action in ("conflict", "invalid"):
                self._frontier_upsert(
                    "contradictory" if action == "conflict" else "unsupported",
                    "proposal %s %s %s refused: %s" % (subject, rel, object, reason),
                    "reconcile_%s" % action,
                    source_refs=(), required_evidence=("review blocking evidence",))
            return False, action, reason
        self.promote(subject, object, rel=rel, authority=authority,
                     document=document, note=note)
        return True, action, reason

    def _frontier_upsert(self, kind, task, failure, source_refs=(),
                         required_evidence=(), candidate_explanations=(),
                         target=None, path=None):
        """Record an unresolved observation, or bump recurrence of the open one.

        Diagnostic write, not a claim: entries never traverse, corroborate,
        or render as evidence. Dedupe key is (kind, task, source_refs); a
        repeat bumps recurrence and priority instead of inserting, unless
        the prior entry is terminal (resolved/dismissed) — then a new
        entry is filed so history is preserved and ids stay unique.
        """
        if kind not in FRONTIER_KINDS:
            raise ValueError("unknown frontier kind: " + str(kind))
        refs = sorted(source_refs)
        dedupe = _digest(json.dumps([kind, task, refs], sort_keys=True).encode("utf-8"))
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Index-backed lookup (frontier_dedupe): at real-repository scale
        # the ledger holds thousands of entries, and ingest files one
        # diagnostic per unresolved call, so a full scan per filing is
        # quadratic. The expression index seeks matching dedupe anchors
        # directly; the semantics are unchanged.
        row = self.store.connection.execute(
            "SELECT id, record FROM frontier "
            "WHERE json_extract(record, '$.dedupe') = ?", (dedupe,)).fetchall()
        for _rid, text in row:
            record = json.loads(text)
            if record.get("status") in ("open", "investigating"):
                record["recurrence"] = record.get("recurrence", 1) + 1
                record["priority"] = min(
                    0.95, FRONTIER_BASE_PRIORITY[kind] + 0.05 * record["recurrence"])
                record["updated_at"] = now
                self.store.connection.execute(
                    "UPDATE frontier SET record=? WHERE id=?",
                    (json.dumps(record, sort_keys=True), record["id"]))
                self.store.connection.commit()
                return record["id"], False
        record = {"kind": kind, "status": "open", "task": task,
                  "failure": failure, "target": target, "source_refs": refs,
                  "candidate_explanations": list(candidate_explanations),
                  "required_evidence": list(required_evidence),
                  "attempts": 0, "recurrence": 1,
                  "priority": min(0.95, FRONTIER_BASE_PRIORITY[kind] + 0.05),
                  "promotion_policy": "review_required",
                  "created_at": now, "updated_at": now, "dedupe": dedupe,
                  "resolution": None}
        if path:
            record["path"] = path
        base = "frontier-" + _digest(
            json.dumps([kind, task, refs, now], sort_keys=True).encode("utf-8"))[:12]
        taken = {r[0] for r in self.store.connection.execute(
            "SELECT id FROM frontier").fetchall()}
        entry_id, suffix = base, 1
        while entry_id in taken:
            suffix += 1
            entry_id = "%s-r%d" % (base, suffix)
        record["id"] = entry_id
        self.store.connection.execute(
            "INSERT INTO frontier(id, record) VALUES(?, ?)",
            (record["id"], json.dumps(record, sort_keys=True)))
        self.store.connection.commit()
        return record["id"], True

    def frontier_record(self, kind, task, failure, source_refs=(),
                        required_evidence=(), candidate_explanations=(),
                        target=None):
        """Record an unresolved observation in the frontier ledger.

        target names the replayable subject: a registry table
        (path::TABLE), a call expression, or a file path, depending on
        the failure kind. Entries without a target cannot auto-replay.
        """
        authorize(self.principal, "review")
        return self._frontier_upsert(kind, task, failure, source_refs=source_refs,
                                     required_evidence=required_evidence,
                                     candidate_explanations=candidate_explanations,
                                     target=target)

    def frontier_list(self, status=None):
        """List ledger entries, highest priority first."""
        authorize(self.principal, "review")
        rows = self.store.connection.execute("SELECT record FROM frontier").fetchall()
        entries = [json.loads(text) for (text,) in rows]
        if status is not None:
            entries = [e for e in entries if e.get("status") == status]
        entries.sort(key=lambda e: (-e.get("priority", 0.0), e.get("created_at", "")))
        return entries

    def _validate_proof(self, proof):
        """Validate a structured resolution proof against the store.

        A proof pins the snapshot where the evidence held plus at least
        one evidence section: asserted assertion ids (members of that
        snapshot), source spans (members of that snapshot, sane offsets),
        or passing test receipts (shape-checked attestations, not
        re-executed). Unknown fields, dangling references, and
        evidence-free proofs raise. Returns the normalized proof with a
        computed current marker: True when the snapshot is still live.
        """
        if not isinstance(proof, dict):
            raise ValueError("resolution must be a structured proof dict, "
                             "not %s" % type(proof).__name__)
        for field in proof:
            if field not in PROOF_FIELDS:
                raise ValueError("unknown proof field: %r" % (field,))
        snapshot = proof.get("snapshot")
        known = {s["id"] for s in self.store.snapshots()}
        if snapshot not in known:
            raise ValueError("proof cites unknown snapshot: %r" % (snapshot,))
        assertion_ids = proof.get("assertions", [])
        spans = proof.get("spans", [])
        tests = proof.get("tests", [])
        if not (assertion_ids or spans or tests):
            raise ValueError("proof carries no evidence: need assertions, "
                             "spans, or test receipts")
        if not isinstance(assertion_ids, list) or not isinstance(spans, list) \
                or not isinstance(tests, list):
            raise ValueError("proof assertions/spans/tests must be lists")
        for aid in assertion_ids:
            row = self.store.connection.execute(
                "SELECT id FROM assertions WHERE id=?", (aid,)).fetchone()
            if row is None:
                raise ValueError("proof cites unknown assertion: %r" % (aid,))
            member = self.store.connection.execute(
                "SELECT 1 FROM snapshot_assertions WHERE snapshot=? AND assertion=?",
                (snapshot, aid)).fetchone()
            if member is None:
                raise ValueError("assertion %r is not a member of proof "
                                 "snapshot" % (aid,))
        for span in spans:
            if not isinstance(span, dict):
                raise ValueError("malformed proof span: %r" % (span,))
            source = span.get("source")
            member = self.store.connection.execute(
                "SELECT 1 FROM members WHERE snapshot=? AND source=?",
                (snapshot, source)).fetchone()
            if member is None:
                raise ValueError("proof span cites unknown source: %r" % (source,))
            start, end = span.get("start"), span.get("end")
            if not isinstance(start, int) or not isinstance(end, int) \
                    or not (0 <= start <= end):
                raise ValueError("malformed proof span offsets: %r" % (span,))
        for receipt in tests:
            if not isinstance(receipt, dict) or not receipt.get("id") \
                    or receipt.get("result") != "pass":
                raise ValueError("malformed test receipt (need id + result pass): "
                                 "%r" % (receipt,))
        reviewer = proof.get("reviewer")
        if reviewer is not None and (
                not isinstance(reviewer, dict) or not reviewer.get("principal")):
            raise ValueError("malformed proof reviewer: %r" % (reviewer,))
        note = proof.get("note", "")
        if not isinstance(note, str):
            raise ValueError("proof note must be a string")
        normalized = {"snapshot": snapshot, "assertions": list(assertion_ids),
                      "spans": [{"source": s.get("source"), "start": s.get("start"),
                                 "end": s.get("end")} for s in spans],
                      "tests": [{"id": t.get("id"), "result": t.get("result")}
                                for t in tests],
                      "reviewer": reviewer, "note": note,
                      "current": snapshot == self.store.current()}
        return normalized

    def frontier_resolve(self, entry_id, proof, resolved_by=None):
        """Resolve an entry against a validated structured proof receipt.

        Fail-closed wrapper over the resolved transition: dangling
        snapshots, assertions, or sources, evidence-free proofs, and
        free-text resolutions all raise and leave the entry open.
        Retrieval-miss entries additionally require the proof to pin the
        verified target span and the target to rank first under the
        recorded config right now — a valid target the system still
        does not retrieve stays open.
        """
        authorize(self.principal, "review")
        row = self.store.connection.execute(
            "SELECT record FROM frontier WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise ValueError("unknown frontier entry: " + entry_id)
        record = json.loads(row[0])
        if record.get("status") in ("resolved", "dismissed"):
            raise ValueError("entry %s is terminal (%s)"
                             % (entry_id, record.get("status")))
        if record.get("failure") == "retrieval_miss" and isinstance(proof, dict):
            retrieval = record.get("retrieval") or {}
            target_key = record.get("target")
            _snap, _view = self.store.view(proof.get("snapshot"))
            symbols = [s for s in _view.get("symbols", [])
                       if s.get("key") == target_key]
            if not symbols:
                raise ValueError("verified target not in proof snapshot")
            sym = symbols[0]
            pinned = {(sp.get("source"), sp.get("start"), sp.get("end"))
                      for sp in proof.get("spans", [])}
            if (sym.get("source"), sym.get("start"),
                    sym.get("end")) not in pinned:
                raise ValueError("proof must pin the verified target span")
            rank, _recall, _rr, _top = self._miss_rank(
                self, record.get("task", ""), target_key,
                retrieval.get("config"))
            if rank != 1:
                raise ValueError("verified target ranks %s, not 1" % (rank,))
        return self.frontier_transition(entry_id, "resolved", resolution=proof,
                                        resolved_by=resolved_by)

    def frontier_transition(self, entry_id, status, resolution=None, resolved_by=None):
        """Move an entry open -> investigating -> resolved | dismissed.

        Terminal states are sticky: resolving or dismissing twice raises.
        History is preserved either way. Resolving requires a validated
        structured proof dict (see _validate_proof); free-text notes are
        accepted only for investigating/dismissed moves.
        """
        authorize(self.principal, "review")
        if status not in ("investigating", "resolved", "dismissed"):
            raise ValueError("unknown frontier status: " + str(status))
        row = self.store.connection.execute(
            "SELECT record FROM frontier WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise ValueError("unknown frontier entry: " + entry_id)
        record = json.loads(row[0])
        if record.get("status") in ("resolved", "dismissed"):
            raise ValueError("entry %s is terminal (%s)" % (entry_id, record.get("status")))
        if status == "resolved":
            proof = self._validate_proof(resolution)
        record["status"] = status
        record["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if resolution is not None:
            if status == "resolved":
                record["resolution"] = {"proof": proof, "by": resolved_by,
                                        "at": record["updated_at"]}
            else:
                record["resolution"] = {"note": resolution, "by": resolved_by,
                                        "at": record["updated_at"]}
        self.store.connection.execute(
            "UPDATE frontier SET record=? WHERE id=?",
            (json.dumps(record, sort_keys=True), entry_id))
        self.store.connection.commit()
        return record

    def frontier_replay(self, entry_id):
        """Re-check an entry against current graph state; resolve only on proof.

        Kind-dispatched: abstentions re-run the task (non-empty answers
        resolve); dispatch/static-edge entries resolve when an asserted
        edge covers a proposed candidate from the recorded table, and move
        to investigating when a candidate exists but nothing is committed;
        parse errors resolve when the file parses with symbols again.
        A vanished diagnostic alone never resolves: without positive
        evidence the entry moves to investigating at most. Manual
        resolution with verification evidence remains available for
        reviewer-verified cycles. Returns (status, verification).
        """
        authorize(self.principal, "review")
        row = self.store.connection.execute(
            "SELECT record FROM frontier WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise ValueError("unknown frontier entry: " + entry_id)
        record = json.loads(row[0])
        if record.get("status") in ("resolved", "dismissed"):
            resolution = record.get("resolution") or {}
            proof = resolution.get("proof") or {}
            return record["status"], proof.get("note", "") or resolution.get("note", "")
        failure, target = record.get("failure", ""), record.get("target")
        snapshot, view = self.store.view()
        as_of = self.store.committed_on(snapshot)

        def table_diagnostics():
            return [d for d in view.get("diagnostics", [])
                    if d.get("kind") == "dispatch_unresolved"
                    and d.get("table") == target]

        def proof_for(note, assertion_ids=(), spans=()):
            return {"snapshot": snapshot, "assertions": list(assertion_ids),
                    "spans": list(spans), "note": note}

        if failure == "retrieval_abstention":
            found = self.answer(record.get("task", ""))
            if found["outcome"] != "empty":
                by_key = {s.get("key"): s for s in view.get("symbols", [])}
                spans = [{"source": by_key[sym].get("source"),
                          "start": by_key[sym].get("start", 0),
                          "end": by_key[sym].get("end", 0)}
                         for sym in [c["symbol"] for c in found["candidates"][:5]]
                         if sym in by_key][:5]
                if not spans:
                    return "investigating", \
                        "retrieves candidates without pinned evidence"
                top = [c["symbol"] for c in found["candidates"][:3]]
                note = "task now retrieves: %s" % ", ".join(top)
                self.frontier_transition(
                    entry_id, "resolved",
                    resolution=proof_for(note, spans=spans),
                    resolved_by="frontier_replay")
                return "resolved", note
            return record["status"], "task still abstains"
        if failure == "retrieval_miss":
            retrieval = record.get("retrieval") or {}
            target_key = record.get("target")
            rank, recall, rr, _top = self._miss_rank(
                self, record.get("task", ""), target_key,
                retrieval.get("config"))
            detail = ("target %s ranks %s (recall@10 %.1f, RR %.3f)"
                      % (target_key, rank, recall, rr))
            if rank == 1:
                _snap, _view = self.store.view()
                symbols = [s for s in _view.get("symbols", [])
                           if s.get("key") == target_key]
                if not symbols:
                    return record["status"], "verified target left the graph"
                sym = symbols[0]
                note = "verified target ranks first: %s" % detail
                self.frontier_transition(
                    entry_id, "resolved",
                    resolution=proof_for(note, spans=[{
                        "source": sym.get("source"),
                        "start": sym.get("start", 0),
                        "end": sym.get("end", 0)}]),
                    resolved_by="frontier_replay")
                return "resolved", note
            return record["status"], detail + "; needs rank 1"
        if failure in ("static_edge_unresolved", "ingest_dispatch_unresolved"):
            if not target:
                return record["status"], "no replayable target recorded"
            candidates, covering = _dispatch_covering(view, target, as_of)
            if covering:
                first = covering[0].get("proposition", {})
                note = "asserted edge covers %s -> %s" % (
                    first.get("caller"), first.get("callee"))
                cited = sorted({aid for c in covering
                                for aid in c.get("covering_assertion", [])})
                self.frontier_transition(
                    entry_id, "resolved",
                    resolution=proof_for(note, assertion_ids=cited),
                    resolved_by="frontier_replay")
                return "resolved", note
            if candidates:
                self.frontier_transition(entry_id, "investigating",
                                         resolution="candidate present, awaiting verification",
                                         resolved_by="frontier_replay")
                return "investigating", "candidate present, awaiting verification"
            if not table_diagnostics():
                return record["status"], "failure no longer reported; positive evidence still required"
            return record["status"], "table still unresolved"
        if failure == "ingest_parse_error":
            if any(d.get("kind") == "parse_error" and d.get("path") == target
                   for d in view.get("diagnostics", [])):
                return record["status"], "file still fails to parse"
            symbols = [s for s in view["symbols"] if s.get("path") == target]
            if symbols:
                note = "file parses with %d symbols" % len(symbols)
                self.frontier_transition(
                    entry_id, "resolved",
                    resolution=proof_for(note, spans=[{
                        "source": s.get("source"), "start": s.get("start", 0),
                        "end": s.get("end", 0)} for s in symbols[:5]]),
                    resolved_by="frontier_replay")
                return "resolved", note
            return "investigating", "parse error gone but no symbols yet"
        if failure == "ingest_unresolved_call":
            if any(d.get("kind") == "unresolved_call" and d.get("expression") == target
                   for d in view.get("diagnostics", [])):
                return record["status"], "call still unresolved"
            return "investigating", "diagnostic gone; resolving edge not confirmed"
        return record["status"], "no replay rule for this failure kind"

    @staticmethod
    def _miss_rank(memory, question, target, config):
        """Rank a verified target in a replayed retrieval (1-based, None if absent).

        Returns (rank, recall_at_10, reciprocal_rank, top_symbols) using the
        recorded retrieval configuration so replays measure the same pipeline
        the audit observed. recall@10 and RR are diagnostic only; the
        resolution predicate is rank == 1.
        """
        config = dict(config or {})
        found = memory.answer(
            question, mode=config.get("mode", "hybrid"),
            policy=config.get("policy", "transparent"),
            select=config.get("select", False),
            limit=config.get("limit", 3), budget=config.get("budget"))
        top = [c["symbol"] for c in found.get("candidates", [])]
        rank = top.index(target) + 1 if target in top else None
        recall = 1.0 if (rank is not None and rank <= 10) else 0.0
        rr = 1.0 / rank if rank else 0.0
        return rank, recall, rr, top[:5]

    def frontier_record_miss(self, question, verified_target, authority,
                             config=None):
        """File an audited retrieval miss: the system answered, but wrong.

        The audit authority must be a human (or a test harness acting for
        one): a model's own unsupported claim that a result is wrong never
        becomes ground truth. The verified target must be a known symbol,
        and the observed outcome must be complete-but-wrong — an empty
        outcome is an abstention, a different entry kind. Captures the
        observed rank, top candidates, config, and snapshot so replay can
        measure the same pipeline.
        """
        if not authority:
            raise ValueError("a retrieval miss needs an audit authority")
        authorize(self.principal, "review")
        _snapshot, view = self.store.view()
        if verified_target not in {s.get("key") for s in view.get("symbols", [])}:
            raise ValueError("unknown verified target: %r" % (verified_target,))
        config = {"mode": "hybrid", "policy": "transparent", "select": False,
                  "limit": 3, "budget": None, **(config or {})}
        found = self.answer(
            question, mode=config["mode"], policy=config["policy"],
            select=config["select"], limit=config["limit"],
            budget=config["budget"])
        if found.get("outcome") == "empty":
            raise ValueError("empty outcome is an abstention, not a miss")
        top = [c["symbol"] for c in found.get("candidates", [])]
        rank = top.index(verified_target) + 1 if verified_target in top else None
        entry_id, created = self._frontier_upsert(
            "unsupported", question, "retrieval_miss", target=verified_target,
            required_evidence=("verified target ranks first on replay",),
            candidate_explanations=tuple(top[:5]))
        row = self.store.connection.execute(
            "SELECT record FROM frontier WHERE id=?", (entry_id,)).fetchone()
        record = json.loads(row[0])
        record["retrieval"] = {"question": question,
                               "verified_target": verified_target,
                               "observed_rank": rank,
                               "observed_top": top[:5],
                               "outcome": found.get("outcome"),
                               "config": config, "authority": authority,
                               "snapshot": self.store.current()}
        self.store.connection.execute(
            "UPDATE frontier SET record=? WHERE id=?",
            (json.dumps(record, sort_keys=True), entry_id))
        self.store.connection.commit()
        return entry_id, created

    def frontier_miss_summary(self):
        """Counts over audited retrieval misses plus the false-confidence rate.

        The rate is the share of audited misses that were returned as
        apparently successful retrievals. Miss records only file on
        complete outcomes, so the rate is 1.0 until correct-answer audits
        are also tracked; it is reported explicitly rather than assumed.
        """
        rows = self.store.connection.execute(
            "SELECT record FROM frontier").fetchall()
        misses = [json.loads(text) for (text,) in rows
                  if json.loads(text).get("failure") == "retrieval_miss"]
        by_status = {}
        for entry in misses:
            by_status[entry.get("status")] = by_status.get(entry.get("status"), 0) + 1
        complete = sum(1 for entry in misses
                       if (entry.get("retrieval") or {}).get("outcome") != "empty")
        summary = {"total": len(misses), "false_confidence_rate":
                   (complete / len(misses) if misses else None)}
        summary.update(by_status)
        return summary

    def frontier_export_regression(self, entry_id, path):
        """Export a resolved entry as a durable regression case file.

        The case pins the entry, its validated proof, and a replayable
        predicate mirroring the replay rule that resolved it. Only
        resolved entries export: an open gap is not a regression test.
        replay_regression_case(memory, case) re-checks the predicate
        against any later graph state, so extractor or policy changes
        cannot silently reintroduce the original failure.
        """
        authorize(self.principal, "review")
        row = self.store.connection.execute(
            "SELECT record FROM frontier WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise ValueError("unknown frontier entry: " + entry_id)
        record = json.loads(row[0])
        if record.get("status") != "resolved":
            raise ValueError("only resolved entries export as regressions "
                             "(%s is %s)" % (entry_id, record.get("status")))
        proof = (record.get("resolution") or {}).get("proof")
        if not proof:
            raise ValueError("entry %s resolved without a proof receipt" % entry_id)
        failure, target, task = (record.get("failure"), record.get("target"),
                                 record.get("task"))
        if failure == "retrieval_abstention":
            predicate = {"type": "answer_nonempty", "question": task}
        elif failure in ("static_edge_unresolved", "ingest_dispatch_unresolved"):
            if not target:
                raise ValueError("dispatch entry %s has no replayable table" % entry_id)
            predicate = {"type": "dispatch_covered", "table": target}
        elif failure == "ingest_parse_error":
            if not target:
                raise ValueError("parse entry %s has no replayable path" % entry_id)
            predicate = {"type": "symbols_present", "path": target}
        elif failure == "retrieval_miss":
            if not target:
                raise ValueError("miss entry %s has no verified target" % entry_id)
            predicate = {"type": "target_rank_1", "question": task,
                         "target": target}
        else:
            raise ValueError("no exportable predicate for failure kind: %r" % (failure,))
        case = {"version": 1,
                "entry": {"id": record.get("id"), "kind": record.get("kind"),
                          "failure": failure, "task": task, "target": target},
                "proof": proof, "predicate": predicate,
                "config": (record.get("retrieval") or {}).get("config"),
                "snapshot": proof.get("snapshot"),
                "environment": _export_environment(),
                "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        out = Path(path)
        out.write_text(json.dumps(case, indent=1, sort_keys=True), encoding="utf-8")
        return case

    def promote(self, subject, object, rel="depends_on", effective=None,
                authority="agent-outcome", document="notes/outcomes.md", note=None):
        """Explicitly approve one candidate relationship and record it.

        Refuses exact duplicates already asserted in the graph and pairs an
        existing contradicts claim blocks; corrections go through an explicit
        contradicts/supersedes claim first. Bare names are rejected, so
        identity can only narrow, never guess. Writes the claim block;
        a later ingest publishes it.
        """
        authorize(self.principal, "promote")
        if rel not in CLAIM_RELATIONS:
            raise ValueError("unknown relation: " + str(rel))
        for ref in (subject, object):
            if not isinstance(ref, str) or "::" not in ref:
                raise ValueError("outcome endpoints must be exact path::symbol keys: " + repr(ref))
        _, view = self.store.view()
        for assertion in view["assertions"]:
            proposition = assertion["proposition"]
            if assertion.get("status") != "asserted_in_source":
                continue
            if (proposition.get("relation") == rel
                    and proposition.get("caller") == subject
                    and proposition.get("callee") == object):
                raise ValueError("duplicate of asserted %s %s %s" % (subject, rel, object))
            if (proposition.get("relation") == "contradicts"
                    and {proposition.get("caller"), proposition.get("callee")} == {subject, object}):
                raise ValueError("contradicts claim blocks %s %s %s; record a correction first"
                                 % (subject, rel, object))
        return self.record_outcome(subject, object, rel=rel, effective=effective,
                                   authority=authority, document=document, note=note)

    def reconcile_proposal(self, subject, object, rel="depends_on"):
        """Classify a candidate memory write without mutating the graph.

        Deterministic first pass for acquisition pipelines: returns an
        (action, reason) pair where action is one of "add", "duplicate",
        "conflict", or "invalid". "duplicate" is the explicit NOOP path --
        callers must not write when the triple is already asserted.
        "conflict" names the blocking evidence and routes to a reviewer
        (human, tests, or -- only for genuine ambiguity -- a second model
        call); the classifier itself never resolves it. "invalid" covers
        unknown relations, non-exact endpoints, and endpoints naming
        symbols or documents the workspace does not know.
        """
        if rel not in CLAIM_RELATIONS:
            return ("invalid", "unknown relation: " + str(rel))
        for ref in (subject, object):
            if not isinstance(ref, str) or "::" not in ref:
                return ("invalid", "endpoint is not an exact path::symbol key: "
                        + repr(ref))
        _, view = self.store.view()
        known = {entry.get("key") for entry in view["symbols"]}
        for assertion in view["assertions"]:
            proposition = assertion.get("proposition", {})
            known.add(proposition.get("caller"))
            known.add(proposition.get("callee"))
        for ref in (subject, object):
            if ref in known:
                continue
            if ref.endswith("::document"):
                candidate = self.root / ref.split("::", 1)[0]
                try:
                    inside = candidate.resolve().is_relative_to(self.root.resolve())
                except (OSError, ValueError):
                    inside = False
                if inside and candidate.is_file():
                    continue
            return ("invalid", "unknown endpoint: " + ref)
        duplicate = None
        conflict = None
        for assertion in view["assertions"]:
            if assertion.get("status") != "asserted_in_source":
                continue
            proposition = assertion.get("proposition", {})
            if (proposition.get("relation") == rel
                    and proposition.get("caller") == subject
                    and proposition.get("callee") == object):
                duplicate = ("duplicate", "already asserted: %s %s %s"
                             % (subject, rel, object))
                continue
            pair = {proposition.get("caller"), proposition.get("callee")}
            if proposition.get("relation") == "contradicts" and pair == {subject, object}:
                conflict = ("conflict", "contradicts claim blocks %s %s %s; "
                            "record a correction first" % (subject, rel, object))
            if (proposition.get("relation") == "retracts"
                    and proposition.get("retract") == rel and pair == {subject, object}):
                conflict = ("conflict", "retraction stands against %s %s %s; "
                            "re-assert only with new evidence" % (subject, rel, object))
        # A disputed triple is never a clean no-op: a blocking contradicts
        # or retracts claim outranks an exact-triple match. Scanning every
        # row (instead of returning on the first id-ordered match) keeps
        # the verdict independent of assertion row order.
        if conflict is not None:
            return conflict
        if duplicate is not None:
            return duplicate
        return ("add", "no duplicate, conflict, or endpoint problem found")

    def answer(self, task, *, snapshot=None, limit=3, mode="hybrid",
               policy="transparent", budget=None, threshold=0.0, provider=None,
               select=False, container_limit=2):
        """Task-based retrieval without an exact symbol.

        Stages propose candidates: exact keys, bare names, BM25 lexical
        chunks, and an optional embedding provider. Weighted reciprocal-rank
        fusion merges the stage rankings with exact outranking name
        outranking discovery, deterministically. Budgets cap each stage and
        the fused total; the semantic stage runs only when its provider is
        available, otherwise it is recorded as skipped. Discovery stages
        select candidates only and never create graph edges; an empty fusion
        abstains. The policy selects the traversal set for every candidate.
        Container-aware retrieval (container_limit): a selected method whose
        dotted prefix resolves to a verified class symbol additionally
        expands that class with compile_related under the same snapshot and
        policy. The expansion is navigational only -- it synthesizes and
        asserts nothing; merged impacts carry a container_context label and
        render under CONTAINER CONTEXT. Expansion fires only for symbols
        already in the answer set (post-selection kept when select=True),
        costs one budget unit per distinct class, and is recorded in
        result["container_expansion"]. Zero disables it.
        With select=True, the evidence-aware selection layer
        (tunic.selection) filters fused candidates into supported /
        proposed / ambiguous / contradicted / insufficient_evidence /
        out_of_scope with a trace; only kept candidates expand into
        impacts, mentions, and claims. The default (False) preserves the
        fusion-only behavior until the selection contract acceptance
        is met on frozen suites.
        """
        authorize(self.principal, "answer")
        import re
        import time
        from .retrieval import EmbeddingProvider, fuse
        if mode not in ("graph", "hybrid"):
            raise ValueError("mode must be 'graph' or 'hybrid'")
        if policy not in compiler.POLICIES:
            raise ValueError("unknown policy: " + str(policy))
        if type(limit) is not int or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        if type(container_limit) is not int or container_limit < 0:
            raise ValueError("container_limit must be a non-negative integer")
        caps = {"exact": limit, "name": limit, "lexical": limit, "semantic": limit}
        if budget is not None:
            unknown = set(budget) - set(caps)
            if unknown:
                raise ValueError("unknown budget stages: " + ", ".join(sorted(unknown)))
            for stage, cap in budget.items():
                if type(cap) is not int or cap < 0:
                    raise ValueError("budget caps must be non-negative integers")
            caps.update(budget)
        provider = EmbeddingProvider() if provider is None else provider
        snapshot, view = self.store.view(snapshot)
        code = [(s["key"], s["name"].split(".")[-1])
                for s in view["symbols"] if not s["key"].endswith("::document")]
        rankings, stages = {}, {}
        started = time.perf_counter()
        exact = sorted(key for key, _ in code if key in task)[:caps["exact"]]
        stages["exact"] = {"candidates": len(exact),
                           "ms": (time.perf_counter() - started) * 1000.0,
                           "skipped": False}
        started = time.perf_counter()

        def _name_pattern(leaf):
            # snake_case symbols match prose with spaces: "avalanche
            # statistics" finds avalanche_statistics. Word boundaries are
            # preserved, so "beta function" still does not match
            # beta_functional.
            return (r"\b" + r"[\s_]+".join(re.escape(part)
                                           for part in leaf.split("_")) + r"\b")

        names = sorted(key for key, leaf in code
                       if len(leaf) >= 2
                       and re.search(_name_pattern(leaf), task,
                                     flags=re.IGNORECASE))[:caps["name"]]
        stages["name"] = {"candidates": len(names),
                          "ms": (time.perf_counter() - started) * 1000.0,
                          "skipped": False}
        index = None
        note_mentions = []
        discovery = None
        if mode == "hybrid" and caps["lexical"] > 0:
            started = time.perf_counter()
            discovery = self._ensure_discovery(snapshot)
            index, notes = discovery
            lexical = [hit["symbol"] for hit in
                       index.search_text(task, top_k=caps["lexical"])]
            note_mentions = []
            for hit in notes.search_text(task, top_k=caps["lexical"]):
                mentioner = hit["symbol"] + "::document"
                for assertion in view["assertions"]:
                    proposition = assertion.get("proposition", {})
                    if (proposition.get("relation") == "text_mention"
                            and proposition.get("caller") == mentioner
                            and proposition.get("callee") is not None):
                        note_mentions.append(
                            {"mentioner": mentioner,
                             "candidate": proposition.get("callee"),
                             "status": "lexical_discovery",
                             "evidence": {"path": hit["symbol"],
                                          "start": 0,
                                          "window": hit.get("text", "")[:1200]}})
            stages["lexical"] = {"candidates": len(lexical),
                                 "ms": (time.perf_counter() - started) * 1000.0,
                                 "skipped": False}
        else:
            lexical = []
            stages["lexical"] = {"candidates": 0, "ms": 0.0, "skipped": True,
                                 "reason": ("lexical stage disabled"
                                            if mode != "hybrid" else "budget cap is zero")}
        started = time.perf_counter()
        if mode == "hybrid" and caps["semantic"] > 0 and provider.available():
            try:
                if discovery is None:
                    discovery = self._ensure_discovery(snapshot)
                index = discovery[0]
                texts, order = [], []
                for document in index.documents:
                    text = document["symbol"] + " " + " ".join(sorted(document["tokens"]))
                    texts.append(text)
                    order.append(document["symbol"])
                chosen = provider.rank(task, texts, caps["semantic"])
                semantic = [order[texts.index(text)] for text in chosen]
                stages["semantic"] = {"candidates": len(semantic),
                                      "ms": (time.perf_counter() - started) * 1000.0,
                                      "skipped": False}
            except Exception as error:
                # A failing discovery stage degrades to the stages that
                # work; it never takes down the whole answer. The reason
                # is recorded so benchmarks can see the gap.
                semantic = []
                stages["semantic"] = {
                    "candidates": 0,
                    "ms": (time.perf_counter() - started) * 1000.0,
                    "skipped": True,
                    "reason": "semantic stage error: %s: %s" % (
                        type(error).__name__, str(error)[:200])}
        else:
            if mode != "hybrid":
                reason = "semantic stage disabled"
            elif caps["semantic"] <= 0:
                reason = "budget cap is zero"
            else:
                reason = "no embedding provider available"
            semantic = []
            stages["semantic"] = {"candidates": 0, "ms": 0.0, "skipped": True,
                                  "reason": reason}
        rankings = {"exact": exact, "name": names,
                    "lexical": lexical, "semantic": semantic}
        fused = fuse(rankings, total=limit, threshold=threshold)
        selection = None
        if select:
            from .selection import (OUTCOME_ORDER, classify_intent,
                                    select as run_selection)
            anchors = None
            if classify_intent(task)["intent"] == "impact":
                subjects = [key for _, key, ranks in fused
                            if "exact" in ranks or "name" in ranks]
                if subjects:
                    anchors = {}
                    for subject in subjects:
                        try:
                            impact = compiler.compile_impact(
                                self.store, subject, snapshot=snapshot,
                                policy=policy, view=view)
                        except ValueError:
                            continue
                        anchors[subject] = {entry["symbol"]
                                            for entry in impact["impacts"]}
                    anchors = anchors or None
            selection = run_selection(task, fused, view=view, policy=policy,
                                      limit=limit, anchors=anchors)
            outcomes = {d["symbol"]: d["outcome"]
                        for d in selection["trace"]["decisions"]}
            top = sorted(selection["kept"],
                         key=lambda k: OUTCOME_ORDER[outcomes.get(k, "proposed")])
        else:
            top = [key for _, key, _ in fused]
        origins = {key: ranks for _, key, ranks in fused}
        order = {"exact": 0, "name": 1, "lexical": 2, "semantic": 3, "graph": -1}
        known = {row["key"] for row in view["symbols"]}
        unknown_symbols = [key for key in top if key not in known]
        top = [key for key in top if key in known]
        scores = {name: score for score, name, _ in fused}
        subjects = [key for _, key, ranks in fused
                    if "exact" in ranks or "name" in ranks]
        followed = []
        if subjects:
            # Successor following: a named subject the graph records as
            # replaced names its replacement; the replacement joins the
            # candidates as a graph-sourced pointer but is not expanded:
            # its own dependency neighborhood answers a different question,
            # and expanding it reintroduces stale callers under current
            # policy (the supersedes edge itself is the evidence).
            from .selection import _live_claim_maps
            superseded_by, _ = _live_claim_maps(view)
            for subject in subjects:
                if subject not in known:
                    continue
                for replacement in superseded_by.get(subject, []):
                    if (replacement in known and replacement not in top
                            and replacement not in scores):
                        top.append(replacement)
                        followed.append(replacement)
                        origins[replacement] = {"graph": 1}
                        scores[replacement] = scores.get(subject, 0.0)
        symbols_by_key = {row["key"]: row for row in view["symbols"]}
        # Container worklist, computed BEFORE dependency expansion from
        # the answer set (post-selection kept when select=True, so
        # expansion can never bypass filtering). Unique classes in answer
        # order; the budget caps how many expand; the rest are recorded
        # as skipped, never silently dropped.
        container_order, _seen_containers = [], set()
        for key in top:
            if key in followed:
                continue
            container = _container_of(symbols_by_key, key)
            if container and container not in _seen_containers:
                _seen_containers.add(container)
                container_order.append((key, container))
        expanded_containers = container_order[:container_limit]
        skipped_containers = container_order[container_limit:]
        failed_containers = []
        impacts = {}
        mentions = []
        claims = []
        for symbol in top:
            if symbol in followed:
                continue
            related = compiler.compile_related(self.store, symbol, snapshot=snapshot,
                                               policy=policy, view=view)
            for entry in related["impact"]["impacts"]:
                impacts.setdefault(entry["symbol"], entry)
            mentions.extend(related["mentions"]["mentions"])
            claims.extend(related["claims"]["claims"])
        for method_key, class_key in expanded_containers:
            try:
                container_related = compiler.compile_related(
                    self.store, class_key, snapshot=snapshot,
                    policy=policy, view=view)
            except ValueError:
                failed_containers.append(class_key)
                continue
            for entry in container_related["impact"]["impacts"]:
                if entry["symbol"] not in impacts:
                    labeled = dict(entry)
                    labeled["container_context"] = {
                        "container": class_key, "via": method_key}
                    impacts[entry["symbol"]] = labeled
            mentions.extend(container_related["mentions"]["mentions"])
            claims.extend(container_related["claims"]["claims"])
        seen = {(m.get("mentioner"), m.get("candidate")) for m in mentions}
        for mention in note_mentions:
            key = (mention.get("mentioner"), mention.get("candidate"))
            if key not in seen:
                seen.add(key)
                mentions.append(mention)
        mentions = _dedup_dicts(mentions)
        claims = _dedup_dicts(claims)
        mentions.sort(key=lambda m: (m["evidence"]["path"], m["evidence"]["start"], m["status"]))
        claims.sort(key=lambda c: (c["relation"], c["caller"], c.get("callee") or ""))
        cand_entries = []
        for key in top:
            entry = {"symbol": key, "score": scores[key],
                     "match": min(origins[key], key=order.__getitem__),
                     "origins": origins[key]}
            container = _container_of(symbols_by_key, key)
            if container:
                entry["container"] = container
            cand_entries.append(entry)
        result = {"task": task, "snapshot": snapshot, "policy": policy,
                "fusion": "weighted_rrf_k60", "provider": provider.name,
                "budget": caps, "threshold": threshold, "stages": stages,
                "candidates": cand_entries,
                "impacts": [impacts[key] for key in sorted(impacts)],
                "mentions": mentions, "claims": claims,
                "selection": selection,
                "unknown_symbols": unknown_symbols,
                "container_expansion": {
                    "budget": container_limit,
                    "used": len(expanded_containers) - len(failed_containers),
                    "attempted": [klass for _, klass in container_order],
                    "expanded": {method: klass
                                 for method, klass in expanded_containers
                                 if klass not in failed_containers},
                    "failed": list(failed_containers),
                    "skipped_over_budget": [klass for _, klass
                                            in skipped_containers]},
                "outcome": "complete" if top else "empty"}
        if result["outcome"] == "empty":
            self._frontier_upsert(
                "unresolved", task, "retrieval_abstention",
                required_evidence=("rephrase the task", "add supporting evidence"))
        return result

    def record_outcome(self, subject, object, rel="depends_on", effective=None,
                       authority="agent-outcome", document="notes/outcomes.md", note=None,
                       retract=None):
        """Append an explicit outcome claim block; ingest separately to publish.

        The claim is asserted text, never an observation. Only exact symbol
        keys are accepted: bare names stay proposals, never merges.
        A `retracts` claim must name its target relation via `retract`;
        any other relation carrying `retract` is rejected.
        """
        authorize(self.principal, "record_outcome")
        if rel not in CLAIM_RELATIONS:
            raise ValueError("unknown relation: " + str(rel))
        for ref in (subject, object):
            if not isinstance(ref, str) or "::" not in ref:
                raise ValueError("outcome endpoints must be exact path::symbol keys: " + repr(ref))
        if effective is None:
            effective = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not is_iso_date(effective):
            raise ValueError("effective must be an ISO YYYY-MM-DD date")
        from .extract import RETRACTABLE
        if rel == "retracts":
            if retract not in RETRACTABLE:
                raise ValueError("retracts needs a retractable relation: " + repr(retract))
        elif retract is not None:
            raise ValueError("retract is only valid with rel='retracts'")
        claim = {"rel": rel, "subject": subject, "object": object,
                 "effective": effective, "authority": authority}
        if note is not None:
            claim["note"] = note
        if retract is not None:
            claim["retract"] = retract
        block = ("# Recorded outcome\n\n```tunic-claims\n"
                 + json.dumps([claim], ensure_ascii=True) + "\n```\n")
        target = self._workspace_path(document)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n\n" + block)
        return block
