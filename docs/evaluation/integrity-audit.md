# Benchmark integrity audit

Date: 2026-11-04. Asked: are these metrics real, or forced? This records
what each benchmark can and cannot support, and what independent evidence
says. Where this conflicts with an older doc, this one wins.

## Headline

On the only test of real use, Tunic did not measurably help Claude. With
Grep, Glob, and Read available, adding Tunic changed neither accuracy nor
cost in a useful direction, on direct or transitive call questions.

| Claude Sonnet, tools + multi-turn | F1 | Recall | Complete | Input tokens | Cost |
|---|---|---|---|---|---|
| Direct, `search` only | 0.809 | 0.938 | 20/24 | 20,288 | $0.019 |
| Direct, + Tunic without `context` | 0.808 | 0.938 | 20/24 | 30,128 | $0.021 |
| Direct, + Tunic with `context` | 0.818 | 0.952 | 21/24 | 29,734 | $0.020 |
| Transitive, `search` only | 0.627 | 0.878 | 10/16 | 121,679 | $0.104 |
| Transitive, + Tunic without `context` | 0.600 | 0.775 | 8/16 | 157,061 | $0.105 |
| Transitive, + Tunic with `context` | 0.621 | 0.788 | 8/16 | 137,706 | $0.095 |

`context` against `search`: direct 1 win, 2 losses, 21 ties; transitive 6
wins, 6 losses, 4 ties. Exact sign test p = 1.0 for both.

Scope of that finding: one model, one 30-file Python package, call
questions only. Precision is depressed equally in every arm, because the
trace did not exercise every real caller. Nothing here tests what search
cannot do at all: decisions and supersession across sessions, provenance,
much larger repositories, or cross-language links. That value is
unmeasured, not proven.

Artifacts: `benchmarks/agent/run_claude_skill_ab_v1.py`,
`benchmarks/agent/runs/claude-skill-ab-v1-sonnet*.json`.

## What was checked in the shipped code

No cheating was found. Fixture symbol names and case ids do not appear in
`tunic/` as logic; the only matches are ordinary words (`right`, `actual`,
`combine`). No code branches on a benchmark path or suite.

## What each benchmark supports

| Benchmark | Real for | Not evidence of |
|---|---|---|
| Golden `extract()` comparison | Refactors preserving output | Output being correct |
| W1 | Regression on author-written fixtures | Quality; its own label says so |
| Agent suite (`benchmark --agent`) | Retrieval regression | Agent behavior: no model runs, and `none` is 0 by definition |
| Memory suite | Stored claims read back | Memory or learning: the harness writes the gold, then reads it |
| Precision scoreboard | False positives on real text | Recall on MochiOS; labels are first-pass |
| Competitive scorecard | Executed rows only | Vendor rows, which come from vendor docs |

Rescored at equal k over all 14 tasks, including the 2 excluded as
experimental: Tunic 12, BM25 11. That is the real size of the suite's gap.
`benchmarks/integrity/rescore_agent_suite.py` reproduces it.

## Independent evidence

**Edges against the interpreter.** `trace_calls.py` records calls while
Tunic's tests run. 751 real calls across 415 functions, from 11 test
files. Python only, on one package.

| Measure | Value |
|---|---|
| Static recall of real calls | 0.812 |
| Static edges seen at runtime, caller executed | 0.830 (floor: untested branches) |
| Hints, recall / seen at runtime | 0.429 / 0.732 |
| Static plus hints, recall | 0.868 |

Of 141 missed calls: 63 are methods on a variable of unknown type, about
35 are function-local imports, 18 are constructors, and the rest are
values, dispatch, or inheritance. Per-hop misses compound, which likely
contributes to the transitive result.

**Symbol walker on held-out repositories.** Earlier figures came from the
repositories its rules were tuned on. On untouched ones:

| Check | Result |
|---|---|
| Python `ast`, 1,500 files (Gymnasium, MetaGPT, OpenHands) | 9,178 definitions, precision and recall 1.000 |
| Spec oracle, 11 languages | 3,002 symbols, recall 0.988 |

Julia is lowest at 0.879, mostly because specs record the qualifier in
`function Base.show`. Precision beyond Python is unmeasured.

## Results that were retracted

| Claim | Why it fell |
|---|---|
| `context` 0.93 vs `retrieve` 0.12 on Qwen | Gold came from the edges packs print: partly a copying test, and it did not transfer to Claude |
| `retrieve` 0.25 in the Claude pilot | Harness fault: the skill named a denied tool, and Read was removed. Artifact deleted |
| `retrieve_followup` 0.000 on Qwen | Payloads of up to 4.6 MB were truncated at the context limit. Invalid |
| Qwen3-Coder 30B on the final harness | Crashed with HTTP 400 on those payloads. `pack-ab-v1-qwen3coder30b.json` is an earlier three-arm run with no runtime set |
| Recall 1.000 across spec languages | Measured on tuning data; held-out is 0.988 |
| Renaming hints makes models respect them | No effect on Qwen 7B: 78 unproven `verified` claims before, 81 after |

One finding from the Qwen runs stands, narrowly: on caller questions the
gold was in both payloads, and a 7B model read the pack far better (0.917
against 0.125). The 30B gap was smaller (0.844 against 0.397), and Claude
showed none. Pack format appears to help small models, not strong ones.

## Defects this exposed

- `related` and `callees` can return megabytes: median 1.7 MB across
  follow-up payloads, maximum 4.6 MB. That is unusable in any context.
- The skill and tool list cost Claude about 9,500 extra input tokens per
  direct-question run, with no accuracy to show for it.
- Tunic's recall fell behind plain search on transitive impact.

## Rules from here

- A claim about helping an agent needs a real agent and a search baseline.
- Gold must not come from the system under test.
- Rules tuned on a corpus are reported on a different corpus.
- A surprising score gets its raw replies read before it is reported.
