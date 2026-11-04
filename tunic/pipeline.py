"""The resolver stages after the Python engine, as one table.

A stage owns a family of files end to end: `resolve` turns its sources
into symbols, assertions, and diagnostics, and `affected` says which
files must re-resolve when others change. Full ingest and incremental
refresh both iterate this table, so they cannot disagree about which
stages exist or what order they run in, and a new family is one row.

Order is load-bearing: later stages read symbols earlier ones published
(blocks reuse TS module scans; notebooks resolve into Python modules).
"""

from collections import namedtuple

from . import blocks, config, notebook, schemaref, tsgraph, unigraph

Stage = namedtuple("Stage", "name resolve affected needs_modules")

STAGES = (
    Stage("typescript", tsgraph.resolve_ts_sources,
          tsgraph.ts_affected_paths, False),
    Stage("unigraph", unigraph.resolve_unigraph_sources,
          unigraph.affected_paths, False),
    Stage("blocks", blocks.resolve_block_edges,
          blocks.affected_paths, False),
    Stage("notebook", notebook.resolve_notebook_cells,
          notebook.affected_paths, True),
    Stage("config", config.resolve_config_keys,
          config.affected_paths, False),
    Stage("schema", schemaref.resolve_schema_refs,
          schemaref.affected_paths, False),
)


def has_edge_engine(path):
    """Whether a stage turns this file's symbols into verified edges."""
    return (tsgraph.is_ts_path(path) or unigraph.is_unigraph_path(path)
            or blocks.is_block_path(path))


def resolve_all(sources, symbols, assertions, diagnostics, *,
                fetch=None, known_paths=None, modules=None):
    """Run every stage. Full rebuilds omit fetch/known_paths; refresh
    passes the fresh snapshot so unchanged files stay resolvable."""
    for stage in STAGES:
        extra = {"modules": modules} if stage.needs_modules else {}
        stage.resolve(sources, symbols, assertions, diagnostics,
                      fetch=fetch, known_paths=known_paths, **extra)


def affected_paths(touched, fresh):
    """Union of every stage's re-resolution set for the touched paths."""
    out = set()
    for stage in STAGES:
        out |= stage.affected(touched, fresh)
    return out
