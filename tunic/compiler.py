"""Deterministic reverse-call witnesses and mechanically validated context IR."""

from collections import defaultdict, deque

from .identity import canonical, digest, identity

IR_VERSION = "tunic.w1.v1"


def _evidence(store, record, snapshot):
    source = store.source(record["source"], snapshot)
    if digest(source["data"]) != source["digest"] or identity("source", [source["path"], source["digest"]]) != source["id"]:
        raise ValueError("source integrity failure")
    start, end = record["start"], record["end"]
    if not 0 <= start < end <= len(source["data"]):
        raise ValueError("invalid evidence coordinates")
    return {"source": source["id"], "path": source["path"], "digest": source["digest"],
            "start": start, "end": end, "excerpt": source["data"][start:end].decode("utf-8")}


def compile_impact(store, symbol, *, snapshot=None, max_depth=8, max_nodes=100):
    if type(max_depth) is not int or max_depth < 1 or type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_depth and max_nodes must be positive integers")
    snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown or unsupported symbol: " + symbol)
    incoming = defaultdict(list)
    for assertion in view["assertions"]:
        if assertion["proposition"].get("relation") != "static_call":
            continue
        incoming[assertion["proposition"]["callee"]].append(assertion)
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
    units = []
    for aid, a in sorted(used.items()):
        units.append({"id": aid, "kind": "static_call_observation", "assertion": a,
                      "evidence": _evidence(store, a, snapshot),
                      "resolution_support": [_evidence(store, premise, snapshot) for premise in a["resolution"]["support"]]})
    artifact = {
        "ir_version": IR_VERSION,
        "query": {"symbol": symbol, "max_depth": max_depth, "max_nodes": max_nodes},
        "selection_receipt": {"snapshot": snapshot, "profile": view["profile"],
                              "policy": "reverse-static-call-bfs-v1", "reuse": "none_full_recompute",
                              "scope": "local_single_principal", "coverage": "bounded_static_analysis",
                              "diagnostics": view["diagnostics"], "bounds_hit": sorted(bounds), "cycles_observed": cycles},
        "target": {"entity": symbols[symbol], "evidence": _evidence(store, symbols[symbol], snapshot)},
        "units": units,
        "impacts": [{"symbol": key, "kind": "possible_impact", "is_test": symbols[key]["is_test"],
                     "derivation": {"rule": "reverse_static_reachability_v1", "premises": [a["id"] for a in path],
                                    "verification": "syntactic_path_only"}}
                    for key, path in sorted(paths.items()) if key != symbol],
        "validation_scope": "source_integrity_and_static_path_support_not_runtime_effect",
        "outcome": "partial",
        "limitations": ["Python profile is an under-approximation; non-observation is not absence.",
                        "A static path establishes possible dependency, not demonstrated execution.",
                        "One shortest witness per caller; alternate paths are not enumerated."],
    }
    artifact["id"] = identity("context", artifact)
    return artifact


def compile_mentions(store, symbol, *, snapshot=None):
    """List document spans that mention a symbol key; mentions are never callers."""
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
                         "evidence": _evidence(store, assertion, snapshot),
                         "resolution": assertion["resolution"]})
    mentions.sort(key=lambda m: (m["evidence"]["path"], m["evidence"]["start"], m["evidence"]["end"]))
    return {"symbol": symbol, "snapshot": snapshot, "profile": view["profile"],
            "mentions": mentions, "outcome": "complete" if mentions else "empty"}


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
