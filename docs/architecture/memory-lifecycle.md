# Tunic memory lifecycle contract: consolidation + forgetting

Status: v1 target contract (2026-11-02). This document owns
consolidation/forgetting semantics. The [semantic graph
contract](semantic-graph.md) owns evidence-state semantics; SG-19
(summaries are views, never replacements) and SG-13 (atomic
publication) constrain this design. Research gaps live in the
[capability index](../research/semantic-graph/index.md).

## Definitions (binding for v1)

- **Consolidation** creates a compact, derived retrieval representation
  of redundant corroboration while retaining every authoritative source
  row. A summary is a view: it resolves to its exact input assertions
  and never becomes independently verified evidence.
- **Forgetting** (v1) is reversible, policy-governed **retrieval
  suppression**, not physical deletion. A suppressed assertion stays in
  storage (snapshots, manifests, audit ledger) but contributes to no
  traversal, selection, claim/mention rendering, timeline, or successor
  reasoning. Physical erasure is explicitly out of scope: SQLite rows,
  snapshots, and backups make secure-deletion claims unprovable here,
  and v1 makes none.

## Ownership

| State | Owner | Readers |
| --- | --- | --- |
| Assertion/snapshot rows | `tunic/store.py` | compiler, selection, lifecycle |
| `consolidated_supports` derived rows | `tunic/lifecycle.py` (derivation), `store.py` (table) | `summarize_supports()` only |
| `suppressions` tombstone ledger | `tunic/lifecycle.py` (writes), `store.py` (table) | enforcement helpers + `review_suppressions()` |
| Suppression enforcement | `selection.py` + `compiler.py` via `lifecycle.suppressed_ids()` (single source) | answer path |

`suppressed_ids()` is the single source: every liveness, traversal,
and rendering rule consults it. No second suppression list exists.

## Consolidation rules (v1)

1. Eligible: one proposition `(relation, caller, callee)` with >= 2
   **asserted** supports. Proposed, contradicted-unresolved, and
   retracted supports never consolidate.
2. Governing assertions (`supersedes`, `contradicts`, `retracts`)
   never consolidate away; they stay live and keep governing. A
   summary records governing flags but does not absorb them.
3. Summary rows carry status `derived_consolidated` and are excluded
   from support-minus-retraction arithmetic, traversal, timeline
   evidence, and selection corroboration.
4. Derivation is deterministic and content-addressed: re-running on the
   same snapshot yields identical rows (idempotent). Summaries record
   `derived_snapshot`, input assertion ids, rule version, support
   count, source ids, authorities, and temporal bounds.
5. Summaries are valid only for their `derived_snapshot`. On a new
   snapshot they are stale: `summarize_supports()` reports stale and
   falls back to a live scan until reconsolidation.
6. v1 consolidation is **additive**: the `answer()` path is untouched
   (byte-equivalence tested). The measurable win is on the new
   `summarize_supports()` read path (bytes + milliseconds vs a full
   support scan), not on answer output. Wiring summaries into the hot
   path needs its own equivalence gate (future work, not v1).

## Forgetting rules (v1)

1. Scope is narrow: explicit assertion ids only. No predicate, bulk, or
   fuzzy suppression (fail closed on ambiguous scope).
2. `suppress()` requires a non-empty policy and reason and the `local`
   principal. Unknown ids, empty policy/reason, and governing
   assertions (`supersedes`, `contradicts`, `retracts`) are refused
   with no partial writes. Refusing a governing assertion reports the
   affected endpoints to the caller (their own action, not a leak).
3. Suppressed assertions are invisible to traversal, selection,
   claims/mentions, timeline, successor reasoning, and container
   expansion (which inherits post-selection filtering). Each
   tombstone names an explicit `covers` cascade computed once at
   suppress time: the named id, every assertion with an identical
   (relation, caller, callee) triple (any status), and every
   text_mention from a suppressed source document naming a suppressed
   endpoint. Later assertions are never silently absorbed (an edited
   source reasserting the triple is new visible evidence).
4. Tombstones record assertion id, covers (opaque content-hash ids),
   policy, reason, actor, timestamp, evidence snapshot, and status
   (`active`/`reversed`) -- never caller/callee/proposition content.
   `review_suppressions()` exposes ledger metadata only.
5. Static code observations with different callers (e.g. a call-site
   candidate) are independent evidence, not the suppressed reviewer
   judgment, and stay visible with their source excerpts. Suppression
   removes the governed relationship and its twins, not the
   independently observed code that motivated review.
5. Suppression survives refresh, re-ingest, reconsolidation, and
   restart (keyed by stable content-addressed assertion id).
6. An edited source reasserting the same triple creates a **new**
   assertion id: it is visible as new evidence, and the old tombstone
   stays in the ledger. Reassertion after edit is new evidence, not
   resurrection.
7. `unsuppress()` restores visibility and records the reversal with
   reason. Double-suppress is idempotent.
8. No per-symbol suppression disclosure in standard APIs (not even
   counts): omission is silent; audit lives in `review_suppressions()`.

## Acceptance criteria (predeclared 2026-11-02)

Consolidation: C1 `answer()` byte-identical before/after on all
fixture tasks. C2 `summarize_supports()` covers every eligible
proposition with exact source links at fewer bytes than the full
support scan. C3 derived rows excluded from supports arithmetic and
traversal (unit test). C4 idempotent re-run. C5 restart-safe.
C6 stale after a new snapshot (live fallback until reconsolidation).
C7 temporal/selection/scheduler/reuse regressions green.

Forgetting: F1 suppressed edge absent from related/impact/callees,
claims, mentions, timeline, selection. F2 audit exposes metadata only.
F3 fail-closed refusals (unknown id, empty policy/reason, governing
assertion) with no partial writes. F4 suppression survives
refresh/re-ingest/reconsolidation/restart. F5 edited-source
reassertion is new and visible; old tombstone retained. F6
unsuppress restores + records reversal; double-suppress idempotent.
F7 all unrelated tasks byte-identical (false forgetting 0). F8
storage row counts reported; no space-reclamation claim.

Benchmark: long-history fixture (evolving assertions, corrections,
supersessions, contradictions, repeats, decoys) with current +
historical tasks, stale-resurrection 0, negative controls
(ineligible unconsolidated; unrelated scope unaffected).
