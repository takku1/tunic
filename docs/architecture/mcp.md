# MCP agent-memory boundary

Tunic exposes its memory subsystem to external coding agents through a
minimal MCP-compatible server. The transport is newline-delimited JSON-RPC
2.0 over stdio; any MCP client can connect with a command entry:

```json
{"command": "python", "args": ["-m", "tunic", "--db", "<db>", "mcp", "--root", "<repo>"]}
```

## Tools

| Tool | Reads | Writes |
| ---- | ----- | ------ |
| `retrieve` | fused candidates, impacts, mentions, claims, cited document passages | none |
| `related`, `impact`, `callees`, `timeline` | compiled evidence for one key (reverse, reverse-impact, forward, timeline) | none |
| `references` | exact identifier occurrences in non-parsed sources (byte spans, digests; investigation leads, NOT call edges) | none |
| `review` | acquisition report for one document | none |
| `candidates` | live proposed dependencies | none |
| `acquire` | reviewed candidate + justification | commits the candidate through the governed path, then ingest publishes it |
| `correct` | governed correction path | records retraction/correction claim, then ingest publishes it |
| `frontier` | open knowledge gaps by status | none |
| `note` | — | timestamped append inside the root |
| `promote` | duplicate/contradiction check | one explicit claim block, then ingest publishes it |
| `refresh` | — | incremental re-ingest of the fixed root |
| `context` | `task`, `limit`, `max_bytes`, `snapshot` | start here: one bounded evidence pack, see [context packs](context-pack.md) |
| `coverage` | — | per-rung file counts, ledger reasons, missing grammars for the fixed root |

`retrieve` takes `task`, `limit`, `policy`, and `threshold`, and returns the
same fused answer as the Python API: per-candidate scores, stage origins,
retrieval method, and abstention (`outcome: empty`) when neither code nor documents qualify. R2 (2026-11-02): a documents channel returns bounded cited prose passages (`documents`: doc, section, span, digest, snapshot, score, excerpt) from published `::document` sources only; prose is OBSERVED text, never a code claim, and never enters candidate selection.
On large corpora a nonsense query still finds weak single-term lexical
matches (observed scores 0.008–0.025 on 1,651 real documents), so callers
that need strict abstention should set `threshold` (0.05 abstained there).

## Safety properties

- The workspace root is fixed at server start; no tool can change it.
- Agent-controlled document paths are confined inside the root by `Memory`
  (`..` escapes, absolute paths, and resolving symlinks are rejected).
- `promote` refuses exact duplicates of asserted claims and refuses to
  promote across an unresolved `contradicts` pair; corrections go through an
  explicit contradicts/supersedes claim first.
- Discovery stages (lexical, semantic) select candidates only; they never
  create graph edges.
- Failures return structured errors (`isError` with a message for bad tool
  arguments, JSON-RPC errors for protocol violations), never tracebacks.

## Limits

- No authentication or multi-tenant isolation: one server, one trusted
  principal, one workspace. Do not expose the stdio socket to untrusted
  callers.
- Long-lived evidence history is preserved in SQLite; concurrent writers
  race at path-check time. Single-agent use only.
- The semantic stage stays skipped until a local embedding model is
  installed; no download is ever attempted.
