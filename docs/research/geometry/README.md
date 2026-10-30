# Tunic: measurable relationships and task geometry

Research date: 2026-10-30. Status: proposed measurement/research layer, not a working graph engine or a new mathematical theory. The supplied proposal is valuable as a direction; its general performance and discovery claims remain hypotheses.

The useful abstraction is **a family of typed, versioned measurements over explicit graph projections**. There need not be one geometry, one distance, or one weight. Some outputs are metrics, some directed costs, some uncertain predictions, and some task-specific utilities. None changes the authority of the underlying evidence.

Read [mathematical assessment](mathematics.md), [experiments](experiments.md), and [primary sources and tools](sources.md). This extends the [semantic graph index](../semantic-graph/index.md), especially uncertainty, reasoning, invalidation, retrieval, and evaluation.

## Assessment of the proposal

| Direction | Useful interpretation | Necessary restriction |
| --- | --- | --- |
| Multidimensional relations | Preserve semantic similarity, structural connectivity, reliability, dependency and utility separately. | Shared subject does not imply shared scale, comparability, or calibrated probability. |
| Negative-log path costs | Most reliable admissible route under a specified transmission model. | A route's success differs from existence of any successful route; correlated paths require shared-event accounting. |
| Effective resistance | Redundant connectivity, edge leverage, and structural fragility on a declared undirected conductance graph. | Not truth, semantic distance, causal strength, or evidence independence. |
| Curvature | Candidate neighborhood/bottleneck feature. | Definition/projection dependent; not a universal bridge detector or a discovery proof. |
| Typed reasoning paths | Explicit relation composition, premise closure, and feasibility checks. | Reachability, possible influence, observed execution, and intervention effects are different claims. |
| Learned task geometry | Query-conditioned ranking/policy that learns useful evidence and paths. | Usually a score rather than a metric; observed use is not evidence of truth or causal necessity. |
| Closed-loop discovery | Propose hypotheses, execute bounded experiments, and retain supporting/refuting observations. | Analogy, solver results, tests, and empirical speedups establish different kinds of evidence. |

## Contract boundary

The authoritative proposed [measurement and discovery contract](../../architecture/measurements.md) owns measurement fields, research-object semantics, authority, and promotion rules. This note owns the research interpretation and alternatives. Use the [decision record](../../architecture/decisions.md) for current dispositions; no geometric method is a required initial dependency.

## Hypothesis and validation lifecycle

```mermaid
flowchart LR
    A[Grounded assertions and artifacts] --> B[Geometric or structural candidate]
    B --> C[Hypothesis with assumptions]
    C --> D[Constraint and applicability checks]
    D --> E[Controlled experiment or proof attempt]
    E --> F[Supporting, refuting, inconclusive observations]
    F --> G[Scoped adjudication record]
    G --> A
```

Prefer observations such as `supported_on`, `refuted_by`, `proved_under`, and `inconclusive` over a universal `verified=true`. A test suite supports behavior for its cases; a theorem holds within its formal model; a benchmark measures one workload/environment. Failed hypotheses and negative measurements remain valuable records.

Wilderbeast, Chili Crisp, and Team Ops remain potential providers/consumers. Their current interfaces have not been audited. Tunic should first support independent experiment receipts; it need not become their optimizer, solver, or orchestration runtime.

## Initial direction

Start with a transparent typed path-cost baseline, exact structural diagnostics on small projections, and a fixed task-aware ranker. Effective resistance is initially a diagnostic/optional feature, not a required online query stage. Train a utility model only after collecting trustworthy outcomes and exposure logs. Curvature, sheaf methods, broad causal discovery, and optimization-transfer synthesis remain later experiments.

This choice preserves the interesting long-term direction while making it possible for a simple baseline to win.
