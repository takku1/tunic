"""Re-score ACQ v2 artifacts after the apply-gate scorer fix (no model).

The first frozen artifacts gated every apply row on the propose commit
(`pass = parsed_ok and prop.committed`). For correction and duplicate
cells the knowledge pre-exists by scenario design, so the correct
propose outcome is a no-op (DUPLICATE reconcile or abstention) and the
gate forced apply pass=False even when retrieval honored the
correction. The raw model observations (parsed/stale per apply row) are
unaffected; only the pass computation changes. This script recomputes
apply pass and the summary from the recorded rows and stamps a
provenance note. Positive cells keep the commit gate.

Usage: python rescore_acq_v2.py runs/acq-v2-dev.json [runs/acq-v2-heldout.json ...]
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from run_acq_v2 import apply_pass, summarize_acq  # noqa: E402

NOTE = ("apply-gate scorer fix: correction/duplicate apply rows re-scored "
        "without the propose-commit gate (their knowledge pre-exists, so the "
        "correct propose outcome is a no-op); raw parsed/stale observations "
        "unchanged; positive cells keep the gate")


def rescore(path):
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = record["tasks"]
    for r in rows:
        if not r["id"].endswith("-apply"):
            continue
        parsed_ok = set(r["gold"]) <= set(r["parsed"]) and not r["stale"]
        r["pass"] = apply_pass(r["scenario"], parsed_ok, r.get("had_commit"))
    record["summary"] = summarize_acq(rows)
    notes = record.setdefault("scorer_notes", [])
    notes.append(NOTE)
    Path(path).write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("re-scored", path, "(%d apply rows)" %
          sum(1 for r in rows if r["id"].endswith("-apply")))


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        rescore(arg)
