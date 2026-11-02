"""Multi-session agent memory benchmark v2: model-proposed writes + history.

Extends v1 (frozen) with the write side of the loop and harder histories.
Same model/prompt/budget, mechanical parsing, paired arms. Each arm runs
on its own repo copy + database (commits must not leak across arms);
sessions are fresh Memory instances sharing that arm's database.

Track A (model-proposed writes; base has NO pre-recorded outcome):
  A1 propose  (s1): propose the rounding decision as a claim. Scored vs gold.
  A2 apply    (s2): retrieve what s1 committed. Commit iff A1 == gold
              (oracle-as-reviewer, documented); A2 analyzed conditionally.
  A3 gate     (s3): question with no true edge; nothing may be committed.
Track B (harness-controlled identical history per arm):
  B0 apply    (s2): read a session-1 record. Gold [place_order].
  B1 support  (s3): same edge recorded twice, first retracted -> alive.
  B2 correct  (s4): second retracted -> dead, abstain.
  B3 supersede(s5): discount supersedes round_total -> answer discount only.
  B4 irrelevant(s5): abstain.
Requires Ollama. Writes: benchmarks/agent/runs/model-ab-multisession-v2.json.
"""
import json
import re
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
from tunic.extract import CLAIM_RELATIONS  # noqa: E402
from tunic.memory import Memory  # noqa: E402

ARMS = ("none", "lexical", "tunic", "tunic-select")
CONTEXT_CAP = 4000
PLACE = "checkout.py::place_order"
ROUND = "pricing.py::round_total"
DISCOUNT = "pricing.py::discount"
CLAIMS_FENCE = re.compile(r"```tunic-claims\s*(.*?)\s*```", re.DOTALL)

PROPOSE_PROMPT = (
    "You are a coding assistant recording what you learned into shared memory. "
    "List ONLY the exact code symbols that answer the question, one per line "
    "inside a ```symbols fence, as relative/path.py::Name. No other text inside the fence. "
    "Then propose the single key relationship as a JSON list inside a "
    "```tunic-claims fence, e.g. [{\"rel\": \"depends_on\", \"subject\": \"a.py::f\", "
    "\"object\": \"b.py::g\"}]. Relations: depends_on, implements, decided_by. "
    "Use exact path::symbol keys you saw in the context; invent nothing. "
    "Endpoint keys look like path/to/file.py::FunctionName for code, or "
    "path/to/note.md::document for a whole note file. "
    "Example decision claim: [{\"rel\": \"decided_by\", "
    "\"subject\": \"pricing.py::round_total\", "
    "\"object\": \"notes/decisions.md::document\"}]. "
    "If the context supports no relationship, emit an empty list [].\n\n"
    "Question: %s\n\nKnown context (may be empty or partial):\n%s")


def bundle_lexical_notes(code_index, notes_index, question, limit=5):
    lines = []
    for hit in code_index.search_text(question, top_k=limit):
        lines.append("%s\n%s" % (hit["symbol"], hit.get("text", "")[:400]))
    for hit in notes_index.search_text(question, top_k=limit):
        lines.append("%s\n%s" % (hit["symbol"], hit.get("text", "")[:400]))
    return "\n---\n".join(lines)[:CONTEXT_CAP]


def run_arm(memory, code_index, notes_index, arm, question, propose=False,
           chat_fn=None):
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
    prompt = PROPOSE_PROMPT if propose else PROMPT
    invoke = chat_fn if chat_fn is not None else chat
    return invoke(prompt % (question, context)), context


def parse_proposal(text):
    match = CLAIMS_FENCE.search(text or "")
    if not match:
        return None, "no tunic-claims fence"
    try:
        claims = json.loads(match.group(1))
    except (ValueError, TypeError):
        return None, "claims fence is not JSON"
    if not isinstance(claims, list) or not claims:
        return None, "claims fence is empty"
    claim = claims[0]
    if not isinstance(claim, dict):
        return None, "claim is not an object"
    if claim.get("rel") not in CLAIM_RELATIONS:
        return None, "unknown relation"
    return claim, None


def gate_commit(memory, claim, gold, document):
    """Oracle-as-reviewer: commit iff the proposal equals the gold triple.

    A production reviewer would be tests or a human; here the harness
    mechanically checks the proposal so later sessions test retrieval of
    model-authored (not harness-authored) memory. Returns (committed, reason).
    """
    for ref in (claim.get("subject"), claim.get("object")):
        if not isinstance(ref, str) or "::" not in ref:
            return False, "endpoints must be exact path::symbol keys"
    _, view = memory.store.view()
    known = {s["key"] for s in view["symbols"]}
    if claim["subject"] not in known or claim["object"] not in known:
        return False, "unknown endpoints"
    triple = (claim["rel"], claim["subject"], claim["object"])
    if triple != gold:
        return False, "reviewer rejected: does not match verified outcome"
    memory.record_outcome(claim["subject"], claim["object"], rel=claim["rel"],
                          document=document, authority="model-proposed")
    memory.ingest()
    return True, "committed"


def score_read(row, memory, code_index, notes_index, known):
    out = {"id": row["id"], "session": row["session"],
           "question": row["question"], "gold": row["gold"],
           "obsolete": row.get("obsolete", []), "arms": {}}
    for arm in ARMS:
        response, context = run_arm(memory, code_index, notes_index,
                                    arm, row["question"])
        parsed = parse_symbols(response.get("text", ""))
        hallucinated = [s for s in parsed if s not in known]
        stale = [s for s in parsed if s in row.get("obsolete", [])]
        if "error" in response:
            passed = False
        else:
            passed = set(row["gold"]) <= set(parsed) and not stale
            if row.get("exact_empty"):
                passed = passed and not parsed
        out["arms"][arm] = {"context_chars": len(context), "parsed": parsed,
                            "hallucinated": hallucinated, "stale": stale,
                            "pass": passed,
                            "context": context[:CONTEXT_CAP],
                            "response": (response.get("text", "") or "")[:2000],
                            "latency_ms": round(response.get("latency_ms", 0.0), 1)}
        if "error" in response:
            out["arms"][arm]["error"] = response["error"]
        print("%s/%s pass=%s parsed=%s stale=%s" %
              (row["id"], arm, passed, parsed, stale), flush=True)
    return out


def fresh_tree(work, arm, track):
    dst = work / arm / track / "repo"
    shutil.copytree(REPO / "benchmarks/agent/repo", dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "notes" / "outcome.md").unlink()
    return dst


def indexes_for(root):
    return BM25(root), TextNotes(root)


def known_keys(memory):
    _, view = memory.store.view()
    return {s["key"] for s in view["symbols"]}


A1_GOLD = ("decided_by", ROUND, "notes/decisions.md::document")
A1_QUESTION = "What decision did we record about round_total?"
A3_QUESTION = "What must change if add_item changes?"


def track_a(work):
    """Model-proposed writes. Returns rows."""
    rows = []
    a1 = {"id": "A1-propose", "session": 1, "question": A1_QUESTION,
          "gold_triple": list(A1_GOLD), "arms": {}}
    a1_contexts = {}
    for arm in ARMS:
        root = fresh_tree(work, arm, "track-a")
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        memory.ingest()
        code_index, notes_index = indexes_for(root)
        response, context = run_arm(memory, code_index, notes_index,
                                    arm, A1_QUESTION, propose=True)
        claim, error = parse_proposal(response.get("text", ""))
        committed, reason = (False, error or "no proposal")
        if claim is not None:
            committed, reason = gate_commit(memory, claim, A1_GOLD,
                                           "notes/proposed.md")
        parsed = parse_symbols(response.get("text", ""))
        a1["arms"][arm] = {"parsed": parsed, "proposal": claim,
                           "proposal_error": error, "committed": committed,
                           "commit_reason": reason,
                           "pass": committed,
                           "context": context[:CONTEXT_CAP],
                           "response": (response.get("text", "") or "")[:2000],
                           "latency_ms": round(response.get("latency_ms", 0.0), 1),
                           "db": str(root.parent / "sessions.sqlite"),
                           "root": str(root)}
        a1_contexts[arm] = (memory, code_index, notes_index)
        print("A1-propose/%s pass=%s committed=%s (%s)" %
              (arm, committed, committed, reason), flush=True)
        if not committed:
            memory.close()
    rows.append(a1)
    a2 = {"id": "A2-apply", "session": 2,
          "question": "What else must change if round_total changes?",
          "gold": [PLACE], "obsolete": [], "arms": {}}
    for arm in ARMS:
        if not a1["arms"][arm]["committed"]:
            a2["arms"][arm] = {"pass": None, "blocked": "A1 proposal not committed",
                               "parsed": [], "hallucinated": [], "stale": []}
            print("A2-apply/%s blocked (A1 knock-on)" % arm, flush=True)
            continue
        memory, code_index, notes_index = a1_contexts[arm]
        memory.close()
        session2 = Memory(a1["arms"][arm]["db"], a1["arms"][arm]["root"])
        row = score_read(a2, session2, code_index, notes_index,
                         known_keys(session2))
        a2["arms"][arm] = row["arms"][arm]
        session2.close()
    rows.append(a2)
    a3 = {"id": "A3-gate", "session": 3, "question": A3_QUESTION,
          "gold_triple": None, "arms": {}}
    for arm in ARMS:
        root = Path(a1["arms"][arm].get("root") or fresh_tree(work, arm, "track-a3"))
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        _, view = memory.store.view()
        before = sum(1 for a in view["assertions"]
                     if a.get("status") == "asserted_in_source")
        code_index, notes_index = indexes_for(root)
        response, context = run_arm(memory, code_index, notes_index,
                                    arm, A3_QUESTION, propose=True)
        claim, error = parse_proposal(response.get("text", ""))
        committed = False
        if claim is not None:
            committed, _ = gate_commit(memory, claim, ("", "", ""), "notes/proposed.md")
        _, view = memory.store.view()
        after = sum(1 for a in view["assertions"]
                    if a.get("status") == "asserted_in_source")
        memory.close()
        a3["arms"][arm] = {"parsed": parse_symbols(response.get("text", "")),
                           "proposal": claim, "proposal_error": error,
                           "committed": committed,
                           "assertions_before": before, "assertions_after": after,
                           "pass": not committed and after == before,
                           "context": context[:CONTEXT_CAP],
                           "response": (response.get("text", "") or "")[:2000],
                           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
        print("A3-gate/%s pass=%s committed=%s" % (arm, not committed, committed),
              flush=True)
    rows.append(a3)
    for arm in ARMS:
        if arm in a1_contexts:
            a1_contexts[arm][0].close()
    return rows


def track_b(work):
    """Harness-controlled history, identical per arm."""
    rows = []
    handles = {}
    for arm in ARMS:
        root = fresh_tree(work, arm, "track-b")
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        memory.ingest()
        memory.record_outcome(PLACE, ROUND, rel="depends_on",
                              document="notes/outcome.md",
                              authority="session-1-verified")
        memory.ingest()
        memory.close()
        handles[arm] = root
    b0 = {"id": "B0-apply", "session": 2,
          "question": "What else must change if round_total changes?",
          "gold": [PLACE], "obsolete": []}
    rows.append(run_track_b_row(work, handles, b0, "B0"))
    for arm in ARMS:
        root = handles[arm]
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        memory.record_outcome(PLACE, ROUND, rel="depends_on",
                              document="notes/outcome2.md",
                              authority="session-2-independent")
        memory.record_outcome(PLACE, ROUND, rel="retracts", retract="depends_on",
                              document="notes/correction1.md",
                              note="first record withdrawn")
        memory.ingest()
        memory.close()
    b1 = {"id": "B1-support", "session": 3,
          "question": "What else must change if round_total changes?",
          "gold": [PLACE], "obsolete": []}
    rows.append(run_track_b_row(work, handles, b1, "B1"))
    for arm in ARMS:
        root = handles[arm]
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        memory.record_outcome(PLACE, ROUND, rel="retracts", retract="depends_on",
                              document="notes/correction2.md",
                              note="second record withdrawn")
        memory.ingest()
        memory.close()
    b2 = {"id": "B2-correct", "session": 4,
          "question": "What else must change if round_total changes?",
          "gold": [], "obsolete": [PLACE], "exact_empty": True}
    rows.append(run_track_b_row(work, handles, b2, "B2"))
    for arm in ARMS:
        root = handles[arm]
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        memory.record_outcome(ROUND, "notes/decisions.md::document",
                              rel="decided_by", document="notes/d1.md",
                              note="round down at totals")
        memory.record_outcome(DISCOUNT, "notes/decision2.md::document",
                              rel="decided_by", document="notes/d2.md",
                              note="rounding moved into discount")
        memory.record_outcome(DISCOUNT, ROUND, rel="supersedes",
                              document="notes/d2.md",
                              note="discount rounding replaces round_total")
        memory.ingest()
        memory.close()
    b3 = {"id": "B3-supersede", "session": 5,
          "question": "What did we decide about round_total?",
          "gold": [DISCOUNT], "obsolete": [ROUND]}
    rows.append(run_track_b_row(work, handles, b3, "B3"))
    b4 = {"id": "B4-irrelevant", "session": 5,
          "question": "Which functions break if route changes?",
          "gold": [], "obsolete": [], "exact_empty": True}
    rows.append(run_track_b_row(work, handles, b4, "B4"))
    return rows


def run_track_b_row(work, handles, spec, label):
    out = {"id": spec["id"], "session": spec["session"],
           "question": spec["question"], "gold": spec["gold"],
           "obsolete": spec.get("obsolete", []), "arms": {}}
    for arm in ARMS:
        root = handles[arm]
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        code_index, notes_index = indexes_for(root)
        response, context = run_arm(memory, code_index, notes_index,
                                    arm, spec["question"])
        parsed = parse_symbols(response.get("text", ""))
        hallucinated = [s for s in parsed if s not in known_keys(memory)]
        stale = [s for s in parsed if s in spec.get("obsolete", [])]
        if "error" in response:
            passed = False
        else:
            passed = set(spec["gold"]) <= set(parsed) and not stale
            if spec.get("exact_empty"):
                passed = passed and not parsed
        out["arms"][arm] = {"context_chars": len(context), "parsed": parsed,
                            "hallucinated": hallucinated, "stale": stale,
                            "pass": passed, "context": context[:CONTEXT_CAP],
                            "response": (response.get("text", "") or "")[:2000],
                            "latency_ms": round(response.get("latency_ms", 0.0), 1)}
        if "error" in response:
            out["arms"][arm]["error"] = response["error"]
        memory.close()
        print("%s/%s pass=%s parsed=%s stale=%s" %
              (spec["id"], arm, passed, parsed, stale), flush=True)
    return out


def main():
    out_name = sys.argv[1] if len(sys.argv) > 1 else "model-ab-multisession-v2.json"
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-multisession-v2-"))
    rows = track_a(work) + track_b(work)
    summary = {}
    for arm in ARMS:
        scored = [r for r in rows if r["arms"][arm].get("pass") is not None]
        summary[arm] = {"passed": sum(1 for r in scored if r["arms"][arm]["pass"]),
                        "n": len(scored),
                        "blocked": sum(1 for r in rows
                                       if r["arms"][arm].get("pass") is None),
                        "stale_tasks": sum(1 for r in scored
                                           if r["arms"][arm].get("stale")),
                        "hallucinated_tasks": sum(1 for r in scored
                                                 if r["arms"][arm].get("hallucinated")),
                        "mean_latency_ms": round(sum(r["arms"][arm].get("latency_ms", 0)
                                                     for r in scored) / len(scored), 1)
                        if scored else None}
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": "same model/prompt/budget; mechanical parsing; paired arms; "
                          "fresh Memory per session; per-arm isolated repos+DBs; "
                          "oracle-as-reviewer gates model-proposed commits; "
                          "A2 analyzed conditionally on A1",
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "sources": {"tunic": source_manifest(REPO)},
              "summary": summary, "tasks": rows,
              "limitations": ["8 synthetic rows on one fixture; directional evidence.",
                              "Oracle-as-reviewer stands in for tests/human review.",
                              "Temperature 0 but GPU sampling may vary; raw responses committed."]}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
