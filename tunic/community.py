"""Deterministic community detection plus structured summaries.

Label propagation (Raghavan, Albert, Kumara 2007): near-linear,
no objective to optimize, only local information. Tunic's variant is
deterministic -- sorted processing order and smallest-label tie-break
instead of random order -- so a cluster is reproducible by hand;
iteration is capped. Communities form over static_call plus asserted
traversable edges, undirected.

Summaries are structured records (member lists, edge counts, entry
points), never prose: an agent reads exact keys, not a paragraph.
Additive like consolidated_supports, excluded from the answer path,
traversal, and selection corroboration. A summary records its member
set and rule version; re-running on the same view yields identical
rows. This is the local/community counterpart to GraphRAG community
summaries, minus the LLM and the map-reduce.
"""

COMMUNITY_RULE = "community_detection_v1"
COMMUNITY_SUMMARY_RULE = "community_summary_v1"
COMMUNITY_MAX_ITER = 20


def _adjacency(view):
    from .extract import TRAVERSABLE_CLAIMS
    adjacent = {}
    for assertion in view.get("assertions", []):
        proposition = assertion.get("proposition", {})
        relation = proposition.get("relation")
        status = assertion.get("status")
        if relation == "static_call":
            if status != "syntactic_observation":
                continue
        elif relation in TRAVERSABLE_CLAIMS:
            if status != "asserted_in_source":
                continue
        else:
            continue
        caller = proposition.get("caller")
        callee = proposition.get("callee")
        if caller and callee and caller != callee:
            adjacent.setdefault(caller, set()).add(callee)
            adjacent.setdefault(callee, set()).add(caller)
    return adjacent


def _weights(view):
    """Parallel-edge counts per unordered node pair (1 minimum)."""
    from .extract import TRAVERSABLE_CLAIMS
    weights = {}
    for assertion in view.get("assertions", []):
        proposition = assertion.get("proposition", {})
        relation = proposition.get("relation")
        status = assertion.get("status")
        if relation == "static_call":
            if status != "syntactic_observation":
                continue
        elif relation in TRAVERSABLE_CLAIMS:
            if status != "asserted_in_source":
                continue
        else:
            continue
        caller = proposition.get("caller")
        callee = proposition.get("callee")
        if caller and callee and caller != callee:
            weights.setdefault(caller, {}).setdefault(callee, 0)
            weights[caller][callee] += 1
            weights.setdefault(callee, {}).setdefault(caller, 0)
            weights[callee][caller] += 1
    return weights


def _directed(view):
    from .extract import TRAVERSABLE_CLAIMS
    edges = []
    for assertion in view.get("assertions", []):
        proposition = assertion.get("proposition", {})
        relation = proposition.get("relation")
        status = assertion.get("status")
        if relation == "static_call":
            if status != "syntactic_observation":
                continue
        elif relation in TRAVERSABLE_CLAIMS:
            if status != "asserted_in_source":
                continue
        else:
            continue
        caller = proposition.get("caller")
        callee = proposition.get("callee")
        if caller and callee:
            edges.append((caller, callee))
    return edges


def detect(view, max_iter=COMMUNITY_MAX_ITER):
    """Map community id to sorted member keys (size >= 2).

    Deterministic label propagation: every node starts with its own
    key as its label; each pass assigns the highest-weight neighbor
    label (parallel edges add weight, like graphiti's edge-count
    weighting; ties to the smallest), processing nodes in sorted
    order; stops when a pass changes nothing or max_iter passes
    elapse.
    """
    adjacent = _adjacency(view)
    weights = _weights(view)
    nodes = sorted(adjacent)
    if not nodes:
        return {}
    labels = {node: node for node in nodes}
    for _ in range(max(0, max_iter)):
        changed = False
        for node in nodes:
            counts = {}
            for peer in adjacent.get(node, ()):
                label = labels[peer]
                counts[label] = counts.get(label, 0) \
                    + weights.get(node, {}).get(peer, 1)
            if not counts:
                continue
            top = max(counts.values())
            winner = min(label for label, count in counts.items()
                         if count == top)
            if winner != labels[node]:
                labels[node] = winner
                changed = True
        if not changed:
            break
    clusters = {}
    for node in nodes:
        clusters.setdefault(labels[node], []).append(node)
    return {"community-%d" % (index + 1): sorted(members)
            for index, members in
            enumerate(sorted(cluster for cluster in clusters.values()
                             if len(cluster) >= 2))}


def summarize(view, clusters=None):
    """Structured summary records for detected communities.

    Each record carries the member keys, member and internal-edge
    counts, covering paths, and entry points (members no in-cluster
    edge calls into). Read-only; summaries never enter the answer
    path. Sorted by community id.
    """
    from .identity import identity
    adjacent = _adjacency(view)
    if clusters is None:
        clusters = detect(view)
    directed = _directed(view)
    records = []
    for community_id in sorted(clusters):
        members = sorted(clusters[community_id])
        member_set = set(members)
        internal = sum(1 for member in members
                       for peer in adjacent.get(member, ())
                       if peer in member_set and peer >= member)
        called_inside = set()
        for caller, callee in directed:
            if caller in member_set and callee in member_set \
                    and caller != callee:
                called_inside.add(callee)
        entry_points = sorted(member_set - called_inside)
        record = {
            "id": community_id,
            "members": members,
            "member_count": len(members),
            "internal_edges": internal,
            "paths": sorted({key.split("::", 1)[0] for key in members}),
            "entry_points": entry_points,
            "rule": COMMUNITY_SUMMARY_RULE,
        }
        record["digest"] = identity("community", record)
        records.append(record)
    return records
