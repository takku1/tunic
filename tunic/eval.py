"""Agent memory evaluation: no-memory vs lexical vs Tunic graph retrieval.

Held-out by construction: the suite fixtures live outside every other
benchmark, and the comparison replays three retrieval conditions over the
same ingested graph. stdlib only; no embeddings, no network.
"""

import json
import shutil
import tempfile
from pathlib import Path

from .baselines import BM25
from .memory import Memory

CONDITIONS = ("none", "lexical", "tunic")


def load_agent_suite(path):
    """Load and validate an agent held-out suite file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if raw.get("version") != 1:
        raise ValueError("unsupported agent suite version")
    if not raw.get("tasks"):
        raise ValueError("agent suite has no tasks")
    for task in raw["tasks"]:
        for field in ("id", "question", "gold", "requires"):
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


def _condition_symbols(condition, question, root, memory, index, limit):
    if condition == "none":
        return []
    if condition == "lexical":
        return [hit["symbol"] for hit in index.search_text(question, top_k=limit)]
    if condition == "tunic":
        return _answer_symbols(memory.answer(question, limit=limit))
    raise ValueError("unknown condition %r" % condition)


def _task_evidence(memory, question, limit):
    result = memory.answer(question, limit=limit)
    kinds = ["mention"] if result["mentions"] else []
    kinds.extend(row["relation"] for row in result["claims"])
    return result, sorted(set(kinds))


def run_agent_suite(suite_path, *, limit=5):
    """Replay every task under each condition; return scores and raw rows."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be positive")
    suite = load_agent_suite(suite_path)
    source = Path(suite_path).parent / suite.get("repo", "repo")
    with tempfile.TemporaryDirectory(prefix="tunic-agent-") as work:
        root = Path(work) / "repo"
        shutil.copytree(source, root)
        memory = Memory(Path(work) / "agent.sqlite", root)
        try:
            memory.ingest()
            index = BM25(root)
            conditions = {}
            for condition in CONDITIONS:
                passed, exact, rows = 0, 0, []
                for task in suite["tasks"]:
                    symbols = _condition_symbols(condition, task["question"], root,
                                                 memory, index, limit)
                    gold = list(task["gold"])
                    hit = set(gold) <= set(symbols)
                    passed += hit
                    exact += hit and set(symbols) == set(gold)
                    record = {"id": task["id"], "gold": gold,
                              "returned": symbols, "pass": hit}
                    if condition == "tunic":
                        _, kinds = _task_evidence(memory, task["question"], limit)
                        record["evidence_kinds"] = kinds
                        record["requires"] = list(task["requires"])
                    rows.append(record)
                conditions[condition] = {"passed": passed, "exact": exact,
                                         "total": len(suite["tasks"]), "tasks": rows}
        finally:
            memory.close()
    return {"suite": suite["id"], "conditions": conditions}
