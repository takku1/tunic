"""A fixed two-segment upfront/boundary experiment, not live KV mutation."""

import time
import json

from .compiler import compile_impact
from .identity import digest
from .ollama import BackendError
from .render import render_context, segment_prompt

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["impacts", "coverage"],
    "properties": {"coverage": {"type": "string", "enum": ["partial"]},
        "impacts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["symbol", "kind", "path", "citations"],
            "properties": {"symbol": {"type": "string"},
                "kind": {"type": "string", "enum": ["possible_impact"]},
                "path": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}}}}


def assess_answer(rendered, text):
    """Mechanical support gate, not independent task correctness/entailment."""
    try:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate answer field")
                result[key] = value
            return result
        answer = json.loads(text, object_pairs_hook=unique_object)
        if (not isinstance(answer, dict) or set(answer) != {"impacts", "coverage"}
                or answer["coverage"] != "partial" or not isinstance(answer["impacts"], list)):
            raise ValueError("answer must contain typed impacts and partial coverage only")
        payload = json.loads(rendered["payload"])
        candidates = {i["symbol"]:i for i in payload["impacts"]}
        observations = {o["label"]:o for o in payload["observations"]}
        seen = set()
        for claim in answer["impacts"]:
            if not isinstance(claim, dict) or set(claim) != {"symbol", "kind", "path", "citations"}:
                raise ValueError("invalid impact fields")
            symbol = claim["symbol"]
            if not isinstance(symbol, str) or symbol not in candidates or symbol in seen:
                raise ValueError("unsupported or duplicate impact symbol")
            seen.add(symbol)
            if claim["kind"] != "possible_impact" or claim["path"] != candidates[symbol]["path"]:
                raise ValueError("impact kind/path lacks compiled support")
            labels = claim["citations"]
            if not isinstance(labels, list) or any(not isinstance(s, str) for s in labels) or len(set(labels)) != len(labels):
                raise ValueError("invalid citations")
            required = set()
            for step in claim["path"]:
                required.update([observations[step]["evidence"], *observations[step]["resolution_support"]])
            if set(labels) != required:
                raise ValueError("citations do not match full compiled path support")
        return {"accepted": True, "scope": "selected_static_path_and_citation_support_only",
                "status": "supported_subset" if seen else "abstained",
                "missing_candidates": sorted(set(candidates)-seen),
                "compiler_coverage": payload["coverage"],
                "task_correctness": "not_independently_scored"}
    except (ValueError, TypeError, KeyError) as error:
        return {"accepted": False, "error": str(error), "task_correctness": "not_independently_scored"}


def _require_snapshot(store, snapshot):
    if store.current() != snapshot:
        raise ValueError("stale attachment: current ingested snapshot changed; restart the comparison")


def _prompt_record(prompt, rendered=None):
    mappings = []
    if rendered is not None:
        payload = rendered["payload"]
        if prompt.count(payload) != 1:
            raise ValueError("rendered evidence must occur exactly once in an attached prompt")
        offset = len(prompt[:prompt.index(payload)].encode("utf-8"))
        for mapping in rendered["receipt"]["source_mappings"]:
            mappings.append({**mapping, "prompt_start": offset+mapping["payload_start"],
                             "prompt_end": offset+mapping["payload_end"]})
    return {"text": prompt, "digest": digest(prompt.encode("utf-8")), "source_mappings": mappings}


def compare_continuation(store, backend, profile, symbol, task, *, repeats=1,
                         plan_tokens=32, answer_tokens=192, context_tokens=8192,
                         max_input_tokens=6000, max_bytes=65536, seed=7):
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be between 1 and 10")
    snapshot = store.current()
    started = time.perf_counter()
    backend.check_profile(profile)
    result = {"experiment": "tunic.segment-schedule.v3", "snapshot": snapshot, "symbol": symbol,
              "task": task, "profile": profile, "runs": [],
              "cache_policy": "backend_managed; counting probes can warm caches; no cache-off control",
              "trigger": "completion_of_buffered_plan_segment",
              "answer_quality": "not_independently_scored",
              "scope": "local_single_principal; last_ingested_snapshot_not_live_filesystem",
              "limitations": ["Two explicit generation requests, not hidden-state or KV injection.",
                              "Plan text and evidence position differ between arms.",
                              "One-token counting probes perform inference and are included in costs.",
                              "No prefetch, tool executor, or multi-tenant security implementation.",
                              "Outputs are model responses, not verified impact conclusions."]}
    expected_ir = None
    for trial in range(repeats):
        order = ("upfront", "boundary") if trial % 2 == 0 else ("boundary", "upfront")
        for arm in order:
            run = {"arm": arm, "trial": trial+1, "phase": "starting", "outcome": "running", "timings": {}}
            run_started = time.perf_counter()
            result["runs"].append(run)
            rendered = None
            try:
                def collect():
                    nonlocal expected_ir
                    _require_snapshot(store, snapshot)
                    run["phase"] = "compile"
                    start = time.perf_counter()
                    artifact = compile_impact(store, symbol, snapshot=snapshot)
                    run["timings"]["compile_seconds"] = time.perf_counter()-start
                    if expected_ir is not None and artifact["id"] != expected_ir:
                        raise ValueError("comparison evidence changed")
                    expected_ir = artifact["id"]
                    run["phase"] = "validate_render"
                    evidence = render_context(store, artifact, max_bytes=max_bytes,
                                              expected_snapshot=snapshot, timings=run["timings"])
                    run["rendered"] = evidence
                    return evidence
                if arm == "upfront":
                    rendered = collect()
                _require_snapshot(store, snapshot)
                run["phase"] = "plan"
                prompt = segment_prompt(task, evidence=rendered, template=profile["template"])
                run["plan_prompt"] = _prompt_record(prompt, rendered)
                run["plan"] = backend.generate(profile, prompt, context_tokens=context_tokens,
                    max_input_tokens=max_input_tokens, output_tokens=plan_tokens, seed=seed)
                _require_snapshot(store, snapshot)
                if arm == "boundary":
                    rendered = collect()
                _require_snapshot(store, snapshot)
                run["phase"] = "attach_and_answer"
                plan = run["plan"]["generation"]["text"]
                prompt = segment_prompt(task, evidence=rendered, prior_plan=(plan, arm == "upfront"), template=profile["template"])
                run["answer_prompt"] = _prompt_record(prompt, rendered)
                run["attachment"] = {"boundary": "before_plan" if arm == "upfront" else "after_plan",
                                     "snapshot": snapshot, "ir": expected_ir,
                                     "rendered": rendered["id"], "profile": profile["id"],
                                     "validation": "mechanical_current_snapshot_at_request_boundary",
                                     "attachment_label": f"trial-{trial+1}/{arm}/evidence-1",
                                     "mode": "segment_continuation", "prefetch": False}
                run["answer"] = backend.generate(profile, prompt, context_tokens=context_tokens,
                    max_input_tokens=max_input_tokens, output_tokens=answer_tokens, seed=seed, output_schema=ANSWER_SCHEMA)
                _require_snapshot(store, snapshot)
                run["answer_support"] = assess_answer(rendered, run["answer"]["generation"]["text"])
                run["outcome"] = run["answer"]["outcome"]
                if run["outcome"] == "complete" and not run["answer_support"]["accepted"]:
                    run["outcome"] = "unsupported_answer"
                run["phase"] = "finished"
            except (ValueError, OSError) as error:
                run["outcome"] = "failed"
                run["error"] = str(error)
                if isinstance(error, BackendError) and error.record is not None:
                    run["failed_request"] = error.record
                run["timings"]["arm_wall_seconds"] = time.perf_counter()-run_started
                result["outcome"] = "failed"
                result["remaining_runs"] = "not_run_after_failure; refresh/restart rather than mix profiles"
                result["wall_seconds"] = time.perf_counter()-started
                return result
            run["timings"]["arm_wall_seconds"] = time.perf_counter()-run_started
    backend.check_profile(profile)
    if any(r["outcome"] == "unsupported_answer" for r in result["runs"]):
        result["outcome"] = "unsupported_answer"
    else:
        result["outcome"] = "complete" if all(r["outcome"] == "complete" for r in result["runs"]) else "output_limit_reached"
    result["wall_seconds"] = time.perf_counter()-started
    return result
