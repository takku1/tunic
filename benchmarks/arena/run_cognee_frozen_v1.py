"""Frozen Cognee baseline v1. Runs under the isolated cognee venv.

Corpus: the 3 agent-suite fixture repos as datasets repo/evolve/library.
Retrieval-only query types (no LLM completion): CHUNKS, CHUNKS_LEXICAL,
GRAPH_COMPLETION with only_context=True. Records raw results; scoring
happens in score_frozen_v1.py (system python, no competitor imports).

Interpreter: /tmp/competitors/cognee-env/Scripts/python
Writes: benchmarks/arena/runs/cognee-frozen-v1.json
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ["LLM_PROVIDER"] = "ollama"
os.environ["LLM_MODEL"] = "qwen3:4b"
os.environ["LLM_ENDPOINT"] = "http://localhost:11434"
os.environ["LLM_API_KEY"] = "local-only-dummy-key"
os.environ["EMBEDDING_PROVIDER"] = "ollama"
os.environ["EMBEDDING_MODEL"] = "nomic-embed-text"
os.environ["EMBEDDING_DIMENSIONS"] = "768"
os.environ["EMBEDDING_ENDPOINT"] = "http://localhost:11434/api/embed"
os.environ["COGNEE_DIR"] = "/tmp/competitors/cognee-frozen"

import cognee  # noqa: E402
from cognee.api.v1.search import SearchType  # noqa: E402

REPO = Path("Z:/tunic")
SUITE = json.loads((REPO / "benchmarks/agent/suite.json").read_text(encoding="utf-8"))
NEGATIVES = ["quantum banana firmware requirements", "how do I bake sourdough bread",
             "explain the offside rule in soccer", "what is the capital of Assyria",
             "summarize yesterday's standup notes", "translate hello to Klingon"]
ARMS = ["CHUNKS", "CHUNKS_LEXICAL", "GRAPH_COMPLETION"]


def entry_dict(entry):
    if isinstance(entry, dict):
        text = entry.get("search_result", entry.get("text", ""))
        return {"kind": entry.get("kind", entry.get("search_type")),
                "score": entry.get("score"),
                "text": str(text or "")}
    return {"kind": getattr(entry, "kind", None),
            "score": getattr(entry, "score", None),
            "text": str(getattr(entry, "text", ""))}


async def main():
    work = Path("/tmp/competitors/cognee-frozen")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    await cognee.prune.prune_system(metadata=True, graph=True, vector=True)
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    for repo in ("repo", "evolve", "library"):
        await cognee.add(str(REPO / "benchmarks/agent" / repo), dataset_name=repo)
    await cognee.cognify()

    async def ask(question, dataset, arm, top_k=5):
        try:
            results = await cognee.search(
                question, query_type=getattr(SearchType, arm),
                datasets=[dataset], top_k=top_k, only_context=True)
            return {"results": [entry_dict(e) for e in results]}
        except Exception as error:
            return {"error": "%s: %s" % (type(error).__name__, str(error)[:200])}

    tasks = []
    for task in SUITE["tasks"]:
        row = {"id": task["id"], "repo": task["repo"], "question": task["question"],
               "gold": task["gold"], "arms": {}}
        for arm in ARMS:
            row["arms"][arm] = await ask(task["question"], task["repo"], arm)
        tasks.append(row)
    negatives = []
    for query in NEGATIVES:
        row = {"question": query, "arms": {}}
        for arm in ARMS:
            row["arms"][arm] = await ask(query, "library", arm)
        negatives.append(row)
    record = {
        "benchmark": "frozen-competitors-v1", "tool": "cognee",
        "tool_version": "1.6.6", "tunic_commit": tunic_commit,
        "pinned_stack": {"llm": "ollama/qwen3:4b", "embeddings": "ollama/nomic-embed-text",
                         "vector_db": "lancedb(default)", "graph": "ladybug(default)",
                         "extraction": "default LLM pipeline"},
        "protocol": "ingest 3 fixture repos; retrieval-only recall (no LLM completion); "
                    "raw results only, scored by score_frozen_v1.py",
        "tasks": tasks, "negatives": negatives,
        "limitations": ["fact extraction uses a 4b local LLM; extraction misses are a tool result, not a protocol failure.",
                        "returned units are texts, not symbols; scored by key-mention rule (see scorer)."]}
    out = REPO / "benchmarks/arena/runs/cognee-frozen-v1.json"
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("wrote", out, flush=True)


asyncio.run(main())
