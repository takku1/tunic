# Field test: Tunic as agent tooling on a non-Python repository

**Date:** 2026-11-02
**Tunic:** working tree at `5882c4a` (Memory lifecycle v1), used through the MCP stdio boundary, as `skills/tunic/SKILL.md` prescribes
**Target:** `Z:\seancarneywebsite`, an npm monorepo
- `apps/web`: Astro 7 + TypeScript, Cloudflare Workers API routes, an LLM concierge.
- `docs/`: project docs (status, launch checklist, security baseline, AI governance, research).
- Vendored `.claude/skills/` and a gitignored crawl snapshot in `reference/`.
- `node_modules`: about 33.7k files.

**Contrast run:** `Z:\auditforge\scripts` (Python, 772 symbols).
**Database:** kept outside both repos (scratchpad); nothing was written into either target.
**Caveat:** Tunic is pre-release, and this tests it outside its documented W1 profile (Python). The point is to map what "works on any repo" would require, not to fault the profile.

Every behavior below was observed in this run, and every cause was confirmed by reading `tunic/*.py` at the commit above.

---

## 1. Summary

| | seancarneywebsite (TS/Astro + docs) | auditforge/scripts (Python) |
|---|---|---|
| Ingest | 1.7 s, 122 files, **0 code symbols** (122 `::document` entities) | 3.9 s, 772 symbols, no diagnostics |
| `retrieve` on questions the repo answers | **Abstained 4/4** (`no_discovery`), 3.5–18 s each | Found the target symbol by name, plus a reverse-impact path to `audit_repo.py::main` with derivation premises |
| Exact-key lookup of a known document | **Abstained** | n/a |
| `callees` on a TS function | `unknown or unsupported symbol` (correct) | Works; the `cold` list is noisy with stdlib calls (§4.4) |
| Honesty / failure mode | Excellent: abstentions are filed in the frontier, limitations are stated in every reply, no invented edges | Same |

**Verdict.** On a Python repo, Tunic does what it promises. On anything else it currently gives an agent nothing usable, and it still costs seconds per query. The gap is structural, not a tuning problem:
1. Extraction is Python-only.
2. Document text is never itself retrievable.
3. Workspace scoping ignores excludes and `.gitignore`.
4. Several stages hard-code Python assumptions.

The good news: the core architecture (typed IR, trust labels, frontier ledger, governed promotion, MCP boundary) is language-neutral. What's missing is the edges: extraction, discovery and scoping (§5).

## 2. What was observed

### 2.1 Ingest
- `python -m tunic --db <scratch>/site.sqlite Z:\seancarneywebsite` produced `files: 122, symbols: 122`, no assertions, no diagnostics, no frontier entries.
- Every entity is `<path>::document`, with no code entities:
  - **88 of 122** came from vendored `.claude/skills/**`: `LICENSE.txt`, 50+ font `*-OFL.txt` files, `SKILL.md` copies.
  - 16 from `reference/current-site` (a gitignored crawl).
  - Only **13** were the project's own docs.
- The 70+ `.ts`/`.astro`/`.mjs` source files were silently skipped. No diagnostic says "N files in unsupported languages were ignored".
  - Silent skip is the right default for binaries, but for source code it makes the 0-symbol result look like an empty repo.

### 2.2 Retrieval
Each of these tasks is answered verbatim in `docs/`:

| Task | Answer location | Result |
|---|---|---|
| "where is API rate limiting configured" | `docs/security/SECURITY.md` §8 (names `src/middleware.ts`, `wrangler.jsonc`) | abstain, lexical 17,917 ms (first call) |
| "DMARC record missing launch checklist" | `docs/current/02-launch-checklist.md` | abstain, 3,504 ms |
| "Scout AI concierge governance and safety rules" | `docs/current/04-scout-ai-governance.md` | abstain, 3,889 ms |
| `"docs/security/SECURITY.md::document"` (an existing exact key) | the entity itself | abstain, 6,644 ms |

Other tools:
- `related("docs/security/SECURITY.md::document")` returned an empty, honest answer, with the Python-profile limitations listed.
- `review("docs/current/02-launch-checklist.md")` returned `complete` with every list empty.
- `frontier` correctly holds one `retrieval_abstention` entry per failed task, with `required_evidence: ["rephrase the task", "add supporting evidence"]`.

### 2.3 Causes, confirmed in code

| Symptom | Cause |
|---|---|
| Exact key of a document never matches | `memory.py` ~L1122 builds the exact/name candidate set from symbols **excluding** keys ending `::document` |
| Doc text that contains the answer is never returned | `baselines.BM25` indexes `rglob("*.py")` function chunks only. `TextNotes` does index `.md/.txt`, but in `retrieve` (~L1157) a notes hit is used only to look up `text_mention` assertions pointing at **code** symbols. The document itself is never a candidate, so with no code the notes stage can't produce anything |
| 3.5–18 s per query on a 122-entity graph | `_ensure_discovery` runs `self.root.rglob("*")` with `is_symlink()`/`is_file()`/`stat()` on every file under the root, including `node_modules` (33.7k files) and `.git`. `TextNotes` then reads and tokenizes **every** `.md/.txt` under the root, including **505 files in `node_modules`**. A plain `os.walk` count of the same tree takes 0.2 s, so the time goes to per-file pathlib stats and reading and tokenizing third-party docs, which are then not used (row above) |
| Vendored and third-party files dominate | Ingest skips only `EXCLUDED = {.git, .venv, venv, __pycache__, .tunic, .research, node_modules}`. There is no `--exclude`, no `.gitignore` support and no config file. The discovery walk (`_ensure_discovery`, `BM25`, `TextNotes`) uses a **different** exclusion rule (only `__pycache__`), so ingest and retrieval disagree about what the workspace is |
| TS/JS functions unknown | `extract._scan` admits only `name.endswith(".py")` plus `TEXT_EXTENSIONS = {.md, .txt, .rst}`; parsing is CPython `ast` |

### 2.4 Contrast: the Python repo works
- `retrieve("what breaks if _header_findings changes")` returned:
  - `auditor/scanners.py::_header_findings` (name match).
  - A `possible_impact` on `audit_repo.py::main` via `reverse_static_reachability_v1`, with premises and `verification: syntactic_path_only`.
- Lexical fallbacks were clearly labeled with low scores.
- This is the evidence quality an agent wants. The aim of everything below is to make that experience available on non-Python repos.

## 3. What already transfers to any repository
These parts are language-neutral and should be kept as-is:
- **Typed context IR and trust labels.** OBSERVED / ASSERTED / CANDIDATE / WITHHELD / DISPUTED / RETRACTED don't depend on the source language.
- **Abstention plus the frontier ledger.** "An empty result is a finding" is exactly right for unfamiliar repos. Digest-anchored dedupe and proof-receipt resolution generalize.
- **Governed promotion and corrections.** `promote`/`acquire`/`correct` with authority recorded works for any `path::symbol` key scheme.
- **The MCP boundary.** One stdio session per root, a fixed principal, validated arguments, structured errors.
- **Snapshots and atomic publish,** valid-time claims and retraction-aware selection.

## 4. What blocks "any repo"

### 4.1 Extraction is Python-only (largest gap)
Everything that makes Tunic valuable (`impact`, `callees`, `related`, candidates, cold sites) needs code entities and call edges. A TypeScript monorepo yields none.

### 4.2 Documents aren't first-class retrieval targets
Many repos carry their most important context in prose: ADRs, runbooks, STATUS/CHECKLIST docs, specs. Today a doc can only help by pointing at Python code. An agent asking "what's left before launch?" or "why did we choose X?" gets nothing, even when the answer is a heading in `docs/`.

### 4.3 Workspace scoping
- No ignore model: `.gitignore`, `--exclude`, a `.tunic/config` or `tool.tunic` in `pyproject.toml`.
- Two different exclusion rules, one for ingest and one for discovery.
- No awareness of vendored code (`vendor/`, `third_party/`, `.claude/skills/`, git submodules) or generated output (`dist/`, `build/`, `.next/`, `.astro/`, `target/`).
- No monorepo or workspace model. One root equals one source tree; `apps/*` and `packages/*` aren't recognized as packages with their own import roots.

### 4.4 Unresolved-call noise
On the Python run, `callees(...).cold` lists `os.path.join`, `os.walk`, `rel.lower` and similar calls as needing "the module or class providing …". For agents these are external, not gaps.
- Classifying calls into stdlib, declared third-party (from the lockfile or manifest), workspace-resolvable but unresolved, and dynamic would shrink the frontier to actionable entries.
- The same issue will grow across languages (`console.log`, `fmt.Println`, `Array.prototype.map`).

### 4.5 Per-query cost scales with the whole tree
The discovery cache key is a stat walk of the root, and the indexes are rebuilt from the filesystem rather than from the published snapshot. On large repos this dominates latency regardless of graph size.

### 4.6 Packaging and agent onboarding
- **Packaging:** `pyproject.toml` exists, with the console script `tunic` and no runtime dependencies, so `pipx install`/`uvx` is close.
- **Skill format:** `skills/tunic/SKILL.md` has no YAML frontmatter (`name`, `description`), so Claude Code won't auto-discover or trigger it.
- **Plugin packaging:** there is no `.claude-plugin/plugin.json` or `.mcp.json` declaring the server, so it can't be installed as a plugin.
- **Docs:** the README has no "use it on my repo" quick start that sets a DB location outside the repo. Zero-config writes `<root>/.tunic/graph.sqlite` into the target, which pollutes the target's working tree unless it's gitignored.

## 5. Recommendations: making Tunic work with any codebase

Ordered by leverage. Each item keeps Tunic's evidence discipline: observations stay observations, and anything heuristic enters as a **candidate**.

### R1. Pluggable extractor interface, with tree-sitter as the default
- **Define an `Extractor` protocol:** `languages`, `file_globs`, `extract(path, bytes) -> {symbols, edges, diagnostics}`. It emits the same assertion records Python does today (`defines`, `calls`, `imports`, `depends_on`), with `profile` and `coverage` labels per language.
- **Tree-sitter grammars** (TS/TSX/JS, Go, Rust, Java, C#, Ruby, PHP, Kotlin, Swift, Bash) cover symbol definitions and syntactic call sites cheaply. Ship tags queries (the `tags.scm` convention) for definitions and references.
  - Resolution is partial, so unresolved references become **frontier entries**, never edges.
- **Higher-fidelity tier (optional):** import a SCIP/LSIF index when present (`scip-typescript`, `scip-java`, `rust-analyzer scip`, `scip-python`). These come from compilers and type checkers, so their reference edges can be labeled OBSERVED with `verification: compiler_index`.
- **Framework adapters** as small add-ons, all emitted as **candidates** with a derivation chain:
  - File-based routing (Astro/Next `pages/`, `app/**/route.ts`, SvelteKit `+server.ts`) yields route → handler entities.
  - Middleware chains: `src/middleware.ts`, Next `middleware.ts`, Express `app.use`.
  - Component composition: `<Component />` usage in `.astro`/`.tsx`/`.vue`.
- **Profile labels per language** (`ts-treesitter-v1`, `ts-scip-v1`), surfaced in every reply's `limitations`, the same way `python-static-v7` is today.
- **Acceptance:** on this repo, `callees("apps/web/src/middleware.ts::onRequest")` returns `allowed`, `requireAuth`, `handleError` and `hostOf`, and `impact("apps/web/src/lib/inquiry/process.ts::processInquiry")` reaches `src/pages/api/inquire.ts` and `src/lib/scout/tools.ts`.

### R2. Documents as first-class entities
- **Chunk prose by heading** (Markdown/RST/AsciiDoc and code docstrings) into `path::heading-slug` entities, keeping `::document` as the container. This mirrors method → class container context.
- **Make prose chunks retrievable** in the lexical and semantic stages as **OBSERVED-text** results. The answer is "this section says X", with span citations, which is a different label from a code-derived fact.
- **Fix the exact stage** so it matches `::document` and heading keys.
- **Qualify mentions across languages:** backticked paths and identifiers (`src/middleware.ts`, `processInquiry`) become `text_mention` candidates against the R1 entities, through the existing evidence-precision gate.
- **Acceptance:** the four tasks in §2.2 return the right doc section, with spans, in under 500 ms.

### R3. One workspace model shared by every stage
- **A `Workspace` object** decides membership once:
  - `.gitignore`, `.git/info/exclude` and global excludes (via `git ls-files -co --exclude-standard` when inside git, otherwise a pure-Python matcher).
  - A config file (`.tunic.toml`, or `[tool.tunic]` in `pyproject.toml`) with `include`/`exclude`/`vendored` globs.
  - Built-in defaults: `node_modules`, `dist`, `build`, `target`, `.next`, `.astro`, `.venv`, `vendor`, `third_party`, `.claude/skills`, `coverage`, plus files over a size cap and minified files.
- **Ingest, discovery and the cache key must all use it.** The cache key becomes the snapshot id plus a digest of `(path, size, mtime)` for **member files only**.
- **Build discovery indexes from the published snapshot** (already-read sources) instead of re-reading the filesystem.
- **Monorepo awareness:** detect `package.json` `workspaces`, `pnpm-workspace.yaml`, `go.work`, Cargo workspaces, and Python `src/` layouts. Record each package's import root so module resolution works per package.
- **Report coverage:** "ingested 213 files: python 0, typescript 71 (tree-sitter), markdown 44; skipped: vendored 88, generated 412, unsupported 9".

### R4. Classify unresolved calls
- **Split `cold` and frontier entries:** `external_stdlib`, `external_dependency` (resolved through lockfile/manifest names), `workspace_unresolved` (actionable), `dynamic` (unsupported by design).
- **Default `frontier`/`triage` to `workspace_unresolved`**, with external buckets as counts.
- **Lockfiles also give dependency entities for free** (`package-lock.json`, `pnpm-lock.yaml`, `poetry.lock`, `Cargo.lock`, `go.sum`), which enables queries like "which of our functions call into `@anthropic-ai/sdk`?"

### R5. Latency budget
- **Target:** p95 `retrieve` under 300 ms on a 50k-file repo after ingest.
- **Persist the BM25 indexes in SQLite** (FTS5 is in CPython's bundled SQLite on all major platforms; feature-detect it and fall back to the current pure-Python index).
- **Incremental refresh** from git (`git diff --name-only <snapshot-commit>`) when available.

### R6. Repo-agnostic onboarding for agents
- **Skill frontmatter:** add YAML frontmatter to `skills/tunic/SKILL.md` and lead with when to use it. Add an explicit "unsupported language" branch: if `ingest` reports 0 code symbols, say so and fall back to plain file reading instead of retrying `retrieve`.
- **Claude Code plugin:** ship `.claude-plugin/plugin.json` plus `.mcp.json` (`"command": "uvx", "args": ["tunic-context", "mcp", "--root", "${workspaceFolder}"]`, or equivalent), plus a marketplace entry.
- **Default DB location outside the target** (a per-user cache dir keyed by the repo path's hash), or write `.tunic/.gitignore` containing `*` on first use.
- **First-run report:** `python -m tunic doctor <root>` prints language coverage, excluded volumes, expected query latency, and which tools will be useful on this repo.

### R7. Cross-language benchmark suite
Extend `benchmarks/` with small frozen fixtures per language (TS monorepo, Go module, Rust workspace, docs-heavy repo), each labeled with:
- Expected `impact`/`callees` sets.
- Doc-section answers for prose tasks.
- Required abstentions, with no unsupported claims allowed.

Gate releases per profile, as the precision split does today. This repo, at a frozen commit, would be a good docs-heavy TS fixture.

## 6. Suggested sequencing
1. **R3 + R5 (scoping and latency).** Small, removes the 3.5–18 s overhead and vendored noise for *every* repo, Python included.
2. **R2 (documents first-class).** Makes Tunic immediately useful on docs-heavy repos in any language, before any new parser exists.
3. **R1 for TypeScript/JavaScript with tree-sitter,** then Go and Rust; SCIP import as the high-fidelity tier.
4. **R4,** then **R6 and R7** in parallel.

## 7. Reproduce
```powershell
$SP = "<scratch dir outside both repos>"
python -m tunic --db "$SP/site.sqlite" Z:\seancarneywebsite        # 122 files, 0 code symbols
python -m tunic --db "$SP/site.sqlite" mcp --root Z:\seancarneywebsite
#   tools/call retrieve {"task":"where is API rate limiting configured"}  -> abstain (no_discovery)
#   tools/call retrieve {"task":"docs/security/SECURITY.md::document"}    -> abstain (exact stage skips ::document)
#   tools/call callees  {"symbol":"apps/web/src/middleware.ts::onRequest"} -> unknown or unsupported symbol
python -m tunic --db "$SP/af.sqlite" ingest Z:\auditforge\scripts  # 772 symbols; retrieve/impact work
```

---

## 8. Re-test after `6becd06` (Universal ingest v1)

Same target, fresh databases. Run A uses defaults; run B adds `--exclude ".claude/"`.

### 8.1 What improved
| Check | Before (`5882c4a`) | After (`6becd06`) |
|---|---|---|
| Code entities on this repo | 0 | **262** (A) / 291 total (B); 21 `.astro` files contribute symbols |
| Exact lookup `apps/web/src/lib/security/auth.ts::requireAuth` | unsupported | **found** (exact stage) |
| Name lookup "what breaks if processInquiry changes" | abstain | **found** `lib/inquiry/process.ts::processInquiry` (name stage) |
| `.gitignore` / `--exclude` / build-dir defaults | none | **work**: `node_modules`, `dist`, `.astro` pruned; excluded counts reported; `--exclude` honored |
| Invented edges from heuristic symbols | n/a | **none** (0 assertions in run B): the inventory stays inventory, as promised |
| Mention candidates in doc review | none | `review docs/security/SECURITY.md` surfaces `headerSafe`, `requireAuth`, `handleError` and `escapeHtml` with strong signals (`code_span`, `module_path_context`, `dotted_or_call`) |
| Ingest time | 1.7 s | 4.5 s (A), with ~10× more entities |

### 8.2 Still open or new
1. **Inventory misses the most important TS/JS entry points.** Not captured:
   - `export const onRequest = defineMiddleware(...)` and `export const POST: APIRoute = async (...) => ...`. These are the route and middleware handlers in Astro, Next, SvelteKit and Remix.
   - UPPER_CASE module constants (`RULES`, `DEFAULT_MAX_BODY`).
   - `interface` / `type` declarations (`RouteRule`).
   - Captured: `function` declarations and some `const` arrows (`clearSiteData`, `render`, `bump`).
   - **Also captures function-local bindings as top-level symbols:** `middleware.ts::url`, `::type`, `::mod` are locals inside the handler, plus many one- to three-letter names (`b`, `d`, `h`, `q`, `el`, `id`).
   - **Suggest:** only module-scope declarations (brace depth 0 for C-family syntax), always including `export const|let|var <name>` regardless of initializer, and `export default`, `interface`, `type`, `enum`, `class`.
2. **Mention-candidate flood from common-word symbols.**
   - Run A filed **5,690** `mention_candidate` diagnostics; 3,738 came from vendored `.claude/` files (license text). The top expressions were `data` 592, `by` 424, `json` 376, `at` 321.
   - Run B still had 1,668. In SECURITY.md, the 100 candidates (the reply appears capped at 100) were mostly words like `data`, `token`, `events`, `catalog`, `trending`.
   - The real identifiers all carried at least one strong signal, and the common words carried none. **Suggest:** require a strong signal (code span, call or dotted syntax, path context) for inventory-derived symbols, or drop symbols of 3 characters or fewer and dictionary words without one. Rank review output by signal strength before applying the cap.
3. **`.claude/skills` (vendored) isn't in the default excludes** and this repo's `.gitignore` doesn't list it, so 815 of 1,106 entities in run A were vendored. Consider default-excluding the common agent-tool dirs (`.claude/skills`, `.cursor`, `.github/copilot`), or detecting vendored trees by their own `LICENSE` plus a `SKILL.md`/`package.json` boundary.
4. **Replies on non-Python symbols still say "Python profile"**:
   - `callees("…/middleware.ts::handleError")` returns empty `direct`/`cold` with the limitation "Python profile is an under-approximation".
   - `related(...)` reports `profile: python-static-v7`.
   - An agent can read empty `direct` as "calls nothing". **Suggest:** per-symbol coverage in every reply, e.g. `coverage: inventory_only (heuristic_inventory_v1) - call extraction not available for .ts`, and an `unsupported_capability` outcome instead of `complete`/empty.
5. **Prose questions still abstain** ("where is API rate limiting configured", "DMARC … checklist"), and the exact key `docs/security/SECURITY.md::document` still abstains. This matches the tracker's open "doc-first-class retrieval" item (R2).
6. **Latency unchanged:** lexical stage 2.8–13.6 s per query. Discovery still walks and reads outside the ingest scope, per the tracker's open "discovery-side scope unification" item (R3/R5).

### 8.3 Net effect for an agent on this repo
Tunic is now useful as a **symbol locator** for TypeScript/Astro: "where is `requireAuth` defined", exact-key lookups, and doc → code mention candidates with good signals. It isn't yet useful for **impact or call questions** (no TS edges) or **doc questions** (prose not retrievable). Fixing 8.2 #1 and #4 is small and would make the locator trustworthy. #2 and #3 remove most of the noise. R1 (per-language extractors) and R2 are the next real capability steps.
---

## 9. Re-test after 2026-11-03 (TS edges v1, R2 documents, R3 scope, vendored scope, inventory precision)

Later commits close or re-scope every item in 8.2 except the vendored-default question, which stays open by explicit decision.

| 8.2 item | Standing after 2026-11-03 | Evidence |
|---|---|---|
| #1 inventory misses (route/middleware entry points, UPPER_CASE constants, interface/type, locals as top-level) | Closed | 21ac060 harvester scope discipline (column-0-or-export, any-initializer const/let/var, interface/type/enum, named export defaults); onRequest harvested; tests/test_inventory_precision.py (6 tests) |
| #2 mention-candidate flood from common words | Closed | Signal-aware ranking (stage-1 recall 3x/min-10, mention tiers, section order score-then-coverage); zero-signal inventory candidates suppressed; tests/test_mention_ranking.py (10 tests); real-site 937/937 signaled candidates |
| #3 .claude/skills not default-excluded | Open by decision | Vendored scope v1 (3063896, .tunic.toml scope, tests/test_vendored_scope.py 9 tests) requires explicit config; no auto-detection and no .claude/skills default. Rationale recorded in the implementation status tracker. |
| #4 replies say Python profile on non-Python symbols | Closed | Per-symbol coverage in every reply (inventory_only plus honest limitation) and unsupported_capability instead of empty-complete (21ac060); verified TS call edges profile ts_static_binding_v1 (a3e1b1e, tests/test_ts_call_edges.py 23 tests; real-site 247 TS symbols, 147 verified edges) |
| #5 prose questions abstain | Closed | Document channel R2 (b08c93c): document and heading passages retrievable with byte-exact citations; tests/test_document_retrieval.py (10 tests); real-site 3/3 previously-abstained prose queries return cited passages |
| #6 per-query latency 2.8-13.6 s | Closed | Discovery scope unification R3 (ca76f9a): BM25/TextNotes prune before descent, scope stats; real-site cold retrieve 0.3 s; tests/test_discovery_scope.py (7 tests). p50/p95 measurement stays PROVISIONAL per D-16. |

### 9.1 Net effect for an agent on this repo (updated)

Tunic on this repo is now a symbol locator with verified TS call edges and retrievable prose: exact-key and name lookups, callees/impact over tree-sitter-backed TypeScript edges with ts_static_binding_v1 receipts, and doc-section answers with span citations. Remaining limits are the ones the tracker states openly: non-TS inventory languages stay edgeless, third-party receivers stay unresolved by design, and vendored scope needs explicit config.