# Ingestion: the coverage ladder

Date: 2026-11-04. Replaces per-type allowlisting. For what each type
yields today, see [file-type coverage](file-type-coverage.md).

## The rule

Every file in scope is admitted, or it is counted with a reason. Each
admitted file lands on the highest rung it qualifies for:

| Rung | What it gives | What it needs |
|---|---|---|
| `edges` | Verified calls | A stage that proves bindings for the language |
| `entities` | Key paths, schema nodes | A family parser |
| `symbols` | Named definitions with byte spans | Any tree-sitter grammar |
| `text` | Stored, citable, retrievable | Decodable UTF-8 |
| `ledger` | A count and a reason, no bytes | Nothing |

Missing knowledge costs a rung, never the file. An extension nobody has
heard of is searchable text, not a silent gap. This is the doctrine
"unresolved is not absent" applied to the scan itself, which was the one
place that still dropped things without saying so.

`tunic coverage <root>` (and the `coverage` MCP tool) reports the result.
An agent should check it before reading an empty answer as absence.

## Why the allowlist went

A suffix set forced four things to be true at once, and they drifted:

- A type had to be added in up to six places (scan, `parse_source`,
  `_collect_code`, the store's refresh, endpoint kinds, the resolver
  list), so each tier forked the same wiring.
- "Not prose" meant "Python module" in the incremental path, so `foo.ts`
  beside `foo.py` joined module `foo`. Refresh then deleted verified
  edges that a full ingest kept. Admitting more types would have made
  this much worse.
- Unlisted files disappeared with no diagnostic: 67 of 324 files on the
  field-test site. The docs described `.css`, `.html`, and Dockerfiles
  as inventory-only, but the code never admitted them.
- Matching was case-sensitive in the scan and case-insensitive in the
  engines, so `.R`, the normal spelling, was dropped.

## The three pieces

- `tunic/filetypes.py` is the only authority on what a file is, and
  `admit` is the only place a file can be turned away. Adding a language
  is one table row. It is stdlib-only.
- `tunic/pipeline.py` is the only list of resolver stages. Full ingest
  and refresh both iterate it, so they cannot disagree. Adding a family
  is one row plus its `resolve` and `affected` functions.
- `tunic/structure.py` gets symbols from any grammar with no
  per-language data: the grammar's `tags.scm` when it ships one, else a
  walk over the naming convention grammars share. Symbols carry
  `structural_walk_v1` and never imply an edge.

Grammars come from `tree_sitter_<language>` wheels, then from languages
already cached by `tree-sitter-language-pack` (372 languages, MIT).
Ingest never touches the network: `coverage --fetch-grammars` is the
explicit step, and a snapshot never depends on what a server returned.

## What keeps new files out of the proof

Admitting everything must not move results that were already correct.

- Text-rung files get a `path::document` record but not `text: True`.
  They are retrievable, and they are invisible to claim parsing, mention
  scanning, and document-frequency statistics.
- Only Python enters the module table.
- Retrieval over new material is additive. Non-Python symbols fill
  whatever the frozen Python BM25 left unused in the lexical budget. File
  passages ride behind prose under their own budget (`files`, default 2,
  `0` disables). Existing results keep their exact positions.
- Key-path and schema entities match bare prose only when the leaf is
  constant-shaped (`APP_NAME`). See "Decisions that changed output".

## Refused on sight

Snapshot bytes reach SQLite and, through retrieval, model context, so
credentials are refused rather than ranked low. `.env` and its variants,
key material (`.pem`, `.key`, `id_rsa`, ...), and credential stores are
`sensitive` in every family, including ones that parse. A private-key
header refuses an unknown file by content. Templates such as
`.env.example` stay, since they carry no secret.

This is a name-and-header screen, not secret scanning: a token pasted
into `settings.yaml` is still ingested, as it always was.

Other ledger reasons: `binary` (NUL in the first 8 KiB), `oversize`
(over 1 MiB, unknown families only), `generated` (`.lock`, `.map`,
`.log`, `.sum`, `.snap`), `empty`, `undecodable`, `unreadable`.

## Evidence

Measured 2026-11-04. The golden set is `extract()` output on five
corpora: the repo's own tests and docs, a TS/Astro site, Rust, Go, and
PHP repositories.

| Check | Result |
|---|---|
| Stage registry and module split | Byte-identical on all five corpora |
| Admission | Additive: no source, symbol, or edge lost or changed |
| Walker against Python `ast`, true labels | 5,640 definitions, recall 1.000, precision 1.000 |
| Walker against the hand-written specs | 2,581 symbols, 15 languages, recall 1.000 |
| Refresh against full rebuild | Equal on a mixed tree, each type edited and deleted |
| Acceptance, base against new | W1 and memory results unchanged; agent suite 12/12 (lexical 11/12) |

Oracle sample sizes vary. C, C++, C#, Go, PHP, PowerShell, and Rust use
real code (157 to 696 symbols). Java, Kotlin, Scala, Swift, Lua, Julia,
Ruby, and Bash rest on test fixtures (4 to 13 symbols), so treat those
rows as smoke tests. The spec oracle measures recall only, because specs
index less than the walker finds. Extras there were inspected by node
type and were real definitions (macros, namespaces, Go types).

### Where the walker is weak

Grammars without a tags query can fall short, and the rule is to abstain
rather than guess:

| Language | Behavior |
|---|---|
| Dart | Finds classes, misses methods and top-level functions |
| Verilog, HCL | No symbols; files stay on `text` |
| Objective-C | Misses `@interface` names and method names |
| Zig | Misses `const X = struct` names; methods are found |
| CSS, HTML, Dockerfile | Nothing to name; files stay on `text` |

## Decisions that changed output

**Common-word key leaves stopped being bare mentions.** Tier 3 made every
config key a mention target by its leaf, so each prose `type`, `name`, or `host`
matched every key with that leaf. On the site this was 156,904 of 158,690
diagnostics, 54 MB per snapshot manifest, and 94% of ingest time. On
ProjectFoundry the manifest exceeded SQLite's limit and ingest crashed.

| Repository | Before | After |
|---|---|---|
| seancarneywebsite diagnostics | 158,690 | 3,333 |
| seancarneywebsite ingest | 19.9 s | 5.3 s |
| ProjectFoundry ingest | Crash (`string or blob too big`) | 66.0 s, refresh 4.0 s |

A leaf must now be constant-shaped to match bare prose, so `APP_NAME`
links and `type` does not. The rule was measured, not guessed. Excluding
every leaf lost a real `APP_NAME` link. Allowing any underscore brought
back 9,874 assertions and 57,583 candidates on ProjectFoundry, from
snake_case keys repeated through data files (`candidate_id`), and none of
that noise was constant-shaped.

This removed assertions: 129 on the site, 40 on comply, 2 on opengrc. All
were inspected. Every one was a `proposed` or `proposed_ambiguous` name
match, nearly all on common words (`nav`, `weight`, `name`). Three are
arguably real losses: `event_weddings` (2) and `node-version` (1). No
verified edge, claim, or exact mention was lost, and
`` `package.json::type` `` still resolves.

**Tool caches left scope.** `.pytest_cache`, `.mypy_cache`, `.tox`, and
similar directories are now pruned, including prose inside them.

## Adding a type

| To add | Do this |
|---|---|
| A language, symbols only | One `CODE_LANGUAGES` row, or nothing if the language pack detects it |
| Verified edges | A spec in `unigraph.py` plus the fixture battery |
| A new family | A module with `resolve` and `affected`, plus one `STAGES` row |

## Open

- Lexical fill is Python-first by construction. In mixed repositories
  Python can use the whole budget, so a joint ranking is the better end
  state. A first attempt to add Python word matches to `answer()` failed
  acceptance; see [context packs](context-pack.md).
- `BM25` and `TextNotes` still walk the filesystem. Moving them onto the
  snapshot would remove the last parallel notion of what is in scope, at
  the cost of re-baselining frozen rankings.
- `unresolved_call` is now the largest diagnostic kind on real repos.
- `mention_candidate` is still 9,788 entries on ProjectFoundry, from code
  symbols. Mention scanning costs lines times keys.
