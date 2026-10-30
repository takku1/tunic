# Local continuation smoke results: 2026-10-30

Status: executed integration checks, not a comparative W1/V/G/T benchmark or a claim of superiority. The [implementation profile](../architecture/continuation-reference.md) owns behavior and limitations. Source extraction, reverse-call selection and the graph snapshot were unchanged throughout these model experiments.

## What worked

The CLI rendered validated W1 IR into reversible evidence JSON, ran a buffered planning segment, supplied graph evidence at a deterministic generation boundary, then ran a final segment. Both arms received identical evidence within each experiment. The final Qwen3 run returned the expected `service.py::process` and `test_service.py::test_process` candidates with exact compiled paths and full citation support in all four responses. The callback in `unrelated.py` remained an explicit analysis gap.

These are mechanically supported possible-impact selections from the compiler's candidate set, not demonstrated runtime effects or independently scored open-ended answers. The gate permits supported subsets/abstention and reports missing candidates; passing it is not proof of exhaustive retrieval or factual truth. The model is selecting/copying supplied structured information in this smoke task, not discovering new dependencies.

## Recorded attempts, including failures

| Trace | Model and representation | Observed result |
| --- | --- | --- |
| [Exploratory free-form](runs/continuation-2026-10-30.json) | Llama 3.2 1B, evidence JSON v1, free-form answer | Four generations completed, but answers omitted requested source labels and mischaracterized some dependencies/analysis gaps. No support gate existed in this exploratory harness; its `complete` outcome describes generation only. |
| [Structured Llama](runs/continuation-structured-2026-10-30.json) | Llama 3.2 1B, schema-constrained answer and support gate | All four rejected: unsupported identifiers/paths, despite syntactically structured responses. |
| [Structured Qwen](runs/continuation-qwen3-2026-10-30.json) | Qwen3 4B, same pre-obligation rendering | All four rejected: unsupported target selection or incomplete path citations. |
| [Explicit support obligations](runs/continuation-qwen3-supported-2026-10-30.json) | Qwen3 4B, evidence JSON v2 with mechanically computed `required_citations` | All four passed the same symbol/path/full-support gate and included both expected candidates. |

The implementation was revised between attempts. This is a development trace, not a preregistered ablation proving the last change caused a general accuracy improvement. No failed attempt was repaired in-place or presented as successful. Exact prompts, profile identities, model outputs, counters and per-stage timings are in the JSON files. Historical profile/template versions are retained as evidence and need not be executable with the current CLI; repin for a new run rather than mixing versions.

Older traces call a local attachment label `idempotency`; it was not a persisted replay guard or exactly-once guarantee. The current implementation names it `attachment_label`. No distributed attachment/retry guarantee is implemented.

The practical lesson is to expose machine-known support obligations explicitly. The model should not have to reconstruct which citations a compiler path requires. The support gate independently derives required citations from observations, rejects extra/missing support or invented identifiers, and never converts a static path into proof of behavior.

## Environment and final protocol

Observed environment: Python 3.12.10, Windows, Ollama **0.40.11**, NVIDIA GeForce RTX 4090, driver **621.01**, reported total GPU memory **24564 MiB**. Final model `qwen3:4b`, GGUF **Q4_K_M**, manifest digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`. Profiles bind backend metadata and Tunic's explicit no-thinking template; they do not claim to pin every driver/kernel scheduling choice.

Final settings: `tunic.segment-schedule.v3`, evidence renderer `w1-evidence-json-v2`, template `tunic-qwen3-nothink-segments-v2`, seed 7, temperature 0, context 8192, maximum input 6000, plan allowance 32 tokens and final allowance 512 tokens. Planning ends at its explicit token boundary and is buffered; its output is not an answer or evidence. Run order: upfront/boundary, then boundary/upfront. This is one task with repeated deterministic generations, not four independent quality samples.

| Trial | Arm | Plan / final input tokens | Plan + final generated tokens | Arm wall time, including probes/checks |
| --- | --- | --- | --- | --- |
| 1 | Upfront | 1423 / 1541 | 32 + 132 | 7637.52 ms |
| 1 | Boundary | 130 / 1538 | 32 + 132 | 1199.33 ms |
| 2 | Boundary | 130 / 1538 | 32 + 132 | 1227.76 ms |
| 2 | Upfront | 1423 / 1541 | 32 + 132 | 1178.65 ms |

Each arm additionally performed **two one-token counting probes**. Their input-token totals were 2964 for upfront and 1668 for boundary; each also generated two discarded output tokens. Actual generation requests repeated those inputs. Input tokens are not equivalent to newly computed prefill tokens: the backend reported cache reuse, and every probe could warm subsequent requests. The raw per-request input/cached-token counts and prefill/generation/load durations are preserved separately. Missing counters would be unavailable, not zero.

`first_response_seconds` measures time to first nonempty response content for a particular HTTP generation request. It excludes that request's preceding counting probe/profile checks and is not end-to-end TTFT or time to first useful grounded answer. The model plan is buffered, so it is not early user-visible grounded output. Phase timings distinguish graph compilation, validation, rendering and complete model-request work.

Do not infer a speedup from the first upfront row: load/cache state and request order were uncontrolled, and its overhead includes expensive preflight work. Later arm times are close; neither quality nor latency superiority is established. Boundary delivery avoids graph evidence in the planning request but still supplies it for the final answer; this does not demonstrate eliminating repeated serialization or decode compute.

## Checks and remaining work

`python -m unittest discover -s tests -v` passes **34 tests**, including source/render mapping, stale/tampered IR, framing for both templates, exact count/context budgets, profile drift/removal, malformed/incomplete streams, unsupported answers, boundary snapshot changes and CLI subprocesses. HTTP fixture tests are protocol simulations; the linked Ollama traces are the actual inference evidence. Checks ran unguarded because this session cannot write the full-stack run log under `.git`.

No cache-disabled control, independent answer-quality benchmark, active trigger, prefetch, custom KV integration, multi-tenant authorization or model-level prompt-injection defense has been validated. Next compare multiple independently labeled tasks under declared cold/warm policy and a tokenizer/counting path whose overhead is acceptable. Only then investigate whether prefetch hides measurable retrieval stalls. Do not add speculative work merely because the local boundary mechanism functions.
