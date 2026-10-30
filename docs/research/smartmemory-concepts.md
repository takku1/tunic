# SmartMemory concepts worth testing

Reviewed 2026-10-30. This document refers primarily to **smartmemory.ai's documentation**. The unrelated experimental [MauriceIsrael repository](https://github.com/MauriceIsrael/SmartMemory) is tracked separately in the [project landscape](project-landscape.md). Follow-up inspection verified public app/integration source on its own forge: see the [source assessment](../references/smartmemory-audit.md). The concept claims below remain documentation observations unless separately verified there; the core graph package was not audited.

## Hybrid retrieval with explicit channels

The [retrieval documentation](https://docs.smartmemory.ai/smartmemory/concepts/hybrid-retrieval/) describes weighted reciprocal-rank fusion across enabled channels. It distinguishes server fusion from Lite's graph-first conditional lexical/vector filling. It also notes backend differences in lexical ranking and requires ownership scoping before the native result limit.

Proposed application: make retrieval channels explicit and trace their contribution. Compare true fusion against sequential fallback; these are different algorithms even if both are described as hybrid. Add a regression where a graph channel fills its quota with weak matches while lexical search contains the decisive evidence. Keep a per-channel work limit and final evidence budget.

## Temporal versions and supersession

[Bi-temporal versioning docs](https://docs.smartmemory.ai/smartmemory/concepts/bi-temporal-versioning/) distinguish valid and transaction times, describe superseded/current queries, and disclose important limits: historical queries need the old creation/reference time, and rollback restores node properties without fully restoring relations.

Proposed application: define transaction-time and valid-time semantics in the core schema, including unknown and backdated times. Require temporal consistency across nodes and relations. Test an old fact, a later correction effective in the past, and a query asking both what was true and what was known at an earlier date. A timestamp field or supersedes edge alone is insufficient.

## Belief revision and assertion challenging

[Assertion challenging](https://docs.smartmemory.ai/smartmemory/concepts/assertion-challenging/) documents staged conflict detection and deferred/manual resolution. It includes model-assisted semantic reasoning and notes that the pipeline can stop early based on its verdict.

Proposed application: store conflicting assertions with evidence, authority, scope, and time before deciding which to surface. Keep detection separate from resolution. An LLM-generated confidence number is not proof and should not silently overwrite a source assertion. Test same-scope contradiction, different-scope coexistence, temporal change, and an attacker-controlled or erroneous claim.

## Evolution, decay, and consolidation

[Evolution docs](https://docs.smartmemory.ai/smartmemory/concepts/evolution-algorithms/) describe episodic-to-semantic promotion, synthesized observations, retention decay, retrieval strengthening, and co-retrieval edge reinforcement. The documented decay model is exponential in elapsed time, while reinforcement changes memory/edge scores based on usage.

Proposed application: separate truth, retention priority, and retrieval utility. Rarely retrieved facts can remain essential; popular mistakes can become more popular through reinforcement. Keep original evidence and reversible synthesis links, bound reinforcement, and evaluate forgetting against long-gap queries. Test a feedback loop in which an early wrong retrieval strengthens its own future rank. Memory promotion should never silently change an uncertain assertion into a verified fact.

## Architectural lesson

Useful ideas here are versioned claims, explicit retrieval channels, reversible belief revision, and observable memory lifecycle operations. They do not require inheriting SmartMemory's taxonomy, thresholds, plugin structure, deployment model, or branding.

For the redesign, begin with evidence versions and simple hybrid retrieval. Add consolidation only after retrieval and update behavior are measurable. Defer autonomous forgetting and reinforcement until a replay evaluation can demonstrate benefit without suppressing rare evidence or amplifying errors.

## Suggested controlled experiments

| Hypothesis | Experiment | Failure to detect |
| --- | --- | --- |
| Fusion improves mixed queries | Same candidates, fallback versus rank fusion, fixed final budget | Graph saturation hides exact lexical evidence. |
| Temporal selection reduces stale answers | Backdated corrections and supersession replay | Current-state results leak into historical answers. |
| Consolidation improves cross-session retrieval | Original assertions versus derived summaries with source links | Summary discards exceptions or invents certainty. |
| Usage improves retention | Decay/strengthening enabled versus disabled on a chronological trace | Popularity feedback suppresses rare critical facts. |

These are proposed tests, not established improvements or copies of SmartMemory's published evaluations.
