"""Rescore the agent suite on equal terms.

`tunic benchmark --agent` lets Tunic return candidates plus impacts while
lexical returns top-k, adds an evidence requirement only Tunic can fail,
and leaves `experimental` tasks out of the headline. Those are defensible
choices for a regression gate and unfair for a comparison. Here every arm
gets the same k symbols and the same pass rule, over all tasks.

No model runs in this suite. It measures retrieval, nothing more.
"""

import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tunic.baselines import BM25  # noqa: E402
from tunic.eval import _answer_symbols  # noqa: E402
from tunic.memory import Memory  # noqa: E402


def passes(task, returned):
    return (set(task["gold"]) <= set(returned)
            and not set(task["forbidden"]) & set(returned))


def main(limit=5):
    suite = json.loads((ROOT / "benchmarks/agent/suite.json").read_text(encoding="utf-8"))
    rows = []
    with tempfile.TemporaryDirectory(prefix="tunic-rescore-") as work:
        for repo in sorted({t["repo"] for t in suite["tasks"]}):
            root = Path(work) / repo
            shutil.copytree(ROOT / "benchmarks/agent" / repo, root)
            lexical = BM25(root)
            with Memory(str(Path(work) / (repo + ".sqlite")), root) as memory:
                memory.ingest()
                for task in [t for t in suite["tasks"] if t["repo"] == repo]:
                    answer = memory.answer(task["question"], limit=limit,
                                           policy="current", select=False)
                    full = _answer_symbols(answer)
                    bm25 = [h["symbol"] for h in lexical.search_text(task["question"], top_k=limit)]
                    rows.append({"id": task["id"], "experimental": task["experimental"],
                                 "tunic_returned": len(full), "bm25_returned": len(bm25),
                                 "tunic_uncapped": passes(task, full),
                                 "tunic_at_k": passes(task, full[:limit]),
                                 "bm25_at_k": passes(task, bm25)})
    total = len(rows)
    print("all %d tasks at k=%d: tunic %d, bm25 %d" % (
        total, limit, sum(r["tunic_at_k"] for r in rows), sum(r["bm25_at_k"] for r in rows)))
    print("tunic uncapped, as the suite scores it: %d" % sum(r["tunic_uncapped"] for r in rows))
    print("mean symbols returned: tunic %.1f, bm25 %.1f" % (
        sum(r["tunic_returned"] for r in rows) / total, sum(r["bm25_returned"] for r in rows) / total))
    for row in rows:
        if row["experimental"] or not row["tunic_at_k"] or not row["bm25_at_k"]:
            print(" ", row)


if __name__ == "__main__":
    main()
