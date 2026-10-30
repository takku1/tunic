# Tunic documentation

Tunic is an evidence and context substrate under development: semantic graph, context compiler, and model-specific runtime adapter. A bounded [Python/SQLite W1 reference slice](architecture/w1-reference.md) now implements ingestion, static impact queries, snapshot history, and deterministic evidence compilation. A [local continuation reference](architecture/continuation-reference.md) adds faithful rendering and a pinned Ollama adapter. Full contracts and comparative benchmarks remain unfinished. GraphGraph is historical reference material with no compatibility requirement.

## Start here

Read [architecture overview](architecture/overview.md), [semantic graph contract](architecture/semantic-graph.md), [context IR](architecture/context-ir.md), then [workloads](evaluation/workloads.md). Use [decisions](architecture/decisions.md) to distinguish settled direction, working proposals, open choices, and deferred experiments. Shared meanings live in the [glossary](architecture/glossary.md).

## Document ownership

| Question | Authoritative document |
| --- | --- |
| What is Tunic, who owns state, and how does a request flow? | [Architecture overview](architecture/overview.md) |
| What do durable claims, evidence, identity, time, and snapshots mean? | [Semantic graph](architecture/semantic-graph.md) |
| What does compilation produce and validate? | [Context IR](architecture/context-ir.md) |
| How are inputs lowered and execution/cache compatibility checked? | [Lowering and runtime](architecture/runtime.md) |
| How might retrieval continue during an inference session? | [Runtime memory controller](architecture/runtime-memory.md) and [bounded implementation](architecture/continuation-reference.md) |
| Where are security and authority boundaries enforced? | [Security requirements and limits](architecture/security.md) |
| What can measurements/hypotheses mean without changing facts? | [Optional measurement contract](architecture/measurements.md) |
| What is chosen or still unresolved? | [Decision dispositions](architecture/decisions.md) |
| What actually runs, with which limits and checks? | [W1 profile](architecture/w1-reference.md), [continuation results](evaluation/continuation-results.md), and [quickstart](../README.md#run-the-reference-slice) |
| How will behavior, utility, and costs be assessed? | [Evaluation strategy](evaluation/strategy.md) and [workload definitions](evaluation/workloads.md) |

Architecture contracts describe proposed behavior, not implemented guarantees. Research notes support or challenge choices; references report inspected external artifacts. If they disagree, update the owning contract/decision explicitly rather than treating an older research suggestion as a second requirement.

## Research map

- [Capability/gap index](research/semantic-graph/index.md): 26 capabilities linked to representation, acquisition, knowledge state, maintenance, retrieval, trust, and evaluation notes. [Source ledger](research/semantic-graph/sources.md) records dates, maturity, and reading depth.
- [Research assessment](research/assessment.md): graph/model integration and transport claims.
- [Runtime memory assessment](research/runtime-memory.md): active retrieval, compatible KV reuse, graph soft-prompt failure modes, prefetch and security; separates viable controller work from speculative model changes.
- [Candidate mathematical foundations](research/mathematics.md): objectives and assumptions; these are not selected algorithms.
- [Database/runtime tools](research/tools.md) and [current project landscape](research/project-landscape.md): candidates and comparison questions.
- [SmartMemory concepts](research/smartmemory-concepts.md): documented memory/retrieval approaches and their limits.
- [Geometry research](research/geometry/README.md): multidimensional scores, paths, resistance, curvature, causal limits, and learned utility. [Mathematics](research/geometry/mathematics.md), [experiments](research/geometry/experiments.md), and [sources](research/geometry/sources.md) retain the research detail.

## Reference evidence

- [GraphGraph investigation](references/graphgraph-investigation.md), [source audit](references/graphgraph-audit.md), and [static inventory](references/graphgraph-source-inventory.json). The reference clone is absent and detailed review was incomplete; historical results are not Tunic validation.
- [SmartMemory source assessment](references/smartmemory-audit.md): inspected artifact/replay, source grouping, and lifecycle code, with revision, license, and review limits.

## Evidence and maintenance rules

**Observed** means a local artifact or described capability was inspected within its stated limits. **Reported** means an external source's claim/result. **Proposed** means a Tunic design or experiment. **Unknown** requires investigation. A paper abstract, code read, test, proof, and benchmark establish different evidence scopes.

Sources were researched through 2026-10-30. Research ledgers record partial reading; no complete literature review or reproduced external result is claimed. Pin actual dependencies, licenses, models, data, and runtime versions before implementation comparisons.

When changing the architecture, update the owning contract and decision disposition, then affected validation criteria and links. Keep equations and alternative techniques in research, historical observations in references, and runnable results separate from planned checks. Avoid duplicating fields/invariants across summaries; link to their owner.
