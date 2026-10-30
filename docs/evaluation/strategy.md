# Tunic evaluation and research plan

Status: proposed comparative experiments. No V/G/T baseline measurements have been collected. Separate [local continuation smoke results](continuation-results.md) record integration checks and exploratory model traces.

The [three-workload protocol](workloads.md) specifies the next comparison: conventional vector RAG, graph retrieval, and Tunic's graph-plus-typed-compiler path. It owns workload definitions and arm controls; this document supplies broader evaluation guidance.

## First establish the task

Start with W1's code-context slice, as proposed in the [decision register](../architecture/decisions.md), and build a versioned corpus with independently labeled evidence and expected outcomes. Retain separate document and temporal-memory suites rather than merging unlike scores. GraphGraph's [audit](../references/graphgraph-audit.md) supplies historical failure cases; restoring its recorded revision is optional unless a source-reuse or historical-comparison claim needs further review. Compatibility is not a prerequisite.

Include exact symbol lookup, cross-file dependencies, multi-hop evidence, changed/deleted sources, contradictory assertions, time-qualified queries, unanswerable questions, and denied-access evidence. Include repeated prefixes and low-reuse requests so caching is not evaluated only under favorable conditions.

## Experiments

| Experiment | Controls | Decision evidence |
| --- | --- | --- |
| Retrieval | Lexical only; dense only; fused; fused plus bounded expansion/PPR | Evidence recall, ranking quality, task success, latency, index/update cost. |
| Subgraph selection | Ranked chunks; bounded BFS; coverage; PCST | Quality at equal token budgets and total retrieval time. |
| Placement | Relevance order; deterministic order; stable prefix plus volatile suffix | Task quality, reused-token fraction, total latency under replayed requests. |
| Runtime caching | Identical prompt cache disabled/enabled; cold/warm/evicted states | Prefill work saved, TTFT, throughput, GPU/host memory, output equivalence. |
| Cache transport | Recompute; local hit; host/disk/network fetch | Transfer/conversion overhead, p95 latency, concurrency effects, failures. |
| Wire format | Identical semantic/token input through text versus supported token/binary path | Serialization/tokenization time and end-to-end improvement. |
| Advanced integration | Full prefill versus cache fusion/custom masks/learned graph bridge | Quality, execution correctness, training cost, operational complexity. |

Measure cold paths independently from warm paths. Match model, weights, tokenizer, template, quantization, sampling, hardware, concurrency, and context lengths. Pin runtime commits, cache capacity, eviction policy, block size, and dependency versions.

## Metrics and correctness

Report evidence Recall@k, nDCG where labels support it, citation correctness, answer/task accuracy, abstention behavior, and stale/unauthorized evidence rate. Separately report indexing cost, update-to-query visibility, retrieval/compilation time, TTFT p50/p95, decode latency, throughput, transfer bytes, and memory residency. Cache-hit counts alone do not establish value.

Cache tests must cover same tokens/different preceding context, changed model/adapters/templates, changed positions/masks, partial blocks, eviction during requests, restart, corrupted payloads, and authorization changes. Repeated isolated chunk hashes must not cause false exact hits after differing prefixes.

For exact compatible caching, compare outputs and where accessible logits against full prefill within defined numerical tolerances. Hardware/kernel nondeterminism must be characterized. For fusion, masking, or learned compression, exact equivalence is not assumed: evaluate quality as a separate intervention.

Use paired queries and fixed replay traces. Report uncertainty across runs and query groups; keep a held-out task set. Inspect retrieved evidence and compiler manifests before attributing an answer improvement to graph reasoning.

## Decision gates

1. **Scope and oracle gate:** pin the first workload, corpus, accepted assertion types, independent evidence labels, and correctness oracle. Historical source recovery is required only for claims that depend on it.
2. **Baseline gate:** establish simple retrieval, faithful provenance, bounded compilation, and reproducible measurements.
3. **Graph gate:** adopt extra graph traversal/selection only when it improves useful evidence or task outcomes at an acceptable cost.
4. **Cache gate:** add placement optimization after exact reuse passes correctness checks; reject gains that materially reduce quality.
5. **Transport gate:** select a binary format or external KV transfer only when profiling shows enough avoidable cost.
6. **Research gate:** investigate custom masks, non-prefix fusion, and trained bridges with their own quality and resource budgets.

Numeric acceptance thresholds remain unset until the workload and deployment budget are known. Do not invent a speedup target from published headline results.

The optional runtime-controller track has its own [factorial experiment protocol](../research/runtime-memory.md#measurable-experiments): retrieval timing crossed with exact cache use, then bounded prefetch or separately evaluated model changes. It does not replace the V/G/T comparison. [Local continuation checks](continuation-results.md) cover a small trusted single-principal path. The full [security acceptance cases](../architecture/security.md#required-tests-before-a-shared-or-model-backed-runtime), controlled cache ablations and independent quality evaluation remain outstanding.

## Follow-up literature work

Read full methods and code for the shortlisted approaches, tracing claims to implementation revisions. Expand into dynamic graph indexing, temporal databases, program dependence graphs, budgeted/submodular retrieval, sparse graph algorithms, cache scheduling, attention sparsity, learned context compression, and graph-to-language alignment. For each addition record problem, assumptions, training requirement, computational complexity, datasets, limitations, reproduction artifacts, and relevance to a concrete design decision.

The objective is a defensible architecture and reproducible comparison, not accumulating mathematical vocabulary without an implementation consequence.
