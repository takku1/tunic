# External coverage: graph tools picked apart for Tunic-shaped work

Date: 2026-11-03. Method: sparse read-only checkout of
`getzep/graphiti` (core nodes/edges/search only, temp dir outside the
repo — no dependency adopted). Mem0/GraphRAG rows below are
**reported** from prior research notes, not re-inspected here.

Rule applied throughout: take the capability, not the framework.
Anything requiring an LLM at ingest/retrieval time, a graph database
server, or paid APIs is recorded as rejected-by-design, not as a gap.

## What graphiti does (inspected)

- Bi-temporal edges: `invalid_at` (fact stopped being true) plus
  `expired_at` (record invalidated). Contradictory new episodes
  invalidate old edges instead of deleting them.
- Three node layers: episodic (source events), entity (semantic),
  community (label-propagation clusters with LLM summaries).
- Entity resolution: `name_embedding` per entity, similarity search
  plus dedup at ingest; per-entity `summary` of surrounding edges.
- Retrieval recipes per channel (edge/node/episode/community):
  BM25 + cosine, reranked by RRF, MMR, or cross-encoder; BFS is a
  search method alongside lexical/semantic.
- `group_id` namespaces every record: one deployment holds many
  projects.

## Mapping to Tunic

| External capability | Tunic status | Verdict |
|---|---|---|
| Bi-temporal invalidation (`invalid_at`/`expired_at`) | Bitemporal `effective`/`recorded`, `corrects`/`supersedes`/`retracts`, support-minus-retraction arithmetic (`decisions` D-32, `memory-lifecycle.md`) | Covered (different encoding, same obligations) |
| Episodic layer (source events retained) | Sources, snapshots, `::document` entities, TextNotes passages (R2) | Covered |
| Contradiction invalidates old edge, history kept | `contradicts` + supersession marking, dispute surfacing, `current` policy excludes superseded | Covered |
| Hybrid BM25 + cosine with RRF | Staged exact/name/lexical/semantic with weighted RRF fusion, budgets, abstention | Covered |
| BFS as retrieval expansion | Bounded BFS lives in traversal (`compile_callees` reachable, container expansion), not as a ranked retrieval stage | Partial (see delta 1) |
| Entity resolution via name embeddings + dedup | Exact keys; bare names propose with corroboration; no embedding-based identity | **Open** (see delta 2) |
| Per-entity / community summaries | Additive support summaries only; no subgraph or community summaries, no summarization of prose | **Open** (see delta 3) |
| MMR diversity reranking | RRF only; no diversity control | **Open, model-free** (see delta 4) |
| Cross-encoder reranking | Requires a model at retrieval time | Rejected by design (model-free retrieval path) |
| `group_id` multi-project namespacing | One workspace root per database | Deferred (platform track, same as multi-tenancy) |
| LLM episode extraction (entities/relations from text) | No NLP relation extraction by design (D-13); mentions/claims are typed and gated | Rejected by design |
| Mem0 ADD/UPDATE/DELETE memory ops via LLM | Deterministic `reconcile_proposal` (ADD/DUPLICATE/CONFLICT/INVALID) + governed promote/correct | Covered without the LLM |
| GraphRAG community summaries via map-reduce | Same as community-summaries row | Open (see delta 3) |

## Tracker deltas (first wave shipped 2026-11-03 as tested v1s)

1. **BFS-expansion retrieval stage** (SHIPPED v1 2026-11-03: `Memory._graph_neighbors`, budget key `"graph"`, default 0 = off; `tests/test_expansion.py`, 4 tests): expose the existing bounded
   traversal as a fused retrieval stage with its own budget and
   receipt, scored against the current RRF fusion on the frozen
   semantic suite. Owner: `evidence-selection.md`.
2. **Embedding-assisted entity resolution** (SHIPPED v1 2026-11-03: `tunic/resolve.py`, `Memory.propose_same_as`, `same_as` in CLAIM_RELATIONS, non-traversable; `tests/test_same_as.py`, 8 tests; no auto-merge, no new model): use the pinned local
   embedding backend (already a dependency for dense retrieval) to
   propose `same_as` candidates between inventoried/proposed names,
   committed only through the existing reviewer promote path. No new
   model, no auto-merge. Owner: new spec or `memory-lifecycle.md`.
3. **Subgraph/community summaries** (SHIPPED v1 2026-11-03: `tunic/community.py`, deterministic label propagation, structured additive records excluded from the answer path; `tests/test_community.py`, 8 tests): deterministic member summaries
   (exact source links, additive like `consolidated_supports`, never
   in the answer path) over file-cluster or triage-cluster units the
   scheduler already computes. Owner: `memory-lifecycle.md`.
4. **MMR diversity pass** (SHIPPED v1 2026-11-03: `retrieval.mmr_order` (Carbonell & Goldstein 1998), answer `mmr_lambda` in [0, 1] default 1.0 = identity with receipt; `tests/test_mmr.py`, 7 tests; default-on acceptance PROVISIONAL): model-free maximal-marginal-relevance
   reorder over the fused candidate set with a pinned lambda,
   acceptance-gated like selection. Owner: `evidence-selection.md`.

None of these needs Neo4j, an LLM call, or a new service. The temp
checkout lives outside the repo and is not a dependency.

## Concept gaps from the graphiti reference review

Source: full read of `docs/references/graphiti` (core: nodes, edges,
dedup/helpers, temporal utils, community ops, search recipes/utils,
MCP wrapper; inspected 2026-11-03). Concept-only: no code adopted,
no dependency added. Each gap states the observed pattern, what the
latest academic work says is optimal, and the Tunic-shaped execution.
Framing throughout: the system is optimized for AI consumers --
deterministic, receipted, structured -- not for human reading.

### G1. Entropy-gated MinHash LSH blocking for resolution at scale

Observed: graphiti blocks with MinHash signatures over 3-gram
shingles (32 permutations, band size 4), fuzzy Jaccard at 0.9, and --
the transferable idea -- a Shannon-entropy specificity gate: short or
repetitive names defer to the LLM instead of trusting fuzzy
similarity. Identity-property keys resolve before fuzzy matching.
Tunic's `same_as` proposer has token blocks with a 200-member cap but
no specificity guard and no LSH; at 1,809 entities it is fine, at
100k it is quadratic.
SOTA: MinHash LSH gives probabilistic Jaccard guarantees and remains
the production default (ONS reports 100% pairs recall at ~8x fewer
candidates); LSBlock (2025) shows the hybrid wins -- minhash lexical
blocking plus dense embeddings with cosine refinement (HNSW-indexed)
beats DeepBlocker and Sparkly on recall and precision. Embedding-only
blocking has the best recall but needs a vectorizer plus ANN index.
Tunic execution: stdlib MinHash (fixed seeds, pinned bands) over
bare-name shingles as the blocking layer, kept entropy gate (names
below the floor need a corroborating structural signal, exactly as
graphiti defers to the stronger judge), dense cosine as the optional
refinement already plumbed through `vectors=`. No new dependency;
blocking stays data, scoring stays deterministic.

### G2. Date-ordered conflict surfacing (Allen intervals, not explicit supersession)

Observed: graphiti derives invalidation from dates -- a later-valid
fact sets `invalid_at` on the earlier one, with explicit
interval-overlap checks (`resolve_edge_contradictions`); new edges
carry the later `valid_at` forward as the old edge's end. Tunic
requires the reviewer to assert `supersedes`/`contradicts` by hand;
same-pair claims with ordered `effective` dates sit side by side
until someone notices.
SOTA: Allen's interval algebra (1983, 13 relations) is the complete
formalism for interval overlap reasoning; Snodgrass's bitemporal
model (BCDM 1994, ISO SQL:2011, XTDB practice) separates valid time
(when the fact is true) from transaction time (when it was recorded)
and closes overlapping effective periods to prevent temporal
conflicts. Valid-time indeterminacy (not knowing exactly when a fact
holds) is modeled, never rounded away.
Tunic execution: a read-only detector over same-pair claims with
ordered `effective` dates emits `contradicts` *proposals* (never
auto-supersession -- the reviewer still promotes, per the governed
path). Undated claims never participate. This is MMR-style
conservatism applied to time: propose the conflict the dates prove,
decide nothing.

### G3. Temporal bound normalization at claim-parse time

Observed: graphiti normalizes future-tense bounds -- a planned or
promised fact's `valid_at` is the statement time, not the future
occurrence; a soft deadline ("will complete by Friday") is
explicitly NOT an `invalid_at`. Deadline vs end-of-state regexes
decide. Tunic's `effective` is raw asserted text: "will migrate by
Friday" and "migrated on Friday" get the same treatment.
SOTA: same Snodgrass valid-time semantics -- valid time models
reality, so a statement about the future is valid at statement time
with an indeterminate end; conflating deadlines with invalidity
corrupts overlap reasoning (G2 depends on getting this right).
Tunic execution: normalize at claim parse (extract.py, with receipt):
future-tense markers pin `effective` to the statement/note date and
record the deadline inside the fact text/attributes; explicit
end-of-state markers ("until", "no longer", "expired") are the only
path to an end bound. Deterministic regexes, reviewer-visible, no
model call.

### G4. Relation type signatures (ontology enforcement, SHACL-shaped)

Observed: graphiti enforces edge-type signatures over endpoint
labels -- strict mode drops violating edges, lenient mode renames to
a default. Tunic's subsystem matrix already records the gap ("lazy
re-exports and attribute-mediated relations have no relationship
type; entity/ontology semantics unresolved"); graphiti confirms the
industry pattern.
SOTA: SHACL (W3C 2017) and ShEx are the standard shape languages;
recent KG-quality literature converges on constraint-based
validation (SHACL/ShEx/SPIN) over pure OWL reasoning, with CI-gated
contracts (shapes checked before bad data commits). Tunic already
cites SHACL as a requirement source in standards-mapping.
Tunic execution: a signature table over (relation, caller-kind,
callee-kind) checked at claim parse -- e.g. `depends_on` needs code
endpoints, `same_as` needs same-kind endpoints -- with graphiti's two
modes: strict drops with an `invalid_claim` diagnostic, lenient
renames to a default relation with a receipt. The table is data in
`graph-schema.md`, not code branches.

### G5. Weighted propagation and distance-ranked expansion

Observed: graphiti weights label propagation by edge count and ranks
retrieval by BFS distance to the center node
(`node_distance_reranker`). Tunic's community detection is
unweighted; its expansion stage is 1-hop, unranked.
SOTA: personalized PageRank (FORA, Wang et al., KDD 2017 / TODS)
is the theoretically grounded answer for query-dependent node
ranking, with approximation guarantees -- but the guarantees matter
at web scale. At repository scale (thousands of nodes) exact power
iteration is trivially affordable: no approximation, no index, fully
reproducible. That is the AI-optimal point: exact math beats
approximate math when the graph fits in memory.
Tunic execution: weight propagation by in-unit edge multiplicity
(cheap, matches graphiti); extend expansion to bounded multi-hop
with exact PPR scores and a distance-decay receipt, acceptance-gated
against the current 1-hop stage on the frozen suite. Deterministic,
stdlib, no index to maintain.

Deliberately not gaps (rejected by design, again): LLM entity /
relation extraction, LLM compare/dedupe, cross-encoder reranking,
embedding-name storage that silently merges, server graph databases,
and group_id multitenancy (still platform track).

## Second wave G1-G5 (shipped 2026-11-03 as tested v1s, no framework adopted)

- **G1. Entropy-gated MinHash LSH blocking** (`tunic/resolve.py`): deterministic MinHash (32 perms, fixed seeds, sha256 rows) over normalized 3-gram shingles, 8x4 banding; shingle-Jaccard signal (>= 0.3, weight 2); specificity gate (normalized length >= 3 and Shannon entropy >= 1.0 bits) that defers non-exact pairs to corroboration. `tests/test_same_as.py` (12 tests, incl. pinned LSH-collision fixture).
- **G2. Date-ordered conflict surfacing** (`tunic/temporal.py` + `Memory.propose_date_conflicts`): same-pair asserted claims with ordered ISO `effective` dates propose `contradicts` with interval signals; undated, suppressed, and already-decided pairs excluded; never auto-supersedes. `tests/test_temporal_bounds.py`.
- **G3. Temporal bound normalization** (`tunic/temporal.py::normalize_bounds`): pure deterministic function -- future-tense statements pin `valid_at` to the explicit reference date, soft deadlines never become `invalid_at`, only end-of-state markers license an end bound. Ships as an explicit-reference helper plus tests; auto-wiring at claim parse waits on statement-time tracking (recorded OPEN, not silently defaulted).
- **G4. Relation type signatures** (`extract.RELATION_SIGNATURES`, `check_relation_signature`, enforced at claim parse with `relation_signature` diagnostics): `depends_on` spans document->code provenance and code->code, rejects only the backwards code->prose direction; `implements` code->code; `same_as` same-kind; meta relations unconstrained. `tests/test_signatures.py` (5 tests).
- **G5. Weighted propagation + exact PPR expansion** (`tunic/community.py` edge-count weighting; `retrieval.ppr_scores` exact power iteration, deterministic, sums to 1; `_graph_neighbors` depth-2 with PPR ranking, receipt records `depth` and `ranking`). `tests/test_ppr.py`, weighted community tests, 2-hop expansion test.