"""Score every scheduled task; missing/error rows cannot shrink denominators.

Result rows: {id, status: complete|error|timeout, answer: [exact atoms],
abstain: bool, evidence: [source paths], measurements: {metric: number}}.
Retrieval-only runs omit answer/abstain and never receive answer-quality scores.
"""

import argparse
import math
from pathlib import Path
import statistics

try:
    from .release import ROOT, load, verify
except ImportError:
    from release import ROOT, load, verify


def ratio(a, b):
    return a / b if b else None


def atoms(row, name):
    values = row.get(name, [])
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError("%s must be a list of strings" % name)
    if len(values) != len(set(values)):
        raise ValueError("duplicate " + name)
    return set(values)


def score(root, result):
    root = Path(root)
    digest = verify(root)
    manifest = load(root / "manifest.json")
    tasks = load(root / manifest["tasks"])
    gold = load(root / manifest["gold"])
    if result.get("benchmark_digest") != digest:
        raise ValueError("result belongs to a different benchmark release")
    if result.get("arm") not in manifest["arms"]:
        raise ValueError("unknown benchmark arm")
    track = result.get("track")
    if track not in ("agent", "retrieval"):
        raise ValueError("track must be agent or retrieval")
    if not isinstance(result.get("cohort"), dict) or not result["cohort"]:
        raise ValueError("cohort identity is required")
    rows = result.get("rows", [])
    ids = [row["id"] for row in rows]
    expected_ids = {task["id"] for task in tasks}
    if len(ids) != len(set(ids)) or not set(ids) <= expected_ids:
        raise ValueError("duplicate or unknown result task IDs")
    by_id = {row["id"]: row for row in rows}
    details = []
    tp = predicted = required = 0
    metrics = {name: [] for name in manifest["cost_metrics"]}
    complete = successes = decisions = 0
    categories = {}
    for task in tasks:
        row = by_id.get(task["id"], {"status": "missing"})
        status = row.get("status")
        if status not in ("complete", "error", "timeout", "missing"):
            raise ValueError("invalid result status")
        expected = gold[task["id"]]
        evidence = atoms(row, "evidence")
        answer = atoms(row, "answer")
        target = set(expected["answer"])
        good = status == "complete"
        complete += good
        # Count incorrect/failed claims in precision; only complete evidence
        # earns recall. This is source localization, not entailment verification.
        hits = len(evidence & set(expected["evidence"])) if good else 0
        tp += hits
        predicted += len(evidence)
        required += len(expected["evidence"])
        success = False
        if track == "agent":
            if good and type(row.get("abstain")) is not bool:
                raise ValueError("complete agent answer needs boolean abstain")
            abstain = row.get("abstain")
            valid_decision = good and abstain == (not target) and not (abstain and answer)
            decisions += valid_decision
            success = valid_decision and answer == target
            successes += success
            group = categories.setdefault(task["category"], [0, 0])
            group[0] += success
            group[1] += 1
        measures = row.get("measurements", {})
        for name in metrics:
            value = measures.get(name)
            if value is not None:
                if (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError("invalid measurement: " + name)
                metrics[name].append(value)
        details.append({"id": task["id"], "status": status,
                        "success": success if track == "agent" else None,
                        "source_hits": hits,
                        "source_required": len(expected["evidence"])})
    n = len(tasks)
    resources = {}
    for name, values in metrics.items():
        # Incomplete telemetry is unavailable, rather than a cheap/success-only mean.
        full = len(values) == n
        resources[name] = {"measured_tasks": len(values), "scheduled_tasks": n,
                           "mean": statistics.mean(values) if full else None,
                           "p95": sorted(values)[math.ceil(.95 * n) - 1] if full else None}
    return {"benchmark_digest": digest, "arm": result["arm"], "track": track,
            "cohort": result["cohort"], "scheduled_tasks": n, "completed_tasks": complete,
            "task_success": ratio(successes, n) if track == "agent" else None,
            "abstention_accuracy": ratio(decisions, n) if track == "agent" else None,
            "by_category": {k: {"success": v[0], "scheduled": v[1], "rate": ratio(*v)}
                            for k, v in categories.items()},
            "evidence_source_precision": ratio(tp, predicted),
            "evidence_source_recall": ratio(tp, required),
            "resources": resources, "tasks": details,
            "limitations": ["Public synthetic development corpus, not held out.",
                            "Evidence scores measure annotated source localization, not claim entailment.",
                            "No cross-arm superiority is inferred by this scorer."]}


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(score(args.root, load(args.result)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
