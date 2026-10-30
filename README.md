# Tunic

Tunic is a proposed shared evidence and context substrate for AI systems: a semantic graph, a context compiler, and model-specific runtime adapters.

The compiler's proposed primary output is a reusable **typed context IR**. Renderers turn that artifact into prompts or token plans; runtime adapters manage compatible model execution and cache reuse.

Status: a bounded Python/SQLite W1 reference implementation is available. It ingests Python source, records static call evidence in immutable snapshots, queries possible change impact, and emits validated deterministic context JSON. The full architecture, V/G/T benchmarks, runtime adapters, and external integrations remain unimplemented.

## Run the reference slice

Requires Python 3.11 or newer; no third-party runtime dependencies. From this repository:

```powershell
New-Item -ItemType Directory -Path .tunic -Force | Out-Null
python -m tunic --db .tunic/example.sqlite ingest examples/w1
python -m tunic --db .tunic/example.sqlite impact core.py::calculate | Set-Content -Encoding utf8 .tunic/context.json
python -m tunic --db .tunic/example.sqlite validate .tunic/context.json
python -m unittest discover -s tests -v
```

The example finds `service.py::process` and `test_service.py::test_process`, with exact call excerpts and dependency paths. The text match in `unrelated.py` is excluded; its callback remains unresolved. Re-run `ingest` after source edits or deletions: the current view is rebuilt atomically and old snapshots remain available. Validation requires the current snapshot by default; `validate --allow-historical` explicitly permits historical evidence validation.

For another repository, pass its Python source root (for example `repository/src`) and a separate database. Symbols use exact `relative/path.py::function` keys. `impact --max-depth 8 --max-nodes 100` bounds traversal and reports reached limits. The [W1 implementation profile](docs/architecture/w1-reference.md) defines supported syntax, storage/identity semantics, and tested limits. Dynamic dispatch, classes/methods, nested scopes, non-UTF-8 source, authorization, and real-world temporal reasoning are outside this profile. Output always reports partial static coverage; mechanical validation does not prove runtime impact.

Start with the [documentation guide](docs/README.md), [architecture](docs/architecture/overview.md), [semantic graph contract](docs/architecture/semantic-graph.md), [context IR contract](docs/architecture/context-ir.md), and [three-workload comparison](docs/evaluation/workloads.md).

The [semantic graph gap index](docs/research/semantic-graph/index.md) maps missing capabilities to current research and proposed validation checks.

GraphGraph is the historical reference project. Its source, names, formats, and interfaces impose no compatibility requirement on Tunic.
