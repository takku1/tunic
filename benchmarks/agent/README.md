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

## Candidate-aided acquisition (ACQ v2, frozen spec)

Does mechanically derived candidate evidence let a model acquire exactly
the edges ACQ v1 could not? Same nine scenario kinds over two seeded,
byte-stable suites (`acqgen2.py`): callback, registry, attribute,
constructor positives plus correction, duplicate, conflict,
irrelevant-note, poisoned-evidence controls. Dev suite is the tuning
surface; the heldout suite runs once, never tuned against. Five arms:
none / lexical / tunic / tunic-candidates (candidate evidence blocks in
context) / tunic-commit-cand (the model SELECTs one live candidate id +
justification, committed via the bounded `Memory.commit_candidate`
path — same governed reconcile/promote as any proposal, refusals file
frontier entries). Product commit path, exact-triple scorer, precision
and recall reported separately; a candidate being present is not an
acquisition success.

Artifacts:

- `runs/acq-v2-dry.json` — 225-cell dry run, 0 misbuilds (CI re-checks
  fixture integrity in `tests/test_acq_v2.py`).
- `runs/acq-v2-dev.json` — dev live run, qwen3:4b, temperature 0.
- `runs/acq-v2-heldout.json` — heldout live run (once).

Key results, by metric class (dev / heldout, tunic-commit-cand unless
noted):

Positive acquisition (exact committed triple, pass@5):

- callback 5/5 and 5/5 (ACQ v1: 0/5 on every arm) — the v1 gap is
  closed and generalizes.
- registry 5/5 and 5/5; attribute 5/5 and 5/5. (Plain tunic reaches only
  0.4–0.6 on dev attribute/registry with 3 false promotions each; both
  candidates arms match commit-cand on callback/registry dev.)
- constructor 0/5 and 0/5 on every arm — the question names a class
  method while the candidate edge targets the class; bridging the two
  is beyond qwen3:4b today (next gap).

Safe refusal / gate behavior (no-commit plus the right reconcile
outcome):

- conflict, irrelevant-note, duplicate: 5/5 and 5/5 on every arm,
  both suites (duplicate apply retrieval also 5/5 and 5/5 once the
  scorer stopped requiring a no-op commit).
- poisoned-evidence: 5/5 and 5/5; zero decoy commits anywhere in either
  suite, any arm.
- acquisition precision (correct / committed): 1.0 and 1.0 for
  commit-cand.

Correction application (apply stage must list the post-correction
caller and not the retracted callee):

- 5/5 and 5/5 on every memory-context arm, both suites — models apply
  retract-and-reassert corrections reliably when the memory is
  visible. (The first artifacts reported 0/5 here: the apply gate
  required a propose commit, which cannot happen on pre-asserted
  knowledge; `rescore_acq_v2.py` recomputed the passes from the
  recorded raw answers, provenance-stamped in the artifacts. Lexical
  alone stays weak — 0.4 dev, 0.6 heldout — with no correction
  context it sometimes re-lists the retracted callee.)

Retrieval after acquisition: the apply stage re-queries the graph after
each commit (post-commit memory must surface the acquired caller), and
`tests/test_acq_v2.py::test_commit_candidate_acquires_exact_gold_edge`
compiles impact across a fresh ingest to prove the committed edge is
traversable in a later session.

Arm-hardening notes (both predate the frozen artifacts): the SELECT
prompt must frame selection as REVIEW of mechanically observed
evidence — with candidates labeled UNVERIFIED and no review
instruction the model abstained on every cell; the runner resolves
model-echoed candidate ids tolerantly (exact, unique suffix, unique
caller/callee symbol; ambiguous or unknown still refused).

### Inference backends

`run_acq_v2.py` has two interchangeable inference backends, selected
with `--backend=` (default `ollama`):

- `ollama` — the frozen qwen3:4b comparison runs above (temperature 0,
  token counts reported).
- `kimi-cli` — shells out to the installed Kimi Code CLI
  (`kimi --quiet --agent-file kimi_agent/agent.yaml -p <prompt>`), riding
  the user's authenticated Kimi Code subscription. No API keys or OAuth
  tokens are handled; no local model is loaded. Safety posture:
  `kimi_agent/agent.yaml` declares `tools: []`, so the model physically
  cannot touch the filesystem or shell regardless of prompt content, and
  the process runs in a per-run scratch work directory. One subprocess per
  inference call — the CLI's persistent `stream-json` session accumulates
  context across messages, which would contaminate independent trials.
  Exit code 75 / timeouts retry with bounded backoff; anything else fails
  the run loudly. Usage:

  ```
  python run_acq_v2.py <out.json> [suite] [scen,...] \
      --backend=kimi-cli [--kimi-model=NAME] [--kimi-timeout=SEC]
  ```

  Caveats recorded in every kimi-cli artifact: decoding is server-side
  (temperature not pinned); the CLI does not report token usage (fields
  are null, never fabricated); latency includes subprocess + session
  startup. Cross-model comparisons against the frozen qwen3 artifacts are
  directional only. Kimi results go to their own artifacts (smoke:
  `runs/acq-v2-kimi-smoke.json`) — never overwrite the qwen3 runs.
  Before any large run, check the subscription's applicable quota; one
  cell is 1–3 calls at ~5–10s each.

## Replays (deterministic, no model)

- `replay_s2_dispatch.py` — S2 registry-indirection write→read→frontier
  cycle (+ rep runs and repeats aggregate in `runs/`).
- `replay_conflict_live.py` + `score_conflict_live.py` — live-conflict
  branch coverage with safety-vs-coverage split scoring.

## Frontier closures

Resolved real-repo entries export to `../frontier/closures/` as durable
regression cases with replay predicates — see its README.
