# Agent integration: Tunic as model-agnostic memory infrastructure

Status: design + deterministic harness, 2026-11-02; benchmark
backend generalized 2026-11-03. Tunic is used by agents; it is not
an agent. Hosted-model benchmark backends go through the generic
subscription-CLI backend (`benchmarks/agent/cli_backend.py`, factory
`ai_backend`): any tool-less print-mode assistant CLI is a preset
dict (argv builder, quota markers, identity), and `kimi-cli` is the
first preset, kept under its frozen artifact label. The kimi
subscription is quota-blocked and no Kimi runs are planned; frozen
ACQ v2 artifacts are preserved untouched. A Muse-as-judge loop stays
out of the evaluation plan: no assistant CLI exposes a stable
callable interface with reproducible task inputs. What follows is
the integration surface, the portable skill contract, and the
tool-using evaluation adapter that replaces model-in-the-loop
benchmarking.

## 1. Capability matrix (MCP tools)

Serve one stdio session per repository:
`python -m tunic --db <path>.sqlite mcp --root <repo>`.
Fixed root; caller paths are confined; only the `local` principal acts.

| Operation | Tool | Read / Write | Provenance | Budget / snapshot |
| --------- | ---- | ------------ | ---------- | ----------------- |
| Answer a task with fused evidence | `retrieve(task, limit?, policy?, threshold?, select?, snapshot?, container_limit?)` | Read | Candidates carry match/stages; impacts carry derivation.verification; claims carry authority/status; selection trace and container-expansion record when selected | `limit` + `threshold` + container budget; snapshot pinned or pinned-at-read; selection opt-in |
| Cross-source context for one symbol | `related(symbol, policy?)` | Read | Same as above | Policy only |
| Reverse-call impact | `impact(symbol, policy?, max_depth?, max_nodes?)` | Read | Witness edges only | Depth/node bounds |
| Valid-time view | `timeline(symbol, at?)` | Read | As-of cutoff | `at` date |
| Acquisition report for one document | `review(document)` | Read | Structured report | None |
| Append a session note | `note(text, document?)` | Write (needs `refresh` to publish) | Timestamped file append | None |
| Record one reviewed claim | `promote(...)` | Write (needs `refresh` to publish) | Authority recorded; duplicates/contradictions refused | Exact `path::symbol` endpoints only |
| Re-ingest the fixed root | `refresh()` | Write (publishes) | Returns new snapshot | Full re-ingest |
| List live proposed candidates | `candidates()` | Read | Derivation chains included | None |
| Commit one reviewed candidate | `acquire(candidate_id, justification, task?)` | Write + publishes | Governed receipt (reconcile/promote); empty justification and non-live ids refused | Bounded to one candidate |
| Record correction / retraction | `correct(subject, object, rel, ...)` | Write + publishes | `contradicts` / `retracts` (+`retract`) / `supersedes`, authority recorded | Exact endpoints only |
| Inspect unresolved knowledge | `frontier(status?, limit?)` | Read | Open/investigating/resolved/dismissed; reply reports `total` alongside the bounded sample | `limit` (default: all — pass one; triage-style ranking is a later milestone) |

Deliberate non-goals: no `select`/`container_limit` on `retrieve`
(fusion-only agent context; the evidence compiler stays
deterministic); no multi-user ACL (single principal by design);
`promote` stays record-only so batching through `refresh` keeps
working — the skill teaches the two-step.

## 2. Write-path security posture (reviewed 2026-11-02)

Trust boundaries: one local principal, one fixed root, one request at
a time. The stdio server is strictly sequential (`serve` reads one
line, handles one call), so concurrent tool calls cannot interleave.
Path confinement (`_workspace_path`) keeps reads, notes, and review
inside the root; caller-supplied `principal` anything but `local` is
denied before dispatch.

Authorization is layered, not single-point: `handle_call` authorizes
every tool call, and the underlying `Memory` methods authorize again
(`promote`, `record_outcome`, `commit_candidate`, `note`, `ingest`,
`refresh` all check the principal; writes funnel through `promote`,
so the check cannot be bypassed structurally).

Validation is strict and loud: exact `path::symbol` endpoints;
non-empty justification and live candidate ids for `acquire`;
closed relation vocabularies for `correct`/`promote` with `retract`
mandatory for retractions; non-negative budgets. Failures return
structured `isError` results (including `OSError`, so an ingest
failure can never kill the session silently). Tests pin: duplicate
`acquire` returns a no-op receipt (exactly one asserted edge, never
two); conflict files a `reconcile_conflict` frontier entry instead of
writing; publish failure leaves the claim block on disk with nothing
asserted (two-phase semantics: a later refresh publishes it — never
half-asserted, never lost silently); invalid snapshot/argument shapes
are refused. Replay tests assert these against actual store state,
not tool output text.

Accepted residual risks: single-writer assumption (same as the rest
of the system; the sequential server makes it structural for MCP);
claim-block-then-ingest is crash-consistent, not atomic; `promote`
stays record-only by design (batched `refresh`), so agents must
follow the skill's write-then-refresh rule.

## 3. Frontier: what the 1,805 open entries are

Measured on the live Tunic repo (18 sources, 198 symbols): 1,798
`ingest_unresolved_call` rows plus 7 `ingest_unresolved_dispatch`
rows — per-site raw diagnostics, mostly third-party imports, dynamic
dispatch, and unsupported syntax, not 1,805 defects. `triage.analyze`
collapses them to 24 active clusters (17 investigable site-clusters
with 1,809 occurrences, 7 blocked). The `frontier` tool therefore
returns the raw ledger with a `total` count and an optional `limit`;
triage-backed ranked retrieval is a later milestone, explicitly not
this one. Skill guidance: sample with `limit`, close few verified
gaps, never sweep the backlog.

## 4. Integration architecture

```
agent (Muse, Codex, any MCP client)
  |  stdio JSON-RPC 2.0 (tunic/mcp.py: thin dispatch, validated args,
  |  structured errors, no tracebacks)            skills/tunic/SKILL.md
  v                                               (portable procedure)
Memory API (tunic/memory.py) + compiler (tunic/compiler.py)
  |  SQLite graph, snapshots, frontier ledger
  v
benchmarks/agent/replay_tool_ab_v1.py (replay + score, no model)
```

The agent never touches the database directly; the benchmark never
calls a model. Both go through `handle_call`, so the evaluated
interface is the shipped interface.

## 3. Portable skill

`skills/tunic/SKILL.md` is provider-agnostic: connect, retrieve-first,
inspect evidence, review-then-acquire, refresh-after-write, correct
explicitly, mind the frontier. Trust table (observed / asserted /
container-context / proposed / withheld / disputed / corrected) and
abstain rules are part of the contract, not advice. No Muse- or
Kimi-specific behavior is referenced.

## 4. Tool-using evaluation adapter

`replay_tool_ab_v1.py` replays recorded transcripts
(`benchmarks/agent/transcripts/*.json`) call-by-call against a fresh
fixture session and scores correctness (gold ⊆ answer), grounding
(every answer symbol appeared in recorded tool output), governance
(commits carry receipts; refusals are recorded, never crashes), and
calibration (expected abstentions commit nothing). Transcript calls
support `{"$ref": n, "via": v}` candidate references so replays stay
deterministic without hardcoding content hashes. Seeded suites:
`constructor-acquire`, `unknown-abstain`, `governance-refusal`.
`tests/test_tool_adapter.py` replays all three plus a tampered-answer
negative and a frozen-artifact-untouched guard.

## 5. Dogfood on the Tunic repository (illustrative, live repo)

Ran 2026-11-02 against a throwaway DB ingesting `tunic/` (18 sources,
198 symbols). All through `tools/call`:

1. `tools/list` → 13 tools at the time (retrieve, related, impact, callees, timeline, review, note, candidates, acquire, correct, frontier, promote, refresh); now 14 with read-only `references` (see [mcp.md](mcp.md)).
2. `retrieve("which functions expand container classes")` → real
   evidence (`extract.py::_code_targets`, …); `related` on
   `memory.py::_container_of` → no static callers (true: it is only
   called through the answer path the extractor cannot see — see gap).
3. `frontier(status=open)` → 1805 open entries on the real repo;
   `candidates` → live proposals (e.g. `ollama.py` constructor rows).
4. `acquire` with a bogus id → `isError`, nothing committed.
5. `related(memory.py::Memory.answer)` → `isError`: the method is
   absent from the graph by the nested-scope extraction rule (see gap);
   `related(memory.py::Memory.candidates)` works normally.

## 6. Remaining gaps (tracked, not in this milestone)

- **Nested-scope extraction** (`tunic/extract.py`, `nested_scope`
  diagnostic): any method containing a nested def/class/lambda is
  invisible — including `Memory.answer` itself. Tunic cannot reason
  about its own retrieval entry point. Fix = extractor work with a
  real-repo regression fixture; explicitly out of this milestone.
- **Agent context is fusion-only**: `retrieve` does not expose the
  selection layer, container budget, or snapshot pinning. Deliberate
  for now; revisit with measured need, not speculation.
- **Vendor presets generalized**: the `kimi-cli` backend is now one
  preset of the generic subscription-CLI backend (`ai_backend`
  factory, `KIMI_PRESET` kept for frozen-artifact provenance); no
  Kimi runs are planned and the subscription is quota-blocked. A
  second preset (e.g. a Muse CLI) is the natural next addition: a
  preset dict plus its tool-less agent spec, no backend code changes.
  The shelved uncommitted `muse-cli` note above is superseded by this.
- **Long histories untested**: consolidation/forgetting absent;
  100K+/1M+ behavior unknown (see competitive scorecard next-metrics).
