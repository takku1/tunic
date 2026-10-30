# Current graph and memory projects

Research date: 2026-10-30. This expands the [database/runtime shortlist](tools.md) into current agent-memory and code-intelligence projects. “Top” means relevant architectural references selected for comparison, not a verified popularity, quality, or speed ranking.

Method: inspect primary GitHub pages, current README content, official documentation, and selected academic methods. No external project was installed or benchmarked. Repository default branches are moving references, not pinned reproducibility artifacts. Maintenance observations below identify explicit notices and current documentation; they are not an audit of commit cadence, CI health, or issue responsiveness.

## SmartMemory: distinguish the names

At least two different projects use this name. Do not attribute one project's implementation or maturity to the other.

**SmartMemory at smartmemory.ai:** its [official docs](https://docs.smartmemory.ai/) describe graph/vector memory with extraction and recall. [Installation docs](https://docs.smartmemory.ai/smartmemory/getting-started/installation/) distinguish local Lite mode using SQLite/usearch from a managed service. Follow-up inspection of the user-provided [public forge repository](https://git.smartmemory.ai/smartmemory/smart-memory) verified an app/integration source mirror, with separately packaged core and MCP dependencies. See the [source assessment](../references/smartmemory-audit.md) for concrete artifact, recall, and lifecycle findings; core algorithm internals remain outside this review.

**MauriceIsrael/SmartMemory:** a separate [GitHub repository](https://github.com/MauriceIsrael/SmartMemory) explicitly labels itself a proof of concept. It describes RDF facts, SPARQL/OWL rules, provenance, background deductions, and human validation for ambiguous claims. Useful as a symbolic-reasoning reference; its own experimental warning rules out treating its feature list as production validation. No affiliation with smartmemory.ai was established.

The detailed [SmartMemory concept assessment](smartmemory-concepts.md) separates the documented capabilities, caveats, and proposed experiments.

## Agent-memory projects

| Project | Primary-source observation | Idea to test / adoption caveat |
| --- | --- | --- |
| [Graphiti](https://github.com/getzep/graphiti) | Incremental temporal knowledge-graph memory. | Temporal assertions, entity reconciliation, and evolving relationships. Measure extraction cost and correction behavior. |
| [Cognee](https://github.com/topoteretes/cognee) | Current README describes persistent graph memory over documents, code, and conversations; local GLiNER-based extraction is available without an LLM key. | Pluggable extraction and typed domain models. “No API key” does not eliminate local model downloads, compute, or indexing costs. |
| [Mem0](https://github.com/mem0ai/mem0) | Current README describes an April 2026 algorithm using append-only extraction, entity linking, and multiple retrieval signals. It explicitly distinguishes managed-platform benchmark results from the open-source SDK. | Append evidence rather than destructively overwriting it; compare retrieval fusion. Do not import hosted benchmark scores into an OSS comparison. |
| [Neo4j Labs Agent Memory](https://github.com/neo4j-labs/agent-memory) | Conversation, entity/fact, and application-supplied reasoning records; documentation marks it experimental and not officially supported. | Explicit memory categories, entity deduplication, decision/tool traces, and audit links. Stored reasoning records are not access to a model's hidden reasoning. |
| [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) | README organizes reusable assets into chat memory, skills, wiki, and code graphs with team-oriented ownership and access concepts. | Separate asset kinds and choose task-specific context rather than retrieving everything into one prompt. Isolation and version behavior need direct testing. |
| [Letta Code](https://github.com/letta-ai/letta-code) | Stateful agent harness with memory and experience-driven changes. The [older Letta repository](https://github.com/letta-ai/letta) now points here and places the retired V1 server in an archive branch. | Agent-managed memory is an alternative orchestration strategy, not automatically a graph engine. Compare explicit memory operations against automatic extraction. Follow current source rather than old MemGPT-era setup assumptions. |
| [LightRAG](https://github.com/HKUDS/LightRAG) | Current README describes graph retrieval, selectable chunking, role-specific models, multimodal ingestion additions, and context-returning evaluation support. | Local/global retrieval and role-specific extraction/query models. Compare evidence retrieval separately from answer generation and multimodal parser cost. |
| [HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG) | Research implementation associated with PPR-based associative retrieval. | Diffusion is a candidate multi-hop mechanism; retain lexical/dense controls and query-stratified evaluation. |

These are complementary references. A memory lifecycle manager, an indexing pipeline, an agent harness, and a database cannot be scored as interchangeable products without specifying the layer under test.

## Code graphs and program analysis

**[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp):** the current README describes a local, SQLite-backed structural graph, Tree-sitter indexing, type-informed call resolution, search, documentation/infrastructure links, and incremental watching. It explicitly qualifies missing callers and documentation as coverage-dependent. Its architecture lists a native indexing/query backend rather than requiring a generative model for graph construction. Study resolver coverage, source receipts, and change-aware tests; parsing many grammars is not equivalent to precise resolution in every language.

**[GitNexus](https://github.com/abhigyanpatwari/GitNexus):** discovered as a code-intelligence candidate and inspected at repository-entry level. Its internal storage, resolution precision, query planning, license terms, and benchmark methodology were not verified here. Keep on the follow-up list rather than borrowing detailed architecture claims from secondary summaries.

**[Joern](https://github.com/joernio/joern):** code property graphs combine program views for static analysis. Its current README reports the v4 migration to flatgraph, superseding older OverflowDB descriptions. The [CPG specification](https://cpg.joern.io/) is a useful source for typed program relationships. A CPG provider could supply richer evidence than import/call graphs, but data/control-flow precision and ingestion cost must be evaluated per language and analysis configuration.

Proposed comparison: lightweight syntax graph, type-aware structural graph, and CPG-derived dependencies on identical repository tasks. Score resolved edge precision/recall, missing-language behavior, incremental cost, and answer quality. Avoid treating guessed calls as proven dependencies.

## New research closest to the model

**[ReCAP: Persistent Context Graphs for Efficient Memory Compaction](https://arxiv.org/abs/2609.40118), September 2026 preprint.** The method stores message/block importance and dependency links derived from prior attention, then combines them with new-request relevance for selection. Selected method sections describe moving-average importance and thresholded support links. It retains graph metadata independently of KV-cache lifetime. This is a strong conceptual match for the project. Its reported latency savings include estimated components; reproduction and attention-extraction overhead remain necessary. Attention-derived links are selection signals, not verified causal or factual dependencies.

**[CodeComp: Structural KV Cache Compression for Agentic Coding](https://arxiv.org/abs/2604.10235), April 2026 preprint.** Selected methods combine query-conditioned chunk filtering, CPG-derived structural scores, protected source spans, and attention-based KV selection. This makes graph information influence retained attention state without inventing a portable latent language. It is lossy state selection, not exact cache reuse. The perplexity filtering stage requires model evaluation and belongs in end-to-end cost accounting. Neither code availability nor an independently reproduced result was established in this pass.

**[Does Memory Need Graphs?](https://aclanthology.org/2026.acl-long.1232/), ACL 2026.** The paper compares memory-system stages under controlled experiments on LongMemEval and HaluMem. It emphasizes implementation details and strong baselines rather than treating a graph as a sufficient explanation for better results. Its [UnifiedMem repository](https://github.com/AvatarMemory/UnifiedMem) is referenced by the paper but was not source-audited here.

**[Graph-based Agent Memory survey](https://arxiv.org/abs/2602.05665), February 2026.** Useful taxonomy spanning extraction, storage, retrieval, and evolution; its [resource collection](https://github.com/DEEP-PolyU/Awesome-GraphMemory) is a discovery route, not evidence that each listed method works.

## Current concept priorities

The following ordering is a proposed research sequence, not a leaderboard:

1. **Evidence and revision:** provenance, temporal assertions, contradiction/supersession, reversible derived knowledge.
2. **Hybrid retrieval:** lexical and dense seeds, typed traversal/diffusion, reranking, explicit budgets, and coverage receipts.
3. **Multiple resolutions:** source spans, entities, events, summaries, and procedural assets linked through derivation edges.
4. **Code semantics:** syntax, type resolution, control/data dependencies, runtime observations, and their distinct confidence/coverage.
5. **Runtime-informed selection:** preserve attention-derived usage statistics and supporting context as an experimental signal.
6. **Model-state management:** exact-prefix caching first; separately test graph-guided lossy KV compression and non-prefix fusion.

A new design may combine a few of these. Adopting every memory taxonomy, graph algorithm, and runtime mechanism would recreate the old complexity before proving the core behavior.

## Evidence required before selecting dependencies

For shortlisted implementations, record a commit/tag, inspect the exact license, verify CI and recent maintainer responses, read ingestion/update/retrieval code, and run one representative local task with cost accounting. Pin model and extraction settings. Tests must cover deletion, conflicting facts, stale indexes, ownership filtering, and extraction failure.

This pass does not claim a full source audit or maintenance ranking. It deliberately records when evidence comes only from a README, docs, or paper.
