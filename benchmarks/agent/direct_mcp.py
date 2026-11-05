"""Let the current agent use Tunic over real stdio, without another model.

python benchmarks/agent/direct_mcp.py --root tunic --db .tunic/direct.sqlite \
    --tool context --arguments '{"task":"what calls bound?"}'

Use --refresh on the first call or after edits. --out saves the full receipt;
stdout contains a compact summary when --out is supplied. Timings include JSON serialization and IPC,
not agent reasoning. No inference backend or embedding service is configured.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]


def run(root, db, calls, *, refresh=False, timeout=120):
    """Run a batch in one server session and return auditable wire receipts."""
    messages = [{"method": "initialize"}, {"method": "tools/list"}]
    if refresh:
        messages.append({"method": "tools/call", "params": {
            "name": "refresh", "arguments": {}}})
    messages.extend({"method": "tools/call", "params": call} for call in calls)
    requests = [dict(message, jsonrpc="2.0", id=i)
                for i, message in enumerate(messages, 1)]
    # communicate drains both pipes and enforces a timeout, including ingestion.
    started = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tunic", "--db", str(Path(db).resolve()),
         "mcp", "--root", str(Path(root).resolve())],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        stdout, stderr = proc.communicate(
            "".join(json.dumps(request) + "\n" for request in requests),
            timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise TimeoutError("Tunic MCP batch exceeded %ss" % timeout) from None
    elapsed = time.perf_counter() - started
    if proc.returncode:
        raise RuntimeError("Tunic server exited %s: %s" % (proc.returncode, stderr))
    lines = stdout.splitlines()
    responses = [json.loads(line) for line in lines]
    if [response.get("id") for response in responses] != list(range(1, len(requests) + 1)):
        raise RuntimeError("missing, reordered, or unexpected JSON-RPC responses")
    receipts = []
    for request, response, line in zip(requests, responses, lines):
        result = response.get("result", {})
        receipt = {"request": request, "response_bytes": len(line.encode("utf-8")),
                   "response": response}
        if "content" in result:
            receipt["payload"] = json.loads(result["content"][0]["text"])
        receipts.append(receipt)
    return {"schema": "tunic.direct-mcp.v1", "root": str(Path(root).resolve()),
            "backend": "none (MCP transport only)", "elapsed_seconds": elapsed,
            "timing_scope": "whole batch including server startup and optional refresh",
            "ok": all("error" not in r and not r.get("result", {}).get("isError")
                      for r in responses), "receipts": receipts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--tool", default="context")
    parser.add_argument("--arguments", default="{}", help="JSON tool arguments")
    parser.add_argument("--calls", type=Path, help="JSON list of {name, arguments}")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    calls = (json.loads(args.calls.read_text(encoding="utf-8")) if args.calls else
             [{"name": args.tool, "arguments": json.loads(args.arguments)}])
    report = run(args.root, args.db, calls, refresh=args.refresh, timeout=args.timeout)
    rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    if args.out:
        print(json.dumps({"ok": report["ok"], "out": str(args.out),
                          "elapsed_seconds": report["elapsed_seconds"],
                          "calls": [{"tool": r["request"].get("params", {}).get("name"),
                                     "response_bytes": r["response_bytes"],
                                     "nodes": [n["id"] for n in r.get("payload", {}).get("nodes", [])],
                                     "gaps": r.get("payload", {}).get("gaps", [])}
                                    for r in report["receipts"]]}, indent=2))
    else:
        print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
