# Tunic vocabulary

These meanings apply to Tunic design docs. External papers/projects may use the same words differently.

| Term | Meaning |
| --- | --- |
| Proposition | A claim's content, independent of which source asserted it. |
| Assertion | A source or explicit derivation asserts a proposition under scope/time and evidence status. It is not automatically true. |
| Evidence | A versioned source record/span or an explicitly scoped observation supporting evaluation of a claim. |
| Provenance / lineage | Who/what produced an artifact and from which inputs/transformations. It establishes origin, not entailment. |
| Entailment | Whether specified premises support a claim under a stated interpretation; separate from provenance and byte integrity. |
| Verified | Avoid unqualified use. Say byte-validated, schema-valid, supported on a named test, or proved under named assumptions. |
| Valid time | When an assertion applies in the represented domain. Unknown is distinct from ingestion time. |
| Transaction time | When a version became recorded/visible in Tunic's history. |
| Snapshot | A consistent committed view of graph/schema/policy state, with explicit index readiness. |
| Coverage | What source/predicate/time space was actually parsed, resolved, indexed, and searched, including exclusions/failures. |
| Derived artifact | Rebuildable output of a versioned transformation; not original source authority. |
| Context IR | Model-independent, query-selected evidence/claim artifact with explicit obligations and validation receipts. |
| Compilation | Retrieval/selection/reconciliation into context IR. Model-specific lowering follows it. |
| Lowering | Rendering and tokenizing an IR for one model/template/capability/budget profile. |
| Selection receipt | Query/snapshot/policy/index dependencies and reasons that validate a selection, including negative/search-universe dependencies. |
| Lowering receipt | Exact rendered/token input identity, token-to-unit mapping, actual costs, and lowering outcome. |
| Cache | Always qualify: source, graph-view, retrieval/selection, compiled-input, or transformer KV cache. |
| KV state | Model/execution-specific computed attention keys/values; prefix-dependent and disposable. |
| Measurement | Versioned observation/estimate over a declared graph projection or experiment. Relevance is not truth. |
| Projection | A filtered/transformed graph view with explicit relation, time, access, aggregation, and direction policies. |
| Hypothesis | A proposed claim with assumptions and a falsifiable check; not promoted by being retrieved often. |
| Constraint | A requirement/formula interpreted within stated theory, types, units, and scope. |
| Transformation | A specified mapping with preconditions, effects, and verification obligations; representation does not authorize execution. |
| Task utility | A measured/estimated outcome for a defined task and evaluator, distinct from causal effect and evidential authority. |
| Baseline | A runnable control once implemented; currently the baseline arms are designs, not collected results. |
