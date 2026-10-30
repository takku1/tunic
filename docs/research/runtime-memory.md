# Runtime-mediated graph memory: critical assessment

Research date: 2026-10-30. This is a targeted primary-source review, not a reproduced result or exhaustive survey. Proposed architecture lives in [runtime memory](../architecture/runtime-memory.md); security requirements live in [security](../architecture/security.md). No Tunic model backend, runtime bridge, prefetcher, or GPU experiment is implemented.

## Recommendation

Retain an optional controller for retrieval at supported generation boundaries, with budgeted evidence prefetch. Keep exact prefix reuse orthogonal to retrieval timing. Treat non-prefix KV fusion and learned graph attention as separate conditional experiments. They do not become dependencies of the W1 graph/compiler reference.

This is an engineering hypothesis about useful integration, not a new mathematical mechanism. A controller can avoid a visible user tool interaction, but it still incurs retrieval, rendering and model-execution work. A token continuation usually means another controlled generation segment/request; a generic streaming endpoint does not imply mutable live decode state.

## What the mechanisms establish

| Mechanism | Evidence and limitation | Tunic disposition |
| --- | --- | --- |
| Active retrieval | [FLARE](https://arxiv.org/html/2305.06983) drafts upcoming sentences, retrieves on a trigger, and regenerates. It supports interleaving without new training, not uninterrupted mutation of an executing transformer block. Low token likelihood is not calibrated factual uncertainty. | Candidate baseline with explicit segment boundaries and regeneration costs. |
| Learned retrieval decisions | [Self-RAG](https://arxiv.org/abs/2310.11511) trains retrieval/reflection tokens; [Toolformer](https://arxiv.org/abs/2302.04761) learns API usage. These are not automatically available capabilities of an arbitrary frozen model. | Compare observable controller events first; learned triggers need their own training/held-out evaluation. |
| Exact prefix reuse | [vLLM APC](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/) avoids repeated prefill for matching prefixes, not decode computation. [SGLang](https://arxiv.org/abs/2312.07104) introduces RadixAttention; calling it “vLLM RadixAttention” conflates implementations. | Supported-backend optimization subject to execution compatibility. |
| Distributed KV movement | [vLLM disaggregation](https://docs.vllm.ai/en/latest/features/disagg_prefill/) and [offloading](https://github.com/vllm-project/vllm/blob/main/docs/features/kv_offloading_usage.md) move existing compatible execution state. Transfer capability does not define arbitrary new memory semantics. The disaggregation documentation still labels the feature experimental. | Possible transport backend, not proof of mid-decode graph attachment. |
| Non-prefix chunk reuse | [CacheBlend](https://arxiv.org/html/2405.16444v1) selectively recomputes cached tokens to restore cross-chunk interaction. [Cache-Craft](https://arxiv.org/html/2502.15734v1) estimates contextualization and adapts repair work. These evaluate quality/performance tradeoffs; they do not prove universal equality to full prefill. | Conditional quality intervention, not an exact cache hit. |
| Graph soft prompts | [G-Retriever](https://arxiv.org/html/2402.07630v3) trains a graph encoder/projector and combines its output with graph text. A frozen base LLM does not mean a training-free bridge or elimination of graph serialization. | Defer until text controls and graph-use interventions justify training. |
| Architected external memory | [RETRO](https://arxiv.org/abs/2112.04426) and [Memorizing Transformers](https://arxiv.org/abs/2203.08913) demonstrate retrieval-integrated model designs. [Infini-attention](https://arxiv.org/abs/2404.07143) changes the attention/memory mechanism. | Relevant architectural precedents; not adapters that make arbitrary KV portable. |
| Predictive prefetch | [DualDecoder](https://arxiv.org/html/2607.26475v1), July 2026, predicts access to already computed sparse KV entries and overlaps host-to-device transfer. This is different from predicting which previously unseen project evidence to retrieve. | Evidence that overlap can matter, not validation of Tunic's predictor or savings. |

### The causal-state constraint is larger than RoPE

For a causal transformer, schematically:

\[
K_i^{(l)}=W_K^{(l)}h_i^{(l-1)},\qquad
V_i^{(l)}=W_V^{(l)}h_i^{(l-1)},\qquad
h_i^{(l)}=F_l(h_{\le i}^{(l-1)};M,P).
\]

These dependencies vary by architecture but illustrate the problem: later-layer state generally depends on preceding visible content, not only a position rotation. [RoFormer](https://arxiv.org/abs/2104.09864) supplies the positional construction; CacheBlend supplies an explicit discussion of missing cross-context interactions. Repositioning keys alone cannot reconstruct those interactions. ALiBi, NoPE, reserved coordinates, or a matching hidden dimension do not by themselves remove them. RoPE's formula does not mandate every conceivable execution layout use contiguous position IDs; runtime support and training distribution are separate constraints.

Appending a supported suffix can preserve previous causal state when preceding inputs/positions/masks stay identical. Inserting evidence before already processed positions generally invalidates affected suffix state; recompute or explicitly evaluate an approximate mechanism. Complete chat-template rendering may also change the actual reusable prefix. Earlier emitted output cannot be retroactively made evidence-grounded by adding context later.

[Prefix-tuning](https://arxiv.org/abs/2101.00190) learns continuous task parameters; it is not equivalent to caching source evidence. [MemGPT](https://arxiv.org/abs/2310.08560) manages virtual context through a controller, not arbitrary native KV attachment. [HyperAttention](https://arxiv.org/abs/2310.05869) approximates attention computation; it does not establish a graph-to-model alignment interface. Sparse compute, semantic retrieval, and memory transport solve different problems.

### A current warning about learned graph bridges

The October 22 preprint [On the Token Collapse Phenomenon](https://arxiv.org/html/2610.31572v1) reports that G-Retriever-style graph tokens can become almost input-independent. In its tested settings, a graph-free learned constant is competitive, and swapping graph-derived tokens often has no resolved answer-level effect. The paper also distinguishes recoverable graph information from information actually used by the decoder. Its analysis is bounded to specific benchmarks, recipes, models and runnable variants; it does not rule out all graph bridges.

Tunic should therefore require fixed-weight graph swaps, corrupted/missing-graph interventions, a matched constant-projector control, and text-only controls. Accuracy alone is insufficient evidence that a latent channel conveys the graph. Code is linked by the authors but was not audited or executed in this pass.

## Security assessment

Static RAG already exposes indirect instruction injection: [Greshake et al.](https://arxiv.org/abs/2302.12173) demonstrate the problem without live KV mutation. Runtime attachment adds more decision times, cache scopes and revocation races, rather than creating an entirely new class of model privilege.

Instruction prioritization can be trained ([Instruction Hierarchy](https://arxiv.org/abs/2404.13208)); provenance transformations can reduce attacks in evaluated settings ([Spotlighting](https://arxiv.org/abs/2403.14720)). Neither establishes universal isolation. A June 2026 [camouflaged-injection study](https://arxiv.org/abs/2606.18530) reports model-dependent residual failures on synthetic documents. Treat that as a warning about generalization, not a validated defense ranking for code agents. Rewriting evidence for defense is also a derived, potentially lossy transformation.

Structural control tokens merit real implementation checks: current [Transformers tokenizer documentation](https://huggingface.co/docs/transformers/main_classes/tokenizer#transformers.PreTrainedTokenizerBase.apply_chat_template) exposes `sanitize_control_tokens`, encoding input occurrences as ordinary text while preserving template-authored control tokens. It defaults off in the reviewed documentation and has limits for dynamically assembled tokens and dictionary keys. Pin and test the actual tokenizer/template rather than assuming the option exists in every release. Neutralizing structural tokens still does not neutralize semantic instructions.

[Auditing Prompt Caching](https://proceedings.mlr.press/v267/gu25b.html) establishes timing/privacy concerns from cross-user cache reuse. Current [vLLM cache design](https://docs.vllm.ai/en/latest/design/prefix_caching/) describes request salting. The controller must own its isolation domain; an attacker-selected salt is not an authorization mechanism. This does not address all resource-contention or hardware side channels. Recent [CacheSolidarity v3](https://arxiv.org/abs/2603.10726v3), revised October 29, studies selective cache isolation; [KVGov](https://arxiv.org/abs/2608.09225) describes per-principal isolation but evaluates its defense in calibrated simulation. Neither was reproduced here or adopted as a dependency.

Attention masks constrain information flow, not instruction authority over a decoder that can read hostile content. Provenance weights are experimental relevance signals, not access control. Tensor shape/checksum checks establish compatibility/integrity, not benign semantic content. See the [security contract](../architecture/security.md) for enforceable boundaries and residual risks.

## Measurable experiments

Keep the existing [V/G/T workloads](../evaluation/workloads.md) unchanged. Add a separate runtime experiment with these factors:

1. Retrieval timing: upfront, reactive at a supported boundary, reactive plus bounded prefetch. First replay a fixed evidence/trigger schedule; then separately test adaptive candidate selection.
2. Cache: disabled versus exact compatible prefix reuse, crossed with timing. Caching is not a competing retriever. Full-prefill input-equivalent runs are the correctness control.
3. Optional representation: full rendered evidence versus non-prefix cache repair or a trained bridge. Fix graph snapshot, evidence, model and budgets wherever the mechanism permits; report differences in exposure/order and any training cost.

Record grounded task correctness, evidence recall, attack success/clean regressions, TTFT, time to first useful grounded output, retrieval stalls, total latency, discarded/regenerated tokens, memory residency, transfer bytes, cache validity, prefetch use/timeliness, and demand-request slowdown. Do not compare only a cold upfront arm to a warm adaptive arm. A fixed evidence schedule isolates availability/representation; adaptive retrieval tests a different hypothesis and may select different evidence from the same snapshot.

For prefetch, estimate expected saved critical-path time and charge CPU/GPU work, network bytes and byte-seconds with explicit policy weights. Also publish raw measures. A graph-neighbor heuristic and no-prefetch control precede any trained predictor. Unused prefetch can still evict useful data, congest demand requests or leak access patterns; it is not automatically harmless because the model never saw its content.

Reject unsupported numerical claims such as “95% of cases get 90% of the benefit.” No reviewed source establishes that bound for Tunic. There is also no evidence here that a particular commercial code agent implements the described predictor, or that the full combination is novel or production-proven.

## Reading depth and reproducibility

Reviewed methods/excerpts: FLARE's trigger/regeneration scheme; G-Retriever's projector/text path; CacheBlend's repair and positional discussion; Cache-Craft's contextualization/repair; DualDecoder's prediction pipeline; token-collapse methods, interventions and limitations. Reviewed official documentation sections: vLLM prefix reuse/disaggregation/offload and isolation, Transformers framing control. Other linked papers were read at abstract/metadata level, including security/caching candidates and architectural precedents; do not infer full method review. No linked repository was cloned/audited, no published result reproduced, and no GPU/backend capability tested. Versioned arXiv links identify specifically read methods where available; `/latest`, `main`, and tokenizer documentation must be repinned before implementation.
