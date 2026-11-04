# File-type coverage: every ingestion tier and the optimal concept per type

Date: 2026-11-03, ladder added 2026-11-04. Goal from the top: a system
optimized for AI consumers, so every type resolves to the same question -- what can an
agent prove from this file? -- answered with receipts, never prose.

Tiers are about proof strength, not effort. Higher tiers reuse the
same engine (`tunic/unigraph.py`), the same edge shapes, and the same
diagnostic vocabulary. A type moves up by adding a declarative spec
plus tests plus a real-repo validation sample, never by forking the
engine (see the TS-migration rule: identical outputs or it stays).

## Tier 0 — verified call edges (shipped)

| Type | Mechanism | Receipt |
|---|---|---|
| `.py` | stdlib AST, module + import resolution | `python-static-v7` |
| `.ts` `.tsx` `.mts` `.cts` `.js` `.jsx` `.mjs` `.cjs` | tree-sitter R1 same-module + R2 relative import | `ts_static_binding_v1` |
| `.go` | unigraph dir-unit spec (package scope, external-test guard, suffix-unique import dirs) | `go_static_binding_v1` |
| `.java` | generic spec: R1c class-qualified, self, implicit-this-bare, `new` ctors, static `import` + wildcard dirs | `java_static_binding_v1` |
| `.kt` `.kts` | generic spec: expr-body fns, implicit-receiver bare, `import`-bound R2, stem ctors | `kotlin_static_binding_v1` |
| `.scala` | generic spec: brace/multi `import`, `new` ctors, class-qualified | `scala_static_binding_v1` |
| `.cs` | generic spec: `using`-bound R2, static R1c, `new` ctors | `csharp_static_binding_v1` |
| `.c` `.h` | generic spec: header/impl same dir, `static` = private, `sizeof` builtin-silent | `c_static_binding_v1` |
| `.cpp` `.hpp` `.cc` | generic spec: qualified defs are methods, `A::` R1c, `new` ctors | `cpp_static_binding_v1` |
| `.rs` | generic spec: file units, `mod`/`use` R2, `pub` = exported, closures nested | `rust_static_binding_v1` |
| `.swift` | generic spec: dir scope, `import`-bound R2, stem ctors | `swift_static_binding_v1` |
| `.php` | generic spec: `require` pull + `B::` scoped R1c, `new` ctors, `$this` self | `php_static_binding_v1` |
| `.rb` | generic spec: `require` pull + R2, `Foo.new` → `initialize`, blocks walk with params | `ruby_static_binding_v1` |
| `.jl` | generic spec: signature naming/params, `include` pull | `julia_static_binding_v1` |
| `.lua` | generic spec: `require` pull + R2, `obj:m` abstains, closures nested | `lua_static_binding_v1` |
| `.sh` `.bash` | generic spec: `source` pull, same-file R1 | `bash_static_binding_v1` |
| `.ps1` `.psm1` | generic spec: `Import-Module` pull, same-file R1 | `powershell_static_binding_v1` |

Battery: `tests/test_tier1_edges.py`, 73 tests (R1/R2/self/R1c/ctor
edges, shadow/privacy/import/receiver negatives, per-language
receipt pins). Go suite unchanged (21 green): the generic path is
branched, the Go path is byte-frozen. Fixture batteries are modeled
on real code idioms plus a CLI ingest/impact smoke test; per-language
real-repo validation samples (the mcp-go precedent) are the remaining
gap before Tier 0 is fully locked.

Shared engine rules that emerged (all data, no per-language code):
scope-pull (a matched require/include/source/mod/use target joins
the visible scope), class decls indexed as `type` symbols (ctor and
R1c targets), unique-or-diagnostic everywhere, per-file (not
per-dir) rescan tracking for file units, case-insensitive stem match.

## Tier 1 — symbols without edges

`.zig` `.ex` `.exs` `.f90` `.groovy` `.m` `.v` `.pl` `.r` and anything else
with a grammar get parser-backed symbols from `tunic/structure.py`. A spec
is only needed to move a language up to verified edges, so specs stay on
demand. `.json` as data is Tier 3, not code.

## Tier 2 — block splitting (no new grammar, parse what is already parsable)

The industry pattern (chunkhound, vue-eslint-parser, TEOSC/aibom for
notebooks) is unanimous: split the container BEFORE parsing, then
feed each block to the grammar you already have. Never regex-harvest
across block boundaries.

| Type | Split | Blocks feed |
|---|---|---|
| `.vue` | `<script>` / `<script setup>` / `<template>` / `<style>` | script blocks → TS grammar (unigraph); template/style → inventory-only |
| `.svelte` | `<script>` / markup / `<style>` | Same as Vue |
| `.astro` | frontmatter fence + template | frontmatter TS → TS grammar; component markup → inventory-only |
| `.ipynb` | notebook JSON cells | code cells → Python AST (strip IPython magics first, mark stripped); markdown cells → document channel; outputs never ingested |

Cell/magic handling rules: magics (`%`, `!`) are unsupported_syntax
diagnostics on the cell, not silent drops; execution count is not
order (notebooks execute non-linearly -- JupOtter/HeaderGen
literature); per-cell symbols key as `notebook.ipynb::cell[N]::name`
so provenance survives re-execution.

## Tier 3 — structured config as key-path entities (SHIPPED 2026-11-03)

Precedent: Backstage `catalog-info.yaml` (YAML as the dependency
graph source of truth), key-path chunking in production indexers.
Shipped as `tunic/config.py` + `tests/test_tier3_config.py` (13 tests). Parses `.json` (location-tracking walk) / `.yaml` (PyYAML compose marks + safe-load values, needs `tunic-context[config]`) / `.toml` (tomllib + line walk) / `.ini` (line walk) into path-keyed entities (`config.yaml::server.port`) that are
retrievable, mentionable, and citable with byte-exact spans -- but
never call edges. Cross-file `$ref`/`$include` (pointer form) and YAML
anchor/merge keys become `depends_on` claims between key entities
(same relation, new `config` endpoint kind in the signatures table).
Whole-file includes abstain (`unsupported_include`); lockfiles stay
inventory-only (Tier-5); YAML needs `tunic-context[config]`.

## Tier 4 — schema-first reference edges (SHIPPED 2026-11-03)

Shipped as `tunic/schemaref.py` + `tests/test_tier4_references.py` (10 tests).
`.sql` (tables/columns, FK→PK, incl. CONSTRAINT-led and ALTER ADD FOREIGN KEY),
`.graphql`/`.gql` (types/fields, implements, unions, enums, directive-tolerant),
`.proto` (messages/fields incl. nested/oneof/map/extend, enums, services/rpc).
References between entities are a `references` relation distinct from `static_call`,
excluded from traversable claims with syntactic_observation status, so impact
queries structurally cannot confuse a foreign key with a call.

## Everything else — the coverage ladder (2026-11-04)

Tiers 0-4 say how strong proof can get for a type. They no longer decide
whether a file is ingested. Every in-scope file is admitted or ledgered
with a reason, per [ingestion](ingestion.md): a language with a grammar
reaches the symbols rung with no spec, any decodable file is retrievable
text, and binaries, oversize files, generated output, and credentials are
counted rather than dropped. `tunic coverage <root>` reports the result.

## Execution rules (all tiers)

1. Specs are data; the engine is shared. A new language is a spec
   table plus the standard fixture battery plus a real-repo sample.
2. Receipts name the tier (`{lang}_static_binding_v1`,
   `key_path_v1`, `cell_extract_v1`): agents key behavior off
   receipts, not file extensions.
3. Abstention beats invention at every tier: unknown imports,
   dynamic includes, magics, and unresolvable blocks file
   diagnostics, never edges.
4. The frozen-suite rule travels with every tier: new stages feed
   fusion and selection like any other stage (the graph-stage
   precedent), and default-on requires acceptance.
