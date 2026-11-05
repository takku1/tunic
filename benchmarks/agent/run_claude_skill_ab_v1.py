"""Does Tunic help Claude, used the way it is really used?

Real Claude Code in print mode, multi-turn, with tools. The baseline is
what Claude does without Tunic, not a weaker Tunic surface:

    search   Grep, Glob, Read on the code. No Tunic.
    retrieve the same, plus Tunic's read tools without `context`
    context  the same, plus Tunic's read tools with `context`

Every arm keeps Grep, Glob, and Read, because a real session has them:
Tunic is an addition, so the question is what it adds. The retrieve arm
cannot see `context`, and its skill text has that step removed. An
earlier version denied the tool but left it listed and took Read away,
which handicapped that arm into a meaningless 0.25.

Gold comes from calls the interpreter made (integrity/trace_calls.py), so
it is independent of Tunic and of grep. Each run sees only a copy of the
package in a scratch directory: the trace, tests, and benchmarks are out
of reach, user settings are not loaded, and tools that write are blocked.

    python benchmarks/agent/run_claude_skill_ab_v1.py --model sonnet \\
        --trace benchmarks/integrity/runs/tunic-runtime-calls.json \\
        --tasks 8 --budget 0.75 --out benchmarks/agent/runs/claude-skill-pilot.json
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks/agent"))

from run_pack_ab_v1 import runtime_tasks, score, stable_order  # noqa: E402
from tunic.memory import Memory  # noqa: E402

ARMS = ("search", "retrieve", "context")
READ_TOOLS = ("retrieve", "related", "impact", "callees", "references", "coverage", "context")
WRITE_TOOLS = ("note", "acquire", "correct", "promote", "refresh", "frontier", "review",
               "candidates", "timeline")
ANSWER = (
    "Answer the question about the code in this directory. Symbols are written "
    "relative to the package root as path::name or path::Class.method, for "
    "example store.py::GraphStore.view. Finish with one JSON object and nothing "
    'after it: {"verified": [...], "possible": [...]}. Put a symbol in verified '
    "only if you confirmed it, and in possible if you suspect it.\n\nQUESTION: ")


def command(arm, model, budget, config, skill):
    base = ["claude", "-p", "--model", model, "--output-format", "json",
            "--no-session-persistence", "--setting-sources", "",
            "--strict-mcp-config", "--max-budget-usd", str(budget),
            "--tools", "Grep,Glob,Read"]
    if arm == "search":
        return base + ["--allowedTools", "Grep,Glob,Read"]
    tools = [t for t in READ_TOOLS if arm == "context" or t != "context"]
    allowed = "Grep,Glob,Read," + ",".join("mcp__tunic__" + t for t in tools)
    hidden = list(WRITE_TOOLS) + ([] if arm == "context" else ["context"])
    return base + ["--mcp-config", config, "--allowedTools", allowed,
                   "--disallowedTools", ",".join("mcp__tunic__" + t for t in hidden),
                   "--append-system-prompt-file", skill]


def answer_lists(text):
    """The last JSON object in the reply, as (verified, possible)."""
    for match in reversed(list(re.finditer(r"\{[^{}]*\}", text or "", re.S))):
        try:
            value = json.loads(match.group(0))
        except ValueError:
            continue
        if "verified" in value or "possible" in value:
            strings = lambda items: [x for x in items or [] if isinstance(x, str)]
            return strings(value.get("verified")), strings(value.get("possible")), True
    return [], [], False


def normalize(symbols):
    """A package-relative answer may carry a leading ./ or tunic/."""
    return [re.sub(r"^(\./|tunic/)", "", s.strip()) for s in symbols]


def transitive_tasks(memory, trace_path, count):
    """Impact questions: everything that reaches X through runtime calls.

    Gold is reachability over the trace, so it stays independent of the
    graph. Direct callers are grep's best case; this is the question a
    graph exists for, and it needs several hops to answer by search.
    """
    trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    snapshot, view = memory.store.view()
    index = memory._pack_index(snapshot, view)
    callers = {}
    for caller, callee in trace["pairs"]:
        if caller in index.symbols and callee in index.symbols and caller != callee:
            callers.setdefault(callee, set()).add(caller)
    tasks = []
    for key in stable_order(callers):
        leaf = key.split("::")[-1].split(".")[-1]
        seen, queue = set(), [key]
        while queue:
            for caller in callers.get(queue.pop(), ()):
                if caller not in seen and caller != key:
                    seen.add(caller)
                    queue.append(caller)
        direct = callers[key]
        if index.unique.get(leaf) != key or not 6 <= len(seen) <= 30 or len(seen) < 2 * len(direct):
            continue
        tasks.append({"id": "tunic-impact-" + leaf, "kind": "transitive", "forbidden": [],
                      "gold": sorted(seen),
                      "question": "If `%s` changes, which functions are affected, directly "
                                  "or transitively through any chain of calls? List all of them." % leaf})
        if len(tasks) == count:
            break
    return tasks


def skill_without_context(path, out):
    """The skill as it read before packs: step 0 removed."""
    text = Path(path).read_text(encoding="utf-8")
    start = text.index("0. **Start with `context")
    end = text.index("1. **Retrieve for the audit trail.**")
    out.write_text(text[:start] + text[end:], encoding="utf-8")
    return str(out)


def run_one(arm, task, args, cwd, config, skill):
    started = time.perf_counter()
    try:
        done = subprocess.run(command(arm, args.model, args.budget, config, skill),
                              input=ANSWER + task["question"], capture_output=True,
                              text=True, encoding="utf-8", cwd=cwd, timeout=args.timeout)
        reply = json.loads(done.stdout)
    except (subprocess.TimeoutExpired, ValueError) as error:
        return {"task": task["id"], "kind": task["kind"], "arm": arm, "error": repr(error)[:300],
                "seconds": round(time.perf_counter() - started, 1), "gold": task["gold"],
                **score(task, [], [])}
    verified, possible, valid = answer_lists(reply.get("result"))
    verified, possible = normalize(verified), normalize(possible)
    usage = reply.get("usage") or {}
    row = {"task": task["id"], "kind": task["kind"], "arm": arm, "gold": task["gold"],
           "valid": valid, "verified": verified, "possible": possible,
           "seconds": round(time.perf_counter() - started, 1),
           "turns": reply.get("num_turns"), "cost_usd": reply.get("total_cost_usd"),
           "subtype": reply.get("subtype"), "is_error": reply.get("is_error"),
           "reply_tail": (reply.get("result") or "")[-500:],
           "denied": [d.get("tool_name") for d in reply.get("permission_denials") or []],
           "input_tokens": sum(usage.get(k) or 0 for k in (
               "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
           "output_tokens": usage.get("output_tokens"),
           **score(task, verified, possible)}
    print("%-40s %-9s f1 %.2f turns %s $%.3f %5.1fs" % (
        task["id"][:40], arm, row["f1"], row["turns"], row["cost_usd"] or 0, row["seconds"]), flush=True)
    return row


def summarize(rows):
    out = {}
    for arm in ARMS:
        group = [r for r in rows if r["arm"] == arm]
        if not group:
            continue
        n = len(group)
        mean = lambda key: sum(r.get(key) or 0 for r in group) / n
        out[arm] = {"tasks": n, "complete": sum(r["complete"] for r in group),
                    "mean_f1": round(mean("f1"), 3), "mean_recall": round(mean("recall"), 3),
                    "mean_precision": round(mean("precision"), 3),
                    "false_verified": sum(len(r["false_verified"]) for r in group),
                    "mean_turns": round(mean("turns"), 1), "mean_seconds": round(mean("seconds"), 1),
                    "mean_cost_usd": round(mean("cost_usd"), 4),
                    "denied_calls": sum(len(r.get("denied") or []) for r in group),
                    "mean_input_tokens": round(mean("input_tokens")),
                    "errors": sum(1 for r in group if r.get("error") or r.get("is_error"))}
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--tasks", type=int, default=8, help="tasks, split across callers and callees")
    parser.add_argument("--budget", type=float, default=0.75, help="USD cap per run")
    parser.add_argument("--set", choices=("direct", "transitive"), default="direct")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tunic-claude-ab-") as work:
        work = Path(work)
        source = work / "repo" / "package"
        shutil.copytree(ROOT / "tunic", source, ignore=shutil.ignore_patterns("__pycache__"))
        with Memory(str(work / "graph.sqlite"), source) as memory:
            memory.ingest()
            if args.set == "transitive":
                tasks = transitive_tasks(memory, args.trace, args.tasks)
            else:
                tasks = runtime_tasks(memory, args.trace)
                callers = [t for t in tasks if t["kind"] == "runtime_callers"]
                callees = [t for t in tasks if t["kind"] == "runtime_callees"]
                half = args.tasks // 2
                tasks = callers[:half] + callees[:args.tasks - half]
        skill = str(ROOT / "skills/tunic/SKILL.md")
        skills = {"search": skill, "context": skill,
                  "retrieve": skill_without_context(skill, work / "skill-retrieve.md")}
        configs = {}
        for arm in ARMS:  # one database per arm: runs may record frontier rows
            db = work / ("%s.sqlite" % arm)
            shutil.copy(work / "graph.sqlite", db)
            config = work / ("%s.json" % arm)
            config.write_text(json.dumps({"mcpServers": {"tunic": {
                "command": sys.executable, "args": ["-m", "tunic", "--db", str(db), "mcp", "--root", str(source)],
                "env": {"PYTHONPATH": str(ROOT)}}}}), encoding="utf-8")
            configs[arm] = str(config)

        def run_arm(arm):
            return [run_one(arm, task, args, source, configs[arm], skills[arm]) for task in tasks]

        with ThreadPoolExecutor(len(ARMS)) as pool:
            rows = [row for group in pool.map(run_arm, ARMS) for row in group]
    result = {"benchmark": "claude-skill-ab-v1", "model": args.model, "arms": ARMS,
              "budget_per_run_usd": args.budget, "summary": summarize(rows), "rows": rows}
    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(result["summary"], indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
