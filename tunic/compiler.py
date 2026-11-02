"""Deterministic reverse-call witnesses and mechanically validated context IR."""

from collections import Counter, defaultdict, deque
import re

from .baselines import _rank, _tokens
from .extract import TRAVERSABLE_CLAIMS, is_iso_date, split_doc_sections
from .identity import canonical, digest, identity
from .lifecycle import suppressed_ids

IR_VERSION = "tunic.w1.v3"
POLICIES = ("transparent", "evidence_only", "current")


def _subject_coverage(symbols, symbol):
    """Per-symbol extraction coverage for honest replies.

    Inventory-only subjects carry names and locations but no call
    edges; replies about them must report inventory_only instead of
    wearing the Python profile.
    """
    subject = symbols.get(symbol, {})
    if subject.get("derivation") == "heuristic_inventory_v1":
        return "inventory_only"
    return "bounded_static_analysis"


def _profile_note(symbols, symbol):
    """First limitations line, honest about inventory-only subjects."""
    subject = symbols.get(symbol, {})
    if subject.get("derivation") == "heuristic_inventory_v1":
        return ("heuristic_inventory_v1: name and location only for .%s; "
                "call extraction not available."
                % (subject.get("language") or "unknown"))
    return ("Python profile is an under-approximation; "
            "non-observation is not absence.")


def _char_window(data, start, end, radius=160):
    """Character-aligned evidence window around byte offsets [start, end).

    Byte-slicing a UTF-8 buffer can split a multibyte character; decoding
    the whole buffer first and converting offsets keeps the excerpt exact
    and the window well-formed on non-ASCII sources.
    """
    text = data.decode("utf-8")
    char_start = len(data[:start].decode("utf-8"))
    char_end = char_start + len(data[start:end].decode("utf-8"))
    return text[max(0, char_start - radius):min(len(text), char_end + radius)]


def _evidence(store, record, snapshot):
    source = store.source(record["source"], snapshot)
    if digest(source["data"]) != source["digest"] or identity("source", [source["path"], source["digest"]]) != source["id"]:
        raise ValueError("source integrity failure")
    start, end = record["start"], record["end"]
    if not 0 <= start < end <= len(source["data"]):
        raise ValueError("invalid evidence coordinates")
    return {"source": source["id"], "path": source["path"], "digest": source["digest"],
            "start": start, "end": end, "excerpt": source["data"][start:end].decode("utf-8"),
            "window": _char_window(source["data"], start, end)}


def compile_impact(store, symbol, *, snapshot=None, max_depth=8, max_nodes=100, policy="transparent",
                   view=None):
    if type(max_depth) is not int or max_depth < 1 or type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_depth and max_nodes must be positive integers")
    if view is None:
        snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown or unsupported symbol: " + symbol)
    if policy not in POLICIES:
        raise ValueError("unknown policy: " + str(policy))
    as_of = store.committed_on(snapshot)
    suppressed = suppressed_ids(store)
    blocked = set()
    retracted = {}
    if policy == "current":
        for assertion in view["assertions"]:
            proposition = assertion["proposition"]
            if assertion.get("status") != "asserted_in_source":
                continue
            if assertion.get("id") in suppressed:
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
        if assertion.get("id") in suppressed:
            continue
        if (proposition.get("relation") in TRAVERSABLE_CLAIMS
                and assertion.get("status") == "asserted_in_source"):
            key = (proposition.get("relation"), proposition.get("caller"),
                   proposition.get("callee"))
            supports[key] = supports.get(key, 0) + 1
    def _survives(assertion):
        proposition = assertion["proposition"]
        relation = proposition.get("relation")
        if assertion.get("id") in suppressed:
            return False
        if relation != "static_call" and not (
                relation in TRAVERSABLE_CLAIMS
                and assertion.get("status") == "asserted_in_source"):
            return False
        if policy == "evidence_only" and relation != "static_call":
            return False
        if policy == "current" and relation in TRAVERSABLE_CLAIMS:
            key = (relation, proposition.get("caller"), proposition.get("callee"))
            if supports.get(key, 0) - retracted.get(key, 0) < 1:
                return False
        if policy == "current" and (proposition.get("caller") in blocked
                                    or proposition.get("callee") in blocked):
            return False
        return True

    incoming = defaultdict(list)
    for assertion in view["assertions"]:
        if not _survives(assertion):
            continue
        caller_key = assertion["proposition"]["caller"]
        callee_key = assertion["proposition"]["callee"]
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
    # A witness path keeps one edge per caller, but every surviving
    # assertion behind a reached caller is evidence too (multi-support
    # after independent reviews, repeated static callsites, parallel
    # static and asserted edges). Without this, which support rows appear
    # in units depends on assertion row order — which is content-hash
    # order and shifts with byte offsets. Impacts keep the single witness
    # premises; units carry the full surviving evidence set.
    reached = set(paths)
    for assertion in view["assertions"]:
        if assertion["proposition"].get("caller") not in reached:
            continue
        if not _survives(assertion):
            continue
        used[assertion["id"]] = assertion
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
                              "scope": "local_single_principal", "coverage": _subject_coverage(symbols, symbol),
                              "diagnostics": view["diagnostics"], "bounds_hit": sorted(bounds), "cycles_observed": cycles},
        "target": {"entity": symbols[symbol], "evidence": _evidence(store, symbols[symbol], snapshot)},
        "units": units,
        "impacts": impacts,
        "validation_scope": "source_integrity_and_static_path_support_not_runtime_effect",
        "policy": policy,
        "outcome": "partial",
        "limitations": [_profile_note(symbols, symbol),
                        "A static path establishes possible dependency, not demonstrated execution.",
                        "One shortest witness per caller; alternate paths are not enumerated."],
    }
    artifact["id"] = identity("context", artifact)
    return artifact


def compile_callees(store, symbol, *, snapshot=None, max_depth=8, max_nodes=100,
                    policy="transparent", view=None):
    """Forward-call traversal: direct, bounded-reachable, and candidate callees.

    Built on the same governed assertion set as reverse impact: only
    surviving ``static_call`` edges traverse. Proposed ``depends_on``
    candidates are listed with their status but never traversed, and
    unresolved call sites surface as cold-frontier receipts instead of
    silent stops. ``direct`` is exactly the single-hop static calls
    (execution-attributed); ``reachable`` holds deeper or asserted-claim
    hops with depth and witness premises.
    """
    if type(max_depth) is not int or max_depth < 1 or type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_depth and max_nodes must be positive integers")
    if view is None:
        snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown or unsupported symbol: " + symbol)
    if policy not in POLICIES:
        raise ValueError("unknown policy: " + str(policy))
    as_of = store.committed_on(snapshot)
    suppressed = suppressed_ids(store)
    blocked = set()
    retracted = {}
    if policy == "current":
        for assertion in view["assertions"]:
            proposition = assertion["proposition"]
            if assertion.get("status") != "asserted_in_source":
                continue
            if assertion.get("id") in suppressed:
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
        if assertion.get("id") in suppressed:
            continue
        if (proposition.get("relation") in TRAVERSABLE_CLAIMS
                and assertion.get("status") == "asserted_in_source"):
            key = (proposition.get("relation"), proposition.get("caller"),
                   proposition.get("callee"))
            supports[key] = supports.get(key, 0) + 1
    def _survives(assertion):
        proposition = assertion["proposition"]
        relation = proposition.get("relation")
        if assertion.get("id") in suppressed:
            return False
        if relation != "static_call" and not (
                relation in TRAVERSABLE_CLAIMS
                and assertion.get("status") == "asserted_in_source"):
            return False
        if policy == "evidence_only" and relation != "static_call":
            return False
        if policy == "current" and relation in TRAVERSABLE_CLAIMS:
            key = (relation, proposition.get("caller"), proposition.get("callee"))
            if supports.get(key, 0) - retracted.get(key, 0) < 1:
                return False
        if policy == "current" and (proposition.get("caller") in blocked
                                    or proposition.get("callee") in blocked):
            return False
        return True

    outgoing = defaultdict(list)
    for assertion in view["assertions"]:
        if not _survives(assertion):
            continue
        outgoing[assertion["proposition"]["caller"]].append(assertion)
    for edges in outgoing.values():
        edges.sort(key=lambda a: (a["proposition"]["callee"], a["id"]))
    paths = {symbol: []}
    queue = deque([symbol])
    bounds = set()
    cycles = False
    while queue:
        caller = queue.popleft()
        for edge in outgoing[caller]:
            callee = edge["proposition"]["callee"]
            path = paths[caller] + [edge]
            nodes = [symbol] + [a["proposition"]["callee"] for a in path]
            if len(set(nodes)) != len(nodes):
                cycles = True
                continue
            if callee in paths:
                continue
            if len(path) > max_depth:
                bounds.add("max_depth")
                continue
            if len(paths) >= max_nodes:
                bounds.add("max_nodes")
                continue
            paths[callee] = path
            queue.append(callee)
    def _entry(key, path, depth):
        asserted = any(a["proposition"].get("relation") != "static_call"
                       for a in path)
        first = path[0]
        return {"callee": key, "depth": depth, "is_test": symbols[key]["is_test"],
                "status": first["status"],
                "derivation": {"rule": "asserted_dependency_v1" if asserted
                               else "forward_static_reachability_v1",
                               "premises": [a["id"] for a in path],
                               "verification": "asserted_not_observed" if asserted
                               else "syntactic_path_only"},
                "evidence": _evidence(store, first, snapshot),
                "resolution_support": [_evidence(store, premise, snapshot)
                                        for premise in first["resolution"]["support"]]}
    direct, reachable = [], []
    for key in sorted(paths):
        if key == symbol:
            continue
        path = paths[key]
        entry = _entry(key, path, len(path))
        if len(path) == 1 and path[0]["proposition"].get("relation") == "static_call":
            entry["kind"] = "direct_callee"
            direct.append(entry)
        else:
            entry["kind"] = "reachable_callee"
            reachable.append(entry)
    candidates = []
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") != "depends_on":
            continue
        if policy == "evidence_only":
            continue
        if proposition.get("caller") != symbol:
            continue
        if assertion.get("status") == "asserted_in_source":
            continue
        candidates.append({"relation": proposition["relation"],
                           "caller": proposition["caller"],
                           "callee": proposition.get("callee"),
                           "candidate": proposition.get("candidate"),
                           "note": proposition.get("note"),
                           "status": assertion["status"],
                           "evidence": _evidence(store, assertion, snapshot),
                           "resolution": assertion["resolution"]})
    candidates.sort(key=lambda r: (r["callee"] or "",
                                   r["evidence"]["path"],
                                   r["evidence"]["start"]))
    reached = set(paths)
    cold = []
    for diagnostic in view.get("diagnostics", []):
        if diagnostic.get("kind") != "unresolved_call":
            continue
        if diagnostic.get("caller") not in reached:
            continue
        expression = diagnostic.get("expression") or ""
        if "." in expression:
            needed = ("the module or class providing '%s'" % expression)
        else:
            needed = ("a same-file or importable definition of '%s'"
                      % expression)
        cold.append({"caller": diagnostic.get("caller"),
                     "path": diagnostic.get("path"),
                     "line": diagnostic.get("line"),
                     "expression": expression, "needed": needed})
    cold.sort(key=lambda r: (r["caller"] or "", r["line"] or 0,
                             r["expression"]))
    artifact = {
        "ir_version": IR_VERSION,
        "query": {"symbol": symbol, "max_depth": max_depth, "max_nodes": max_nodes, "policy": policy},
        "selection_receipt": {"snapshot": snapshot, "profile": view["profile"],
                              "policy": "forward-static-call-bfs-v1", "reuse": "none_full_recompute",
                              "scope": "local_single_principal", "coverage": _subject_coverage(symbols, symbol),
                              "diagnostics": view["diagnostics"], "bounds_hit": sorted(bounds), "cycles_observed": cycles},
        "target": {"entity": symbols[symbol], "evidence": _evidence(store, symbols[symbol], snapshot)},
        "direct": direct,
        "reachable": reachable,
        "candidates": candidates,
        "cold": cold,
        "validation_scope": "source_integrity_and_static_path_support_not_runtime_effect",
        "policy": policy,
        "coverage": _subject_coverage(symbols, symbol), "outcome": "unsupported_capability" if _subject_coverage(symbols, symbol) == "inventory_only" else ("complete" if not bounds and not cold else "partial"),
        "limitations": [_profile_note(symbols, symbol),
                        "A static path establishes possible dependency, not demonstrated execution.",
                        "One shortest witness per callee; alternate paths are not enumerated.",
                        "Candidates are proposed, not asserted; cold entries mark where knowledge ends."],
    }
    artifact["id"] = identity("context", artifact)
    return artifact


def compile_mentions(store, symbol, *, snapshot=None, view=None):
    """List document spans that mention a symbol key; proposals never traverse.

    A proposal counts as corroborated when the same document carries exact
    evidence for the same entity: an exact mention or an asserted claim.
    """
    if view is None:
        snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown symbol: " + symbol)
    mentions = []
    suppressed = suppressed_ids(store)
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if assertion.get("id") in suppressed:
            continue
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
        if assertion.get("id") in suppressed:
            continue
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


def compile_candidates(store, *, snapshot=None):
    """Proposed-but-unverified relationship candidates.

    Dispatch analysis and other mechanical passes surface candidates here
    with derivation chains. Status is always proposed/proposed_ambiguous:
    candidates never traverse, corroborate, or render as evidence until
    verified and promoted through the governed path. Model and reviewer
    tooling reads this to decide what is worth verifying.
    """
    snapshot, view = store.view(snapshot)
    found = []
    for assertion in view["assertions"]:
        if assertion.get("status") not in ("proposed", "proposed_ambiguous"):
            continue
        proposition = assertion.get("proposition", {})
        resolution = assertion.get("resolution") or {}
        found.append({"id": assertion.get("id"),
                      "relation": proposition.get("relation"),
                      "caller": proposition.get("caller"),
                      "callee": proposition.get("callee"),
                      "via": proposition.get("via"),
                      "status": assertion.get("status"),
                      "rule": resolution.get("rule"),
                      "derivation": assertion.get("derivation"),
                      "resolution": resolution})
    found.sort(key=lambda c: (c["relation"] or "", c["caller"] or "",
                              c["callee"] or "", c["id"] or ""))
    return {"snapshot": snapshot, "profile": view["profile"],
            "candidates": found,
            "outcome": "complete" if found else "empty"}


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


def compile_documents(store, *, snapshot=None, view=None, task="", hits=(),
                      frequency=None, top_k=3, exact=(),
                      excerpt_chars=1200, per_doc_sections=2):
    """Bounded cited prose passages for one task; read-only.

    Documents participate in retrieval as OBSERVED prose with
    byte-exact citations, never as synthetic code symbols or asserted
    edges. Whole-document TextNotes hits are split into heading
    sections (split_doc_sections) and re-ranked with the shared BM25
    scorer against the discovery corpus frequencies, so passages stay
    bounded and citable. Only documents published in the snapshot can
    appear: the mentioner key must resolve to a live ::document symbol
    and its source must be a snapshot member, so excluded, vendored,
    or out-of-scope documents cannot leak through. Doc keys named
    exactly in the task (exact) rank first. Returns passages sorted by
    (-score, unit) with {doc, section, span, digest, snapshot, score,
    excerpt, unit}; retrieval adds no assertions.
    """
    if view is None:
        snapshot, view = store.view(snapshot)
    if type(top_k) is not int or top_k < 0:
        raise ValueError("top_k must be a non-negative integer")
    symbols = {s["key"]: s for s in view["symbols"]}
    terms = set(_tokens(task)) if isinstance(task, str) and task.strip() else set()
    ordered, seen = [], set()
    for key in exact or ():
        if key in symbols and key not in seen:
            seen.add(key)
            ordered.append(key)
    for hit in hits or ():
        mentioner = (hit.get("symbol") or "") + "::document"
        if mentioner in symbols and mentioner not in seen:
            seen.add(mentioner)
            ordered.append(mentioner)
    frequency = frequency if frequency is not None else Counter()
    sections, meta = [], []
    for mentioner in ordered:
        record = symbols[mentioner]
        try:
            source = store.source(record["source"], snapshot)
        except ValueError:
            continue
        data = source["data"]
        try:
            data.decode("utf-8")  # guard: skip undecodable docs
        except UnicodeError:
            continue
        for slug, start, end in split_doc_sections(source["path"], data):
            body = data[start:end].decode("utf-8")
            unit = mentioner if slug is None else mentioner.replace(
                "::document", "::" + slug, 1)
            content = source["path"] + " " + (slug or "") + " " + body
            sections.append({"symbol": unit, "is_test": False,
                             "tokens": Counter(_tokens(content))})
            meta.append({"doc": source["path"], "section": slug,
                         "span": {"start": start, "end": end},
                         "digest": source["digest"], "snapshot": snapshot,
                         "excerpt": body[:excerpt_chars], "unit": unit})
    if not sections or not terms:
        return []
    lengths = [sum(d["tokens"].values()) for d in sections]
    ranked = _rank(sections, lengths,
                   sum(lengths) / len(lengths) if lengths else 1,
                   frequency, terms, None, len(sections))
    by_unit = {m["unit"]: m for m in meta}
    per_doc, passages = {}, []
    for row in ranked:
        passage = by_unit.get(row["symbol"])
        if passage is None:
            continue
        score = row["score"]
        taken = per_doc.get(passage["doc"], 0)
        if taken >= per_doc_sections:
            continue
        per_doc[passage["doc"]] = taken + 1
        passages.append({**passage, "score": round(score, 4)})
        if len(passages) >= top_k:
            break
    passages.sort(key=lambda p: (-p["score"], p["unit"]))
    return passages[:top_k]


def compile_related(store, symbol, *, snapshot=None, policy="transparent", view=None):
    """Cross-source context: reverse dependencies plus document mentions, claims, and proposals."""
    if view is None:
        snapshot, view = store.view(snapshot)
    if symbol not in {s["key"] for s in view["symbols"]}:
        raise ValueError("unknown symbol: " + symbol)
    return {"symbol": symbol, "snapshot": snapshot, "profile": view["profile"],
            "impact": compile_impact(store, symbol, snapshot=snapshot, policy=policy,
                                     view=view),
            "coverage": _subject_coverage({s["key"]: s for s in view["symbols"]}, symbol), "mentions": compile_mentions(store, symbol, snapshot=snapshot, view=view),
            "claims": compile_claims(store, symbol, snapshot=snapshot, view=view),
            "outcome": "complete"}


def compile_claims(store, symbol, *, snapshot=None, view=None):
    """List typed document claims and proposals touching a symbol; never traversed here."""
    if view is None:
        snapshot, view = store.view(snapshot)
    symbols = {s["key"]: s for s in view["symbols"]}
    if symbol not in symbols:
        raise ValueError("unknown symbol: " + symbol)
    supports, retractions = {}, {}
    suppressed = suppressed_ids(store)
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if assertion.get("id") in suppressed:
            continue
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
        if assertion.get("id") in suppressed:
            continue
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
        if assertion.get("id") in suppressed_ids(store):
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
