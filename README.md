# Tunic

Tunic is an evidence compiler for AI context. It separates source-grounded graph evidence, reusable typed context, and model-specific rendering/execution.

The compiler emits a bounded W1 **typed context IR**. A faithful renderer preserves its evidence and source mappings; a local Ollama adapter supports a two-segment continuation experiment with pinned models.

Status: Python/SQLite ingestion with methods and self/cls receivers, static impact queries, document mentions with an evidence-precision gate, temporal claims with retrieval policies, retraction-aware evidence selection (opt-in), trust-labeled model context rendering (observed/asserted/corrections/disputed/withheld with truncation receipts), deterministic evidence rendering, local segment continuation, a persistent agent-memory API with a multi-session record/apply/correct benchmark, an MCP stdio boundary, and local-embedding hybrid retrieval with a persistent vector cache are implemented and benchmarked. See [benchmarks/precision](benchmarks/precision) (frozen mention-precision split), [benchmarks/arena](benchmarks/arena) (semantic baseline runs and audit notes), and the [architecture overview](docs/architecture/overview.md). Prefetch, custom KV integration, multi-user authorization, and distributed operation remain unfinished. [Recorded smoke results](docs/evaluation/continuation-results.md) include failed model answers as well as a successful mechanically supported example; they establish no general quality or speed advantage.

## Run the reference slice

Requires Python 3.11 or newer; no third-party runtime dependencies. From this repository:

```powershell
New-Item -ItemType Directory -Path .tunic -Force | Out-Null
python -m tunic --db .tunic/example.sqlite ingest examples/w1
python -m tunic --db .tunic/example.sqlite impact core.py::calculate | Set-Content -Encoding utf8 .tunic/context.json
python -m tunic --db .tunic/example.sqlite validate .tunic/context.json
python -m tunic --db .tunic/example.sqlite render .tunic/context.json | Set-Content -Encoding utf8 .tunic/rendered.json
python -m tunic --db .tunic/example.sqlite mentions core.py::calculate
python -m tunic --db .tunic/example.sqlite claims core.py::calculate
python -m tunic --db .tunic/example.sqlite related core.py::calculate
python -m tunic --db .tunic/example.sqlite timeline core.py::calculate --at 2026-06-01
python -m tunic note notes/session.md --text "Investigating core.py::calculate."
python -m tunic --db .tunic/example.sqlite draft notes/session.md
python -m unittest discover -s tests -v
```

The example finds `service.py::process` and `test_service.py::test_process`, with exact call excerpts and dependency paths. The text match in `unrelated.py` is excluded; its callback remains unresolved. Re-run `ingest` after source edits or deletions: the current view is rebuilt atomically and old snapshots remain available. Validation requires the current snapshot by default; `validate --allow-historical` explicitly permits historical evidence validation.

## Local model continuation

Requires an already running local Ollama with an installed dense `qwen3` or `llama3.2` GGUF model. Tunic does not download models. List installed tags first, then pin a supported one:

```powershell
python -m tunic models
python -m tunic pin qwen3:4b | Set-Content -Encoding utf8 .tunic/model-profile.json
python -m tunic --db .tunic/example.sqlite experiment core.py::calculate --task "Which functions and tests might be affected by changing calculate?" --profile .tunic/model-profile.json --answer-tokens 512 --repeats 2 | Set-Content -Encoding utf8 .tunic/experiment.json
```

The controller compares evidence supplied before planning with evidence supplied after a completed, buffered planning segment. Final responses must name supported symbols, paths and source citations; malformed/unsupported answers and output caps return nonzero outcomes. Mechanical support is not proof of runtime behavior or independent answer quality. If a model is removed/replaced, refresh `models`, repin, and restart the comparison; profiles never switch models mid-run.

Offline `render --max-bytes` is an evidence-byte budget, not a token count. Exact complete-prompt counts require a charged one-token inference probe on this backend. Probe work and cache effects are included in traces; over-budget input may consume probe work before explicit rejection. See the [continuation profile](docs/architecture/continuation-reference.md) for supported templates, budgets and security limits.

For another repository, pass its Python source root (for example `repository/src`) and a separate database. Symbols use exact `relative/path.py::function` keys. `impact --max-depth 8 --max-nodes 100` bounds traversal and reports reached limits. The [W1 implementation profile](docs/architecture/w1-reference.md) defines supported syntax, storage/identity semantics, and tested limits. Dynamic dispatch, nested scopes, non-UTF-8 source, and multi-user authorization remain outside the profile; classes/methods and temporal valid-time queries are supported. Output always reports partial static coverage; mechanical validation does not prove runtime impact.

## Agent memory boundary and semantic retrieval

An MCP-compatible stdio server exposes retrieval, impact, timeline, review, note, promote, and refresh over a fixed workspace root (`python -m tunic --db <db> mcp --root <repo>`); see [docs/architecture/mcp.md](docs/architecture/mcp.md). Hybrid retrieval fuses exact, name, lexical, and optional local-embedding stages with reciprocal-rank fusion and abstention; the embedding provider seam supports a pinned `nomic-embed-text` model with a persistent, digest-keyed vector cache. Bare prose mentions are qualified before becoming graph evidence (see [docs/architecture/evidence-qualification.md](docs/architecture/evidence-qualification.md) and [benchmarks/precision/README.md](benchmarks/precision/README.md)); unqualified matches persist as reviewable candidates, never as edges. Admission, qualification, authorization, selection, and action are governed by the [knowledge policy contract](docs/architecture/policy-contract.md) with the versioned [graph schema](docs/architecture/graph-schema.md). No embedding model is ever downloaded automatically.

Start with the [documentation guide](docs/README.md), [architecture](docs/architecture/overview.md), [semantic graph contract](docs/architecture/semantic-graph.md), [context IR contract](docs/architecture/context-ir.md), and [three-workload comparison](docs/evaluation/workloads.md).

The [semantic graph gap index](docs/research/semantic-graph/index.md) maps missing capabilities to current research and proposed validation checks.

GraphGraph is the historical reference project. Its source, names, formats, and interfaces impose no compatibility requirement on Tunic.
