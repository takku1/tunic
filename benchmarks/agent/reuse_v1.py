"""Cross-Session Verified Memory Reuse v1 (model-free, deterministic).

Session A performs bounded governed acquisitions on a scratch copy of
the ``reuse/`` fixture: a reviewer audit note (supersedes / depends_on /
contradicts claims) plus the trusted frontier scheduler closing the
investigable dispatch cluster with proof. Session B runs paired arms on
pristine copies of the same code snapshot:

- ``cold``: fresh ingest (static knowledge only);
- ``warm``: a copy of the Session-A database (static + acquired memory).

Both arms run the SAME fixed scripted agent policy with identical
budgets, retrieval settings, and scoring. The agent answers static
questions from traversed impacts and judgment questions (routing,
replacement, dispute) from asserted memory claims; proposed-only
evidence never counts as an answer. No model calls, no frozen heldout
contact. See docs/evaluation/strategy.md for the acceptance gates.
"""

import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.arena.runner_v2 import TaskManifest, run_cell  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.schedule import run_schedule  # noqa: E402

REUSE_VERSION = 1
FIXTURE = HERE / "reuse"

SESSION_A_NOTE = """# Session-A review audit (2026-11-02)

```tunic-claims
[{"rel": "supersedes", "subject": "svc.py::handle_new", "object": "svc.py::handle_old"},
 {"rel": "depends_on", "subject": "svc.py::dispatch", "object": "svc.py::do_legacy"},
 {"rel": "contradicts", "subject": "svc.py::do_new", "object": "svc.py::do_legacy"}]
```

Audit findings:

- `handle_old` is retired. New code must call `handle_new`. `route`
  was migrated; `route_b` still calls the retired handler.
- `dispatch` routes `kind="legacy"` to `do_legacy` (verified by audit
  of the dispatch table; the key is dynamic so static analysis cannot
  see this edge.
- `do_new` was proposed as the canonical legacy handler, disputing
  `do_legacy` (scope dispute: legacy traffic vs new code).
"""

# (task_id, kind, text, key, role) — role documents why the task exists:
# win = only answerable with Session-A memory; guard = must pass both arms.
REUSE_TASKS = [
    ("reuse-route", "callers",
     "Which function does dispatch route legacy requests to?",
     {"sites": ["svc.py::do_legacy"]}, "win"),
    ("reuse-correction", "callers",
     "What should replace handle_old?",
     {"sites": ["svc.py::handle_new"]}, "win"),
    ("reuse-dispute", "uniqueness",
     "Which legacy handler is canonical: do_legacy or do_new?",
     {"definitions": ["svc.py::do_legacy", "svc.py::do_new"],
      "unique": False}, "win"),
    ("reuse-traverse", "callers",
     "Which functions break if do_legacy changes?",
     {"sites": ["svc.py::dispatch", "svc.py::main"]}, "win"),
    ("reuse-static", "callers",
     "Which functions break if handle_new changes?",
     {"sites": ["svc.py::route"]}, "guard"),
    ("reuse-caller", "callers",
     "Which functions break if dispatch changes?",
     {"sites": ["svc.py::main"]}, "guard"),
    ("reuse-decoy-stale", "decoy",
     "Does route call handle_old?",
     {"verdict": "no-call"}, "guard"),
    ("reuse-decoy-irrelevant", "decoy",
     "Does do_new call dispatch?",
     {"verdict": "no-call"}, "guard"),
    ("reuse-missing", "abstention",
     "zz.py::Missing",
     {"expected": "abstain"}, "guard"),
]

VERDICT_Q = re.compile(r"^(does|is|are|can|will|was|has|should)\b.*\?$",
                       re.IGNORECASE | re.DOTALL)
SUPERSEDE_Q = re.compile(r"replac|successor|instead|supersede",
                         re.IGNORECASE)
ROUTING_Q = re.compile(r"\b(rout\w*|traffic|dispatch\w*)\b", re.IGNORECASE)
SYMBOL_RE = re.compile(r"[A-Za-z_][\w]*\.py::[A-Za-z_][\w.]*")
WORD_RE = re.compile(r"[A-Za-z_][\w]*")
FALLBACK_STOP = {"which", "what", "does", "function", "functions", "the",
                 "and", "for", "are", "is", "canonical", "should", "call",
                 "calls", "current", "change", "changes", "break", "if",
                 "legacy", "requests", "replace", "route", "dispatch",
                 "handler", "handlers"}


def _basename(symbol):
    return symbol.split("::", 1)[1] if "::" in symbol else symbol


def _asserted(claims):
    return [c for c in (claims or [])
            if c.get("status") == "asserted_in_source"]


def _fallback(task_text, ctx):
    """Bounded repo search when memory yields nothing (2 searches + 1 read)."""
    tokens = [w.lower() for w in WORD_RE.findall(task_text)
              if len(w) >= 4 and w.lower() not in FALLBACK_STOP][:2]
    for token in tokens:
        try:
            hits = ctx.action("search", pattern=token)
        except Exception:
            continue
        if not hits:
            continue
        path = hits[0].get("path")
        if not path:
            continue
        try:
            content = ctx.action("read_file", path=path)
        except Exception:
            continue
        names = []
        for name in re.findall(r"^def ([A-Za-z_]\w*)", content,
                               re.MULTILINE):
            if name.lower() in tokens or token in name.lower():
                symbol = "%s::%s" % (path.rsplit(".", 1)[0] + ".py"
                                     if "/" not in path else path, name)
                names.append(symbol)
        if names:
            return "\n".join(sorted(set(names)))
    return "ABSTAIN\nREASON: no memory or source evidence"


def reuse_agent(task_text, workspace, budget, ctx):
    """Fixed scripted agent: identical policy in both arms.

    Retrieval first; judgment questions answered from asserted memory
    claims (routing/replacement/dispute); static questions from
    traversed impacts. Proposed-only evidence never answers. Unknown
    task-named symbols abstain. Empty memory results fall back to a
    bounded repo search so repeated-work cost is observable.
    """
    res = ctx.action("retrieve", task=task_text, limit=5,
                     policy="current", select=True)
    cands = {c["symbol"] for c in res.get("candidates", [])}
    impacts = {i["symbol"] for i in res.get("impacts", [])}
    evidence = cands | impacts
    claims = _asserted(res.get("claims", []))
    tokens = {w.lower() for w in WORD_RE.findall(task_text)}
    named = set(SYMBOL_RE.findall(task_text))
    unknown = set(res.get("unknown_symbols") or [])
    text = " ".join(task_text.split())

    if named and named <= unknown:
        return "ABSTAIN\nREASON: unknown symbol"

    if VERDICT_Q.match(text):
        mentioned = {c for c in evidence
                     if _basename(c).lower() in tokens}
        for claim in claims:
            if {claim.get("caller"), claim.get("callee")} <= mentioned \
                    and len({claim.get("caller"),
                             claim.get("callee")}) == 2:
                return ("YES\nEVIDENCE: %s %s %s"
                        % (claim["caller"], claim["relation"],
                           claim["callee"]))
        return "NO\nREASON: no asserted relationship found"

    if SUPERSEDE_Q.search(task_text):
        targets = {c for c in evidence
                   if _basename(c).lower() in tokens}
        replacements = sorted({c["caller"] for c in claims
                               if c["relation"] == "supersedes"
                               and c["callee"] in targets})
        if replacements:
            return "\n".join(replacements)
        return _fallback(task_text, ctx)

    contra = [c for c in claims if c["relation"] == "contradicts"
              and c.get("caller") in evidence
              and c.get("callee") in evidence
              and _basename(c["caller"]).lower() in tokens
              and _basename(c["callee"]).lower() in tokens]
    if contra:
        defs = sorted({c["caller"] for c in contra} |
                      {c["callee"] for c in contra})
        return "\n".join(defs) + "\nVERDICT: NOT-DETERMINED"

    if ROUTING_Q.search(task_text) and not re.search(
            r"\b(break|caller|impact|affect)\b", task_text, re.IGNORECASE):
        subjects = {c for c in evidence
                    if _basename(c).lower() in tokens}
        routed = sorted({c["callee"] for c in claims
                         if c["relation"] == "depends_on"
                         and c["caller"] in subjects})
        if routed:
            return "\n".join(routed)
        return _fallback(task_text, ctx)

    superseded_objects = {c["callee"] for c in claims
                          if c["relation"] == "supersedes"}
    answer = sorted(impacts - superseded_objects)
    if answer:
        return "\n".join(answer)
    return _fallback(task_text, ctx)


def build_session_a(workdir):
    """Session A: ingest fixture + reviewer note, run governed scheduler.

    Returns (db_path, session_ws, snapshot, schedule_report). The
    database holds static knowledge plus exactly the governed
    acquisitions: reviewer note claims and scheduler-committed edges.
    """
    workdir = Path(workdir)
    session_ws = workdir / "session-a"
    if session_ws.exists():
        raise FileExistsError("refusing to reuse session workspace: %s"
                              % session_ws)
    shutil.copytree(str(FIXTURE), str(session_ws))
    notes = session_ws / "notes"
    notes.mkdir(exist_ok=True)
    (notes / "review-01.md").write_text(SESSION_A_NOTE, encoding="utf-8")
    db_path = str(workdir / "session-a.sqlite")
    memory = Memory(db_path, str(session_ws))
    ingest_report = memory.ingest()
    memory.close()
    schedule_report = run_schedule(db_path, str(session_ws), top_n=2,
                                   budget={"max_sites": 10,
                                           "max_commits": 5})
    memory = Memory(db_path, str(session_ws))
    snapshot = memory.store.current()
    memory.close()
    return {"db_path": db_path, "session_ws": str(session_ws),
            "snapshot": snapshot, "ingest": ingest_report,
            "schedule": schedule_report}


def _manifests():
    return [TaskManifest(id=task_id, kind=kind, text=text, key=key)
            for task_id, kind, text, key, _role in REUSE_TASKS]


def _roles():
    return {task_id: role for task_id, _k, _t, _key, role in REUSE_TASKS}


def run_reuse_experiment(workdir):
    """Paired Session-B arms: cold (fresh ingest) vs warm (Session-A DB).

    Every task runs once per arm through run_cell with identical
    budgets, toolsets, retrieval settings, and scoring. Returns a
    JSON-serializable report with per-cell outcomes and reuse metrics.
    """
    workdir = Path(workdir)
    started = time.time()
    session = build_session_a(workdir)
    roles = _roles()
    cells = []
    for manifest in _manifests():
        for arm, db_src in (("cold", None),
                            ("warm", session["db_path"])):
            result = run_cell(manifest, arm, reuse_agent,
                              str(FIXTURE), str(workdir / "cells"),
                              runs_dir=str(workdir / "cells"),
                              toolsets=("repo", "tunic"),
                              tunic_db_src=db_src)
            cells.append({"task": manifest.id, "kind": manifest.kind,
                          "role": roles[manifest.id], "arm": arm,
                          "score": result.score, "status": result.status,
                          "actions": result.actions,
                          "wall_s": round(result.wall_s, 3),
                          "answer": result.answer,
                          "transcript": result.transcript_path,
                          "provenance": result.provenance})
    wins = [c["task"] for c in cells
            if c["role"] == "win" and c["arm"] == "warm"]
    wins_hit = [t for t in wins
                if _cell(cells, t, "warm")["score"] == 1.0
                and _cell(cells, t, "cold")["score"] == 0.0]
    guards = [c["task"] for c in cells
              if c["role"] == "guard" and c["arm"] == "warm"]
    guards_held = [t for t in guards
                   if _cell(cells, t, "warm")["score"] == 1.0
                   and _cell(cells, t, "cold")["score"] == 1.0]
    warm_actions = [c["actions"] for c in cells if c["arm"] == "warm"]
    cold_actions = [c["actions"] for c in cells if c["arm"] == "cold"]
    report = {
        "reuse_version": REUSE_VERSION,
        "model_free": True,
        "deterministic_agents": True,
        "session_a": {"snapshot": session["snapshot"],
                      "assertions": session["ingest"]["assertions"],
                      "sources": session["ingest"]["sources"]},
        "cells": cells,
        "metrics": {
            "verified_reuse_rate": round(len(wins_hit) / max(len(wins), 1),
                                         3),
            "wins_hit": sorted(wins_hit),
            "wins_total": len(wins),
            "guard_hold_rate": round(len(guards_held)
                                     / max(len(guards), 1), 3),
            "guards_held": sorted(guards_held),
            "guards_total": len(guards),
            "warm_never_worse": all(
                not (_cell(cells, t, "cold")["score"] == 1.0
                     and _cell(cells, t, "warm")["score"] != 1.0)
                for t in {c["task"] for c in cells}),
            "mean_actions_warm": round(sum(warm_actions)
                                       / max(len(warm_actions), 1), 2),
            "mean_actions_cold": round(sum(cold_actions)
                                       / max(len(cold_actions), 1), 2),
        },
        "elapsed_s": round(time.time() - started, 1),
    }
    return report


def _cell(cells, task, arm):
    for cell in cells:
        if cell["task"] == task and cell["arm"] == arm:
            return cell
    raise KeyError("no cell for %s/%s" % (task, arm))


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else \
        HERE / "runs" / "reuse-v1-dev.json"
    with tempfile.TemporaryDirectory() as tmp:
        report = run_reuse_experiment(tmp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True),
                   encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2, sort_keys=True))
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
