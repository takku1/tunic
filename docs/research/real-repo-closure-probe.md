# Real-repo Frontier closure probe: PyQuifer @ 1eb4a95

Date: 2026-10-31. Scratch DB only (temp dir); PyQuifer untouched.
Snapshot: `snapshot:5d84a59dec98d84a5c3a596a49c3bbf6167caf1cf97aa42987737134be627f37`
(1554 symbols, 737 assertions, 29247 diagnostics, 10783 open entries).

Goal: close one genuine entry via verified evidence, export a regression
case, invalidate the evidence, confirm replay fails.

## 1. Dispatch entries: all 17 correctly stay open (verified by reading code)

No A3 candidates exist for any of them, and each was checked against
A3's documented scope (closed function-dispatch table, one static
assignment, literal-key indexing):

- `docs/sphinx/conf.py` x4 (`autodoc_default_options`, `intersphinx_mapping`,
  `html_theme_options`, `source_suffix`): config value dicts, not dispatch.
- `scripts/measure_neuro_alignment.py::REGIME_TARGETS`,
  `scripts/v4_verify.py::{sd_r_at, modes, mode_transitions}`,
  `scripts/verify_reorg.py::{file_map, total_classes, total_methods, total_funcs}`:
  accumulator/data tables indexed by loop variables (verified by reading
  `verify_reorg.py:9-40`, `v4_verify.py:69-76,135-150,178-193`).
- `src/pyquifer/__init__.py::_LAZY_IMPORTS`: real lazy-import registry, but
  indexed by the dynamic `__getattr__` parameter (`__init__.py:872-873`) —
  correctly refused, and a correct-abstention case.
- `src/pyquifer/runtime/tick_result.py::PROCESSING_MODE_NAMES`: indexed by
  `int(result.processing_mode)` (dynamic) — correctly refused.
- `tests/benchmarks/bench_penrose_chess.py::{PIECE_VALUES, FORTRESS_FENS,
  PIECE_TO_CHANNEL}`: constant dicts indexed by variables — correctly refused.

The ledger is right to keep these open. None is closable via
`dispatch_covered` without new extractor capability (callback/attribute
dispatch — the already-queued next milestone).

## 2. Retrieval battery: no abstentions, three confident misretrievals

Every natural question returned `complete` (BM25 always finds something in
1554 symbols), so no `retrieval_abstention` entry exists or was filed, and
the `answer_nonempty` predicate is vacuous on this repo:

- "Which methods invoke the registered Kuramoto live-feed callback?" →
  `[set_kuramoto_live_feed, actualize_vision, fit]`. Text match works;
  the registrar (non-invoker) ranks first.
- "Which module provides PerturbationLayer?" → three unrelated
  `__init__`s. Truth (`src/pyquifer/memory/perturbation.py` via
  `_LAZY_IMPORTS`) is invisible: lazy re-exports have no relation in the
  claim vocabulary. Real gap, no committable triple without stretching
  `depends_on` semantics — refused to force it.
- "Which function computes avalanche statistics?" →
  `[phase_activity_to_spikes, _rk4_step, compute_lyapunov]` while the
  exact symbol `diagnostics/neuroscience.py::avalanche_statistics` IS in
  the graph. Knowledge present, retrieval wrong.
- "How is the string name of a processing mode recovered from a tick
  result?" → junk (eval-harness tests, AdapterManager). Root cause:
  `TickResult` (the NamedTuple) is not extracted as a symbol at all, so
  its docstring ("recover the string name...") is absent from the graph.
  Ingestion gap + ranking gap combined.

## 3. Conclusion: v1 predicates do not grip real-repo failure modes

The closure loop (resolve → export → invalidate → replay-fails) is proven
on synthetic fixtures (238 tests) but has no entry point on PyQuifer:

- `dispatch_covered` needs an A3 candidate; real tables are dynamic-key.
- `answer_nonempty` needs an abstention; real retrieval never abstains.
- Confident misretrieval (exact symbol present but unreturned; truth
  invisible to the vocabulary) has no entry kind, no replay rule, and no
  predicate in v1.

Required next work, in order: (a) callback/attribute candidate generation
(the queued milestone — covers Q1/Q2-class gaps); (b) a `retrieval_miss`
entry kind + rule (file when a human or audit marks the top-k wrong;
resolve when the verified symbol ranks first; predicate checks rank on
replay); (c) only then re-attempt real-repo closure, starting with Q4
(exact symbol present) as the first case.
