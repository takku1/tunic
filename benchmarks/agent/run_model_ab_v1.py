"""Model-in-the-loop agent A/B v1: same model, same tasks, same budget.

Arms: none (question only), lexical (BM25 top-5 bundles), tunic
(answer() candidates + impacts, policy=current). Every arm gets the
same prompt template, model (qwen3:4b, temperature 0, seed 0), and
context cap. The model lists exact path::symbol keys in a ```symbols
fence; scoring is mechanical. Memory setup is fixture docs (prior
session records); the model under test only reads.

Requires Ollama. Writes: benchmarks/agent/runs/model-ab-v2.json
(v1 artifact frozen; v2 adds the tunic-select arm.)
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tunic.baselines import BM25  # noqa: E402
from tunic.eval import load_agent_suite  # noqa: E402
from tunic.memory import Memory  # noqa: E402

MODEL = "qwen3:4b"
ENDPOINT = "http://localhost:11434/api/chat"
CONTEXT_CAP = 4000
FENCE = re.compile(r"```symbols\s*(.*?)\s*```", re.DOTALL)


def source_manifest(path):
    """Commit + working-tree status for a source checkout.

    A SHA alone does not capture uncommitted or ignored files, so record
    `git status --porcelain` alongside the commit. Non-git sources report
    an error instead of a silent fake SHA.
    """
    manifest = {"path": str(path), "commit": None, "status": None,
                "error": None}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                            text=True, cwd=str(path))
    if commit.returncode != 0:
        manifest["error"] = "not a git checkout"
        return manifest
    manifest["commit"] = commit.stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"],
                            capture_output=True, text=True, cwd=str(path))
    manifest["status"] = status.stdout.strip() or "clean"
    return manifest


def chat(prompt):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "stream": False,
                       "options": {"temperature": 0, "seed": 0}}).encode()
    started = time.perf_counter()
    try:
        reply = urllib.request.urlopen(
            urllib.request.Request(ENDPOINT, data=body,
                                   headers={"Content-Type": "application/json"}),
            timeout=300)
        data = json.loads(reply.read().decode())
    except Exception as error:
        return {"error": "%s: %s" % (type(error).__name__, str(error)[:200]),
                "latency_ms": (time.perf_counter() - started) * 1000.0}
    return {"text": data["message"]["content"],
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
            "latency_ms": (time.perf_counter() - started) * 1000.0}


def parse_symbols(text):
    match = FENCE.search(text or "")
    if not match:
        return []
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def bundle_lexical(index, question, limit=5):
    lines = []
    for hit in index.search_text(question, top_k=limit):
        lines.append("%s\n%s" % (hit["symbol"], hit.get("text", "")[:400]))
    return "\n---\n".join(lines)[:CONTEXT_CAP]


def bundle_tunic(memory, question, limit=5, select=False):
    from tunic.render import render_answer_text  # noqa: E402
    result = memory.answer(question, limit=limit, policy="current", select=select)
    return render_answer_text(result, cap=CONTEXT_CAP)


PROMPT = ("You are a code assistant answering about a small repository. "
          "List ONLY the exact code symbols that answer the question, one per line "
          "inside a ```symbols fence, as relative/path.py::Name. No other text inside the fence.\n\n"
          "Question: %s\n\nKnown context (may be empty or partial):\n%s")


def main():
    suite = load_agent_suite(str(REPO / "benchmarks/agent/suite.json"))
    base = REPO / "benchmarks/agent"
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-model-ab-"))
    prepared = {}
    for repo in sorted({t["repo"] for t in suite["tasks"]}):
        dst = work / repo
        shutil.copytree(base / repo, dst, ignore=shutil.ignore_patterns("__pycache__"))
        memory = Memory(str(work / (repo + ".sqlite")), dst)
        memory.ingest()
        prepared[repo] = (memory, BM25(dst))
    rows = []
    for task in suite["tasks"]:
        memory, index = prepared[task["repo"]]
        _, view = memory.store.view()
        known = {s["key"] for s in view["symbols"]}
        bundles = {"none": "(no context provided)",
                   "lexical": bundle_lexical(index, task["question"]) or "(no context provided)",
                   "tunic": bundle_tunic(memory, task["question"]) or "(no context provided)",
                   "tunic-select": bundle_tunic(memory, task["question"], select=True) or "(no context provided)"}
        row = {"id": task["id"], "repo": task["repo"], "question": task["question"],
               "gold": task["gold"], "forbidden": task.get("forbidden", []), "arms": {}}
        for arm, context in bundles.items():
            response = chat(PROMPT % (task["question"], context))
            parsed = parse_symbols(response.get("text", ""))
            hallucinated = [s for s in parsed if s not in known]
            arm_row = {"context_chars": len(context), "parsed": parsed,
                       "hallucinated": hallucinated,
                       "response": (response.get("text", "") or "")[:2000],
                       "prompt_tokens": response.get("prompt_tokens"),
                       "completion_tokens": response.get("completion_tokens"),
                       "latency_ms": round(response.get("latency_ms", 0.0), 1)}
            if "error" in response:
                arm_row["error"] = response["error"]
                arm_row["pass"] = False
                arm_row["exact"] = False
            else:
                arm_row["pass"] = (set(task["gold"]) <= set(parsed)
                                   and not (set(task.get("forbidden", [])) & set(parsed)))
                arm_row["exact"] = arm_row["pass"] and set(parsed) == set(task["gold"])
            row["arms"][arm] = arm_row
            print("%s/%s pass=%s parsed=%d hallucinated=%d" %
                  (task["id"], arm, arm_row["pass"], len(parsed), len(hallucinated)),
                  flush=True)
        rows.append(row)
    for memory, _ in prepared.values():
        memory.close()
    summary = {}
    for arm in ("none", "lexical", "tunic", "tunic-select"):
        passed = [r for r in rows if r["arms"][arm]["pass"]]
        summary[arm] = {"passed": len(passed), "n": len(rows),
                        "hallucinated_tasks": sum(1 for r in rows
                                                  if r["arms"][arm]["hallucinated"]),
                        "mean_latency_ms": round(sum(r["arms"][arm]["latency_ms"]
                                                     for r in rows) / len(rows), 1)}
    record = {"benchmark": "model-ab-v2",
              "protocol": "same model/prompt/budget; mechanical fence parsing; "
                          "paired arms within one run; reruns may vary (sampled decoding)",
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "summary": summary, "tasks": rows,
              "limitations": ["14 small synthetic tasks; not a general agent claim.",
                              "single-turn answers; the model never writes memory (read-only A/B).",
                              "temperature 0 but GPU sampling may vary across runs; raw responses committed."]}
    out = REPO / "benchmarks/agent/runs/model-ab-v2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
