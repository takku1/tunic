"""Valid-time reasoning: bound normalization and date-ordered conflict surfacing.

Academic footing: Allen's interval algebra (1983, 13 relations) is the
complete formalism for interval overlap; Snodgrass's bitemporal model
(BCDM 1994, ISO SQL:2011) separates valid time (when the fact is true)
from transaction time (when it was recorded) and closes overlapping
effective periods. Graphiti applies the same ideas with explicit
valid_at/invalid_at arithmetic; Tunic keeps the governed path -- this
module only normalizes bounds and *proposes* conflicts, it decides
nothing.

Optimized for AI, not human reading: deterministic regexes, ISO
dates, machine-checkable signals, no model call.
"""

import re

TEMPORAL_RULE = "temporal_bounds_v1"
CONFLICT_RULE = "temporal_order_conflict_v1"

_FUTURE_MARKERS = re.compile(
    r"\b(will|shall|going to|plan to|planned to|planning to|"
    r"intend to|intended to|expect to|expected to|"
    r"promis(?:e|ed|ing) to|scheduled to|is scheduled to|"
    r"aim to|aims to|committed to|by|before|deadline)\b",
    re.IGNORECASE)

_DEADLINE_MARKERS = re.compile(
    r"\b(by|before|no later than|deadline|due by|due on)\b",
    re.IGNORECASE)

_END_MARKERS = re.compile(
    r"\b(until|through|thru|expir(?:e|es|ed|ing)|end(?:s|ed|ing)?|"
    r"stop(?:s|ped)?|cease(?:s|d)?|no longer)\b",
    re.IGNORECASE)

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_iso_day(value):
    """(year, month, day) or None for non-ISO-day strings."""
    if not isinstance(value, str):
        return None
    match = _ISO_DATE.match(value.strip())
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return (year, month, day)


def normalize_bounds(fact_text, reference_iso, valid_at=None,
                     invalid_at=None):
    """Normalize temporal bounds for one asserted fact.

    Future-tense statements are valid at statement (reference) time,
    not at the future occurrence: ``valid_at`` moves back to the
    reference date. A soft deadline ("will complete by Friday") is
    never an invalidation time; only explicit end-of-state markers
    ("until", "no longer", "expired") license an end bound, and this
    function never invents one -- it only clears a deadline-derived
    invalid_at. Returns (valid_at, invalid_at, signals).
    """
    signals = []
    if not isinstance(fact_text, str) or not fact_text:
        return valid_at, invalid_at, signals
    if parse_iso_day(reference_iso) is None:
        return valid_at, invalid_at, signals
    if not _FUTURE_MARKERS.search(fact_text):
        return valid_at, invalid_at, signals
    if valid_at is None or valid_at > reference_iso:
        if valid_at != reference_iso:
            signals.append("future_statement_pinned_to_reference")
        valid_at = reference_iso
    if (invalid_at is not None and invalid_at > reference_iso
            and _DEADLINE_MARKERS.search(fact_text)
            and not _END_MARKERS.search(fact_text)):
        signals.append("soft_deadline_is_not_invalidation")
        invalid_at = None
    return valid_at, invalid_at, signals


def _same_pair_groups(view, suppressed=()):
    """Group asserted traversable/same_as claims by endpoint pair."""
    from .extract import TRAVERSABLE_CLAIMS
    suppressed = set(suppressed or ())
    groups = {}
    for assertion in view.get("assertions", []):
        if assertion.get("status") != "asserted_in_source":
            continue
        if assertion.get("id") in suppressed:
            continue
        proposition = assertion.get("proposition", {})
        relation = proposition.get("relation")
        if relation not in TRAVERSABLE_CLAIMS | {"same_as"}:
            continue
        caller = proposition.get("caller")
        callee = proposition.get("callee")
        if not caller or not callee:
            continue
        effective = proposition.get("effective")
        if parse_iso_day(effective) is None:
            continue
        groups.setdefault((relation, caller, callee), []).append(
            (effective, assertion.get("id")))
    return groups


def _decided(view):
    """Pairs already carrying contradicts/supersedes/retracts."""
    done = set()
    for assertion in view.get("assertions", []):
        if assertion.get("status") != "asserted_in_source":
            continue
        proposition = assertion.get("proposition", {})
        if proposition.get("relation") in ("contradicts", "supersedes",
                                           "retracts"):
            done.add(frozenset((proposition.get("caller"),
                                proposition.get("callee"))))
    return done


def propose_date_conflicts(view, limit=10, suppressed=()):
    """Propose contradicts for same-pair claims ordered dates apart.

    When one asserted claim's effective date strictly follows another's
    on the same (relation, caller, callee), the later statement
    supersedes the earlier one in time -- surfaced as a `contradicts`
    proposal with interval signals, never an auto-supersession. Undated
    claims never participate; already-decided pairs are skipped.
    Read-only; promotion goes through the governed path.
    """
    decided = _decided(view)
    candidates = []
    for (relation, caller, callee), dated in _same_pair_groups(
            view, suppressed).items():
        if frozenset((caller, callee)) in decided:
            continue
        ordered = sorted(set(dated))
        if len(ordered) < 2:
            continue
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier[0] == later[0]:
                continue
            subject, object_ = sorted((caller, callee))
            candidates.append({
                "subject": subject,
                "object": object_,
                "relation": "contradicts",
                "score": 3,
                "signals": ["earlier_effective:%s" % earlier[0],
                            "later_effective:%s" % later[0],
                            "same_pair:%s" % relation],
                "derivation": CONFLICT_RULE,
            })
    candidates.sort(key=lambda c: (c["subject"], c["object"],
                                   c["signals"][1]))
    return candidates[:limit] if type(limit) is int and limit >= 0 \
        else candidates
