# Representation, schema, and identity

Scope: SG-01–SG-04. All recommendations are proposed Tunic choices. Source status/depth is in the [ledger](sources.md); no standards conformance or implementation is claimed.

## SG-01 Claims, events, and qualifiers

**Remaining gap:** the [graph contract](../../architecture/semantic-graph.md) now distinguishes propositions from source assertions. The concrete ontology, qualified-event encoding, and validation fixtures remain undefined; binary relations alone can lose who approved what, when, and under which exception.

**Current concepts:** [RDF 1.2 Concepts](https://www.w3.org/TR/2026/CR-rdf12-concepts-20260407/) distinguishes triple terms and reifiers; referring to a proposition does not itself assert it. [SAG](https://arxiv.org/abs/2606.15971) offers a counter-design: source events indexed by entities and connected at query time rather than decomposing all meaning into a global triple graph.

**Minimum:** distinct proposition, source-assertion, evidence, and qualified event records; explicit participant roles and context. A property graph can represent these as nodes and typed edges. Do not commit to a native hypergraph unless it improves a measured task.

**Check:** two sources assert one proposition with different validity/authority; removing one preserves the other. Represent an approval with initiator, approver, target, policy version, and time without inventing pairwise implications. Compare event-index joins with graph traversal on the same evidence.

## SG-02 Ontology and constraints

**Gap:** no relation vocabulary, type/domain/range rules, cardinality policy, or schema migration procedure.

**Current concepts:** [OntoKG](https://arxiv.org/abs/2604.02618) makes the declarative schema reusable independently of extraction/storage. [DIAL-KG](https://arxiv.org/abs/2603.20059) combines extraction, adjudication, and evolving schemas; selected integration methods aggregate changes into one round. [SHACL 1.2 Core](https://www.w3.org/TR/2026/WD-shacl12-core-20261030/) is a current Working Draft for graph constraints, not a finished Recommendation.

**Minimum:** a small versioned schema for entities, claims, source versions, evidence, and relation roles. Validate at publication; quarantine unknown types rather than letting an extractor silently redefine production semantics. Separate structural validation from factual verification. Stage proposed schema changes, map old/new predicates, and invalidate affected derivations/indexes.

**Check:** rename/split a relation, reject an invalid literal/endpoint, and reproduce historical queries under the old schema. Compare fixed schema with controlled induction; score type proliferation, coverage, errors, review cost, and downstream utility.

## SG-03 Identity and canonicalization

**Gap:** logical identity, source revision, merge identity, and artifact digests are proposed but unspecified on the wire.

**Established/current concepts:** [RDF Dataset Canonicalization](https://www.w3.org/TR/rdf-canon/) normalizes RDF datasets for comparison/hashing; [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) defines a different JSON canonicalization problem. The [SmartMemory source assessment](../../references/smartmemory-audit.md) provides concrete domain-separated content/manifest/operation identities.

**Minimum:** stable logical IDs; immutable source versions; explicit alias/merge/split records; separately versioned content, derivation, selection, and execution hashes. Define canonical encoding, array/set semantics, number limits, and domain separation. A content hash identifies bytes under that contract, not universally equivalent meaning. Do not assume RDF canonicalization applies automatically to RDF 1.2 extensions or to a property graph.

**Check:** reordered object fields and defined sets yield the expected identity; genuinely changed claims do not. Two identical excerpts from different source versions retain separate provenance. Model/tokenizer changes affect lowering identities, not entity identities.

## SG-04 Literals and source coordinates

**Remaining gap:** the graph contract requires explicit values, units, language, coordinates, and unknown time, but their encoding and normalization rules remain undefined. Treating them as undifferentiated strings would invite accidental equivalence.

**Current concepts:** RDF's typed/language-aware literal model remains relevant; its 1.2 work also addresses text direction. Ontology-oriented extraction reinforces deciding which properties are intrinsic values versus relations. These concepts do not supply automatic unit conversion or source anchoring.

**Minimum:** typed literals with explicit units and unknown/null semantics; preserve original lexical form beside normalized values. Use byte offsets tied to immutable bytes for text and defined record pointers for structured inputs. Preserve Unicode/line-ending transformations with mappings. Distinguish a path display label from source identity.

**Check:** `5 ms` differs from `5 s`; dates do not silently gain a timezone; normalization preserves original evidence. Round-trip non-ASCII text and CRLF/LF sources without shifting excerpts. Treat two equal labels as potentially different entities.

**Decision:** adopt the semantics first; choose RDF, property tables, or another storage layout after the fixture reveals query/update needs. No representation eliminates the need to define these contracts.
