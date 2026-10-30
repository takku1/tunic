# Geometry sources and tool candidates

Accessed 2026-10-30. Abstract-only entries are research leads; no paper was reproduced. Selected method reads are identified. Publication comments on arXiv are reported rather than independently verified venue status. Source results do not transfer automatically to Tunic's tasks.

## Foundations and counter-evidence

| Source | Status / date | Reviewed use and limits |
| --- | --- | --- |
| [Spielman–Srivastava, Graph Sparsification by Effective Resistances](https://arxiv.org/abs/0803.0929) | 2008 original; 2009 v4 | Abstract/theorem overview. Spectral preservation and approximate resistance; not evidence/typed-query preservation. |
| [Ollivier, Ricci curvature of Markov chains on metric spaces](https://arxiv.org/abs/math/0701886) | 2007 arXiv; journal work 2009 | Abstract/definition reference. Transport/neighborhood dependence. |
| [Meila, Comparing clusterings—an information based distance](https://www.sciencedirect.com/science/article/pii/S0047259X06002016) | Journal of Multivariate Analysis, May 2007 | Publisher abstract; VI metric for partitions, not arbitrary semantic nodes. |
| [Dudik, Langford, Li, Doubly Robust Policy Evaluation and Learning](https://arxiv.org/abs/1103.4601) | 2011, v2 May 6 | Abstract/evaluation setting; biased action exposure and statistical assumptions. |
| [Chen et al., Rethinking the Gold Standard](https://proceedings.iclr.cc/paper_files/paper/2026/hash/0850848de2a37c65a055bf489ba63480-Abstract-Conference.html) | ICLR 2026 proceedings | Venue abstract and its counterexample claims; not a Tunic retrieval experiment. |

## Recent geometry and adaptive retrieval

| Source | Status / date | Reviewed use and limits |
| --- | --- | --- |
| [Warton et al., Graph Inference with Effective Resistance Queries](https://proceedings.mlr.press/v313/warton26a.html) | ALT 2026 / PMLR 313, February 23–26 | Abstract/results overview. Reconstructs/tests hidden graph properties with an ER oracle; does not discover semantic facts from text. |
| [Ribeiro et al., Mitigating Over-squashing without Rewiring](https://arxiv.org/abs/2610.04157) | October 3, 2026; arXiv reports NeurIPS acceptance | Abstract and selected flat-bundle method passages. Sheaf/message-passing assumptions; not arbitrary transformer or knowledge-graph theorem. |
| [Luo et al., Local-Curvature-Aware Knowledge Graph Embedding](https://arxiv.org/abs/2512.07332) | December 10, 2025 v2 | Abstract; heterogeneous geometric representation and link prediction, not source-grounded claim validation. |
| [Li et al., CaSKG](https://arxiv.org/abs/2608.25500) | August 26, 2026 preprint | Abstract plus selected edge-probing passages. Removal/substitution/reordering of skill descriptions are model-mediated probes, not automatically identified real-world causal effects. |
| [Chen et al., GTA-RAG](https://arxiv.org/abs/2608.22479) | August 30, 2026 v2; arXiv reports EMNLP Findings acceptance | Abstract; trajectory/evidence-chain reward signal, not an adopted optimizer. |
| [Luo et al., CoRA](https://arxiv.org/abs/2607.27766) | July 30, 2026; under review | Abstract; gradient-free task-conditioned demonstration retrieval from paired data. Applicability to graph path ranking unestablished. |
| [RouteRAG](https://arxiv.org/abs/2512.09487) | December 10, 2025 preprint | Abstract; adaptive graph/text retrieval and efficiency-aware RL. Needs training and cost comparison. |

These sources support investigating distinct mechanisms, not merging their results into a state-of-the-art claim. In particular, a GNN geometry result, a link-prediction result, and a downstream agent-task result answer different questions.

## Tools worth evaluating

| Candidate | Verified primary entry | Proposed role / important constraint |
| --- | --- | --- |
| NetworkX | [Resistance distance docs](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.distance_measures.resistance_distance.html) | Small exact structural oracle. Directed graphs unsupported by this function; check connectivity and resistance-versus-conductance weight convention. |
| GraphRicciCurvature | [Official repository](https://github.com/saibalmars/GraphRicciCurvature) and [tutorial](https://graphriccicurvature.readthedocs.io/en/latest/tutorial.html) | Ollivier/Forman exploratory calculations; pin method, parameters, supported graph class, solver, and approximation. No installation or benchmark here. |
| DoWhy / PyWhy | [Causal effects workflow](https://www.pywhy.org/dowhy/main/user_guide/causal_tasks/estimating_causal_effects/index.html) | Model, identify, estimate, refute; cannot supply missing identification assumptions. |
| Z3 | [Official guide](https://microsoft.github.io/z3guide/) | Check explicitly encoded constraints and bounded feasibility. Solver success has only the scope of the encoding. |
| egg | [Willsey et al. paper](https://arxiv.org/abs/2004.03082) | Specialized equational search for transformations under sound rewrite rules. An e-graph is not a substitute for a provenance/temporal semantic graph. |

The 2026 [Optimism in Equality Saturation](https://doi.org/10.1145/3808302) publisher record is an additional lead for conditional analysis/rewrite reasoning; only the publisher overview was reviewed. No claim of compatibility or soundness for Tunic's transformations follows from it.

No tool was installed, no external code copied, and no integration with Wilderbeast/Chili Crisp was exercised. Existing [graph tools](../tools.md) and [source ledger](../semantic-graph/sources.md) remain relevant for the evidence substrate.
