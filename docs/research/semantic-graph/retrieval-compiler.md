# Retrieval, planning, context compilation, and runtime

Scope: SG-17–SG-20 plus adjacent SG-25. A semantic graph is useful even without an LLM; model execution is a separate consumer.

## SG-17 Indexes and fusion

**Gap:** no selected index families, update visibility, retrieval-combination policy, or relevance calibration.

**Current concepts:** [HippoRAG 2](https://arxiv.org/abs/2502.14802) combines passage integration and graph diffusion. [SAG's current record](https://arxiv.org/abs/2606.15971) motivates comparing entity/event joins with global graph construction. Its August duplicate record was withdrawn in favor of the June record; do not count those as independent studies.

**Minimum:** exact/lexical and dense controls, optional typed graph expansion, explicit fusion versus fallback, per-channel budgets, embedding/index version identity, and freshness receipts. Distinguish query-specific relevance from confidence in truth. Filter access/time before rank propagation.

**Check:** exact symbol, paraphrase, disconnected evidence, high-degree hub, and stale embedding backend. Compare channel contributions, latency, and evidence recall at the same final context budget. Graph retrieval must beat a credible hybrid control, not only a weak dense baseline.

## SG-18 Query planning

**Gap:** no logical query operators, admissible paths, scope semantics, timeout behavior, or completeness contract.

**Current concepts:** [AutoSchema](https://arxiv.org/abs/2608.14228) studies live schema/identifier/path grounding rather than relying solely on static schema descriptions. This is relevant to heterogeneous/federated graphs, but requires measured endpoint calls and correctness checks.

**Minimum:** a small read-only typed plan: resolve identifiers, apply time/access filters, traverse bounded relation paths, join, rank, aggregate, and limit. Validate generated plans against schema and cost bounds. Distinguish legitimate empty results from timeout, unsupported operation, and partial coverage. Deterministic graph/count/arithmetic queries should use executable operators rather than narrative approximation.

**Check:** ambiguous entity, unsupported predicate, traversal explosion, stale schema, wrong join, and mixed-time path. Verify plan results against independent queries/oracles. Agentic iterative planning remains an experiment after fixed plans work.

## SG-19 Resolution and diversity

**Gap:** no consistent link between source spans, extracted claims, entities, events, subsystem summaries, and procedural assets.

**Current concepts:** [LineageRAG](https://arxiv.org/abs/2608.16004) keeps evidence-demand provenance through candidate discovery and grounding. SmartMemory's inspected source grouping addresses fact rows monopolizing result slots. Community summaries and learned graph encoders from earlier research are optional higher-resolution views, not replacements for original evidence.

**Minimum:** resolve every derived view to its sources, group related facts by origin, preserve decisive child evidence, and identify unresolved query obligations. Select complements rather than many near-duplicates. Summaries retain transformation/version and omissions; do not assume a readable summary preserves exceptions.

**Check:** many extracted facts from one source versus a decisive second source, copied-source corroboration, an omitted exception, and a summary whose input changed. Score diversity and demand completion alongside answer quality and tokens.

## SG-20 Context IR

**Remaining gap:** the [IR contract](../../architecture/context-ir.md) now has a bounded serialized W1 profile, validator, canonical identity and [faithful model renderer](../../architecture/continuation-reference.md). General temporal/access/selection contracts, token-offset export and richer derivation schemas remain unfinished.

**Minimum:** typed claims/excerpts, evidence references, derivations, obligation closure, time/access selectors, selection dependencies, and model-lowering receipts. Require explicit unsupported/ambiguous/partial results. Keep source-authoritative metadata outside presentation strings and token IDs. Exact token count comes after whole-input rendering.

**Check:** every rendered statement maps to an accepted unit; every required premise is retained; a derived paraphrase cannot masquerade as a quotation. Different models lower the same IR with different execution identities; a capacity failure requests recompilation or returns a clear failure instead of silently deleting evidence.

**Research decision:** compare direct rendering against validated IR at the same candidate pool and budget. Its value must come from fidelity, reuse, inspection, or task outcomes, not a renamed abstraction.

## SG-25 Runtime boundary

**Remaining gap:** the [local Ollama adapter](../../architecture/continuation-reference.md) now executes pinned Llama 3.2/dense Qwen3 segments with measured input budgets. Tunic has no custom KV import/export, cache-off control or verified exact-cache equivalence; broader runtime integration remains conditional.

**Current concepts:** [vLLM prefix caching](https://docs.vllm.ai/en/latest/design/prefix_caching/) includes preceding-prefix identity. [ReCAP](https://arxiv.org/abs/2609.40118) persists attention-derived history signals; [CodeComp](https://arxiv.org/abs/2604.10235) uses program structure for lossy KV selection. These are different mechanisms with different correctness/quality goals.

**Minimum when runtime integration begins:** typed capability negotiation and execution fingerprints; exact-prefix reuse or ordinary prefill. Runtime-derived influence links remain model/layer/context observations, not semantic proof. Lossy compression is a separately evaluated intervention.

**Check:** changed prefix, adapter/model/tokenizer/mask, eviction, transferred layout mismatch, and no-cache equivalence. Compare prefill saved with lookup/transfer/recomputation costs; report unsupported cache observability explicitly. This layer must not dictate entity identity or the graph's storage model.
