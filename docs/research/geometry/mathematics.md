# Mathematical assessment of Tunic measurements

Definitions below are conditional on their stated models. The small arithmetic examples were independently checked in a one-off Python calculation on 2026-10-30; they are not Tunic performance results. Source and reading limits are in the [ledger](sources.md).

## 1. Similarity, distance, and information

For nonzero embeddings, cosine similarity is `s(a,b)=<a,b>/(||a|| ||b||)`. The common dissimilarity `1-s` is not generally a metric: vectors at 0, 60, and 120 degrees yield distances 0.5, 0.5, and 1.5, violating the triangle inequality. Angular or unit-vector Euclidean distances can supply metric properties in embedding space, but not guarantees about semantic truth or task relevance. Distinct entities can share a representation.

Mutual information needs specified random variables and a joint distribution:

`I(X;Y) = sum_(x,y) p(x,y) log[p(x,y)/(p(x)p(y))]`.

It measures dependence, not graph distance or causal effect. A single pair of concept labels supplies no such empirical distribution by itself. Define observations, sampling, conditioning, smoothing, and estimation uncertainty before attaching an information score.

For partitions of a shared sample space, variation of information is `VI(X,Y)=H(X|Y)+H(Y|X)`. [Meila's work](https://www.sciencedirect.com/science/article/pii/S0047259X06002016) establishes a metric on clusterings. It does not turn arbitrary graph nodes into information-theoretic objects. Observed entropy reduction or model log-loss reduction from adding context is another measurement, scoped to the answer model and task distribution.

## 2. Reliable paths

Let E_i be the event that transition i succeeds under a defined task/protocol. The general chain rule is:

`P(intersection_i E_i) = product_i P(E_i | E_1,...,E_(i-1))`.

If the necessary independence or local conditional assumptions are established, route success reduces to `product_e p_e`. Then `cost(path)=-log P(success)=sum_e -log p_e`. With fixed nonnegative edge costs, Dijkstra can find a maximum-product admissible route. Zero probability means an unavailable/infinite-cost edge. If costs depend on path history, simple edge-local shortest paths are insufficient; the search state must encode that history.

The 0.9/0.8 route has probability **0.72** and cost approximately **0.3285 nats**. This is negative-log reliability under the defined event model, not automatically mutual information or semantic information distance. Deterministic typed path costs remain valid retrieval heuristics even without probability semantics, provided they are named as costs.

The most reliable single route is not network reliability. Two independent routes each succeeding with probability 0.72 give `1-(1-0.72)^2=0.9216`. If the routes instead share a 0.9 first transition and have independent 0.8 branches, the result is `0.9*(1-0.2^2)=0.864`. Treating those path successes as independent overestimates reliability. Repeated citations can share an underlying error source even with no shared graph edge.

**Contract:** every probability identifies its event, conditioning, provenance dependence, calibration data, and applicability. An extractor confidence or attention weight is not a transmission probability.

## 3. Effective resistance and leverage

For a finite connected **undirected** graph with positive conductances w, choose an oriented incidence matrix B and set `L=B^T diag(w) B`. For nodes a,b:

`R_eff(a,b)=(e_a-e_b)^T L^+ (e_a-e_b)`.

Resistance is a structural metric within that connected network. Electrical conductance aggregation does not require statistical independence; interpreting redundant routes as independent evidence would require a separate provenance model. A directed typed semantic graph must be projected explicitly before using this formula, losing some direction/type semantics.

Checked examples with unit conductances:

| Network | Relevant endpoints | Shortest-path length | Effective resistance |
| --- | --- | ---: | ---: |
| One two-edge chain | Chain endpoints | 2 | 2 |
| Two internally disjoint two-edge routes | Shared endpoints | 2 | 1 |
| Triangle | Any adjacent pair | 1 | 2/3 |
| Disconnected components | One node in each | No path | Infinite/undefined by contract |

Do not apply a full disconnected Laplacian pseudoinverse formula blindly: it can return a finite algebraic number that is not inter-component electrical resistance. Check components first. For one actual edge e, leverage `ell_e=w_e R_eff(e)` lies in (0,1] in this positive network; a bridge has leverage 1. Raw R_eff alone is not scale-invariant: uniformly scaling conductances changes it. Ordinary bridges/articulation points are cheaper controls for structural fragility.

[Spielman–Srivastava](https://arxiv.org/abs/0803.0929) motivates approximate resistance and spectral sparsification. Spectral preservation is not preservation of typed paths, rare evidence, provenance, or arbitrary query answers. Keep sparsified measurement projections separate from authoritative facts.

For computation, solve a grounded sparse Laplacian system or use justified approximations; avoid materializing an all-pairs dense pseudoinverse for large graphs. Resistance on a candidate-induced subgraph can be inflated because omitted external routes disappear. Record whether the measurement uses a global, scoped, or candidate-only projection. [NetworkX](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.distance_measures.resistance_distance.html) also requires care: its default weight convention is resistance and inverses weights; supplying conductance requires the corresponding option.

## 4. Curvature

For a chosen metric d and neighborhood measures m_x, Ollivier curvature has the form `kappa(x,y)=1-W_1(m_x,m_y)/d(x,y)`. It depends on the walk, idleness, weights, and base metric. [Ollivier](https://arxiv.org/abs/math/0701886) defines it through transport contraction; it is not a context-free score of a semantic relationship.

For the plain unweighted graph-as-1-complex Forman definition, `F(u,v)=4-deg(u)-deg(v)`. A triangle edge and an internal edge in a four-node path both have F=0, but only the latter is a bridge. Augmented definitions counting triangles are different measurements; the simple formula cannot stand in for them.

An [ICLR 2026 paper](https://proceedings.iclr.cc/paper_files/paper/2026/hash/0850848de2a37c65a055bf489ba63480-Abstract-Conference.html) presents counterexamples to curvature as a complete GNN over-squashing detector. Its reported findings concern GNN behavior, not proof of failures or benefits for Tunic retrieval. October's [sheaf-resistance work](https://arxiv.org/abs/2610.04157) further shows why topology and message-passing maps need separate treatment under its flat-bundle assumptions. Do not transfer its theorem to an arbitrary semantic graph or transformer.

## 5. Typed path validity and causal effects

An admissible path must satisfy a relation-composition language, endpoint types, temporal compatibility, and any value/control preconditions. For a regular relation language, search the product of graph nodes with automaton state; arbitrary constraints may require a different bounded solver/search procedure. Preserve complete premises in the compiler IR.

`A calls B; B writes C; D reads C` establishes a structural candidate route. It need not show that A executes the write, that the same field/version is read, that the time ordering matches, or that D's output changes. Distinguish reachability, possible value/control dependence, observed trace dependence, and measured intervention effect.

For a specified outcome Y, intervention X, population, and causal model, one target could be `E[Y|do(X=x1)]-E[Y|do(X=x0)]`. [DoWhy's workflow](https://www.pywhy.org/dowhy/main/user_guide/causal_tasks/estimating_causal_effects/index.html) separates modeling assumptions, identification, estimation, and refutation. A tool does not establish absence of hidden confounding. Removing a document measures an intervention on the retrieval/answer pipeline, not necessarily on the real-world process it describes.

SMT can check satisfiability of encoded path conditions. `sat` supplies a model under the encoding; `unsat` rules out that encoding's possibilities; timeout/`unknown` is inconclusive. Incomplete static models and bounded executions do not justify universal runtime claims.

## 6. Learned utility and experiment value

Start with a task-conditioned score `s_theta(candidate,q,view)` rather than asserting a metric `d_theta`. A possible retrieval objective is expected independently scored task utility minus token/latency costs, subject to evidence, access, and budget constraints. Weights and outcome definitions must be preregistered or fit on training data, not selected after seeing held-out gains.

Logged use is biased toward items the current policy exposes. Record candidate set, chosen action, policy/version, selection propensity where defined, outcome/oracle, model, costs, and failed runs. [Doubly robust evaluation](https://arxiv.org/abs/1103.4601) offers a precedent, but needs support/overlap and valid nuisance-model or propensity assumptions. It does not recover effects of actions never tried by a deterministic policy.

For active discovery, a proposed experiment utility is expected reduction in uncertainty about a specified hypothesis or decision, minus experiment cost. Without a calibrated belief/outcome model this is a heuristic priority, not quantified information gain. Keep negative evidence, failed attempts, and inconclusive results; otherwise the graph learns survivorship bias rather than useful geometry.
