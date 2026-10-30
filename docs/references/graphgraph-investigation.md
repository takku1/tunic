# Existing GraphGraph investigation

Inspected 2026-10-30. All investigation outside `Z:\tunic` was read-only.

## Update: reference source supplied

The user subsequently cloned GraphGraph into `Z:\tunic\docs\graphgraph`. The checkout is at `6f6b0132a48ade34920101e1f7683943ff2b80e8`, matching a historical baseline commit identified below. Initial Git status was clean. See the [source audit](graphgraph-audit.md) for findings and limits, and the [static inventory](graphgraph-source-inventory.json) for Python file identities and definitions.

The user authorizes deleting this reference clone once review is complete and explicitly permits scrapping the old design. During the later Tunic naming/IR pass, the clone was no longer present; the agent did not remove it and its detailed review remains incomplete. The audit and inventory remain. Compatibility is optional and must be justified, rather than assumed.

The following investigation records the search before the clone was provided.

## What was found

`Z:\tunic` started empty and is not a Git repository. No existing implementation or project instructions were present there.

The visible top-level directories under `Z:\` contain neither `graphgraph` nor the spelling `grpahgraph`. This does not establish that the repository was deleted.

Direct historical evidence exists in:

- `Z:\locus\docs\graphgraph_optimization_attempt.md`
- `Z:\locus\docs\bugs\2026-07-20-stable-intrinsic-round-trip-false-positives.md`
- Archived copies of those documents under `Z:\locus\docs\archive\`.

The trial note describes a Python source tree at `src/graphgraph/`, including filesystem access, regex search, BM25, PageRank, and Git commands. The bug note references `src/graphgraph/retrieval/relevance.py:80` and a GraphGraph findings document dated 2026-07-20. These are historical descriptions, not a current source audit.

A filename search across `Z:\` additionally located:

- `Z:\recurspec\docs\research\r-400-case-record-graphgraph-dx-wdac-01.md`
- `Z:\recurspec\docs\research\pairs\graphgraph-01.md`
- `Z:\recurspec\docs\research\pairs\graphgraph-02.md`

The opening sections of all three Recurspec records were subsequently read. They identify the historical repository as `C:\Users\dcarn\aiprojects\graphgraph`. That exact path currently returns false from `Test-Path`. They also record Python CLI/MCP interfaces, evidence packets, capability identity, abstention, and response ordering as prior design concerns. These are useful redesign requirements to investigate, not current implementation guarantees.

The case records mention historical commit IDs including `6f6b013`, `11c7e25`, and `2db4f38`; they may help identify a recovered repository. Historical test counts in those records were not rerun and are not validation of this redesign.

The drive-wide search encountered access-denied directories and used default ignore behavior; absence from its results is not proof of absence. It matched filenames, not every file's contents or Git history.

## Related local projects

`Z:\graphsage\README.md` describes a separate PyTorch Geometric GraphSAGE experiment, including citation graphs and Python import graphs. It is not evidence that GraphGraph was renamed to GraphSAGE.

`Z:\memshape\docs\README.md` describes a memory-behavior analysis project. Its research directory includes local vLLM and FlashAttention source checkouts, which may be useful later for runtime source inspection. Their revisions and changes were not audited.

`Z:\locus` is a Rust discovery/optimization workspace. Its historical trial explicitly reports no algebraic rewrite innovations for GraphGraph. Do not promote the note's broader tensor-modeling aspirations into established GraphGraph functionality.

## Optional follow-up source review

If a source-reuse or historical-comparison claim requires further inspection, follow additional paths or remotes in the historical records and inspect candidate Git metadata before considering larger content searches. Recover source, tests, schemas, sample indexes, CLI contracts, and benchmarks if available. Record commit IDs and distinguish executable behavior from aspirational documentation.

Historical recovery is not a prerequisite for Tunic and compatibility is not required. Any future claim of compatibility, migration correctness, or measured improvement over GraphGraph would require its own source and evaluation evidence.
