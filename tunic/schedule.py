"""Frontier investigation scheduler: bounded, resumable, governed work.

The scheduler turns triage rankings into executed investigations without
inventing a second priority engine, priority rule, or commit path:

- selection reuses Ranking v1 (`tunic.triage.analyze`) verbatim;
- investigation reuses `tunic.investigate.investigate` on isolated
  scratch workspaces (the source tree is never written);
- promotion reuses `Memory.commit_proposal` (duplicate-safe NOOP on
  replays) plus `frontier_resolve` with proof-backed closure.

Job records persist in the home database (`schedule_jobs` table) with
cluster identity, snapshots, state transitions, attempt history, and
outcome receipts. Blocked clusters produce capability-work receipts and
leave entries open: no fabricated evidence, ever. Interruption resumes
without duplicate promotions or repeated closures; a moved home
snapshot marks the job stale instead of applying stale evidence.
Concurrency is sequential in v1: `concurrency_limit` must be 1, so a
future worker pool lifts an explicit, enforced gate.
"""

import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .investigate import _asserted_edge_id, _copy_workspace, investigate
from .memory import Memory
from .triage import _cluster_key as cluster_of
from .triage import analyze

SCHEDULER_VERSION = 1
JOB_STATES = ("queued", "running", "done", "failed", "blocked", "stale")
WORK_QUEUES = ("closable_now", "investigable")
SCHEDULE_JOBS_DDL = (
    "CREATE TABLE IF NOT EXISTS schedule_jobs("
    "id TEXT PRIMARY KEY, record TEXT NOT NULL);"
    "CREATE INDEX IF NOT EXISTS schedule_jobs_state ON "
    "schedule_jobs(json_extract(record, '$.state'));")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _job_id(cluster_key, snapshot):
    return "sched-" + _digest(json.dumps(
        [list(cluster_key), snapshot], sort_keys=True))[:12]


def _ensure_table(memory):
    memory.store.connection.executescript(SCHEDULE_JOBS_DDL)


def _load_jobs(memory):
    _ensure_table(memory)
    rows = memory.store.connection.execute(
        "SELECT id, record FROM schedule_jobs ORDER BY id").fetchall()
    return {job_id: json.loads(record) for job_id, record in rows}


def _save_job(memory, job):
    _ensure_table(memory)
    job["updated_at"] = _now()
    memory.store.connection.execute(
        "INSERT OR REPLACE INTO schedule_jobs(id, record) VALUES(?, ?)",
        (job["id"], json.dumps(job, sort_keys=True)))
    memory.store.connection.commit()


def list_jobs(db_path):
    """Read-only job inventory for operators and future schedulers."""
    memory = Memory(db_path, ".")
    try:
        return sorted(_load_jobs(memory).values(),
                      key=lambda job: job.get("created_at", ""))
    finally:
        memory.close()


def _home_snapshot(memory):
    snapshot, _view = memory.store.view()
    return snapshot


def _cluster_entries(memory, cluster_key):
    wanted = tuple(cluster_key)
    return [e for e in memory.frontier_list(status="open")
            if tuple(cluster_of(e)) == wanted]


def _plan(db_path, root, report, top_n, budget):
    """Create queued jobs for the top-N ranked active clusters."""
    memory = Memory(db_path, root)
    try:
        jobs = _load_jobs(memory)
        snapshot = _home_snapshot(memory)
        planned = []
        for item in report["top"][:max(top_n, 0)]:
            key = tuple(item["key"])
            job_id = _job_id(key, snapshot)
            if job_id in jobs:
                planned.append(jobs[job_id])
                continue
            job = {"id": job_id, "scheduler": SCHEDULER_VERSION,
                   "cluster": list(key), "queue": item.get("queue"),
                   "score": item.get("score"),
                   "snapshot": snapshot, "state": "queued",
                   "budget": dict(budget), "attempts": [], "outcome": None,
                   "created_at": _now(), "updated_at": _now()}
            _save_job(memory, job)
            planned.append(job)
        return planned
    finally:
        memory.close()


def _execute_job(db_path, root, job, budget):
    """Run one job: scratch investigation, then governed home application."""
    started = time.monotonic()
    attempt = {"started_at": _now(), "actions": [], "outcome": None}
    memory = Memory(db_path, root)
    try:
        _ensure_table(memory)
        if _home_snapshot(memory) != job["snapshot"]:
            job["state"] = "stale"
            job["outcome"] = {
                "reason": "home snapshot moved since planning; "
                          "reschedule instead of applying stale evidence"}
            _save_job(memory, job)
            return job
        live = _cluster_entries(memory, job["cluster"])
        if not live:
            job["state"] = "done"
            job["outcome"] = {"reason": "no open entries at execution; "
                                        "nothing to investigate",
                               "resolved": 0, "edges_added": 0, "yield": 0}
            _save_job(memory, job)
            return job
        job["state"] = "running"
        _save_job(memory, job)
    finally:
        memory.close()

    if job.get("queue") not in WORK_QUEUES:
        return _finish_blocked(db_path, root, job, attempt)

    scratch_dir = Path(tempfile.mkdtemp(prefix="tunic-sched-"))
    try:
        arm_ws = scratch_dir / "ws"
        _copy_workspace(root, arm_ws)
        work = Memory(str(scratch_dir / "sched.sqlite"), str(arm_ws))
        try:
            work.ingest()
            outcome = investigate(work, tuple(job["cluster"]), budget)
        finally:
            work.close()
        attempt["actions"] = outcome.get("actions", [])
        pairs = sorted({tuple(p[:2]) for a in outcome.get("actions", [])
                        if a.get("action") == "commit" and a.get("committed")
                        for p in [a.get("pair", [])] if len(p) >= 2})
        attempt["outcome"] = {"resolved": outcome.get("resolved", 0),
                              "edges_added": outcome.get("edges_added", 0),
                              "yield": outcome.get("yield", 0),
                              "pairs": [list(p) for p in pairs]}
        return _apply_home(db_path, root, job, attempt, pairs,
                           time.monotonic() - started)
    finally:
        shutil.rmtree(scratch_dir, True)


def _finish_blocked(db_path, root, job, attempt):
    """Blocked clusters become capability requests; entries stay open."""
    memory = Memory(db_path, root)
    try:
        live = _cluster_entries(memory, job["cluster"])
        targets = sorted({e.get("target", "") for e in live
                          if e.get("target")})
        capability = ("external or unsupported receiver; no graph action "
                      "available (targets: %s)" % ", ".join(targets[:5]))
        job["state"] = "blocked"
        job["outcome"] = {"reason": "cluster queue is not investigable",
                          "capability_request": capability,
                          "open_entries": len(live),
                          "resolved": 0, "edges_added": 0, "yield": 0}
        job["attempts"].append(attempt)
        _save_job(memory, job)
        return job
    finally:
        memory.close()


def _apply_home(db_path, root, job, attempt, pairs, elapsed_s):
    """Apply scratch yield to home through the governed path only."""
    memory = Memory(db_path, root)
    try:
        if _home_snapshot(memory) != job["snapshot"]:
            job["state"] = "stale"
            job["outcome"] = {
                "reason": "home snapshot moved during investigation; "
                          "scratch yield withheld, reschedule",
                "scratch": attempt["outcome"]}
            job["attempts"].append(attempt)
            _save_job(memory, job)
            return job
        applied, entry_receipts = [], []
        doc = "notes/schedule-%s.md" % job["id"]
        for caller, callee in pairs:
            try:
                committed, action, reason = memory.commit_proposal(
                    caller, callee, document=doc, authority="scheduler",
                    note="frontier job %s" % job["id"])
            except ValueError as error:
                attempt["actions"].append(
                    {"action": "home-commit", "pair": [caller, callee],
                     "committed": False, "reason": str(error)})
                continue
            applied.append({"pair": [caller, callee], "committed": committed,
                            "action": action, "reason": reason})
        if any(a["committed"] for a in applied):
            memory.ingest()
        snapshot = _home_snapshot(memory)
        for entry in _cluster_entries(memory, job["cluster"]):
            entry_id = entry["id"]
            current = [e for e in memory.frontier_list()
                       if e["id"] == entry_id]
            if current and current[0].get("status") in ("resolved",
                                                        "dismissed"):
                entry_receipts.append(
                    {"id": entry_id, "status": "already-terminal",
                     "note": "entry is %s; left untouched"
                     % current[0].get("status")})
                continue
            edge_id = None
            for item in applied:
                edge_id = _asserted_edge_id(
                    memory, item["pair"][0], item["pair"][1])
                if edge_id is not None:
                    break
            if edge_id is None:
                entry_receipts.append({"id": entry_id, "status": "open",
                                       "note": "no asserted edge to cite"})
                continue
            try:
                memory.frontier_resolve(
                    entry_id, {"snapshot": snapshot,
                               "assertions": [edge_id],
                               "note": "scheduled job %s" % job["id"]},
                    resolved_by="scheduler")
            except ValueError as error:
                status, note = "error", str(error)
            else:
                status, note = "resolved", "scheduled job %s" % job["id"]
            entry_receipts.append({"id": entry_id, "status": status,
                                   "note": note})
        resolved = sum(1 for r in entry_receipts if r["status"] == "resolved")
        job["state"] = "done"
        job["home_snapshot"] = _home_snapshot(memory)
        job["outcome"] = {"scratch": attempt["outcome"], "applied": applied,
                          "entries": entry_receipts, "resolved": resolved,
                          "edges_added": attempt["outcome"]["edges_added"],
                          "yield": resolved + attempt["outcome"]["edges_added"],
                          "elapsed_s": round(elapsed_s, 3)}
        job["attempts"].append(attempt)
        _save_job(memory, job)
        return job
    finally:
        memory.close()


def run_schedule(db_path, root=".", top_n=3, budget=None,
                 max_clusters=None, deadline_s=None, concurrency_limit=1,
                 resume=True, hooks=None):
    """Schedule, execute, and govern frontier investigations.

    Deterministic and local: ranking comes from triage, work from
    investigate(), promotion from commit_proposal + frontier_resolve.
    Returns a JSON-serializable report. Raises on concurrency_limit != 1
    (sequential v1 gate) and invalid budgets.
    """
    hooks = hooks or {}
    if concurrency_limit != 1:
        raise ValueError("scheduler v1 runs sequentially: "
                         "concurrency_limit must be 1")
    budget = dict(budget or {"max_sites": 5, "max_commits": 3})
    for field in ("max_sites", "max_commits"):
        value = budget.get(field, 3)
        if type(value) is not int or value < 0:
            raise ValueError("budget %s must be a non-negative int" % field)
    if deadline_s is not None and not (isinstance(deadline_s, (int, float))
                                       and deadline_s >= 0):
        raise ValueError("deadline_s must be a non-negative number")
    if type(top_n) is not int or top_n < 0:
        raise ValueError("top_n must be a non-negative int")
    if max_clusters is not None and (type(max_clusters) is not int
                                      or max_clusters < 0):
        raise ValueError("max_clusters must be a non-negative int")

    report = analyze(db_path, root=root, top=max(top_n, 1))
    limit = top_n if max_clusters is None else min(top_n, max_clusters)
    planned = _plan(db_path, root, report, limit, budget)
    memory = Memory(db_path, root)
    try:
        stored_all = _load_jobs(memory)
    finally:
        memory.close()
    planned_ids = {job["id"] for job in planned}
    carryover = [job for job_id, job in sorted(stored_all.items())
                 if job_id not in planned_ids
                 and job.get("state") in ("queued", "running")]
    planned = carryover + planned
    started_all = time.monotonic()
    ran, outcomes = [], {"done": 0, "failed": 0, "blocked": 0,
                         "stale": 0, "queued": 0}
    resolved_total, edges_total = 0, 0
    applied_snapshot = None
    for job in planned:
        if deadline_s is not None and \
                time.monotonic() - started_all >= deadline_s:
            break
        memory = Memory(db_path, root)
        try:
            stored = _load_jobs(memory).get(job["id"], job)
            current_snap = _home_snapshot(memory)
            if stored["state"] == "done" and resume \
                    and stored.get("home_snapshot") == current_snap:
                applied_snapshot = current_snap
        finally:
            memory.close()
        if stored["state"] == "done" and resume:
            ran.append(stored)
            outcomes["done"] += 1
            resolved_total += stored.get("outcome", {}).get("resolved", 0)
            edges_total += stored.get("outcome", {}).get("edges_added", 0)
            continue
        if stored["state"] == "stale" and resume:
            ran.append(stored)
            outcomes["stale"] += 1
            continue
        memory = Memory(db_path, root)
        try:
            current_snap = _home_snapshot(memory)
            if current_snap != stored.get("snapshot"):
                if applied_snapshot is not None \
                        and current_snap == applied_snapshot:
                    stored["rebased_from"] = stored["snapshot"]
                    stored["snapshot"] = current_snap
                    _save_job(memory, stored)
                else:
                    stored["state"] = "stale"
                    stored["outcome"] = {
                        "reason": "home snapshot moved since planning; "
                                  "reschedule instead of applying stale "
                                  "evidence"}
                    _save_job(memory, stored)
                    ran.append(stored)
                    outcomes["stale"] += 1
                    continue
        finally:
            memory.close()
        if callable(hooks.get("before_job")):
            hooks["before_job"](stored)
        try:
            finished = _execute_job(db_path, root, stored, budget)
        except Exception as error:
            memory = Memory(db_path, root)
            try:
                stored["state"] = "failed"
                stored["outcome"] = {"error": "%s: %s"
                                     % (type(error).__name__, error)}
                stored["attempts"].append(
                    {"started_at": _now(), "actions": [],
                     "outcome": None})
                _save_job(memory, stored)
                finished = stored
            finally:
                memory.close()
        if callable(hooks.get("after_job")):
            hooks["after_job"](finished)
        if finished.get("home_snapshot"):
            applied_snapshot = finished["home_snapshot"]
        ran.append(finished)
        outcomes[finished["state"]] = outcomes.get(finished["state"], 0) + 1
        resolved_total += finished.get("outcome", {}).get("resolved", 0)
        edges_total += finished.get("outcome", {}).get("edges_added", 0)
    queued_left = len(planned) - len(ran)
    outcomes["queued"] += queued_left
    return {"scheduler": SCHEDULER_VERSION, "snapshot": report["snapshot"],
            "budget": budget, "top_n": top_n,
            "max_clusters": max_clusters, "deadline_s": deadline_s,
            "clusters": [job["cluster"] for job in planned],
            "jobs": ran, "queued_left": queued_left,
            "totals": dict(outcomes, resolved=resolved_total,
                           edges_added=edges_total)}
