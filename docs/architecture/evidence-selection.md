# Evidence selection contract (v1, target)

Decision contract for the query-aware selection layer: which
candidates enter agent context, with what evidence, and why the rest
do not. Status: **target contract, not yet implemented**. The current
pipeline (fusion scores + global threshold + budgets) is the baseline
this contract must beat on the frozen suites before it changes any
default. Do not tune it on the 14-task development probes; validate
on positives, negatives, and the held-out split.

## Ordering invariant

Authorization → temporal validity → epistemic status → relevance.
A denied or stale candidate never reaches ranking; relevance never
overrides denial. This is [policy](policy-contract.md) §3/§4 made
executable per query.

## Outcomes

Every candidate ends in exactly one outcome, with reasons:

| Outcome | Meaning | Enters context |
| --- | --- | --- |
| `supported` | Current, authorized, evidence-backed for this query | yes, with spans |
| `proposed` | Plausible candidate, insufficient evidence to assert | only as marked candidate, never as fact |
| `ambiguous` | Multiple identities, unresolved | only with the ambiguity attached |
| `contradicted` | A live counter-assertion disputes it | flagged, never silently |
| `insufficient_evidence` | Relevant-looking but ungrounded | no (trace records why) |
| `out_of_scope` | Fails authorization, time, or query intent | no (trace records which gate) |

## Query-intent obligations

Different questions need different proof. The layer must know what
the query asks before deciding what counts as evidence:

| Query kind | Example | Obligation |
| --- | --- | --- |
| Impact / blast radius | "what breaks if I change X" | observed `static_call` paths; asserted deps marked as assertions |
| Provenance lookup | "who decided X, when" | `decided_by` chain + valid-time scope; superseded marked |
| How-to / locator | "what finalizes a purchase" | semantic candidates allowed, each labeled by retrieval stage |
| Historical | "what did we believe in June" | transaction/valid-time scoped view, corrections visible |

## Selection trace

Each selection returns a trace: per candidate, its outcome, the
winning and losing signals (stage, score, qualification, status,
policy gate), and for abstentions the nearest misses and their
exclusion reasons. The trace replays against the frozen snapshot.

## Discovery versus proof

The candidate index (lexical, semantic, name matches) optimizes
recall. The evidence graph admits only qualified relationships. The
selection layer sits between them: semantic similarity proposes, it
never proves. Pre- and post-qualification candidate sets are both
recorded in arena runs so discovery gains and filtering gains stay
separable.

## Acceptance

Adopt this layer only when, on frozen suites: precision and F1 rise
above the current hybrid baseline with Recall@1/@3 held, negatives
and held-out behave, and no global-threshold sweep explains the gain.
Until then this document is the specification, not the behavior.
