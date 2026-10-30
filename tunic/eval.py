"""Agent memory evaluation: no-memory vs lexical vs Tunic graph retrieval.

Held-out by construction: the suite fixtures live outside every other
benchmark, and the comparison replays three retrieval conditions over the
same ingested graph. The Tunic arm answers under policy="current", so
superseded evidence is excluded while disputes stay visible. stdlib only;
no embeddings, no network. Latency and context size are measured, not
modeled: no model is called, so token counts are not reported.
"""

import json
import shutil
import tempfile
import time
from pathlib import Path

from .baselines import BM25
from .memory import Memory

CONDITIONS = ("none", "lexical", "tunic")


def load_agent_suite(path):
    """Load and validate an agent held-out suite file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if raw.get("version") != 2:
        raise ValueError("unsupported agent suite version")
    if not raw.get("tasks"):
        raise ValueError("agent suite has no tasks")
    for task in raw["tasks"]:
        for field in ("id", "repo", "question", "gold", "forbidden",
                      "evidence", "requires"):
            if field not in task:
                raise ValueError("agent task missing %r" % field)
        if not task["gold"]:
            raise ValueError("agent task %r has empty gold" % task["id"])
    return raw


def _answer_symbols(result):
    """Candidates first, then their cross-source impacts: the usable context."""
    seen = [row["symbol"] for row in result["candidates"]]
    seen.extend(entry["symbol"] for entry in result["impacts"]
                if entry["symbol"] not in seen)
    return seen


def _condition_run(condition, question, memory, index, limit):
    if condition == "none":
        return [], []
    if condition == "lexical":
        ranking = index.search_text(question, top_k=limit)
        return [hit["symbol"] for hit in ranking], ranking
    if condition == "tunic":
        result = memory.answer(question, limit=limit, policy="current")
        return _answer_symbols(result), result
    raise ValueError("unknown condition %r" % condition)


def _task_evidence(memory, question, limit):
    result = memory.answer(question, limit=limit, policy="current")
    kinds = ["mention"] if result["mentions"] else []
    kinds.extend(row["relation"] for row in result["claims"])
    return result, sorted(set(kinds))


def run_agent_suite(suite_path, *, limit=5):
    """Replay every task under each condition; return scores and raw rows."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be positive")
    suite = load_agent_suite(suite_path)
    base = Path(suite_path).parent
    conditions = {name: {"passed": 0, "exact": 0, "total": 0,
                         "total_latency_ms": 0.0, "tasks": []}
                  for name in CONDITIONS}
    with tempfile.TemporaryDirectory(prefix="tunic-agent-") as work:
        prepared = {}
        for repo in sorted({task["repo"] for task in suite["tasks"]}):
            root = Path(work) / repo
            shutil.copytree(base / repo, root)
            memory = Memory(Path(work) / (repo + ".sqlite"), root)
            try:
                memory.ingest()
                prepared[repo] = (memory, BM25(root))
            except Exception:
                memory.close()
                raise
        try:
            for task in suite["tasks"]:
                memory, index = prepared[task["repo"]]
                gold = list(task["gold"])
                forbidden = list(task["forbidden"])
                for condition in CONDITIONS:
                    started = time.perf_counter()
                    symbols, payload = _condition_run(condition, task["question"],
                                                      memory, index, limit)
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    record = {"id": task["id"], "gold": gold,
                              "forbidden": forbidden, "returned": symbols,
                              "latency_ms": elapsed_ms,
                              "context_chars": len(json.dumps(payload, sort_keys=True,
                                                              ensure_ascii=True, default=str))}
                    entry = conditions[condition]
                    if condition == "tunic":
                        _, kinds = _task_evidence(memory, task["question"], limit)
                        record["evidence_kinds"] = kinds
                        record["requires"] = list(task["requires"])
                        evidence_ok = set(task["evidence"]) <= set(kinds)
                    else:
                        evidence_ok = True
                    record["pass"] = (set(gold) <= set(symbols)
                                      and not (set(forbidden) & set(symbols))
                                      and evidence_ok)
                    entry["passed"] += record["pass"]
                    entry["exact"] += record["pass"] and set(symbols) == set(gold)
                    entry["total"] += 1
                    entry["total_latency_ms"] += elapsed_ms
                    entry["tasks"].append(record)
        finally:
            for memory, _ in prepared.values():
                memory.close()
    return {"suite": suite["id"], "conditions": conditions}
