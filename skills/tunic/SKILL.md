---
name: tunic
description: Source-grounded code memory for AI agents. Use when working in a code repository to answer dependency questions (what breaks if X changes, who calls X, what does X call) with cited evidence, to record verified outcomes as durable memory, or to inspect unresolved gaps. Prefers local SQLite evidence over guessing; abstains when evidence is missing.
---

# Tunic skill — source-grounded code memory for AI agents

Tunic is a local code-intelligence graph an agent uses as persistent
working memory: ingest a repository once, then retrieve dependencies
with evidence, propose new relationships, commit reviewed knowledge
through a governed path, record corrections, and inspect unresolved
gaps. It never sends data anywhere; every tool runs against a local
SQLite database over a fixed workspace root.

## Connect

Serve one stdio session per repository (newline-delimited JSON-RPC 2.0):

```
python -m tunic --db <path>.sqlite mcp --root <repo>
```

`initialize` → `tools/list` → `tools/call`. Tool arguments are
validated; failures return structured errors, never tracebacks. The
server accepts only the `local` principal: a caller-supplied
`principal` anything else is denied loudly.

## Tool workflow

0. **Start with `context(task, limit?, max_bytes?, snapshot?)`.** One
   bounded pack: each relevant node with its code, proven `calls` and
   `called_by`, unverified `refs` and `ref_by`, claims, open questions,
   plus doc passages and `gaps`. Read its `trust` field. Treat `refs`,
   `ref_by`, and `via: "words"` as leads to check, never as proof. An
   empty `calls` on a node below the `edges` rung means unknown, not none.
   Use `coverage` before reading an empty answer as absence.
1. **Retrieve for the audit trail.** `retrieve(task, limit?, policy?, threshold?,
   select?, snapshot?, container_limit?, budget?, mmr_lambda?)`
   fuses exact, name, lexical, and (if configured) semantic stages.
   Use `policy="current"` for answers (superseded knowledge
   excluded). Selection is on by default since 2026-11-02
   (frozen-suite acceptance PASS-scoped; `select=false` opts out to
   raw fusion); it returns its trace; `snapshot`
   pins a view; `container_limit` (default 2, 0 disables) bounds
   method-to-class expansion. `budget={"graph": N}` adds the
   dependency-neighborhood expansion stage (default off); `mmr_lambda`
   in [0, 1] (default 1.0 = pure relevance) diversifies fused order.
   An empty result is a finding, not a failure: record what is
   missing.
2. **Inspect evidence.** `related(symbol)` / `impact(symbol)` /
   `timeline(symbol)` expand one exact `path::symbol` key with
   provenance. `callees(symbol)` traverses forward instead: `direct`
   (single-hop static calls), `reachable` (bounded multi-hop with
   depth and witness premises), `candidates` (proposed, never
   traversed), and `cold` (unresolved call sites with what would
   be needed to resolve them). Use `related` for who calls X,
   `callees` for what X calls; never present `candidates` as fact. `references(symbol, extensions?, limit?)` finds exact identifier occurrences in non-parsed sources (e.g. `.astro`) with byte spans: investigation leads, NEVER verified call edges. Keep the two questions separate: `related`/`callees` prove what the graph establishes; `references` tells you where else to investigate. Never present the second as the first. `review(document)` reads the structured acquisition
   report for one note file. Never treat a symbol from a question as
   an answer; only symbols the tools return.
3. **Review candidates, never invent edges.** `candidates` lists live
   proposed relationships with derivation chains. To acquire one,
   `acquire(candidate_id, justification, task?)`: the proposition
   commits through the same governed reconcile/promote path as any
   proposal, and the tool publishes before returning. Empty
   justifications and non-live ids are refused.
4. **Refresh after direct writes.** `promote(...)` only writes the
   claim block; `note(...)` only appends text. Call `refresh` before
   trusting that new writes are visible. (`acquire` and `correct`
   publish inside the call.)
5. **Correct explicitly.** `correct(subject, object, rel, ...)` with
   `contradicts` to contest, `retracts` (plus `retract` naming the
   relation) to withdraw, `supersedes` to replace. Corrections are
   graph operations with authority recorded, not deletions.
6. **Mind the gaps.** `frontier(status?, limit?)` lists
   unresolved observations the agent could close with evidence.
   The ledger is raw diagnostics, not a ranked queue: sample it with
   `limit` (the reply reports `total`), and prefer few, verified
   closures over sweeping the backlog.

## Trust labels (never override these)

| Label | Meaning | Usable as answer? |
| ----- | ------- | ----------------- |
| OBSERVED / syntactic path | Static code extraction | Yes |
| ASSERTED + authority + `asserted_in_source` | Reviewed session claim | Yes, cite authority |
| CONTAINER CONTEXT | Reached via a containing class; no direct edge | Yes, name the bridge |
| CANDIDATES / proposed | Mechanically derived, unverified | No — review first |
| WITHHELD / insufficient | Excluded evidence | No |
| DISPUTED / contradicted | Contested | Only flagged, never alone |
| CORRECTIONS / retracted | Withdrawn knowledge | Never list as answer |

A method question may retrieve its class (`method of path::Class`):
constructing a class embeds its behavior, so a change to a member
reaches every function that constructs the class. The edge remains
`caller → Class`; no method edge is synthesized.

## Abstain rules

Abstain (say what is missing) when: no candidate's relationship
answers the task; the only support is proposed or withheld; the
evidence is disputed with no undisputed alternative; endpoints are
not exact `path::symbol` keys. Never guess a callee, never promote
an unknown id, never present a candidate listing as established fact.

## Worked shape

```
retrieve("what breaks if start changes")      # candidates: Gadget.start
candidates()                                  # live: create -> Gadget
acquire(id, "create constructs Gadget ...")   # receipt: committed/add
retrieve("what breaks if start changes")      # impacts: create [container]
correct(...)                                  # if later evidence refutes it
frontier()                                    # what is still unresolved
```
