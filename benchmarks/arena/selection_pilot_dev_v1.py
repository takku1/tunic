"""Selection-default pilot, dev set v1 (embedding-free, deterministic).

Compares three arms on a tiny fixture repo using Memory.answer with
no embedding provider (semantic stage records itself skipped):
  bm25-only    lexical stage only (budgets zero the rest)
  full-hybrid  exact + name + lexical fusion (current default)
  hybrid-select  full-hybrid filtered by the selection layer

Tasks cover exact-symbol, name, import-context, ambiguous, decoy,
cold-site, insufficient-evidence, checkout-flow, container, and
helper queries. Golds are text-retrievable relevant symbols only;
caller discovery is measured by Arena stage-1, not here.

PREDECLARED ADOPTION CRITERIA (frozen; mirrored by
tests/test_selection_pilot.py -- do not tune the pilot to pass):
adopt select as default iff ALL hold on this dev set:
  1. micro-F1(select) > micro-F1(fusion) AND micro-P(select) >
     micro-P(fusion), strictly.
  2. micro-R(select) >= micro-R(fusion).
  3. decoy/empty-gold non-empty rate (select) <= (fusion).
  4. false-abstain rate (select) <= (fusion).
  5. p95 latency(select) < 2 * p95 latency(fusion).
BM25 gap is reported as context, not a gate: BM25 is a diagnostic
arm, not the current default.

Writes benchmarks/arena/runs/selection-pilot-dev-v1.json. The dev
set lives inside this script; frozen suites are read-only here.
"""
import gc
import json
import platform
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tunic.memory import Memory  # noqa: E402

ARMS = {
    "bm25-only": {"budget": {"exact": 0, "name": 0, "semantic": 0},
                  "select": False},
    "full-hybrid": {"budget": {}, "select": False},
    "hybrid-select": {"budget": {}, "select": True},
}

CRITERIA = [
    "micro-F1(select) > micro-F1(fusion) AND micro-P(select) > micro-P(fusion)",
    "micro-R(select) >= micro-R(fusion)",
    "decoy/empty-gold non-empty rate (select) <= (fusion)",
    "false-abstain rate (select) <= (fusion)",
    "p95 latency(select) < 2 * p95 latency(fusion)",
]

LEGACY = "".join(
    "def cover_legacy_%d(rows):\n    return rows\n\n\n" % i
    for i in range(12))

FILES = {
    "__init__.py": "",
    "legacy.py": LEGACY,
    "core.py": ("def cover(table):\n"
                "    return table\n"
                "\n\n"
                "def helper(value):\n"
                "    return value\n"
                "\n\n"
                "class Gadget:\n"
                "    def start(self):\n"
                "        return cover(self)\n"),
    "shop.py": ("from core import cover\n"
                "\n\n"
                "def run(table):\n"
                "    return cover(table)\n"
                "\n\n"
                "def checkout(cart):\n"
                "    return [cover(item) for item in cart]\n"),
    "billing.py": ("def cover(log):\n"
                   "    return log\n"
                   "\n\n"
                   "def invoice(order):\n"
                   "    total = cover(order)\n"
                   "    return total\n"),
    "analytics.py": ("def cover_all(rows):\n"
                     "    return [r for r in rows]\n"
                     "\n\n"
                     "def cover_version(tag):\n"
                     "    return tag\n"),
}

# (id, question, gold symbol set). Empty gold = must stay empty.
TASKS = [
    ("exact", "core.py::cover",
     {"core.py::cover"}),
    ("name", "cover function",
     {"core.py::cover", "billing.py::cover"}),
    ("import-context", "from core import cover usage",
     {"core.py::cover"}),
    ("ambiguous", "which cover implementation handles refunds",
     {"core.py::cover", "billing.py::cover"}),
    ("decoy", "does shop.py::run call analytics.py::cover_all",
     set()),
    ("cold", "billing invoice helper",
     {"billing.py::invoice"}),
    ("insufficient", "quantum banana firmware requirements",
     set()),
    ("checkout-flow", "checkout flow",
     {"shop.py::checkout"}),
    ("container", "Gadget start method",
     {"core.py::Gadget.start"}),
    ("helper", "helper utility",
     {"core.py::helper"}),
]

LIMIT = 5


def percentile(values, pct):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(pct / 100.0 * len(ordered)))]


def main():
    tracemalloc.start()
    tmp = Path(tempfile.mkdtemp(prefix="tunic-select-pilot-"))
    root = tmp / "repo"
    root.mkdir()
    for name, text in FILES.items():
        (root / name).write_text(text, encoding="utf-8")
    out_path = (REPO / "benchmarks" / "arena" / "runs" /
                "selection-pilot-dev-v1.json")
    memory = Memory(str(tmp / "pilot.sqlite"), str(root))
    memory.ingest()
    _, ingest_peak = tracemalloc.get_traced_memory()
    gc.collect()

    rows = []
    for task_id, question, gold in TASKS:
        row = {"id": task_id, "question": question,
               "gold": sorted(gold), "arms": {}}
        for arm, cfg in ARMS.items():
            t0 = time.perf_counter()
            out = memory.answer(question, limit=LIMIT, policy="transparent",
                                budget=dict(cfg["budget"]),
                                select=cfg["select"])
            wall_ms = (time.perf_counter() - t0) * 1000.0
            got = {c["symbol"] for c in out["candidates"]}
            tp = len(got & gold)
            fp = len(got - gold)
            fn = len(gold - got)
            row["arms"][arm] = {
                "returned": sorted(got), "tp": tp, "fp": fp, "fn": fn,
                "outcome": out["outcome"],
                "latency_ms": wall_ms,
                "context_symbols": len(out["candidates"]),
                "stages": {k: {"candidates": v["candidates"],
                               "ms": v["ms"], "skipped": v["skipped"]}
                           for k, v in out["stages"].items()},
            }
        rows.append(row)

    summary = {}
    for arm in ARMS:
        tp = sum(r["arms"][arm]["tp"] for r in rows)
        fp = sum(r["arms"][arm]["fp"] for r in rows)
        fn = sum(r["arms"][arm]["fn"] for r in rows)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        lat = [r["arms"][arm]["latency_ms"] for r in rows]
        empties = [r for r in rows if not r["gold"]]
        answerables = [r for r in rows if r["gold"]]
        summary[arm] = {
            "micro_precision": prec, "micro_recall": rec,
            "micro_f1": (2 * prec * rec / (prec + rec)
                         if prec + rec else 0.0),
            "decoy_nonempty_rate": sum(
                1 for r in empties if r["arms"][arm]["tp"] +
                r["arms"][arm]["fp"] > 0) / len(empties),
            "false_abstain_rate": sum(
                1 for r in answerables
                if r["arms"][arm]["outcome"] == "empty") / len(answerables),
            "p50_latency_ms": percentile(lat, 50),
            "p95_latency_ms": percentile(lat, 95),
            "mean_context_symbols": sum(
                r["arms"][arm]["context_symbols"] for r in rows) / len(rows),
        }

    sel, fus, bm = (summary["hybrid-select"], summary["full-hybrid"],
                    summary["bm25-only"])
    checks = {
        "f1_up": sel["micro_f1"] > fus["micro_f1"],
        "precision_up": sel["micro_precision"] > fus["micro_precision"],
        "recall_held": sel["micro_recall"] >= fus["micro_recall"],
        "decoy_no_worse": (sel["decoy_nonempty_rate"] <=
                           fus["decoy_nonempty_rate"]),
        "abstain_no_worse": (sel["false_abstain_rate"] <=
                             fus["false_abstain_rate"]),
        "latency_ok": (sel["p95_latency_ms"] < 2 * fus["p95_latency_ms"]),
    }
    verdict = ("ADOPT" if all(checks.values()) else "RETAIN")
    failing = sorted(k for k, v in checks.items() if not v)

    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    report = {
        "benchmark": "selection-pilot-dev-v1",
        "criteria": CRITERIA,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "tunic_commit": tunic_commit,
            "semantic": "skipped (no embedding provider; lexical regime)",
            "limit": LIMIT,
        },
        "fixture_files": sorted(FILES),
        "summary": summary,
        "bm25_f1_gap_select_minus_bm25": (sel["micro_f1"] -
                                          bm["micro_f1"]),
        "checks": checks,
        "verdict": verdict,
        "failing_checks": failing,
        "ingest_peak_kb": ingest_peak / 1024.0,
        "tasks": rows,
    }
    out_path.write_text(json.dumps(report, indent=1, sort_keys=True),
                        encoding="utf-8")
    memory.close()
    print(json.dumps({"verdict": verdict, "failing": failing,
                      "summary": summary}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
