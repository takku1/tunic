"""Arena semantic runner v2: ranked metrics, cold/warm phases, negatives.

Frozen protocol, versioned with its results. Compares lexical, semantic,
and hybrid retrieval on the 14 frozen gold tasks plus fixed negative
calibration probes. Reports Recall@1/@3, MRR, nDCG@5, micro P/R/F1,
per-phase latency (cold model+cache, warm, reopened cache), threshold
sweep abstention curves, environment manifest, and resource usage.
No tuning: gold labels are only scored, never fed back.

Usage: PYTHONPATH=<tunic-repo> python benchmarks/arena/run_semantic_v2.py
Requires the pinned local embedding model (see ../models.json).
Writes runs/semantic-baseline-v3.json.
"""
import gc
import json
import os
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

from tunic.benchmark import set_scores  # noqa: E402
from tunic.embedcache import CachedEmbeddingProvider, EmbeddingCache  # noqa: E402
from tunic.eval import load_agent_suite  # noqa: E402
from tunic.identity import digest  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.retrieval import OllamaEmbeddingProvider  # noqa: E402

MODELS = json.loads((REPO / "benchmarks/arena/models.json").read_text(encoding="utf-8"))
SUITE = REPO / "benchmarks/agent/suite.json"

ARMS = {
    "bm25-only": {"exact": 0, "name": 0, "semantic": 0},
    "embeddings-only": {"exact": 0, "name": 0, "lexical": 0},
    "bm25-plus-embeddings": {"exact": 0, "name": 0},
    "full-hybrid": {},
}

NEGATIVES = [
    "quantum banana firmware requirements",
    "how do I bake sourdough bread",
    "explain the offside rule in soccer",
    "what is the capital of Assyria",
    "summarize yesterday's standup notes",
    "translate hello to Klingon",
]

THRESHOLDS = [0.0, 0.01, 0.02, 0.05, 0.1]


def ranked_list(result):
    """Fusion-ordered candidates, then graph impacts not already present."""
    ordered = [c["symbol"] for c in result["candidates"]]
    seen = set(ordered)
    for entry in result["impacts"]:
        if entry["symbol"] not in seen:
            seen.add(entry["symbol"])
            ordered.append(entry["symbol"])
    return ordered


def ranked_metrics(ranked, gold):
    gold = list(gold)
    if not gold:
        raise ValueError("ranked metrics need a non-empty gold set")
    positions = [i + 1 for i, symbol in enumerate(ranked) if symbol in set(gold)]
    first = min(positions) if positions else None
    import math
    dcg = sum(1.0 / math.log2(i + 2) for i in range(min(5, len(ranked)))
              if ranked[i] in set(gold))
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(5, len(gold))))
    return {
        "recall_at_1": len(set(ranked[:1]) & set(gold)) / len(gold),
        "recall_at_3": len(set(ranked[:3]) & set(gold)) / len(gold),
        "mrr": 1.0 / first if first else 0.0,
        "ndcg_at_5": dcg / idcg if idcg else None,
        "first_hit_rank": first,
    }


def latency_stats(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return {"n": len(ordered), "mean": sum(ordered) / len(ordered),
            "p50": (ordered[mid] + ordered[~mid]) / 2,
            "max": ordered[-1], "min": ordered[0]}


def main():
    tracemalloc.start()
    work = Path(tempfile.mkdtemp(prefix="tunic-arena-v2-"))
    suite = load_agent_suite(str(SUITE))
    env = {"python": platform.python_version(), "platform": platform.platform(),
           "cpu_count": os.cpu_count(),
           "tunic_commit": subprocess.run(
               ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
               cwd=str(REPO)).stdout.strip(),
           "suite_digest": digest(SUITE.read_bytes()),
           "model": MODELS["model"], "endpoint": MODELS["endpoint"],
           "chunking": MODELS["chunking"],
           "preprocessing": MODELS["preprocessing"]}
    prepared, ingest_seconds, db_bytes = {}, {}, 0
    for repo in ("repo", "evolve", "library"):
        dst = work / repo
        shutil.copytree(SUITE.parent / repo, dst,
                        ignore=shutil.ignore_patterns("__pycache__"))
        memory = Memory(str(work / (repo + ".sqlite")), dst)
        started = time.perf_counter()
        memory.ingest()
        ingest_seconds[repo] = round(time.perf_counter() - started, 2)
        db_bytes += (work / (repo + ".sqlite")).stat().st_size
        prepared[repo] = memory
    _, ingest_peak = tracemalloc.get_traced_memory()
    gc.collect()

    def build_provider(cache_path):
        cache = EmbeddingCache(
            str(cache_path), model_digest=MODELS["model"]["blob_digest"],
            chunking_version=MODELS["chunking"]["version"],
            preprocessing_version=MODELS["preprocessing"]["version"])
        provider = CachedEmbeddingProvider(
            OllamaEmbeddingProvider(model="nomic-embed-text",
                                    endpoint=MODELS["endpoint"]), cache)
        assert provider.available(), "embedding backend unavailable"
        return provider

    def run_phase(name, provider):
        rows = []
        for task in suite["tasks"]:
            memory = prepared[task["repo"]]
            row = {"id": task["id"], "repo": task["repo"], "arms": {}}
            for arm, budget in ARMS.items():
                started = time.perf_counter()
                result = memory.answer(task["question"], limit=5,
                                       budget=dict(budget),
                                       provider=provider if budget.get(
                                           "semantic", 1) else None)
                ms = (time.perf_counter() - started) * 1000.0
                ranked = ranked_list(result)
                row["arms"][arm] = {
                    "ranked": ranked,
                    "scores": set_scores(ranked, task["gold"]),
                    "ranked_metrics": ranked_metrics(ranked, task["gold"]),
                    "latency_ms": round(ms, 1),
                    "outcome": result["outcome"],
                    "n_mentions": len(result["mentions"]),
                    "n_claims": len(result["claims"]),
                    "n_candidates_fused": len(result["candidates"])}
            rows.append(row)
        _, peak = tracemalloc.get_traced_memory()
        return {"name": name, "tasks": rows,
                "tracemalloc_peak_mb": round(peak / 1048576, 1),
                "cache": provider.cache.stats() if provider is not None else None}

    def summarize(rows):
        out = {}
        for arm in ARMS:
            scored = [r["arms"][arm]["scores"] for r in rows]
            mets = [r["arms"][arm]["ranked_metrics"] for r in rows]
            tp = sum(s["tp"] for s in scored)
            fp = sum(s["fp"] for s in scored)
            fn = sum(s["fn"] for s in scored)
            lat = [r["arms"][arm]["latency_ms"] for r in rows]
            out[arm] = {
                "micro_precision": tp / (tp + fp) if tp + fp else None,
                "micro_recall": tp / (tp + fn) if tp + fn else None,
                "micro_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                "exact_tasks": sum(s["exact_match"] for s in scored),
                "mean_recall_at_1": sum(m["recall_at_1"] for m in mets) / len(mets),
                "mean_recall_at_3": sum(m["recall_at_3"] for m in mets) / len(mets),
                "mrr": sum(m["mrr"] for m in mets) / len(mets),
                "mean_ndcg_at_5": sum(m["ndcg_at_5"] for m in mets if m["ndcg_at_5"] is not None) / len(mets),
                "latency_ms": latency_stats(lat)}
        return out

    cold = run_phase("cold-model-cold-cache",
                     build_provider(work / "vectors-cold.sqlite"))
    # Warm phase reopens the same cache file with new objects; the model
    # stays loaded server-side, so this measures warm retrieval, not load.
    warm_provider = build_provider(work / "vectors-cold.sqlite")
    warm = run_phase("warm-model-warm-cache", warm_provider)
    cache_bytes = (work / "vectors-cold.sqlite").stat().st_size
    cache_rows = warm_provider.cache.connection.execute(
        "SELECT COUNT(*) FROM vectors").fetchone()[0]
    restart_provider = build_provider(work / "vectors-cold.sqlite")
    restart = run_phase("reopened-cache", restart_provider)

    sweep = []
    library = prepared["library"]
    for threshold in THRESHOLDS:
        row = {"threshold": threshold, "positives": {}, "negatives_abstained": 0}
        for task in suite["tasks"]:
            result = prepared[task["repo"]].answer(
                task["question"], limit=5, threshold=threshold,
                provider=warm_provider)
            predicted = ranked_list(result)
            scores = set_scores(predicted, task["gold"])
            row["positives"][task["id"]] = {
                "exact": scores["exact_match"],
                "abstained": result["outcome"] == "empty"}
        neg_abstained = 0
        for query in NEGATIVES:
            result = library.answer(query, limit=5, threshold=threshold,
                                    provider=warm_provider)
            if result["outcome"] == "empty":
                neg_abstained += 1
        row["negatives_abstained"] = neg_abstained
        row["negatives_total"] = len(NEGATIVES)
        row["positive_exact"] = sum(1 for v in row["positives"].values() if v["exact"])
        row["positive_abstained"] = sum(1 for v in row["positives"].values() if v["abstained"])
        sweep.append(row)

    graph_state = {}
    for repo, memory in prepared.items():
        _, view = memory.store.view()
        relations = {}
        for a in view["assertions"]:
            key = (a["proposition"].get("relation"), a.get("status"))
            relations.setdefault(str(key), 0)
            relations[str(key)] += 1
        graph_state[repo] = {
            "symbols": len(view["symbols"]),
            "assertions": len(view["assertions"]),
            "by_relation_status": relations,
            "mention_candidates": sum(
                1 for d in view["diagnostics"] if d.get("kind") == "mention_candidate")}

    record = {
        "benchmark": "semantic-baseline-v3", "protocol": "arena-runner-v2",
        "conditions": {"top_k": 5, "thresholds": THRESHOLDS,
                       "negatives": NEGATIVES,
                       "percentiles": "mean/p50/max only; n=14 per arm supports no p95/p99",
                       "cold_note": "cold phase includes one-time model load on first embedding call; server keep-alive may shorten later cold starts"},
        "environment": env,
        "ingest_seconds": ingest_seconds,
        "ingest_tracemalloc_peak_mb": round(ingest_peak / 1048576, 1),
        "disk_bytes": {"graph_dbs": db_bytes, "vector_cache": cache_bytes,
                       "vector_rows": cache_rows},
        "graph_state": graph_state,
        "phases": [{"name": p["name"], "summary": summarize(p["tasks"]),
                    "tracemalloc_peak_mb": p["tracemalloc_peak_mb"],
                    "cache": p["cache"], "tasks": p["tasks"]}
                   for p in (cold, warm, restart)],
        "threshold_sweep_full_hybrid": sweep,
        "limitations": [
            "14 small synthetic tasks; not a general semantic-retrieval claim.",
            "Semantic candidates are proposals only; graph edges unchanged.",
            "n=14 supports means and p50 only; no p95/p99, no confidence intervals.",
            "Server-side embedding time and RAM not instrumented; misses count client-side texts sent.",
            "Negatives are fixed calibration probes, not a held-out suite.",
        ],
    }
    out = REPO / "benchmarks/arena/runs/semantic-baseline-v3.json"
    out.write_text(json.dumps(record, ensure_ascii=True, sort_keys=True, indent=2),
                   encoding="utf-8")
    for phase in (cold, warm, restart):
        print("==", phase["name"], "cache:", phase["cache"])
        for arm, summary in summarize(phase["tasks"]).items():
            print("  %-20s F1=%s R@1=%.2f R@3=%.2f MRR=%.2f nDCG=%.2f p50=%sms" % (
                arm, summary["micro_f1"], summary["mean_recall_at_1"],
                summary["mean_recall_at_3"], summary["mrr"],
                summary["mean_ndcg_at_5"], summary["latency_ms"]["p50"]))
    print("sweep:", [(s["threshold"], s["positive_exact"], s["negatives_abstained"]) for s in sweep])
    print("wrote", out)
    for memory in prepared.values():
        memory.close()


if __name__ == "__main__":
    main()
