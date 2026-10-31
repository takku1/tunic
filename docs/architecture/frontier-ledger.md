# Frontier Ledger: remembering what Tunic cannot yet understand

Status: v0 implemented (ledger table, record/list/transition, dedupe,
answer-abstention hook, commit_proposal refusal filing, `frontier` and
`candidates` CLI reads, S2 open→resolved cycle demonstrated). Pending:
ingest-diagnostic auto-wiring, zero-config `tunic <path>` ingest (spec
below, not built), investigation scheduling.
Motivation: the v4 acquisition benchmark (S2, 0/6 commits on every
protocol) proved models will not synthesize an edge through registry
indirection even with the evidence in context, and probes of real
repositories show most unresolved observations are currently discarded
silently. A memory system that forgets its failures cannot learn from
them.

## Non-goals

- Two buckets ("understood" vs "noise"). Unfamiliar-but-valuable,
  contradictory, and valid-but-irrelevant observations need different
  handling. Everything the ledger holds is *unresolved*; noise is one
  category with the lowest priority, not the default.
- Autonomous self-modification of parsers, schemas, or prompts. Learning
  a fact (knowledge acquisition), learning a method (procedural), and
  changing the system (improvement) follow different approval paths.
  The ledger feeds all three; it directly performs none.
- A second trusted graph. Frontier entries are never traversal targets
  for impact/claims and never render as evidence until promoted through
  the existing governed path (proposal, reviewer, commit).

## Classification (six categories, not mutually exclusive)

| Category       | Meaning                                            | Example                                              |
| -------------- | -------------------------------------------------- | ---------------------------------------------------- |
| Verified       | Supported, valid knowledge (lives in the graph)    | Static call with source span                         |
| Unresolved     | Insufficient evidence to decide                    | Abstained retrieval with partial candidates          |
| Novel          | Potentially new concept or relation                | Unknown symbol with corroborating mentions           |
| Contradictory  | Conflicting sources or claims                      | `contradicts` pair awaiting resolution               |
| Unsupported    | Parser or capability gap                           | Registry-dispatch edge the extractor cannot resolve  |
| Irrelevant     | Low-value or malformed input, preserved not purged | Boilerplate match with no task relevance             |

An observation may be novel, unresolved, and high-priority at once.
Raw observations stay available independently of classification (open-world
assumption: absent from the graph is not false).

## Record schema (v0)

```json
{
  "id": "frontier-0042",
  "kind": "unsupported",
  "status": "open",
  "source_refs": ["source-<sha>:<start>-<end>"],
  "task": "Resolve registry-based Python dispatch",
  "failure": "static_edge_unresolved",
  "candidate_explanations": [],
  "required_evidence": ["registry assignment", "verified runtime call"],
  "attempts": 0,
  "priority": 0.82,
  "promotion_policy": "review_required"
}
```

Lifecycle: `open -> investigating -> resolved | dismissed` (dismissed
requires a reason; history is preserved either way). Resolution must name
the promoting evidence or the capability change; replay of the original
failure is the acceptance check.

## v0 instrumentation (three sources, all already exist as seams)

1. Failed parsing: extractor diagnostics (`malformed_claims`,
   `invalid_claim`, `unknown_relation` in `tunic/extract.py`) become
   `unsupported` entries, deduped by source digest + kind + detail.
2. Retrieval abstention: `answer()` with outcome `empty` becomes an
   `unresolved` entry carrying the task text, deduped by task digest
   (an open entry for the same task suppresses duplicates; re-encounter
   bumps recurrence instead of inserting).
3. Rejected/conflicting proposals: the deterministic
   `Memory.reconcile_proposal()` classifier already returns
   ADD/DUPLICATE/CONFLICT/INVALID without mutating. A `commit_proposal()`
   wrapper (one-shot propose -> reconcile -> reviewer gate) files
   `conflict`/`invalid` outcomes as `contradictory`/`unsupported`
   entries instead of dropping them. This productizes the v4 condition-C
   path, which earned the default on cost and duplicate-prevention.

No model call is added by instrumentation; classification of the entry
itself is deterministic. A second model call happens only for genuine
ambiguity, mirroring the v4 conflict branch.

## Prioritization (heuristic, not calibrated)

```
Priority = (utility_gain * P(resolution)) / investigation_cost
```

Estimated from observable signals only: recurrence across tasks, number
of downstream dependents, failure severity, and kind base weight
(contradictory > unsupported > novel > unresolved > irrelevant).
Hard gates precede scoring: safety, authorization, privacy. A suspicious
instruction in untrusted text is preserved as hostile-content evidence,
never as an investigation the agent should obey. Model confidence is not
an input; independently measurable signals are.

## Promotion

- Knowledge: entry evidence satisfies `review_required` (tests, human,
  or oracle reviewer) -> normal `promote`/`record_outcome` path.
- Procedural: a reusable resolution method is recorded as a reviewed
  note with its scope, not as a code change.
- System: extractor/parser changes go through sandboxed development,
  regression tests, and approval. Never self-applied at runtime.

## Division of responsibilities

| Component       | Responsibility                                                      |
| --------------- | ------------------------------------------------------------------- |
| Tunic           | Trusted graph, provenance, retrieval, temporal history, reconcile   |
| Frontier ledger | Unresolved observations, failure categories, evidence requests      |
| MochiOS         | Schedules investigations, allocates resources, invokes tools/agents |
| Wilderbeast     | Candidate deeper program analysis for structural gaps               |
| Chili Crisp     | Candidate controlled optimization of extraction/retrieval           |
| Team Ops        | Candidate coordination of bounded, reviewed improvement workflows   |

The last three are integration roles, not existing interfaces.

## Zero-config ingest (specified with v0)

`python -m tunic <path>` with no subcommand must: resolve `<path>` as
the workspace root, default the database to `<root>/.tunic/graph.sqlite`
(overridable with `--db`), ingest, and print a summary: files, symbols,
assertions by status, diagnostics by kind, and open frontier entries by
kind. No manifest authoring, no repo-specific configuration. The summary's
frontier counts are the user-visible proof that unknowns are kept, not
dropped. A `frontier` subcommand lists open entries with reasons and
required evidence (read path for the future investigation scheduler).

## Milestones

- v0: ledger + three instrumentation sources + CLI summary. Acceptance:
  a previously silent failure (unresolved dispatch, abstained query,
  refused proposal) is retrievable with its reason and required evidence.
- v1 (implemented): structured proof receipts + regression export.
  Resolution requires a validated proof dict — pinned snapshot plus
  assertion ids, source spans, and/or passing test receipts, all
  referentially checked; free-text resolves fail closed. Resolved
  entries export to durable regression cases (`answer_nonempty`,
  `dispatch_covered` with support-minus-retraction arithmetic,
  `symbols_present`) re-checkable via `replay_regression_case`.
  Acceptance: resolve → export → replay passes; retract evidence →
  replay fails; `frontier --replay` / `--export-regression` on the CLI.
- Later: MochiOS-scheduled investigation, procedural learning records,
  sandboxed system-improvement proposals.

## Relation to existing contracts

- `policy-contract.md`: frontier writes are admission-policy decisions
  with receipts; promotion reuses authorization + verification gates.
- `evidence-selection.md`: the ledger never feeds selection; only
  promoted graph evidence does.
- `graph-schema.md`: frontier records are not graph entities; no schema
  migration touches them.
- V4 result `model-ab-multisession-v4.json`: S2/DUP/GATE/CORR are the
  first entries' worth of evidence for *why* the ledger exists.
