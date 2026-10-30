# GraphGraph reference audit

Date: 2026-10-30. Reference checkout: `docs/graphgraph`. Commit: `6f6b0132a48ade34920101e1f7683943ff2b80e8`. Initial Git status was clean.

**Redesign mandate:** the user explicitly permits scrapping the old design. This repository is prior art and a source of lessons, not a compatibility target. No obligation to retain its language, formats, CLI/MCP surface, schema, algorithms, or architecture is inferred.

## Review scope

Inventoried all 364 discovered Python files through AST parsing; no parse errors occurred. [Machine-readable inventory](graphgraph-source-inventory.json) records file digests, module summaries, top-level definitions, imports, and reference commit. This is structural inventory, not proof that every implementation path has been understood or tested.

Read the README, packaging configuration, system architecture narrative, roadmap excerpts, and selected implementation sections covering the graph IR, compiler driver, cache, binary storage, temporal episodes, embeddings, BM25, token planning, rule inference, and influence-field research. No tests, benchmarks, installers, scans, or model calls were executed. Historical measurements in repository docs remain reported results.

During the later Tunic naming/IR documentation pass, `docs/graphgraph` was no longer present. The agent did not remove it; the cause was not established. Detailed behavioral review remains incomplete, and the inventory is not evidence of completion. This audit and inventory survive outside the former clone. Further source review requires restoring the recorded revision.

## What actually exists

| Area | Source evidence at the reference commit | Implication |
| --- | --- | --- |
| Graph IR | `src/graphgraph/graph/core.py`: frozen Node/Edge records, mutable graph collections with revision tracking, typed edges and provenance fields. | Existing domain model is richer than the earlier historical notes suggested; inspect semantics before choosing what to replace. |
| Context compiler | `services/compiler_driver.py` exposes DriverRequest/CompilerDriver; `platform/compiler.py` defines requests, passes, outcomes, and compilation receipts. | A context compiler is already an organizing idea. The redesign needs a better contract and measured benefit, not merely a new compiler name. |
| Storage | `storage/sectioned.py` implements GGB4 sections, offsets, record counts, and CRC32 checks. | Binary graph storage already exists. It is separate from binary model input and transferable KV state. |
| Packet caching | `runtime/cache.py`: TopologicalKVCache is explicitly an LRU packet cache; get returns a packet string, set stores one in JSON. | Rename this concept in the redesign; it is not transformer KV caching. Preserve its invalidation lesson without preserving the misleading name. |
| Query response coordination | Compiler driver coordinates freshness, graph loading, response caching, validation, and control receipts. | Strong candidate for decomposition; selection, rendering, application state, and model execution deserve explicit boundaries. |
| Lexical ranking | `retrieval/relevance.py` implements candidate-set BM25 with heading term multiplicity. | Existing BM25 is a concrete baseline; candidate-relative IDF must not be mistaken for corpus-global relevance. |
| Graph diffusion | `graph/core.py` includes size/seed-adaptive local-PPR work limits; research influence fields call personalized PageRank. | PPR is existing machinery. Its calibrated work curves are heuristics, not universal mathematical bounds or guarantees of retrieval quality. |
| Token planning | `planning/token_cost.py` uses packet-specific fitted cost surfaces. | Distinguish cheap planning estimates from final model-tokenizer counts. Historical fit results need reproduction on new syntax and workloads. |
| Semantic retrieval | `platform/embeddings.py` distinguishes hashed bag-of-words from optional real embedding backends and rejects backend mismatch. | Preserve representation identity checks. A hashed lexical vector is not a paraphrase-capable semantic model. |
| Temporal memory | `platform/temporal.py` defines append-only episodes, timestamps, supersession, and graph projection. | Existing timestamps/supersession do not establish a complete bitemporal database contract. |
| Inference | `platform/inference.py` applies bounded two-hop Horn-style rules and marks inferred edges with provenance. | Here inference means graph-rule derivation, not neural model serving. |
| Attention research | `research/attention_field.py` defines PPR influence fields and hierarchy coverage planners/oracles. | This is graph-side retrieval/compression research, not transformer attention-mask injection. |
| Resident execution | Architecture docs describe a long-lived MCP path and process graph reuse. | A resident service is not a wholly new contribution; benchmark process startup separately from resident retrieval. |

## Lessons worth carrying forward

These are candidate principles, not requirements to port code:

- Source grounding and evidence references should survive compact rendering.
- Correctness, completeness, and freshness need separate meanings; a small packet is not automatically a complete answer.
- Missing matches are not proof of absence. Historical roadmap entries describe abstention mistakes and misleading classification gates.
- Exact lookup, conceptual retrieval, and multi-hop synthesis need separate evaluation strata.
- Real embedding indexes need backend/version compatibility and freshness checks.
- A change outside the previously selected evidence can alter the answer. The packet cache explicitly warns that positive dependency paths alone cannot prove validity.
- Process startup, graph load, retrieval, packet generation, and model prefill are different latency components.
- Published or internal headline numbers must retain corpus, source-mode, tokenizer, and evaluation qualifications.

## What is freely replaceable

The compact packet syntax and its ten-format catalog, CLI/MCP envelope, custom GGB4 store, mutable Python IR, class-specific routing heuristics, fitted token surfaces, and package layout are all replaceable. Retaining any of them requires an advantage demonstrated against a simpler alternative.

Avoid porting the old layering wholesale. Reconstruct requirements from representative tasks and known failure cases. Extract small reference fixtures or behavioral descriptions where useful; copying an entire old test suite would silently impose compatibility the user did not request.

## Proposed redesign emphasis

1. Define immutable evidence/version identities and explicit source spans rather than relying on compact text conventions for core metadata.
2. Establish simple retrieval controls before adopting or reimplementing the old ranking stack.
3. Define a compilation artifact with exact model input identity and evidence mapping.
4. Distinguish source cache, retrieval cache, compiled-input cache, and transformer state cache in both types and telemetry.
5. Add a model-runtime adapter only after establishing deployment control and compatible state semantics.

## Remaining reference review

Before deleting the clone, trace the complete query and update paths, examine packet encoding/validation and abstention logic, inspect operation logging and storage recovery, and review evaluation artifacts for reusable failure cases. Review research mechanisms against their actual implementation and tests. Record the keep/discard rationale and extract any fixtures needed by the redesign outside the clone. No preservation decision is implied merely by a component being implemented.
