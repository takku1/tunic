# Tunic typed context IR

Status: proposed logical contract, not an implemented schema, binary format, or proven novelty. This document owns the IR semantics; [architecture](overview.md) owns component boundaries and [workloads](../evaluation/workloads.md) owns the planned comparisons.

## Purpose and choices

The context compiler produces a reusable typed artifact describing selected assertions and evidence. Model-specific lowering produces messages, tokens, position/mask policy, and execution fingerprints. This separates durable evidence meaning from a model's input representation.

The first contract favors faithful excerpts and explicit structured records. Rewriting, summarization, learned compression, and lossy KV retention remain separately identified transformations. Direct prompt emission remains the simpler comparison baseline. Revisit the IR choice if validation and metadata costs outweigh its measured benefits.

## Proposed records

| Record | Required meaning |
| --- | --- |
| `ContextUnit` | Immutable unit ID/version; kind; exact excerpt or typed claim; source/evidence references; validity/transaction intervals or explicit unknowns; uncertainty/evidence status; access scope. |
| `EvidenceRef` | Immutable source version and digest; exact byte span or structured-record path; extraction method/version; optional independent verification artifact. |
| `Derivation` | Input unit IDs; transformation name/version/parameters; output IDs; premise dependencies; verification status; whether information was omitted or meaning may have changed. |
| `ContextRelation` | Typed relationship between units, with provenance. Distinguish semantic, required-premise, contradiction, supersession, and usage-signal relations. |
| `SelectionReceipt` | Query and policy identity; snapshot/time/access selectors; candidate/index epochs and predicate scopes; selection reasons; excluded/truncated obligations; planning budgets. |
| `ContextArtifact` | IR version; ordered units/groups and relations; derivations; required obligations; content identity; reference to the validation/selection receipt. |

These are semantic field requirements, not prescribed class names or an API. Durable source/assertion records belong to the [semantic graph contract](semantic-graph.md). Compiler `Derivation` records reference durable derivation records where applicable and also describe compiler transformations; the two need not share a physical schema. The [runtime contract](runtime.md) owns the resulting `LoweringReceipt`.

A confidence score is optional and must identify its producer/calibration when present. Unknown confidence is not zero, and extraction confidence must not be represented as factual truth probability without supporting calibration.

## Invariants and failure behavior

Every included factual assertion must reference source evidence or an explicitly labeled derivation. Missing, inaccessible, corrupt, or unresolved evidence rejects the assertion from a validated artifact and produces a diagnostic; it cannot silently become a grounded fact. Explicitly typed hypotheses, constraints, and proposed transformations may also be included under the [measurement and research-object contract](measurements.md). Evidence of their origin does not establish the truth of their conclusions. Validation checks these distinctions rather than certifying every included statement as true.

Exact excerpts must match their source version and span. Generated paraphrases and summaries are derived units, even if they carry citations. A source reference establishes origin, not entailment. A rule derivation can establish a consequence only under its explicit premises and rule semantics; it does not verify those premises against the world. Model-generated entailment judgments remain recorded judgments.

Required premise closure is validated before lowering. Cyclic semantic references are permitted; a derivation cycle cannot bootstrap its own evidential authority. Resolve through a grounded premise or mark the result unsupported. Preserve contradictory or superseded assertions with their history and requested time semantics; the current answer view is a projection, not destructive replacement.

Authorization applies before retrieval/ranking and again when an artifact is reused. A changed policy or revoked source invalidates the relevant receipt even when content bytes match. Emit access-safe diagnostics without exposing protected claims.

An unfinished or cancelled compilation is not published as validated output. Readers see an immutable complete artifact plus receipt, or an explicit failure. On restart, derived artifacts can be rebuilt from source versions; no unverified cache entry is authoritative.

## Example: correction and supporting evidence

Suppose source revision S1 records `service A uses endpoint X`, and revision S2 corrects that to endpoint Y effective October 10. Store both assertions and both evidence references, plus explicit supersession. A request for current state selects Y. A request for October 5 selects according to valid time; a request for what was known October 5 also constrains transaction time. If the effective date is unknown, report that uncertainty instead of fabricating a date from ingestion time.

An optional summary saying “A migrated from X to Y” is a new derived claim, not an exact excerpt. Its derivation must retain both inputs and the temporal reasoning needed to support migration; supersession alone could represent a correction of an error rather than an actual migration.

## Incremental validity and identity

Maintain two distinct structures: an artifact dependency DAG for reusable deterministic transformations, and a query-selection dependency certificate. The DAG tracks source versions, extraction parameters, and derived units. The selection certificate also tracks candidate-set effects, negative/absence dependencies, index versions, scope, access policy, and time.

An unrelated update permits reuse only if the certificate can establish disjointness. Broad conceptual retrieval may depend on global ranking/index state; conservatively rerun selection when proof of independence is unavailable. Whole-graph invalidation is the safe control, not a failure of the design.

Hash stable payload separately from snapshot-validation receipts where their semantics allow it. A new snapshot can validate unchanged content without forcing a new payload identity. Conversely, equal selected text does not establish equal temporal/access validity or equal KV state.

## Runtime portability

No token IDs, model hidden states, device pointers, or KV tensors are authoritative semantic fields. A model-specific token artifact references the IR rather than redefining its claims. Different models may lower the same IR differently; required evidence and uncertainty labels remain preserved.

Model-independent representation does not mean model-independent selection: planning may use a target model's capacity budget. Reusing an artifact for another target requires that its obligations still fit. Exact rendering, budget failures, unsupported capabilities, and cache compatibility follow the [runtime contract](runtime.md); lowering cannot silently discard premises.

## Planned acceptance checks

Verify excerpt/digest integrity, complete premise closure, labeled lossy transformations, temporal history preservation, revoked-access rejection, and deterministic artifacts under fixed inputs. Compare incremental output against full recompilation after edits, insertions, deletions, and policy changes. Lower one artifact for two pinned models and verify independent input/cache identities. These checks are planned and unexecuted.
