# Knowledge policy survey: what Tunic borrows, what it does not adopt

Question driving this note: what rules determine whether information
deserves to become knowledge, remain knowledge, be retrieved, or be
trusted by an agent? Retrieval, memory, graph standards, and policy
engines each answer part of it. Tunic borrows semantics from several
disciplines rather than adopting any single RAG framework wholesale.
No new runtime dependency follows from this survey.

## 1. RAG and retrieval: LlamaIndex, Haystack, GraphRAG

Borrow: staged retrieval pipelines, reranking between discovery and
delivery, and evaluation discipline (frozen tasks, ablations, negative
queries). Do not adopt: framework-owned chunk stores or black-box
retrievers. Tunic's lexical/semantic/graph stages stay explicit and
independently measurable, so a weak stage can be identified instead of
hidden inside a pipeline object.

## 2. Agent memory: Mem0, Graphiti, Letta, LangGraph

Borrow: the episodic/semantic/procedural split for memory kinds, and
Graphiti's treatment of facts as temporal (supersession as first-class
history, never silent overwrite). Tunic already keeps supersession and
contradiction as preserved evidence. Do not adopt: opaque memory APIs
whose promotion rules an agent cannot inspect. Every Tunic state change
goes through explicit review/promote with reasons.

## 3. Knowledge representation: RDF, OWL, SHACL, PROV-O

Borrow semantics only; no RDF migration. From PROV-O: entities,
activities, and agents as the provenance triple — every assertion
already carries source, producer, and snapshots. From SHACL: shapes as
validation constraints over the graph (closed-world checks Tunic can
run at ingest and refresh: no dangling supports, no silent merges,
status vocabulary conformance). OWL-style open-world inference is
explicitly out of scope: Tunic never invents edges by reasoning.

## 4. Policy and authorization: OPA/Rego, Cedar, OpenFGA

Design reference order: Cedar first, OPA second. Cedar's
principal/action/resource/context model maps directly onto an agent
requesting evidence from a repository, with explicit permits, explicit
forbids, and default-deny. Compare with OPA when Tunic needs policies
beyond authorization (retention, budgets, routing). Neither engine is a
dependency today; the contract (see
[policy-contract](../architecture/policy-contract.md)) is written so a
Cedar-shaped evaluator can plug in later without changing call sites.
Current status: single trusted principal, workspace confinement, no
ambient authority. Multi-user authorization is open work.

## 5. Context engineering: MCP, typed IR, structured contracts

Already Tunic's home ground. The MCP boundary, typed context IR, and
selection receipts are the delivery mechanism the other five areas
constrain. Borrow from MCP practice: stable schemas, explicit error
behavior, capability advertisement. Nothing here changes the wire
format; policy decisions travel as reasoned receipts, not hidden flags.

## 6. Evaluation and governance: RAGAS, DeepEval, NIST AI RMF, OWASP LLM

Borrow DeepEval's separation of contextual precision, contextual
recall, relevancy, and answer faithfulness — it names exactly the
finding-vs-cleanliness distinction Tunic's arena measures (Recall@k
and MRR for finding; F1, abstention calibration, and evidence-edge
precision for cleanliness). Borrow the OWASP GenAI risk list as a
future security benchmark family: prompt injection, memory/data
poisoning, vector weaknesses, excessive agency, sensitive disclosure.
NIST AI RMF governs release process, not code. None of these are test
dependencies; the arena implements its own frozen tasks.

## What not to build

One giant `policy.py` deciding everything. Policy splits into five
independent decisions (admission, qualification, authorization,
selection, runtime) with separate owners, reviewed in the contract
note. A cursed conditional ball of logic would couple trust to
retrieval scores — exactly the failure mode the contract forbids.
