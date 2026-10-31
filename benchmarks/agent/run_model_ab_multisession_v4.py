"""Multisession v4 (FROZEN): acquisition protocol comparison A/B/C.

Frozen scenario specification (do not tune prompts against results;
fresh scenarios; held-out PyQuifer tasks untouched):

  S1  prose decision:  decided_by round_total -> notes/decisions.md::document.
      Discoverable via mention synthesis; static graph has no such edge.
  S2' synthesis edge:   depends_on place_order -> round_total, true only via
      a registry-dict indirection the static extractor misses, plus a
      decision note. Self-check fails the cell if the edge is statically
      visible ("scenario-misbuilt"). Replaces the v3-pilot S2 whose gold was
      redundant with a static edge (rational abstention, floor effect).
  DUP photocopier:     S1 gold pre-committed; model re-proposes; nothing new
      may be asserted. Pass = assertion count unchanged (and, when the model
      re-proposes gold, reconcile action == duplicate).
  GATE no-truth:       add_item question; nothing may be committed.
  CORR correction use: harness commits S2' gold then a reviewer-accepted
      retracts; fresh session must NOT return the obsolete symbol.

Protocol conditions (same stage-0 context per arm+scenario; context_sha
recorded to prove it):
  A one-stage: answer+propose in one call -> reviewer gate -> commit.
  B two-stage: discover call -> second call with FULL same context plus the
    identified symbols as ADVISORY (never restrictive) -> gate -> commit.
  C hybrid:   A's proposal -> deterministic Memory.reconcile_proposal;
    ADD -> gate; DUPLICATE -> NOOP (no write, no second call);
    INVALID -> refuse; CONFLICT -> one disambiguation call -> gate.
    C reuses A's proposal: B-vs-A isolates staging, C-vs-A isolates the
    deterministic gate, with zero extra proposal calls.

Scorecard per row: proposal validity, exact-triple accuracy, commits,
duplicate insertions (must be 0), false writes, next-session apply pass,
stale reliance, model calls, prompt/completion tokens, latency.
n=1, temperature 0: directional evidence only; no CIs claimed.
Requires Ollama. Writes: benchmarks/agent/runs/model-ab-multisession-v4.json.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from run_model_ab_v1 import MODEL, PROMPT  # noqa: E402
from run_model_ab_multisession_v2 import (  # noqa: E402
    CONTEXT_CAP, PROPOSE_PROMPT, bundle_lexical_notes, chat,
    gate_commit, parse_proposal, parse_symbols, run_arm, source_manifest)
from run_model_ab_multisession_v2 import (  # noqa: E402
    TextNotes as _TextNotes, BM25 as _BM25)

from tunic.memory import Memory  # noqa: E402

ARMS = ("lexical", "tunic-select")
PLACE = "checkout.py::place_order"
ROUND = "pricing.py::round_total"
ADD = "cart.py::add_item"

PROPOSE2_PROMPT = (
    "You are recording what you learned into shared memory. "
    "A first pass over the same context below identified these symbols "
    "as possibly relevant (advisory, not exhaustive): {symbols}. "
    "Now propose the single key relationship as a JSON list inside a "
    "```tunic-claims fence, e.g. [{{\"rel\": \"depends_on\", \"subject\": \"a.py::f\", "
    "\"object\": \"b.py::g\"}}]. Relations: depends_on, implements, decided_by. "
    "Use exact path::symbol keys for code, or path/to/note.md::document for a "
    "whole note file. You may use anything evidenced in the context, whether "
    "or not it appears in the advisory list. "
    "If no true relationship holds, emit an empty list [].\n\n"
    "Question: %s\n\nKnown context (identical for every protocol):\n%s")

DISAMBIGUATE_PROMPT = (
    "You proposed %s, but the memory holds blocking evidence: %s. "
    "If you have NEW evidence from the context below that overcomes the "
    "block, restate your proposal as a ```tunic-claims fence. If the "
    "blocking evidence is right, emit an empty list [].\n\n"
    "Question: %s\n\nKnown context:\n%s")

S1 = {"id": "S1", "question": "What decision did we record about round_total?",
      "gold": ("decided_by", ROUND, "notes/decisions.md::document"),
      "apply": {"question": "What did we decide about round_total?",
                "gold": [ROUND], "obsolete": []}}
S2 = {"id": "S2", "question": "What must change if round_total changes, and why?",
      "gold": ("depends_on", PLACE, ROUND),
      "apply": {"question": "What else must change if round_total changes?",
                "gold": [PLACE], "obsolete": []}}

DISPATCH_PY = '''"""Totals go through a named-strategy registry (static calls invisible)."""
from pricing import round_total

STRATEGIES = {"standard": round_total}


def apply(strategy, cart):
    return STRATEGIES[strategy](cart)
'''
PLACE_ORDER_PY = '''from cart import add_item, total
from dispatch import apply


def place_order(cart):
    return apply("standard", cart)
'''
DECISION_EXTRA = ("\nDECISION 2026-10-27: checkout totals resolve through "
                  "dispatch.apply with the standard strategy, which names "
                  "`pricing.py::round_total`.\n")


def fresh_cell(work, arm, cell, dispatch=False):
    dst = work / arm / cell / "repo"
    shutil.copytree(REPO / "benchmarks/agent/repo", dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "notes" / "outcome.md").unlink()
    if dispatch:
        (dst / "dispatch.py").write_text(DISPATCH_PY, encoding="utf-8")
        (dst / "checkout.py").write_text(PLACE_ORDER_PY, encoding="utf-8")
        with (dst / "notes" / "decisions.md").open("a", encoding="utf-8") as h:
            h.write(DECISION_EXTRA)
    return dst


def static_edge_visible(memory, caller, callee):
    _, view = memory.store.view()
    return any(a.get("proposition", {}).get("caller") == caller
               and a.get("proposition", {}).get("callee") == callee
               and a.get("proposition", {}).get("relation") == "static_call"
               for a in view["assertions"])


def assertion_count(memory):
    _, view = memory.store.view()
    return len(view["assertions"])


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def propose_cell(work, arm, condition, scen, dispatch=False):
    """One propose cell. Returns (propose row, apply row, memory handles)."""
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    root = fresh_cell(work, arm, scen["id"] + "-" + condition, dispatch=dispatch)
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    if dispatch and (static_edge_visible(memory, "dispatch.py::apply", ROUND)
                     or static_edge_visible(memory, PLACE, ROUND)):
        memory.close()
        return {"id": "%s-propose-%s" % (scen["id"], condition), "session": 1,
                "arm": arm, "condition": condition, "question": scen["question"],
                "pass": False, "commit_reason": "scenario-misbuilt: gold edge "
                "statically visible", "db": "", "root": ""}, None
    code_index, notes_index = BM25(root), TextNotes(root)
    tokens = [0, 0]

    def tracked(prompt_text):
        response = chat(prompt_text)
        calls_tracker[0] += 1
        tokens[0] += response.get("prompt_tokens") or 0
        tokens[1] += response.get("completion_tokens") or 0
        return response
    calls_tracker = [0]
    discover, context = run_arm(memory, code_index, notes_index,
                                arm, scen["question"], propose=False)
    calls_tracker[0] += 1
    tokens[0] += discover.get("prompt_tokens") or 0
    tokens[1] += discover.get("completion_tokens") or 0
    context_sha = sha(context)
    before = assertion_count(memory)
    if condition in ("A", "C"):
        response = tracked(PROPOSE_PROMPT % (scen["question"], context))
        identified = []
    else:
        identified = parse_symbols(discover.get("text", ""))
        response = tracked(PROPOSE2_PROMPT.format(
            symbols=", ".join(identified) or "(none)") % (scen["question"], context))
    claim, error = parse_proposal(response.get("text", ""))
    committed, reason, action = False, error or "no proposal", None
    if claim is not None:
        if condition == "C":
            action, why = memory.reconcile_proposal(
                claim.get("subject"), claim.get("object"),
                rel=claim.get("rel", "depends_on"))
            if action == "add":
                committed, reason = gate_commit(memory, claim, scen["gold"],
                                               "notes/proposed.md")
                reason = "reconcile=add; " + reason
            elif action == "duplicate":
                reason = "reconcile=duplicate NOOP; " + why
            elif action == "invalid":
                reason = "reconcile=invalid; " + why
            else:
                dis = tracked(DISAMBIGUATE_PROMPT % (claim, why, scen["question"],
                                                    context))
                claim2, error2 = parse_proposal(dis.get("text", ""))
                if claim2 is not None:
                    claim = claim2
                    committed, reason = gate_commit(memory, claim, scen["gold"],
                                                   "notes/proposed.md")
                    reason = "reconcile=conflict, disambiguated; " + reason
                else:
                    reason = ("reconcile=conflict; disambiguation abstained: "
                              + (error2 or ""))
        else:
            committed, reason = gate_commit(memory, claim, scen["gold"],
                                            "notes/proposed.md")
    after = assertion_count(memory)
    prop = {"id": "%s-propose-%s" % (scen["id"], condition), "session": 1,
            "arm": arm, "condition": condition, "question": scen["question"],
            "gold_triple": list(scen["gold"]), "context_sha": context_sha,
            "advisory_identified": identified,
            "proposal": claim, "proposal_error": error,
            "reconcile_action": action,
            "committed": committed, "commit_reason": reason,
            "assertions_before": before, "assertions_after": after,
            "duplicate_rows_added": max(0, after - before - (1 if committed else 0)),
            "pass": committed,
            "model_calls": calls_tracker[0], "prompt_tokens": tokens[0],
            "completion_tokens": tokens[1],
            "latency_ms": round(response.get("latency_ms", 0.0), 1),
            "db": str(root.parent / "sessions.sqlite"), "root": str(root)}
    print("%s/%s/%s pass=%s committed=%s (%s) calls=%d" %
          (prop["id"], arm, condition, committed, committed, reason,
           calls_tracker[0]), flush=True)
    apply_row = apply_stage(memory, code_index, notes_index, prop,
                              scen["apply"], context)
    memory.close()
    return prop, apply_row


def apply_stage(memory, code_index, notes_index, prop, spec, context):
    """Session 2 reads whatever session 1 left (commit or not)."""
    _, view = memory.store.view()
    known = {s["key"] for s in view["symbols"]}
    response, _ = run_arm(memory, code_index, notes_index,
                          prop["arm"], spec["question"])
    parsed = parse_symbols(response.get("text", ""))
    hallucinated = [s for s in parsed if s not in known]
    stale = [s for s in parsed if s in spec.get("obsolete", [])]
    passed = (set(spec["gold"]) <= set(parsed) and not stale
              and "error" not in response)
    out = {"id": prop["id"].replace("-propose-", "-apply-"), "session": 2,
           "arm": prop["arm"], "condition": prop["condition"],
           "question": spec["question"], "gold": spec["gold"], "obsolete": [],
           "had_commit": prop.get("committed", False),
           "parsed": parsed, "hallucinated": hallucinated, "stale": stale,
           "pass": passed,
           "model_calls": 1,
           "prompt_tokens": response.get("prompt_tokens") or 0,
           "completion_tokens": response.get("completion_tokens") or 0,
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("%s/%s pass=%s committed=%s parsed=%s" %
          (out["id"], prop["arm"], passed, prop.get("committed"), parsed),
          flush=True)
    return out


def dup_row(work, arm):
    """S1 gold pre-committed; model re-proposes; C-path must NOOP."""
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    root = fresh_cell(work, arm, "dup")
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    memory.record_outcome(S1["gold"][1], S1["gold"][2], rel=S1["gold"][0],
                          document="notes/proposed.md", authority="prior-session")
    memory.ingest()
    before = assertion_count(memory)
    code_index, notes_index = BM25(root), TextNotes(root)
    response, context = run_arm(memory, code_index, notes_index,
                                arm, S1["question"], propose=True)
    claim, error = parse_proposal(response.get("text", ""))
    action, why, would_commit = None, error or "no proposal", False
    if claim is not None:
        action, why = memory.reconcile_proposal(
            claim.get("subject"), claim.get("object"),
            rel=claim.get("rel", "depends_on"))
        would_commit = ((claim.get("rel"), claim.get("subject"),
                         claim.get("object")) == S1["gold"])
    after = assertion_count(memory)
    reproposed_gold = claim is not None and would_commit
    passed = (after == before and (not reproposed_gold or action == "duplicate"))
    out = {"id": "DUP", "session": 3, "arm": arm, "condition": "C",
           "question": S1["question"], "proposal": claim,
           "proposal_error": error, "reconcile_action": action,
           "reconcile_reason": why, "reproposed_gold": reproposed_gold,
           "gate_would_commit_without_reconcile": would_commit,
           "assertions_before": before, "assertions_after": after,
           "pass": passed, "model_calls": 1,
           "prompt_tokens": response.get("prompt_tokens") or 0,
           "completion_tokens": response.get("completion_tokens") or 0,
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("DUP/%s pass=%s action=%s would_commit=%s" %
          (arm, passed, action, would_commit), flush=True)
    memory.close()
    return out


def gate_row(work, arm):
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    root = fresh_cell(work, arm, "gate")
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    before = assertion_count(memory)
    code_index, notes_index = BM25(root), TextNotes(root)
    response, context = run_arm(memory, code_index, notes_index,
                                arm, "What must change if add_item changes?",
                                propose=True)
    claim, error = parse_proposal(response.get("text", ""))
    committed = False
    if claim is not None:
        action, _ = memory.reconcile_proposal(
            claim.get("subject"), claim.get("object"),
            rel=claim.get("rel", "depends_on"))
        if action == "add":
            committed, _ = gate_commit(memory, claim, ("", "", ""),
                                       "notes/proposed.md")
    after = assertion_count(memory)
    memory.close()
    out = {"id": "GATE", "session": 3, "arm": arm, "condition": "C",
           "question": "What must change if add_item changes?",
           "proposal": claim, "proposal_error": error, "committed": committed,
           "pass": not committed and after == before, "model_calls": 1,
           "prompt_tokens": response.get("prompt_tokens") or 0,
           "completion_tokens": response.get("completion_tokens") or 0,
           "context": context[:CONTEXT_CAP],
           "response": (response.get("text", "") or "")[:2000],
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("GATE/%s pass=%s committed=%s" % (arm, out["pass"], committed),
          flush=True)
    return out


def corr_row(work, arm):
    """Reviewer-accepted commit + retracts; fresh session must not go stale."""
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    root = fresh_cell(work, arm, "corr", dispatch=True)
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    memory.record_outcome(S2["gold"][1], S2["gold"][2], rel=S2["gold"][0],
                          document="notes/proposed.md", authority="reviewer")
    memory.record_outcome(S2["gold"][1], S2["gold"][2], rel="retracts",
                          retract="depends_on", document="notes/proposed.md",
                          authority="reviewer")
    memory.ingest()
    memory.close()
    reread = Memory(str(root.parent / "sessions.sqlite"), root)
    code_index, notes_index = BM25(root), TextNotes(root)
    response, context = run_arm(reread, code_index, notes_index,
                                arm, S2["apply"]["question"])
    parsed = parse_symbols(response.get("text", ""))
    stale = [PLACE] if PLACE in parsed else []
    passed = PLACE not in parsed and "error" not in response
    out = {"id": "CORR", "session": 4, "arm": arm, "condition": "-",
           "question": S2["apply"]["question"],
           "parsed": parsed, "stale": stale, "pass": passed, "model_calls": 1,
           "prompt_tokens": response.get("prompt_tokens") or 0,
           "completion_tokens": response.get("completion_tokens") or 0,
           "context": context[:CONTEXT_CAP],
           "response": (response.get("text", "") or "")[:2000],
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("CORR/%s pass=%s parsed=%s" % (arm, passed, parsed), flush=True)
    reread.close()
    return out


def main():
    out_name = sys.argv[1] if len(sys.argv) > 1 else "model-ab-multisession-v4.json"
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-multisession-v4-"))
    rows = []
    for scen, dispatch in ((S1, False), (S2, True)):
        for condition in ("A", "B", "C"):
            for arm in ARMS:
                prop, apply = propose_cell(work, arm, condition, scen,
                                           dispatch=dispatch)
                rows.append(prop)
                if apply is not None:
                    rows.append(apply)
    for arm in ARMS:
        rows.append(dup_row(work, arm))
        rows.append(gate_row(work, arm))
        rows.append(corr_row(work, arm))
    summary = {}
    for arm in ARMS:
        for scen in (S1, S2):
            shas = {r.get("context_sha") for r in rows
                    if r["arm"] == arm and r["id"].startswith(scen["id"] + "-propose-")
                    and r.get("context_sha")}
            summary["%s/%s/same-evidence" % (arm, scen["id"])] = sorted(shas)
        for condition in ("A", "B", "C"):
            cells = [r for r in rows if r["arm"] == arm
                     and r.get("condition") == condition
                     and "-propose-" in r["id"]]
            summary["%s/%s" % (arm, condition)] = {
                "commits": sum(1 for r in cells if r.get("committed")),
                "apply_pass_given_commit": [
                    a["pass"] for r in cells for a in rows
                    if a["id"] == r["id"].replace("-propose-", "-apply-")
                    and r.get("committed")],
                "duplicate_rows_added": sum(r.get("duplicate_rows_added", 0)
                                            for r in cells),
                "model_calls": sum(r.get("model_calls", 0) for r in cells),
                "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in cells),
                "completion_tokens": sum(r.get("completion_tokens", 0) for r in cells),
                "mean_latency_ms": round(sum(r.get("latency_ms", 0) for r in cells)
                                         / len(cells), 1) if cells else 0}
        arm_rows = [r for r in rows if r["arm"] == arm
                    and r["id"] in ("DUP", "GATE", "CORR")]
        summary[arm + "/guards"] = {
            "dup_prevented": next(r["pass"] for r in arm_rows if r["id"] == "DUP"),
            "gate_held": next(r["pass"] for r in arm_rows if r["id"] == "GATE"),
            "correction_applied": next(r["pass"] for r in arm_rows if r["id"] == "CORR"),
            "hallucinated_applies": sum(
                1 for r in rows if r["arm"] == arm and "-apply-" in r["id"]
                and r.get("hallucinated"))}
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": ("frozen v4: A one-stage vs B two-stage (advisory) vs "
                           "C hybrid (deterministic reconcile first); same stage-0 "
                           "context per arm+scenario (context_sha); oracle reviewer; "
                           "S2 replaced with registry-indirection edge invisible to "
                           "static extraction; DUP/GATE/CORR guards"),
              "model": MODEL, "decoding": {"temperature": 0, "seed": 0},
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "sources": {"tunic": source_manifest(REPO)},
              "summary": summary, "tasks": rows,
              "limitations": ["n=1 per cell, temperature 0; directional evidence only.",
                              "Oracle reviewer stands in for tests/human review.",
                              "S2 gold asserts place_order->round_total directly though "
                              "code indirection runs via dispatch.apply."]}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(summary))
    print("wrote", out)


if __name__ == "__main__":
    main()
