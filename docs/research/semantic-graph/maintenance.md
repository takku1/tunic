# Transactions, increments, and artifact lifecycle

Scope: SG-13–SG-16. Correctness under change comes before minimizing recomputation. These are design choices, not implemented guarantees.

## SG-13 Transactions and snapshots

**Remaining gap:** the [graph contract](../../architecture/semantic-graph.md) requires atomic publication and consistent snapshot reads. Concrete transaction/index publication, concurrency, and recovery mechanisms remain unchosen.

**Established/current concepts:** [SQLite isolation](https://www.sqlite.org/isolation.html) illustrates consistent snapshots and write concurrency tradeoffs. DIAL-KG's integration description applies a round's additions/deprecations together; that architectural description does not establish crash durability for Tunic. The SmartMemory wrapper is a separate concrete receipt-persistence precedent.

**Minimum:** atomic source/assertion mutations, a monotone committed snapshot/epoch identity, versioned schema/policies, and clear query-ready state. Decide whether graph and indexes publish together or use explicit lagging index generations. A query cannot unknowingly mix graph revision N with an incompatible index from N-1.

**Check:** interrupted source update, concurrent readers/writers, index failure, restart, and schema migration. Query output must identify its consistent evidence/index view or reject unavailable freshness. Begin with one local transactional authority; distributed consensus is not a prerequisite.

## SG-14 Incremental maintenance

**Gap:** equality with full recompilation is specified, but no delta calculus, dependency representation, or cost model exists.

**Current concepts:** [Enzyme](https://arxiv.org/abs/2603.27775), SIGMOD 2026, emphasizes cost-based refresh planning for materialized-view pipelines. [Materialize arrangements](https://materialize.com/docs/fundamentals/concepts/arrangements/) describe incremental collections of `(data,time,diff)`; its [2026 self-correction discussion](https://materialize.com/blog/self-correcting-materialized-views/) addresses output drift from implementation changes. [Feldera](https://docs.feldera.com/) is another incremental-compute candidate, not a selected dependency.

**Minimum:** treat updates as additions/retractions, retain extraction/derivation dependencies, and version producer/query semantics. Prefer full refresh when its measured cost is lower or dependency support is insufficient. Immutable unchanged fragments can be reused while graph-wide selection reruns.

**Check:** insert/delete/rename, changed extractor or rule, alternate proof support, and global ranking changes. Compare full versus incremental results and total CPU/memory/update lag. “Work proportional to the edit” is not a universal guarantee for joins, hubs, or recursive dependencies.

## SG-15 Selection dependencies

**Gap:** selected evidence is known, but all conditions that could change selection are not. This is harder than tracking positive provenance.

**Current concepts:** provenance-guided concept revision is a useful localization precedent, but it is not a general certificate for arbitrary ANN ranking or graph selection. A new candidate can enter top-k without touching any selected artifact. Index rebuilding, embedding changes, or entity merges may alter many query results.

**Minimum:** record snapshot, index/model versions, predicate/scope/time filters, policy, deterministic tie-breaking, positive dependencies, and negative/search-universe dependencies. Conservative epoch invalidation is the reference. Narrower invalidation requires an explicit argument for a restricted query class; broad conceptual search may remain global.

**Check:** add a new better-ranked candidate, add an incoming caller, change alias resolution, and revoke access to an unseen bridge. Reused selection must equal a fresh compilation under the same semantics. Equal rendered text does not establish equal validity receipt.

## SG-16 Artifacts and replay

**Gap:** no canonical artifact format, publication protocol, storage lifecycle, or interoperable replay contract.

**Concrete precedent:** the [SmartMemory source audit](../../references/smartmemory-audit.md) inspected frozen payloads, domain-separated hashes, atomic receipts, and uncertain-send reconciliation. Those mechanisms belong to synchronization, not proof that a claim is true.

**Minimum:** immutable content and derivation artifacts; atomic manifest publication; bounded validation; independent content/selection/operation identities. Record producer versions and distinguish staged, published, superseded, invalidated, and physically removed artifacts. Make local readers unable to mistake partial output for validated IR.

**Conditional distributed extension:** operation IDs, idempotency bindings, continuity checks, and committed/uncertain receipt queries. Do not infer remote failure from a timeout or replay a different payload under an old operation ID. Define cancellation, resource ownership, cleanup, and retention.

**Check:** crash before/after commit, duplicate delivery, content-hash mismatch, missing referenced artifact, stale source generation, and source erasure. Recover without silently promoting uncertain state to success or dropping needed evidence.

**Decision:** a simple full-recompute transactional reference is mandatory; a distributed mirror, custom binary protocol, or continuous-dataflow backend is not. Choose optimization only from observed update/query economics.
