"""The claim vocabulary: which relations exist and what they may connect."""

from . import config, schemaref

# Receipt on every record this reference produces.
PROFILE = "python-static-v7"

CLAIM_RELATIONS = {"depends_on", "implements", "supersedes", "decided_by", "contradicts", "same_as",
                   "corrects", "retracts"}


RELATION_SIGNATURES = {
    # Allowed (caller-kind, callee-kind) endpoint pairs per relation.
    # depends_on deliberately spans document->code: prose cites the
    # code it documents (provenance), and code cites code. Only the
    # backwards direction (code depending on prose) is rejected.
    # Meta relations (supersedes, contradicts, corrects, retracts,
    # decided_by) span both by design and stay unconstrained.
    "depends_on": ({"code", "document", "config"}, {"code", "config"}),
    "implements": ({"code"}, {"code"}),
    "same_as": ({"code", "document"}, {"code", "document"}),
    "references": ({"schema"}, {"schema"}),
}


def _endpoint_kind(key):
    """Endpoint kind derived from the key shape alone (stateless).

    document: path::document prose records; config: Tier-3 key-path
    entities; schema: Tier-4 schema entities; everything else code.
    depends_on spans document/code/config to code/config (prose and
    config cite what they depend on; the reverse stays rejected);
    references spans schema to schema only.
    """
    if key.endswith("::document"):
        return "document"
    path = key.split("::")[0]
    if config.is_config_path(path):
        return "config"
    if schemaref.is_schema_path(path):
        return "schema"
    return "code"


def check_relation_signature(relation, caller, callee):
    """True when (relation, caller, callee) satisfies the signature table.

    same_as additionally requires same-kind endpoints (code-code or
    document-document). Unlisted relations (the meta set) accept any
    endpoint kinds.
    """
    if relation == "same_as":
        return _endpoint_kind(caller) == _endpoint_kind(callee)
    allowed = RELATION_SIGNATURES.get(relation)
    if allowed is None:
        return True
    callers, callees = allowed
    return _endpoint_kind(caller) in callers \
        and _endpoint_kind(callee) in callees


TRAVERSABLE_CLAIMS = {"depends_on", "implements"}


# Relations a retracts claim may target: asserted relationships only.
# Observations (static_call, text_mention) vanish when their source
# changes; retracting them is meaningless. Meta-operations
# (corrects, retracts) are superseded, never retracted.
RETRACTABLE = {"depends_on", "implements", "supersedes", "decided_by", "contradicts"}


def is_iso_date(value):
    """YYYY-MM-DD without importing date parsing; lexicographic order matches time order."""
    return (isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"
            and value.replace("-", "").isdigit())
