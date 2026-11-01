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

import sys
from datetime import datetime, timezone

from .identity import digest as _digest
from .memory import Memory, _dispatch_covering

TRIAGE_VERSION = 1

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
        # cluster by observation site, not expression text: the task
        # carries path + line, so the same expression at distinct call
        # sites (or identical content in different files, which shares
        # a content digest) clusters separately, while recurrences of
        # one site stay one cluster.
        return ("unresolved_call", target, task)
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
        if _is_external_call(key[1]):
            info.update(closability=0.0, effort=EFFORT["external"],
                        impact=IMPACT["unresolved_call_external"],
                        queue="blocked",
                        reason="external stdlib symbol (%s): no graph action possible"
                               % (key[1],))
        else:
            info.update(closability=0.4, effort=EFFORT["investigate"],
                        impact=IMPACT["unresolved_call_local"],
                        queue="investigable",
                        reason="local unknown call; needs investigation")
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
    cluster_infos = []
    for key, members in clusters.items():
        occurrences = sum(m.get("recurrence", 1) for m in members
                          if m.get("status") not in ("resolved", "dismissed"))
        info = _assess({"key": key, "entries": members}, view, as_of)
        info["occurrences"] = occurrences
        info["example_ids"] = [m.get("id") for m in members[:3]]
        refs = sorted({(m.get("target") or "") for m in members if m.get("target")})
        info["targets"] = refs[:5]
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

    top_list = []
    for rank, row in enumerate(rankings["base"], 1):
        info = next(i for i in cluster_infos if list(i["key"]) == row["key"])
        top_list.append({"rank": rank, "kind": row["kind"],
                         "occurrences": row["occurrences"],
                         "queue": row["queue"], "score": row["score"],
                         "impact": info["impact"], "effort": info["effort"],
                         "closability": info["closability"],
                         "reason": row["reason"],
                         "example_ids": info["example_ids"],
                         "targets": info["targets"]})
    queues = {}
    for info in active:
        queues.setdefault(info["queue"], []).append(
            {"kind": info["kind"], "occurrences": info["occurrences"]})
    for queue in queues.values():
        queue.sort(key=lambda q: q["occurrences"], reverse=True)

    # The recommendation selects from the FULL active cluster set ranked
    # by the base weighting, never from top_list: a low-ranked but
    # immediately closable cluster must survive a small --top cutoff.
    ranked_active = sorted(active,
                           key=lambda i: (_score(i, i["occurrences"],
                                                 WEIGHTINGS["base"]),
                                          i["occurrences"]), reverse=True)

    def _candidate(info):
        return {"kind": info["kind"], "targets": info["targets"],
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
            "top": top_list,
            "recommendation": recommendation,
            "mutation_check": {"before": before, "after": after,
                               "writes": 0}}
