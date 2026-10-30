# Acquisition, resolution, evidence, and coverage

Scope: SG-05–SG-08 and conditional SG-26. Recommendations are Tunic proposals; implementation examples are precedent, not adopted code.

## SG-05 Extraction and readiness

**Gap:** no unified producer contract, failure taxonomy, publication state, or replay behavior for code/text extraction.

**Current concepts:** [DIAL-KG](https://arxiv.org/abs/2603.20059) separates initial extraction, adjudication, and schema evolution. SmartMemory's inspected wrapper distinguishes quick capture from core-owned background enrichment. [KGI-Bench](https://github.com/ScaDS/KGI-Bench) evaluates integration pipelines through coverage, correctness, and consistency rather than extraction accuracy alone.

**Minimum:** deterministic structured/code parsing where available; optional model extraction as a labeled producer. Record input digest, producer/version, parameters, schema version, scope, progress, failures, and output IDs. Distinguish saved, extracted, validated, indexed, and query-ready states. Reprocessing the same job must not accidentally duplicate assertions.

**Check:** crash between capture and enrichment, replay an input, update a source while enrichment is queued, and reject outputs for an obsolete revision. Score construction cost and publication lag as well as precision/recall.

## SG-06 Entity resolution

**Gap:** exact stable IDs are easy; aliases, renamed code symbols, ambiguous people/projects, and reversible clustering are not specified.

**Current concepts:** [Entity Resolution in Practice](https://arxiv.org/abs/2607.26298) emphasizes diverse candidate retrieval, independent precision safeguards, and rechecking cross-cluster merges instead of assuming pairwise match transitivity is safe. [SNAP-KG](https://arxiv.org/abs/2608.25149) studies inductive community assignment to reduce candidate search; it is a candidate-scope mechanism, not identity proof.

**Minimum:** exact source keys first; ambiguous matches remain proposals with reason, evidence, model/version, and scope. Keep aliases and same-entity hypotheses distinct from committed canonical identity. Merge/split operations retain lineage and trigger reindexing/invalidation. Avoid naive union-find over noisy similarity matches.

**Check:** A resembles B and B resembles C while A/C conflict. Inject one false bridge and measure cluster corruption. Split an erroneous merge and verify restored evidence, graph edges, and query results. Compare candidate recall separately from merge precision.

## SG-07 Evidence and lineage

**Gap:** source references exist in the IR draft, but exact support, extraction transformations, and obligation-to-span mapping need executable contracts.

**Current concepts:** [PROV-O](https://www.w3.org/TR/prov-o/) supplies entity/activity/agent provenance vocabulary. [LineageRAG](https://arxiv.org/html/2608.16004v1) retains query evidence demands through discovery and source grounding. Its selected audit section distinguishes partial support and wrong-demand links; grounded spans are not automatically sufficient.

**Minimum:** store original evidence and an extraction/derivation record; preserve query obligation identity through selection into the IR. Exact-span validation is deterministic. Entailment remains a separate checked or unknown status. Copied sources should retain common ancestry so repetition does not count as independent corroboration.

**Check:** a correct citation attached to the wrong claim, a partial quote omitting an exception, and three documents copied from one origin. Score byte fidelity, entailment, demand coverage, and independent source diversity separately.

## SG-08 Coverage and negative evidence

**Gap:** Tunic cannot infer absence from an empty result without defining what was scanned, resolved, indexed, and searched.

**Current concepts:** [OWL 2's open-world treatment](https://www.w3.org/TR/owl2-primer/) is a useful semantics baseline: missing information is not generally false. KGI-Bench's coverage dimension and the old GraphGraph abstention failures motivate explicit completeness accounting. No recent paper removes this obligation.

**Minimum:** a coverage receipt with source snapshot, included/excluded paths, parser/language limits, failed/truncated records, unresolved links, index readiness, and search bounds. Permit closed-world negatives only for an explicitly complete predicate/scope/time query. Otherwise report “not observed within this coverage,” not universal absence.

**Check:** the only caller sits in an excluded file, unsupported language, stale index, or unresolved dynamic dispatch. A negative answer must change accordingly. A new matching record invalidates the old negative result even when every previously selected node is unchanged.

## SG-26 Multimodal grounding

**Gap:** no image-region, table-cell, audio/video time-range, OCR, or alignment contract. Conditional on adding these inputs.

**Current concepts:** [MKG-RAG-Bench](https://arxiv.org/abs/2606.26458) isolates retrieval over multimodal graphs; [evidence-grounded lecture KG construction](https://arxiv.org/abs/2608.03161) ties extracted concepts to transcript/OCR/visual evidence, but its retrieval test is preliminary and tiny.

**Minimum when needed:** immutable media identity, original coordinates/timebase, extraction confidence, alignment mappings, and cross-modal support links. Retain OCR/transcript as derived evidence rather than replacing the original.

**Check:** contradicting caption and diagram, shifted video timestamp, repeated table header, and OCR decimal error. Evaluate alignment and supporting-region retrieval separately from generated answers. Keep this outside the first code/text slice unless a real workload requires it.
