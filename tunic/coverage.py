"""What Tunic can establish about a repository, file by file.

Every in-scope file lands on the highest rung it qualifies for, and
everything else in scope is on the ledger with a reason. The report is
re-derived per call and never stored: it depends on which grammars this
machine has, which is a fact about the machine, not the repository.

    edges     verified calls: a stage proves bindings for this language
    entities  key paths and schema nodes
    symbols   named definitions with byte spans
    text      stored, citable, retrievable
    ledger    counted with a reason, bytes not kept
"""

from . import filetypes, pipeline, structure, tsgraph, unigraph
from .scope import _scan


def _engine_ready(path):
    """False when the edge engine exists but its grammar does not."""
    if tsgraph.is_ts_path(path):
        return tsgraph._parser(False) is not None
    spec = unigraph.spec_for_path(path)
    return spec is None or unigraph._parser(spec) is not None


def rung_of(path, head=b""):
    """(rung, language, needs_grammar) for one admitted file."""
    filetype = filetypes.classify(path, head)
    family, language = filetype.family, filetype.language
    if family in (filetypes.PYTHON, filetypes.NOTEBOOK):
        return "edges", "python", False
    if family in (filetypes.CONFIG, filetypes.SCHEMA):
        return "entities", family, False
    if family == filetypes.PROSE:
        return "text", "prose", False
    if pipeline.has_edge_engine(path):
        if _engine_ready(path):
            return "edges", language, False
        return "symbols", language, True
    if language and structure.parser_for(language) is not None:
        return "symbols", language, False
    if filetypes.suffix_of(path) in filetypes.HARVEST_EXTENSIONS:
        return "symbols", language, bool(language)
    return "text", language or "unknown", bool(language)


def report(root, exclude=()):
    """Coverage for a source root, without touching any database."""
    entries, _diagnostics, excluded = _scan(root, exclude)
    rungs, languages, missing = {}, {}, {}
    for entry in entries:
        rung, language, needs_grammar = rung_of(
            entry["path"], entry["data"][:filetypes.SNIFF_BYTES])
        rungs[rung] = rungs.get(rung, 0) + 1
        per_language = languages.setdefault(language, {})
        per_language[rung] = per_language.get(rung, 0) + 1
        if needs_grammar:
            missing[language] = missing.get(language, 0) + 1
    return {"files": len(entries), "rungs": rungs,
            "languages": dict(sorted(languages.items())),
            "ledger": excluded["ledger"],
            "pruned": {"dirs": excluded["dirs"], "files": excluded["files"]},
            "missing_grammars": dict(sorted(missing.items()))}


def fetch_grammars(languages):
    """Download grammars into the local cache. Network; explicit only.

    Returns {"fetched": [...], "unavailable": [...]}. Ingest never calls
    this: a snapshot must not depend on what a network returned today.
    """
    try:
        import tree_sitter_language_pack as pack
    except ImportError as error:
        raise ValueError("fetching grammars needs tunic-context[grammars]") from error
    fetched, unavailable = [], []
    for language in sorted(languages):
        try:
            pack.download([language])
            fetched.append(language)
        except (ValueError, RuntimeError, OSError, pack.Error):
            unavailable.append(language)
    return {"fetched": fetched, "unavailable": unavailable}
