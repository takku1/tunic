# Trust, interoperability, and evaluation

Scope: SG-21–SG-24. Minimum trust and measurement contracts are required; distributed governance and sophisticated defenses depend on deployment. No external project integration is implemented.

## SG-21 Authorization

**Gap:** no principal/scope model, authorization enforcement point, derived-data policy, or revocation contract.

**Established concepts:** [ODRL 2.2](https://www.w3.org/TR/odrl-model/) is a policy-expression reference, not an authorization enforcement engine. Scope-safe source reads in the SmartMemory wrapper are useful local examples, but do not prove arbitrary derived graph isolation.

**Minimum:** define source/claim visibility, allowed writers, and policy revision identity even for a single local project. Filter before retrieval, traversal, aggregation, and model input. A summary, embedding, merge, or derived relation must not expose an inaccessible premise. Revalidate access on artifact/cache reuse; choose an explicit rule for mixed-policy derivations rather than assuming content hashes authorize reads.

**Check:** access revoked after compilation, hidden bridge entity, aggregate leakage, a shared summary, and cross-project alias collision. For a single-user first slice, declare that limitation; do not advertise tenant isolation based on workspace labels alone.

## SG-22 Adversarial evidence

**Gap:** source trust, graph mutation authority, and content-as-data boundaries are not formalized.

**Current research:** [BadGraph](https://www.usenix.org/conference/usenixsecurity26/presentation/yan-leiming), USENIX Security 2026, studies topological manipulation that hides useful evidence using neutral linker text. October's [GraphRAG-LTM](https://arxiv.org/abs/2610.19549) separates attacker access levels; [GuardRAG](https://arxiv.org/abs/2610.35071) proposes post-retrieval clustering/filtering for coordinated poisoning. The latter two are preprints and not universal defenses.

**Minimum:** untrusted documents cannot become execution instructions or modify ontology/trust policy by assertion. Record origin and mutation authority; preserve an independent lexical retrieval route and measurable source diversity. Keep co-retrieval reinforcement from silently changing authority.

**Check:** prompt injection in evidence, neutral hub/bridge flooding, copied assertions, malicious entity merges, and deletion of a bridge. Measure attack success and clean-task regressions; a defense that suppresses rare valid evidence can be harmful.

## SG-23 Interchange and federation

**Gap:** no export schema, namespace mapping, imported-evidence ownership, cross-store identity, or delete propagation contract.

**Current concepts:** RDF/PROV provide established interchange semantics; the SmartMemory source audit contributes frozen manifests and uncertain-operation receipts. AutoSchema is a conceptual reference for querying heterogeneous schemas, not compatibility evidence for Tunic.

**Minimum when needed:** export original source identities and claim/evidence semantics without token/runtime state. Version schemas; preserve unknown fields or reject loss explicitly. Separate imported claims from locally authoritative entities, infer no global identity from matching labels, and make source replacement/retraction visible to consumers.

**Check:** export/import without losing assertion multiplicity, temporal scope, uncertainty, or references; namespace collision; duplicate/late operations; orphan evidence; and out-of-order deletion. A mapping that drops qualifiers must report loss. History retention and required erasure need distinct policies across replicas.

## SG-24 Evaluation and economics

**Gap:** three workloads are designed, but fixtures, labels, runnable arms, and resource targets are missing.

**Current concepts:** [KGI-Bench](https://github.com/ScaDS/KGI-Bench) covers incremental integration quality. [GraphRAG-Bench](https://arxiv.org/abs/2506.05690) separates graph construction, retrieval, and generation questions. [Does Memory Need Graphs?](https://aclanthology.org/2026.acl-long.1232/) motivates controlled stage-wise baselines rather than graph/no-graph branding. Results from different datasets, answer models, and extraction settings do not form a reliable combined leaderboard.

**Minimum:** independent evidence oracles, versioned data/update traces, deterministic graph queries where possible, paired V/G/T arms from [workloads](../../evaluation/workloads.md), and verified model/template settings. Inspect coverage, graph error, selection, grounding, and answer failure separately. Diagnostics must distinguish stale, partial, unsupported, rejected, and failed outcomes.

**Check:** full-recompute differential tests, held-out tasks, source changes and retractions, conflict/absence cases, cold/warm runs, and failed requests. Report construction/extraction, update lag, retrieval/validation/lowering, model tokens/latency, cache reuse, and memory. Evaluate quality at fixed budget and cost at matched quality; include update frequency and amortization.

**Decision:** no new graph technique is accepted merely because it improves a synthetic metric or yields a prettier representation. Its gain must survive realistic errors, incremental changes, and total cost accounting.
