"""Exercise the public common-memory corpus through actual Tunic MCP.

This is retrieval instrumentation only. The current agent can inspect the
saved context; no model is invoked and no gold answer is copied into a run.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from benchmarks.agent.direct_mcp import run  # noqa: E402

try:
    from .release import ROOT, load, verify
except ImportError:
    from release import ROOT, load, verify


def exercise(root=ROOT):
    root = Path(root)
    digest = verify(root)
    manifest = load(root / "manifest.json")
    tasks = load(root / manifest["tasks"])
    work = REPO / ".tunic" / "competitive" / uuid.uuid4().hex
    work.mkdir(parents=True)
    rows, batches = [], []
    for cutoff in sorted({task["after_event"] for task in tasks}):
        group = [task for task in tasks if task["after_event"] == cutoff]
        workspace = work / ("event-%02d" % cutoff)
        workspace.mkdir()
        for relative in manifest["events"][:cutoff]:
            dest = workspace / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, dest)
        params = manifest["parameters"]
        calls = [{"name": "context", "arguments": {
            "task": task["question"], "limit": params["retrieval_limit"],
            "max_bytes": params["context_bytes"]}} for task in group]
        report = run(workspace, work / ("event-%02d.sqlite" % cutoff), calls,
                     refresh=True, timeout=params["timeout_seconds"])
        receipt_path = work / ("event-%02d.json" % cutoff)
        receipt_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        batches.append({"after_event": cutoff, "tasks": len(group),
                        "elapsed_seconds": report["elapsed_seconds"],
                        "receipts": str(receipt_path)})
        # Initialize, list, refresh precede query receipts. There is no
        # answer key in the workspace, query, tool input, or row construction.
        for task, receipt in zip(group, report["receipts"][3:]):
            response = receipt["response"]
            payload = receipt.get("payload", {})
            error = "error" in response or response.get("result", {}).get("isError")
            sources = {doc["at"].rsplit("@", 1)[0] for doc in payload.get("docs", [])}
            sources.update(node["id"].split("::", 1)[0]
                           for node in payload.get("nodes", [])
                           if node.get("kind") == "document" and node.get("src"))
            rows.append({"id": task["id"], "status": "error" if error else "complete",
                         "evidence": sorted(sources), "measurements": {},
                         "wire_bytes": receipt["response_bytes"],
                         "omitted": payload.get("omitted"),
                         "gaps": payload.get("gaps", []),
                         "snapshot": payload.get("snapshot")})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                            text=True, capture_output=True, check=True).stdout.strip()
    implementation = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted((REPO / "tunic").glob("*.py"))}
    return {"benchmark_digest": digest, "arm": "tunic", "track": "retrieval",
            "cohort": {"python": platform.python_version(), "platform": platform.platform(),
                       "agent_model": "none; retrieval instrumentation only",
                       "commit": commit, "implementation": implementation,
                       "transport_sha256": hashlib.sha256(
                           (REPO / "benchmarks/agent/direct_mcp.py").read_bytes()).hexdigest()},
            "rows": rows, "batches": batches,
            "limitations": ["Batch times include startup and refresh; not per-query latency.",
                            "No token, cost, or resident-memory measurement is fabricated.",
                            "Independent prefixes test visibility, not incremental update performance."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError("refusing to overwrite benchmark run: " + str(args.out))
    result = exercise(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": str(args.out), "tasks": len(result["rows"]),
                      "batch_seconds": [batch["elapsed_seconds"] for batch in result["batches"]]}))


if __name__ == "__main__":
    main()
