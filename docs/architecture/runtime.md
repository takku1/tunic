# Tunic lowering and runtime contract

Status: proposed contract. No model/runtime/backend has been selected or integrated. This document owns model-specific input and execution-state semantics; [context IR](context-ir.md) owns model-independent evidence semantics.

## Lowering boundary

Inputs are a validated context IR, current authorization/validity receipt, and a pinned target profile. The profile identifies model/tokenizer/template/renderer versions, context capacity, requested output allowance, attention/position policy, and supported input modalities/capabilities.

Lowering renders complete inputs deterministically and tokenizes the complete sequence. Independent excerpt tokenizations cannot be assumed concatenation-equivalent. Preserve mappings from rendered/token spans to IR units and source evidence. Keep semantic source boundaries distinct from runtime KV allocation blocks.

Outputs are a `TokenArtifact` or supported hosted-message artifact plus a `LoweringReceipt`: exact input digest, model contract, actual token costs, unit mapping, outcome, and any execution reference. A model that cannot fit required premises returns `budget_unsatisfied` or requests separately identified recompilation. No silent premise dropping. Unsupported representations return `unsupported_lowering`.

The compiler may receive a target-derived planning budget as policy input. The resulting IR is model-independent in representation, but its selected evidence can be budget/policy-specific. The same IR may lower to multiple models only where required evidence fits and capabilities permit. Portability does not guarantee identical answers or budgets.

## Cache and execution state

Ordinary prefill is the correctness baseline. Exact KV reuse requires compatible preceding tokens, weights/adapters, positions, masks, multimodal inputs, numerical representation, and runtime layout. Source/content hashes alone are insufficient.

A proposed `CacheReference` identifies execution fingerprint, prefix-dependent identity, token extent, layout metadata, runtime locator, authorization scope, and lifecycle. It is a disposable handle, not a portable device pointer or authoritative evidence.

Before importing state, validate dtype, axes, layers/heads, sharding, block size, quantization metadata, device/ownership, and backend compatibility. Verified conversions may be supported; otherwise recompute. Unknown, stale, missing, corrupt, or incompatible state is a miss/error with safe ordinary-prefill fallback where possible. A source/policy revocation must not be bypassed through cached input or tensors.

Runtime-owned allocation, leases/references, synchronization, eviction, cancellation, and incomplete-transfer handling must be defined by a real backend adapter. Do not mark transferred state usable before completion. Losing cached computation cannot lose durable knowledge or silently change evidence selection.

## Protocol scope

Start with existing supported message/token/cache interfaces. A future envelope can identify version, payload kind, size/checksum, request/session, scope, execution fingerprint, prefix identity, token range, layout, and completion/error state. Negotiate bounds, backpressure, timeouts, and ownership. Binary transport and external KV transfer are conditional optimizations requiring measured benefit.

A hosted provider without a KV interface receives supported messages and retains IR/lowering receipts locally. Unobservable provider cache statistics are unavailable, not assumed hits or misses. Fusion, lossy KV compression, learned latent bridges, custom masks, and positions are distinct experiments, not transparent exact-cache optimizations.

## Acceptance

Planned checks compare compatible cache reuse with full prefill under declared numerical tolerances; exercise changed prefix/model/adapters/template/masks, partial blocks, eviction/restart, corrupt transfer, and revocation; and lower one IR to two pinned profiles with independent identities. No Tunic runtime checks have run. The [workload protocol](../evaluation/workloads.md) controls benchmark arms; the [strategy](../evaluation/strategy.md) controls general measurement rules.
