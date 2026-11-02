"""Memory lifecycle v1: consolidation + retrieval suppression.

Consolidation is additive: derived support summaries compact redundant
corroboration with exact source links but never enter the answer path,
supports arithmetic, traversal, or timeline evidence. Summaries are
valid only for their derived snapshot.

Suppression is reversible policy-governed retrieval invisibility, not
deletion: suppressed assertions stay stored but contribute to no
traversal, selection, rendering, or successor reasoning. Tombstones
record metadata only, never proposition content.
"""

import json
from datetime import datetime, timezone

from .identity import canonical, identity

CONSOLIDATION_RULE = "support_consolidation_v1"

GOVERNING_RELATIONS = frozenset({"supersedes", "contradicts", "retracts"})

ELIGIBLE_STATUSES = frozenset({"asserted_in_source"})

SUPPRESSION_POLICIES = frozenset(
    {"governance-review", "retention-expiry", "user-request"})


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def eligible_groups(view):
    """Group asserted non-governing supports by proposition.

    Returns {proposition_key: [assertion records]} for groups with >= 2
    asserted supports. Governing assertions never group. Deterministic.
    """
    groups = {}
    for assertion in view["assertions"]:
        if assertion.get("status") not in ELIGIBLE_STATUSES:
            continue
        proposition = assertion.get("proposition", {})
        relation = proposition.get("relation")
        if relation in GOVERNING_RELATIONS:
            continue
        key = (relation, proposition.get("caller"), proposition.get("callee"))
        if len(key) != 3 or key[0] is None:
            continue
        groups.setdefault(key, []).append(assertion)
    return {key: sorted(rows, key=lambda a: a["id"])
            for key, rows in groups.items() if len(rows) >= 2}


def _governing_flags(view, caller, callee):
    """Whether live governance touches this proposition's endpoints."""
    superseded = contradicted = retracted = False
    for assertion in view["assertions"]:
        if assertion.get("status") not in ELIGIBLE_STATUSES:
            continue
        proposition = assertion.get("proposition", {})
        relation = proposition.get("relation")
        endpoints = {proposition.get("caller"), proposition.get("callee")}
        if not {caller, callee} & endpoints:
            continue
        if relation == "supersedes":
            superseded = True
        elif relation == "contradicts":
            contradicted = True
        elif (relation == "retracts" and
              proposition.get("retract") in ("depends_on", "static_call")):
            retracted = True
    return {"superseded": superseded, "contradicted": contradicted,
            "retracted": retracted}


def consolidate(memory, snapshot=None):
    """Derive support summaries for the snapshot. Idempotent.

    Writes one `consolidated_supports` row per eligible proposition
    plus a `consolidation_receipts` row. Re-running on the same
    snapshot reuses identical rows. Returns a JSON-serializable
    report. Never touches assertions, snapshots, or the answer path.
    """
    store = memory.store
    snapshot, view = store.view(snapshot)
    groups = eligible_groups(view)
    summaries = []
    for (relation, caller, callee), rows in sorted(groups.items()):
        inputs = [row["id"] for row in rows]
        sources = sorted({row.get("source") for row in rows
                          if row.get("source")})
        authorities = sorted({row.get("proposition", {}).get("authority")
                              for row in rows
                              if row.get("proposition", {}).get("authority")})
        summary = {
            "id": identity("consolidated", {
                "rule": CONSOLIDATION_RULE,
                "proposition": {"relation": relation, "caller": caller,
                                "callee": callee},
                "inputs": inputs}),
            "rule": CONSOLIDATION_RULE,
            "snapshot": snapshot,
            "status": "derived_consolidated",
            "proposition": {"relation": relation, "caller": caller,
                            "callee": callee},
            "support_count": len(rows),
            "inputs": inputs,
            "sources": sources,
            "authorities": authorities,
            "governing": _governing_flags(view, caller, callee),
        }
        summaries.append(summary)
    receipt = {"snapshot": snapshot, "rule": CONSOLIDATION_RULE,
               "groups": len(summaries),
               "summary_ids": sorted(s["id"] for s in summaries),
               "derived_at": _now()}
    with store.connection:
        for summary in summaries:
            store.connection.execute(
                "INSERT OR IGNORE INTO consolidated_supports VALUES (?,?,?)",
                (summary["id"], snapshot, canonical(summary)))
        store.connection.execute(
            "INSERT OR REPLACE INTO consolidation_receipts VALUES (?,?)",
            (snapshot, canonical(receipt)))
    return {"snapshot": snapshot, "rule": CONSOLIDATION_RULE,
            "groups": len(summaries),
            "summary_ids": sorted(s["id"] for s in summaries)}


def summarize_supports(memory, symbol=None, snapshot=None):
    """Read consolidated support summaries for a snapshot.

    Returns {"status": "current", "summaries": [...]} when a receipt
    covers the requested snapshot, else {"status": "stale",
    "summaries": []}. Never scans live assertions as a fallback: a
    stale receipt means reconsolidation is due. Summaries carry full
    source links and governing flags; they are views, not evidence.
    """
    store = memory.store
    snapshot = snapshot or store.current()
    row = store.connection.execute(
        "SELECT record FROM consolidation_receipts WHERE snapshot=?",
        (snapshot,)).fetchone()
    if row is None:
        return {"status": "stale", "snapshot": snapshot, "summaries": [],
                "receipt": None}
    receipt = json.loads(row[0])
    rows = store.connection.execute(
        "SELECT record FROM consolidated_supports WHERE snapshot=? "
        "ORDER BY id", (snapshot,)).fetchall()
    summaries = [json.loads(row[0]) for row in rows]
    if symbol is not None:
        summaries = [s for s in summaries
                     if symbol in (s["proposition"]["caller"],
                                   s["proposition"]["callee"])]
    return {"status": "current", "snapshot": snapshot,
            "summaries": summaries, "receipt": receipt}


def suppressed_ids(store, snapshot=None):
    """Assertion ids hidden by active suppressions. Single source for
    every enforcement point (selection, compiler, timeline).

    Each tombstone covers its named assertion plus the cascade
    computed at suppress time: same-triple twins (any status) and
    same-source mentions of a suppressed endpoint. The cascade is
    explicit in the tombstone, never recomputed silently.
    """
    covered = set()
    rows = store.connection.execute(
        "SELECT record FROM suppressions WHERE "
        "json_extract(record, '$.status') = 'active'").fetchall()
    for row in rows:
        record = json.loads(row[0])
        covered.add(record["assertion"])
        covered.update(record.get("covers", []))
    return covered


def _covers(view, ids):
    """Cascade set for newly suppressed ids: the ids themselves, every
    assertion with an identical (relation, caller, callee) triple, and
    every text_mention from a suppressed source document that names a
    suppressed endpoint. Static observations with different callers
    (e.g. a call-site candidate) are never covered.
    """
    by_id = {a["id"]: a for a in view["assertions"]}
    triples = set()
    sources = set()
    endpoints = set()
    for aid in ids:
        proposition = (by_id[aid].get("proposition") or {})
        triples.add((proposition.get("relation"),
                     proposition.get("caller"), proposition.get("callee")))
        if by_id[aid].get("source"):
            sources.add(by_id[aid]["source"])
        endpoints.update([proposition.get("caller"),
                          proposition.get("callee")])
    endpoints.discard(None)
    covered = set(ids)
    for assertion in view["assertions"]:
        proposition = (assertion.get("proposition") or {})
        if (proposition.get("relation"), proposition.get("caller"),
                proposition.get("callee")) in triples:
            covered.add(assertion["id"])
        elif proposition.get("relation") == "text_mention" \
                and assertion.get("source") in sources \
                and proposition.get("callee") in endpoints:
            covered.add(assertion["id"])
    return sorted(covered)


def suppress(memory, assertion_ids, policy, reason):
    """Suppress asserted evidence from retrieval (reversible, governed).

    Validates every id before writing anything: unknown ids, empty
    policy/reason, unknown policies, and governing assertions
    (supersedes/contradicts/retracts) are refused with ValueError and
    no partial writes. Suppressing an already-active id is an
    idempotent no-op. Returns a JSON-serializable report.
    """
    from .memory import authorize
    authorize(memory.principal, "suppress")
    ids = sorted(set(assertion_ids))
    if not ids:
        raise ValueError("suppress requires at least one assertion id")
    if not isinstance(policy, str) or not policy.strip():
        raise ValueError("suppress requires a non-empty policy")
    if policy not in SUPPRESSION_POLICIES:
        raise ValueError("unknown suppression policy: %r" % (policy,))
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("suppress requires a non-empty reason")
    store = memory.store
    snapshot, view = store.view()
    by_id = {a["id"]: a for a in view["assertions"]}
    for aid in ids:
        assertion = by_id.get(aid)
        if assertion is None:
            raise ValueError("unknown assertion: %s" % (aid,))
        relation = (assertion.get("proposition") or {}).get("relation")
        if relation in GOVERNING_RELATIONS:
            proposition = assertion.get("proposition") or {}
            raise ValueError(
                "refusing to suppress governing %s %s %s; suppressing "
                "governance would resurrect the evidence it governs"
                % (relation, proposition.get("caller"),
                   proposition.get("callee")))
    covered = _covers(view, ids)
    with store.connection:
        for aid in ids:
            row = store.connection.execute(
                "SELECT record FROM suppressions WHERE assertion=?",
                (aid,)).fetchone()
            if row is not None and json.loads(
                    row[0]).get("status") == "active":
                continue
            stone = {
                "id": identity("suppression", {"assertion": aid}),
                "assertion": aid,
                "covers": covered,
                "policy": policy,
                "reason": reason,
                "actor": memory.principal or "local",
                "created_at": _now(),
                "snapshot": snapshot,
                "status": "active",
            }
            store.connection.execute(
                "INSERT OR REPLACE INTO suppressions VALUES (?,?,?)",
                (stone["id"], aid, canonical(stone)))
    return {"suppressed": ids, "covered": covered, "snapshot": snapshot,
            "policy": policy}


def unsuppress(memory, assertion_ids, reason):
    """Restore suppressed assertions to retrieval. Records the reversal.

    Unknown ids, never-suppressed ids, and empty reasons are refused.
    Already-visible ids are idempotent no-ops.
    """
    from .memory import authorize
    authorize(memory.principal, "unsuppress")
    ids = sorted(set(assertion_ids))
    if not ids:
        raise ValueError("unsuppress requires at least one assertion id")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("unsuppress requires a non-empty reason")
    store = memory.store
    with store.connection:
        for aid in ids:
            row = store.connection.execute(
                "SELECT record FROM suppressions WHERE assertion=?",
                (aid,)).fetchone()
            if row is None:
                raise ValueError("assertion was never suppressed: %s"
                                 % (aid,))
            stone = json.loads(row[0])
            if stone.get("status") != "active":
                continue
            stone["status"] = "reversed"
            stone["reversed_at"] = _now()
            stone["reverse_reason"] = reason
            stone["reverse_actor"] = memory.principal or "local"
            store.connection.execute(
                "UPDATE suppressions SET record=? WHERE assertion=?",
                (canonical(stone), aid))
    return {"unsuppressed": ids}


def review_suppressions(memory):
    """Audit the suppression ledger. Metadata only: policy, reason,
    actor, timestamps, snapshot, status. Never caller/callee content."""
    from .memory import authorize
    authorize(memory.principal, "review_suppressions")
    store = memory.store
    rows = store.connection.execute(
        "SELECT record FROM suppressions ORDER BY "
        "json_extract(record, '$.created_at'), assertion").fetchall()
    stones = []
    for row in rows:
        record = json.loads(row[0])
        stones.append({
            "id": record["id"],
            "covers": record.get("covers", [record["assertion"]]),
            "policy": record["policy"],
            "reason": record["reason"],
            "actor": record["actor"],
            "created_at": record["created_at"],
            "snapshot": record["snapshot"],
            "status": record["status"],
            "reversed_at": record.get("reversed_at"),
            "reverse_reason": record.get("reverse_reason"),
        })
    return {"tombstones": stones,
            "active": sum(1 for s in stones if s["status"] == "active")}
