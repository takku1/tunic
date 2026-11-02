"""Frontier Ranking v1 experiment: ranked top-N vs raw-count top-N.

Builds a scratch dogfood database (ingest target, frontier auto-files),
ranks its clusters with Ranking v1, investigates the top-N ranked and
the top-N raw-occurrence baseline clusters with an identical per-cluster
budget, and reports verified-relationship yield per arm.

Yield = entries resolved with proof + newly asserted edges, both
mechanical (governance + proof validation). Commits land only in
scratch copies; the source tree and any existing database are untouched.
No model calls.

Usage:
    python benchmarks/frontier/v1_rank_vs_baseline.py \
        --target Z:/tunic/tunic --top-n 3 --out benchmarks/frontier/runs/v1.json
"""
import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tunic.investigate import compare_arms  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.triage import analyze  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ranked vs baseline frontier investigation")
    parser.add_argument("--target", default=str(REPO / "tunic"),
                        help="tree to ingest as dogfood")
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--max-sites", type=int, default=5)
    parser.add_argument("--max-commits", type=int, default=3)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp())
    db = str(tmp / "dogfood.sqlite")
    memory = Memory(db, args.target)
    ingest_result = memory.ingest()
    memory.close()

    report = analyze(db, root=args.target)
    budget = {"max_sites": args.max_sites, "max_commits": args.max_commits}
    arms = compare_arms(db, args.target, top_n=args.top_n, budget=budget)

    result = {
        "experiment": "frontier-ranking-v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": args.target,
        "ranking_top": [
            {"key": c["key"], "score": c["score"],
             "factors": c["factors"], "occurrences": c["occurrences"],
             "distinct_sites": c["distinct_sites"], "queue": c["queue"],
             "reason": c["reason"]}
            for c in report["top"][:args.top_n + 3]],
        "ingest": {"symbols": ingest_result.get("symbols"),
                   "assertions": ingest_result.get("assertions"),
                   "frontier_filed": ingest_result.get("frontier_filed")},
        "clusters": report["counts"]["active_clusters"],
        "arms": arms,
    }
    text = json.dumps(result, indent=1)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    else:
        print(text)
    shutil.rmtree(tmp, True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
