"""Agent-facing memory sessions sharing one database across processes.

A Memory is a thin, explicit wrapper over GraphStore and the compiler
queries. Sessions are separate Memory instances (possibly in separate
processes) over the same database path: whatever one session ingests and
records, another session retrieves. Nothing is inferred; every write is a
timestamped file append or an explicit claim block awaiting ingest.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

from . import compiler
from .baselines import BM25, TextNotes
from .extract import CLAIM_RELATIONS, is_iso_date
from .store import GraphStore


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
        return self.store.ingest(str(self.root))

    def refresh(self):
        authorize(self.principal, "refresh")
        return self.store.refresh(str(self.root))

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
        for assertion in view["assertions"]:
            if assertion.get("status") != "asserted_in_source":
                continue
            proposition = assertion.get("proposition", {})
            if (proposition.get("relation") == rel
                    and proposition.get("caller") == subject
                    and proposition.get("callee") == object):
                return ("duplicate", "already asserted: %s %s %s"
                        % (subject, rel, object))
            pair = {proposition.get("caller"), proposition.get("callee")}
            if proposition.get("relation") == "contradicts" and pair == {subject, object}:
                return ("conflict", "contradicts claim blocks %s %s %s; "
                        "record a correction first" % (subject, rel, object))
            if (proposition.get("relation") == "retracts"
                    and proposition.get("retract") == rel and pair == {subject, object}):
                return ("conflict", "retraction stands against %s %s %s; "
                        "re-assert only with new evidence" % (subject, rel, object))
        return ("add", "no duplicate, conflict, or endpoint problem found")

    def answer(self, task, *, snapshot=None, limit=3, mode="hybrid",
               policy="transparent", budget=None, threshold=0.0, provider=None,
               select=False):
        """Task-based retrieval without an exact symbol.

        Stages propose candidates: exact keys, bare names, BM25 lexical
        chunks, and an optional embedding provider. Weighted reciprocal-rank
        fusion merges the stage rankings with exact outranking name
        outranking discovery, deterministically. Budgets cap each stage and
        the fused total; the semantic stage runs only when its provider is
        available, otherwise it is recorded as skipped. Discovery stages
        select candidates only and never create graph edges; an empty fusion
        abstains. The policy selects the traversal set for every candidate.
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
        names = sorted(key for key, leaf in code
                       if len(leaf) >= 2
                       and re.search(r"\b" + re.escape(leaf) + r"\b", task,
                                     flags=re.IGNORECASE))[:caps["name"]]
        stages["name"] = {"candidates": len(names),
                          "ms": (time.perf_counter() - started) * 1000.0,
                          "skipped": False}
        index = None
        note_mentions = []
        if mode == "hybrid" and caps["lexical"] > 0:
            started = time.perf_counter()
            index = self._lexical_index(snapshot)
            lexical = [hit["symbol"] for hit in
                       index.search_text(task, top_k=caps["lexical"])]
            notes = self._notes_index(snapshot)
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
                if index is None:
                    index = self._lexical_index(snapshot)
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
                                policy=policy)
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
        impacts = {}
        mentions = []
        claims = []
        for symbol in top:
            if symbol in followed:
                continue
            related = compiler.compile_related(self.store, symbol, snapshot=snapshot,
                                               policy=policy)
            for entry in related["impact"]["impacts"]:
                impacts.setdefault(entry["symbol"], entry)
            mentions.extend(related["mentions"]["mentions"])
            claims.extend(related["claims"]["claims"])
        seen = {(m.get("mentioner"), m.get("candidate")) for m in mentions}
        for mention in note_mentions:
            key = (mention.get("mentioner"), mention.get("candidate"))
            if key not in seen:
                seen.add(key)
                mentions.append(mention)
        mentions.sort(key=lambda m: (m["evidence"]["path"], m["evidence"]["start"], m["status"]))
        claims.sort(key=lambda c: (c["relation"], c["caller"], c["callee"] or ""))
        return {"task": task, "snapshot": snapshot, "policy": policy,
                "fusion": "weighted_rrf_k60", "provider": provider.name,
                "budget": caps, "threshold": threshold, "stages": stages,
                "candidates": [{"symbol": key,
                                "score": scores[key],
                                "match": min(origins[key], key=order.__getitem__),
                                "origins": origins[key]} for key in top],
                "impacts": [impacts[key] for key in sorted(impacts)],
                "mentions": mentions, "claims": claims,
                "selection": selection,
                "unknown_symbols": unknown_symbols,
                "outcome": "complete" if top else "empty"}

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
