# Tunic optional measurement and discovery contract

Status: proposed extension. This document owns the logical boundary; [geometry research](../research/geometry/README.md) owns mathematical alternatives, counterexamples, and experiments. No measurement engine or discovery harness exists.

## Derived view

Measurements attach to a node pair, edge, path, subgraph, transformation, or task candidate. They do not overwrite original assertions. Semantic similarity, connectivity, reliability estimates, dependency, and task utility have distinct meanings; some are asymmetric or are not metrics.

A proposed measurement records kind, value/range/units, direction, domain, and explicit missing/undefined/infinite state; graph snapshot and access/time-filtered projection; relation filters, aggregation/symmetrization and provenance policy; algorithm/model/version/parameters; training/calibration data identity; numerical error/uncertainty; task/model/hardware scope; and source/experiment receipts plus invalidation dependencies.

Compute only from authorized evidence. Local graph edits may change global spectral measures, so stale scores need recomputation or a justified approximation/update policy. Retrieval use and factual authority remain separate: repeated exposure or high centrality does not strengthen truth.

## First-class research objects

| Object | Semantic commitment |
| --- | --- |
| `Hypothesis` | Proposed claim/transfer candidate with origin, assumptions, alternatives, predicted effect, and falsifiable check. |
| `Constraint` | Stated requirement/formula with theory, types/units, scope/authority, and satisfying/violating observations. |
| `Transformation` | Mapping with input/output contracts, preconditions, effects, revision, applicability limits, and verification obligations. |
| `Experiment` | Planned or executed procedure with inputs, control/candidate, environment, resource bounds, outcome/failures, and raw evidence identity. |

Representing an executable artifact does not authorize executing it. A configured external harness owns resource/side-effect boundaries. Solver results, sampled tests, formal proofs, and benchmark measurements establish different scoped evidence. Record `supported_on`, `refuted_by`, `proved_under`, or `inconclusive` with their conditions rather than unqualified verification.

Hypothesis records may appear in a context IR as explicitly labeled proposals about what to investigate; they cannot masquerade as established assertions. Support for the existence/origin of a hypothesis is distinct from support for its proposed conclusion.

## Promotion and feedback

Candidate geometry/analogy leads to a hypothesis, applicability checks, a controlled experiment/proof attempt, observations, and a scoped adjudication record. Supporting results can justify a new assertion under the recorded conditions; they do not mutate the original hypothesis into a universally true fact. Keep failed and inconclusive attempts.

Learned ranking requires versioned candidate/exposure/action/outcome logs and independent evaluation. Begin with fixed typed-path costs and simple structural controls. Resistance starts as a diagnostic. Curvature, learned geometry, causal discovery, and optimization transfer require residual failures and held-out quality/cost evidence before adoption. Detailed planned checks live in [geometry experiments](../research/geometry/experiments.md).
