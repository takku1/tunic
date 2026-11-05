# Spec-derivation experiment (2026-11-03)

Question: can the syntax half of Tunic's per-language specs be generated
from pinned `node-types.json`, leaving semantic policy hand-reviewed?

## Donors (scouted read-only via Cannibalize; cards vaulted)

- **helix-editor/helix** (`runtime/queries/rust/tags.scm`, MPL-2.0,
  idea-only): checked-in per-language query files mapping grammar
  nodes (`function_item` + `name` field) to definition kinds. Precedent
  for generated mappings as checked-in artifacts, including the
  `function_item` vs `function_signature_item` (definition vs
  prototype) distinction Tunic also needs.
- **semgrep/ocaml-tree-sitter-core** (idea-only): mechanical
  derivation `grammar.json` -> checked-in typed artifacts
  (`simplify-grammar` + codegen). Precedent for derivation-from-grammar
  with interpretation-preserving rewrites.
- **comby** (doctrine, from its own FAQ): generic language support
  "sacrifices the ability to recognize many predefined
  language-specific constructs". Used as the explicit counter-argument:
  the win condition is identical evidence with less manual work, not
  broader coverage.

## Architecture (as directed)

- Universal engine: `tunic/unigraph.py` R1/R2 + provenance + receipts
  (unchanged).
- Derived syntax adapter: `generate.py` (this directory) over pinned
  `node-types.json` — six fields only
  (`def/call/class/ctor/literal/import` node sets).
- Reviewed semantic policy: call shapes, receivers, scope units,
  import styles, `mod_item`-as-import, proof eligibility — stays in
  the hand specs, never generated.
- `overrides.json`: the reviewed trim/add deltas, checked in.
- `generated/*.json`: the assembled artifacts, checked in with
  grammar tags and generator version. Never produced at ingest time.

## Equivalence gates (all required, all pass)

| Gate | Result |
|---|---|
| Mapping parity (generated − trim + add == hand spec, 6 fields × 3 langs) | PASS (`test_spec_derivation.py`) |
| Real-repo byte-identical output (memchr@bd6068c, gson@5773109, argparse@7d093f8): symbols, assertions, spans, diagnostics, receipts, snapshot ids | PASS, full JSON equality |
| Full suite (950 tests) with generated specs substituted | PASS, 950/950 |
| C++ parser-error behavior preserved (whole-file ERROR skip) | PASS (cpp outputs identical incl. `unsupported_syntax`; the limitation is engine-level, untouched) |
| Over-fitting check: generator must not memorize the 3 grammars | Partially addressed: rules are structural (fields, hidden-subtype expansion, name patterns), no per-language branches in `generate.py`; the rust `mod_item` case is an explicit override, not a hidden special case |

## Maintenance measurement

| Item | Lines |
|---|---|
| Hand specs, 3 languages (java/cpp/rust sections of `unigraph.py`) | 137 |
| `generate.py` (fixed cost, shared across languages) | 117 |
| `overrides.json` (reviewed deltas, 3 languages) | 41 |
| `test_spec_derivation.py` (parity pin) | 35 |
| Generated entries kept per language | 10 / 9 / 7 |

At 3 languages the generator is roughly break-even on lines
(158 vs 137). The win is marginal cost per new language: ~45 lines
of hand spec from grammar docs vs ~15 lines of reviewed trim/add
against machine-generated candidates, plus a parity test that fails
audibly on divergence instead of silent drift. Rejection criterion
(more generator heuristics than spec lines) not triggered: two
candidate generic rules were tried and REJECTED during the experiment
(optional-body-container imports over-fired; bare name-field defs
miss `function_definition`), each recorded here rather than patched
around.

## Verdict

Experiment succeeds as an architecture experiment. Recommended next
step (separate decision): accept generated artifacts + parity test as
the authoring path for the 3 measured languages, keep hand specs
authoritative until that decision. Do NOT auto-derive the remaining
11 specs without repeating the equivalence gates per language.
Semantic differences (Rust `use as` aliasing, `Type::method`
receivers, Java overloads, C++ declarators) remain policy work;
derivation only thins the syntax adapter, exactly as directed.
