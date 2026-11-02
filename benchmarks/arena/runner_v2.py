"""Arena v2 runner: trusted dispatch, strict scoring, immutable evidence.

Model-agnostic and model-free. The runner never calls any model: it
takes an ``agent_fn(task_text, workspace, budget, ctx)`` callable,
runs it against an isolated workspace copy, and scores the
machine-parsed answer against the frozen key.

Integrity rules (v0.3.0):

- Tool calls execute inside the trusted ``ToolRouter``: ``ctx.action``
  authorizes (registered + permitted), admits against the budget, then
  runs the handler. Admitted attempts (completed or handler-failed)
  consume budget; denied attempts (unknown or unpermitted tool) and
  over-budget attempts do not, are recorded, and raise to the agent.
  An agent that catches ``BudgetExhausted`` and answers anyway is
  still invalid: the post-return budget check fails the cell.
- Scoring parses typed answers (exact symbol sets, ordered hops,
  single verdicts). Substring hits, contradictions, and unexpected
  extra relationships score 0. Raw answers stay verbatim in the
  transcript.
- Every run gets a unique run id and an immutable transcript path;
  nothing is overwritten and no pre-existing workspace is deleted.
- Agent exceptions finalize error cells (category, action count,
  elapsed time, incomplete status) with the full action history --
  failure is never converted into abstention or a score.
- The provenance record (repo SHA, manifest digest, runner/scoring
  versions, arm config, allowed tools, handler inventory, sandbox
  disclaimer) travels with every result.
- The agent interface carries task text only, never answer keys.
  Workspace copying is isolation, not a security sandbox: a real
  agent runs with the operator's filesystem trust, recorded as such.

Interface change from v0.2.0: ``ctx.action`` dispatches real
handlers instead of counting labels; ``run_cell`` takes ``toolsets``
and returns ``status`` (complete | budget-exceeded | error).
"""
import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from tunic.investigate import _copy_workspace
from tunic.memory import Memory

RUNNER_VERSION = "0.3.0"
SCORING_VERSION = "v3-strict"

SYMBOL_RE = re.compile(r"^[A-Za-z0-9_./-]+\.py::[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")
HOP_RE = re.compile(r"^(\S+) -> (\S+)$")
COLD_RE = re.compile(r"^COLD: (\S+)$")
VERDICT_RE = re.compile(r"^VERDICT: (UNIQUE \S+|NOT-DETERMINED)$")


class BudgetExhausted(Exception):
    """Admitted attempts passed the budget. Terminal for the cell."""


class UnknownTool(Exception):
    """No handler is registered under that name. Not budget-consuming."""


class ToolDenied(Exception):
    """Registered but outside this arm's permission set. Not consuming."""


class ToolRouter:
    """Trusted dispatch boundary. Agents cannot call tools around it."""

    def __init__(self):
        self.handlers = {}

    def register(self, name, handler):
        if name in self.handlers:
            raise ValueError("duplicate tool registration: " + name)
        self.handlers[name] = handler

    @property
    def names(self):
        return sorted(self.handlers)

    def dispatch(self, ctx, tool, args):
        if tool not in self.handlers:
            ctx._record(tool, args, "denied-unknown",
                        "no handler registered")
            raise UnknownTool("unknown tool: %r" % tool)
        if ctx.allowed_tools is not None and tool not in ctx.allowed_tools:
            ctx._record(tool, args, "denied-permission",
                        "tool not permitted for this arm")
            raise ToolDenied("tool not permitted for this arm: %r" % tool)
        ctx._admit(tool)
        try:
            result = self.handlers[tool](**args)
        except BudgetExhausted:
            raise
        except Exception as error:
            ctx._record(tool, args, "failed",
                        "%s: %s" % (type(error).__name__, error))
            raise
        ctx._record(tool, args, "completed", None, result=result)
        return result


class AgentContext:
    """Counting gate in front of the trusted router."""

    def __init__(self, budget, router, allowed_tools=None, t0=None):
        self.budget = budget
        self.router = router
        self.allowed_tools = (set(allowed_tools)
                              if allowed_tools is not None else None)
        self.t0 = t0 if t0 is not None else time.time()
        self.actions = 0
        self.rows = []

    def _record(self, tool, args, outcome, detail=None, result=None):
        row = {"t": time.time() - self.t0, "event": "action",
               "tool": tool, "args": args, "outcome": outcome}
        if detail is not None:
            row["detail"] = detail
        if result is not None:
            row["result_preview"] = str(result)[:300]
        self.rows.append(row)

    def _admit(self, tool):
        self.actions += 1
        if self.actions > self.budget:
            self._record(tool, {}, "budget-exceeded",
                         "action %d exceeds budget %d"
                         % (self.actions, self.budget))
            raise BudgetExhausted(
                "action %d exceeds budget %d" % (self.actions, self.budget))

    def action(self, tool, **args):
        """Execute a registered tool. Only admitted calls consume budget."""
        return self.router.dispatch(self, tool, args)

    def note(self, **fields):
        self.rows.append({"t": time.time() - self.t0, "event": "note",
                          **fields})


@dataclass
class TaskManifest:
    id: str
    kind: str  # callers | uniqueness | chain | decoy | abstention
    text: str
    key: dict = field(default_factory=dict)
    budget_actions: int = 12


@dataclass
class CellResult:
    task_id: str
    arm: str
    run_id: str
    answer: str
    actions: int
    wall_s: float
    score: float
    status: str  # complete | budget-exceeded | error
    transcript_path: str
    provenance: dict = field(default_factory=dict)


def normalize_answer(text):
    """Canonical answer form: lowercase lines, deduplicated, sorted."""
    lines = sorted({line.strip().lower() for line in str(text).splitlines()
                    if line.strip()})
    return "\n".join(lines)


def _symbol_lines(answer):
    return [line.strip() for line in str(answer).splitlines()
            if line.strip()]


def parse_callers(answer):
    """(ok, items | None). Exact symbols, or lone ABSTAIN. Nothing else."""
    lines = _symbol_lines(answer)
    if len(lines) == 1 and lines[0].upper() == "ABSTAIN":
        return True, []
    if any(not SYMBOL_RE.match(line) for line in lines):
        return False, None
    if any(line.upper() == "ABSTAIN" for line in lines):
        return False, None
    if len(set(lines)) != len(lines):
        return False, None
    return True, sorted(lines)


def parse_uniqueness(answer):
    """(ok, (defs, verdict, resolved)). One VERDICT line, exact defs."""
    lines = _symbol_lines(answer)
    verdicts = [line for line in lines if VERDICT_RE.match(line.upper())]
    if len(verdicts) != 1:
        return False, None
    defs = sorted(line for line in lines
                  if line.upper() not in [v.upper() for v in verdicts] and
                  SYMBOL_RE.match(line))
    if len(defs) + 1 != len(lines):
        return False, None
    verdict = verdicts[0]
    parts = verdict.split(None, 2)
    if len(parts) == 2 and parts[0].upper() == "VERDICT:" \
            and parts[1].upper() == "NOT-DETERMINED":
        return True, (defs, False, None)
    if len(parts) != 3 or parts[0].upper() != "VERDICT:":
        return False, None
    if parts[1].upper() != "UNIQUE":
        return False, None
    resolved = parts[2]
    if not SYMBOL_RE.match(resolved):
        return False, None
    return True, (defs, True, resolved)


def parse_chain(answer):
    """(ok, (hops, cold)). Ordered hops, one COLD line, optional REASON."""
    hops, colds = [], []
    for line in _symbol_lines(answer):
        hop = HOP_RE.match(line)
        if hop:
            hops.append((hop.group(1), hop.group(2)))
            continue
        cold = COLD_RE.match(line.upper())
        if cold:
            colds.append(cold.group(1))
            continue
        if line.upper().startswith("REASON:"):
            continue
        return False, None
    if len(colds) != 1:
        return False, None
    return True, (hops, colds[0])


def parse_verdict(answer, allowed=("YES", "NO")):
    """(ok, verdict). Single leading verdict, no contradiction, no symbols."""
    lines = _symbol_lines(answer)
    if not lines or lines[0].upper() not in allowed:
        return False, None
    rest = [line.upper() for line in lines[1:]]
    if any(line in allowed and line != lines[0].upper() for line in rest):
        return False, None
    for line in lines[1:]:
        if line.upper().startswith(("REASON:", "EVIDENCE:")):
            continue
        return False, None
    return True, lines[0].upper()


def parse_abstention(answer, expect_absent=False):
    """(ok, verdict). ABSTAIN (or NO for verified-absent), no symbols."""
    lines = _symbol_lines(answer)
    if not lines:
        return False, None
    head = lines[0].upper()
    if expect_absent:
        if head != "NO":
            return False, None
    elif head != "ABSTAIN":
        return False, None
    for line in lines[1:]:
        if line.upper().startswith("REASON:"):
            continue
        return False, None
    return True, head


def score_answer(manifest, answer):
    """Independent strict scoring: 1.0 on exact parse + key match, else 0.0.

    Never raises on agent output: unparseable answers are misses, not
    errors. Unknown task kinds raise -- that is a runner defect, not
    an agent answer.
    """
    key = manifest.key
    if manifest.kind == "callers":
        ok, items = parse_callers(answer)
        if not ok:
            return 0.0
        want = sorted({(c[0] if isinstance(c, (list, tuple)) else c)
                       for c in key.get("sites", [])})
        if not want:
            return 1.0 if items == [] else 0.0
        return 1.0 if items == want else 0.0
    if manifest.kind == "uniqueness":
        ok, parsed = parse_uniqueness(answer)
        if not ok:
            return 0.0
        defs, unique, resolved = parsed
        want_defs = sorted(key.get("definitions", []))
        if defs != want_defs:
            return 0.0
        if key.get("unique"):
            want_resolved = (key.get("resolved") or "")
            return 1.0 if (unique and resolved == want_resolved) else 0.0
        return 1.0 if not unique else 0.0
    if manifest.kind == "chain":
        ok, parsed = parse_chain(answer)
        if not ok:
            return 0.0
        hops, cold = parsed
        want_hops = [(a, b) for a, b in key.get("hops", [])]
        if hops != want_hops:
            return 0.0
        return 1.0 if cold.lower() == key.get("cold", "").lower() else 0.0
    if manifest.kind == "decoy":
        ok, verdict = parse_verdict(answer)
        if not ok:
            return 0.0
        want = "NO" if key.get("verdict") == "no-call" else "YES"
        return 1.0 if verdict == want else 0.0
    if manifest.kind == "abstention":
        ok, _verdict = parse_abstention(
            answer, expect_absent=(key.get("expected") == "verified-absent"))
        return 1.0 if ok else 0.0
    raise ValueError("unknown task kind: " + manifest.kind)


def manifest_digest(manifest):
    import hashlib
    canonical = json.dumps({"id": manifest.id, "kind": manifest.kind,
                            "text": manifest.text, "key": manifest.key,
                            "budget_actions": manifest.budget_actions},
                           sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def repo_sha(source_root):
    try:
        proc = subprocess.run(["git", "-C", str(source_root),
                               "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    sha = (proc.stdout or "").strip()
    return sha if proc.returncode == 0 and sha else "n/a"


class ReadOnlyRepoTools:
    """Filesystem inspection confined to the arm workspace. No writes."""

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()

    def _confine(self, path):
        target = (self.workspace / path).resolve()
        if target != self.workspace and \
                self.workspace not in target.parents:
            raise PermissionError("path escapes workspace: %r" % path)
        return target

    def list_files(self):
        return sorted(p.relative_to(self.workspace).as_posix()
                      for p in sorted(self.workspace.rglob("*"))
                      if p.is_file() and ".tunic" not in p.parts)

    def read_file(self, path):
        target = self._confine(path)
        if not target.is_file():
            raise FileNotFoundError("no such file: %r" % path)
        return target.read_text(encoding="utf-8-sig")

    def search(self, pattern, suffix=".py"):
        import re as _re
        try:
            rx = _re.compile(pattern)
        except _re.error as error:
            raise ValueError("bad pattern: %s" % error)
        hits = []
        for path in sorted(self.workspace.rglob("*" + suffix)):
            if not path.is_file() or ".tunic" in path.parts:
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if rx.search(line):
                    hits.append({"path": path.relative_to(
                        self.workspace).as_posix(), "line": lineno})
                    if len(hits) >= 200:
                        return hits
        return hits

    def register_all(self, router):
        router.register("list_files", lambda: self.list_files())
        router.register("read_file", lambda path: self.read_file(path))
        router.register("search",
                        lambda pattern, suffix=".py": self.search(
                            pattern, suffix))


class TunicReadTools:
    """Tunic's read-only memory tools through the accounting interface."""

    def __init__(self, memory):
        self.memory = memory

    def register_all(self, router):
        memory = self.memory
        router.register("retrieve",
                        lambda task, limit=5, policy="transparent",
                        threshold=0.0, select=False, snapshot=None,
                        container_limit=2: memory.answer(
                            task, limit=limit, policy=policy,
                            threshold=threshold, select=select,
                            snapshot=snapshot,
                            container_limit=container_limit))
        router.register("related",
                        lambda symbol, policy="transparent":
                        memory.related(symbol, policy=policy))
        router.register("impact",
                        lambda symbol, policy="transparent", max_depth=8,
                        max_nodes=100: memory.impact(
                            symbol, policy=policy, max_depth=max_depth,
                            max_nodes=max_nodes))
        router.register("callees",
                        lambda symbol, policy="transparent", max_depth=8,
                        max_nodes=100: memory.callees(
                            symbol, policy=policy, max_depth=max_depth,
                            max_nodes=max_nodes))
        router.register("mentions",
                        lambda symbol: memory.mentions(symbol))
        router.register("claims",
                        lambda symbol: memory.claims(symbol))
        router.register("timeline",
                        lambda symbol, at=None: memory.timeline(
                            symbol, at=at))
        router.register("candidates", lambda: memory.candidates())
        router.register("frontier",
                        lambda status=None, limit=None: memory.frontier_list(
                            status=status)[:limit]
                        if limit is not None else memory.frontier_list(
                            status=status))


def _write_transcript(path, rows):
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def run_cell(manifest, arm, agent_fn, source_root, workdir, runs_dir=None,
             allowed_tools=None, run_id=None, toolsets=("repo",)):
    """Run one task x arm cell with enforced isolation and accounting.

    Toolsets select the trusted handlers: ``"repo"`` (read-only
    workspace inspection) and ``"tunic"`` (a fresh per-arm ingest with
    read-only memory tools). The agent acts only through ``ctx``; the
    answer key never crosses the agent interface. Workspaces and
    transcripts are unique per run id and never overwritten; agent
    exceptions finalize error cells. Returns a CellResult.
    """
    run_id = run_id or uuid.uuid4().hex
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    arm_ws = workdir / ("%s-%s-%s"
                        % (manifest.id.replace(":", "_"), arm,
                           run_id[:8]))
    if arm_ws.exists():
        raise FileExistsError("refusing to delete existing workspace: %s"
                              % arm_ws)
    _copy_workspace(str(source_root), str(arm_ws))
    out_dir = Path(runs_dir) if runs_dir else workdir
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = str(out_dir / ("run-%s.jsonl" % run_id))
    if Path(transcript_path).exists():
        raise FileExistsError("refusing to overwrite transcript: %s"
                              % transcript_path)
    router = ToolRouter()
    ReadOnlyRepoTools(arm_ws).register_all(router) \
        if "repo" in toolsets else None
    memory = None
    setup_s = 0.0
    if "tunic" in toolsets:
        t_setup = time.time()
        db_path = str(workdir / ("db-%s.sqlite" % run_id[:8]))
        memory = Memory(db_path, str(arm_ws))
        memory.ingest()
        TunicReadTools(memory).register_all(router)
        setup_s = time.time() - t_setup
    provenance = {"repo_sha": repo_sha(source_root),
                  "manifest_digest": manifest_digest(manifest),
                  "runner_version": RUNNER_VERSION,
                  "scoring_version": SCORING_VERSION,
                  "arm": arm, "budget_actions": manifest.budget_actions,
                  "allowed_tools": (sorted(allowed_tools)
                                    if allowed_tools is not None else None),
                  "handlers": router.names,
                  "toolsets": sorted(toolsets),
                  "sandbox": "none-workspace-copy-only"}
    t0 = time.time()
    rows = [{"t": 0.0, "event": "start", "run_id": run_id,
             "task_id": manifest.id, "arm": arm,
             "setup_s": setup_s, "provenance": provenance}]
    ctx = AgentContext(manifest.budget_actions, router, allowed_tools, t0)
    try:
        try:
            answer = agent_fn(manifest.text, arm_ws, manifest.budget_actions,
                              ctx)
        finally:
            if memory is not None:
                memory.close()
    except BudgetExhausted as error:
        wall_s = time.time() - t0
        rows.append({"t": wall_s, "event": "error",
                     "category": "budget-exceeded", "detail": str(error),
                     "actions": ctx.actions})
        return _finalize(manifest, arm, run_id, "", ctx.actions, wall_s,
                         0.0, "budget-exceeded", transcript_path,
                         provenance, rows, ctx)
    except (UnknownTool, ToolDenied) as error:
        wall_s = time.time() - t0
        rows.append({"t": wall_s, "event": "error",
                     "category": ("unknown-tool"
                                  if isinstance(error, UnknownTool)
                                  else "tool-denied"),
                     "detail": str(error), "actions": ctx.actions})
        return _finalize(manifest, arm, run_id, "", ctx.actions, wall_s,
                         0.0, "error", transcript_path, provenance, rows,
                         ctx)
    except Exception as error:
        wall_s = time.time() - t0
        rows.append({"t": wall_s, "event": "error",
                     "category": "agent-crash",
                     "detail": "%s: %s" % (type(error).__name__, error),
                     "actions": ctx.actions})
        return _finalize(manifest, arm, run_id, "", ctx.actions, wall_s,
                         0.0, "error", transcript_path, provenance, rows,
                         ctx)
    wall_s = time.time() - t0
    answer = answer if isinstance(answer, str) else str(answer)
    if ctx.actions > manifest.budget_actions:
        rows.append({"t": wall_s, "event": "error",
                     "category": "budget-exceeded",
                     "detail": "caught BudgetExhausted but kept acting",
                     "actions": ctx.actions})
        return _finalize(manifest, arm, run_id, answer, ctx.actions,
                         wall_s, 0.0, "budget-exceeded", transcript_path,
                         provenance, rows, ctx)
    score = score_answer(manifest, answer)
    return _finalize(manifest, arm, run_id, answer, ctx.actions, wall_s,
                     score, "complete", transcript_path, provenance, rows,
                     ctx)


def _finalize(manifest, arm, run_id, answer, actions, wall_s, score,
              status, transcript_path, provenance, rows, ctx):
    for row in ctx.rows:
        rows.append({"t": row["t"], "event": "action", **{
            k: v for k, v in row.items() if k != "t"}})
    rows.append({"t": wall_s, "event": "result", "run_id": run_id,
                 "task_id": manifest.id, "arm": arm, "answer": answer,
                 "actions": actions, "wall_s": wall_s, "score": score,
                 "status": status, "provenance": provenance})
    _write_transcript(transcript_path, rows)
    return CellResult(task_id=manifest.id, arm=arm, run_id=run_id,
                      answer=answer, actions=actions, wall_s=wall_s,
                      score=score, status=status,
                      transcript_path=transcript_path,
                      provenance=provenance)


def grep_stub_agent(task_text, workspace, budget, ctx):
    """Deterministic stub: real dispatched search, then abstains."""
    ctx.action("search", pattern=r"def \w+")
    ctx.action("list_files")
    return "ABSTAIN\nREASON: insufficient evidence in stub scan"


def abstain_stub_agent(task_text, workspace, budget, ctx):
    """Deterministic stub: one inspection, then abstains."""
    ctx.action("list_files")
    return "ABSTAIN\nREASON: stub agent abstains on every task"


def isolation_probe(agent_fn, source_root, workdir):
    """Prove per-cell isolation: a marker written by one cell's agent
    must not exist in a later cell's workspace. Reports run ids so
    transcript uniqueness is auditable."""
    manifest = TaskManifest(id="probe:isolation", kind="abstention",
                            text="Which functions call nope.py::Missing?",
                            key={"expected": "abstain"})

    def writing_agent(task_text, workspace, budget, ctx):
        (Path(workspace) / "MARKER").write_text("leak", encoding="utf-8")
        return agent_fn(task_text, workspace, budget, ctx)

    first = run_cell(manifest, "first", writing_agent, source_root,
                     workdir, runs_dir=workdir)
    ws_first = list(Path(workdir).glob("probe_isolation-first-*"))
    assert ws_first and (ws_first[0] / "MARKER").exists()
    second = run_cell(manifest, "second", agent_fn, source_root,
                      workdir, runs_dir=workdir)
    ws_second = list(Path(workdir).glob("probe_isolation-second-*"))
    leaked = any((ws / "MARKER").exists() for ws in ws_second)
    assert first.run_id != second.run_id
    assert first.transcript_path != second.transcript_path
    return {"first": first, "second": second, "leaked": leaked}
