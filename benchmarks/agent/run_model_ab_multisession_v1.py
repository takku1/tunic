"""Multi-session agent memory benchmark v1: record -> apply -> correct.

Claim: Tunic's temporal memory changes agent behavior correctly across
sessions, versus a lexical-notes baseline. Same model/prompt/budget,
mechanical fence parsing, paired arms.

Sessions are separate Memory instances over one database file (what one
session records, another retrieves); the harness performs the writes an
agent would (recording verified outcomes, ingesting corrections) and the
model under test only reads -- acquisition writing is unit-tested in
tests/test_memory.py, not scored here.

Phases on the `repo` fixture (prior session already recorded a verified
outcome: checkout.py::place_order depends_on pricing.py::round_total):
  MS-apply   (session 2, new instance): use the prior record. Gold [place_order].
  MS-decide  (session 2): retrieve the prior rounding decision. Gold [round_total].
  MS-correct (session 3, after a retracts claim is ingested): the old edge is
             obsolete. Gold [] (exact abstention); obsolete [place_order].
  MS-irrelevant (session 3): unknown symbol. Gold [] (exact abstention).

Arms: none, lexical (BM25 over code+notes), tunic, tunic-select.
Pass: gold ⊆ parsed. For MS-correct/MS-irrelevant additionally require
parsed == [] (no stale reliance, no confabulation); obsolete violations
are reported separately.
Requires Ollama. Writes: benchmarks/agent/runs/model-ab-multisession-v1.json.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from run_model_ab_v1 import (MODEL, PROMPT, bundle_tunic,  # noqa: E402
                             chat, parse_symbols, source_manifest)

from tunic.baselines import BM25, TextNotes  # noqa: E402
from tunic.memory import Memory  # noqa: E402

CONTEXT_CAP = 4000


def bundle_lexical_notes(code_index, notes_index, question, limit=5):
    """Lexical memory over code AND prose notes: the honest BM25 baseline.

    Code-only BM25 cannot retrieve session records at all, so it cannot
    be fooled by stale ones either. This arm indexes both, like a real
    lexical agent memory would.
    """
    lines = []
    for hit in code_index.search_text(question, top_k=limit):
        lines.append("%s\n%s" % (hit["symbol"], hit.get("text", "")[:400]))
    for hit in notes_index.search_text(question, top_k=limit):
        lines.append("%s\n%s" % (hit["symbol"], hit.get("text", "")[:400]))
    return "\n---\n".join(lines)[:CONTEXT_CAP]

ARMS = ("none", "lexical", "tunic", "tunic-select")
PLACE = "checkout.py::place_order"
ROUND = "pricing.py::round_total"

ROWS = [
    {"id": "MS-apply", "session": 2,
     "question": "What else must change if round_total changes?",
     "gold": [PLACE], "obsolete": [], "exact_empty": False},
    {"id": "MS-decide", "session": 2,
     "question": "What did we decide about round_total?",
     "gold": [ROUND], "obsolete": [], "exact_empty": False},
    {"id": "MS-correct", "session": 3,
     "question": "What else must change if round_total changes?",
     "gold": [], "obsolete": [PLACE], "exact_empty": True},
    {"id": "MS-irrelevant", "session": 3,
     "question": "Which functions break if route changes?",
     "gold": [], "obsolete": [], "exact_empty": True},
]


def fresh_memory(work, db_name="sessions.sqlite"):
    return Memory(str(work / db_name), work / "repo")


def run_arm(memory, code_index, notes_index, arm, question):
    if arm == "none":
        context = "(no context provided)"
    elif arm == "lexical":
        context = (bundle_lexical_notes(code_index, notes_index, question)
                   or "(no context provided)")
    elif arm == "tunic":
        context = bundle_tunic(memory, question) or "(no context provided)"
    else:
        context = (bundle_tunic(memory, question, select=True)
                   or "(no context provided)")
    return chat(PROMPT % (question, context)), context


def score_row(row, memory, code_index, notes_index, known):
    out = {"id": row["id"], "session": row["session"],
           "question": row["question"], "gold": row["gold"],
           "obsolete": row["obsolete"], "arms": {}}
    for arm in ARMS:
        response, context = run_arm(memory, code_index, notes_index,
                                    arm, row["question"])
        parsed = parse_symbols(response.get("text", ""))
        hallucinated = [s for s in parsed if s not in known]
        stale = [s for s in parsed if s in row["obsolete"]]
        if "error" in response:
            passed, exact = False, False
        else:
            passed = set(row["gold"]) <= set(parsed) and not stale
            exact = passed and (set(parsed) == set(row["gold"])
                                if not row["exact_empty"] else not parsed)
            if row["exact_empty"]:
                passed = passed and not parsed
        out["arms"][arm] = {"context_chars": len(context), "parsed": parsed,
                            "context": context[:CONTEXT_CAP],
                            "hallucinated": hallucinated,
                            "stale": stale, "pass": passed, "exact": exact,
                            "response": (response.get("text", "") or "")[:2000],
                            "prompt_tokens": response.get("prompt_tokens"),
                            "completion_tokens": response.get("completion_tokens"),
                            "latency_ms": round(response.get("latency_ms", 0.0), 1)}
        if "error" in response:
            out["arms"][arm]["error"] = response["error"]
        print("%s/%s pass=%s parsed=%s stale=%s" %
              (row["id"], arm, passed, parsed, stale), flush=True)
    return out


def main():
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-multisession-"))
    shutil.copytree(REPO / "benchmarks/agent/repo", work / "repo",
                    ignore=shutil.ignore_patterns("__pycache__"))
    code_index = BM25(work / "repo")
    notes_index = TextNotes(work / "repo")

    session2 = fresh_memory(work)
    session2.ingest()
    _, view = session2.store.view()
    known = {s["key"] for s in view["symbols"]}
    rows = [score_row(ROWS[0], session2, code_index, notes_index, known),
            score_row(ROWS[1], session2, code_index, notes_index, known)]
    session2.close()

    # Session 2's agent work ends; new evidence retracts the old edge.
    writer = fresh_memory(work)
    writer.record_outcome(PLACE, ROUND, rel="retracts", retract="depends_on",
                          document="notes/correction.md",
                          note="rounding moved into place_order; round_total unused")
    writer.ingest()
    writer.close()

    # Session 3: a new agent that never saw the original session.
    session3 = fresh_memory(work)
    _, view3 = session3.store.view()
    known3 = {s["key"] for s in view3["symbols"]}
    # Rebuild the lexical indexes: session 2's correction added notes that
    # a real lexical memory would now retrieve, stale record included.
    code_index = BM25(work / "repo")
    notes_index = TextNotes(work / "repo")
    rows.append(score_row(ROWS[2], session3, code_index, notes_index, known3))
    rows.append(score_row(ROWS[3], session3, code_index, notes_index, known3))
    session3.close()

    summary = {}
    for arm in ARMS:
        summary[arm] = {"passed": sum(1 for r in rows if r["arms"][arm]["pass"]),
                        "n": len(rows),
                        "stale_tasks": sum(1 for r in rows
                                           if r["arms"][arm]["stale"]),
                        "hallucinated_tasks": sum(1 for r in rows
                                                 if r["arms"][arm]["hallucinated"]),
                        "mean_latency_ms": round(sum(r["arms"][arm]["latency_ms"]
                                                     for r in rows) / len(rows), 1)}
    record = {"benchmark": "model-ab-multisession-v1",
              "protocol": "same model/prompt/budget; mechanical fence parsing; "
                          "paired arms; fresh Memory per session over one DB; "
                          "harness performs agent-side writes, model only reads",
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": 4000, "tunic_commit": tunic_commit,
              "sources": {"tunic": source_manifest(REPO)},
              "summary": summary, "tasks": rows,
              "limitations": ["4 synthetic rows on one fixture; directional evidence.",
                              "Agent-side recording is harness-simulated, not model-emitted.",
                              "Temperature 0 but GPU sampling may vary; raw responses committed."]}
    out = REPO / "benchmarks/agent/runs/model-ab-multisession-v1.json"
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
