# Tunic Knowledge Policy Contract

Five versioned policy interfaces govern what becomes knowledge, stays
knowledge, reaches an agent, and may be acted on. Each decision returns
a reason and a receipt, never a bare boolean, so any decision replays
against a frozen snapshot and explains itself. Architectural rule:
policy constrains retrieval; it is never buried inside a retrieval
score. An unauthorized result with cosine similarity 0.99 stays denied.

## The five decisions

### 1. Admission — should this observation enter memory?

Owner: ingest and assisted-acquisition paths. Inputs: source bytes,
provenance, claimed authority. Output: admitted with retention and
trust metadata, or refused with reason. Current: workspace-confined
`note`/`record_outcome`, symlinks and escapes rejected, history
preserved. Open: per-source trust tiers and retention rules.

### 2. Qualification — verified relationship, hypothesis, or candidate?

Owner: the evidence gate ([evidence-qualification](evidence-qualification.md)).
Bare matches need an independent signal: same-document exact or claim
evidence, class context (frequency-independent), or low-frequency
dotted/call, file-path, or inventory-list syntax. Unqualified matches
persist as reviewable candidates. No stopword lists; frequencies are
corpus-relative. Status: implemented and benchmarked (MochiOS 0 FP;
PyQuifer dev refers recall 0.80).

### 3. Authorization and temporal validity — may this agent see it, now?

Owner: an explicit Cedar-shaped boundary (principal, action, resource,
context; explicit permits, explicit forbids, default-deny). Current:
single trusted principal plus workspace confinement; multi-user
authorization is OPEN, not silently assumed. Temporal validity is
implemented: supersession and valid-time filtering decide what is
current for the requested time.

### 4. Retrieval and context compilation — which authorized evidence fits?

Owner: the compiler over graph-owned views. Only authorized, current
evidence enters selection; relevance ranks within that set, never
overrides it. Budgets, thresholds, and abstention live here. Status:
implemented; the evidence-aware ranking layer is next and must keep
discovery (recall) separate from proof (precision).

### 5. Runtime and action — may the agent rely on it or act?

Owner: the MCP boundary and memory tools. Retrieved context is data,
never instruction: documents cannot promote themselves to facts
(promotion needs explicit approval), repeated retrieval never upgrades
a hypothesis, and memory writes stay inside the fixed root. Status:
single-principal; shared/multi-agent use needs the authorization
interface from §3 first.

## Testable questions (contract acceptance)

1. Can a semantically excellent match override an authorization denial?
   No — denial wins before ranking runs.
2. Can retrieved memory be relevant but historically invalid? Yes —
   superseded and contradicted records persist and are marked, never
   silently served as current.
3. Can two contradictory assertions both remain valid evidence records?
   Yes, until status and interpretation resolve them; traversal never
   treats a disputed edge as undisputed.
4. Can a model-generated summary become fact through repeated retrieval?
   No — only explicit review/promote changes status.
5. Can a document contain instructions the agent must treat as data?
   Yes — ingest never executes document content; documents propose,
   they do not command.

Questions 2–4 are enforced by current machinery and pinned by
`tests/test_policy_contract.py`. Question 1 is enforced at the
workspace boundary today and awaits the multi-principal interface for
the general case. Question 5 holds by construction (no execution path
takes document text as code) and is pinned the same way.

## Versioning

Interfaces are versioned independently. Changing a decision's inputs,
outputs, or defaults is a contract bump recorded in
[decisions](decisions.md) with paired before/after arena runs. Receipts
carry the contract version, the snapshot identity, and the inputs, so a
past decision can be replayed exactly.
