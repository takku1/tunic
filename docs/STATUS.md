# Tunic Status

## Current Phase

W1 reference slice plus memory loop are implemented and regression-tested on `main`. Working tree focus is field validation on real repositories (Python + TypeScript/JavaScript) and closing the documented retrieval/coverage gaps.

## Active Focus

- TypeScript/JavaScript verified call edges v1 (shipped 2026-11-03, `tests/test_ts_call_edges.py` 27 tests) and real-site validation on seancarneywebsite.
- Uniform tree-sitter engine v1 (shipped 2026-11-03, `tunic/unigraph.py`): one R1/R2 resolver driven by declarative per-language specs; Go first (`tests/test_go_call_edges.py` 17 tests, real-site mcp-go 1,480 verified edges). TS stays on `tunic/tsgraph.py` (frozen receipts); migration is open, gated on TS-suite parity.
- External-coverage deltas v1 (shipped 2026-11-03, distilled from graphiti): graph expansion stage (`Memory._graph_neighbors`, budget `"graph"`, default off), MMR diversity reorder (`mmr_lambda`, default 1.0 identity), `same_as` entity-resolution proposals (`tunic/resolve.py`, reviewer-gated, no auto-merge), deterministic community detection plus structured summaries (`tunic/community.py`, excluded from answer path). 27 new tests; 741 green full suite. Default-on acceptance for graph/MMR stays PROVISIONAL.
- Concept gaps G1-G5 (docs only, from full read of `docs/references/graphiti`, no code adopted): entropy-gated MinHash LSH blocking, Allen-interval date-ordered conflict surfacing, temporal bound normalization at claim parse, SHACL-shaped relation type signatures, weighted propagation plus exact-PPR distance-ranked expansion. Each has a SOTA anchor (LSBlock 2025, Allen 1983 / Snodgrass BCDM, SHACL W3C 2017, FORA KDD 2017) and a Tunic-shaped execution plan in [external coverage](research/external-coverage.md).
- Benchmark backend generalized (2026-11-03): `ai_backend` factory plus preset dicts replaces the Kimi-specific surface; `kimi-cli` stays only as the first preset and its frozen artifact label, `kimi_backend` as a deprecated alias. `kimi_agent/` renamed to `ai_agent/`; `--ai-model`/`--ai-timeout` flags added (`--kimi-*` still accepted). Orphan `runs-kimi-ctor.*` process files removed (pid dead; run JSON kept). 22 acq tests green. Frozen `runs/acq-v2-kimi-*.json` untouched.
- G1-G5 second wave (shipped 2026-11-03): deterministic MinHash LSH + entropy gate in `same_as` proposals; `tunic/temporal.py` (date-ordered `contradicts` proposals via `Memory.propose_date_conflicts`, pure `normalize_bounds` helper with explicit reference); enforced relation signatures at claim parse (`relation_signature` diagnostics, document->code provenance preserved); edge-count-weighted propagation and depth-2 exact-PPR expansion. Tests: `test_signatures.py`, `test_temporal_bounds.py`, `test_ppr.py` plus extended `test_same_as.py`/`test_community.py`/`test_expansion.py`. G3 auto-wiring waits on statement-time tracking (OPEN).
- All-file-type ingestion Tier 0 shipped (2026-11-03): 14 generic unigraph specs (java/kotlin/scala/csharp/c/cpp/rust/swift/php/ruby/julia/lua/bash/powershell) with tests/test_tier1_edges.py (73 tests); [unigraph] pip extra; .jl/.psm1 plus on-demand-six extensions inventoried.
- All-file ingestion Tiers 2-4 shipped (2026-11-03): Tier 2 block-splitting (vue/svelte/astro + ipynb shared-namespace cells, 17 + 8 tests), Tier 3 config key-paths (JSON/YAML/TOML/INI, 13 tests), Tier 4 schema references (SQL/GraphQL/Protobuf, `references` never traverses impact, 10 tests). See [file-type coverage](architecture/file-type-coverage.md).
- Document-first-class retrieval R2 and discovery scope unification R3 (shipped 2026-11-02).
- Frontier scheduler v1 turning ranked clusters into governed investigations with proof-backed resolves.
- Frontier signal quality + multi-language precision baseline (2026-11-03): bare data-label mention-candidates no longer filed (151,674 to 37,600 diagnostics, DB 72 to 34 MB); 40-sample precision baseline across Rust/Java/C++/PowerShell pinned SHAs (28 judged: 21 pass, 7 miss, 0 false edges; cpp 0 edges = method/free-function harvest gap).
- Retrieval calibration + language diagnostics (2026-11-03): releaseHref answers complete with both verified callers on pre-pivot snapshot (reported abstention was stale-DB artifact + live-tree symbol removal; live no_discovery is correct); C++ gap is whole-file ERROR skip not node coverage (clean fixtures 3/3); Rust alias vs Type::method isolated; 40-sample candidate review 0/40 useful on the self-ingest frame — no further filter. 939 tests.
- Snapshot freshness + storage backup/restore (2026-11-03): read-only `freshness` receipt (added/deleted/changed vs current snapshot, profile/root/committed_at, no publish) + `answer()` provenance; atomic `backup` (VACUUM INTO + rename) and validated `restore` (refuses existing dest, removes half-writes, rejects corrupt/future-schema files); schema-version gating tested. 950 tests.
- Spec-derivation experiment (2026-11-03): generated syntax mappings + reviewed overrides == hand specs for Java/Rust/C++ (mapping parity, byte-identical real-repo outputs, 950/950 suite under substitution); semantic policy stays hand-reviewed; helix/semgrep/comby donors scouted via Cannibalize. 951 tests.
- Blocked: constructor acquisition/application re-measurement (needs model access); platform deployment (by design, not started).

## Architecture Summary

Tunic is a local evidence substrate: a Python/SQLite reference ingests code and prose into versioned snapshots with byte-exact provenance, compiles source-grounded impact/mention/claim evidence through a typed context IR with trust labels and abstention, and governs learning through review/promotion plus a frontier ledger that keeps unresolved observations with required evidence. Retrieval fuses exact, name, lexical, and pinned-local dense stages behind an evidence qualification gate and an opt-out selection default. A pinned local continuation profile renders validated IR for model execution without making the model an authority.

## Key Decisions

- D-16 acceptance framework approved as provisional v1 (2026-11-02): HELD obligations pin frozen behavior; PASS-scoped wins (selection default, reuse, lifecycle) apply only to their documented scope; PROVISIONAL items remain measurement plans; BLOCKED items stay unresolved with no substitutes.
- Selection default flipped on 2026-11-02 with opt-out (`select=False`); BM25 suite lead recorded as the standing trade-off.
- Generic unigraph v2: specs are pure data (node-type tables per language); shared rules are scope-pull on matched imports, classes-as-type symbols, unique-or-diagnostic, per-file rescan tracking. Go path byte-frozen.
- W1 profile stays Python stdlib plus tree-sitter TypeScript/JavaScript verified edges; other inventory languages stay edgeless; heuristic inventory never invents edges.
- Tier-3/4 endpoint doctrine (2026-11-03): `config` and `schema` endpoint kinds derived from key shape (stateless); `depends_on` spans code/document/config to code/config; `references` spans schema to schema only and never traverses impact (excluded from TRAVERSABLE_CLAIMS, syntactic_observation only). Data-file test contract updated: .json yields config entities (key_path_v1 receipt), never code symbols or call edges. Whole-file includes and bare-file $refs abstain (depending on a file is not depending on a key).
- Memory consolidation is additive and forgetting is reversible suppression; physical erasure is out of scope.
- Autonomous write path stays reviewer-gated; the frontier ledger feeds learning but performs none.

## Deprecated/Legacy

- GraphGraph: historical reference only, no compatibility requirement.
- "Evidence selection unimplemented" headers: stale; selection v1 is implemented and default-on since 2026-11-02.
- "No scheduler" statements: stale; scheduler v1 shipped 2026-11-02 (`tunic/schedule.py`, `schedule` CLI).
- Field-test §8.2 items 1, 2, 4, 5, 6 (inventory scope, mention flood, Python-only reply labels, prose abstention, discovery latency): closed by universal ingest v1, inventory precision, per-symbol coverage, document retrieval R2, discovery unification R3, and TS call edges v1 (see §9 in the field-test doc). Item 3 (default-excluding `.claude/skills`) stays open by explicit decision: vendored scope needs explicit config. Broader language extractors beyond TS are closed for 14 languages (Tier 0, 73 tests), block-splitting containers (Tier 2, 25 tests), config key-paths (Tier 3, 13 tests), and schema references (Tier 4, 10 tests); the on-demand six (zig/elixir/fortran/groovy/objc/verilog) remain open.
- Pre-fix ACQ constructor 0/5 apply figures: retained as the baseline, not current behavior; fresh re-measurement is BLOCKED on model access.

## Last Updated

2026-11-03. Tracker: [implementation status](architecture/implementation-status.md). Numbers here come from committed artifacts or suite runs, never from memory.
