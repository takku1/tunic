import json
import sys

sys.path.insert(0, "benchmarks/agent")
from run_acq_v1 import summarize_acq

RUNS = "benchmarks/agent/runs/"


def load(name):
    with open(RUNS + name, encoding="utf-8") as fh:
        return json.load(fh)["tasks"]


b1 = load("model-ab-acq-v1-batch1.json")
b2 = load("model-ab-acq-v1-batch2.json")
b3 = load("model-ab-acq-v1-batch3.json")
b4 = load("model-ab-acq-v1-batch4.json")
b3kept = [t for t in b3 if t.get("scenario") not in ("correct", "irrelevant")]
rows = b1 + b2 + b3kept + b4
print("rows:", len(rows), "voided:", len(b3) - len(b3kept))
print("scenarios:", sorted({t["scenario"] for t in rows}))
rec = {
    "benchmark": "model-ab-acq-v1",
    "protocol": (
        "frozen ACQ v1: 10 seeded scenarios x 5 reps x 3 arms; "
        "product commit_proposal path, exact-triple scorer; "
        "abstain scenarios pass on no-commit plus frontier entry; "
        "reps are robustness probes, not independent tasks; "
        "held-out PyQuifer tasks untouched"),
    "merged_from": ["model-ab-acq-v1-batch1.json",
                    "model-ab-acq-v1-batch2.json",
                    "model-ab-acq-v1-batch3.json",
                    "model-ab-acq-v1-batch4.json"],
    "superseded": (
        "batch3 correct/* (void: scorer obsolete list contained the gold "
        "symbol, pass impossible) and irrelevant/* (void: ledger-dedupe "
        "coalescing not accepted by scorer) replaced by batch4 reruns "
        "under identical fixtures (digests unchanged)"),
    "summary": summarize_acq(rows),
    "tasks": rows,
}
with open(RUNS + "model-ab-acq-v1.json", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, indent=1))
print(json.dumps(rec["summary"], indent=1))
