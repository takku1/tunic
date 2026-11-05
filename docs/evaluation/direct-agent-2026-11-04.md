# Direct current-agent exercise, 2026-11-04

The current Codex agent read `skills/tunic/SKILL.md`, launched the shipped
MCP stdio server, inspected its returned evidence, and checked it against
source. No secondary assistant, local Qwen, inference API, or embedding
service was used. The host did not expose Tunic as a registered MCP tool;
`benchmarks/agent/direct_mcp.py` supplied the actual protocol transport.

Reproduce from the repository root on Windows:

```powershell
py -3.12 benchmarks/agent/direct_mcp.py --root tunic --db .tunic/direct.sqlite --refresh --calls benchmarks/agent/direct_calls.json --out .tunic/direct-context.json
py -3.12 benchmarks/agent/direct_mcp.py --root tunic --db .tunic/direct.sqlite --calls benchmarks/agent/direct_calls.json --out .tunic/direct-warm.json
py -3.12 -m unittest discover -s tests -p test_mcp*.py -q
```

The saved receipts contain full requests, wire responses, decoded payloads,
and wire sizes. Runtime databases and raw receipts remain under ignored
`.tunic/`. Requests are retained in `benchmarks/agent/direct_calls.json`.
The runner returns nonzero for protocol/tool errors; `ok` measures transport
success only, not retrieval correctness.

## Observations

Five calls plus initialization and tool discovery completed without tool
errors. One run with refresh took **16.4985 s**; a separate process using
the existing database took **1.6919 s**. These are single batch observations
including process startup and serialization, not per-tool latency
distributions or evidence of speedup over another system.

| Check | Observed evidence | Assessment |
| --- | --- | --- |
| Find response limiting | First node `bounded.py::bound`; returned callers include `mcp.py::_bounded_call` and `bounded.py::page` | Relevant and source-confirmed |
| Follow request handling | `callees(mcp.py::handle_request)` returns bounded static evidence and a truncation receipt | Interface succeeds; partial evidence requires paging before exhaustive claims |
| Find pack assembly | First node `pack.py::build`; proven calls include `_fit`, `_gaps`, `_node`, `_size`, `_word_hints`; Memory methods appear separately as unverified hints | Relevant, trust distinction preserved |
| Inspect reverse context | `related(bounded.py::bound)` returns evidence with a truncation receipt | Interface succeeds; not an exhaustive-answer score |
| Missing exact symbol | Reports `not in this snapshot: nonexistent.py::imaginary_symbol`, but includes four unrelated nodes | Gap detection works; retrieval precision fails this negative check |

Context wire responses measured 5,404, 7,318, and 2,210 bytes. Larger
callees/related wire responses measured 36,644/36,648 bytes. The default
32,768-byte bound applies to the decoded tool payload; JSON escaping and
the JSON-RPC envelope add wire overhead. Do not describe it as a wire cap.

All **34 MCP tests passed** on Python 3.12. The initial sandboxed test run
could not access temporary fixture directories; rerunning with permission
passed. No product failure was inferred from that environment error.

Follow-up: exact missing-key queries now suppress fuzzy substitutes and
word hints, while preserving discovery counts in the retrieval audit.
New regressions cover missing files, the same leaf name in another file,
existing-symbol prefixes, and mixed known/unknown questions. Re-running
the direct call list returned no nodes for the missing symbol, with
`unknown_subject` reported explicitly. Raw follow-up receipts are in
`.tunic/direct-after-fix.json`.

This is a direct agent dogfood and transport benchmark. It is not a blind
answer-quality evaluation, a memory A/B result, or evidence of general
retrieval accuracy. The existing unfinished `benchmarks/memory_ab` files
were left untouched. The missing-exact-symbol issue above was investigated
and fixed with targeted regressions; these observations describe the
before and after states separately.
