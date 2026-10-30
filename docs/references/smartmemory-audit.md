# SmartMemory source findings for Tunic

Inspected 2026-10-30: [smartmemory/smart-memory](https://git.smartmemory.ai/smartmemory/smart-memory). Main resolved to `ce6f8bf60c0fff8cd794ce42f5c826bdc5079756`, timestamp 2026-10-30. API metadata identifies the repository as a mirror of `https://github.com/smartmemory/smart-memory.git`. Files were read from main during this session; this was not an atomic checkout, complete audit, or executed benchmark.

The web fetch service could not open the forge, but direct HTTPS reads of the public API and raw files succeeded. Git clone/archive attempts did not produce a usable checkout. No package, hook, daemon, or test was run. Conclusions below distinguish inspected code from README descriptions.

## What this repository contains

The [package manifest](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/pyproject.toml) identifies `smartmemory` version 1.5.31, with an exact `smartmemory-core` dependency and a separately pinned MCP package. The visible app owns CLI, lifecycle/capture, recall formatting, storage adapters, diagnostics, and mirror persistence. It is not the complete implementation of the separately packaged core's graph indexing/reasoning algorithms.

This resolves the earlier missing public app repository identity for smartmemory.ai. It does not establish that all core source is available here, and it is distinct from MauriceIsrael/SmartMemory.

## Highest-value implementation precedents

### 1. Frozen artifacts and layered identity

[`mirror_integrity.py`](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/smartmemory_app/mirror_integrity.py) validates normalized protocol data with JSON Schema, canonicalizes it using RFC 8785, and uses domain-separated SHA-256 digests for source payloads, manifests, groups, and operations. It checks frozen payload references and rejects mismatches. The schema distinguishes typed references, dependency hashes, temporal fields, snapshot state, and operation errors.

**Tunic implication:** specify canonical bytes and separate source-content, derivation, selection-receipt, and operation identity. Do not build a hash from an unspecified JSON dump. Keep dependency bindings distinct from intrinsic content identity, especially around cycles. This is synchronization-artifact precedent, not a ready-made context IR or proof of semantic correctness.

### 2. Durable uncertainty and replay

[`mirror_state.py`](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/smartmemory_app/mirror_state.py) uses locked SQLite state and a FULL-sync transaction for its receipt document. It stages frozen snapshots, verifies source/pair continuity, prevents overlapping unresolved operations on the same keys, and records a `sent_uncertain` state. [`mirror_outcomes.py`](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/smartmemory_app/mirror_outcomes.py) reconciles outcomes and baselines; an unobserved receipt query does not itself settle an operation.

**Tunic implication:** a future ingestion/federation protocol should distinguish queued, sent, uncertain, committed, and verified outcomes. A timeout is not a failed mutation; a receipt lookup is not permission to replay an incompatible operation. Start with local atomic artifact publication before adopting distributed mirror machinery.

### 3. Rank source groups instead of extracted rows

[`search_collapse.py`](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/smartmemory_app/search_collapse.py) groups extracted facts under their source memory, orders groups by the best-ranked member, caps displayed children, fetches parents through a scoped read, and checks temporal eligibility before grouping and after parent promotion. It retains an explicit unresolved-parent fallback.

**Tunic implication:** compare source-group selection against flat claim ranking. A prolific extractor should not monopolize the evidence budget. Preserve the matched child and its score/provenance when surfacing a parent. For Tunic's validated IR, unresolved parentage remains unsupported until independently grounded; displaying a standalone fact does not automatically make it valid evidence.

### 4. Keep evidence boundaries visible in recall

[`recall_format.py`](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/smartmemory_app/recall_format.py) provides a common local/remote formatting path, deduplicates before applying the item cap, carries memory IDs/date/staleness markers, and budgets whole entries or query-focused sentence excerpts rather than arbitrary string cuts. Lessons are treated specially.

**Tunic implication:** keep unit-to-rendered-span mapping, explicit omission receipts, and atomic required premises. Do not copy all defaults: this path approximates its token budget as four characters per token, may omit missing confidence markers, and synthesizes a content-derived label when IDs are missing. Tunic needs exact final tokenizer accounting, explicit uncertainty, and authoritative source/version identity.

### 5. Bounded incremental capture and background enrichment

[`capture_slices.py`](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/smartmemory_app/capture_slices.py) streams transcript tails from byte offsets into bounded, line-aligned slices. [`work_graph.py`](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/smartmemory_app/work_graph.py) adapts a core-owned durable work graph and reports prerequisite failures. The README describes immediate lightweight extraction with later enrichment and pending/running/dead work status.

**Tunic implication:** ingestion acknowledgment and enrichment completeness need different receipts. Record extraction version/status on evidence so background changes invalidate the appropriate selection artifacts. Byte-offset capture also needs source rotation/truncation identity checks; the inspected slicing module alone does not establish the complete end-to-end retry/commit guarantee.

## Useful documented operational lessons

The [README](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/README.md) describes isolated onboarding stores, real-token receipts in its tour, freshness/ownership diagnostics, timeout-as-not-checked outcomes, bounded capture workers, and fresh-install CI. These are references for evaluation and operations, not test results reproduced here. A tour's real-token receipt does not imply every runtime budget uses exact tokens.

## Adoption boundary

The inspected [license](https://git.smartmemory.ai/smartmemory/smart-memory/src/commit/ce6f8bf60c0fff8cd794ce42f5c826bdc5079756/LICENSE) is a custom SmartMemory Lite Runtime License, not a permissive open-source license. It includes redistribution and competing-service restrictions. Public readability is not a basis for treating the code as freely reusable. This pass copied no implementation into Tunic and makes no legal determination about a proposed use.

The architecture ideas are worth independently evaluating; selecting this runtime as a dependency would require resolving its applicable terms and verifying the core package's real behavior. The inspected files do not demonstrate transformer KV transfer, custom attention, latent communication, or Tunic's query-selection invalidation contract.

## Concrete additions to Tunic's evaluation

- Add a source with many extracted facts competing against a second decisive source; measure diversity, recall, and token cost.
- Mutate or remove a parent while its extracted child remains indexed; validate evidence and access behavior.
- Crash after remote send but before acknowledgment; verify outcome reconciliation rather than blind duplicate replay.
- Change a source while background enrichment is pending; compare incremental compilation with full recompilation.
- Hash equivalent protocol objects with reordered fields/sets, and reject unsafe numeric representations under the selected canonicalization contract.
- Compare whole-unit budgeting with sentence excerpts while checking entailment, qualifications, and exact final tokens.

Priority: artifact identity/validation and source-group retrieval first; distributed mirroring and automatic memory evolution later, only if a representative workload requires them.
