"""Tool-using agent benchmark adapter v1.

Replays recorded agent-tool transcripts against a fresh Tunic session
through the exact MCP dispatch (mcp.handle_call) -- the same interface
any agent uses. No model, no hosted inference, fully deterministic.

Transcript schema:
  {"benchmark": str, "task": str,
   "fixture": {"builder": "acqgen2", "suite": "dev",
               "scenario": str, "rep": int},
   "gold": {"symbols": [...] } | {"abstain": true},
   "calls": [{"tool": str, "arguments": {...}}],
   "answer": {"symbols": [...] } | {"abstain": true}}

A call argument may reference an earlier call's output:
  {"candidate_id": {"$ref": 1, "via": "constructor_call"}}
resolves to the first candidate with that via from call 1's result.

Scoring (all deterministic):
  correctness: gold symbols ⊆ answer symbols (or expected abstention
    with no committed acquisition);
  grounding: every answer symbol appeared in some recorded tool output;
  governance: every committed acquisition carries a receipt, and every
    refused call is recorded (never a crash).
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from tunic.mcp import handle_call  # noqa: E402

SYMBOL_RE = re.compile(r"[A-Za-z0-9_./-]+\.py::[A-Za-z0-9_.]+")


def _walk_strings(node, into):
    if isinstance(node, str):
        into.update(SYMBOL_RE.findall(node))
    elif isinstance(node, dict):
        for value in node.values():
            _walk_strings(value, into)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _walk_strings(value, into)
    return into


def _resolve(value, results):
    if isinstance(value, dict) and "$ref" in value:
        index = value["$ref"]
        if not isinstance(index, int) or index >= len(results):
            raise ValueError("bad $ref %r" % (value,))
        output = results[index]
        if output is None:
            raise ValueError("$ref %d points at a refused call" % index)
        cands = [c for c in output.get("candidates", [])
                 if c.get("via") == value.get("via")]
        if not cands:
            raise ValueError("$ref %d has no %s candidate" % (index, value.get("via")))
        return cands[0]["id"]
    if isinstance(value, dict):
        return {key: _resolve(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, results) for item in value]
    return value


def build_fixture(tmp, fixture):
    """Fresh database + ingested fixture. Only the seeded dev builder
    is frozen enough for scored runs; anything else is illustrative."""
    from acqgen2 import build
    from tunic.memory import Memory
    if fixture.get("builder") != "acqgen2":
        raise ValueError("unsupported fixture builder: %r" % (fixture.get("builder"),))
    if fixture.get("suite") != "dev":
        raise ValueError("only the dev suite may back scored runs")
    spec = build(fixture["scenario"], fixture["rep"], "dev")
    root = Path(tmp) / "repo"
    root.mkdir(parents=True)
    for path, text in spec["files"].items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "sessions.sqlite"), root)
    memory.ingest()
    return memory, spec


def replay(memory, transcript):
    """Run every transcript call through MCP dispatch. Returns the
    per-call log; refused calls record the error, never raise."""
    log = []
    results = []
    for position, call in enumerate(transcript["calls"]):
        try:
            arguments = _resolve(call["arguments"], results)
            output = handle_call(memory, call["tool"], arguments)
            entry = {"call": position, "tool": call["tool"],
                     "refused": False, "output": output}
            results.append(output)
        except (ValueError, KeyError, TypeError, PermissionError,
                OSError) as error:
            entry = {"call": position, "tool": call["tool"],
                     "refused": True, "error": str(error)}
            results.append(None)
        log.append(entry)
    return log


def score(transcript, log):
    """Rule-based scorecard over a replay log."""
    gold = transcript["gold"]
    answer = transcript["answer"]
    retrieved = set()
    for entry in log:
        if not entry["refused"]:
            _walk_strings(entry["output"], retrieved)
    committed = [e for e in log
                 if e["tool"] == "acquire" and not e["refused"]
                 and (e["output"] or {}).get("committed")]
    governed = all((e["output"] or {}).get("justification")
                   and (e["output"] or {}).get("candidate_id")
                   for e in committed)
    if answer.get("abstain"):
        grounded = True
        correct = bool(gold.get("abstain")) and not committed
    else:
        symbols = answer.get("symbols", [])
        grounded = set(symbols) <= retrieved
        correct = set(gold.get("symbols", [])) <= set(symbols) and grounded
    refused_clean = all("error" in e for e in log if e["refused"])
    overall = correct and governed and refused_clean
    return {"correct": correct, "grounded": grounded, "governed": governed,
            "refusals_clean": refused_clean, "pass": overall,
            "retrieved_count": len(retrieved), "commits": len(committed)}


def run_transcript(path, tmp):
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    memory, _spec = build_fixture(str(tmp), record["fixture"])
    try:
        log = replay(memory, record)
        result = score(record, log)
    finally:
        memory.close()
    return {"transcript": Path(path).name, "task": record["task"],
            "score": result, "log": log}


if __name__ == "__main__":
    import tempfile
    for name in sys.argv[1:]:
        with tempfile.TemporaryDirectory() as tmp:
            print(json.dumps(run_transcript(name, tmp), indent=1,
                             sort_keys=True, default=str))
