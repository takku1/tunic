# Tunic graph schema (v2)

Versioned type and relationship validation for the evidence graph.
SHACL-style in semantics, implemented natively in Tunic — no RDF
storage, no external validator. Bumping this version (new types,
changed signatures, new defaults) is a contract change: record it in
[decisions](decisions.md) with paired before/after arena runs.

v2 adds `corrects` (backdated correction, distinct from supersession)
and `retracts` (support withdrawal with multi-support survival), plus
the retraction arithmetic and bitemporal surfacing rules below.

## Entities

| Entity kind | Key shape | Examples |
| --- | --- | --- |
| Code symbol | `relative/path.py::Dotted.Name` | `pkg/mod.py::Parser.parse` |
| Document | `relative/path.md::document` | `guide.md::document` |

Code identity is the exact stable key; document identity is the
source path plus `::document`. Similarity never establishes identity:
uncertain links stay proposals with distinct candidates.

## Assertions

Every assertion carries: `proposition` (relation + caller + callee),
`source` (source id), byte `start`/`end`, `resolution` (expression,
rule, support, qualification), `producer` (extraction profile, e.g.
`python-static-v7`), `status`, and a deterministic `id` over the
edge coordinates (stable across profile bumps).

| Relation | Caller | Callee | Status | Resolution rule | Traverses |
| --- | --- | --- | --- | --- | --- |
| `static_call` | code | code | `syntactic_observation` | `unshadowed_static_binding_v1` | yes |
| `static_call` | code | code | `syntactic_observation` | `ts_static_binding_v1` | yes |
| `static_call` | code | code | `syntactic_observation` | `ts_enclosed_callback_v1` | yes |
| `text_mention` (exact) | document | code | `syntactic_observation` | `exact_key_match_v1` | no |
| `text_mention` (bare) | document | code | `proposed` / `proposed_ambiguous` | `name_match_proposed_v1` + qualification | no |
| `depends_on` | doc entity | doc/code entity | `asserted_in_source` | exact or proposed (same rules) | yes |
| `implements` | doc entity | doc/code entity | `asserted_in_source` | exact or proposed (same rules) | yes |
| `supersedes` | claim | claim | `asserted_in_source` | exact claim reference | temporal filter |
| `corrects` | claim | claim | `asserted_in_source` | exact claim reference | temporal filter (reported as correction) |
| `retracts` | claim | claim | `asserted_in_source` | exact claim reference + `retract` field | removes one support (see arithmetic) |
| `decided_by` | claim | doc entity | `asserted_in_source` | exact or proposed | no |
| `contradicts` | claim | claim | `asserted_in_source` | exact claim reference | blocks promotion |

Unknown claim relations never become assertions: they are recorded as
`unknown_relation` diagnostics. Promotion of a `contradicts`-blocked
pair is refused; `review` output (`novel` drafts, candidates) is not
graph evidence.

## Temporal qualifiers

Assertions may carry valid-time `effective`/`authority` qualifiers
(asserted text, not verified history). Superseded and future-dated
entries filter out of `current`-policy views without deletion.
History is never rewritten: a correction is a new assertion, and
`corrects` marks it as retrospective (the old claim was never true)
as opposed to `supersedes` (a real-world change at the effective
date). Every timeline entry carries both axes: `effective`
(valid time) and `recorded` (transaction time = snapshot commit).
Open: retention/erasure.

## Retraction arithmetic

A traversable asserted edge (`depends_on`, `implements`) is live
under the `current` policy iff `supports - retractions >= 1`, where
supports count asserted claims of that triple and retractions count
asserted `retracts` claims naming it. One support survives one
retraction; the last retraction removes the edge from traversal
while both stay in history. `retracts` targets asserted
relationships only (`RETRACTABLE`); retracting an observation or a
bare/named (proposed) endpoint is an `invalid_retract` diagnostic
with no effect. Named `retracts` stay proposals and never apply.

## Validation rules

| ID | Rule | Enforced by |
| --- | --- | --- |
| V-01 | Snapshot manifest equals committed graph rows or reads fail | `store.view` integrity check, reference tests |
| V-02 | Unknown relations are diagnostics, never assertions | extractor allowlist, reference tests |
| V-03 | Exact matches claim `(start, key)` before bare matching runs | ordered passes + `seen` registry, refresh tests |
| V-04 | Unqualified bare matches are `mention_candidate` diagnostics | [qualification gate](evidence-qualification.md), precision tests |
| V-05 | Candidates never traverse, merge, or become edges | compiler reads assertions only, precision tests |
| V-06 | Ambiguity is preserved (`proposed_ambiguous`, distinct candidates) | extractor + review tests |
| V-07 | Disputed pairs cannot be promoted over | review/promote tests |
| V-08 | `current` policy excludes superseded entities, queried node included | compiler guard removal test, agent suite v2 |
| V-09 | `retracts` without a retractable target (or with `retract` on another relation) is an `invalid_retract` diagnostic | temporal tests |
| V-10 | Traversable edges apply supports-minus-retractions; last retraction removes the edge, survivors keep it | temporal tests |
| V-11 | `corrects` blocks like `supersedes` under `current` but is reported distinctly (`corrected_by`, not `superseded_by`) | temporal tests |
| V-12 | History replays per snapshot in commit order and skips symbols unknown to old snapshots, never backfilling | temporal tests |

Open: multi-principal scope validation, erasure validation.
See [policy contract](policy-contract.md) §3/§5.
