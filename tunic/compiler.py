"""Deterministic reverse-call witnesses and mechanically validated context IR."""

from collections import defaultdict, deque
import re

from .extract import TRAVERSABLE_CLAIMS, is_iso_date
from .identity import canonical, digest, identity

IR_VERSION = "tunic.w1.v3"
POLICIES = ("transparent", "evidence_only", "current")


def _evidence(store, record, snapshot):
    source = store.source(record["source"], snapshot)
    if digest(source["data"]) != source["digest"] or identity("source", [source["path"], source["digest"]]) != source["id"]:
        raise ValueError("source integrity failure")
    start, end = record["start"], record["end"]
    if not 0 <= start < end <= len(source["data"]):
        raise ValueError("invalid evidence coordinates")
    return {"source": source["id"], "path": source["path"], "digest": source["digest"],
            "start": start, "end": end, "excerpt": source["data"][start:end].decode("utf-8")}


def compile_impact(store, symbol, *, snapshot=None, max_depth=8, max_nodes=100, policy="transparent"):
    if type(max_depth) is not int or max_depth < 1 or type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_depth and max_nodes must be positive integers")
    snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown or unsupported symbol: " + symbol)
    if policy not in POLICIES:
        raise ValueError("unknown policy: " + str(policy))
    as_of = store.committed_on(snapshot)
    blocked = set()
    retracted = {}
    if policy == "current":
        for assertion in view["assertions"]:
            proposition = assertion["proposition"]
            if assertion.get("status") != "asserted_in_source":
                continue
            if proposition.get("relation") in ("supersedes", "corrects"):
                if (proposition.get("effective") or as_of) <= as_of:
                    blocked.add(proposition.get("callee"))
            elif proposition.get("relation") == "retracts":
                if (proposition.get("effective") or as_of) <= as_of:
                    key = (proposition.get("retract"), proposition.get("caller"),
                           proposition.get("callee"))
                    retracted[key] = retracted.get(key, 0) + 1
    supports = {}
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if (proposition.get("relation") in TRAVERSABLE_CLAIMS
                and assertion.get("status") == "asserted_in_source"):
            key = (proposition.get("relation"), proposition.get("caller"),
                   proposition.get("callee"))
            supports[key] = supports.get(key, 0) + 1
    incoming = defaultdict(list)
    for assertion in view["assertions"]:
        relation = assertion["proposition"].get("relation")
        if relation != "static_call" and not (relation in TRAVERSABLE_CLAIMS
                                              and assertion.get("status") == "asserted_in_source"):
            continue
        caller_key = assertion["proposition"]["caller"]
        callee_key = assertion["proposition"]["callee"]
        if policy == "evidence_only" and assertion["proposition"].get("relation") != "static_call":
            continue
        if policy == "current" and relation in TRAVERSABLE_CLAIMS:
            key = (relation, caller_key, callee_key)
            if supports.get(key, 0) - retracted.get(key, 0) < 1:
                continue
        if policy == "current" and (caller_key in blocked or callee_key in blocked):
            continue
        incoming[callee_key].append(assertion)
    for edges in incoming.values():
        edges.sort(key=lambda a:(a["proposition"]["caller"], a["id"]))
    paths = {symbol: []}
    queue = deque([symbol])
    bounds = set()
    cycles = False
    while queue:
        callee = queue.popleft()
        for edge in incoming[callee]:
            caller = edge["proposition"]["caller"]
            path = [edge] + paths[callee]
            endpoints = [a["proposition"]["caller"] for a in path] + [symbol]
            if len(set(endpoints)) != len(endpoints):
                cycles = True
                continue
            if caller in paths:
                continue
            if len(path) > max_depth:
                bounds.add("max_depth")
                continue
            if len(paths) >= max_nodes:
                bounds.add("max_nodes")
                continue
            paths[caller] = path
            queue.append(caller)
    used = {a["id"]: a for path in paths.values() for a in path}
    # Order units by stable edge coordinates, not by assertion hash, so call
    # labels survive extractor profile bumps when the edge set is unchanged.
    used = {aid: used[aid] for aid in sorted(used, key=lambda aid: (used[aid]["proposition"]["caller"],
            used[aid]["proposition"]["callee"], used[aid]["source"], used[aid]["start"], used[aid]["end"], aid))}
    impacts = []
    for key, path in sorted(paths.items()):
        if key == symbol:
            continue
        asserted = any(a["proposition"].get("relation") != "static_call" for a in path)
        impacts.append({"symbol": key, "kind": "possible_impact", "is_test": symbols[key]["is_test"],
                        "derivation": {"rule": "asserted_dependency_v1" if asserted
                                       else "reverse_static_reachability_v1",
                                       "premises": [a["id"] for a in path],
                                       "verification": "asserted_not_observed" if asserted
                                       else "syntactic_path_only"}})
    units = []
    for aid, a in used.items():
        units.append({"id": aid, "kind": "static_call_observation" if a["proposition"].get("relation") == "static_call" else "asserted_claim_observation", "assertion": a,
                      "evidence": _evidence(store, a, snapshot),
                      "resolution_support": [_evidence(store, premise, snapshot) for premise in a["resolution"]["support"]]})
    artifact = {
        "ir_version": IR_VERSION,
        "query": {"symbol": symbol, "max_depth": max_depth, "max_nodes": max_nodes, "policy": policy},
        "selection_receipt": {"snapshot": snapshot, "profile": view["profile"],
                              "policy": "reverse-static-call-bfs-v1", "reuse": "none_full_recompute",
                              "scope": "local_single_principal", "coverage": "bounded_static_analysis",
                              "diagnostics": view["diagnostics"], "bounds_hit": sorted(bounds), "cycles_observed": cycles},
        "target": {"entity": symbols[symbol], "evidence": _evidence(store, symbols[symbol], snapshot)},
        "units": units,
        "impacts": impacts,
        "validation_scope": "source_integrity_and_static_path_support_not_runtime_effect",
        "policy": policy,
        "outcome": "partial",
        "limitations": ["Python profile is an under-approximation; non-observation is not absence.",
                        "A static path establishes possible dependency, not demonstrated execution.",
                        "One shortest witness per caller; alternate paths are not enumerated."],
    }
    artifact["id"] = identity("context", artifact)
    return artifact


def compile_mentions(store, symbol, *, snapshot=None):
    """List document spans that mention a symbol key; proposals never traverse.

    A proposal counts as corroborated when the same document carries exact
    evidence for the same entity: an exact mention or an asserted claim.
    """
    snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown symbol: " + symbol)
    mentions = []
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") != "text_mention" or proposition.get("callee") != symbol:
            continue
        mentions.append({"mentioner": proposition["caller"],
                         "candidate": proposition.get("candidate"),
                         "status": assertion["status"],
                         "evidence": _evidence(store, assertion, snapshot),
                         "resolution": assertion["resolution"],
                         "source": assertion["source"]})
    confirmed = set()
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") == "text_mention" and assertion.get("status") == "syntactic_observation":
            confirmed.add((assertion["source"], proposition.get("callee")))
        elif proposition.get("relation") in TRAVERSABLE_CLAIMS and assertion.get("status") == "asserted_in_source":
            confirmed.add((assertion["source"], proposition.get("caller")))
            confirmed.add((assertion["source"], proposition.get("callee")))
    for mention in mentions:
        mention["corroborated"] = (mention.pop("source"), symbol) in confirmed
    mentions.sort(key=lambda m: (m["evidence"]["path"], m["evidence"]["start"], m["status"]))
    return {"symbol": symbol, "snapshot": snapshot, "profile": view["profile"],
            "mentions": mentions, "outcome": "complete" if mentions else "empty"}


def compile_drafts(store, document, *, snapshot=None):
    """Propose claim blocks for entity pairs co-mentioned in one document.

    Only exact mentions seed proposals, and pairs already linked by static
    paths or existing claims are skipped. Direction follows first mention and
    is always marked unverified: a draft is review input, never an assertion.
    """
    snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    mentioner = document + "::document"
    if mentioner not in symbols:
        raise ValueError("unknown document: " + document)
    first_seen = {}
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") != "text_mention" or assertion.get("status") != "syntactic_observation":
            continue
        if proposition.get("caller") != mentioner:
            continue
        key = proposition.get("callee")
        excerpt = _evidence(store, assertion, snapshot)["excerpt"]
        seen = (assertion["start"], excerpt.find(key))
        if key not in first_seen or seen < first_seen[key]:
            first_seen[key] = seen
    linked = set()
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") == "text_mention":
            continue
        linked.add((proposition.get("caller"), proposition.get("callee")))
        linked.add((proposition.get("callee"), proposition.get("caller")))
    drafts = []
    ordered = sorted(first_seen, key=lambda key: first_seen[key])
    for index, subject in enumerate(ordered):
        try:
            reachable = {i["symbol"] for i in compile_impact(store, subject, snapshot=snapshot)["impacts"]}
        except ValueError:
            continue
        for other in ordered[index + 1:]:
            if (subject, other) in linked or other in reachable:
                continue
            try:
                back = {i["symbol"] for i in compile_impact(store, other, snapshot=snapshot)["impacts"]}
            except ValueError:
                continue
            if subject in back:
                continue
            drafts.append({"rel": "depends_on", "subject": subject, "object": other,
                           "note": "direction unverified - confirm before saving"})
    return drafts


def compile_review(store, document, *, snapshot=None):
    """Structured acquisition report for one document; read-only.

    Separates observed facts (exact mentions with spans), agent assertions
    already recorded in the document, bare-name proposals awaiting identity
    resolution, novel co-mention drafts with supporting excerpts, and entity
    pairs blocked by an existing contradicts claim. Returns review input and
    asserts nothing; promotion is an explicit separate step.
    """
    snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    mentioner = document + "::document"
    if mentioner not in symbols:
        raise ValueError("unknown document: " + document)
    entities, asserted, proposals, candidates = [], [], [], []
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        evidence = _evidence(store, assertion, snapshot)
        if evidence["path"] != document:
            continue
        relation = proposition.get("relation")
        if relation == "text_mention" and assertion.get("status") == "syntactic_observation":
            entities.append({"symbol": proposition.get("callee"),
                             "status": assertion["status"], "evidence": evidence})
        elif relation not in ("static_call", "text_mention") and assertion.get("status") == "asserted_in_source":
            asserted.append({"relation": relation, "caller": proposition.get("caller"),
                             "callee": proposition.get("callee"),
                             "authority": proposition.get("authority"),
                             "effective": proposition.get("effective"),
                             "status": assertion["status"], "evidence": evidence})
        elif assertion.get("status") in ("proposed", "proposed_ambiguous"):
            proposals.append({"relation": relation, "caller": proposition.get("caller"),
                              "callee": proposition.get("callee"),
                              "candidate": proposition.get("candidate"),
                              "expression": assertion["resolution"].get("expression"),
                              "status": assertion["status"], "evidence": evidence})
    for diagnostic in view["diagnostics"]:
        if diagnostic.get("kind") != "mention_candidate" or diagnostic.get("path") != document:
            continue
        candidates.append({"expression": diagnostic.get("expression"),
                           "callee": diagnostic.get("callee"),
                           "candidate": diagnostic.get("candidate"),
                           "document_frequency": diagnostic.get("document_frequency"),
                           "signals": diagnostic.get("signals"),
                           "line": diagnostic.get("line")})
    candidates.sort(key=lambda c: (c["line"] or 0, c["expression"], c["callee"] or ""))
    entities.sort(key=lambda e: (e["evidence"]["start"], e["symbol"]))
    spans = {entity["symbol"]: entity["evidence"].get("excerpt", "") for entity in entities}
    drafts = []
    for draft in compile_drafts(store, document, snapshot=snapshot):
        drafts.append({**draft, "status": "novel",
                       "support": [spans.get(draft["subject"], ""),
                                   spans.get(draft["object"], "")]})
    mentioned = sorted({entity["symbol"] for entity in entities})
    conflicts = []
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") != "contradicts":
            continue
        pair = sorted([proposition.get("caller"), proposition.get("callee")])
        if len(pair) == 2 and pair[0] in mentioned and pair[1] in mentioned:
            conflicts.append({"subject": pair[0], "object": pair[1],
                              "claim": {"relation": "contradicts",
                                        "caller": proposition.get("caller"),
                                        "callee": proposition.get("callee"),
                                        "authority": proposition.get("authority"),
                                        "status": assertion.get("status")}})
    conflicts.sort(key=lambda c: (c["subject"], c["object"]))
    return {"document": document, "snapshot": snapshot, "profile": view["profile"],
            "entities": entities, "asserted": asserted, "proposals": proposals,
            "candidates": candidates,
            "drafts": drafts, "conflicts": conflicts, "outcome": "complete"}


def compile_related(store, symbol, *, snapshot=None, policy="transparent"):
    """Cross-source context: reverse dependencies plus document mentions, claims, and proposals."""
    snapshot, view = store.view(snapshot)
    if symbol not in {s["key"] for s in view["symbols"]}:
        raise ValueError("unknown symbol: " + symbol)
    return {"symbol": symbol, "snapshot": snapshot, "profile": view["profile"],
            "impact": compile_impact(store, symbol, snapshot=snapshot, policy=policy),
            "mentions": compile_mentions(store, symbol, snapshot=snapshot),
            "claims": compile_claims(store, symbol, snapshot=snapshot),
            "outcome": "complete"}


def compile_claims(store, symbol, *, snapshot=None):
    """List typed document claims and proposals touching a symbol; never traversed here."""
    snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown symbol: " + symbol)
    supports, retractions = {}, {}
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if assertion.get("status") != "asserted_in_source":
            continue
        if proposition.get("relation") in TRAVERSABLE_CLAIMS:
            key = (proposition.get("relation"), proposition.get("caller"),
                   proposition.get("callee"))
            supports[key] = supports.get(key, 0) + 1
        elif proposition.get("relation") == "retracts":
            key = (proposition.get("retract"), proposition.get("caller"),
                   proposition.get("callee"))
            retractions[key] = retractions.get(key, 0) + 1
    rows = []
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") in ("static_call", "text_mention"):
            continue
        if proposition.get("caller") != symbol and proposition.get("callee") != symbol:
            continue
        rows.append({"relation": proposition["relation"], "caller": proposition["caller"],
                     "callee": proposition["callee"], "candidate": proposition.get("candidate"),
                     "note": proposition.get("note"), "effective": proposition.get("effective"),
                     "authority": proposition.get("authority"), "status": assertion["status"],
                     "supports": supports.get((
                         proposition.get("relation"), proposition.get("caller"),
                         proposition.get("callee")), 0),
                     "retractions": retractions.get((
                         proposition.get("relation"), proposition.get("caller"),
                         proposition.get("callee")), 0),
                     "retract": proposition.get("retract"),
                     "evidence": _evidence(store, assertion, snapshot),
                     "resolution": assertion["resolution"]})
    rows.sort(key=lambda r: (r["relation"], r["caller"], r["callee"] or "",
                             r["evidence"]["path"], r["evidence"]["start"]))
    return {"symbol": symbol, "snapshot": snapshot, "profile": view["profile"],
            "claims": rows, "outcome": "complete" if rows else "empty"}


def compile_timeline(store, symbol, *, snapshot=None, at=None):
    """Bitemporal evidence view: valid-time `at` over transaction-time `snapshot`.

    Every entry carries `effective` (valid time, asserted or defaulted to the
    snapshot commit date) and `recorded` (transaction time: when this snapshot
    committed, i.e. what was known when). Superseded, corrected, and
    future-dated entries are filtered by `at`, never deleted; replaying an old
    snapshot reproduces exactly what was known then.
    """
    if at is not None and not is_iso_date(at):
        raise ValueError("at must be an ISO YYYY-MM-DD date")
    snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown symbol: " + symbol)
    current = store.committed_on(snapshot)
    as_of = at or current
    entries = []
    for claim in compile_claims(store, symbol, snapshot=snapshot)["claims"]:
        effective = claim.get("effective") or current
        if effective > as_of:
            continue
        entries.append({"kind": "claim", **claim, "effective": effective,
                        "recorded": current})
    for mention in compile_mentions(store, symbol, snapshot=snapshot)["mentions"]:
        if current > as_of:
            continue
        entries.append({"kind": "mention", **mention, "effective": current,
                        "recorded": current})
    entries.sort(key=lambda e: (e["effective"], e["kind"], e["evidence"]["path"], e["evidence"]["start"]))
    superseded_by = []
    corrected_by = []
    conflicts = []
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if assertion.get("status") != "asserted_in_source":
            continue
        relation = proposition.get("relation")
        effective = proposition.get("effective") or current
        if effective > as_of:
            continue
        if relation == "supersedes" and proposition.get("callee") == symbol:
            superseded_by.append({"by": proposition.get("caller"), "effective": effective,
                                  "evidence": _evidence(store, assertion, snapshot),
                                  "recorded": current})
        elif relation == "corrects" and proposition.get("callee") == symbol:
            corrected_by.append({"by": proposition.get("caller"), "effective": effective,
                                 "evidence": _evidence(store, assertion, snapshot),
                                 "recorded": current})
        elif relation == "contradicts" and (proposition.get("caller") == symbol
                                            or proposition.get("callee") == symbol):
            conflicts.append([proposition.get("caller"), proposition.get("callee")])
    superseded_by.sort(key=lambda s: (s["effective"], s["by"]))
    corrected_by.sort(key=lambda s: (s["effective"], s["by"]))
    conflicts.sort()
    return {"symbol": symbol, "snapshot": snapshot, "as_of": as_of, "recorded": current,
            "entries": entries,
            "superseded_by": superseded_by, "corrected_by": corrected_by,
            "conflicts": conflicts,
            "outcome": "complete" if entries or superseded_by or corrected_by or conflicts
                       else "empty"}


def compile_history(store, symbol):
    """Per-snapshot evolution: what each retained snapshot knew, in commit order.

    Symbols unknown to an old snapshot are skipped there, not backfilled:
    history shows first appearance, never retroactive knowledge.
    """
    rows = []
    for entry in store.snapshots():
        snapshot = entry["id"]
        try:
            timeline = compile_timeline(store, symbol, snapshot=snapshot)
        except ValueError:
            continue
        rows.append({"snapshot": snapshot, "committed_on": entry["committed_on"],
                     "entries": len(timeline["entries"]),
                     "superseded_by": timeline["superseded_by"],
                     "corrected_by": timeline["corrected_by"],
                     "conflicts": timeline["conflicts"],
                     "outcome": timeline["outcome"]})
    return {"symbol": symbol, "snapshots": rows,
            "outcome": "complete" if rows else "empty"}


def validate_artifact(store, artifact, *, expected_snapshot=None):
    """Recompile against immutable evidence; freshness is explicit, not implicit."""
    try:
        if not isinstance(artifact, dict):
            raise ValueError("artifact must be an object")
        payload = {k:v for k,v in artifact.items() if k != "id"}
        if artifact.get("id") != identity("context", payload):
            raise ValueError("artifact identity mismatch")
        if artifact.get("ir_version") != IR_VERSION:
            raise ValueError("unsupported IR version")
        receipt = artifact["selection_receipt"]
        snapshot = receipt["snapshot"]
        if expected_snapshot is not None and snapshot != expected_snapshot:
            raise ValueError("stale artifact: snapshot differs from expected view")
        _, view = store.view(snapshot)
        if identity("snapshot", view) != snapshot:
            raise ValueError("snapshot integrity failure")
        expected = compile_impact(store, **artifact["query"], snapshot=snapshot)
        if canonical(expected) != canonical(artifact):
            raise ValueError("artifact differs from supported deterministic compilation")
        return {"valid": True, "snapshot": snapshot, "scope": artifact["validation_scope"],
                "freshness": "expected_snapshot_matched" if expected_snapshot else "historical_snapshot_only"}
    except (ValueError, KeyError, TypeError, UnicodeError) as error:
        return {"valid": False, "error": str(error)}
