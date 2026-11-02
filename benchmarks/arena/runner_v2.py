"""Arena v2 runner infrastructure: manifests, isolation, transcripts, scoring.

Model-agnostic and model-free. The runner never calls any model: it
takes an ``agent_fn`` callable, runs it against an isolated workspace
copy with a bounded action budget, captures a JSONL transcript, and
scores the normalized answer against the task key independently.

Real-agent comparisons plug a tool-using agent in as ``agent_fn``
later; the two stub agents below prove the pipeline end to end
without inference. Budgets, timing, and action accounting are
recorded per cell so a future model arm cannot silently exceed them.
"""
import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from tunic.investigate import _copy_workspace
from tunic.memory import Memory


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
    answer: str
    actions: int
    wall_s: float
    score: float
    over_budget: bool
    transcript_path: str


def normalize_answer(text):
    """Canonical answer form: lowercase lines, deduplicated, sorted."""
    lines = sorted({line.strip().lower() for line in str(text).splitlines()
                    if line.strip()})
    return "\n".join(lines)


def score_answer(manifest, answer):
    """Independent scoring: normalized answer vs frozen key, 1.0 or 0.0.

    Partial credit is deliberately absent: arena answers are sets of
    exact keys or fixed verdicts, and anything else is a miss the
    operator must inspect, not a fraction the runner invents.
    """
    key = manifest.key
    norm = normalize_answer(answer)
    if manifest.kind == "callers":
        want = sorted({(c[0] if isinstance(c, (list, tuple)) else c).lower()
                       for c in key.get("sites", [])})
        got = [line for line in norm.splitlines() if line]
        if "abstain" in got and want:
            return 0.0
        return 1.0 if sorted(got) == want else 0.0
    if manifest.kind == "uniqueness":
        want_defs = sorted(d.lower() for d in key.get("definitions", []))
        defs_ok = all(d in norm for d in want_defs)
        if key.get("unique"):
            resolved = (key.get("resolved") or "").lower()
            return 1.0 if (defs_ok and resolved and resolved in norm) else 0.0
        return 1.0 if (defs_ok and "not-determined" in norm) else 0.0
    if manifest.kind == "chain":
        hops = ["%s -> %s" % (a.lower(), b.lower())
                for a, b in key.get("hops", [])]
        cold = key.get("cold", "").lower()
        hops_ok = all(h in norm for h in hops)
        return 1.0 if (hops_ok and cold and cold in norm) else 0.0
    if manifest.kind == "decoy":
        verdict = key.get("verdict", "")
        if verdict == "no-call":
            return 1.0 if norm.startswith("no") else 0.0
        return 1.0 if norm.startswith("yes") else 0.0
    if manifest.kind == "abstention":
        if key.get("expected") == "verified-absent":
            return 1.0 if norm.startswith("no") else 0.0
        return 1.0 if "abstain" in norm else 0.0
    raise ValueError("unknown task kind: " + manifest.kind)


def run_cell(manifest, arm, agent_fn, source_root, workdir,
             runs_dir=None):
    """Run one task x arm cell with isolation and transcript capture.

    ``agent_fn(task_text, workspace, budget, log)`` returns the answer
    string; ``log(event, **fields)`` appends transcript rows, and rows
    with ``event="action"`` count against the budget. ``source_root``
    is copied to an isolated workspace; the agent's writes stay there.
    Returns a CellResult; the transcript JSONL lands under runs_dir
    when given (otherwise beside the workspace).
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    arm_ws = workdir / ("%s-%s" % (manifest.id.replace(":", "_"), arm))
    if arm_ws.exists():
        import shutil
        shutil.rmtree(arm_ws)
    _copy_workspace(str(source_root), str(arm_ws))
    transcript = []
    t0 = time.time()

    def log(event, **fields):
        record = {"t": time.time() - t0, "event": event}
        record.update(fields)
        transcript.append(record)

    answer = agent_fn(manifest.text, arm_ws, manifest.budget_actions, log)
    wall_s = time.time() - t0
    actions = sum(1 for row in transcript if row.get("event") == "action")
    over_budget = actions > manifest.budget_actions
    score = score_answer(manifest, answer)
    out_dir = Path(runs_dir) if runs_dir else workdir
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = str(out_dir / ("%s-%s.jsonl"
                                     % (manifest.id.replace(":", "_"), arm)))
    with open(transcript_path, "w", encoding="utf-8") as handle:
        for row in transcript:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.write(json.dumps({
            "t": wall_s, "event": "result",
            "task_id": manifest.id, "arm": arm,
            "answer": answer, "actions": actions,
            "wall_s": wall_s, "score": score,
            "over_budget": over_budget}) + "\n")
    return CellResult(task_id=manifest.id, arm=arm, answer=answer,
                      actions=actions, wall_s=wall_s, score=score,
                      over_budget=over_budget,
                      transcript_path=transcript_path)


def grep_stub_agent(task_text, workspace, budget, log):
    """Deterministic stub: answers caller questions by source search.

    A pipeline stand-in, not a model. Counts one action per file
    scanned so budgets and accounting are exercised honestly.
    """
    import re
    hits = []
    for path in sorted(Path(workspace).glob("*.py")):
        log("action", tool="search", path=path.name)
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"def (\w+)", text):
            hits.append("%s::%s" % (path.name, match.group(1)))
        if len([r for r in hits]) > budget * 10:
            break
    log("action", tool="answer", key="abstain-stub")
    return "ABSTAIN\ninsufficient evidence in stub scan"


def abstain_stub_agent(task_text, workspace, budget, log):
    """Deterministic stub: always abstains with the reason."""
    log("action", tool="answer", key="abstain")
    return "ABSTAIN\nstub agent abstains on every task"


def isolation_probe(agent_fn, source_root, workdir):
    """Prove per-cell isolation: a marker written by one cell's agent
    must not exist in a later cell's workspace."""
    marker = workdir / "probe"
    manifest = TaskManifest(id="probe:isolation", kind="abstention",
                            text="Which functions call nope.py::Missing?",
                            key={"expected": "abstain"})

    def writing_agent(task_text, workspace, budget, log):
        (Path(workspace) / "MARKER").write_text("leak", encoding="utf-8")
        log("action", tool="write", path="MARKER")
        return agent_fn(task_text, workspace, budget, log)

    first = run_cell(manifest, "first", writing_agent, source_root,
                     workdir, runs_dir=workdir)
    assert (Path(workdir) / "probe_isolation-first" / "MARKER").exists()
    second = run_cell(manifest, "second", agent_fn, source_root,
                      workdir, runs_dir=workdir)
    leaked = (Path(workdir) / "probe_isolation-second" / "MARKER").exists()
    return {"first": first, "second": second, "leaked": leaked,
            "marker": str(marker)}
