# Research assessment

Research date: 2026-10-30. This document separates source-backed findings from proposed engineering conclusions.

Follow-up research added [current projects and 2026 papers](project-landscape.md), including ReCAP and CodeComp. These extend the initial runtime direction: a graph can also guide history selection using attention feedback or guide lossy KV retention using program structure. Both require separate quality/cost evaluation; neither makes arbitrary tensor state portable.

## What a context graph should mean here

Three different graphs are relevant: an evidence graph of entities and claims; a dependency graph of the context needed for a task; and a runtime prefix tree or block graph representing reusable computation. They can reference one another, but collapsing them loses essential semantics.

[Microsoft GraphRAG](https://arxiv.org/abs/2404.16130) targets corpus-level question answering using extracted relationships and community summaries. Its [query documentation](https://microsoft.github.io/graphrag/query/overview/) distinguishes local entity-oriented retrieval from global summarization. That is useful for broad synthesis, but does not itself specify a model-state protocol.

[HippoRAG 2](https://arxiv.org/abs/2502.14802) combines passage integration, retrieval models, and Personalized PageRank for associative retrieval. Its reported improvements motivate testing diffusion over typed evidence, without assuming graph retrieval improves every task.

[G-Retriever](https://arxiv.org/abs/2402.07630) formulates subgraph retrieval with prize-collecting Steiner trees and combines graph encoding with soft prompting. This provides a concrete trained bridge between graph structure and an LLM, rather than assuming arbitrary graph vectors are already meaningful to the model.

[Zep/Graphiti](https://arxiv.org/abs/2501.13956) studies temporal agent memory. The [Graphiti repository](https://github.com/getzep/graphiti) is a practical reference for incremental knowledge extraction. Its extraction and reconciliation costs should be included in any comparison.

Proposed synthesis: preserve explicit evidence and temporal semantics; compare lexical/dense retrieval against bounded graph expansion and diffusion; compile selected evidence into a reproducible runtime input.

## Assessment of the binary/tensor proposal

| Proposal | Assessment | Design consequence |
| --- | --- | --- |
| Replace JSON with binary buffers | Can reduce parsing and transport overhead. HTTP can carry binary; JSON parsers need not use regex. | Measure serialization separately from inference. No new protocol is required merely to avoid UTF-8. |
| Send existing token IDs | Can avoid repeated tokenization if the runtime accepts them and tokenizer/template identity is pinned. | A useful adapter capability, not a new semantic representation. |
| Invent compact opcode IDs | New vocabulary entries require learned meaning; reusing unrelated IDs does not transfer opcode semantics. | Treat new opcodes as a training experiment. |
| Inject arbitrary residual tensors | Correct shape is insufficient for semantic compatibility. Model, layer, contextual computation, and positional conventions matter. | Require a trained bridge or a precisely controlled same-model experiment. |
| Transfer KV state | Established runtime capability under compatible execution conditions. | Check prefix dependence and tensor layout, not just matching weights. |
| Map graph topology to attention | Changes computation and potentially model behavior. | Separate quality-gated experiment; not a transparent optimization. |

The quoted 45-token versus 9-token example is illustrative, not a measured tokenizer result. Attention cost depends on model and kernel: dense prefill has a quadratic attention term, but the complete inference pipeline is not simply O(T^2). No universal microsecond estimate or thousandfold speedup follows from that example.

For an ordinary causal decoder, context is represented through ordered positions and layer state. Attention is causal and head/layer dependent, not a single fully connected semantic graph. Some architectures also use sparse, sliding-window, recurrent, or multimodal mechanisms. The semantic graph remains explicit outside the model unless a model-specific integration is built.

## Computed context is prefix dependent

[vLLM's prefix cache design](https://docs.vllm.ai/en/latest/design/prefix_caching/) chains block identity through its parent prefix and includes additional distinguishing inputs. This is direct evidence against indexing reusable KV by isolated chunk hash alone.

[SGLang](https://arxiv.org/abs/2312.07104) introduces RadixAttention for sharing KV state across related calls. Its radix tree is an execution-prefix index, not an entity or knowledge graph. [LMCache](https://docs.lmcache.ai/) provides infrastructure for cache storage and reuse; runtime integration and compatibility still need verification.

[CacheBlend](https://arxiv.org/abs/2405.16444) addresses reuse of non-prefix chunks with selective recomputation. Its results support investigating this problem, not declaring arbitrary cached chunks interchangeable. Reused KV tensors generally miss the effects of newly preceding context.

Practical inference: first maximize compatible shared prefixes; measure whether their reuse outweighs lookup, transfer, and residency costs. Content addressing source evidence and addressing computed KV require different keys.

## Structured attention and learned compression

[Medusa](https://arxiv.org/abs/2401.10774) uses tree attention to verify candidate continuations. This demonstrates a specialized execution mechanism, not that masking an arbitrary knowledge graph preserves a pretrained model's reasoning quality. Sibling position reuse is meaningful only within a complete position/mask/cache scheme.

[GraphSAGE](https://arxiv.org/abs/1706.02216) learns inductive node representations by neighborhood aggregation. It can supply retrieval features, but its output vectors are not automatically valid LLM input embeddings. G-Retriever is a more relevant reference for a trained graph-to-model bridge.

[Lost in the Middle](https://arxiv.org/abs/2307.03172) reports positional sensitivity on its evaluated tasks and models. It motivates testing placement and evidence distance; it is not proof that every current model has identical biases.

## Transport and memory are separate problems

[Apache Arrow](https://arrow.apache.org/docs/format/Columnar.html) specifies relocatable layouts suitable for zero-copy shared-memory access. That does not mean arbitrary host buffers can be dereferenced efficiently by a GPU or transported across machines without copies. CUDA allocation, device ownership, synchronization, network registration, framing, and layout conversion remain implementation concerns.

A bf16 embedding array uses approximately `2*T*D` bytes. At D=4096, each position occupies 8192 bytes versus 4 bytes for a uint32 token ID. Fewer latent positions could offset this, but semantic compression and byte compression are different claims. KV state can be larger still; quantify it before choosing transfer over recomputation.

## Research gaps

This pass reviewed primary documentation, paper abstracts, and selected method sections for G-Retriever and HippoRAG 2. It did not reproduce results or perform a complete PhD-level literature review. Follow-up work should audit full methods, datasets, failure analyses, licenses, current implementations, and newer competing work under a reproducible bibliography. No paper's benchmark headline is adopted as a project performance target.
