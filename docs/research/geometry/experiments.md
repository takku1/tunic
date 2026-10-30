# Geometry investigation and acceptance plan

Status: proposed experiments. Only the arithmetic/graph examples recorded in [mathematics](mathematics.md) were calculated. No retrieval, learning, solver, or discovery benchmark has run.

## Scope and controls

Use the existing [three workload protocol](../../evaluation/workloads.md), candidate pools, source versions, generation settings, and evidence oracles. Add each measurement independently before combining them. Fix final tokens and separately compare matched-quality total cost.

| Arm | Added mechanism | Question |
| --- | --- | --- |
| M0 | Vector/lexical retrieval and ordinary bounded graph traversal | Credible simple baseline. |
| M1 | Fixed typed path costs and explicit relation composition | Does a path-aware policy recover missing premises? |
| M2 | Bridge/articulation, degree, and simple source-diversity features | How much is explained by inexpensive structure? |
| M3 | Selected-pair effective resistance/leverage | Does global redundancy add value beyond M2? |
| M4 | Supervised task-aware ranking | Do measured outcomes justify query-conditioned relevance? |
| M5 | Curvature, learned geometric embeddings, or sheaf features | Do these improve on M0–M4 after their cost is counted? |

M1's costs need not pretend to be probabilities. Calibrate a probability model only if repeatable transmission/usage events exist. Resistance is a diagnostic feature; high resistance may signal important isolated evidence to rescue, not evidence to discard. Include an independent lexical rescue route so topology does not suppress sparse valid sources.

## Required cases

- **Provenance duplication:** duplicate/copy one source many times. Structural connectivity can change, but factual authority and independent support count must not rise merely from copying.
- **Shared support:** two reasoning paths share a premise. Compare dependence-aware support with naive product/union formulas.
- **Direction and time:** symmetrization creates a short route that is invalid for the task; edges on a structural route never coexisted.
- **Bottlenecks:** chain, triangle, two-route graph, bridge-linked communities, hub spam, disconnected components, and a rare decisive isolated source.
- **Projection sensitivity:** global versus authorization-filtered versus candidate-induced projections; changing weights/units or graph normalization.
- **Local edit/global score:** insert a distant alternate route and check stale resistance/ranking invalidation.
- **Exposure feedback:** a useful unseen source is absent from the current policy's results. Verify that held-out evaluation and controlled exploration can reveal it without promoting popularity into truth.
- **Path feasibility:** a call is conditional, a table write targets another field, or a read precedes the write. Reachability must not become demonstrated influence.

## Learning and attribution

First collect offline task outcomes with independent evidence/answer scoring. Train a transparent ranker using task, relation, source diversity, costs, and structural features. Split by project/session/time and candidate provenance so near-duplicates and repeated tasks cannot leak into test data. Compare feature ablations and a frozen policy.

Only then investigate exploration or RL. Log propensities and restrict evaluation to supported actions, or run a genuine randomized comparison. A final successful answer does not establish every selected edge was useful: use evidence-completeness labels, matched counterfactual removals, and control repeated model runs. A supplied LLM judgment remains a noisy observation; do not use the same judge as the sole reward and sole independent validator.

Report answer/task quality, evidence support/closure, candidate recall, tokens, prefill/decode and retrieval latency, feature construction/update cost, memory, calibration, and clean/adversarial regressions separately. Include numerical solver error and the graph-projection identity for resistance/curvature measurements.

## Minimal discovery demonstration

Represent an optimization-transfer proposal as a hypothesis linking an existing transformation to a new target. Include the required semantic equivalence/preconditions, hardware assumptions, and a predicted cost change. The first case should be independently known and falsifiable, not a claim to discover new mathematics.

1. Retrieve a candidate analogy and record why it was proposed.
2. Check applicability and explicit type/effect/aliasing/precision constraints.
3. Run a bounded proof/solver attempt or correctness comparison with explicit limitations.
4. Benchmark control and candidate on pinned inputs/hardware, including failures and variance.
5. Record supporting/refuting/inconclusive results without rewriting the original hypothesis or erasing its history.

Example failure: reassociation of real-number addition is not automatically a valid floating-point rewrite. Another is applying a locality optimization to a workload whose new indexing cost outweighs reuse. A passing test or faster sample cannot become universal equivalence or a general speedup theorem.

## Ordering and decision gates

Measurement contracts and small exact oracles first; fixed typed paths next; inexpensive diagnostics and resistance comparison next. Learned ranking follows trustworthy outcome logs. Curvature/latent geometry and broader discovery follow only if residual failures and measurements justify them.

Promote a technique only if it improves a preregistered quality/cost frontier on held-out tasks and preserves evidence/authorization contracts. Specific sample sizes, tolerance margins, and resource budgets remain unset until actual fixtures/model/hardware are selected. The toy calculations validate formulas and expose pitfalls; they do not validate the architectural hypothesis.
