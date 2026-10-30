# Graph and runtime tool landscape

Reviewed 2026-10-30. This is a shortlist for workload-driven evaluation, not a performance ranking. Features below come from primary sources; deployment fit is a proposal. No tools were installed or benchmarked.

See the expanded [current project landscape](project-landscape.md) for SmartMemory, Cognee, Mem0, current Letta source, Neo4j Agent Memory, TencentDB Agent Memory, LightRAG, code-intelligence projects, and newer graph-guided context/KV research.

## Storage and graph processing

| Candidate | Source-backed capability | Proposed role and evaluation concern |
| --- | --- | --- |
| Neo4j | [Current search-index documentation](https://neo4j.com/docs/cypher-manual/current/indexes/search-indexes/) covers vector and full-text search alongside Cypher. | Server-backed graph baseline; test bounded traversals, updates, memory, and the chosen edition's operational/licensing constraints. |
| LadybugDB | [Project](https://github.com/LadybugDB/ladybug) and [getting started](https://docs.ladybugdb.com/get-started/) document an embedded graph system and persistent graphs. | Local embedded candidate; verify language bindings, durability, update behavior, extension support, and release stability. |
| FalkorDB | [Documentation](https://docs.falkordb.com/) describes property graphs, sparse matrices, Cypher extensions, and search indexes. | Server-backed candidate for graph-plus-search workloads; measure ingestion, traversal, resident memory, and concurrent updates. |
| GraphBLAS / SuiteSparse | [GraphBLAS Forum](https://graphblas.org/) specifies sparse algebra primitives for graph algorithms. | Candidate computational backend for diffusion and traversal, not a database or durability layer. |
| Simple relational adjacency baseline | Proposed node/edge tables, indexes, and bounded application traversal. | Necessary control to establish whether a dedicated graph engine is worth operating. Choose an actual engine and verify its docs before implementation. |

Do not equate Cypher support across products with identical semantics, planner quality, transaction behavior, or extensions. Pin a query subset and test answer equivalence.

A recent [cross-engine study](https://arxiv.org/abs/2609.23315) reports workload-dependent tradeoffs across query shapes, ingest, and updates. It is a recent preprint with its own synthetic biomedical workload and an evaluated system associated with its framing; inspect artifacts and conflicts before relying on its ranking. Its results do not select our database.

## Retrieval and memory frameworks

| Candidate | What to learn or reuse | Limitation to measure |
| --- | --- | --- |
| [Microsoft GraphRAG](https://microsoft.github.io/graphrag/query/overview/) | Local/global retrieval separation; community summaries. | Extraction/reindex cost, update freshness, and broad-summary latency. |
| [Graphiti](https://github.com/getzep/graphiti) | Incremental temporal memory and evidence reconciliation. | Entity resolution errors, contradiction handling, extraction model cost, and backend requirements. |
| [HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG) | Dense/sparse integration and PPR-based associative retrieval. | Factual versus multi-hop performance, index growth, and seed sensitivity. |
| [G-Retriever](https://github.com/XiaoxinHe/G-Retriever) | PCST retrieval and learned graph-to-LLM integration. | Training requirements, task transfer, encoder cost, and source traceability after compression. |

Use frameworks as baselines or replaceable components. Keep the project's evidence schema and invalidation contract independent of a framework's private index representation.

## Model runtime and interchange

| Candidate | Evidence | Proposed use |
| --- | --- | --- |
| vLLM | [Prefix-cache design](https://docs.vllm.ai/en/latest/design/prefix_caching/) | First candidate adapter for standard causal prompts and exact-prefix reuse. Inspect deployed code and token-input interfaces. |
| SGLang | [Paper](https://arxiv.org/abs/2312.07104) | Alternative runtime baseline for structured calls and radix-prefix reuse. |
| LMCache | [Documentation](https://docs.lmcache.ai/) | Evaluate cache offload/reuse after a single-runtime baseline works. Check exact runtime version compatibility. |
| CacheBlend | [Paper](https://arxiv.org/abs/2405.16444) | Separate non-prefix cache-fusion experiment with quality checks and fallback. |
| Apache Arrow | [Format](https://arrow.apache.org/docs/format/Columnar.html) | Candidate bulk graph/token interchange; zero-copy properties depend on the actual memory path. |

Binary metadata representation, RPC framing, shared-memory ownership, and GPU transport are separate choices. Do not assume Arrow, FlatBuffers, gRPC, shared memory, and RDMA form a turnkey zero-copy stack.

## Selection policy

Compare one simple storage baseline, one embedded graph candidate, and one server-backed candidate first. Use the same graph, update stream, and result checks. Measure import time, incremental update latency, p50/p95 query latency, memory, restart recovery, and total operating complexity.

Select a runtime separately based on model/hardware support and end-to-end latency. Verify each dependency's current license, maintenance activity, supported platforms, and pinned release before adoption; this pass is not a license audit. No claim is made that these are the only current tools or that the shortlist is exhaustive.
