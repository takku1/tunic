# W1 regression benchmark contract

Status: implementation scope for a reproducible benchmark suite, separate from the earlier one-task continuation smoke test. This suite must not be presented as a held-out memory benchmark or a six-product comparison.

## Scope and ownership

`benchmarks/w1/suite.json` owns independently authored expected symbol sets and task semantics. Small Python repositories cover aliases, chains, diamonds, relative imports, recursion, empty impact sets, shadowing, and deliberately unsupported methods/dynamic dispatch. Update stages cover edits, newly incoming callers, deletions and unrelated changes. Labels are manually specified from fixture source, never generated from Tunic output. They are independent of the extractor, not independently human-audited or hidden test data.

`tunic/benchmark.py` owns isolated replay, dataset identities, baseline invocation, scoring and aggregate results. `tunic/baselines.py` owns an ordinary BM25 function-chunk search control and a one-hop static-call control. Both use the same captured source/query inputs; the one-hop control shares Tunic extraction and must say so. No fake dense/vector baseline is included. `tunic/__main__.py` exposes a model-free default and an optional pinned-model comparison. Existing ingestion/compiler/renderer algorithms remain fixed.

## Independent outcomes

Report predicted-versus-gold precision, recall, F1, exact set match and expected-test recall for retrieval. Preserve false positives and misses as symbol lists. Precision is unavailable for no predictions; recall is unavailable for an empty expected set. Exact empty-set success is separately meaningful. Do not equate support with completeness.

For model responses, independently score returned symbols against gold, plus mechanical per-claim support precision and the full support gate. A supported subset can have perfect support precision and poor answer recall. Task completion requires successful generation, full support validation and an exact expected-impact set; no discarded failure or unsupported answer can improve the denominator. Model scores concern this typed static-impact task, not unrestricted reasoning or conversational memory.

## Protocol and acceptance

Every case gets its own scratch source root and SQLite database. Every update republishes through full ingestion and compares the result with a fresh database built from the resulting source tree; that differential check tests maintenance, not label correctness. Record dataset/source/suite hashes, implementation digests, parameters, timings, raw candidate sets and failures. Engine code never reads labels to construct retrieval results or model prompts. Gold is used only by the scorer.

Core-profile regression acceptance requires correct graph candidates, evidence/IR validity and fresh-rebuild equality. Known unsupported cases remain in overall accuracy metrics and are reported separately; core pass is not all-task success. Model-free baseline runs require no API keys, model downloads or services. Optional inference uses the existing pinned adapter, preserves probe costs and cache limitations, and stops on infrastructure/profile changes while accounting for unrun tasks. No cache-disabled or cold/warm guarantee is invented.

Changes are limited to benchmark data/harness/baselines, CLI, focused tests and documentation/results. Run direct tests unguarded because the session cannot write the full-stack run log under `.git`. Acceptance checks include fixed hand-scored examples, zero/partial/incorrect answers, bounds/failures in denominators, safe replay paths, dataset drift identity, independent labels catching a broken retriever, CLI execution and a real optional model run. Broader W2/W3, dense RAG, external-product adapters and held-out real repositories remain follow-on evaluation.
