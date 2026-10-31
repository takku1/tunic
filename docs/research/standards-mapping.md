# Standards mapping: what Tunic implements, mapped to what it cites

These are standards-informed engineering requirements, not conformance
claims. Tunic is not certified against any of these specifications, and
citing one never substitutes for a frozen benchmark. Each row names the
concept borrowed, the Tunic machinery that implements it, and the test
that pins it. No new framework dependency follows from this mapping.

## Relevance table

| Standard | Relevance | Tunic implementation |
| -------- | --------- | -------------------- |
| ISO/IEC 25012 | Data and memory quality | Accuracy/completeness/consistency/traceability via evidence spans, snapshot identity, and the frozen precision splits |
| W3C PROV-O | Evidence provenance | Every assertion carries source, producer, and derivation; history preserved, never overwritten |
| W3C SHACL | Graph integrity | Closed-world validation: status vocabulary, no dangling supports, no silent merges, required evidence shapes |
| W3C RDF / OWL 2 | Semantic representation | Formal entity/relationship/identity semantics borrowed; no RDF storage, no open-world inference |
| ISO/IEC 5259 | AI data quality | Ingestion quality gates, dataset suitability notes, benchmark integrity (frozen manifests, no tuning on holdout) |
| NIST AI RMF | Reliability and trustworthiness | Unsupported-conclusion tests, unreliable-retrieval probes, misleading-context and failure-behavior coverage in the arena |
| OWASP LLM Top 10 | RAG and agent security | Poisoned-memory, prompt-injection-as-data, malicious retrieval content, and sensitive-leakage probes as a benchmark family |
| ISO/IEC 27001/27002 | Information security | Workspace confinement, default-deny posture, retention via immutable history, audit via receipts; multi-user access control open |
| ISO/IEC 42001 | AI governance | Responsibilities, risk assessment, monitoring, lifecycle controls; adopted as process, not code |
| MCP specification | Agent interoperability | Stable tool schemas, explicit errors, capability advertisement, authorization boundaries on the stdio server |

## Priority mapping (current development)

- P0 — PROV-O, SHACL, ISO/IEC 25012 → evidence graph, qualification gate, integrity tests.
- P0 — OWASP LLM Top 10, NIST AI RMF → retrieval safety, memory-poisoning probes, arena negatives and sweeps.
- P1 — ISO/IEC 5259, RDF/OWL → ingestion checks, semantic modeling discipline, evaluation design.
- P1 — ISO/IEC 27001/27002, MCP → agent access boundaries, security probes, interop conformance.
- P2 — ISO/IEC 42001 → governance maturity as the project approaches production use.

## Five concrete requirements and their pins

1. Every graph assertion traceable to original evidence or an explicitly
   recorded derivation → source ids resolve to stored bytes, spans stay
   in bounds; pinned by graph-integrity tests.
2. Every relationship satisfies structural and evidence constraints
   before becoming authoritative → the qualification gate plus SHACL-like
   shape checks; bare tokens stay candidates.
3. Retrieval distinguishes relevance from trust → semantic similarity
   proposes candidates only; only reviewed evidence becomes asserted;
   pinned by no-promotion tests and the precision/recall split.
4. Memory preserves temporal validity, contradictions, corrections, and
   uncertainty → supersession, contradicts preservation, conflict
   surfacing; pinned by the policy-contract tests.
5. Arena tests groundedness, poisoning resistance, false positives,
   false negatives, and unauthorized exposure — not just accuracy →
   MochiOS negative split, PyQuifer positive split, threshold sweeps,
   poisoning probes; scoreboard records all five families.

## References

- W3C PROV-O — https://www.w3.org/TR/prov-o/
- W3C SHACL — https://www.w3.org/TR/shacl/
- W3C OWL 2 — https://www.w3.org/TR/owl2-overview/
- ISO/IEC 25012 — https://www.iso.org/standard/35736.html
- NIST AI RMF — https://www.nist.gov/itl/ai-risk-management-framework
- OWASP LLM Top 10 — https://genai.owasp.org/llm-top-10/
- MCP Specification — https://modelcontextprotocol.io/specification
