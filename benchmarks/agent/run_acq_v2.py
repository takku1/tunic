"""ACQ v2: candidate-aided acquisition, frozen A/B evaluation.

Five arms over the acqgen2 dev/heldout suites:
  none                  question only, no memory context
  lexical               BM25 code + text notes context (v1 lexical arm)
  tunic                 tunic-select context (v1 select arm)
  tunic-candidates      tunic context + candidate evidence blocks (all vias)
  tunic-commit-cand     model SELECTS one live candidate id + justification;
                        the arm commits through Memory.commit_candidate
                        (governed path, receipt recorded)

Product path throughout; the scorer compares committed triples to gold
exactly. A candidate being present is not an acquisition success.
Abstain/conflict/duplicate cells pass on no-commit plus the right gate
behavior. Precision and recall are reported separately.

Usage:
  python run_acq_v2.py <out.json> [suite] [scen,...] [--dry-run] [--arms a,b]
      [--backend=ollama|kimi-cli] [--kimi-model=NAME] [--kimi-timeout=SEC]

Backend: ollama (default) serves the frozen qwen3:4b comparison arms;
kimi-cli shells out to the installed Kimi Code CLI in tool-less print mode,
riding the authenticated subscription (no API keys). kimi-cli results must
be written to their own artifacts -- never overwrite the qwen3 runs.
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
import run_model_ab_multisession_v2 as _ms_v2  # noqa: E402
from run_model_ab_multisession_v2 import (  # noqa: E402
    CONTEXT_CAP, PROPOSE_PROMPT, chat, parse_proposal, parse_symbols, run_arm)
from run_model_ab_multisession_v4 import static_edge_visible  # noqa: E402

from kimi_cli_backend import model_identity as _kimi_identity  # noqa: E402

# Inference backend. The frozen qwen3:4b Ollama arm is the default; the
# kimi-cli backend rides the user's authenticated Kimi Code subscription
# (subprocess per call, tool-less agent). run_arm resolves `chat` at call
# time from its own module globals, so switching backends patches that
# global; nothing in the frozen v1/v2 modules is modified.
_BACKEND = {"name": "ollama", "model": MODEL, "chat": chat}


def use_backend(name, kimi_model=None, kimi_timeout=None):
    if name == _BACKEND["name"]:
        return
    if name != "kimi-cli":
        raise SystemExit("unknown --backend %r (ollama|kimi-cli)" % name)
    import kimi_cli_backend
    kimi_cli_backend.configure(model=kimi_model, timeout_s=kimi_timeout)
    _BACKEND.update(name=name, model=kimi_cli_backend.model_identity(),
                    chat=kimi_cli_backend.chat)
    _ms_v2.chat = kimi_cli_backend.chat

from acqgen2 import SCENARIOS, build, digest  # noqa: E402
from tunic.memory import Memory  # noqa: E402

ARMS = ("none", "lexical", "tunic", "tunic-candidates", "tunic-commit-cand")
GUARD_FAILURES = ("reconcile_invalid", "reconcile_conflict", "proposal_abstained")
CANDIDATE_VIAS = ("callback_parameter", "registry_dispatch",
                  "attribute_dispatch", "constructor_call")

SELECT_PROMPT = """Task: %s

Context:
%s

Proposed relationship candidates (mechanically derived from static code
observations; UNVERIFIED until reviewed):
%s

Your job is REVIEW, not discovery. Each candidate names a caller -> callee
relationship with the mechanical observation that produced it (via/rule, with
derivation spans). Judge each candidate's caller/callee pair against the task:
pick the ONE candidate whose relationship answers the task. Selecting records
that candidate as a reviewed claim through the governed commit path.

Dependency semantics: a candidate whose callee is a CLASS also answers tasks
about that class's methods or attributes â€” constructing a class embeds its
behavior, so a change to a member reaches every function that constructs the
class.

Reply with ONE of:
SELECT <candidate-id, copied exactly from the 'id' field of the chosen
line> | <one-line justification naming the evidence relied on>
or
ABSTAIN | <reason> (only when no candidate's relationship answers the task)"""


def fresh_cell(work, arm, suite, scenario, rep, files):
    dst = work / arm / suite / scenario / ("rep%d" % rep) / "repo"
    dst.mkdir(parents=True)
    for path, text in files.items():
        target = dst / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return dst


def case_candidates(memory, vias=CANDIDATE_VIAS):
    found = memory.candidates()
    return [c for c in found["candidates"] if c.get("via") in vias]


def resolve_candidate_id(selected, candidates):
    """Map a model-echoed selection back to a live candidate id.

    Exact id first; then a unique id the selection is a suffix of; then a
    unique candidate whose caller or callee symbol the selection names
    (small models sometimes echo the endpoints instead of the id).
    Unresolvable or ambiguous -> None (the commit path then refuses)."""
    ids = [c["id"] for c in candidates]
    if selected in ids:
        return selected
    matches = [i for i in ids if i.endswith(selected)]
    if len(matches) == 1:
        return matches[0]
    by_symbol = [c["id"] for c in candidates
                 if selected in (c.get("caller"), c.get("callee"))]
    if len(by_symbol) == 1:
        return by_symbol[0]
    return None


def _class_methods(memory, class_key):
    """Mechanically derived member list for a class symbol (methods the
    extraction keyed under path::Class.name). Capped: the block is
    evidence, not documentation."""
    _snap, view = memory.store.view()
    prefix = class_key + "."
    methods = sorted(s["key"][len(prefix):].split(".", 1)[0]
                     for s in view.get("symbols", [])
                     if s["key"].startswith(prefix))
    return methods[:5]


def candidates_block(candidates, memory=None):
    """One line per candidate. Constructor-call candidates name the class
    they construct; without the class's method list the model cannot
    connect a method-named task to a class-level candidate (the ACQ v2
    constructor gap), so the mechanical member list rides along."""
    lines = []
    for c in candidates:
        methods = ""
        if memory is not None and c.get("via") == "constructor_call":
            members = _class_methods(memory, c.get("callee"))
            if members:
                methods = "; class methods: %s" % ", ".join(members)
        lines.append(
            "- id %s | %s %s %s [via %s; rule %s%s; UNVERIFIED; derivation %s]" % (
                c["id"], c["caller"], c["relation"], c["callee"], c.get("via"),
                c.get("rule"), methods,
                json.dumps(c.get("derivation"), sort_keys=True)))
    return "\n".join(lines)


def setup_cell(memory, spec):
    setup = spec.get("setup")
    if setup == "contra":
        rel, subj, obj = spec["gold"]
        memory.record_outcome(obj, subj, rel="contradicts",
                              document="notes/proposed.md", authority="reviewer")
    elif setup == "duplicate":
        rel, subj, obj = spec["gold"]
        memory.record_outcome(subj, obj, rel=rel,
                              document="notes/proposed.md", authority="reviewer")
    elif setup == "correct":
        e1, e2 = spec["stale"], spec["gold"]
        memory.record_outcome(e1[1], e1[2], rel=e1[0],
                              document="notes/proposed.md", authority="reviewer")
        memory.record_outcome(e1[1], e1[2], rel="retracts", retract=e1[0],
                              document="notes/proposed.md", authority="reviewer")
        memory.record_outcome(e2[1], e2[2], rel=e2[0],
                              document="notes/proposed.md", authority="reviewer")
    memory.ingest()


def propose_cell(work, arm, suite, scenario, rep, dry=False):
    """One propose cell. Returns (propose row, [apply rows])."""
    from tunic.baselines import BM25, TextNotes  # noqa: E402
    spec = build(scenario, rep, suite)
    root = fresh_cell(work, arm, suite, scenario, rep, spec["files"])
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    setup_cell(memory, spec)
    calls, tokens = [0], [None, None]

    def tracked(prompt_text, select_prompt=False):
        response = _BACKEND["chat"](prompt_text)
        calls[0] += 1
        for i, key in enumerate(("prompt_tokens", "completion_tokens")):
            value = response.get(key)
            if value is not None:
                tokens[i] = (tokens[i] or 0) + value
        return response

    code_index, notes_index = BM25(root), TextNotes(root)
    question = spec["question"]
    candidates = case_candidates(memory)
    present_vias = {c.get("via") for c in candidates}
    misbuilt, why = False, ""
    if spec["kind"] in ("positive", "contra") and spec["gold"] is not None:
        rel, subj, obj = spec["gold"]
        visible = static_edge_visible(memory, subj, obj)
        want = spec.get("static", "invisible") == "visible"
        if visible != want:
            misbuilt, why = True, "scenario-misbuilt: gold static visibility=%s" % visible
    for via in spec.get("expected_vias", ()):
        if via not in present_vias:
            misbuilt, why = True, "scenario-misbuilt: expected %s candidate absent" % via
    if dry:
        memory.close()
        return {"id": "%s-%s-rep%d-propose" % (suite, scenario, rep), "arm": arm,
                "suite": suite, "scenario": scenario, "rep": rep, "question": question,
                "gold": list(spec["gold"]) if spec["gold"] else None,
                "misbuilt": misbuilt, "misbuilt_reason": why,
                "candidate_vias": sorted(present_vias),
                "fixture_digest": digest(suite, scenario, rep), "dry": True}, []

    if misbuilt:
        memory.close()
        return {"id": "%s-%s-rep%d-propose" % (suite, scenario, rep), "arm": arm,
                "suite": suite, "scenario": scenario, "rep": rep, "question": question,
                "gold": list(spec["gold"]) if spec["gold"] else None,
                "pass": False, "commit_reason": why, "dry": False}, []

    claim, receipt = None, None
    action = reason = None
    committed = False
    response = {"text": "", "latency_ms": 0.0}
    bundle_arm = "lexical" if arm == "lexical" else "tunic-select"
    if arm == "tunic-commit-cand":
        _resp, context = run_arm(memory, code_index, notes_index,
                                 "tunic-select", question)
        calls[0] += 1
        block = candidates_block(candidates, memory) or "(no candidates)"
        response = tracked(SELECT_PROMPT % (question, context, block))
        text = response.get("text", "")
        if text.strip().upper().startswith("ABSTAIN") or not candidates:
            committed, action, reason = memory.commit_proposal(None, None, task=question)
            receipt = {"candidate_id": None, "justification": text.strip()[:200],
                       "proposition": None, "committed": committed,
                       "action": action, "reason": reason}
        else:
            selected = None
            for line in text.splitlines():
                if line.strip().upper().startswith("SELECT"):
                    selected = line.strip()
                    break
            if selected is None:
                committed, action, reason = memory.commit_proposal(
                    None, None, task=question)
                receipt = {"candidate_id": None, "justification": "unparseable",
                           "proposition": None, "committed": committed,
                           "action": action, "reason": reason}
            else:
                parts = selected.split("|", 1)
                head = parts[0].split(None, 1)
                candidate_id = head[1].strip() if len(head) > 1 else ""
                justification = parts[1].strip() if len(parts) > 1 else ""
                candidate_id = (resolve_candidate_id(candidate_id, candidates)
                                or candidate_id)
                try:
                    receipt = memory.commit_candidate(candidate_id, justification,
                                                      task=question)
                except ValueError as error:
                    receipt = {"candidate_id": candidate_id,
                               "justification": justification[:200],
                               "proposition": None, "committed": False,
                               "action": "invalid", "reason": str(error)}
                committed, action, reason = (receipt["committed"],
                                             receipt["action"], receipt["reason"])
        if committed:
            memory.ingest()
    else:
        if arm == "none":
            context = "(no repository context available)"
        else:
            _resp, context = run_arm(memory, code_index, notes_index,
                                     bundle_arm, question)
            calls[0] += 1
        if arm == "tunic-candidates" and candidates:
            context = (context + "\n\nProposed relationship candidates "
                       "(mechanically derived, UNVERIFIED - the candidate list is "
                       "evidence to evaluate, not facts):\n"
                       + candidates_block(candidates, memory))
        elif arm == "tunic-candidates":
            context += "\n\n(no mechanical candidates for this task)"
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
    frontier_ok = any(e.get("failure") in GUARD_FAILURES
                      for e in memory.frontier_list())

    prop = {"id": "%s-%s-rep%d-propose" % (suite, scenario, rep), "arm": arm,
            "suite": suite, "scenario": scenario, "rep": rep, "question": question,
            "gold": list(spec["gold"]) if spec["gold"] else None,
            "lure": spec.get("lure"),
            "candidate_vias": sorted(present_vias),
            "proposal": claim, "receipt": receipt,
            "committed": committed,
            "reconcile_action": (receipt or {}).get("action") or action,
            "commit_reason": (receipt or {}).get("reason") or reason,
            "model_calls": calls[0],
            "prompt_tokens": tokens[0], "completion_tokens": tokens[1],
            "latency_ms": round(response.get("latency_ms", 0.0), 1),
            "fixture_digest": digest(suite, scenario, rep)}

    applies = []
    if spec["kind"] in ("positive", "duplicate") and spec.get("apply"):
        applies.append(apply_stage(memory, code_index, notes_index, bundle_arm, prop,
                                   spec["apply"]))
    elif spec["kind"] == "correct":
        for check in spec["apply"]:
            applies.append(apply_stage(memory, code_index, notes_index,
                                       bundle_arm, prop, check))

    if spec["kind"] == "positive":
        exact = False
        if receipt and receipt.get("proposition"):
            exact = (receipt["proposition"]["caller"],
                     receipt["proposition"]["callee"]) == spec["gold"][1:]
        elif claim is not None:
            exact = (claim.get("subject"), claim.get("object")) == spec["gold"][1:] \
                and claim.get("rel", "depends_on") == spec["gold"][0]
        decoy = bool(spec.get("lure")) and (
            (claim is not None and claim.get("object") == spec["lure"])
            or (receipt and (receipt.get("proposition") or {}).get("callee")
                == spec["lure"]))
        prop["proposal_exact"] = exact
        prop["committed_correct"] = committed and exact
        prop["false_promotion"] = committed and not exact
        prop["decoy_committed"] = decoy
        prop["pass"] = bool(prop["committed_correct"])
    elif spec["kind"] == "duplicate":
        prop["pass"] = (not committed) and prop["reconcile_action"] == "duplicate"
    elif spec["kind"] in ("abstain", "contra"):
        prop["no_commit"] = not committed
        entries = memory.frontier_list()
        same_gap = any(e.get("task") == question and e.get("status") == "open"
                       for e in entries)
        prop["frontier_ok"] = frontier_ok or (claim is None and receipt is None
                                              and same_gap)
        if receipt is not None:
            prop["frontier_ok"] = frontier_ok or receipt["action"] == "abstain"
        prop["pass"] = (not committed) and prop["frontier_ok"]
    memory.close()
    return prop, applies


def apply_pass(scenario, parsed_ok, had_commit):
    """Apply-row pass rule.

    Positive cells gate on the propose commit (a correct answer without
    the acquired edge is not attributable to the arm). Correction and
    duplicate cells pre-assert their knowledge, so the correct propose
    outcome is a no-op and the gate would destroy the retrieval signal."""
    if scenario == "positive":
        return bool(parsed_ok and had_commit)
    return bool(parsed_ok)


def apply_stage(memory, code_index, notes_index, arm, prop, check):
    """Post-commit retrieval check (pass rule: see apply_pass)."""
    response, _ = run_arm(memory, code_index, notes_index, arm, check["question"])
    parsed = parse_symbols(response.get("text", ""))
    stale = [s for s in parsed if s in check.get("obsolete", [])]
    passed = (set(check["gold"]) <= set(parsed) and not stale
              and "error" not in response)
    out = {"id": prop["id"].replace("-propose", "-apply"),
           "arm": prop["arm"], "suite": prop["suite"],
           "scenario": prop["scenario"], "rep": prop["rep"],
           "question": check["question"], "gold": check["gold"],
           "obsolete": check.get("obsolete", []), "parsed": parsed,
           "stale": stale,
           "pass": apply_pass(prop["scenario"], passed,
                              prop.get("committed", True)),
           "had_commit": prop.get("committed", False), "model_calls": 1,
           "prompt_tokens": response.get("prompt_tokens"),
           "completion_tokens": response.get("completion_tokens"),
           "latency_ms": round(response.get("latency_ms", 0.0), 1)}
    print("%s/%s apply pass=%s parsed=%s" % (out["id"], prop["arm"], out["pass"], parsed),
          flush=True)
    return out


def _sum_optional(rows, key):
    """Sum a token field across rows; None when no row reported it (the
    kimi-cli backend cannot see usage and never fabricates counts)."""
    vals = [r.get(key) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def summarize_acq(rows):
    summary = {}
    proposes = [r for r in rows if r["id"].endswith("-propose")]
    applies = [r for r in rows if r["id"].endswith("-apply")]
    for suite in sorted({r.get("suite") for r in rows if r.get("suite")}):
        for scenario in SCENARIOS:
            for arm in ARMS:
                cells = [r for r in proposes if r.get("suite") == suite
                         and r.get("scenario") == scenario and r.get("arm") == arm]
                if not cells:
                    continue
                rated = [r for r in cells if "pass" in r]
                app = [r for r in applies if r.get("suite") == suite
                       and r.get("scenario") == scenario and r.get("arm") == arm]
                commits = [r for r in cells if r.get("committed")]
                correct = [r for r in cells if r.get("committed_correct")]
                summary["%s/%s/%s" % (suite, scenario, arm)] = {
                    "n": len(cells),
                    "pass_rate": (sum(1 for r in rated if r.get("pass")) / len(rated)
                                  if rated else None),
                    "apply_pass_rate": (sum(1 for r in app if r.get("pass")) / len(app)
                                        if app else None),
                    "commits": len(commits),
                    "committed_correct": len(correct),
                    "acquisition_precision": (len(correct) / len(commits)
                                              if commits else None),
                    "acquisition_recall": (len(correct) / len(cells)
                                           if cells else None),
                    "false_promotions": sum(1 for r in cells if r.get("false_promotion")),
                    "decoy_commits": sum(1 for r in cells if r.get("decoy_committed")),
                    "model_calls": sum(r.get("model_calls", 0) for r in cells + app),
                    "prompt_tokens": _sum_optional(cells + app, "prompt_tokens"),
                    "completion_tokens": _sum_optional(cells + app, "completion_tokens")
                                        }
    return summary


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    out_name = args[0] if args else "acq-v2.json"
    suite = args[1] if len(args) > 1 else "dev"
    scenarios = args[2].split(",") if len(args) > 2 else list(SCENARIOS)
    arms = None
    backend = "ollama"
    kimi_model = None
    kimi_timeout = None
    for a in sys.argv:
        if a.startswith("--arms="):
            arms = a.split("=", 1)[1].split(",")
        elif a.startswith("--backend="):
            backend = a.split("=", 1)[1]
        elif a.startswith("--kimi-model="):
            kimi_model = a.split("=", 1)[1]
        elif a.startswith("--kimi-timeout="):
            kimi_timeout = int(a.split("=", 1)[1])
    arms = arms or list(ARMS)
    use_backend(backend, kimi_model=kimi_model, kimi_timeout=kimi_timeout)
    tunic_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=str(REPO)).stdout.strip()
    work = Path(tempfile.mkdtemp(prefix="tunic-acq-v2-"))
    rows = []
    for scenario in scenarios:
        for rep in range(5):
            for arm in arms:
                prop, applies = propose_cell(work, arm, suite, scenario, rep, dry=dry)
                rows.append(prop)
                rows.extend(applies)
                print("%s/%s/%s pass=%s %s" %
                      (prop["id"], arm, "dry" if dry else "live",
                       prop.get("pass"), prop.get("commit_reason", "")), flush=True)
    decoding = ({"temperature": 0, "seed": 0} if backend == "ollama" else
                {"temperature": "server-side (not exposed by kimi-cli)",
                 "seed": None})
    limitations = ["n=5 reps per cell; directional evidence only; heldout run "
                   "once, not tuned against"]
    if backend == "ollama":
        limitations[0] = "n=5 reps per cell; temperature 0; " + limitations[0]
    else:
        # Model swap: the kimi-cli arm differs from the frozen qwen3 run in
        # model, decoding, and latency regime, so arm deltas conflate those.
        limitations.append("kimi-cli backend: decoding is server-side "
                           "(temperature not pinned); token usage not reported "
                           "by the CLI (null, never fabricated); each call is a "
                           "fresh subprocess + session so trials are independent "
                           "but startup latency is included in latency_ms")
        limitations.append("cross-model comparisons with the frozen qwen3 "
                           "artifacts are directional only, not controlled")
    record = {"benchmark": out_name.replace(".json", ""),
              "protocol": ("ACQ v2: candidate-aided acquisition; 5 arms "
                           "(none/lexical/tunic/tunic-candidates/tunic-commit-cand); "
                           "product commit paths; exact-triple scorer; precision and "
                           "recall reported separately; a candidate being present is "
                           "not an acquisition success"),
              "model": _BACKEND["model"], "backend": _BACKEND["name"],
              "decoding": decoding,
              "context_cap": CONTEXT_CAP, "tunic_commit": tunic_commit,
              "dry_run": dry, "suite": suite, "scenarios": scenarios, "arms": arms,
              "summary": summarize_acq(rows), "tasks": rows,
              "limitations": limitations}
    out = REPO / "benchmarks/agent/runs" / out_name
    out.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print("summary:", json.dumps(record["summary"]))
    print("wrote", out)


if __name__ == "__main__":
    main()
