# Tunic

Tunic is a proposed shared evidence and context substrate for AI systems: a semantic graph, a context compiler, and model-specific runtime adapters.

The compiler's proposed primary output is a reusable **typed context IR**. Renderers turn that artifact into prompts or token plans; runtime adapters manage compatible model execution and cache reuse.

Status: research and architecture design. No Tunic engine, benchmark result, or external integration has been implemented.

Start with the [documentation guide](docs/README.md), [architecture](docs/architecture/overview.md), [semantic graph contract](docs/architecture/semantic-graph.md), [context IR contract](docs/architecture/context-ir.md), and [three-workload comparison](docs/evaluation/workloads.md).

The [semantic graph gap index](docs/research/semantic-graph/index.md) maps missing capabilities to current research and proposed validation checks.

GraphGraph is the historical reference project. Its source, names, formats, and interfaces impose no compatibility requirement on Tunic.
