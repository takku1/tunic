"""S2 dispatch replay: does the A3 candidate let the model propose the edge?

Builds the frozen v4 S2 cell (registry indirection invisible to static
extraction), records a frontier entry for the previously unsolved failure,
lists mechanical dispatch candidates, and asks the model to verify-or-abstain
with the candidates in context. Commit goes through the same oracle
reviewer gate as v4 (unchanged protocol); the frontier entry resolves only
on a passing apply. A model failure is recorded honestly and the entry
stays open. Requires Ollama. Writes benchmarks/agent/runs/model-ab-s2-dispatch-replay.json.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from run_model_ab_v1 import MODEL, PROMPT  # noqa: E402
from run_model_ab_multisession_v2 import (  # noqa: E402
    CONTEXT_CAP, PROPOSE_PROMPT, bundle_tunic, chat,
    gate_commit, parse_proposal, parse_symbols, run_arm)
from run_model_ab_multisession_v4 import S2, fresh_cell  # noqa: E402

from tunic.memory import Memory  # noqa: E402

ARMS = ("lexical", "tunic-select")
PLACE = "checkout.py::place_order"


def main():
    out_name = sys.argv[1] if len(sys.argv) > 1 else "model-ab-s2-dispatch-replay.json"
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-s2-replay-"))
    rows = []
    for arm in ARMS:
        from tunic.baselines import BM25, TextNotes  # noqa: E402
        root = fresh_cell(work, arm, "replay", dispatch=True)
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        memory.ingest()
        entry_id, _ = memory.frontier_record(
            "unsupported", S2["question"], "static_edge_unresolved",
            required_evidence=("registry assignment", "verified runtime call"))
        memory.frontier_transition(entry_id, "investigating")
        found = memory.candidates()
        dispatch = [c for c in found["candidates"] if c.get("via") == "registry_dispatch"]
        block = "\n".join(
            "- %s %s %s [via %s table %s key %s; rule %s; UNVERIFIED]" % (
                c["caller"], c["relation"], c["callee"], c.get("via"),
                (c.get("derivation") or {}).get("table"),
                (c.get("derivation") or {}).get("key"), c.get("rule"))
            for c in dispatch)
        code_index, notes_index = BM25(root), TextNotes(root)
        _, context = run_arm(memory, code_index, notes_index, arm, S2["question"])
        prompt = PROPOSE_PROMPT % (
            S2["question"],
            context + "\n\nCandidate dispatch edges (mechanically derived, "
            "UNVERIFIED - propose one only if the context supports it):\n"
            + (block or "(none)"))
        response = chat(prompt)
        claim, error = parse_proposal(response.get("text", ""))
        committed, reason = False, error or "no proposal"
        if claim is not None:
            committed, reason = gate_commit(memory, claim, S2["gold"],
                                           "notes/proposed.md")
        prop = {"id": "S2-replay-propose", "session": 1, "arm": arm,
                "question": S2["question"], "gold_triple": list(S2["gold"]),
                "dispatch_candidates": len(dispatch),
                "proposal": claim, "proposal_error": error,
                "committed": committed, "commit_reason": reason,
                "pass": committed,
                "model_calls": 2,
                "prompt_tokens": response.get("prompt_tokens") or 0,
                "completion_tokens": response.get("completion_tokens") or 0,
                "latency_ms": round(response.get("latency_ms", 0.0), 1),
                "response": (response.get("text", "") or "")[:2000]}
        print("S2-replay-propose/%s pass=%s committed=%s (%s)" %
              (arm, committed, committed, reason), flush=True)
        rows.append(prop)
        response2, _ = run_arm(memory, code_index, notes_index,
                               arm, S2["apply"]["question"])
        parsed = parse_symbols(response2.get("text", ""))
        _, view = memory.store.view()
        known = {s["key"] for s in view["symbols"]}
        hallucinated = [s for s in parsed if s not in known]
        passed = (set(S2["apply"]["gold"]) <= set(parsed)
                  and "error" not in response2)
        apply = {"id": "S2-replay-apply", "session": 2, "arm": arm,
                 "question": S2["apply"]["question"],
                 "gold": S2["apply"]["gold"], "had_commit": committed,
                 "parsed": parsed, "hallucinated": hallucinated,
                 "pass": passed and committed, "model_calls": 1,
                 "prompt_tokens": response2.get("prompt_tokens") or 0,
                 "completion_tokens": response2.get("completion_tokens") or 0,
                 "latency_ms": round(response2.get("latency_ms", 0.0), 1),
                 "response": (response2.get("text", "") or "")[:2000]}
        print("S2-replay-apply/%s pass=%s parsed=%s" % (arm, apply["pass"], parsed),
              flush=True)
        rows.append(apply)
        if committed and apply["pass"]:
            memory.frontier_transition(
                entry_id, "resolved",
                resolution="dispatch candidate verified, committed, and applied",
                resolved_by="model-ab-s2-dispatch-replay")
            frontier = "resolved"
        else:
            frontier = "open-investigating"
        rows.append({"id": "S2-replay-frontier", "session": 3, "arm": arm,
                     "frontier_entry": entry_id, "frontier_state": frontier})
        memory.close()
    summary = {arm: {
        "committed": next(r["committed"] for r in rows
                          if r["id"] == "S2-replay-propose" and r["arm"] == arm),
        "apply_pass": next(r["pass"] for r in rows
                           if r["id"] == "S2-replay-apply" and r["arm"] == arm),
        "frontier": next(r["frontier_state"] for r in rows
                         if r["id"] == "S2-replay-frontier" and r["arm"] == arm)}
        for arm in ARMS}
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": ("S2 replay unchanged except the A3 candidate block is "
                           "appended to the propose context; oracle reviewer gate; "
                           "frontier entry resolves only on passing apply"),
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "summary": summary, "tasks": rows,
              "limitations": ["n=1 per arm, temperature 0; directional evidence only."]}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
