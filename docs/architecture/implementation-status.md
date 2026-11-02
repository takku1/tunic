# Tunic implementation status (single authoritative tracker)

Date: 2026-11-02. Scope: local reference implementation on `main`.
Full suite: 591 tests green (`python -m unittest discover -s tests`, ~80-210 s
depending on live-repo ingests; always record command, count, and date).
MCP tools: 13 (see [mcp.md](mcp.md)). No `/docs/STATUS.md` exists in this
repo; this file is the registry (per binding convention below).
This file records what is shipped, tested, and demonstrated — not what the
architecture documents propose. For target architecture, see
[overview.md](overview.md); for frozen evidence, see the benchmark
artifacts cited below. Update this file, not scattered headers, when
capabilities land.

## Release gates

| Gate | Bar | Standing |
| ---- | --- | -------- |
| Core 1.0: reliable evidence ingestion, grounded retrieval, corrections, context compilation, regression-tested real-repository answers | Avalanche-class closure committed; selection acceptance evaluated PASS-scoped on the frozen suite (D-16 targets proposed below, provisional) | Not met: constructor post-fix re-measurement blocked on model access (selection default adopted with opt-out; BM25 suite lead stands as documented trade-off); three real-repo closure artifacts exported (avalanche rank-1, PyQuifer attribute-dispatch, PyQuifer constructors) |
| Memory 1.0: durable multi-session semantic/episodic/procedural memory with safe acquisition, consolidation, lifecycle | Multi-session benchmarks + ACQ v1/v2 exist; consolidation/forgetting absent; frontier scheduler absent | Partial: acquisition measured (ACQ v2 candidate-aided arms 5/5 callback/registry/attribute both suites, controls 5/5, zero decoys, precision 1.00); lifecycle incomplete (no consolidation, no forgetting, no cross-session reuse metric) |
| Platform 1.0: authenticated multi-user deployment, isolation, recovery, field-validated reliability | Single-principal local system today | Not started (by design) |

## D-16 numeric acceptance targets (proposed 2026-11-02, provisional)

D-16 approved as a provisional, versioned acceptance framework
(user decision 2026-11-02, framework v1) -- not a declaration that
targets passed. HELD targets are regression obligations; PASS-scoped
targets are accepted only for their documented experimental scope;
PROVISIONAL targets remain measurement plans until validated;
BLOCKED targets stay unresolved with no invented substitutes; OPEN
targets require a defined metric before acceptance. Every target
below cites its baseline artifact; targets without a measured
baseline are marked PROVISIONAL (a direction and a first-measurement
plan, not a pass).
"Hold" targets pin current frozen behavior against regression.

| Domain | Target | Baseline artifact | Standing |
| --- | --- | --- | --- |
| Mention precision | 0 false positives on frozen MochiOS strata; PyQuifer dev `refers` recall >= 0.80 with 0/28 `not-a-mention` FP | decisions D-29; frozen MochiOS precision split | HELD by suite (re-verify on change) |
| Selection acceptance | hybrid-select beats full-hybrid on micro-precision and micro-F1 with Recall@1/@3 held, on suite and heldout; identical threshold-sweep response; negatives without regression | `benchmarks/arena/runs/semantic-selection-v1.json`; `tests/test_selection_acceptance.py` (5 tests, green 2026-11-02) | PASS-scoped; pilot ADOPT on dev set per predeclared criteria; default flipped 2026-11-02 with opt-out and BM25 caveat recorded |
| Abstention / decoy | 100% correct verdicts on frozen abstention + decoy tasks | Arena v2 frozen set (`build_abstention_task`, `build_decoy_task`; stage-1 pins) | PROVISIONAL (frozen set is small; widen before claiming calibration) |
| Cross-module calls | 12/12 on the frozen source-grounded P/R set | Cross-module coverage census 2026-11-02 (396 -> 563 assertions, -167 unresolved) | HELD by dogfood tests (generality unproven) |
| Candidate-commit precision | 1.00 with zero decoy commits | ACQ v2 frozen artifacts, both suites | HELD |
| ACQ callback/registry/attribute | 5/5 candidate-aided arms, both suites | ACQ v2 frozen artifacts | HELD |
| Constructor acquisition/application | >= 4/5 acquisition and >= 4/5 application on a fresh dev run post-fix | Pre-fix 0/5 apply; blocked on model access | BLOCKED (no fresh run yet) |
| Frontier closure yield | Ranked investigation yield >= raw-count baseline at fixed budget, replicated | v1 experiment ranked 4 vs baseline 2 (single run) | PROVISIONAL (needs replication) |
| Memory reuse across sessions | Metric undefined; first measurement plan: verified-reuse rate on paired investigations | None | PROVISIONAL (no baseline) |
| Consolidation/forgetting safety | No metric; plan: retention/distortion on long-history fixture | Not implemented | OPEN (no target settable) |
| Ingest/retrieval budgets | No regression: self-ingest wall within 2x of 11 s baseline (237 symbols); retrieval p50/p95 to be measured | Census 2026-11-02 | PROVISIONAL (retrieval latency unmeasured) |

Core 1.0 = evidence ingestion + grounded retrieval + corrections + context
compilation + regression-tested answers. Memory 1.0 = retention,
correction, consolidation, and reuse across investigations. Platform 1.0
= deployment, multi-user, recovery (out of the critical path by design).

## Prioritized core backlog (dependency order)

Wave 1 (release truth): this reconciliation. Exit: every gate above has
a measurable pass/fail and a cited source.
Wave 2 (retrieval correctness): selection default adoption decided ADOPT 2026-11-02 (opt-out documented, BM25 trade-off recorded);
abstention widening SHIPPED 2026-11-02 (cause taxonomy + HISTORICAL markers, 12/12 suite gate holds);
constructor-to-method gap (post-fix hosted re-measurement when
unblocked) and receiver resolution (static/class SHIPPED 2026-11-02;
inheritance deferred). Exit: source-grounded accuracy
improvements, no speculative-edge promotion, no regressions.
Wave 3 (persistent memory): consolidation + forgetting design and
fixture; frontier scheduler; cross-session reuse metric and lifecycle
evaluation. Exit: measurable reuse, safe consolidation, verified
frontier closure.
Wave 4 (operational reliability): backup/restore + migrations;
observability; authenticated principals; optional stronger sandboxing.
Exit: documented recovery guarantees and deployment trust boundary.
Deferred by design: multi-tenant ACLs, KV fusion, Wilderbeast/Chili
Crisp/Team-Ops integrations, geometry-driven ranking (D-08/D-14/D-15).

| Item | Owning docs | Implementation | Failure evidence | Expected tests | Depends on | Effort | Unblocked now? |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Selection default adoption | evidence-selection.md | `tunic/answer.py` default flip + budgets | None (decision, not bug) | Frozen-suite gate re-run + full suite | Selection acceptance (done) | Small | Yes (no model needed) |
| Abstention widening | evaluation/strategy.md | Cause taxonomy (`no_discovery`/`unknown_subject`/`all_withheld`/`budget_exhausted` on every empty answer) + HISTORICAL render markers + frozen abstention/decoy task set | Arena v1 Q6 wording flaw; unmarked historical candidates, causeless empties (fixed) | Suite gate: 12/12 non-experimental tasks hold under select=True+current, 0 false-abstentions on gold questions, 0 unmarked historical, 0 OBSERVED violations (`tests/test_abstention_widening.py`, 12 tests, green 2026-11-02) | Arena v2 protocol (done); widened calibration SHIPPED 2026-11-02 | Small | Yes |
| Constructor re-measurement | implementation-status (this file) | Fresh ACQ constructor dev run | Pre-fix 0/5 apply | ACQ harness `--resume` | Model access | Small | No |
| Receiver resolution | decisions D-25, w1-reference | `self`/local-constructed receiver proofs + static/class-method registration and self/cls/ClassName resolution (`tests/test_static_methods.py`, 9 tests) | Arena v1 Q3 key-rule flaw | Extractor fixtures + dogfood pin (Tunic +15 verified static edges/0 removed, PyQuifer +22/0 removed; torch/F.* still unresolved) | Cross-module fix (done); static/class SHIPPED 2026-11-02; same-file inheritance measured-and-deferred (31 Tunic fixture sites, 4 PyQuifer) | Medium | Yes |
| Staleness-explicit references withheld | evaluation/strategy.md (agent suite stale/evolve tasks) | Keep task-named superseded symbols with status vs withhold under select+current | Agent-heldout stale-task/evolve-para fail under select+current (pass under transparent default and fusion) | Selection fixture + eval pin | Eval harness pins fusion (done); product fix SHIPPED 2026-11-02: named history kept flagged, impact/provenance fallback when silence is the alternative, unrelated decoys still withheld (`tests/test_selection_supersession.py`, 11 tests; suite 557 green; pilot re-run ADOPT, metrics unchanged) | Small | Yes |
| Consolidation/forgetting | research/semantic-graph (SG-09..12) | Design + long-history fixture | Absent capability | New lifecycle suite | None (greenfield) | Large | Yes (design only) |
| Frontier scheduler | architecture/frontier-ledger.md | Scheduler v1 SHIPPED 2026-11-02: ranked clusters become persisted jobs; scratch investigation applies home only via commit_proposal + proof-backed resolve (`tunic/schedule.py`, `schedule` CLI, `schedule_jobs` table) | N/A (greenfield) | 12 committed adversarial tests (duplicate/crash/running-resume/stale/budget/deadline/invalid-proof/blocked/terminal/gates/CLI); real-repo yield on Tunic copy: scheduled 2 resolved + 2 edges vs raw-count baseline 0 under equal budgets; source-verified loans.py closure (matches frozen lib-para gold direction); DEV-dispatch capability deferral with entries open | Triage v1 + investigate harness (done) | Medium | Yes |
| Memory reuse metric | evaluation/strategy.md | Paired-investigation reuse harness | Absent capability | Runner-based rehearsal | Arena v2 runner (done) | Medium | Yes (harness), No (real agents) |
| Backup/restore + migrations | architecture/runtime-memory.md, storage row | Snapshot export/import + migration test | Absent capability | Recovery tests | None | Medium | Yes |
| Authenticated principals | architecture/security.md | Principal interface + tests | "No multi-tenant ACL" | Security acceptance subset | None | Large | Yes (local subset) |

## Subsystem matrix

| Subsystem | Shipped and tested | Known limitations | Exit criteria |
| --------- | ------------------ | ----------------- | ------------- |
| Evidence ingestion and provenance | Python AST extraction (static profile v7: functions, methods, self/cls, imports) with byte-exact spans; doc mentions + typed claims; source digests, snapshots, manifest integrity; incremental refresh with rebuild-equivalence tests; registry-dispatch, callback-parameter (`callback_parameter_candidate_v1`), known-receiver attribute-dispatch (`known_receiver_method_candidate_v1`, local single-binding variables AND `self.attr = C()` assigned once in `__init__`), and bare constructor-call (`constructor_call_candidate_v1`) candidates — all proposed `depends_on` with derivation chains, negative controls, refresh equivalence; class declarations are graph symbols of `kind "class"` (mention/claim-invisible; NamedTuple-style annotation-only bodies now visible — the documented TickResult gap is closed); `test_callback_candidates.py`, `test_attribute_candidates.py`, `test_constructor_candidates.py` (`test_reference.py`, `test_incremental.py`, `test_refresh.py`); nested-scope extraction v1/v1.1 (pruner + nested-call diagnostics, root-header stripping, nested class bodies, generator timing, async parity); method-of-class container expansion (v1, bounded, supersession-safe); cross-module import-backed bare-name resolution (relative-import guard fix, import cited in resolution support) (`test_nested_scope.py`, `test_container_retrieval.py`, `test_cross_module_calls.py`); static/class-method indexing + self/cls/ClassName resolution, bare `@staticmethod`/`@classmethod` only (`tests/test_static_methods.py`) | Python only; attribute dispatch resolves only provably known receivers — third-party receivers (F.softmax, Rs.mean) correctly stay unresolved, bound-method arguments and `torch.__call__`-style dunders stay invisible; decorated classes stay unsupported (40 on PyQuifer — a decorator may replace the callable), as do non-builtin-decorated methods, properties, and inherited methods (same-file inheritance measured: 31 Tunic fixture sites + 4 PyQuifer, deferred); `typing.NamedTuple(...)` factory-call syntax not implemented; metaclasses/dynamic mutation outside the static profile | Broader receiver proofs; class-construction traversal after reviewer promotion; bridge the ACQ v2 constructor gap (question names a class method, candidate edge targets the class — 0/5 every arm on both suites) |
| Semantic graph and truth maintenance | Typed assertions, bitemporal timeline, corrects/supersedes/retracts with support-minus-retraction arithmetic, dispute surfacing (`test_temporal.py`, `test_memory.py` correction tests) | Lazy re-exports and attribute-mediated relations have no relationship type; entity/ontology semantics unresolved | Relation vocabulary for re-export/attribute edges; merge/split identity rules |
| Retrieval and evidence selection | Hybrid exact/name/lexical/semantic stages with RRF fusion, budgets, abstention; pinned local embeddings with persistent cache; selection layer with traces, default on since 2026-11-02 (`test_retrieval.py`, `test_selection.py`, `test_embedcache.py`, `test_precision.py`) | Dense/hybrid F1 trails BM25 (0.41 vs 0.61 on the 14-task semantic suite) while leading Recall@1/MRR; selection default since 2026-11-02 (acceptance PASS-scoped + pilot ADOPT per predeclared criteria, opt-out `select=False`, BM25 suite lead recorded; `tests/test_selection_acceptance.py`, `tests/test_selection_pilot.py`); confident wrong answers on large repos (PyQuifer misses) with abstention tasks and `cold` receipts now measured | Selection wins a frozen suite; rank-based regression coverage; calibrated abstention |
| Context compiler and typed IR | Versioned W1 IR, three traversal policies, trust-labeled rendering, truncation receipts, forward-callee traversal (`compile_callees`: direct/reachable/candidates/cold receipts, bounded + cycle-safe) (`test_eval.py`, `test_render_answer.py`, `test_policy_contract.py`, `test_forward_callees.py`, `test_forward_adversarial.py`) | Full target IR and richer derivations remain proposed | Portability checks for the full IR obligation set |
| Agent memory and acquisition | Persistent multi-session API, MCP stdio server (13 tools, incl. forward-callee `callees`), portable agent skill, deterministic tool-replay adapter, proposals with deterministic reconcile, corrections, multi-session + ACQ v1 benchmarks, bounded candidate-aided acquisition path (`commit_candidate`: agent selects one live proposed candidate + justification; the proposition commits through the same governed reconcile/promote path — evidence never promotes directly, refusals file frontier), ACQ v2 five-arm candidate benchmark over dev/heldout suites (`test_memory.py`, `test_acq_v2.py`, `benchmarks/agent/`) | Constructor questions were 0/5 apply pre-fix across arms on both suites (method-to-class container retrieval and cross-module import resolution shipped after; re-measurement blocked on model access); correction application is reliable once the memory is visible (5/5 both suites) but lexical correction-application is 0.4–0.6 without context; lexical never acquires indirection; autonomous write path still reviewer-gated | Bridge the constructor gap; correction modeling for context-poor retrieval; turn a held-out slice green without tuning on it |
| Frontier and learning loop | Auto-filing (ingest diagnostics, abstentions, refused proposals), structured proof receipts, kind-dispatched replay, regression export, `retrieval_miss` records with rank-1 predicates, read-only triage (file-level call clustering, three queues, heuristic ranking with four sensitivity weightings, terminal clusters excluded from active ranking, recommendation over the full cluster set rather than the top-N display, frontier-digest + whole-DB `data_version` mutation check, `triage` CLI), frontier ranking v1 (`TRIAGE_VERSION 2`, verifiability-scored queues, identical-budget validation experiment), deterministic investigation harness (`tunic/investigate.py`: per-arm workspace copies, publish-then-resolve), Arena v2 protocol (taskgen validation, trusted-dispatch runner, stage-1 pins), zero-config `tunic <path>` ingest with summary (files, symbols, assertions by status, diagnostics by kind, open frontier by kind), index-backed frontier dedupe anchor (digest + line + byte offset; ingest filing seeks instead of scanning the whole ledger per diagnostic). Validated on the real PyQuifer repo: 10,906 entries collapse to 125 file clusters; the recommendation names `src/pyquifer/identity/strange_attractor.py` (313 occurrences), dominated by attribute-dispatch and class-constructor calls — matching the next extractor milestone (`benchmarks/frontier/pyquifer-triage-v1.json`), CLI (`test_memory.py` Frontier* classes, `test_triage.py`, `test_zeroconfig.py`) | No scheduler; three closures exported (`benchmarks/frontier/closures/avalanche-rank1-v1.json`, `pyquifer-attrdispatch-v1.json`, `pyquifer-constructors-v1.json` — candidate_present predicates replay green, fail on frozen baselines); constructor milestone: 153 PyQuifer constructor candidates, 383 class symbols, TickResult recovered, 10,982 -> 10,829 unresolved-call diagnostics, strange_attractor.__init__ wiring recovered in the triage-named top hotspot | Scheduler; dedup accounting; verified-internal-coverage audit sample (denominator, not raw Frontier counts) |
| Runtime memory integration | Ollama continuation path with support checking and segment boundaries; KV/prefetch isolated as research | No prefetch, no adaptive triggers, no KV fusion | Demonstrated quality or cost win before any fusion work |
| Security and governance | Workspace path containment, single-principal authorization gate, guarded promotion, adversarial/poison tests (`test_adversarial.py`, `test_policy_contract.py`) | No multi-tenant ACL, revocation, erasure, or hostile-input isolation; MCP takes a caller-supplied principal | Threat-model acceptance backlog; authenticated-principal interface |
| Storage and operations | SQLite + snapshots + manifests; deterministic local tests; no external services required | No backup/recovery/migration story; no observability; Ollama keeps ~8 GB resident during model benchmarks | Backup/restore and migration tests before any deployment claim |
| Evaluation and benchmarks | Frozen suites and runs: W1/memory/agent suites, mention-precision split, Arena semantic/selection runs, frozen Cognee/Mem0 competitors, model A/Bs (incl. PyQuifer 17/20 select), multi-session v1–v4, S2 replays, conflict branch, ACQ v1 (240 rows, dry + batches + merge), ACQ v2 (dry + dev + heldout, qwen3:4b t0: candidate-aided arms 5/5 callback/registry/attribute on both suites, controls 5/5, zero decoys, precision 1.0; correction apply 5/5 on memory arms both suites after the apply-gate scorer fix; constructor open); Arena v2 (taskgen_v2 defect validation, runner_v2 trusted dispatch + strict scoring, stage-1 graph validation); selection acceptance pinned (`tests/test_selection_acceptance.py`); cross-module coverage census (+167 assertions, -167 unresolved, 12/12 frozen P/R); versioned competitive scorecard (`benchmarks/competitive-scorecard.json`, v1, schema-tested) separating verified Tunic results, user-supplied-unverified vendor figures, and not-tested head-to-head metrics | Small synthetic cores (n=14/20); model variance across reruns; no confidence intervals; held-out PyQuifer 20 must stay untouched | Pinned numeric targets (D-16); one more real-repo closure; selection A/B on frozen suites |

## Doc conventions (binding)

- This file is the only completion/progress tracker. Architecture docs
  describe contracts and targets; they must link here instead of
  restating status.
- A shipped capability lands here with its tests and artifacts in the
  same commit that implements it. Headers elsewhere saying
  "unimplemented" for shipped work are bugs.
- Numbers in this file come from committed artifacts or the suite run,
  never from memory. No new completion percentages.
