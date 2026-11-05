# Common competitive benchmark core

This implements the first executable part of the
[fixed competitive contract](../../docs/evaluation/competitive-benchmark.md).
It is a **public synthetic development candidate**, not the completed
competitive exam or evidence that Tunic beats another product.

The 16 tasks cover multi-hop relationships, current versus historical
facts, ownership corrections, persistent session decisions, an unapproved
rumor, and unanswerable questions. Six ordered source records are the only
source information supplied to the memory adapter. Each query sees only
its declared event prefix; tasks and gold remain outside that workspace.
The Tunic adapter does not parse the gold file. The inventory checker
hashes it as opaque bytes; the scorer interprets it afterward.

## Run directly

```powershell
py -3.12 benchmarks/competitive/release.py verify
py -3.12 benchmarks/competitive/run_tunic.py --out .tunic/competitive-new-run.json
py -3.12 benchmarks/competitive/score.py .tunic/competitive-new-run.json
```

Run paths cannot be overwritten. MCP transcripts and isolated event-prefix
workspaces are retained in `.tunic/competitive/<unique-id>/`. The current
agent can inspect these directly; the runner never calls a secondary
assistant, Qwen, Ollama, or an embedding service. Initialization, tool
discovery, refresh, and context requests use the shipped stdio server.

`release.lock.json` pins source bytes, tasks, gold, parameters, adapter,
and scorer. Verification refuses changed inputs or undeclared corpus
files. `release.py lock` uses exclusive creation and refuses to replace
an existing lock. The checked-in candidate snapshot has a reproducible
identity; the broader suite remains unfinished. Future editions get
separate identities, preserving this snapshot and its history.

## Common results and honest scores

An adapter emits the same result envelope for each named arm:

```json
{
  "benchmark_digest": "the verified release digest",
  "arm": "native",
  "track": "agent",
  "cohort": {"model": "pinned identity", "hardware": "pinned identity"},
  "rows": [{
    "id": "launch-gateway",
    "status": "complete",
    "answer": ["exact answer atom"],
    "abstain": false,
    "evidence": ["corpus/source.md"],
    "measurements": {"context_tokens": 100}
  }]
}
```

The scorer rejects wrong releases, unknown/duplicate task IDs, malformed
answers, and invalid measurements. Missing, failed, and timed-out tasks
stay in denominators. Partial resource telemetry produces unknown means
and percentiles. Retrieval-only rows receive **no agent accuracy score**.
No winner is inferred automatically. These rules prevent a successful
transport call or cheap failed answer from being presented as model use.

Source precision/recall measure annotated document localization. They do
not establish claim entailment, edge correctness, or full provenance
quality. Labels are hand-authored development expectations, not independent
audited ground truth; alternate sufficient sources still need auditing.

## First measurement

The retained [Tunic retrieval run](runs/tunic-retrieval-v1.json) completed
16/16 MCP requests, with annotated-source recall **1.0** and source
precision **0.3125**. This small corpus often fits almost entirely into a
pack, so perfect source recall is easy and context noise remains high.
Five independent prefix batches took approximately 0.26–0.29 seconds each,
including startup and refresh. This is neither per-query p95 nor an
incremental update benchmark. Tokens, cost, peak memory, and agent answer
quality remain unmeasured.

Graphiti, Zep, Cognee, Mem0, LightRAG, Hindsight, and native have no results
on this candidate yet. Their cells remain unknown. The reference roster
is fixed; real adapters, broader formats/scale, label audit, an isolated
current-agent execution cohort, repetitions, and uncertainty procedures
are required before a full comparative release.
