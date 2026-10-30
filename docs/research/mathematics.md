# Tunic mathematical foundations and candidate objectives

These equations define a proposed design space. They do not establish empirical superiority. All latency and quality tradeoffs need calibration on the actual workload.

The [geometry assessment](geometry/mathematics.md) adds explicit domains and counterexamples for negative-log path costs, information measures, resistance, curvature, causal effects, and learned utility. The [measurement contract](geometry/README.md) keeps these distinct from evidence authority; the [experiment plan](geometry/experiments.md) defines comparisons with simpler controls.

## Evidence closure and incremental correctness

For selected claims C, let D(c) be the required premises of c and E(c) its immutable source references. A validated artifact requires each selected c to have resolvable authorized evidence or an explicit derivation whose premise closure is grounded. This validates trace structure; factual truth and semantic entailment require additional assumptions or checks. A dependency cycle alone supplies no grounding.

Let `compile(q,G,p)` denote deterministic logical compilation for query q, graph snapshot G, and policy p. An incremental implementation must satisfy `incremental(q,G,delta,p) = compile(q,apply(G,delta),p)` at the logical artifact level under the same declared tie-breaking and failure semantics. It need not reproduce timing or cache residency.

Reuse is safe when recorded positive and negative selection dependencies establish that delta cannot affect the result. An unchanged selected-node set before the update is insufficient evidence: a new candidate can outrank it. When independence cannot be certified, recompute selection. See [IR semantics](../architecture/context-ir.md) and [workload oracles](../evaluation/workloads.md).

## Typed temporal evidence graph

Let `G=(V,E,tau_V,tau_E)` be a directed property multigraph. Distinct edge types preserve `calls`, `imports`, `supports`, `contradicts`, `derived_from`, and `supersedes`. An edge assertion carries its evidence and origin; an extracted claim is not automatically a verified fact.

Represent each assertion with a valid-time interval `[v_start,v_end)` and a transaction-time interval `[t_start,t_end)`. The first asks when the assertion held in the represented world; the second asks when the system recorded that version. Unknown validity remains explicit. Source updates generate new versions and invalidate derived artifacts through recorded dependencies.

Higher-arity relations can be represented as event/claim nodes with typed participant edges. Start there before introducing native hypergraphs: preserve the relation's role structure and measure whether a more elaborate representation is justified. Temporal agent-memory motivation: [Zep](https://arxiv.org/abs/2501.13956).

## Retrieval, diffusion, and sparse algebra

Lexical retrieval protects exact identifiers; dense retrieval covers paraphrases. Fuse ranked lists with a candidate reciprocal-rank score:

`RRF(v) = sum_j w_j / (k + rank_j(v))`

Absent candidates contribute zero. Tune `k` and weights on held-out queries; this formula is a proposed baseline, not a calibrated probability.

For query-seeded diffusion, let P be a row-stochastic transition matrix and s a normalized nonnegative seed vector. Personalized PageRank solves:

`p = alpha*s + (1-alpha)*transpose(P)*p`, with `0 < alpha <= 1`.

Normalize eligible outgoing weights; redistribute dangling mass using s so mass is conserved. Stop iteration at a specified L1 residual or an iteration/work cap. A sparse iteration costs O(|E|+|V|) over the chosen working graph. Prefer bounded candidate expansion or local approximations when a whole-graph pass is too costly.

Typed transitions may use `P = sum_r beta_r*P_r` with nonnegative weights summing to one, provided each P_r handles missing outgoing edges consistently. Filter unauthorized and temporally ineligible nodes before propagation; filtering only the final answer can leak inaccessible structure into ranking.

PPR is supported as a retrieval direction by [HippoRAG 2](https://arxiv.org/abs/2502.14802). It measures graph accessibility from seeds, not factual confidence or proof of a dependency. High-degree hubs and incorrect extracted edges can dominate results.

[GraphBLAS](https://graphblas.org/) expresses traversal through sparse algebra. Boolean OR/AND supports reachability; min/plus supports shortest-path relaxation. Preserve relation-specific matrices rather than densifying an N by N by R tensor. Sparse matrix form can accelerate graph operations without creating a meaningful neural latent representation.

## Selecting an evidence subgraph

One connected retrieval candidate is prize-collecting Steiner tree selection:

`maximize sum_(v in V_H) prize_q(v) - lambda*sum_(e in E_H) cost(e)`

over connected subgraphs H, with an optional root constraint. [G-Retriever](https://arxiv.org/abs/2402.07630) uses a PCST formulation. Exact combinatorial selection can be expensive; solver approximations and candidate pruning need explicit budgets. A single connected component may be inappropriate for multiple independent evidence chains.

A separate budgeted coverage candidate is:

`F(S) = sum_i w_i * max_(v in S) similarity(i,v)`

with `F(empty)=0`, nonnegative bounded similarity, and `sum_(v in S) token_cost(v) <= B`. This facility-location-style objective has diminishing returns under those assumptions. Greedy selection is a practical heuristic; do not assume its familiar cardinality guarantees transfer unchanged to arbitrary token budgets, edge dependencies, or cache rewards.

Compare bounded BFS, PPR ranking, coverage selection, and PCST on the same candidate pool. Graph sophistication earns its cost through measured evidence recall and task success.

## Compilation as constrained optimization

Choose selected evidence S and an ordering pi to minimize a proposed loss:

`L = expected_task_error(S,pi) + lambda*latency(S,pi) + mu*memory(S,pi)`

subject to token limits, authorization, version consistency, and required dependency inclusion. Do not estimate task error solely from cache-hit ratio.

Partition placement into stable instructions, reusable evidence groups, volatile context, and the current request. This is a hypothesis to compare against relevance-first placement. A deterministic sort maximizes reproducibility, but does not by itself maximize either quality or prefix reuse.

Use precedence constraints only for actual rendering dependencies. General evidence graphs can contain cycles: collapse strongly connected components in that dependency projection or use explicit cycle-safe references. Do not topologically sort every semantic edge.

## Attention and cache identity

For a conventional attention head:

`Attention(Q,K,V) = softmax(Q*K^T/sqrt(d_k) + M)*V`.

M supplies causal or other attention restrictions. Altering M changes model computation. The result does not automatically implement symbolic graph inference.

At layer l and position i, K and V are computed from layer inputs that generally depend on previous positions. Consequently, the same chunk after different prefixes generally has different KV state.

Proposed abstract block identity:

`h_i = H(domain, execution_fingerprint, h_(i-1), exact_block_token_ids, auxiliary_inputs)`.

The execution fingerprint covers weights, adapters, tokenizer/template contract, positions, mask policy, and cache representation. Auxiliary inputs include multimodal identities when relevant. This mirrors the prefix-dependent principle in [vLLM](https://docs.vllm.ai/en/latest/design/prefix_caching/); adapters must respect a backend's real keying and compatibility rules.

Content-chunk hash and h_i have different purposes. If prefix A changes to B, an unchanged suffix chunk retains its content identity but generally loses exact KV compatibility.

## Memory and transfer economics

For a conventional full-attention transformer with uniform cache layout:

`KV_bytes approximately 2 * L * T * H_kv * d_head * bytes_per_element`.

The factor two accounts for keys and values. GQA/MQA reduce H_kv; sliding-window, hybrid, compressed, sharded, or quantized caches need their actual layout model. Include allocator overhead and replication separately.

Transfer is beneficial only when:

`lookup + fetch + conversion + synchronization + residual_prefill < full_prefill`.

This excludes neither network bottlenecks nor recomputation costs. Cache misses, eviction, and failure recovery must be included in the expected latency, not hidden behind a warm-cache average.
