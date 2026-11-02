"""Long-history lifecycle benchmark v1 (model-free, API-level).

Builds an evolving history on a scratch copy of the lifecycle
fixture: two audit notes asserting the same routing edge (redundant
corroboration), a retirement correction, a disputed replacement, and
an unrelated decoy edge. Ingests in stages (three snapshots),
consolidates, then suppresses the routing edge under governance
review. Tasks measure current-answer correctness, historical
traceability, stale resurrection, false forgetting, latency, context
size, and storage at each phase. No model calls, no frozen heldout
contact. See docs/architecture/memory-lifecycle.md for the contract
and docs/evaluation/strategy.md for acceptance.
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tunic.lifecycle import (consolidate, summarize_supports, suppress,  # noqa: E402
                             suppressed_ids, review_suppressions)
from tunic.memory import Memory  # noqa: E402

LIFECYCLE_VERSION = 1
FIXTURE = HERE / "lifecycle"

EXTRA = '''"""Benchmark-only extension: disputed replacement + decoy."""


def legacy_alt(kind, item):
    return ("alt", kind, item)


def orphan(x):
    return x


def orphan_caller():
    return orphan(1)
'''

NOTES = [
    ("audit-00.md", """# Lifecycle audit 01 (2026-11-02)

```tunic-claims
[{"rel": "depends_on", "subject": "svc.py::dispatch", "object": "svc.py::legacy_worker"},
 {"rel": "supersedes", "subject": "svc.py::new_handler", "object": "svc.py::old_handler"}]
```

Audit: `dispatch` routes legacy traffic to `legacy_worker` (dynamic
table, verified). `old_handler` retired; use `new_handler`.
"""),
    ("audit-01.md", """# Lifecycle audit 02 (2026-11-02)

```tunic-claims
[{"rel": "depends_on", "subject": "svc.py::dispatch", "object": "svc.py::legacy_worker"},
 {"rel": "depends_on", "subject": "svc.py::route", "object": "svc.py::new_handler"},
 {"rel": "depends_on", "subject": "svc.py::orphan_caller", "object": "svc.py::orphan"}]
```

Re-audit: dispatch routing confirmed. `route` -> `new_handler`
confirmed by inspection. Unrelated `orphan_caller` -> `orphan`
recorded as a decoy control.
"""),
    ("audit-02.md", """# Lifecycle audit 03 (2026-11-02)

```tunic-claims
[{"rel": "contradicts", "subject": "svc.py::legacy_alt", "object": "svc.py::legacy_worker"}]
```

Dispute: `legacy_alt` proposed as the canonical legacy handler,
disputing `legacy_worker` (scope dispute: new code vs legacy
traffic). Unresolved.
"""),
]

QUESTIONS = {
    "route": "Which function does dispatch route legacy requests to?",
    "replace": "What should replace old_handler?",
    "static": "Which functions break if new_handler changes?",
    "decoy": "Which functions break if orphan changes?",
    "callers": "Which functions break if legacy_worker changes?",
}


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if k not in ("ms", "elapsed_s", "wall_s", "elapsed_ms",
                             "duration_s", "setup_s")}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _symbols(result):
    return sorted({c["symbol"] for c in result.get("candidates", [])} |
                  {i["symbol"] for i in result.get("impacts", [])})


def build_history(workdir):
    """Three staged ingests on a scratch workspace. Returns paths."""
    work = Path(workdir) / "history"
    shutil.copytree(str(FIXTURE), str(work))
    (work / "extra.py").write_text(EXTRA, encoding="utf-8")
    notes = work / "notes"
    notes.mkdir(exist_ok=True)
    db_path = str(Path(workdir) / "history.sqlite")
    memory = Memory(db_path, str(work))
    snapshots = []
    (notes / NOTES[0][0]).write_text(NOTES[0][1], encoding="utf-8")
    memory.ingest()
    snapshots.append(memory.store.current())
    for name, body in NOTES[1:]:
        (notes / name).write_text(body, encoding="utf-8")
        memory.refresh()
        snapshots.append(memory.store.current())
    memory.close()
    return {"db_path": db_path, "work": str(work),
            "snapshots": snapshots}


def _table_counts(store):
    out = {}
    for table in ("assertions", "consolidated_supports",
                  "consolidation_receipts", "suppressions"):
        out[table] = store.connection.execute(
            "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    return out


def _phase_report(memory, label):
    t0 = time.perf_counter()
    answers = {key: memory.answer(text, select=True)
               for key, text in QUESTIONS.items()}
    answer_ms = (time.perf_counter() - t0) * 1000.0
    t0 = time.perf_counter()
    live_supports = _live_support_count(memory)
    live_ms = (time.perf_counter() - t0) * 1000.0
    t0 = time.perf_counter()
    summary = summarize_supports(memory)
    summary_ms = (time.perf_counter() - t0) * 1000.0
    timeline = memory.timeline("svc.py::legacy_worker")
    route_claims = sorted(
        {(c["caller"], c["relation"], c["callee"])
         for c in answers["route"].get("claims", [])})
    return {
        "label": label,
        "snapshot": memory.store.current(),
        "answers": {key: _symbols(res) for key, res in answers.items()},
        "route_claims": route_claims,
        "answer_bytes": sum(len(json.dumps(_scrub(res), sort_keys=True))
                            for res in answers.values()),
        "answer_ms": round(answer_ms, 2),
        "live_support_rows": live_supports,
        "live_support_ms": round(live_ms, 2),
        "summary_status": summary["status"],
        "summary_count": len(summary["summaries"]),
        "summary_bytes": len(json.dumps(summary["summaries"],
                                        sort_keys=True)),
        "summary_ms": round(summary_ms, 2),
        "timeline_entries": len(timeline["entries"]),
        "tables": _table_counts(memory.store),
    }


def _live_support_count(memory):
    _snapshot, view = memory.store.view()
    count = 0
    hidden = suppressed_ids(memory.store)
    for assertion in view["assertions"]:
        if assertion["id"] in hidden:
            continue
        if assertion.get("status") == "asserted_in_source":
            count += 1
    return count


def _proposition_ids(memory, relation, caller, callee):
    hidden = suppressed_ids(memory.store)
    _snapshot, view = memory.store.view()
    return sorted(a["id"] for a in view["assertions"]
                  if a["id"] not in hidden
                  and a.get("status") == "asserted_in_source"
                  and (a.get("proposition") or {}).get("relation")
                  == relation
                  and (a.get("proposition") or {}).get("caller") == caller
                  and (a.get("proposition") or {}).get("callee") == callee)


def run_lifecycle_benchmark(workdir):
    """Staged history -> consolidate -> suppress, reporting each phase."""
    built = build_history(workdir)
    memory = Memory(built["db_path"], built["work"])
    try:
        phases = [_phase_report(memory, "baseline")]
        t0 = time.perf_counter()
        consolidate(memory)
        phases[0]["consolidate_ms"] = round(
            (time.perf_counter() - t0) * 1000.0, 2)
        consolidated_summaries = summarize_supports(
            memory)["summaries"]
        phases.append(_phase_report(memory, "consolidated"))
        ids = _proposition_ids(memory, "depends_on", "svc.py::dispatch",
                               "svc.py::legacy_worker")
        t0 = time.perf_counter()
        suppress_report = suppress(
            memory, ids, policy="governance-review",
            reason="lifecycle benchmark: routing under re-audit")
        phases.append(_phase_report(memory, "suppressed"))
        phases[2]["suppress_ms"] = round(
            (time.perf_counter() - t0) * 1000.0, 2)
        phases[2]["suppress_report"] = {
            "suppressed": suppress_report["suppressed"],
            "covered": suppress_report["covered"],
            "policy": suppress_report["policy"],
        }
        suppressed_callers = phases[2]["answers"]["callers"]
        baseline_callers = phases[0]["answers"]["callers"]
        stale = [s for s in suppressed_callers
                 if s == "svc.py::dispatch"]
        callers_narrowed = (
            "svc.py::dispatch" in baseline_callers
            and suppressed_callers == ["svc.py::legacy_worker"])
        unrelated_same = (
            phases[0]["answers"]["static"] == phases[2]["answers"]["static"]
            and phases[0]["answers"]["decoy"] == phases[2]["answers"]["decoy"]
            and phases[0]["answers"]["replace"]
            == phases[2]["answers"]["replace"])
        metrics = {
            "answer_equivalence_consolidation": (
                phases[0]["answers"] == phases[1]["answers"]),
            "stale_resurrection_count": len(stale),
            "suppressed_subgraph_removed": callers_narrowed,
            "false_forgetting_count": 0 if unrelated_same else 1,
            "summary_groups": len(consolidated_summaries),
            "summary_max_support": max(
                [s["support_count"] for s in consolidated_summaries]
                or [0]),
            "summary_bytes": sum(
                len(json.dumps(s, sort_keys=True))
                for s in consolidated_summaries),
        }
        report = {
            "lifecycle_version": LIFECYCLE_VERSION,
            "model_free": True,
            "snapshots": built["snapshots"],
            "phases": phases,
            "tombstones": review_suppressions(memory)["tombstones"],
            "metrics": metrics,
            "elapsed_note": "per-op milliseconds recorded per phase",
        }
        return report
    finally:
        memory.close()


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else \
        HERE / "runs" / "lifecycle-v1-dev.json"
    with tempfile.TemporaryDirectory() as tmp:
        report = run_lifecycle_benchmark(tmp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True),
                   encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2, sort_keys=True))
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
