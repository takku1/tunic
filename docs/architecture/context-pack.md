# Context packs: the graph as an LLM reads it

Date: 2026-11-04. Implements the "optimized for AI consumers" goal from
[file-type coverage](file-type-coverage.md). Code: `tunic/pack.py`.

## The split

| Surface | Reader | Purpose |
|---|---|---|
| `retrieve` | Auditor | How an answer was reached: every stage, score, and selection decision |
| `context` | Model | What to act on: code, links, claims, gaps, in one bounded call |

Before packs, a candidate was a key and a score. A model had to spend a
further call per symbol to learn what it was, while the payload carried
pipeline internals and repeated digests. `context` is now the first step
in the agent skill.

```
tunic --db x.sqlite context "how does the scan cache snapshots" --root .
```

## Shape

```json
{"schema": "tunic.pack/1", "snapshot": "...", "task": "...", "trust": {},
 "nodes": [{"id": "src/scan.rs::scan", "kind": "function", "rung": "edges",
            "at": "src/scan.rs:143", "src": "pub fn scan(...",
            "calls": [], "called_by": [], "refs": [], "ref_by": [],
            "docs": [], "claims": [], "open": [], "superseded_by": [],
            "via": "words", "src_cut": true}],
 "docs": [{"id": "...", "at": "README.md@0-1200", "text": "..."}],
 "gaps": [], "coverage": {"edges": 16, "text": 2},
 "omitted": {"nodes": 0, "docs": 1, "src": 1}}
```

Empty fields are left out. `trust` states once how far each field can be
relied on, so the pack needs no external documentation to be read
correctly. Snapshot id plus path and span pin every byte, so per-item
digests are gone. Whatever the byte budget cuts is counted in `omitted`.

## Proof and hints stay apart

| Field | Meaning | Source |
|---|---|---|
| `calls`, `called_by` | Proven by a static binding rule | Snapshot assertions |
| `refs`, `ref_by` | A unique symbol name is used in the code | Computed at read time |
| `via: "words"` | The task's words match; the graph did not select it | Computed at read time |

Hints are how every language gets a connected neighborhood, including
languages with no edge spec. The model is the right judge of a name
match, because it can read the code beside it. The graph never asserts
one: hints are not stored, so they cannot reach a snapshot, an impact
traversal, or a proof. `tests/test_context_pack.py` pins this.

A name only counts when it is unique in the snapshot and used rather than
merely bound: called, reached through a path or receiver, or capitalized
like a type.

| Repository (language) | Recall of verified calls | Hints that are verified calls |
|---|---|---|
| comply (Go) | 0.86 | 0.55 |
| hhdm (Rust) | 0.73 | 0.31 |
| tunic (Python) | 0.45 | 0.82 |

The last column is a floor, since verified edges under-approximate. It is
still why `trust` says to interpret these rather than cite them. A model
should expect a meaningful share to be wrong, especially in Rust, where
`.field` access looks like a use. Recall is low in Python because unique
names are rarer there. Sampled on up to 323 callers per repository.

## Why word matching lives here and not in `answer()`

`BM25` matches whole identifiers, so "charge invoice" cannot find
`chargeInvoice`. Models ask in words, so `SnapshotLexical` splits
identifiers. Feeding those matches into `answer()` regressed the agent
suite from 12/12 to 11/12 by surfacing superseded symbols. They are
offered here instead, labeled `via: "words"`, only when most of the
task's content words match, and never for a superseded symbol.

## Not measured

No model has been run against packs. The evidence above covers what a
pack contains, not whether a model answers better with it, because
model access is blocked (see [status](../STATUS.md)). Comparing
`retrieve` and `context` on the agent suite with a real model is the
acceptance test still owed. A pack is not always smaller than `retrieve`:
on the sampled tasks it ranged from about half the size to about 1.7
times, while carrying code and links that `retrieve` does not.
