"""Arena v2 runner: enforced budgets, strict scoring, immutable evidence.

Model-agnostic and model-free. The runner never calls any model: it
takes an ``agent_fn(task_text, workspace, budget, ctx)`` callable,
runs it against an isolated workspace copy, and scores the
machine-parsed answer against the frozen key.

Integrity rules (v0.2.0):

- Budgets are enforced by the trusted ``AgentContext``: every tool
  call goes through ``ctx.action``, which counts and raises
  ``BudgetExhausted`` past the limit. Agents cannot self-report.
- Scoring parses typed answers (exact symbol sets, ordered hops,
  single verdicts). Substring hits, contradictions, and unexpected
  extra relationships score 0. Raw answers stay verbatim in the
  transcript.
- Every run gets a unique run id and an immutable transcript path;
  nothing is overwritten and no pre-existing workspace is deleted.
- Agent exceptions finalize the transcript as an error cell (category,
  action count, elapsed time, incomplete status) -- failure is never
  converted into abstention or a score.
- The provenance record (repo SHA, manifest digest, runner/scoring
  versions, arm config, allowed tools, sandbox disclaimer) travels
  with every result.
- The agent interface carries task text only, never answer keys.
  Workspace copying is isolation, not a security sandbox: a real
  agent runs with the operator's filesystem trust, recorded as such.

Interface change from v0.1.0: ``agent_fn`` receives ``ctx`` instead
of a passive ``log`` callback, and ``run_cell`` returns ``status``
instead of ``over_budget``.
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

RUNNER_VERSION = "0.2.0"
SCORING_VERSION = "v3-strict"

SYMBOL_RE = re.compile(r"^[A-Za-z0-9_./-]+\.py::[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$")
HOP_RE = re.compile(r"^(\S+) -> (\S+)$")
COLD_RE = re.compile(r"^COLD: (\S+)$")
VERDICT_RE = re.compile(r"^VERDICT: (UNIQUE \S+|NOT-DETERMINED)$")


class BudgetExhausted(Exception):
    """Raised by the trusted context when the agent exceeds its budget."""


class UnknownTool(Exception):
    """Raised when the agent calls a tool outside its permission set."""


class AgentContext:
    """Trusted action counter. The only tool-call path the runner honors."""

    def __init__(self, budget, allowed_tools=None):
        self.budget = budget
        self.allowed_tools = (set(allowed_tools)
                              if allowed_tools is not None else None)
        self.actions = 0
        self.rows = []

    def action(self, tool, **args):
        if self.allowed_tools is not None and tool not in self.allowed_tools:
            raise UnknownTool("tool not permitted for this arm: %r" % tool)
        self.actions += 1
        if self.actions > self.budget:
            raise BudgetExhausted(
                "action %d exceeds budget %d" % (self.actions, self.budget))
        self.rows.append({"tool": tool, "args": args})
        return self.actions

    def note(self, **fields):
        self.rows.append(dict(fields))


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
                  if line.upper() not in verdicts and
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


def _write_transcript(path, rows):
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def run_cell(manifest, arm, agent_fn, source_root, workdir, runs_dir=None,
             allowed_tools=None, run_id=None):
    """Run one task x arm cell with enforced isolation and accounting.

    The agent acts only through ``ctx.action`` (budget-enforced) and
    ``ctx.note``. Workspaces and transcripts are unique per run id and
    never overwritten; a colliding workspace aborts instead of
    deleting. Agent exceptions finalize an error cell. Returns a
    CellResult; the answer key never crosses the agent interface.
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
    provenance = {"repo_sha": repo_sha(source_root),
                  "manifest_digest": manifest_digest(manifest),
                  "runner_version": RUNNER_VERSION,
                  "scoring_version": SCORING_VERSION,
                  "arm": arm, "budget_actions": manifest.budget_actions,
                  "allowed_tools": (sorted(allowed_tools)
                                    if allowed_tools is not None else None),
                  "sandbox": "none-workspace-copy-only"}
    rows = [{"t": 0.0, "event": "start", "run_id": run_id,
             "task_id": manifest.id, "arm": arm,
             "provenance": provenance}]
    ctx = AgentContext(manifest.budget_actions, allowed_tools)
    t0 = time.time()
    try:
        answer = agent_fn(manifest.text, arm_ws, manifest.budget_actions,
                          ctx)
    except BudgetExhausted as error:
        wall_s = time.time() - t0
        rows.append({"t": wall_s, "event": "error",
                     "category": "budget-exceeded", "detail": str(error),
                     "actions": ctx.actions})
        return _finalize(manifest, arm, run_id, "", ctx.actions, wall_s,
                         0.0, "budget-exceeded", transcript_path,
                         provenance, rows)
    except UnknownTool as error:
        wall_s = time.time() - t0
        rows.append({"t": wall_s, "event": "error",
                     "category": "unknown-tool", "detail": str(error),
                     "actions": ctx.actions})
        return _finalize(manifest, arm, run_id, "", ctx.actions, wall_s,
                         0.0, "error", transcript_path, provenance, rows)
    except Exception as error:
        wall_s = time.time() - t0
        rows.append({"t": wall_s, "event": "error",
                     "category": "agent-crash",
                     "detail": "%s: %s" % (type(error).__name__, error),
                     "actions": ctx.actions})
        return _finalize(manifest, arm, run_id, "", ctx.actions, wall_s,
                         0.0, "error", transcript_path, provenance, rows)
    wall_s = time.time() - t0
    answer = answer if isinstance(answer, str) else str(answer)
    score = score_answer(manifest, answer)
    return _finalize(manifest, arm, run_id, answer, ctx.actions, wall_s,
                     score, "complete", transcript_path, provenance, rows,
                     ctx_rows=ctx.rows, t0=t0)


def _finalize(manifest, arm, run_id, answer, actions, wall_s, score,
              status, transcript_path, provenance, rows, ctx_rows=None,
              t0=None):
    base = t0 if t0 is not None else time.time()
    for row in (ctx_rows or []):
        rows.append({"t": time.time() - base, "event": "action", **row})
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
    """Deterministic stub: scans sources, then abstains with a reason."""
    import re
    for path in sorted(Path(workspace).glob("*.py")):
        ctx.action("search", path=path.name)
        text = path.read_text(encoding="utf-8")
        if re.search(r"def \w+", text):
            ctx.note(hit=path.name)
    ctx.action("answer", key="abstain-stub")
    return "ABSTAIN\nREASON: insufficient evidence in stub scan"


def abstain_stub_agent(task_text, workspace, budget, ctx):
    """Deterministic stub: always abstains with the reason."""
    ctx.action("answer", key="abstain")
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
    assert (Path(first.transcript_path).parent /
            first.transcript_path).exists()
    ws_first = list(Path(workdir).glob("probe_isolation-first-*"))
    assert ws_first and (ws_first[0] / "MARKER").exists()
    second = run_cell(manifest, "second", agent_fn, source_root,
                      workdir, runs_dir=workdir)
    ws_second = list(Path(workdir).glob("probe_isolation-second-*"))
    leaked = any((ws / "MARKER").exists() for ws in ws_second)
    assert first.run_id != second.run_id
    assert first.transcript_path != second.transcript_path
    return {"first": first, "second": second, "leaked": leaked}
