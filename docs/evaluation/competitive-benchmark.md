# Fixed competitive benchmark

This is Tunic's governing development objective: improve against the
same benchmark until it outperforms Graphiti/Zep, Cognee, Mem0,
LightRAG, Hindsight, and the native agent baseline on every declared
primary metric. A transport smoke test or Tunic-only regression pass
does not meet that objective. The compiler/transformer design principle
is owned by [context IR](../architecture/context-ir.md).

## Fixed comparison targets

| Target | Required workload emphasis |
| --- | --- |
| Graphiti / Zep | Temporal knowledge, relationship provenance, changing facts |
| Cognee | Knowledge extraction, graph construction, broad ingestion |
| Mem0 | Persistent memory, retrieval quality, efficiency |
| LightRAG | Graph retrieval quality and latency |
| Hindsight | Long-term memory, reflection, contextual recall |
| Native | Same agent, ordinary context and file/search tools, no added memory system |

These are benchmark requirements supplied by the project owner, not
claims about measured product capabilities. All systems face all tasks;
the emphasis column does not grant exclusive task assignments.
Graphiti and hosted Zep are distinct implementations: pin the concrete
variant for each run and keep separate rows if both are evaluated.

## What stays fixed

Freeze the corpus bytes, event/session ordering, task wording, evidence
labels, gold answers, splits, scoring code, metric definitions, budgets,
timeouts, repeat counts, and baseline configurations as one versioned,
content-addressed release. Once frozen, development changes Tunic, not
the exam. Preserve failed and unfavorable results. New workloads belong
in a separately named future suite; never silently replace this suite.

Competitor versions/configurations and their reference results remain
pinned. Later competitor releases get additional named rows, without
overwriting the reference target. Changes to agent model or hardware
create a new comparison cohort; rerun every arm in that cohort.

The contract and roster are established here. An executable
[16-task development core](../../benchmarks/competitive/README.md) now has
a content-addressed candidate snapshot, common scorer, and direct Tunic
MCP adapter. A complete corpus and external adapters have **not** yet
been frozen or executed. Existing W1,
precision, arena, lifecycle, and agent fixtures are development material
and historical evidence, not a completed implementation of this contract.

## Primary metric vector

Report the full vector, overall and per workload. Do not hide regressions
inside a weighted composite. Each rate uses a fixed denominator.

| Metric | Direction | Definition |
| --- | --- | --- |
| Task success | Higher | Correct completed tasks / all scheduled tasks |
| Evidence precision | Higher | Correct supporting evidence items / all claimed supporting items |
| Evidence recall | Higher | Retrieved required evidence items / all gold required items |
| Temporal accuracy | Higher | Correct current/as-of answers / all temporal tasks |
| Update correctness | Higher | Correct post-change answers / all correction, replacement, and deletion tasks |
| Abstention accuracy | Higher | Correct answerability decisions / all answerable and unanswerable tasks; also report each class separately |
| Cross-session recall | Higher | Correct answers using earlier-session facts / all cross-session tasks |
| Retrieval latency p95 | Lower | Query-to-context-ready elapsed time across scheduled retrievals |
| End-to-end latency p95 | Lower | Task start to final answer, including tool work |
| Context tokens per scheduled task | Lower | All supplied context tokens, including tool results and repeated input |
| Total cost per scheduled task | Lower | Ingestion, extraction, updates, retrieval, and answer-generation cost amortized over the fixed workload |
| Peak memory | Lower | Peak resident memory of the complete measured stack, including child services |

Also report ingestion coverage/precision by input type, ingestion time,
storage, update visibility delay, stale-answer rate, unsupported-claim
rate, p50 latencies, total tool calls, and raw byte counts. Missing tokens,
cost, or service memory measurements are unknown, never zero. Efficiency
claims require matched task quality; empty context and failed answers
cannot count as efficiency wins. Timeouts/errors remain task failures;
report their rate, charge consumed resources, and retain timeout entries
in latency reporting rather than calculating a success-only percentile.

## Fair execution and model interpretation

Use identical source information, event order, agent model/version,
sampling, task budgets, machine allocation, and evaluation prompts.
Systems may use their own representations and retrieval methods: forcing
Tunic into another system's chunk format would remove the intervention.
Record all extraction/embedding dependencies and their costs. Native has
the same original sources and ordinary tools; preserve its native context
across the declared session boundaries under a fixed policy. Do not give
it an invented empty-memory handicap.

The current agent consumes tools directly; do not substitute a local
Qwen or a secondary assistant for it. Direct transport is available in
`benchmarks/agent/direct_mcp.py`. Each scored arm needs an isolated agent
context so answers and memory from another arm cannot leak into it.
Without that isolation, label the run development/dogfood, not a valid
paired answer-quality comparison. Gold is accessible only to the scorer.

Measure representation changes separately from retrieval changes using
identical selected evidence, at equal token budgets and at equal quality.
Compare task success, evidence errors, input tokens, tool calls, and
end-to-end latency. The compiler aims to reduce the model's reconstruction
work; a short or unreadable format is not sufficient evidence of that.
Token IDs, hidden states, KV state, or custom runtime instructions are
separate interventions requiring actual runtime support and validation.

## Completion criterion and current status

The target is a strict improvement on each primary metric against each
reference arm, not merely a higher average score. Report ties and mixed
outcomes explicitly; a ceiling tie is not strict outperformance. Freeze
repeat counts and paired uncertainty procedures before the first scored
release, and show both estimates and uncertainty. Until all required
reference arms have comparable results, overall superiority is unmeasured.

Next implementation work: assemble and audit the common corpus; freeze
tasks/gold and execution parameters; implement real adapters for every
reference arm; run and retain the reference results; then repeatedly
measure Tunic changes against that unchanged release. Historical vendor
numbers and mismatched local-model runs do not fill missing cells.
