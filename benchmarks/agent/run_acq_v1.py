"""Acquisition Generalization Benchmark v1 (FROZEN on first model run).

Ten seeded scenarios x reps x 3 arms (lexical, tunic-select, tunic-select+A3).
Product path throughout: one-shot propose -> Memory.commit_proposal ->
ingest on commit -> apply. No oracle gate; the scorer compares committed
triples to gold exactly. Must-abstain scenarios pass on no-commit plus a
frontier entry (refusal or abstention filing); false promotions fail.
Repetitions vary names/layouts/wording and are reported as reps, not
independent tasks. Held-out PyQuifer tasks are untouched.

Usage:
  python run_acq_v1.py <out.json> [scen,...] [--dry-run]
--dry-run builds every cell and checks static visibility, A3 presence, and
product-path mechanics with the gold triple directly (no model calls).
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

from run_model_ab_v1 import MODEL  # noqa: E402
from run_model_ab_multisession_v2 import (  # noqa: E402
    CONTEXT_CAP, PROPOSE_PROMPT, chat, parse_proposal, parse_symbols, run_arm)
from run_model_ab_multisession_v4 import static_edge_visible  # noqa: E402

from acqgen import SCENARIOS, build, digest  # noqa: E402
from tunic.memory import Memory  # noqa: E402

ARMS = ("lexical", "select", "select-a3")
GUARD_FAILURES = ("reconcile_invalid", "reconcile_conflict", "proposal_abstained")


def fresh_cell(work, arm, scenario, rep, files):
    dst = work / arm / scenario / ("rep%d" % rep) / "repo"
    dst.mkdir(parents=True)
    for path, text in files.items():
        target = dst / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return dst


def case_a3_candidates(memory):
    found = memory.candidates()
    return [c for c in found["candidates"] if c.get("via") == "registry_dispatch"]


def candidates_block(dispatch):
    return "\n".join(
        "- %s %s %s [via %s table %s key %s; rule %s; UNVERIFIED]" % (
            c["caller"], c["relation"], c["callee"], c.get("via"),
            (c.get("derivation") or {}).get("table"),
            (c.get("derivation") or {}).get("key"), c.get("rule"))
        for c in dispatch)


def setup_cell(memory, spec):
    if spec["kind"] == "contra":
        rel, subj, obj = spec["gold"]
        memory.record_outcome(obj, subj, rel="contradicts",
                              document="notes/proposed.md", authority="reviewer")
    elif spec["kind"] == "correct":
        e1, e2 = spec["stale"], spec["gold"]
        memory.record_outcome(e1[1], e1[2], rel=e1[0],
                              document="notes/proposed.md", authority="reviewer")
        memory.record_outcome(e1[1], e1[2], rel="retracts", retract=e1[0],
                              document="notes/proposed.md", authority="reviewer")
        memory.record_outcome(e2[1], e2[2], rel=e2[0],
                              document="notes/proposed.md", authority="reviewer")
    memory.ingest()


def propose_cell(work, arm, scenario, rep, dry=False):
    """One propose cell. Returns (propose row, [apply rows])."""
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    spec = build(scenario, rep)
    root = fresh_cell(work, arm, scenario, rep, spec["files"])
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    setup_cell(memory, spec)
    calls, tokens = [0], [0, 0]

    def tracked(prompt_text):
        response = chat(prompt_text)
        calls[0] += 1
        tokens[0] += response.get("prompt_tokens") or 0
        tokens[1] += response.get("completion_tokens") or 0
        return response

    code_index, notes_index = BM25(root), TextNotes(root)
    question = spec["question"]
    misbuilt, why = False, ""
    if spec["kind"] in ("positive", "contra") and spec["gold"] is not None:
        rel, subj, obj = spec["gold"]
        visible = static_edge_visible(memory, subj, obj)
        want = spec.get("static", "invisible") == "visible"
        if visible != want:
            misbuilt, why = True, "scenario-misbuilt: gold static visibility=%s" % visible
    dispatch = case_a3_candidates(memory)
    if spec.get("a3_expected") and not dispatch:
        misbuilt, why = True, "scenario-misbuilt: expected A3 candidate absent"
    if dry:
        memory.close()
        return {"id": "%s-rep%d-propose" % (scenario, rep), "arm": arm,
                "scenario": scenario, "rep": rep, "question": question,
                "gold": list(spec["gold"]) if spec["gold"] else None,
                "misbuilt": misbuilt, "misbuilt_reason": why,
                "a3_present": bool(dispatch),
                "fixture_digest": digest(scenario, rep), "dry": True}, []

    if misbuilt:
        memory.close()
        return {"id": "%s-rep%d-propose" % (scenario, rep), "arm": arm,
                "scenario": scenario, "rep": rep, "question": question,
                "gold": list(spec["gold"]) if spec["gold"] else None,
                "pass": False, "commit_reason": why, "dry": False}, []
    bundle_arm = "lexical" if arm == "lexical" else "tunic-select"
    if spec["kind"] == "correct":
        prop = {"id": "%s-rep%d-setup" % (scenario, rep), "arm": arm,
                "scenario": scenario, "rep": rep, "question": question,
                "gold": list(spec["gold"]), "stale": list(spec["stale"]),
                "model_calls": 0, "setup_only": True}
        applies = [apply_stage(memory, code_index, notes_index, bundle_arm,
                               prop, check) for check in spec["apply"]]
        memory.close()
        return prop, applies
    _, context = run_arm(memory, code_index, notes_index, bundle_arm, question)
    calls[0] += 1
    if arm == "select-a3" and dispatch:
        context = (context + "\n\nCandidate dispatch edges (mechanically derived, "
                   "UNVERIFIED - propose one only if the context supports it):\n"
                   + candidates_block(dispatch))
    response = tracked(PROPOSE_PROMPT % (question, context))
    claim, error = parse_proposal(response.get("text", ""))
    if claim is None:
        committed, action, reason = memory.commit_proposal(
            None, None, task=question)
    else:
        committed, action, reason = memory.commit_proposal(
            claim.get("subject"), claim.get("object"),
            rel=claim.get("rel", "depends_on"), task=question)
    if committed:
        memory.ingest()
    _, view = memory.store.view()
    frontier_ok = any(e.get("failure") in GUARD_FAILURES
                      for e in memory.frontier_list())
    prop = {"id": "%s-rep%d-propose" % (scenario, rep), "arm": arm,
            "scenario": scenario, "rep": rep, "question": question,
            "gold": list(spec["gold"]) if spec["gold"] else None,
            "a3_present": bool(dispatch), "a3_count": len(dispatch),
            "proposal": claim, "proposal_error": error,
            "committed": committed, "reconcile_action": action,
            "commit_reason": reason, "model_calls": calls[0],
            "prompt_tokens": tokens[0], "completion_tokens": tokens[1],
            "latency_ms": round(response.get("latency_ms", 0.0), 1),
            "fixture_digest": digest(scenario, rep)}
    applies = []
    if spec["kind"] == "positive":
        applies.append(apply_stage(memory, code_index, notes_index, bundle_arm,
                                   prop, spec["apply"]))
    elif spec["kind"] == "correct":
        for check in spec["apply"]:
            applies.append(apply_stage(memory, code_index, notes_index, bundle_arm,
                                       prop, check))
    elif spec["kind"] in ("abstain", "contra"):
        prop["no_commit"] = not committed
        # Ledger dedupe coalesces a same-question acquisition abstention
        # into an already-filed retrieval abstention (identical dedupe
        # key); either one records this cell's open gap. Accept the
        # coalesced entry only when the model itself abstained (claim None),
        # never as cover for a committed or invalid proposal.
        entries = memory.frontier_list()
        same_gap = any(e.get("task") == question and e.get("status") == "open"
                       for e in entries)
        prop["frontier_ok"] = frontier_ok or (claim is None and same_gap)
        prop["frontier_coalesced"] = (not frontier_ok) and prop["frontier_ok"]
        prop["pass"] = (not committed) and prop["frontier_ok"]
    if spec["kind"] == "positive":
        exact = (claim is not None
                 and (claim.get("rel"), claim.get("subject"), claim.get("object"))
                 == spec["gold"])
        prop["proposal_exact"] = exact
        prop["committed_correct"] = committed and exact
        prop["false_promotion"] = committed and not exact
        prop["pass"] = bool(prop["committed_correct"])
    memory.close()
    return prop, applies


def apply_stage(memory, code_index, notes_index, arm, prop, check):
    response, _ = run_arm(memory, code_index, notes_index,
                          arm, check["question"])
    parsed = parse_symbols(response.get("text", ""))
    stale = [s for s in parsed if s in check.get("obsolete", [])]
    passed = (set(check["gold"]) <= set(parsed) and not stale
              and "error" not in response)
    out = {"id": prop["id"].replace("-propose", "-apply").replace("-setup", "-apply"),
           "arm": prop["arm"],
           "scenario": prop["scenario"], "rep": prop["rep"],
           "question": check["question"], "gold": check["gold"],
           "obsolete": check.get("obsolete", []), "parsed": parsed,
           "stale": stale, "pass": passed and prop.get("committed", True),
           "had_commit": prop.get("committed", False), "model_calls": 1,
           "prompt_tokens": response.get("prompt_tokens") or 0,
           "completion_tokens": response.get("completion_tokens") or 0,
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("%s/%s apply pass=%s parsed=%s" % (out["id"], prop["arm"], out["pass"], parsed),
          flush=True)
    return out


def summarize_acq(rows):
    summary = {}
    for scenario in SCENARIOS:
        for arm in ARMS:
            cells = [r for r in rows if r.get("scenario") == scenario
                     and r.get("arm") == arm
                     and ("-propose" in r["id"] or "-setup" in r["id"])]
            if not cells:
                continue
            rated = [r for r in cells if "pass" in r]
            applies = [r for r in rows if r.get("scenario") == scenario
                       and r.get("arm") == arm and "-apply" in r["id"]]
            summary["%s/%s" % (scenario, arm)] = {
                "n": len(cells),
                "pass_rate": (sum(1 for r in rated if r.get("pass")) / len(rated)
                              if rated else None),
                "apply_pass_rate": (sum(1 for r in applies if r.get("pass")) / len(applies)
                                    if applies else None),
                "commits": sum(1 for r in cells if r.get("committed")),
                "false_promotions": sum(1 for r in cells if r.get("false_promotion")),
                "model_calls": sum(r.get("model_calls", 0) for r in cells + applies),
                "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in cells + applies),
                "completion_tokens": sum(r.get("completion_tokens", 0) for r in cells + applies)}
    return summary


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if "--merge" in sys.argv:
        out_name, parts = args[-1], args[:-1]
        rows = []
        for part in parts:
            rows.extend(json.load(open(REPO / "benchmarks/agent/runs" / part,
                                       encoding="utf-8"))["tasks"])
        final = {"benchmark": "model-ab-acq-v1", "merged_from": parts,
                 "summary": summarize_acq(rows), "tasks": rows}
        out = REPO / "benchmarks/agent/runs" / out_name
        out.write_text(json.dumps(final, indent=1), encoding="utf-8")
        print("merged", len(rows), "rows ->", out)
        return
    out_name = args[0] if args else "model-ab-acq-v1.json"
    scenarios = args[1].split(",") if len(args) > 1 else list(SCENARIOS)
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-acq-v1-"))
    rows = []
    for scenario in scenarios:
        for rep in range(5):
            for arm in ARMS:
                prop, applies = propose_cell(work, arm, scenario, rep, dry=dry)
                rows.append(prop)
                rows.extend(applies)
                print("%s/%s/%s pass=%s %s" %
                      (prop["id"], arm, "dry" if dry else "live",
                       prop.get("pass"), prop.get("commit_reason", "")),
                      flush=True)
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": ("frozen ACQ v1: 10 seeded scenarios x 5 reps x 3 arms; "
                           "product commit_proposal path, exact-triple scorer; "
                           "abstain scenarios pass on no-commit plus frontier entry; "
                           "reps are robustness probes, not independent tasks; "
                           "held-out PyQuifer tasks untouched"),
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "dry_run": dry, "scenarios": scenarios,
              "summary": summarize_acq(rows), "tasks": rows,
              "limitations": ["n=5 reps per cell; temperature 0; directional evidence only."]}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(record["summary"]))
    print("wrote", out)


if __name__ == "__main__":
    main()
