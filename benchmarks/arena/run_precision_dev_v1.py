"""Precision dev-set scorer v1: retrieval-side iteration ground.

Scores bm25-only / full-hybrid / hybrid-select on the DEV set
(benchmarks/arena/pyquifer-precision-dev-v1.json) with set
precision/recall/F1 plus ranked metrics. Tune freely here; the 20-task
held-out (pyquifer-impact-v1) is scored only, never tuned against.

Writes: benchmarks/arena/runs/pyquifer-precision-dev-v1.json
"""
import gc
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tunic.benchmark import aggregate, set_scores  # noqa: E402
from tunic.embedcache import CachedEmbeddingProvider, EmbeddingCache  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.retrieval import OllamaEmbeddingProvider  # noqa: E402
from run_semantic_v2 import (MODELS, latency_stats, ranked_list,  # noqa: E402
                             ranked_metrics)

DEV = REPO / "benchmarks/arena/pyquifer-precision-dev-v1.json"

ARMS = {
    "bm25-only": {"budget": {"exact": 0, "name": 0, "semantic": 0}, "select": False},
    "full-hybrid": {"budget": {}, "select": False},
    "hybrid-select": {"budget": {}, "select": True},
}


def caller_ranked(task, ranked):
    """Ranked list minus the query-named subject (gold-free rule).

    For impact questions the named leaf is the referent, never an
    answer; measuring caller ranking separately keeps a rank-1 subject
    from masking whether true callers surface early. Exclusion uses only
    the question text (whole-word leaf match), never the gold set.
    """
    import re
    words = set(re.findall(r"[a-z_][a-z_0-9]*", task["question"].lower()))
    return [s for s in ranked
            if s.split("::")[-1].split(".")[-1].lower() not in words]


def summarize(rows, arms):
    out = {}
    for arm in arms:
        scored = [r["arms"][arm]["scores"] for r in rows]
        mets = [r["arms"][arm]["ranked_metrics"] for r in rows]
        caller_mets = [ranked_metrics(caller_ranked(r, r["arms"][arm]["ranked"]),
                                      r["gold"])
                       for r in rows]
        agg = aggregate([{"tp": s["tp"], "fp": s["fp"], "fn": s["fn"],
                          "exact_match": s["exact_match"], "available": True}
                         for s in scored])
        lat = [r["arms"][arm]["latency_ms"] for r in rows]
        out[arm] = {
            "micro_precision": agg["micro_precision"],
            "micro_recall": agg["micro_recall"],
            "micro_f1": agg["micro_f1"],
            "exact_tasks": agg["exact_tasks"],
            "junk_symbols": sorted({s for r in rows
                                    for s in r["arms"][arm]["scores"]["unexpected"]
                                    if "bench_" in s or "/tests/" in s}),
            "mean_recall_at_1": sum(m["recall_at_1"] for m in mets) / len(mets),
            "mean_recall_at_3": sum(m["recall_at_3"] for m in mets) / len(mets),
            "mrr": sum(m["mrr"] for m in mets) / len(mets),
            "caller_recall_at_1": sum(m["recall_at_1"] for m in caller_mets) / len(caller_mets),
            "caller_recall_at_3": sum(m["recall_at_3"] for m in caller_mets) / len(caller_mets),
            "caller_mrr": sum(m["mrr"] for m in caller_mets) / len(caller_mets),
            "mean_ndcg_at_5": sum(m["ndcg_at_5"] for m in mets
                                  if m["ndcg_at_5"] is not None) / len(mets),
            "latency_ms": latency_stats(lat)}
    return out


def main():
    tracemalloc.start()
    dev = json.loads(DEV.read_text(encoding="utf-8"))
    env = {"python": platform.python_version(), "platform": platform.platform(),
           "cpu_count": __import__("os").cpu_count(),
           "tunic_commit": subprocess.run(
               ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
               cwd=str(REPO)).stdout.strip(),
           "dev_digest": __import__("tunic.identity", fromlist=["digest"]).digest(
               DEV.read_bytes()),
           "model": MODELS["model"], "endpoint": MODELS["endpoint"],
           "chunking": MODELS["chunking"],
           "preprocessing": MODELS["preprocessing"]}
    work = Path(tempfile.mkdtemp(prefix="tunic-precision-dev-"))
    pyq_root = __import__("os").environ.get("TUNIC_PYQ_ROOT", "Z:/PyQuifer")
    pyq_dst = work / "pyquifer"
    shutil.copytree(pyq_root, pyq_dst,
                    ignore=shutil.ignore_patterns("__pycache__", ".git"))
    memory = Memory(str(work / "pyquifer.sqlite"), pyq_dst)
    memory.ingest()
    _, ingest_peak = tracemalloc.get_traced_memory()
    gc.collect()

    cache = EmbeddingCache(str(work / "vectors.sqlite"),
                           model_digest=MODELS["model"]["blob_digest"],
                           chunking_version=MODELS["chunking"]["version"],
                           preprocessing_version=MODELS["preprocessing"]["version"])
    provider = CachedEmbeddingProvider(
        OllamaEmbeddingProvider(model="nomic-embed-text",
                                endpoint=MODELS["endpoint"]), cache)
    assert provider.available(), "embedding backend unavailable"

    rows = []
    for task in dev["tasks"]:
        print("task", task["id"], flush=True)
        row = {"id": task["id"], "question": task["question"],
               "gold": task["gold"], "arms": {}}
        for arm, spec in ARMS.items():
            started = time.perf_counter()
            result = memory.answer(
                task["question"], limit=5, budget=dict(spec["budget"]),
                provider=provider if spec["budget"].get("semantic", 1) else None,
                select=spec["select"])
            ms = (time.perf_counter() - started) * 1000.0
            ranked = ranked_list(result)
            answer_ranked = caller_ranked(task, ranked)
            row["arms"][arm] = {
                "ranked": ranked,
                "scores": set_scores(answer_ranked, task["gold"]),
                "ranked_metrics": ranked_metrics(ranked, task["gold"]),
                "latency_ms": round(ms, 1),
                "outcome": result["outcome"]}
        rows.append(row)
    _, peak = tracemalloc.get_traced_memory()

    record = {
        "benchmark": "pyquifer-precision-dev-v1",
        "protocol": "dev-only retrieval scoring; tune freely; held-out stays untouched. "
                    "Set metrics (P/R/F1/exact) score the answer set after the gold-free "
                    "query-leaf exclusion (caller_ranked rule: the named subject is context, "
                    "never an impact answer); ranking metrics use the raw list.",
        "conditions": {"top_k": 5, "dev": "pyquifer-precision-dev-v1 (disjoint from held-out)"},
        "environment": env,
        "tracemalloc_peak_mb": round(peak / 1048576, 1),
        "dev_summary": summarize(rows, ARMS),
        "dev_tasks": rows,
        "limitations": [
            "20 dev tasks, 1-hop impact phrasing; retrieval only, no model loop.",
            "Server-side embedding time and RAM not instrumented.",
        ],
    }
    out = REPO / "benchmarks/arena/runs/pyquifer-precision-dev-v1.json"
    out.write_text(json.dumps(record, ensure_ascii=True, sort_keys=True, indent=2),
                   encoding="utf-8")
    for arm, s in record["dev_summary"].items():
        print("  %-14s P=%.3f R=%.3f F1=%.3f exact=%d junk=%d R@1=%.2f R@3=%.2f cR@1=%.2f cMRR=%.2f p50=%sms" % (
            arm, s["micro_precision"] or 0, s["micro_recall"] or 0,
            s["micro_f1"] or 0, s["exact_tasks"], len(s["junk_symbols"]),
            s["mean_recall_at_1"], s["mean_recall_at_3"],
            s["caller_recall_at_1"], s["caller_mrr"],
            s["latency_ms"]["p50"]))
        if s["junk_symbols"]:
            print("    junk: %s" % s["junk_symbols"][:5])
    print("wrote", out)
    memory.close()


if __name__ == "__main__":
    main()
