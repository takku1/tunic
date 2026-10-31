"""Multisession v3: two-stage acquisition (discover, then propose).

Tests whether splitting memory acquisition into a discovery stage and a
proposal stage elicits writes that one-shot answer+propose does not, on
fresh scenarios. Per-(arm, scenario) isolated repos+DBs; fresh Memory per
session; oracle-as-reviewer gates commits (documented stand-in).

Scenarios (base = repo fixture minus pre-recorded outcome):
  S1-propose: decided_by round_total -> decisions.md (prose decision).
  S2-propose: depends_on place_order -> round_total (synthesis: rounding
    happens at order time; no single source states it; prose alone is
    insufficient -- v2 A2 proved models cannot infer it unaided).
  GATE: add_item (no true edge; nothing may be committed).
Protocols: one-stage (answer+propose together) vs two-stage
(discover symbols first, then propose from the identified set).
Applies run unconditionally: with a commit they test cross-session
retrieval; without one they document prose (in)sufficiency.
Requires Ollama. Writes: benchmarks/agent/runs/model-ab-multisession-v3.json.
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
    CONTEXT_CAP, bundle_lexical_notes, chat, gate_commit, parse_proposal,
    parse_symbols, run_arm, source_manifest)

from tunic.memory import Memory  # noqa: E402

ARMS = ("lexical", "tunic-select")
PROTOCOLS = ("one-stage", "two-stage")
PLACE = "checkout.py::place_order"
ROUND = "pricing.py::round_total"
DISCOUNT = "pricing.py::discount"

PROPOSE2_PROMPT = (
    "You are recording what you learned into shared memory. "
    "You previously identified these symbols as relevant: {symbols}. "
    "Now propose the single key relationship as a JSON list inside a "
    "```tunic-claims fence, e.g. [{{\"rel\": \"depends_on\", \"subject\": \"a.py::f\", "
    "\"object\": \"b.py::g\"}}]. Relations: depends_on, implements, decided_by. "
    "Only propose relationships between the identified symbols, using exact "
    "path::symbol keys. Endpoint keys look like path/to/file.py::FunctionName "
    "for code, or path/to/note.md::document for a whole note file. "
    "If no true relationship holds between them, emit an empty list [].\n\n"
    "Question: %s\n\nKnown context (may be empty or partial):\n%s")

S1 = {"id": "S1", "question": "What decision did we record about round_total?",
      "gold": ("decided_by", ROUND, "notes/decisions.md::document"),
      "apply": {"question": "What did we decide about round_total?",
                "gold": [ROUND], "obsolete": []}}
S2 = {"id": "S2",
      "question": "What must change if round_total changes, and why?",
      "gold": ("depends_on", PLACE, ROUND),
      "apply": {"question": "What else must change if round_total changes?",
                "gold": [PLACE], "obsolete": []}}
GATE = {"id": "GATE", "question": "What must change if add_item changes?"}


def fresh_cell(work, arm, cell):
    import shutil
    dst = work / arm / cell / "repo"
    shutil.copytree(REPO / "benchmarks/agent/repo", dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "notes" / "outcome.md").unlink()
    return dst


def propose_cell(work, arm, protocol, scen):
    """One propose cell: returns (memory paths, proposal outcome row)."""
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    root = fresh_cell(work, arm, scen["id"] + "-" + protocol)
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    code_index, notes_index = BM25(root), TextNotes(root)
    if protocol == "one-stage":
        response, context = run_arm(memory, code_index, notes_index,
                                    arm, scen["question"], propose=True)
    else:
        discover, context = run_arm(memory, code_index, notes_index,
                                    arm, scen["question"], propose=False)
        identified = parse_symbols(discover.get("text", ""))
        prompt = PROPOSE2_PROMPT.format(symbols=", ".join(identified) or "(none)")
        response = chat(prompt % (scen["question"], context))
    claim, error = parse_proposal(response.get("text", ""))
    committed, reason = (False, error or "no proposal")
    if claim is not None:
        committed, reason = gate_commit(memory, claim, scen["gold"],
                                       "notes/proposed.md")
    row = {"id": "%s-propose-%s" % (scen["id"], protocol), "session": 1,
           "arm": arm, "protocol": protocol, "question": scen["question"],
           "gold_triple": list(scen["gold"]),
           "proposal": claim, "proposal_error": error,
           "committed": committed, "commit_reason": reason,
           "pass": committed,
           "context": context[:CONTEXT_CAP],
           "response": (response.get("text", "") or "")[:2000],
           "latency_ms": round(response.get("latency_ms", 0.0), 1),
           "db": str(root.parent / "sessions.sqlite"), "root": str(root)}
    print("%s/%s/%s pass=%s committed=%s (%s)" %
          (row["id"], arm, protocol, committed, committed, reason), flush=True)
    memory.close()
    return row


def apply_row(work, prop_row, scen):
    """Session 2 reads whatever session 1 left (commit or not)."""
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    memory = Memory(prop_row["db"], prop_row["root"])
    code_index, notes_index = BM25(prop_row["root"]), TextNotes(prop_row["root"])
    _, view = memory.store.view()
    known = {s["key"] for s in view["symbols"]}
    response, context = run_arm(memory, code_index, notes_index,
                                prop_row["arm"], scen["apply"]["question"])
    parsed = parse_symbols(response.get("text", ""))
    hallucinated = [s for s in parsed if s not in known]
    stale = [s for s in parsed if s in scen["apply"].get("obsolete", [])]
    passed = (set(scen["apply"]["gold"]) <= set(parsed) and not stale
              and "error" not in response)
    out = {"id": "%s-apply-%s" % (scen["id"], prop_row["protocol"]),
           "session": 2, "arm": prop_row["arm"],
           "protocol": prop_row["protocol"],
           "question": scen["apply"]["question"],
           "gold": scen["apply"]["gold"], "obsolete": [],
           "had_commit": prop_row["committed"],
           "parsed": parsed, "hallucinated": hallucinated, "stale": stale,
           "pass": passed, "context": context[:CONTEXT_CAP],
           "response": (response.get("text", "") or "")[:2000],
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("%s/%s pass=%s committed=%s parsed=%s" %
          (out["id"], prop_row["arm"], passed, prop_row["committed"], parsed),
          flush=True)
    memory.close()
    return out


def gate_row(work, arm):
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    root = fresh_cell(work, arm, "gate")
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    _, view = memory.store.view()
    before = sum(1 for a in view["assertions"]
                 if a.get("status") == "asserted_in_source")
    code_index, notes_index = BM25(root), TextNotes(root)
    response, context = run_arm(memory, code_index, notes_index,
                                arm, GATE["question"], propose=True)
    claim, error = parse_proposal(response.get("text", ""))
    committed = False
    if claim is not None:
        committed, _ = gate_commit(memory, claim, ("", "", ""),
                                   "notes/proposed.md")
    _, view = memory.store.view()
    after = sum(1 for a in view["assertions"]
                if a.get("status") == "asserted_in_source")
    memory.close()
    out = {"id": "GATE", "session": 3, "arm": arm,
           "question": GATE["question"], "proposal": claim,
           "proposal_error": error, "committed": committed,
           "pass": not committed and after == before,
           "context": context[:CONTEXT_CAP],
           "response": (response.get("text", "") or "")[:2000],
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("GATE/%s pass=%s committed=%s" % (arm, out["pass"], committed),
          flush=True)
    return out


def main():
    out_name = sys.argv[1] if len(sys.argv) > 1 else "model-ab-multisession-v3.json"
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-multisession-v3-"))
    rows = []
    for scen in (S1, S2):
        for protocol in PROTOCOLS:
            for arm in ARMS:
                prop = propose_cell(work, arm, protocol, scen)
                rows.append(prop)
                rows.append(apply_row(work, prop, scen))
    for arm in ARMS:
        rows.append(gate_row(work, arm))
    summary = {}
    for arm in ARMS:
        arm_rows = [r for r in rows if r["arm"] == arm]
        summary[arm] = {
            "proposed_committed": sum(1 for r in arm_rows if r.get("committed")),
            "gate_held": all(r["pass"] for r in arm_rows if r["id"] == "GATE"),
            "apply_pass_given_commit": [
                r["pass"] for r in arm_rows
                if r["id"].endswith("-apply") and r.get("had_commit")],
            "hallucinated_tasks": sum(1 for r in arm_rows if r.get("hallucinated")),
            "mean_latency_ms": round(sum(r.get("latency_ms", 0) for r in arm_rows)
                                     / len(arm_rows), 1)}
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": "discover-then-propose vs one-shot; per-cell isolated "
                          "repos+DBs; fresh Memory per session; oracle-as-reviewer "
                          "gates commits; applies run unconditionally",
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "sources": {"tunic": source_manifest(REPO)},
              "summary": summary, "tasks": rows,
              "limitations": ["14-16 model calls; synthetic fixture; directional evidence.",
                              "Oracle-as-reviewer stands in for tests/human review.",
                              "Temperature 0 but GPU sampling may vary; raw responses committed."]}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
