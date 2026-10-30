# Time, conflict, uncertainty, and inference

Scope: SG-09–SG-12. These define what graph assertions mean under change. They are separate from embedding geometry and retrieval score.

## SG-09 Temporal state

**Gap:** initial bitemporal fields exist in the proposal; temporal joins, edge validity, correction semantics, and uncertain time remain undefined.

**Current concepts:** [Zep](https://arxiv.org/abs/2501.13956) is temporal-memory precedent. [ChronoQG](https://arxiv.org/abs/2607.14770) explicitly evaluates preservation of temporal constraints over support subgraphs; question generation is not the same task as Tunic's temporal answering. [PrimeKG-CL](https://arxiv.org/abs/2605.10529) distinguishes added, persistent, and removed knowledge in evolving graphs.

**Minimum:** independent valid/transaction intervals with open/unknown bounds; versioned nodes and assertions; distinguish correction, real-world change, and observation time. Temporal query operators specify intersection/ordering, not vague “recentness.” History-preserving supersession is separate from access revocation or required physical erasure.

**Check:** a backdated correction, overlap, unknown date, deletion/retraction, and a path whose edges never coexisted. Evaluate valid-time and known-at-time questions separately; do not reward retaining deprecated facts as successful memory.

## SG-10 Conflict and belief revision

**Gap:** contradictory claims can be retained, but scope/authority rules and answer-view resolution are unspecified.

**Current concepts:** [Kontrast](https://arxiv.org/abs/2607.25959) distinguishes genuine conflicts, granularity differences, temporal mismatch, and incomplete KG structure when comparing text/tables/graphs. SmartMemory's documented assertion challenging offers a staged detection pattern, but not proof of a correct verdict.

**Minimum:** keep assertions and conflict records with source, time, scope, and authority. Publish a versioned resolution decision with reason and alternatives. A globally newest assertion is not automatically most authoritative. Scope/time differences can make apparently contradictory claims compatible; a noisy extractor must not overwrite trusted source history.

**Check:** conflicting sources, a correction misclassified as migration, source-priority change, and a conflict later resolved by new evidence. Resolution and historical answers must be reproducible under the recorded policy.

## SG-11 Uncertainty and abstention

**Gap:** optional confidence metadata is not a calibrated inference system.

**Current concepts:** Zhu's October 2026 [doctoral dissertation](https://arxiv.org/abs/2610.06974) separates input-knowledge, training/algorithmic, and predictive uncertainty in KG embeddings, including conformal-set approaches. Only its abstract/metadata were read in this sweep, not all 255 pages. Such coverage claims rely on the method's statistical assumptions and do not transfer directly to graph-RAG answers under drift.

**Minimum:** distinguish extractor confidence, entity-match score, source reliability, retrieval score, entailment judgment, and prediction uncertainty. Unknown remains explicit. Do not multiply arbitrary scores into a truth probability. Start with supported/unsupported/ambiguous outcomes and a labeled abstention policy.

**Check:** reliability curves and selective accuracy on held-out tasks; perturb source distribution and compare stale versus recalibrated estimates. A frequent claim or high-PPR hub must not gain factual certainty merely from being retrieved repeatedly.

## SG-12 Inference and truth maintenance

**Gap:** Tunic lacks a rule language, closed/open-world profile, support tracking, and semantics for retracting derived conclusions.

**Established/current concepts:** [SHACL inference-rule work](https://www.w3.org/TR/shacl-inference-rules/) is evolving and must be treated as draft work, distinct from shape validation. [HAPPI](https://arxiv.org/abs/2108.07758) provides older but relevant provenance-based maintenance for uncertain graph-query results. [provenance-guided concept revision](https://arxiv.org/abs/2608.23893) studies using explicit definition changes to localize repair.

**Minimum:** a bounded, versioned derivation profile with named rules and explicit premises. Keep asserted and derived claims distinct. Track alternate justifications; remove a conclusion only when its remaining justification no longer satisfies the rule. Bound recursion and define behavior for cycles and negation. Predicted links are hypotheses until independently accepted.

**Check:** two independent supports, removal of one/all, a cyclic unsupported derivation, a rule revision, and a new fact that invalidates negation. Compare incremental conclusions with full materialization. Do not claim general theorem proving or probabilistic soundness from recording a derivation trace.

**Decision:** preserve history and uncertainty now; add expressive or learned reasoning only after a small derivation profile has correct retraction behavior and measurable task value.
