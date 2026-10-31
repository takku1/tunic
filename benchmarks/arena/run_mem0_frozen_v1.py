"""Frozen Mem0 baseline v1. Runs under the isolated mem0 venv.

Corpus: every .py/.md/.txt file of the 3 fixture repos stored as one
memory each (infer=False: no LLM extraction, deterministic retrieval).
Search filters by repo user_id. Records raw results; scoring happens in
score_frozen_v1.py (system python, no competitor imports).

Note: Mem0 2.2.1 + local qwen3:4b fact extraction (infer=True) yields
zero memories on this stack, so infer=False is the runnable protocol.
The extraction failure is recorded in the run artifact.

Interpreter: /tmp/competitors/mem0-env/Scripts/python
Writes: benchmarks/arena/runs/mem0-frozen-v1.json
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from mem0 import Memory  # noqa: E402

REPO = Path("Z:/tunic")
SUITE = json.loads((REPO / "benchmarks/agent/suite.json").read_text(encoding="utf-8"))
NEGATIVES = ["quantum banana firmware requirements", "how do I bake sourdough bread",
             "explain the offside rule in soccer", "what is the capital of Assyria",
             "summarize yesterday's standup notes", "translate hello to Klingon"]
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".rst"}


def build_memory(path):
    config = {
        "llm": {"provider": "ollama",
                "config": {"model": "qwen3:4b",
                           "ollama_base_url": "http://localhost:11434"}},
        "embedder": {"provider": "ollama",
                     "config": {"model": "nomic-embed-text",
                                "ollama_base_url": "http://localhost:11434",
                                "embedding_dims": 768}},
        "vector_store": {"provider": "faiss",
                         "config": {"collection_name": "frozen",
                                    "embedding_model_dims": 768,
                                    "path": str(path)}},
    }
    return Memory.from_config(config)


def main():
    work = Path("/tmp/competitors/mem0-frozen")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    memory = build_memory(work)
    counts = {}
    for repo in ("repo", "evolve", "library"):
        root = REPO / "benchmarks/agent" / repo
        count = 0
        for path in sorted(root.rglob("*")):
            if path.suffix not in TEXT_EXTENSIONS or path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            try:
                text = path.read_bytes().decode("utf-8-sig")
            except (ValueError, UnicodeError):
                continue
            memory.add("File: %s\n%s" % (rel, text),
                       user_id="arena-" + repo, metadata={"file": rel},
                       infer=False)
            count += 1
        counts[repo] = count

    def ask(question, repo, limit=5):
        try:
            results = memory.search(question, filters={"user_id": "arena-" + repo},
                                    limit=limit)
            items = results.get("results", []) if isinstance(results, dict) else results
            return {"results": [{"memory": str(it.get("memory", it))[:2000],
                                 "score": it.get("score")} for it in items]}
        except Exception as error:
            return {"error": "%s: %s" % (type(error).__name__, str(error)[:200])}

    tasks = []
    for task in SUITE["tasks"]:
        tasks.append({"id": task["id"], "repo": task["repo"],
                      "question": task["question"], "gold": task["gold"],
                      "answer": ask(task["question"], task["repo"])})
    negatives = [{"question": q, "answer": ask(q, "library")} for q in NEGATIVES]
    record = {
        "benchmark": "frozen-competitors-v1", "tool": "mem0",
        "tool_version": "2.2.1", "tunic_commit": tunic_commit,
        "pinned_stack": {"llm": "ollama/qwen3:4b (extraction only; infer=False here)",
                         "embeddings": "ollama/nomic-embed-text",
                         "vector_store": "faiss(local, 768 dims)"},
        "protocol": "one memory per repo file (infer=False); search filtered by repo; "
                    "raw results only, scored by score_frozen_v1.py",
        "ingest_counts": counts,
        "extraction_note": "infer=True (LLM fact extraction with qwen3:4b) stores zero "
                           "memories on this stack; retrieval benchmark uses infer=False.",
        "tasks": tasks, "negatives": negatives,
        "limitations": ["fact extraction with the pinned 4b model fails; only retrieval of stored file texts is measured.",
                        "returned units are texts, not symbols; scored by key-mention rule (see scorer)."]}
    out = REPO / "benchmarks/arena/runs/mem0-frozen-v1.json"
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("wrote", out, flush=True)


main()
