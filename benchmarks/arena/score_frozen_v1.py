"""Score frozen competitor raws with the uniform key-mention rule.

System python, no competitor imports. A gold symbol counts as retrieved
iff its exact `path::symbol` key appears in any returned text, or its
file path and its leaf (as a word) appear in the same returned text.
The rule is generous to text systems on common leaves (disclosed); raw
outputs stay committed for rescoring.

Writes: benchmarks/arena/runs/frozen-competitors-v1.json
"""
import json
import re
import sys
from pathlib import Path

REPO = Path("Z:/tunic")


def hit(gold, texts):
    leaf = gold.split("::")[-1].split(".")[-1]
    path = gold.split("::")[0]
    word = re.compile(r"\b" + re.escape(leaf) + r"\b")
    for text in texts:
        if gold in text:
            return True
        if path in text and word.search(text):
            return True
    return False


def score_tasks(tasks, texts_of):
    rows, hits, exact, abstained = [], 0, 0, 0
    for task in tasks:
        texts = texts_of(task)
        covered = [g for g in task["gold"] if hit(g, texts)]
        hits += len(covered)
        exact += task["gold"] and len(covered) == len(task["gold"])
        abstained += not texts
        rows.append({"id": task["id"], "covered": covered,
                     "gold": task["gold"], "abstained": not texts})
    total = sum(len(t["gold"]) for t in tasks)
    return {"tasks": rows, "hits": hits, "gold_total": total,
            "recall": hits / total if total else None,
            "exact_tasks": exact, "abstained_tasks": abstained,
            "n": len(tasks)}


def score_negatives(negatives, texts_of):
    rows = []
    for neg in negatives:
        texts = texts_of(neg)
        rows.append({"question": neg["question"], "abstained": not texts,
                     "n_results": len(texts)})
    return {"tasks": rows, "abstained": sum(r["abstained"] for r in rows),
            "n": len(rows)}


def main():
    cognee = json.loads((REPO / "benchmarks/arena/runs/cognee-frozen-v1.json")
                        .read_text(encoding="utf-8"))
    mem0 = json.loads((REPO / "benchmarks/arena/runs/mem0-frozen-v1.json")
                      .read_text(encoding="utf-8"))

    def cognee_texts(arm):
        def get(task):
            entry = task["arms"][arm]
            if "error" in entry:
                return []
            return [r["text"] for r in entry["results"]]
        return get

    def mem0_texts(task):
        entry = task["answer"]
        if "error" in entry:
            return []
        return [r["memory"] for r in entry["results"]]

    summary = {}
    arms = {}
    for arm in ("CHUNKS", "CHUNKS_LEXICAL", "GRAPH_COMPLETION"):
        arms[arm] = {"suite": score_tasks(cognee["tasks"], cognee_texts(arm)),
                     "negatives": score_negatives(cognee["negatives"], cognee_texts(arm))}
    summary["cognee"] = {"tool_version": cognee["tool_version"],
                         "pinned_stack": cognee["pinned_stack"], "arms": arms}
    summary["mem0"] = {"tool_version": mem0["tool_version"],
                       "pinned_stack": mem0["pinned_stack"],
                       "suite": score_tasks(mem0["tasks"], mem0_texts),
                       "negatives": score_negatives(mem0["negatives"], mem0_texts)}
    record = {"benchmark": "frozen-competitors-v1",
              "protocol": "uniform key-mention rule (exact key, or file path + leaf word "
                          "in the same returned text); generous to text systems on common "
                          "leaves; raw outputs committed for rescoring",
              "summary": summary,
              "limitations": ["text-system precision is not computed: returned units are texts, not symbols.",
                              "frozen rows never rerun: corpus, labels, tool versions, and model digests pinned."]}
    out = REPO / "benchmarks/arena/runs/frozen-competitors-v1.json"
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    for system, arms in summary.items():
        if system == "cognee":
            for arm, parts in arms["arms"].items():
                s, n = parts["suite"], parts["negatives"]
                print("cognee/%s recall=%.3f exact=%d/%d abstained=%d neg-abstained=%d/%d" %
                      (arm, s["recall"], s["exact_tasks"], s["n"], s["abstained_tasks"],
                       n["abstained"], n["n"]))
        else:
            s, n = arms["suite"], arms["negatives"]
            print("mem0 recall=%.3f exact=%d/%d abstained=%d neg-abstained=%d/%d" %
                  (s["recall"], s["exact_tasks"], s["n"], s["abstained_tasks"],
                   n["abstained"], n["n"]))
    print("wrote", out)


main()
