# Frontier Ledger: remembering what Tunic cannot yet understand

Status: v1 implemented (ledger table, record/list/transition with
dedupe, answer-abstention hook, commit_proposal refusal filing,
ingest-diagnostic auto-wiring, structured proof receipts for resolution,
replay with per-kind rules, regression export, `retrieval_miss` records
with rank-1 replay predicates; `frontier` CLI reads plus `--replay`,
`--export-regression`, `--record-miss`). Triage v1 implemented
(read-only prioritization: clustering, three queues, heuristic ranking
with sensitivity passes, `triage` CLI; see "Triage" below).
Demonstrated: S2
open→resolved cycle, PyQuifer avalanche `retrieval_miss` closure with an
exported regression case (`benchmarks/frontier/closures/`). Zero-config
`tunic <path>` ingest implemented (v1.3, spec below). Pending:
investigation scheduling, triage ranking validated against a second
real-repo closure.
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
   Ingest-filed entries anchor on digest + line + byte offset (v1.4):
   two distinct calls on the same line must file as two observations —
   PyQuifer showed three `print()` calls on one line collapsing into a
   single entry with recurrence 3 after one ingest. Entries also carry
   the originating `path` for file-level triage clustering.
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

## Triage (v1, read-only)

`tunic triage --db <db> --root <root>` projects the ledger into
normalized clusters, three queues, and a ranked investigation list
without ever writing: it digests every frontier record before and after
analysis and raises on any mismatch. Scoring is the provisional
heuristic from the Prioritization section with observable factors only:
`P = (I^a * R^b * C^c) / E^d` (impact, recurrence-weighted occurrences,
closability under a supported replay predicate, reviewer-sized effort).
Four weighting passes (base, recall-heavy, effort-averse, impact-first)
report top-N overlap against base so ranking stability is visible, not
assumed.

Queues separate what Tunic can act on now from what it cannot:
`closable_now` (a supported replay predicate passes against current
evidence, e.g. an asserted edge already covers a dispatch table),
`investigable` (evidence or verification is reachable through existing
capability), and `blocked` (missing capability or external noise — a
stdlib call names no graph action). A blocked entry at the top of the
ranking generates a capability task, never a fabricated proof; the
recommendation field names the closest verifiable second closure or
states what it would take.

Hardening rules from the v1.2 review:
- Clusters whose entries are all terminal (resolved/dismissed) carry
  zero active occurrences: they stay in historical counts but are
  excluded from rankings, queues, and the recommendation — a closed
  cluster must not be recommended again.
- The recommendation selects from the full active cluster set ranked by
  the base weighting, never from the top-N display truncation, so a
  low-ranked but immediately closable cluster survives `--top 1`.
- Unresolved calls cluster by file, not by site or expression: at
  real-repo scale (PyQuifer: ~10.7k unresolved calls) per-site clusters
  are singletons, which flattens occurrence-weighted ranking and makes
  every sensitivity weighting identical (all overlap 1.0 — the pass
  then carries no information). File clusters keep every site visible
  as a member (`example_ids`) while occurrences aggregate into a
  ranking signal. Expression-level grouping was rejected: it merges
  unrelated causes; per-site grouping was rejected: no aggregation.
  A file cluster blocks only when EVERY open member target is an
  external stdlib symbol. Cluster keys surface in report rows and the
  recommendation (`key` field).
- The mutation check pairs the frontier-record digest with
  `PRAGMA data_version` before/after, so a write to any table by any
  connection fails closed, not just ledger tampering (review finding:
  a ledger-only digest does not prove the rest of the database was
  untouched).

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

Measured distinction (keep it explicit): triage-guided development is
demonstrated — Tunic surfaces, deduplicates, and prioritizes its own
uncertainty, and that ranking has now selected real defects and one
real-repo closure (`benchmarks/frontier/closures/pyquifer-attrdispatch-v1.json`).
Autonomous learning is NOT demonstrated: diagnosis, implementation,
testing, and promotion approval remain developer-owned. The ledger
feeds learning; it performs none of it.

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
  A fifth predicate, `candidate_present` (v1.5), replays authored
  extraction-gap closures: a live proposed `depends_on` candidate with
  the recorded (via, caller, callee) must exist — the fail-before /
  pass-after shape for extractor milestones (see
  `benchmarks/frontier/closures/pyquifer-attrdispatch-v1.json`).
- v1.1 (implemented): audited `retrieval_miss` records. A human audit
  files the verified target, observed rank, config, and snapshot for a
  complete-but-wrong answer; replay resolves only when the target ranks
  first under the recorded config, and the proof must pin the target
  span. First real-repo closure exported to
  `benchmarks/frontier/closures/avalanche-rank1-v1.json` (bare-name
  stage now matches snake_case against spaced prose; break-tested by
  zeroing the name budget). `frontier --record-miss` files;
  `frontier_miss_summary` reports counts plus the false-confidence rate
  over audited misses. The `authority` field is an attested string, not
  proof a human performed the audit — keep that distinction explicit
  before introducing autonomous investigators.
- v1.2 (implemented): read-only triage over the ledger (see "Triage"
  above). Acceptance: clustering, three queues, four sensitivity
  weightings, ranked list with reasons, a recommendation that never
  fabricates proof for blocked entries, and a digest-anchored mutation
  check proven by a tamper test; `triage` CLI.
- v1.3 (implemented): zero-config ingest (see spec below) plus an
  index-backed dedupe anchor. Acceptance: `tunic <path>` with no
  subcommand resolves the workspace root, defaults the database to
  `<root>/.tunic/graph.sqlite` (`--db` overrides), ingests, and prints
  the summary: files, symbols, assertions by status, diagnostics by
  kind, and open frontier entries by kind; rerunning re-ingests and
  bumps recurrence instead of duplicating. Subcommand names are
  reserved: a workspace directory named like a command is parsed as
  that command, not a path. The dedupe anchor is a SQLite expression
  index over the record's dedupe field, so filing a diagnostic seeks
  instead of scanning every ledger row — at real-repository scale
  (thousands of open entries, one filing per unresolved call) the old
  full scan per filing was quadratic. Existing databases gain the index
  on next open (`CREATE INDEX IF NOT EXISTS`); record semantics are
  unchanged.
- v1.4 (implemented): dedupe anchor granularity (digest + line + byte
  offset) and file-level triage clustering, validated on the real
  PyQuifer repository — 10,906 entries collapse to 125 file clusters
  with a discriminating ranking (`benchmarks/frontier/pyquifer-triage-v1.json`).
  Acceptance: same-line calls file separately; cluster keys visible in
  report rows; blocked requires every member target external.
- v1.5 (implemented): known-receiver attribute-dispatch candidates
  (`known_receiver_method_candidate_v1`): a variable provably bound once
  to a resolvable constructor call, used after the binding, proposes a
  depends_on candidate with binding + constructor + call derivation;
  reassigned, shadowed, unknown, unknown-method, and use-before-binding
  receivers propose nothing. Second audited real-repo closure:
  `benchmarks/frontier/closures/pyquifer-attrdispatch-v1.json`
  (CausalGraph.interventional_graph -> add_edge/add_variable, verified
  by source inspection; `candidate_present` regression predicates replay
  green and fail on the frozen v1.4 baseline). Extended the same
  milestone: `self.attr = C()` assigned exactly once in `__init__`
  proves the receiver for `self.attr.method()` in any method of the
  class (binding precedes any method run; calls inside `__init__` must
  follow the assignment textually; every Store of the attribute counts
  toward the single-binding proof, call-valued or not — a non-call
  `self.x = C` is not a constructor binding). Real-repo yield: 83
  PyQuifer candidates (10,889 -> 10,806 unresolved), including
  MemoryConsolidationLoop.recall/consolidate -> DifferentiableMemoryBank
  .read/write. Scope discipline: third-party receivers stay unresolved
  (never guessed) and constructor calls are the separate
  class-extraction problem — neither is counted as an
  attribute-dispatch success.
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
