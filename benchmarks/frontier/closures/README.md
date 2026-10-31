# Frontier regression closures

Exported regression cases from closed Frontier Ledger entries. Each file is
self-contained: the entry, its validated proof, the replay predicate, the
retrieval config the audit observed, and the snapshot that pinned the evidence.

## avalanche-rank1-v1.json

- Failure: `retrieval_miss` — "Which function computes avalanche statistics?"
  returned three wrong symbols; the exact symbol
  `src/pyquifer/diagnostics/neuroscience.py::avalanche_statistics` was in
  the graph but absent from candidates (bare-name stage required exact
  underscores; the query uses spaces).
- Fix: bare-name matching normalizes `_`/` ` between snake_case tokens
  (word boundaries preserved).
- Predicate: `target_rank_1` under the recorded config. Replay passes iff
  the verified target ranks first (recall@10 and RR reported).
- Break condition demonstrated: replay with the name stage budgeted to 0
  fails (rank None); restoring the recorded config passes.

## Reproducing

1. Check out PyQuifer at `1eb4a95` to `Z:/PyQuifer` (or any path).
2. Ingest it with this Tunic commit into a scratch database
   (`Memory(db, root); memory.ingest()`); the proof snapshot should match
   `snapshot:5d84a59dec98d84a5c3a596a49c3bbf6167caf1cf97aa42987737134be627f37`
   byte-for-byte given identical inputs.
3. `replay_regression_case(memory, json.load(open(case)))` must pass;
   with `config_override` zeroing the `name` budget it must fail.
