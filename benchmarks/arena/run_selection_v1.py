"""Arena selection runner v1: hybrid vs selection-filtered hybrid.

Frozen protocol, versioned with its results. Compares bm25-only,
full-hybrid, and hybrid-select (evidence-aware selection opt-in) on the
14 frozen suite tasks, the 20-task PyQuifer impact held-out (never tuned
against), and 6 fixed negative probes. Reports ranked metrics, set
scores, selection outcome histograms, abstention, latency, and paired
deltas. Writes runs/semantic-selection-v1.json.
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

from tunic.benchmark import set_scores  # noqa: E402
from tunic.embedcache import CachedEmbeddingProvider, EmbeddingCache  # noqa: E402
from tunic.eval import load_agent_suite  # noqa: E402
from tunic.identity import digest  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.retrieval import OllamaEmbeddingProvider  # noqa: E402
from run_semantic_v2 import (MODELS, NEGATIVES, THRESHOLDS, latency_stats,  # noqa: E402
                             ranked_list, ranked_metrics)

SUITE = REPO / "benchmarks/agent/suite.json"
HELDOUT = REPO / "benchmarks/arena/pyquifer-impact-v1.json"

ARMS = {
    "bm25-only": {"budget": {"exact": 0, "name": 0, "semantic": 0}, "select": False},
    "full-hybrid": {"budget": {}, "select": False},
    "hybrid-select": {"budget": {}, "select": True},
}


def summarize(rows, arms):
    out = {}
    for arm in arms:
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
            "mean_ndcg_at_5": sum(m["ndcg_at_5"] for m in mets
                                  if m["ndcg_at_5"] is not None) / len(mets),
            "latency_ms": latency_stats(lat)}
    return out


def main():
    tracemalloc.start()
    work = Path(tempfile.mkdtemp(prefix="tunic-sel-arena-"))
    suite = load_agent_suite(str(SUITE))
    heldout = json.loads(HELDOUT.read_text(encoding="utf-8"))
    env = {"python": platform.python_version(), "platform": platform.platform(),
           "cpu_count": __import__("os").cpu_count(),
           "tunic_commit": subprocess.run(
               ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
               cwd=str(REPO)).stdout.strip(),
           "suite_digest": digest(SUITE.read_bytes()),
           "heldout_digest": digest(HELDOUT.read_bytes()),
           "model": MODELS["model"], "endpoint": MODELS["endpoint"],
           "chunking": MODELS["chunking"],
           "preprocessing": MODELS["preprocessing"]}
    prepared = {}
    for repo in ("repo", "evolve", "library"):
        dst = work / repo
        shutil.copytree(SUITE.parent / repo, dst,
                        ignore=shutil.ignore_patterns("__pycache__"))
        memory = Memory(str(work / (repo + ".sqlite")), dst)
        memory.ingest()
        prepared[repo] = memory
    pyq_dst = work / "pyquifer"
    shutil.copytree("/mnt/z/PyQuifer" if Path("/mnt/z/PyQuifer").exists() else "Z:/PyQuifer",
                    pyq_dst, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    pyq_memory = Memory(str(work / "pyquifer.sqlite"), pyq_dst)
    pyq_memory.ingest()
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

    def run_rows(tasks, get_memory):
        rows = []
        for task in tasks:
            print("task", task["id"], flush=True)
            memory = get_memory(task)
            row = {"id": task["id"], "arms": {}}
            for arm, spec in ARMS.items():
                started = time.perf_counter()
                result = memory.answer(
                    task["question"], limit=5, budget=dict(spec["budget"]),
                    provider=provider if spec["budget"].get("semantic", 1) else None,
                    select=spec["select"])
                ms = (time.perf_counter() - started) * 1000.0
                ranked = ranked_list(result)
                outcomes, trace = {}, None
                if result["selection"] is not None:
                    trace = result["selection"]["trace"]
                if trace is not None:
                    for d in trace["decisions"]:
                        outcomes[d["outcome"]] = outcomes.get(d["outcome"], 0) + 1
                row["arms"][arm] = {
                    "ranked": ranked,
                    "scores": set_scores(ranked, task["gold"]),
                    "ranked_metrics": ranked_metrics(ranked, task["gold"]),
                    "latency_ms": round(ms, 1),
                    "outcome": result["outcome"],
                    "selection_outcomes": outcomes,
                    "selection_intent": (trace["intent"]["intent"]
                                         if trace else None),
                    "selection_trace": (trace["decisions"] if trace else None)}
            rows.append(row)
        return rows

    print("suite phase", flush=True)
    suite_rows = run_rows(suite["tasks"], lambda task: prepared[task["repo"]])
    print("suite done", flush=True)
    print("heldout phase", flush=True)
    heldout_rows = run_rows(
        [{"id": t["id"], "question": t["question"], "gold": t["gold"]} for t in heldout["tasks"]],
        lambda task: pyq_memory)
    print("heldout done", flush=True)
    _, peak = tracemalloc.get_traced_memory()

    sweep = []
    library = prepared["library"]
    for threshold in THRESHOLDS:
        row = {"threshold": threshold, "arms": {}}
        for arm in ("full-hybrid", "hybrid-select"):
            spec = ARMS[arm]
            positives, abstained = [], 0
            for task in suite["tasks"]:
                result = prepared[task["repo"]].answer(
                    task["question"], limit=5, threshold=threshold,
                    provider=provider, select=spec["select"])
                predicted = ranked_list(result)
                scores = set_scores(predicted, task["gold"])
                positives.append({"id": task["id"], "exact": scores["exact_match"],
                                  "abstained": result["outcome"] == "empty"})
                abstained += result["outcome"] == "empty"
            neg_abstained = 0
            for query in NEGATIVES:
                result = library.answer(query, limit=5, threshold=threshold,
                                        provider=provider, select=spec["select"])
                neg_abstained += result["outcome"] == "empty"
            row["arms"][arm] = {
                "positive_exact": sum(1 for p in positives if p["exact"]),
                "positive_abstained": abstained,
                "negatives_abstained": neg_abstained,
                "negatives_total": len(NEGATIVES)}
        sweep.append(row)

    record = {
        "benchmark": "selection-v1", "protocol": "arena-runner-selection-v1",
        "conditions": {"top_k": 5, "thresholds": THRESHOLDS, "negatives": NEGATIVES,
                       "heldout": "pyquifer-impact-v1 (1-hop caller golds, never tuned)"},
        "environment": env,
        "tracemalloc_peak_mb": round(peak / 1048576, 1),
        "suite_summary": summarize(suite_rows, ARMS),
        "heldout_summary": summarize(heldout_rows, ARMS),
        "suite_tasks": suite_rows, "heldout_tasks": heldout_rows,
        "threshold_sweep": sweep,
        "limitations": [
            "14 small synthetic suite tasks; not a general retrieval claim.",
            "Held-out is 1-hop impact only; provenance/locator generalization untested.",
            "Negatives are fixed calibration probes, not a held-out suite.",
            "Server-side embedding time and RAM not instrumented.",
        ],
    }
    out = REPO / "benchmarks/arena/runs/semantic-selection-v1.json"
    out.write_text(json.dumps(record, ensure_ascii=True, sort_keys=True, indent=2),
                   encoding="utf-8")
    for name, summary in (("suite", record["suite_summary"]),
                          ("heldout", record["heldout_summary"])):
        print("==", name)
        for arm, s in summary.items():
            print("  %-14s F1=%s R@1=%.2f R@3=%.2f MRR=%.2f nDCG=%.2f p50=%sms" % (
                arm, s["micro_f1"], s["mean_recall_at_1"], s["mean_recall_at_3"],
                s["mrr"], s["mean_ndcg_at_5"], s["latency_ms"]["p50"]))
    print("sweep:", [(s["threshold"],
                      {a: (s["arms"][a]["positive_exact"],
                           s["arms"][a]["negatives_abstained"]) for a in s["arms"]})
                     for s in sweep])
    print("wrote", out)
    for memory in list(prepared.values()) + [pyq_memory]:
        memory.close()


if __name__ == "__main__":
    main()
