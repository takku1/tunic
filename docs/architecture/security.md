# Evidence and runtime security boundaries

Status: proposed requirements for model-backed or shared deployments. The current [W1 reference](w1-reference.md) is local and single-principal, retains source history, has no model/tools/cache bridge, and does not implement tenant isolation, revocation, erasure, hostile-input resource isolation, or these runtime defenses. This document owns security boundaries; [research](../research/runtime-memory.md#security-assessment) records evidence and limits.

## Threat model and limits

Consider malicious repository text/graph writes, forged origins, structural control tokens, poisoned derived measurements, unauthorized traversal, forged tensors/cache handles, and cross-session observations of speculative work. Keep source writers, extraction/derivation producers, policy administrators, model providers, runtime/cache operators and tool executors as distinct authorities. Source provenance identifies origin and lineage, not truth, authorization, or harmlessness.

Static RAG is not secure merely because content was “sanitized.” Runtime retrieval adds attachment races and resource exposure, but an instruction embedded in a docstring can already affect a model reading an ordinary prompt. “Kernel exploitation” is an analogy, not proof of native execution privilege. A model reading hostile evidence may follow it even with correct framing and unchanged system-prompt state.

## Enforceable boundaries

| Boundary | Required behavior | What it does not prove |
| --- | --- | --- |
| Retrieval and graph writes | Bind principal/capabilities and policy version outside the model. Authorize before candidate lookup, traversal, aggregation, prefetch and source fetch. Only authorized writers can change graph/schema/policy state. | A high trust/relevance score does not authorize reads or writes. |
| Derived evidence | Preserve all premise visibility obligations or apply an explicitly authorized declassification rule. Recheck artifacts, summaries, indexes, embeddings and caches when policy changes. | Per-node ACLs alone do not prevent hidden-edge, aggregate, ranking or summary leakage. |
| Framing | A trusted renderer owns roles, template, tool metadata and structural token IDs. Treat evidence as data, never as template code, tool definitions, or caller-selected roles. Test tokenizer-specific literalization/rejection of reserved tokens. | Text quoting, control-token handling and instruction hierarchy are not universal semantic-injection prevention. |
| Execution tools | Separate model proposals from capability-checked execution. Validate arguments/destinations, enforce filesystem/network scopes, and independently govern side effects. Credentials stay outside graph/model context. | Read access to a document cannot grant shell, network, policy-write or secret-read authority. |
| Runtime state import | Accept only authenticated authorized producers and compatible, bounded immutable payloads. Validate size/shape/dtype/layer/layout, execution/prefix identity, integrity and completion before use. Do not deserialize executable objects or accept arbitrary client tensor/device handles. | Shape checks, hashes or finite values do not establish benign latent semantics. |
| Speculative work and caches | Controller-owned isolation domain, policy epoch, snapshot and execution keys; authorize before any speculative fetch and again before use. Scope logs, indexes and caches, not just GPU allocations. | A user-provided cache salt or session label alone is not isolation. |

RBAC is one possible policy implementation; authorization may also depend on source labels, project, attributes, purpose, and time. Do not require duplicated mutable ACLs on every edge if inherited policy can enforce the same obligations. Hidden premises must remain hidden through derived records and explanations.

For control-token handling, keep original source bytes/evidence intact and create a separately mapped rendered representation. Prefer explicit literalization over destructive source rewriting. Validate roles, dictionary keys, dynamically constructed delimiters, model templates and any special-token allowlist. Raw token-ID paths require equally strict ownership of which positions may contain structural IDs. Authorized tool results remain lower-trust content even when their transport is authenticated.

Attention masks are not a general way to stop evidence overriding instructions: a decoder permitted to read malicious content can act on it without that content modifying the stored system tokens. Provenance-weighted attention may be evaluated as a heuristic, but cannot replace access checks, framing, or tool execution policy. There is no adopted latent “trust weight” defense.

## Revocation, speculation and residual leakage

Pin policy decisions to request and attachment receipts. If authorization changes between prepare and attach, reject or reauthorize; stale permission is not a cache hit. Denied work must not enter model input. Revocation during a live segment needs an explicit cancel/restart policy; do not promise removal of already observed information. Cross-session sharing is off by default for sensitive derived/model state unless a tested policy defines a permitted trust domain.

Apply quotas and demand priority so prefetch cannot exhaust the service. Release unused references, evict according to policy and prevent reuse after cancellation. Immediate reliable physical scrubbing across GPU pools, allocator copies, host tiers, logs, backups and providers is not assumed; retention/erasure guarantees require a tested backend contract. Likewise, disabling cross-principal prefix reuse narrows a timing channel without proving freedom from shared-resource or hardware side channels.

Use scoped audit records for authorization, producer, source/snapshot identity, attach time and execution mode. Avoid recording protected content or exposing cache-hit state through unauthorized diagnostics. Cryptographic identity protects integrity/authenticity only under managed keys and trusted producers; content addressing alone authenticates no producer. Keep learned usage feedback and graph mutation authority separate so repeated exposure cannot automatically promote attacker-controlled material.

## Required tests before a shared or model-backed runtime

These tests are planned, not executed by the current W1 implementation:

- Benign and malicious evidence in upfront, reactive and prefetched arms; include natural-language instructions without control tokens, spoofed roles, copied provenance and poisoned high-degree nodes. Report attack success and clean-task/over-refusal regressions separately.
- Template/tokenizer checks showing that evidence cannot create structural roles, tool metadata or unapproved control IDs; preserve exact source-to-render mappings. Test multilingual/encoded/split-marker variants against the actual backend, without treating passing cases as a semantic guarantee.
- Denied bridge nodes, summaries and aggregates; revoke between retrieval/preparation/attachment; race retries/cancellation and test mixed-policy premises. Authorized callers must not observe protected results, explanations or diagnostic details.
- Forged/incompatible cache references, wrong prefix/model/layout, corrupted/incomplete transfers, oversized inputs, replay across isolation domains, and tensor-source provenance checks. Keep latent adversarial-behavior tests distinct from payload validation.
- Shared-cache timing probes and demand-load tests with prefetch on/off. Verify isolation keys are controller-owned, blocked speculation does no protected fetch, and unused work remains within resource budgets. Declare which leakage channels remain out of scope.
- Tool-policy tests where a model requests an otherwise forbidden side effect after reading poisoned evidence. The executor must reject independently of whether the model appears persuaded.

No model-only defense is accepted as an authorization boundary. Model output correctness and permitted external actions are separate acceptance properties.
