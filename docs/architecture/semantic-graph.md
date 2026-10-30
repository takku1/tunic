# Tunic semantic graph contract

Status: proposed logical contract. No graph implementation, serialized schema, ontology, or storage engine is selected. This document owns durable evidence-state semantics. Research gaps/alternatives live in the [capability index](../research/semantic-graph/index.md); decision dispositions live in [decisions](decisions.md).

## Responsibility and records

The graph owns immutable source versions, source assertions, provenance, temporal history, identity decisions, and authorized snapshot reads. It exposes those records to retrieval/compilation without embedding model-specific token/KV identities in entity identity.

| Record | Required meaning |
| --- | --- |
| `SourceVersion` | Stable source identity, immutable revision/digest, original content or resolvable record, producer, access scope, and retention/deletion state. |
| `EvidenceSpan` | Source-version reference plus exact original byte range or defined structured-record pointer; transformation mappings when normalization changes coordinates. |
| `Entity` | Stable logical identity, type, and versioned aliases with explicit merge/split lineage. Similarity alone does not establish identity. |
| `Proposition` | Typed claim content, participant roles/qualifiers, and interpretation. It can be discussed without being asserted as true. |
| `Assertion` | A source/derivation asserting a proposition, with evidence, scope, temporal intervals, uncertainty/evidence status, and producer identity. |
| `DerivationRecord` | Named/versioned rule or transformation, input assertions/artifacts, parameters, output assertions, verification scope, and alternate supports. |
| `SnapshotReceipt` | Committed graph/schema/policy view and compatible index/coverage generations; explicitly reports lag or incomplete extraction. |

Names describe semantics, not required classes. Qualified relations/events retain their participant roles; a storage choice must not silently flatten away scope or assertion multiplicity. Literals preserve types, units, language, original lexical values, and unknown/null distinctions.

## Read and mutation semantics

Read operations are scoped by principal, source/predicate selection, valid time, transaction time, and coverage requirements. Authorization precedes traversal/ranking/aggregation. A result identifies the view it used; it cannot claim freshness or completeness absent matching receipts.

Mutations publish a consistent committed change or an explicit failure. Source capture, extraction, validation, indexing, and query readiness are distinguishable states. The implementation may use synchronous publication or explicit lagging indexes; the first slice must choose one and test it. An enrichment result for an obsolete source version cannot silently replace current evidence.

Assertions are immutable versions. Supersession, conflict resolution, and identity merges are new decisions, preserving original provenance. A correction is distinct from a real-world change. Valid and transaction time apply to assertions and qualified relationships; temporal paths require compatible time scopes. Unknown effective time remains unknown.

Retraction invalidates dependent views and derivations while preserving justified history where policy allows. If another valid support remains, a derived conclusion may survive. Required erasure/access revocation has a separate policy and can make retained artifacts inaccessible or require physical cleanup; preserving history is not a blanket retention mandate.

## Integrity and uncertainty

Every admitted assertion has original evidence or a named derivation with grounded premise closure. A cycle cannot create its own support. Extraction, identity-match, source-reliability, retrieval, and entailment scores remain separate. No arbitrary probability multiplication or universal confidence field determines factual truth.

Coverage records expose omissions, unsupported languages, unresolved references, extraction/index failures, and search bounds. Absence is asserted only for a declared complete scope/predicate/time view; otherwise report non-observation. Exact stable source keys are the first-slice identity baseline; uncertain links remain proposals rather than destructive merges.

## Consumers and errors

The compiler consumes immutable source/assertion records plus snapshot/coverage receipts. The measurement layer consumes authorized projections and cannot overwrite assertion authority. Model-specific lowering is downstream of the graph.

Logical outcomes include complete, partial, stale, unsupported, unauthorized, invalid, and failed, with access-safe diagnostics. These categories need a serialized error schema before code; they are not existing APIs. Cancellation or interrupted publication must not produce a validated partial snapshot. Recovery must restore a committed view or expose unknown readiness.

## Acceptance and remaining choices

Planned checks cover duplicate sources/assertions, alias ambiguity, merge reversal, backdated correction, temporal path mismatch, failed/stale enrichment, alternative support retraction, excluded-source negatives, concurrent snapshot visibility, and revocation after compilation. No checks have been executed against a Tunic engine.

The exact initial types/predicates, supported source languages, storage, transaction/index publication profile, canonical identity encoding, retention rules, and entailment policy remain open. Full recomputation is the reference; optimization must preserve these semantics before demonstrating resource gains.
