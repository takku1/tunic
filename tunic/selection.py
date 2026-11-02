"""Evidence-aware selection: decide what enters context, with reasons.

Sits between fusion and context use: fusion ranks what was found, this
layer decides what is supported enough to return. Similarity proposes,
structure disposes — a candidate with no graph ties and no identity in
the query is discovery noise, not evidence.

Status: default ON since 2026-11-02 (`answer(...)` selects unless
`select=False`). Named history is kept as flagged historical context;
see the contract.

Outcomes follow the evidence-selection contract: supported, proposed,
ambiguous, contradicted, insufficient_evidence, out_of_scope. Every
decision carries reasons; the trace replays against the snapshot.
"""

import re

IMPACT_PATTERNS = (
    r"break|broken|affect|impact|depend|what happens|if .+ chang|"
    r"blast radius|downstream|caller|who calls|reli(es|ed)"
)

PROVENANCE_PATTERNS = (
    r"who decid|whose decision|why (was|did|is)|history of|supersed|"
    r"obsolet|when was|chose|rationale|overrul|revert"
)

OUTCOMES = ("supported", "proposed", "ambiguous", "contradicted",
            "insufficient_evidence", "out_of_scope")


def classify_intent(task):
    """Deterministic query-intent classification with reasons.

    Impact queries need observed behavior; provenance queries need
    asserted history; everything else is a locator, where semantic
    discovery is allowed but still needs structural corroboration.
    """
    text = task.lower()
    if re.search(IMPACT_PATTERNS, text):
        return {"intent": "impact",
                "reasons": ["query asks about effects, breakage, or dependence"]}
    if re.search(PROVENANCE_PATTERNS, text):
        return {"intent": "provenance",
                "reasons": ["query asks about decisions, history, or supersession"]}
    return {"intent": "locator",
            "reasons": ["no impact or provenance markers; treating as a locator query"]}


def _live_claim_maps(view):
    """Index asserted claims once per selection pass.

    Mirrors the compiler's staleness rules: supersedes blocks its callee
    under the current policy; contradicts touching a symbol flags it.
    """
    superseded_by = {}
    contradicted = set()
    for assertion in view["assertions"]:
        if assertion.get("status") != "asserted_in_source":
            continue
        proposition = assertion["proposition"]
        relation = proposition.get("relation")
        caller, callee = proposition.get("caller"), proposition.get("callee")
        if relation == "supersedes" and callee:
            superseded_by.setdefault(callee, []).append(caller)
        elif relation == "contradicts" and (caller or callee):
            if caller:
                contradicted.add(caller)
            if callee:
                contradicted.add(callee)
    return superseded_by, contradicted


def candidate_evidence(view, symbol, anchors=None):
    """Structural evidence touching one candidate symbol.

    Asserted ties use supports-minus-retractions, mirroring the compiler:
    a fully retracted edge contributes no corroboration, so selection
    cannot resurrect through ranking what traversal correctly removed.

    When `anchors` (subject keys the query names) is given, `anchored_ties`
    counts only edges shared with an anchor: raw connectivity says a
    symbol is busy, anchored ties say it is busy *with this question*.
    """
    from .extract import TRAVERSABLE_CLAIMS
    subjects = set(anchors or ())
    supports, retractions = {}, {}
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if assertion.get("status") != "asserted_in_source":
            continue
        relation = proposition.get("relation")
        if relation in TRAVERSABLE_CLAIMS:
            key = (relation, proposition.get("caller"), proposition.get("callee"))
            supports[key] = supports.get(key, 0) + 1
        elif relation == "retracts" and proposition.get("retract") in TRAVERSABLE_CLAIMS:
            key = (proposition.get("retract"), proposition.get("caller"),
                   proposition.get("callee"))
            retractions[key] = retractions.get(key, 0) + 1
    observed, asserted, mentions, proposals = 0, 0, 0, 0
    anchored = 0
    ambiguous = False
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("caller") != symbol and proposition.get("callee") != symbol:
            continue
        relation = proposition.get("relation")
        status = assertion.get("status")
        other = proposition.get("callee") if proposition.get("caller") == symbol else proposition.get("caller")
        counts = True
        if relation == "static_call":
            observed += 1
        elif (relation in TRAVERSABLE_CLAIMS and status == "asserted_in_source"
                and supports.get((relation, proposition.get("caller"),
                                  proposition.get("callee")), 0)
                > retractions.get((relation, proposition.get("caller"),
                                   proposition.get("callee")), 0)):
            asserted += 1
        elif relation == "text_mention" and status == "syntactic_observation":
            mentions += 1
        elif status in ("proposed", "proposed_ambiguous"):
            proposals += 1
            if status == "proposed_ambiguous":
                ambiguous = True
        else:
            counts = False
        if counts and other in subjects and relation in (
                "static_call", "text_mention", *TRAVERSABLE_CLAIMS):
            anchored += 1
    return {"observed_edges": observed, "asserted_ties": asserted,
            "qualified_mentions": mentions, "proposals": proposals,
            "ambiguous": ambiguous,
            "graph_ties": observed + asserted + mentions + proposals,
            "anchored_ties": anchored,
            "retracted_ties": sum(1 for key, count in supports.items()
                                  if count <= retractions.get(key, 0)
                                  and symbol in key[1:])}


OUTCOME_ORDER = {"supported": 0, "proposed": 1, "ambiguous": 2,
                 "contradicted": 3, "insufficient_evidence": 4,
                 "out_of_scope": 5}


def select(task, fused, *, view, policy, limit, anchors=None):
    """Filter fused [(score, key, origins)] into kept keys plus a trace.

    Structural rules, no tuned cutoffs:
    - superseded candidates are out_of_scope under the current policy, unless the query names them or an impact/provenance query has no other viable evidence (kept flagged as historical context, never as current);
    - contradicted candidates are kept but flagged, never silently;
    - ambiguous identity is kept with the ambiguity attached;
    - exact/name matches are supported: the query names the entity;
    - otherwise a candidate needs at least one graph tie to be more
      than discovery noise; impact queries additionally require
      observed edges for `supported` (asserted-only is `proposed`).
    - for impact queries with `anchors` (subject keys the query names),
      discovery-only candidates need a graph path to a named subject:
      raw connectivity without it is hub noise, not query evidence,
      so unanchored discovery is insufficient_evidence even when the
      candidate is busy elsewhere.
    """
    intent = classify_intent(task)
    subjects = set(anchors or ())
    superseded_by, contradicted = _live_claim_maps(view)
    code_keys = [s["key"] for s in view["symbols"]
                 if not s["key"].endswith("::document")]
    leaves = {}
    for key in code_keys:
        leaves.setdefault(key.split("::")[-1].split(".")[-1], []).append(key)
    order = {"exact": 0, "name": 1, "lexical": 2, "semantic": 3}
    decisions, kept = [], []
    superseded_hold = {}
    for score, key, origins in fused:
        evidence = candidate_evidence(view, key, anchors=subjects)
        best = min(origins, key=order.__getitem__)
        reasons = ["best stage: " + best,
                   "graph ties: %d" % evidence["graph_ties"],
                   "anchored ties: %d" % evidence["anchored_ties"]]
        if key in contradicted:
            outcome = "contradicted"
            reasons.append("a live contradicts claim touches this symbol")
            kept.append(key)
        elif evidence["ambiguous"] or len(leaves.get(
                key.split("::")[-1].split(".")[-1], [])) > 1:
            outcome = "ambiguous"
            reasons.append("identity shared or proposed as ambiguous")
            kept.append(key)
        elif best in ("exact", "name"):
            outcome = "supported"
            reasons.append("entity named in the query; identity established")
            kept.append(key)
        elif (intent["intent"] == "impact" and subjects
                and evidence["anchored_ties"] == 0):
            outcome = "insufficient_evidence"
            reasons.append("no graph path to the named subject; "
                           "discovery noise for this impact query")
        elif evidence["graph_ties"] == 0:
            outcome = "insufficient_evidence"
            reasons.append("discovery-only candidate with no graph ties")
        elif intent["intent"] == "impact" and evidence["observed_edges"] == 0:
            outcome = "proposed"
            reasons.append("asserted or mentioned but not observed in the call graph")
            kept.append(key)
        else:
            outcome = "supported"
            reasons.append("discovery corroborated by graph evidence")
            kept.append(key)
        merits = outcome
        if policy == "current" and key in superseded_by:
            note = ("superseded by " + ", ".join(sorted(superseded_by[key]))
                    + " under current policy")
            if best in ("exact", "name"):
                # Identity established: the query names this symbol, so it
                # is historical context for the question, not a stale
                # discovery. The merits outcome stands; the note records
                # that it must not be read as current.
                reasons.append(note + "; returned as named historical "
                                     "context, not current")
            else:
                outcome = "out_of_scope"
                reasons.append(note)
                if key in kept:
                    kept.remove(key)
                if merits in ("supported", "proposed"):
                    superseded_hold[key] = (merits, note)
        decisions.append({"symbol": key, "score": score, "match": best,
                          "origins": origins, "outcome": outcome,
                          "reasons": reasons, "evidence": evidence})
    if (not kept and superseded_hold
            and intent["intent"] in ("impact", "provenance")):
        # Silence would be worse than flagged history: an effects/history
        # question whose only viable evidence is superseded keeps that
        # evidence marked as historical context, never as current.
        # Withheld discovery stays withheld whenever live evidence exists.
        for decision in decisions:
            key = decision["symbol"]
            if key in superseded_hold:
                merits, note = superseded_hold[key]
                decision["outcome"] = merits
                decision["reasons"].append(note + "; retained as historical "
                                                "context, not current")
                kept.append(key)
    kept = kept[:max(limit, 0)]
    if len(fused) > max(limit, 0):
        reasons = ["fused total %d exceeds limit %d" % (len(fused), limit)]
    else:
        reasons = []
    trace = {"intent": intent, "policy": policy, "decisions": decisions,
             "budget_note": reasons,
             "abstained": not kept,
             "abstention_reason": ("no candidate survived selection"
                                   if not kept else None)}
    assert all(d["outcome"] in OUTCOMES for d in decisions)
    return {"kept": kept, "trace": trace}
