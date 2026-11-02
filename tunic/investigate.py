"""Bounded investigation of frontier clusters with identical budgets.

Procedure per cluster, all capped by the budget:
  1. replay the open entries of at most max_sites deduped sites;
  2. for dispatch sites with exactly one uncovered candidate, commit it;
  3. for call sites with exactly one graph symbol matching the target
     head, commit caller -> callee through the governed path;
  4. duplicates close immediately, citing the asserted edge; fresh
     commits publish via one refresh, then close citing the new edge.

Yield = entries resolved with proof + newly asserted edges. Commits
land only in scratch copies; workspace claim files are snapshotted
and restored, so the source tree is untouched.
"""

import shutil
import tempfile
from pathlib import Path

from .memory import Memory, _dispatch_covering
from .triage import _cluster_key as cluster_of
from .triage import analyze, dedup_sites


def _asserted_count(memory):
    _snapshot, view = memory.store.view()
    return sum(1 for a in view.get("assertions", [])
               if a.get("status") == "asserted_in_source")


def _asserted_edge_id(memory, caller, callee):
    _snapshot, view = memory.store.view()
    for assertion in view.get("assertions", []):
        proposition = assertion.get("proposition", {})
        if (assertion.get("status") == "asserted_in_source"
                and proposition.get("caller") == caller
                and proposition.get("callee") == callee):
            return assertion.get("id")
    return None


def _caller_for(memory, site):
    """Find the calling symbol for a deduped site via live diagnostics."""
    _snapshot, view = memory.store.view()
    for diagnostic in view.get("diagnostics", []):
        if (diagnostic.get("path") == site["path"]
                and diagnostic.get("line") == site["line"]
                and (diagnostic.get("expression") == site["target"]
                     or diagnostic.get("table") == site["target"])):
            return diagnostic.get("caller")
    return None


def _single_symbol_match(memory, site):
    """Exactly one graph symbol matching the site target head."""
    target = site.get("target") or ""
    head = target.split(".")[0]
    if not head:
        return None
    _snapshot, view = memory.store.view()
    matches = [s.get("key") for s in view.get("symbols", [])
               if (s.get("key") or "").split("::")[-1].split(".")[-1] == head]
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_with_edge(memory, entry_id, edge_id, note):
    snapshot, _view = memory.store.view()
    try:
        memory.frontier_resolve(
            entry_id, {"snapshot": snapshot, "assertions": [edge_id],
                       "note": note})
    except ValueError as error:
        return "error", str(error)
    return "resolved", note


def investigate(memory, cluster_key, budget):
    """Investigate one cluster; return bounded action log and yield."""
    max_sites = budget.get("max_sites", 5)
    max_commits = budget.get("max_commits", 3)
    wanted = tuple(cluster_key)
    entries = sorted(
        (e for e in memory.frontier_list(status="open")),
        key=lambda e: e.get("id", ""))
    members = [e for e in entries if tuple(cluster_of(e)) == wanted]
    by_id = {e["id"]: e for e in members}
    actions, commits_used = [], 0
    resolved, edges_added = 0, 0

    def replay(entry_id):
        try:
            status, note = memory.frontier_replay(entry_id)
        except ValueError as error:
            return "error", str(error)
        return status, note

    sites = dedup_sites(members)[:max_sites]
    site_entry_ids = [i for s in sites for i in s["entry_ids"] if i in by_id]
    for entry_id in sorted(set(site_entry_ids)):
        status, note = replay(entry_id)
        actions.append({"action": "replay", "id": entry_id,
                        "status": status, "note": note})
        if status == "resolved":
            resolved += 1

    before = _asserted_count(memory)
    _snapshot, view = memory.store.view()
    as_of = memory.store.committed_on(_snapshot)
    added = []
    for site in sites:
        if commits_used >= max_commits:
            break
        failure = site.get("failure") or ""
        pairs = []
        if failure in ("static_edge_unresolved",
                       "ingest_dispatch_unresolved"):
            _snapshot2, view2 = memory.store.view()
            candidates, covering = _dispatch_covering(
                view2, site.get("target"), as_of)
            if not covering and len(candidates) == 1:
                proposition = candidates[0].get("proposition", {})
                pairs = [(proposition.get("caller"),
                          proposition.get("callee"))]
        elif failure == "ingest_unresolved_call":
            caller = _caller_for(memory, site)
            match = _single_symbol_match(memory, site)
            if caller and match and caller != match:
                pairs = [(caller, match)]
        for caller, callee in pairs:
            if commits_used >= max_commits:
                break
            try:
                committed, action, reason = memory.commit_proposal(
                    caller, callee)
            except ValueError as error:
                actions.append({"action": "commit",
                                "pair": [caller, callee],
                                "committed": False, "reason": str(error)})
                continue
            commits_used += 1
            actions.append({"action": "commit",
                            "pair": [caller, callee],
                            "committed": committed, "reason": reason})
            if action == "duplicate":
                # Relationship already verified: close citing it now.
                edge_id = _asserted_edge_id(memory, caller, callee)
                if edge_id is not None:
                    for entry_id in sorted(site["entry_ids"]):
                        if entry_id not in by_id:
                            continue
                        status, note = _resolve_with_edge(
                            memory, entry_id, edge_id,
                            "investigated: edge %s -> %s covers the site"
                            % (caller, callee))
                        actions.append({"action": "close", "id": entry_id,
                                        "status": status, "note": note})
                        if status == "resolved":
                            resolved += 1
            elif committed:
                added.append((caller, callee, site))
    if added:
        # Publish filed claims, then close each with proof citing the
        # newly asserted edge. One refresh per cluster, not per site.
        try:
            memory.ingest()
            actions.append({"action": "refresh", "status": "ok",
                            "note": "published %d claim(s)" % len(added)})
        except ValueError as error:
            actions.append({"action": "refresh", "status": "error",
                            "note": str(error)})
            added = []
        for caller, callee, site in added:
            edge_id = _asserted_edge_id(memory, caller, callee)
            if edge_id is None:
                continue
            for entry_id in sorted(site["entry_ids"]):
                if entry_id not in by_id:
                    continue
                status, note = _resolve_with_edge(
                    memory, entry_id, edge_id,
                    "investigated: edge %s -> %s covers the site"
                    % (caller, callee))
                actions.append({"action": "close", "id": entry_id,
                                "status": status, "note": note})
                if status == "resolved":
                    resolved += 1
    edges_added = _asserted_count(memory) - before
    return {"cluster": list(wanted), "actions": actions,
            "commits": commits_used,
            "resolved": resolved, "edges_added": edges_added,
            "yield": resolved + edges_added}


def _copy_workspace(root, dest):
    """Copy a source tree for one arm; dotfiles, caches, and stray
    databases stay behind. Symbol keys are root-relative, so clusters
    stay comparable across copies."""
    shutil.copytree(
        root, dest,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.sqlite", "*.sqlite-journal",
            "*.sqlite-wal", "*.sqlite-shm", ".pytest_cache"))


def compare_arms(db_path, root, top_n=3, budget=None):
    """Ranked top-N vs raw-occurrence top-N, identical budget each.

    Each arm gets its own scratch workspace COPY plus a freshly
    ingested database, so no arm ever writes the source tree (no
    mutate-and-restore: a crash cannot leave claim files behind).
    Ranked order comes from Ranking v1 (verifiability); baseline order
    comes from raw open-occurrence counts (the thing v1 rejects).
    Returns per-arm cluster keys, action logs, and yields.
    """
    budget = dict(budget or {"max_sites": 5, "max_commits": 3})
    report = analyze(db_path, root=root)
    ranked_keys = [tuple(c["key"]) for c in report["top"][:top_n]]
    holder = Memory(db_path, root)
    try:
        open_entries = [e for e in holder.frontier_list()
                        if e.get("status") not in ("resolved", "dismissed")]
    finally:
        holder.close()
    counts = {}
    for entry in open_entries:
        key = tuple(cluster_of(entry))
        # Raw diagnostic count: one vote per filed entry. Recurrence
        # stays in the ledger; the baseline must not reuse the
        # occurrence weighting the experiment rejects.
        counts[key] = counts.get(key, 0) + 1
    baseline_keys = [k for k, _ in
                     sorted(counts.items(), key=lambda kv: kv[1],
                            reverse=True)[:top_n]]

    arms = {}
    for name, keys in (("ranked", ranked_keys),
                       ("baseline", baseline_keys)):
        scratch_dir = Path(tempfile.mkdtemp())
        arm_ws = scratch_dir / "ws"
        _copy_workspace(root, arm_ws)
        memory = Memory(str(scratch_dir / "arm.sqlite"), str(arm_ws))
        try:
            memory.ingest()
            total = {"clusters": [list(k) for k in keys], "actions": 0,
                     "resolved": 0, "edges_added": 0, "yield": 0,
                     "detail": []}
            for key in keys:
                outcome = investigate(memory, key, budget)
                total["detail"].append(outcome)
                total["actions"] += len(outcome["actions"])
                total["resolved"] += outcome["resolved"]
                total["edges_added"] += outcome["edges_added"]
                total["yield"] += outcome["yield"]
            arms[name] = total
        finally:
            memory.close()
            shutil.rmtree(scratch_dir, True)
    arms["budget"] = budget
    return arms
