# Tunic: three representative workloads

Status: comparative benchmark design. A bounded [W1 reference slice](../architecture/w1-reference.md) and independent small correctness fixtures exist; V/G/T arms, answer models, numeric acceptance limits, and comparative runs do not. The experiment tests whether typed compilation adds value beyond conventional retrieval; no performance or novelty conclusion is implied.

## Common comparison

Use the same versioned sources, access scope, answer model, generation settings, and actual final input-token budget for three arms:

- **V: vector RAG.** Retrieve dense source chunks and render them with source references using a fixed template.
- **G: graph retrieval.** Add typed graph retrieval/expansion, then render the selected evidence directly with the same reference and answer conventions.
- **T: Tunic.** Use the same graph candidate pool as G, compile a validated typed IR with explicit dependencies/time/conflicts, and lower it to model input.

This isolates compiler effects in G versus T; V versus G measures the graph contribution. Add lexical/hybrid retrieval as a diagnostic control so a weak dense baseline cannot manufacture the apparent benefit. Hold extraction inputs constant where possible and report graph construction as an additional cost.

Initially disable model prefix caching in all arms. Then run a second paired cache-enabled track with identical cache capacity and request replay. Split faithful excerpt compilation from any summarization/compression experiment. Truncated or failed requests remain in results with an explicit outcome.

## W1: code change impact and evidence chain

Task: given a change to a configuration field or shared function, identify affected callers, behavior, and tests across several files. Return source evidence for each proposed impact; distinguish definite dependencies from unresolved dynamic dispatch.

Fixtures should include exact symbols, renamed aliases, a misleading text match, a cross-file dependency, an unrelated subsystem, and an incomplete-analysis case. Ground truth comes from independently reviewed source dependencies and executable tests where available, not the graph under evaluation.

Replay a leaf implementation edit, a new incoming caller in a previously unselected file, a deletion, and an unrelated change. Compare incremental selection/IR with full recomputation after every event. A new caller must invalidate completeness even though previously selected nodes are unchanged.

Primary outcomes: dependency/evidence precision and recall, test-identification accuracy, unsupported-impact rate, and update-to-answer freshness. Record compilation work reused separately from runtime prefill saved.

## W2: temporal decisions and contradictory evidence

Task: answer which policy or architectural decision applied at a requested time, who/what supports it, and whether later records corrected or superseded it.

Fixtures should include original evidence, a later decision, a backdated correction, conflicting sources of different authority, unknown effective time, and a source outside the requester's access scope. Query current truth, valid-time history, transaction-time history, and an unanswerable interval separately.

Independent labels must distinguish an actual change from a correction of a historically false assertion. Grounding requires the specific versions and spans, not merely a citation to a document containing similar language.

Primary outcomes: temporal answer correctness, contradiction preservation, citation entailment/fidelity, calibrated abstention, and unauthorized/stale evidence exposure. Keeping history is evaluated separately from selecting the correct historical view.

## W3: multi-session task resumption and portable compilation

Task: resume a paused investigation using its constraints, verified findings, unresolved hypotheses, tool observations, and recent source changes. Produce the next action plus the evidence and premises it requires.

Replay sessions sharing stable instructions and reusable evidence while the request, selected subgraph, and freshness change. Include a previously omitted fact that becomes relevant, an observation invalidated by a file edit, and identical text placed after different prefixes. Evaluate a cold start, warm same-model run, eviction/restart, and a switch to a second pinned model.

Use the same semantic artifact for both model lowerings when required evidence fits both budgets. Otherwise record explicit recompilation or budget failure. Never reuse model A's KV state for model B merely because the IR or text is identical. Hosted APIs lacking cache observability report cache metrics as unavailable, not zero.

Primary outcomes: constraint/premise recovery, next-action task success, unsupported assumption rate, cold/warm latency, token use, and valid prefix reuse. Score historical evidence preservation separately from selected working-context size.

## Independent measurement and economics

Report answer correctness, evidence fidelity, input/output tokens, latency, and cache reuse as separate columns, stratified by workload and replay condition. Include source/index construction, extraction model calls, retrieval, validation, IR serialization, lowering/tokenization, prefill, decode, and cache transfer where observable. Report p50/p95, failed runs, resource residency, and paired uncertainty.

Evidence fidelity includes exact excerpt matching, claim-to-source support, temporal correctness, and whether lossy derivations retain qualifications. Mechanical provenance checks do not substitute for independently labeled entailment checks. Use rule-based or executable oracles where possible and blinded human review for ambiguous cases; any model judge must be specified and audited against a human-labeled subset.

Compare quality at fixed budgets and cost at matched quality. Keep a Pareto frontier rather than collapsing every metric into one arbitrary score. For indexing work I, average per-query savings s, and positive s, the simplified break-even query count is `I/s` in compatible cost units. Include update frequency and failed/retried requests before drawing operating-cost conclusions.

## Acceptance and unresolved choices

Required correctness cases include no invalid cross-model/state reuse, no silent premise removal, no unauthorized evidence, and incremental/full-recompute agreement under declared semantics. Any violation is reported individually rather than averaged away. Numeric quality tolerances, latency/resource limits, sample sizes, and noninferiority margins must be preregistered after workload construction and before inspecting held-out results.

Choose repositories, session traces, two model profiles, serving stack, hardware, and the independent label process next. External Wilderbeast/Chili Crisp/Team Ops integration remains proposed; these workloads must be runnable without assuming those systems provide unverified APIs.
