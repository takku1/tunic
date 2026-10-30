# Semantic graph research source ledger

Accessed **2026-10-30**. Dates below are publication/submission or documented version dates, not search crawl ages. An arXiv venue comment is reported as such. No cited experiment was reproduced. **Abstract** means only abstract/metadata reviewed; **selected sections** does not mean a complete paper or code audit. Sources are primary standards, author papers, official repositories, or vendor documentation; vendor behavior/performance remains vendor evidence.

## Meaning and construction

| Source / publisher or authors | Date and status | Reading and use |
| --- | --- | --- |
| [RDF 1.2 Concepts](https://www.w3.org/TR/2026/CR-rdf12-concepts-20260407/), W3C RDF & SPARQL WG | 2026-04-07 Candidate Recommendation Snapshot | Abstract, status, triple terms/reification. Claim/assertion distinction; not final Recommendation conformance. |
| [SHACL 1.2 Core](https://www.w3.org/TR/2026/WD-shacl12-core-20261030/), W3C Data Shapes WG | 2026-10-30 Working Draft | Abstract/status and constraint examples. Shape validation, not factual truth. |
| [SHACL Inference Rules](https://www.w3.org/TR/2026/WD-shacl12-inference-rules-20260917/), W3C | 2026-09-17 First Public Working Draft | Publication/status overview; inference direction, not implementation selection. |
| [OWL 2 Primer](https://www.w3.org/TR/owl2-primer/), W3C | 2012-12-11 Recommendation | Primer semantics reference; open world and identity assumptions. |
| [PROV-O](https://www.w3.org/TR/prov-o/), Lebo, Sahoo, McGuinness / W3C | 2013-04-30 Recommendation | Overview/provenance classes; interchange basis, not entailment proof. |
| [RDF Dataset Canonicalization](https://www.w3.org/TR/rdf-canon/), W3C | 2024-05-21 Recommendation | Scope/status/algorithm overview; RDF identity, not arbitrary semantic equality. |
| [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785), RFC Editor | June 2020, Informational RFC | Canonical JSON reference; distinct from RDF canonicalization. |
| [OntoKG](https://arxiv.org/abs/2604.02618), Li et al. | 2026-04-03 preprint | Abstract; independent declarative ontology routing. No reproduction/source audit. |
| [DIAL-KG](https://arxiv.org/html/2603.20059v1), Bao et al. | 2026-03-20; arXiv reports DASFAA 2026 acceptance | Abstract and selected normalization/integration sections; dynamic extraction/schema stages. |
| [Entity Resolution in Practice](https://arxiv.org/abs/2607.26298), Pavani et al. | 2026-07-28 preprint | Abstract; match precision/recall and cluster-merge failure lessons. |
| [SNAP-KG](https://arxiv.org/abs/2608.25149), Lin, Amiri, Seneviratne | 2026-08-25; arXiv reports ISWC 2026 acceptance | Abstract; inductive candidate scoping, not identity verification. |

## Evidence, knowledge state, and change

| Source / authors | Date and status | Reading and use |
| --- | --- | --- |
| [LineageRAG](https://arxiv.org/html/2608.16004v1), Zheng et al. | 2026-08-17 preprint | Selected demand/grounding, audit, and cost sections. Support errors and online overhead remain material. |
| [Zep](https://arxiv.org/abs/2501.13956), Rasmussen et al. | 2025-01-20 preprint | Abstract/architecture precedent from earlier pass; temporal memory. |
| [ChronoQG](https://arxiv.org/abs/2607.14770), Liu et al. | 2026-07-16 preprint | Abstract; temporal question generation, not bitemporal query execution. |
| [PrimeKG-CL](https://arxiv.org/abs/2605.10529), Radwan et al. | 2026-05-11 preprint | Abstract; changed/persistent/deprecated benchmark strata; biomedical transfer not assumed. |
| [Kontrast](https://arxiv.org/abs/2607.25959), Wei, Ehrhart, Troncy | 2026-07-29 v2 preprint | Abstract; cross-format inconsistency taxonomy and limitations. |
| [Uncertainty in Representation Learning on Knowledge Graphs](https://arxiv.org/abs/2610.06974), Yuqicheng Zhu | 2026-10-03 doctoral dissertation, 255 pages | Abstract/metadata only. Uncertainty taxonomy and research leads; not a complete thesis review. |
| [HAPPI](https://arxiv.org/abs/2108.07758), Gaur et al. | 2021-08-17 arXiv record | Abstract; provenance-supported uncertain query maintenance under its query/model assumptions. |
| [Provenance Guided Incremental Learning](https://arxiv.org/abs/2608.23893), Lamaakal | 2026-08-24 preprint | Abstract; explicit rule-change localization, not arbitrary top-k invalidation proof. |
| [Enzyme](https://arxiv.org/abs/2603.27775), Yadav et al. | 2026-04-30 v2; SIGMOD Companion 2026 reference | Abstract; cost-based view refresh planning, vendor system context. |
| [Materialize arrangements](https://materialize.com/docs/fundamentals/concepts/arrangements/) and [self-correction](https://materialize.com/blog/self-correcting-materialized-views/), Materialize / Jan Teske | Current docs; article 2026-02-26 | Mechanism sections; incremental collections/output drift. Not dependency endorsement. |
| [SQLite isolation](https://www.sqlite.org/isolation.html), SQLite | Current official docs | Snapshot/write semantics; not a selected graph store. |
| [Feldera docs](https://docs.feldera.com/), Feldera | Current docs | Overview only; candidate for later IVM investigation. |

## Retrieval, runtime, trust, and evaluation

| Source / authors | Date and status | Reading and use |
| --- | --- | --- |
| [SAG](https://arxiv.org/abs/2606.15971), Wu et al. | 2026-08-25 v2 of June record | Abstract; event/entity query-time joins. [August duplicate](https://arxiv.org/abs/2608.12129) explicitly withdrawn in favor of this record; not independent evidence. |
| [AutoSchema](https://arxiv.org/abs/2608.14228), Zhang, Tsuda | 2026-08-14 preprint | Abstract; live schema/identifier/path grounding. |
| [HippoRAG 2](https://arxiv.org/abs/2502.14802), Gutiérrez et al. | 2025-06-19 v2 | Abstract/selected methods in earlier pass; associative retrieval reference. |
| [vLLM prefix cache design](https://docs.vllm.ai/en/latest/design/prefix_caching/), vLLM | Current official docs | Prefix-key semantics; pin deployed runtime before integration. |
| [ReCAP](https://arxiv.org/abs/2609.40118), Yang et al. | 2026-09-30 preprint | Earlier selected methods and current metadata; attention-derived selection signals, not semantic causality. |
| [CodeComp](https://arxiv.org/abs/2604.10235), Chen et al. | 2026-04-11 preprint | Earlier selected methods and current metadata; structural/lossy KV selection. |
| [ODRL 2.2](https://www.w3.org/TR/odrl-model/), W3C | 2018-02-15 Recommendation | Policy-model overview; does not supply enforcement. |
| [BadGraph](https://www.usenix.org/conference/usenixsecurity26/presentation/yan-leiming), Yan, Xu, Li | August 2026 USENIX Security proceedings | Venue abstract; graph availability/topology attacks, not reproduction. |
| [GraphRAG-LTM](https://arxiv.org/abs/2610.19549), Tang et al. | 2026-10-14 preprint | Abstract; distinct threat access levels. |
| [GuardRAG](https://arxiv.org/abs/2610.35071), Jiao et al. | 2026-10-26 preprint | Abstract; post-retrieval coordinated-poison filtering, not universal defense. |
| [KGI-Bench](https://github.com/ScaDS/KGI-Bench), Hofer, Rahm / ScaDS | Repository reviewed at cutoff | README; pipeline coverage/correctness/consistency; code and data not run. |
| [GraphRAG-Bench](https://arxiv.org/abs/2506.05690), Xiang et al. | 2026-02-22 v3 of June 2025 record; current project page reviewed | Abstract/project overview; stage/task comparison, not a Tunic leaderboard. |
| [Does Memory Need Graphs?](https://aclanthology.org/2026.acl-long.1232/), Hu et al. | July 2026 ACL proceedings | Abstract/earlier PDF opening; stage-wise dialog-memory controls. |
| [MKG-RAG-Bench](https://arxiv.org/abs/2606.26458), Wang et al. | 2026-06-24; arXiv reports KDD 2026 acceptance | Abstract; multimodal retrieval isolation and synthetic-curation limits. |
| [Evidence-grounded lecture KG](https://arxiv.org/abs/2608.03161), Al Farib et al. | 2026-08-04 preprint | Abstract; media grounding precedent with explicitly preliminary tiny retrieval evaluation. |

For inspected SmartMemory app code, exact repository revision, file links, and limits are in the [source assessment](../../references/smartmemory-audit.md). Broader tools and earlier academic references remain in [current landscape](../project-landscape.md) and [research](../assessment.md). Source availability, implementation compatibility, license suitability, and benchmark reproduction are separate questions.
