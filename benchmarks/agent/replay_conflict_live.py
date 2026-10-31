"""Live conflict-branch execution with reviewer-accepted conflicting evidence.

Setup (reviewer-accepted, no oracle): a contradicts claim against the S2
edge is recorded and ingested, without asserting the edge itself. The model
then proposes with the A3 candidate block in context (the replay probe shows
it proposes the gold triple reliably). The product commit_proposal path must
refuse with action=conflict, file a frontier entry, and perform zero writes;
a disambiguation call then runs against the blocking evidence. Asserts no
assertion is ever added. Requires Ollama.
Writes: benchmarks/agent/runs/model-ab-conflict-live.json.
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
    CONTEXT_CAP, PROPOSE_PROMPT, chat, parse_proposal, run_arm)
from run_model_ab_multisession_v4 import (  # noqa: E402
    DISAMBIGUATE_PROMPT, S2, fresh_cell)

from tunic.memory import Memory  # noqa: E402

ARMS = ("lexical", "tunic-select")
PLACE = "checkout.py::place_order"
ROUND = "pricing.py::round_total"


def count_assertions(memory):
    _, view = memory.store.view()
    return len(view["assertions"])


def main():
    out_name = sys.argv[1] if len(sys.argv) > 1 else "model-ab-conflict-live.json"
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-conflict-live-"))
    rows = []
    for arm in ARMS:
        from tunic.baselines import BM25, TextNotes  # noqa: E402
        root = fresh_cell(work, arm, "conflict", dispatch=True)
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        memory.ingest()
        memory.record_outcome(ROUND, PLACE, rel="contradicts",
                              document="notes/proposed.md", authority="reviewer")
        memory.ingest()
        before = count_assertions(memory)
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
        committed, action, reason = False, error or "no proposal", None
        disambiguated = None
        if claim is not None:
            committed, action, reason = memory.commit_proposal(
                claim.get("subject"), claim.get("object"),
                rel=claim.get("rel", "depends_on"))
            if action == "conflict":
                dis = chat(DISAMBIGUATE_PROMPT % (claim, reason, S2["question"],
                                                  context))
                disambiguated, error2 = parse_proposal(dis.get("text", ""))
                if disambiguated is None:
                    reason = reason + "; disambiguation abstained (%s)" % (error2 or "")
        after = count_assertions(memory)
        entries = [e for e in memory.frontier_list()
                   if e["failure"] == "reconcile_conflict"]
        passed = (action == "conflict" and not committed and after == before
                  and len(entries) == 1)
        rows.append({"id": "CONFLICT-live", "session": 1, "arm": arm,
                     "question": S2["question"],
                     "proposal": claim, "proposal_error": error,
                     "committed": committed, "reconcile_action": action,
                     "reconcile_reason": reason,
                     "disambiguated_proposal": disambiguated,
                     "frontier_entries": len(entries),
                     "assertions_before": before, "assertions_after": after,
                     "pass": passed, "model_calls": 2,
                     "prompt_tokens": response.get("prompt_tokens") or 0,
                     "completion_tokens": response.get("completion_tokens") or 0,
                     "latency_ms": round(response.get("latency_ms", 0.0), 1),
                     "response": (response.get("text", "") or "")[:2000]})
        print("CONFLICT-live/%s pass=%s action=%s writes=%d" %
              (arm, passed, action, after - before), flush=True)
        memory.close()
    summary = {arm: next(r["pass"] for r in rows if r["arm"] == arm) for arm in ARMS}
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": ("reviewer-accepted contradicts against the S2 edge, no oracle; "
                           "model proposes with A3 candidates; product commit_proposal must "
                           "refuse via conflict, file frontier, write nothing; "
                           "disambiguation runs live"),
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "summary": summary, "tasks": rows,
              "limitations": ["n=1 per arm, temperature 0; directional evidence only.",
                              "Contradicts claim stands in for reviewer-accepted correction."]}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
