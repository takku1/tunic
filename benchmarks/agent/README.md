# Agent benchmarks

Model-in-the-loop comparisons (Qwen3-class local models, temperature 0)
plus deterministic replay/scoring harnesses. Frozen result artifacts live
in `runs/`; the runners rebuild them. Held-out sets are never tuned
against — see each runner's protocol string.

## Suites

- `suite.json` — 7-task multi-session agent suite (code, decisions,
  recorded outcomes, staleness, disputes, irrelevant notes).
- `../memory/suite.json` — memory recall suite (dispatch/registry
  fixtures, session-to-session replay vs lexical).

## One-shot A/B runners (same model/tasks/budget across arms)

| Runner | Compares | Frozen result |
| ------ | -------- | ------------- |
| `run_model_ab_v1.py` | none / lexical / tunic (+select follow-up) on 14 synthetic tasks | `runs/model-ab-v1.json`, `runs/model-ab-v2.json` |
| `run_model_ab_pyq_v1.py` | none / lexical / tunic / select on 20 PyQuifer impact tasks (regression floor: 17/20 select) | `runs/model-ab-pyq-v1.json`, `runs/model-ab-pyq-v2.json` (tiering failure, preserved) |
| `run_model_ab_multisession_v1.py` | 4 write→read→correct trials | `runs/model-ab-multisession-v1.json` |
| `run_model_ab_multisession_v2.py` | 8-row write/read/vary matrix | `runs/model-ab-multisession-v2.json` |
| `run_model_ab_multisession_v3.py` | pilot with model-proposed writes | `runs/model-ab-multisession-v3-pilot.json` |
| `run_model_ab_multisession_v4.py` | one-stage (A) vs two-stage (B) vs hybrid-gate (C) acquisition + DUP/GATE/CORR guards | `runs/model-ab-multisession-v4.json` |
| `run_poison_ab_v1.py` | poisoned-note resistance | `runs/poison-ab-v1.json` |

## Acquisition generalization (ACQ v1, frozen spec)

Seeded 10-scenario × 5-rep × 3-arm fixture generator (`acqgen.py`) with a
product-path runner (`run_acq_v1.py`: propose → `commit_proposal` →
ingest → apply, exact-triple scorer). Batches ran separately and merged:

- `runs/model-ab-acq-v1-dry.json` — 150-cell dry run (0 misbuilds).
- `runs/model-ab-acq-v1-batch{1,2,3,4}.json` — raw batch results.
  Batch 3 `correct/*` + `irrelevant/*` rows are void (documented scorer
  bugs, fixed before batch 4) and superseded, not deleted.
- `runs/model-ab-acq-v1.json` — merged 240-row final artifact
  (`merge_acq_v1.py` records the merge + supersede list).

Key result: select+A3 exact 5/5 on dict/registry with 0 false
promotions; callback 0/5 on every arm (queued capability gap).

## Replays (deterministic, no model)

- `replay_s2_dispatch.py` — S2 registry-indirection write→read→frontier
  cycle (+ rep runs and repeats aggregate in `runs/`).
- `replay_conflict_live.py` + `score_conflict_live.py` — live-conflict
  branch coverage with safety-vs-coverage split scoring.

## Frontier closures

Resolved real-repo entries export to `../frontier/closures/` as durable
regression cases with replay predicates — see its README.
