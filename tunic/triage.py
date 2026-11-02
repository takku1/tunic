"""Frontier triage: read-only prioritization over unresolved observations.

The ledger owns the entries; triage only projects them into clusters,
queues, and a ranked investigation list. analyze() never writes to the
database: it digests every frontier record before and after reading and
fails closed on any mismatch. A PRAGMA data_version pair brackets the
analysis too, so a write to ANY table by ANY connection is caught, not
just ledger tampering.

Clusters whose entries are all terminal (resolved/dismissed) carry zero
active occurrences: they stay in the historical counts but are excluded
from rankings, queues, and the recommendation — a closed cluster must
not be recommended again. The recommendation is selected from the full
active cluster set, never from the top-N display truncation, so a
low-ranked but immediately closable cluster is still found at --top 1.

Scoring is a provisional heuristic, not a calibrated probability:
P = (I^a * R^b * C^c) / (E^d) with documented factor rules and a
sensitivity pass over alternative weightings. Queues separate what Tunic
can act on now (supported replay predicates with evidence present),
what needs investigation, and what is blocked on missing capability or
is external noise. A high-priority blocked entry must generate a
capability task, never a fabricated proof.
"""

import builtins
import sys
from datetime import datetime, timezone

from .identity import digest as _digest
from .memory import Memory, _dispatch_covering

TRIAGE_VERSION = 2

BUILTIN_NAMES = set(dir(builtins))

# Standard investigation budget the score predicts against: at most
# RANK_SITES deduped sites examined, at most RANK_COMMITS governed
# commits per cluster. Y counts only what that budget can realize;
# E counts only the sites it touches. A 500-site file cannot
# out-yield a 2-site file by volume -- the harness in
# tunic/investigate.py enforces the same caps, so the score and the
# experiment cannot disagree about what "expected" means.
RANK_SITES = 5
RANK_COMMITS = 3

# Ranking v2 factor model. The score answers one question: how many
# new VERIFIABLE relationships will investigating this cluster
# recover, and at what cost? Raw occurrence counts never enter the
# score -- a hundred repeated builtins corroborate nothing.
#   Y  expected new verifiable relationships realizable under the
#      standard budget (top budgeted site weights, commit-capped)
#   Q  evidence quality bits (re-observed digests, candidate
#      explanations, non-default required evidence, asserted-graph
#      corroboration of the target)
#   L  retrieval/impact leverage (asserted fan-in on cluster files,
#      adjacency to retrieval-miss/abstention targets)
#   E  investigation cost in reviewer-sized steps for the budgeted
#      sites (queue effort plus investigated sites, not the cluster)
#   G  governance risk multiplier (1 clean, lower when refused or
#      retraction-adjacent)
# S = Y * (1 + Q) * (1 + L) * G / E. Every ranked row carries its
# factors and a reason naming the dominant driver, so the score is
# reproducible by hand from the same view.

# Factor rules. I = estimated impact of the underlying gap, R =
# occurrences (sum of recurrences: repeats bump recurrence instead of
# inserting, so raw entry counts understate coverage), C = estimated
# closability under a supported replay predicate, E = investigation
# effort in reviewer-sized steps. All provisional; the sensitivity pass
# shows how rankings move when the emphasis changes.
IMPACT = {"retrieval_miss": 3.0, "retrieval_abstention": 2.0,
          "unresolved_dispatch": 2.0, "parse_error": 1.0,
          "unresolved_call_local": 1.0, "unresolved_call_external": 0.5}
EFFORT = {"resolve_now": 1, "verify_commit": 2, "rank_check": 2,
          "await_evidence": 3, "investigate": 4, "extraction": 5,
          "external": 99}

WEIGHTINGS = {"base": (1, 1, 1, 1), "recall_heavy": (1, 2, 1, 1),
              "effort_averse": (1, 1, 1, 2), "impact_first": (2, 1, 1, 1)}


def _cluster_key(record):
    failure = record.get("failure", "?")
    target = record.get("target") or ""
    task = record.get("task") or ""
    if failure in ("static_edge_unresolved", "ingest_dispatch_unresolved"):
        return ("unresolved_dispatch", target)
    if failure == "ingest_unresolved_call":
        # Aggregate by file, not by site or expression: at real-repo
        # scale (PyQuifer: ~10.7k unresolved calls) per-site clusters
        # are singletons, which flattens occurrence-weighted ranking and
        # makes every weighting pass identical. File clusters keep every
        # site visible as a member (example_ids) while occurrences
        # aggregate into a signal that can rank. Grouping by expression
        # instead would merge unrelated causes (review finding).
        return ("unresolved_call", record.get("path") or target or task[:80])
    if failure == "retrieval_miss":
        return ("retrieval_miss", target)
    if failure == "retrieval_abstention":
        return ("retrieval_abstention", task)
    if failure == "ingest_parse_error":
        return ("parse_error", target)
    if failure in ("proposal_abstained", "reconcile_invalid", "reconcile_conflict"):
        return ("refused_proposal", failure, target or task[:80])
    return (failure, target or task[:80])


def _is_external_call(target):
    if not target or "/" in target or target.endswith(".py"):
        return False
    head = target.split(".")[0]
    return head in sys.stdlib_module_names


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def dedup_sites(entries):
    """Distinct investigation sites for RANKING ONLY.

    Key is (path, line, start, failure, target): the source location
    from the filing anchor plus diagnostic identity. Repeats of the
    same site -- recurrence bumps, re-ingests, multi-pass filings --
    count once. Pure function; stored records are never touched.
    """
    sites = {}
    for entry in entries:
        if entry.get("status") in ("resolved", "dismissed"):
            continue
        refs = entry.get("source_refs") or []
        line, start = entry.get("line"), None
        if refs:
            parts = str(refs[0]).split(":")
            if len(parts) > 1:
                line = parts[1]
            if len(parts) > 2:
                start = parts[2]
        key = (entry.get("path") or "", _to_int(line), _to_int(start),
               entry.get("failure"), entry.get("target"))
        sites.setdefault(key, []).append(entry.get("id"))
    return [{"path": k[0], "line": k[1], "start": k[2], "failure": k[3],
             "target": k[4], "entry_ids": sorted(v)}
            for k, v in sorted(sites.items(), key=lambda kv: str(kv[0]))]


def _symbol_leaves(view):
    leaves = set()
    for symbol in view.get("symbols", []):
        key = symbol.get("key") or ""
        tail = key.split("::")[-1]
        leaves.add(tail.split(".")[-1])
        leaves.add(tail)
    return leaves


def _symbol_leaf_counts(view):
    """Terminal-name match counts: unique (1) vs ambiguous (>1)."""
    counts = {}
    for symbol in view.get("symbols", []):
        key = symbol.get("key") or ""
        tail = key.split("::")[-1].split(".")[-1]
        counts[tail] = counts.get(tail, 0) + 1
    return counts


def _asserted_pairs(view):
    pairs = set()
    mentioned = set()
    for assertion in view.get("assertions", []):
        proposition = assertion.get("proposition", {})
        caller, callee = proposition.get("caller"), proposition.get("callee")
        if assertion.get("status") == "asserted_in_source" \
                and proposition.get("relation") in ("depends_on", "static_call") \
                and caller and callee:
            pairs.add((caller, callee))
            mentioned.add(caller)
            mentioned.add(callee)
        if proposition.get("relation") in ("text_mention", "claim",
                                           "depends_on"):
            for endpoint in (caller, callee):
                if endpoint:
                    mentioned.add(endpoint)
    return pairs, mentioned


def _retracted_endpoints(view):
    bad = set()
    for assertion in view.get("assertions", []):
        proposition = assertion.get("proposition", {})
        if proposition.get("relation") == "retracts":
            for endpoint in (proposition.get("caller"),
                             proposition.get("callee")):
                if endpoint:
                    bad.add(endpoint)
    return bad


def _site_weight(site, leaf_counts, leaves):
    """Verifiability of one deduped site: 1.0, 0.2, or 0.0.

    1.0 only when the target head names EXACTLY ONE graph symbol --
    the only case the bounded investigation can act on without human
    judgment. Everything else scores 0.0: ambiguous heads need
    disambiguation the budget does not cover, and builtins, stdlib,
    and symbol-less heads can never become edges.
    """
    target = site.get("target") or ""
    head = target.split(".")[0]
    if not head or head in BUILTIN_NAMES:
        return 0.0
    if head in sys.stdlib_module_names:
        return 0.0
    matches = leaf_counts.get(head, 0)
    if target in leaves:
        matches = max(matches, 1)
    if matches == 1:
        return 1.0
    return 0.0


def _assess(cluster, view, as_of):
    """Closability, effort, impact, queue, and reason for one cluster."""
    key, entries = cluster["key"], cluster["entries"]
    kind = key[0]
    open_entries = [e for e in entries
                    if e.get("status") not in ("resolved", "dismissed")]
    info = {"kind": kind, "key": list(key), "entries": len(entries),
            "open": len(open_entries),
            "terminal": len(entries) - len(open_entries)}
    if kind == "retrieval_miss":
        target = key[1]
        present = any(s.get("key") == target for s in view.get("symbols", []))
        if present:
            info.update(closability=0.7, effort=EFFORT["rank_check"],
                        impact=IMPACT["retrieval_miss"], queue="investigable",
                        reason="predicate supported; replay rank check needed")
        else:
            info.update(closability=0.2, effort=EFFORT["investigate"],
                        impact=IMPACT["retrieval_miss"], queue="blocked",
                        reason="verified target left the graph")
    elif kind == "retrieval_abstention":
        info.update(closability=0.5, effort=EFFORT["await_evidence"],
                    impact=IMPACT["retrieval_abstention"], queue="investigable",
                    reason="resolves when evidence arrives; nothing to verify yet")
    elif kind == "unresolved_dispatch":
        table = key[1]
        _candidates, covering = _dispatch_covering(view, table, as_of)
        if covering:
            pairs = sorted({(c["proposition"]["caller"],
                             c["proposition"]["callee"]) for c in covering})
            info.update(closability=1.0, effort=EFFORT["resolve_now"],
                        impact=IMPACT["unresolved_dispatch"], queue="closable_now",
                        reason="asserted edge already covers %s; replay resolves now"
                               % (pairs,))
        elif _candidates:
            info.update(closability=0.5, effort=EFFORT["verify_commit"],
                        impact=IMPACT["unresolved_dispatch"], queue="investigable",
                        reason="candidate present without asserted cover; "
                               "verify and commit, then replay")
        else:
            info.update(closability=0.2, effort=EFFORT["extraction"],
                        impact=IMPACT["unresolved_dispatch"], queue="blocked",
                        reason="no emittable candidate: needs extraction "
                               "capability or human-verified commit")
    elif kind == "unresolved_call":
        # A file cluster mixes targets; block only when EVERY open
        # member is an external stdlib symbol, otherwise the file
        # contains at least one actionable call.
        open_targets = [(m.get("target") or "") for m in open_entries]
        if open_targets and all(_is_external_call(t) for t in open_targets):
            info.update(closability=0.0, effort=EFFORT["external"],
                        impact=IMPACT["unresolved_call_external"],
                        queue="blocked",
                        reason="all open calls are external stdlib symbols "
                               "(e.g. %s): no graph action possible"
                               % (open_targets[0],))
        else:
            info.update(closability=0.4, effort=EFFORT["investigate"],
                        impact=IMPACT["unresolved_call_local"],
                        queue="investigable",
                        reason="%d unresolved call site(s) in the file; "
                               "needs investigation" % (len(open_entries),))
    elif kind == "parse_error":
        info.update(closability=0.4, effort=EFFORT["investigate"],
                    impact=IMPACT["parse_error"], queue="investigable",
                    reason="source file fails to parse upstream")
    elif kind == "refused_proposal":
        info.update(closability=0.4, effort=EFFORT["investigate"],
                    impact=1.0, queue="investigable",
                    reason="proposal refused at the gate; review the conflict")
    else:
        info.update(closability=0.1, effort=EFFORT["investigate"],
                    impact=1.0, queue="blocked",
                    reason="no replay rule for this failure kind")
    return info


def _score(info, occurrences, weights):
    a, b, c, d = weights
    return ((info["impact"] ** a) * (occurrences ** b)
            * (max(info["closability"], 1e-9) ** c)
            / (info["effort"] ** d))


def _evidence_bits(cluster_key, entries, view_targets):
    """Independent corroboration bits for one cluster (0-4).

    b1 re-observed across distinct source digests; b2 recorded
    candidate explanations; b3 non-default required evidence; b4
    target corroborated by the asserted graph. Raw occurrence counts
    are deliberately absent: repeats without new evidence add no bit.
    """
    open_entries = [e for e in entries
                    if e.get("status") not in ("resolved", "dismissed")]
    digests = {str((e.get("source_refs") or [""])[0]).split(":")[0]
               for e in open_entries if e.get("source_refs")}
    digests.discard("")
    b1 = 1 if len(digests) > 1 else 0
    b2 = 1 if any(e.get("candidate_explanations") for e in open_entries) else 0
    defaults = (["supporting evidence"],
                ["rephrase the task", "add supporting evidence"])
    stated = [list(e.get("required_evidence") or []) for e in open_entries]
    b3 = 1 if any(s and s not in defaults for s in stated) else 0
    heads = {(e.get("target") or "").split(".")[0] for e in open_entries}
    heads.discard("")
    b4 = 1 if (heads & view_targets) else 0
    return b1 + b2 + b3 + b4, [b1, b2, b3, b4]


def _leverage(cluster_files, site_targets, asserted_pairs,
              retrieval_targets):
    """Retrieval/impact leverage: asserted fan-in (cap 5) plus
    adjacency to known retrieval gaps (+2). Counts graph structure,
    never diagnostic volume."""
    fan = sum(1 for caller, callee in asserted_pairs
              if any(caller.startswith(f) or callee.startswith(f)
                     for f in cluster_files))
    adjacent = 2 if (set(site_targets) & retrieval_targets) else 0
    return min(5, fan) + adjacent


def score_v2(factors):
    """S = Y * (1 + Q) * (1 + L) * G / E. Pure; see module docstring."""
    return (factors["Y"] * (1 + factors["Q"]) * (1 + factors["L"])
            * factors["G"] / factors["E"])


def _assess_v2(info, entries, view, context):
    """Score one cluster under the v2 factor model.

    context carries precomputed view indexes (symbol leaves and
    leaf match counts, asserted pairs, mentioned endpoint leaves,
    retracted endpoints, retrieval targets). Returns (factors,
    reason); never writes.
    """
    key, kind = info["key"], info["kind"]
    sites = dedup_sites(entries)
    site_targets = [s["target"] for s in sites if s["target"]]
    cluster_files = {s["path"] for s in sites if s["path"]}
    leaves = context["leaves"]
    leaf_counts = context["leaf_counts"]
    pairs, mentioned = context["pairs"], context["mentioned"]

    if kind == "unresolved_dispatch":
        _candidates, covering = _dispatch_covering(
            view, key[1], context["as_of"])
        if covering:
            new_yield = 1.0
            yield_why = "asserted edge already covers the table"
        elif _candidates:
            new_yield = 0.5
            yield_why = "proposed candidate awaits verification"
        else:
            new_yield = 0.1
            yield_why = "no emittable candidate yet"
    elif kind == "unresolved_call":
        weights = sorted((_site_weight(s, leaf_counts, leaves)
                            for s in sites),
                           reverse=True)[:RANK_SITES]
        new_yield = round(min(float(RANK_COMMITS), sum(weights)), 2)
        verifiable = sum(1 for w in weights if w >= 1.0)
        yield_why = ("%d of %d budgeted site(s) name an existing symbol"
                     % (verifiable, len(weights)))
    elif kind == "retrieval_miss":
        target = key[1]
        present = any(s.get("key") == target for s in view.get("symbols", []))
        new_yield = 1.0 if present else 0.0
        yield_why = ("verified target present" if present
                     else "verified target left the graph")
    elif kind == "retrieval_abstention":
        new_yield = 0.5
        yield_why = "one task to re-run when evidence arrives"
    elif kind == "parse_error":
        new_yield = 0.3
        yield_why = "fixing the parse restores its symbols"
    elif kind == "refused_proposal":
        new_yield = 0.3
        yield_why = "reviewing the conflict may recover the edge"
    else:
        new_yield = 0.1
        yield_why = "no replay rule for this failure kind"

    quality, bits = _evidence_bits(key, entries, mentioned)
    leverage = _leverage(cluster_files, site_targets, pairs,
                         context["retrieval_targets"])
    cost = info["effort"] + min(len(sites), RANK_SITES)
    risk, risk_why = 1.0, "clean"
    if kind == "refused_proposal":
        risk, risk_why = 0.5, "proposal refused at the gate"
    elif kind in ("contradictory",) or any(
            e.get("kind") == "contradictory" for e in entries
            if e.get("status") not in ("resolved", "dismissed")):
        risk, risk_why = 0.7, "contradictory observation in cluster"
    elif set(site_targets) & context["retracted"]:
        risk, risk_why = 0.7, "target touches a retracted endpoint"

    factors = {"Y": new_yield, "Q": quality, "L": leverage,
               "E": cost, "G": risk}
    dominant = max(("Y", new_yield), ("Q", quality), ("L", leverage),
                   key=lambda item: item[1])[0]
    reason = ("%s; evidence bits %s; leverage %d; cost %d step(s); "
              "governance %s" % (yield_why, bits, leverage, cost, risk_why))
    return factors, reason, dominant


def analyze(db_path, root=".", top=20):
    """Read-only triage of a frontier database. Returns a report dict.

    Never mutates the database: digests every frontier record before and
    after analysis and raises on any mismatch. Report carries the
    snapshot analyzed, per-kind counts, normalized clusters with
    occurrence-weighted scores, three queues, four weighting passes with
    top-N overlap against base, a top-N ranked list with reasons, and a
    recommended second-closure candidate (or what it would take).
    Clusters with zero active occurrences (every entry terminal) are
    excluded from rankings, queues, and the recommendation; selection
    for the recommendation runs over the full active cluster set, not
    the top-N display truncation.
    """
    memory = Memory(db_path, root)
    try:
        def db_state():
            rows = memory.store.connection.execute(
                "SELECT id, record FROM frontier ORDER BY id").fetchall()
            frontier_digest = _digest(
                "".join(i + r for i, r in rows).encode("utf-8"))
            count = memory.store.connection.execute(
                "SELECT count(*) FROM frontier").fetchone()[0]
            # data_version moves when ANY connection commits a write to
            # ANY table, so a non-ledger write (graph, notes) is caught
            # as well as frontier tampering.
            version = memory.store.connection.execute(
                "PRAGMA data_version").fetchone()[0]
            return frontier_digest, count, version

        before = db_state()
        snapshot, view = memory.store.view()
        as_of = memory.store.committed_on(snapshot)
        # frontier_list() is the authorized read path and returns parsed
        # records; the digest check above anchors on the raw stored text.
        records = memory.frontier_list()
        after = db_state()
        if before != after:
            raise AssertionError("triage observed a database write during "
                                 "analysis; refusing report")
    finally:
        memory.close()

    by_failure, by_kind, by_status = {}, {}, {}
    for record in records:
        by_failure[record.get("failure")] = by_failure.get(record.get("failure"), 0) + 1
        by_status[record.get("status")] = by_status.get(record.get("status"), 0) + 1
    clusters = {}
    for record in records:
        key = _cluster_key(record)
        clusters.setdefault(key, []).append(record)
    leaves = _symbol_leaves(view)
    asserted_pairs, mentioned = _asserted_pairs(view)
    mentioned_leaves = {e.split("::")[-1].split(".")[-1] for e in mentioned}
    mentioned_leaves |= {e.split("::")[-1] for e in mentioned}
    retrieval_targets = set()
    for record in records:
        if record.get("failure") == "retrieval_miss" and record.get("target"):
            retrieval_targets.add(record.get("target"))
        if record.get("failure") == "retrieval_abstention":
            retrieval_targets.add(record.get("task"))
    context = {"leaves": leaves, "leaf_counts": _symbol_leaf_counts(view),
               "pairs": asserted_pairs, "mentioned": mentioned_leaves,
               "retracted": _retracted_endpoints(view),
               "retrieval_targets": retrieval_targets, "as_of": as_of}
    cluster_infos = []
    for key, members in clusters.items():
        occurrences = sum(m.get("recurrence", 1) for m in members
                          if m.get("status") not in ("resolved", "dismissed"))
        info = _assess({"key": key, "entries": members}, view, as_of)
        info["occurrences"] = occurrences
        info["example_ids"] = [m.get("id") for m in members[:3]]
        refs = sorted({(m.get("target") or "") for m in members if m.get("target")})
        info["targets"] = refs[:5]
        factors, reason, _dominant = _assess_v2(info, members, view, context)
        info["factors"] = factors
        info["v2_reason"] = reason
        info["v2_score"] = round(score_v2(factors), 4)
        info["distinct_sites"] = len(dedup_sites(members))
        by_kind[info["kind"]] = by_kind.get(info["kind"], 0) + 1
        cluster_infos.append(info)

    # Active work only: clusters whose entries are all terminal carry
    # zero occurrences and stay in the historical counts above, but a
    # closed cluster must not rank, queue, or be recommended again.
    active = [i for i in cluster_infos if i["occurrences"] > 0]
    rankings = {}
    for name, weights in WEIGHTINGS.items():
        ranked = sorted(active,
                        key=lambda i: (_score(i, i["occurrences"], weights),
                                       i["occurrences"]), reverse=True)
        rankings[name] = [
            {"kind": i["kind"], "key": i["key"], "occurrences": i["occurrences"],
             "queue": i["queue"], "score": round(_score(i, i["occurrences"],
                                                        weights), 4),
             "reason": i["reason"]} for i in ranked[:top]]
    base_ids = {tuple(r["key"]) for r in rankings["base"]}
    overlap = {name: (1.0 if not base_ids else
                      round(len({tuple(r["key"]) for r in ranks} & base_ids)
                            / len(base_ids), 3))
               for name, ranks in rankings.items()}

    # Ranking v1 display order: verifiability over noise. `rankings`
    # above stays the legacy occurrence-weighted sensitivity analysis;
    # overlap_vs_v2 shows how each legacy weighting's top-N survives
    # the re-score, so a silent model change cannot hide.
    # Score descends; ties prefer fewer distinct sites (finishable
    # clusters first), then key order for determinism. Raw occurrence
    # counts never break ties.
    v2_ranked = sorted(
        active,
        key=lambda i: (-i["v2_score"], i["distinct_sites"],
                       str(i["key"])))
    v2_ids = {tuple(i["key"]) for i in v2_ranked[:top]}
    overlap_vs_v2 = {name: (1.0 if not v2_ids else
                            round(len({tuple(r["key"]) for r in ranks}
                                      & v2_ids) / len(v2_ids), 3))
                     for name, ranks in rankings.items()}

    top_list = []
    for rank, info in enumerate(v2_ranked[:top], 1):
        top_list.append({"rank": rank, "kind": info["kind"],
                         "key": info["key"],
                         "occurrences": info["occurrences"],
                         "distinct_sites": info["distinct_sites"],
                         "queue": info["queue"], "score": info["v2_score"],
                         "factors": info["factors"],
                         "queue_reason": info["reason"],
                         "impact": info["impact"], "effort": info["effort"],
                         "closability": info["closability"],
                         "reason": info["v2_reason"],
                         "example_ids": info["example_ids"],
                         "targets": info["targets"]})
    queues = {}
    for info in active:
        queues.setdefault(info["queue"], []).append(
            {"kind": info["kind"], "occurrences": info["occurrences"]})
    for queue in queues.values():
        queue.sort(key=lambda q: q["occurrences"], reverse=True)

    # The recommendation selects from the FULL active cluster set ranked
    # by the v2 score, never from top_list: a low-ranked but
    # immediately closable cluster must survive a small --top cutoff.
    ranked_active = sorted(
        active,
        key=lambda i: (-i["v2_score"], i["distinct_sites"],
                       str(i["key"])))

    def _candidate(info):
        return {"kind": info["kind"], "key": info["key"],
                "targets": info["targets"],
                "occurrences": info["occurrences"]}

    closable = [c for c in ranked_active if c["queue"] == "closable_now"]
    if closable:
        pick = closable[0]
        recommendation = {
            "candidate": _candidate(pick),
            "plan": "run frontier_replay on an entry, verify resolved with "
                    "proof, export the regression case",
            "verifiable_target": pick["targets"][0] if pick["targets"] else None}
    else:
        candidates = [c for c in ranked_active if c["queue"] == "investigable"]
        pick = candidates[0] if candidates else None
        recommendation = {
            "candidate": _candidate(pick) if pick else None,
            "plan": ("no closable_now cluster: closest needs "
                     + (pick["reason"] if pick else "nothing actionable")),
            "verifiable_target": None}

    return {"version": TRIAGE_VERSION,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "snapshot": snapshot,
            "counts": {"entries": len(records),
                       "clusters": len(cluster_infos),
                       "active_clusters": len(active),
                       "by_failure": by_failure, "by_kind": by_kind,
                       "by_status": by_status},
            "queues": {name: {"clusters": len(items),
                              "occurrences": sum(i["occurrences"] for i in items)}
                       for name, items in queues.items()},
            "rankings_overlap_vs_base": overlap,
            "rankings_overlap_vs_v2": overlap_vs_v2,
            "top": top_list,
            "recommendation": recommendation,
            "mutation_check": {"before": before, "after": after,
                               "writes": 0}}
