# Tunic design decisions and open choices

Status: design record, 2026-10-30. This is the authoritative list of decision dispositions. It is not an implementation completion tracker. “Working” means a reversible proposal adopted for consistent design/evaluation; it does not imply user approval of an irreversible dependency or demonstrated benefit.

| ID | Question | Disposition | Rationale and revisit condition |
| --- | --- | --- | --- |
| D-01 | Product identity | Settled by user: **Tunic** | GraphGraph remains a historical reference name. |
| D-02 | Compatibility with GraphGraph | Settled by user: not required | Preserve useful evidence and lessons; retain code/contracts only when independently justified. |
| D-03 | System boundary | Working: semantic graph, context compiler, runtime adapter | Logical owners, not separate mandatory services. Revisit deployment boundaries after measurement. |
| D-04 | Compiler output | Working: model-independent typed context IR | Separate evidence meaning from rendering/execution. Compare against direct rendering in G/T arms. |
| D-05 | First comparison | Working: W1 code impact, W2 temporal decisions, W3 task resumption | W1 is the proposed first vertical slice; later suites remain planned. Dataset/model/resource choices are open. |
| D-06 | Initial evidence policy | Working: source-faithful excerpts and explicit structured assertions/derivations | No silent summarization or premise loss. Add lossy transforms only as separately scored interventions. |
| D-07 | Initial update correctness | Working: atomic publication and full recomputation oracle | Conservative invalidation is acceptable. Narrower reuse needs an independence argument and differential checks. |
| D-08 | Geometry | Working: optional derived measurement view | Structural/learned scores never become source authority. Promote a feature only after its quality/cost ablation. |
| D-09 | Runtime reuse | Working: ordinary prefill; exact compatible prefix reuse when supported | No arbitrary cross-model KV/latent portability. Fusion/compression/custom attention remain experiments. |
| D-10 | Representation and persistence | SQLite selected for the user-authorized W1 reference; production choice open | The [reference profile](w1-reference.md) uses relational/adjacency tables and atomic full snapshots. Compare alternatives against actual queries and updates before a production commitment. No RDF/property-graph conformance is established. |
| D-11 | Language and process layout | Python selected for the user-authorized W1 reference; production layout open | Standard-library local implementation first. Choose later deployment boundaries from constraints and profiling. |
| D-12 | Model/runtime/hardware | Pinned local Ollama reference profiles selected; production choice open | [Continuation profile](continuation-reference.md) supports installed Llama 3.2/dense Qwen3 with explicit raw templates and input counting probes. [Smoke results](../evaluation/continuation-results.md) identify tested model/server/hardware; no general backend recommendation. |
| D-13 | Ontology and extraction profile | Bounded Python `static_call` plus document `text_mention` and typed claims reference profile selected; broader ontology open | [W1 profile](w1-reference.md) defines identities, byte evidence, coverage and static-path meaning. Mentions link prose to code entities; structured claims assert typed relations with proposals instead of merges. No automatic schema induction, NLP relation extraction, or general entailment engine. |
| D-14 | Causal/discovery execution | Deferred | Hypotheses/constraints/transformations may be represented; execution and broad causal identification require a real harness and explicit assumptions. |
| D-15 | Integrations and federation | Deferred | Wilderbeast, Chili Crisp, and Team Ops are potential partners. No current interface compatibility is verified or required for the first slice. |
| D-16 | Numeric acceptance targets | Open | Select labels, model/hardware, sample sizes, noninferiority margins, and budgets before held-out evaluation. No imported paper speedup is a Tunic target. |
| D-17 | Runtime-mediated memory | Bounded local segment implementation; broader extension experimental | [Controller](runtime-memory.md) now has a [local segment reference](continuation-reference.md); bounded prefetch remains proposed. Exact prefix reuse is orthogonal. Non-prefix fusion/learned bridges require distinct quality and graph-use tests; arbitrary mid-layer injection is not adopted. |
| D-18 | Evidence/runtime security | Working external requirements; shared/privileged deployment conditional | [Security](security.md) requires authorization before speculative reads and at attachment, trusted framing and independent tool policy. The local continuation profile implements bounded framing/profile checks and answer support; authorization, tools and shared isolation remain absent. Attention masks, provenance weights and token stripping are not security boundaries. |

## Authority and changes

Component contracts live in [semantic graph](semantic-graph.md), [context IR](context-ir.md), [runtime](runtime.md), and [measurements](measurements.md). [Overview](overview.md) owns boundaries and flow; [workloads](../evaluation/workloads.md) owns comparison arms and task definitions. Research notes supply alternatives and evidence, not competing requirements.

When evidence changes a working choice, update this record and the owning contract together. Keep the source/measurement qualification in research. A feature becomes implemented only through actual code/check evidence; the [W1 profile](w1-reference.md) records the bounded implementation and executed checks. Target contracts must not be mistaken for completed capabilities.
