"""Real-repo model A/B v1: PyQuifer impact held-out through a model.

Same protocol as run_model_ab_v1 (same model/prompt/budget, mechanical
fence parsing, paired arms): none, lexical (BM25 top-5), tunic
(trust-labeled answer(), policy=current), tunic-select (select=True).
20 held-out tasks from benchmarks/arena/pyquifer-impact-v1.json; retrieval
never tuned against them. Requires Ollama.
Writes: benchmarks/agent/runs/model-ab-pyq-v1.json.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from run_model_ab_v1 import (MODEL, PROMPT, bundle_lexical, bundle_tunic,  # noqa: E402
                             chat, parse_symbols, source_manifest)

from tunic.baselines import BM25  # noqa: E402
from tunic.memory import Memory  # noqa: E402

ARMS = ("none", "lexical", "tunic", "tunic-select")
PYQ = os.environ.get("TUNIC_PYQ_ROOT", "Z:/PyQuifer")


def main():
    out_name = sys.argv[1] if len(sys.argv) > 1 else "model-ab-pyq-v1.json"
    heldout = json.loads((REPO / "benchmarks/arena/pyquifer-impact-v1.json")
                         .read_text(encoding="utf-8"))
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    manifests = {"tunic": source_manifest(REPO), "pyquifer": source_manifest(PYQ)}
    work = Path(tempfile.mkdtemp(prefix="tunic-model-ab-pyq-"))
    dst = work / "pyquifer"
    shutil.copytree(PYQ, dst, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    memory = Memory(str(work / "pyquifer.sqlite"), dst)
    memory.ingest()
    index = BM25(dst)
    _, view = memory.store.view()
    known = {s["key"] for s in view["symbols"]}
    rows = []
    for task in heldout["tasks"]:
        bundles = {"none": "(no context provided)",
                   "lexical": bundle_lexical(index, task["question"]) or "(no context provided)",
                   "tunic": bundle_tunic(memory, task["question"]) or "(no context provided)",
                   "tunic-select": bundle_tunic(memory, task["question"], select=True) or "(no context provided)"}
        row = {"id": task["id"], "question": task["question"],
               "gold": task["gold"], "callee": task.get("callee"), "arms": {}}
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
                arm_row["pass"] = False
                arm_row["exact"] = False
                arm_row["error"] = response["error"]
            else:
                arm_row["pass"] = set(task["gold"]) <= set(parsed)
                arm_row["exact"] = arm_row["pass"] and set(parsed) == set(task["gold"])
            row["arms"][arm] = arm_row
            print("%s/%s pass=%s parsed=%d hallucinated=%d" %
                  (task["id"], arm, arm_row["pass"], len(parsed), len(hallucinated)),
                  flush=True)
        rows.append(row)
    memory.close()
    summary = {}
    for arm in ARMS:
        passed = [r for r in rows if r["arms"][arm]["pass"]]
        summary[arm] = {"passed": len(passed), "n": len(rows),
                        "exact": sum(1 for r in rows if r["arms"][arm]["exact"]),
                        "hallucinated_tasks": sum(1 for r in rows
                                                 if r["arms"][arm]["hallucinated"]),
                        "mean_latency_ms": round(sum(r["arms"][arm]["latency_ms"]
                                                     for r in rows) / len(rows), 1)}
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": "same model/prompt/budget; mechanical fence parsing; "
                          "paired arms within one run; trust-labeled tunic context; "
                          "held-out retrieval set, never tuned against",
              "suite_digest": heldout.get("split"),
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": 4000, "tunic_commit": tunic_commit,
              "sources": manifests,
              "summary": summary, "tasks": rows,
              "limitations": ["20 real-repo tasks but still single-hop impact phrasing.",
                              "single-turn answers; the model never writes memory.",
                              "temperature 0 but GPU sampling may vary; raw responses committed."]}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
