"""Local single-principal CLI; never imports or executes analyzed source."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

from .compiler import compile_claims, compile_drafts, compile_impact, compile_mentions, compile_related, compile_review, compile_timeline, validate_artifact
from .store import GraphStore
from .render import render_context
from .ollama import Ollama
from .continuation import compare_continuation
from .benchmark import run_benchmark, run_memory_suite
from .eval import run_agent_suite


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tunic")
    parser.add_argument("--db", help="SQLite database path (required for graph/render/experiment commands)")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="fully rebuild and atomically publish a Python source snapshot")
    ingest.add_argument("root", help="Python source root; e.g. repository/src for a src layout")
    refresh = commands.add_parser("refresh", help="incrementally re-ingest; equivalent to ingest with less work")
    refresh.add_argument("root", help="Python source root; e.g. repository/src for a src layout")
    impact = commands.add_parser("impact", help="compile possible reverse-call impact with evidence")
    impact.add_argument("symbol", help="exact path::function key, e.g. core.py::calculate")
    impact.add_argument("--policy", default="transparent", help="retrieval policy: transparent, evidence_only, or current")
    mentions = commands.add_parser("mentions", help="list document spans that mention a symbol key")
    mentions.add_argument("symbol", help="exact path::function key, e.g. core.py::calculate")
    claims = commands.add_parser("claims", help="list typed document claims and proposals touching a symbol key")
    claims.add_argument("symbol", help="exact path::function key, e.g. core.py::calculate")
    related = commands.add_parser("related", help="combine impact, mentions, and claims for a symbol key")
    related.add_argument("symbol", help="exact path::function key, e.g. core.py::calculate")
    related.add_argument("--policy", default="transparent", help="retrieval policy: transparent, evidence_only, or current")
    timeline = commands.add_parser("timeline", help="valid-time evidence view for a symbol key")
    timeline.add_argument("symbol", help="exact path::function key, e.g. core.py::calculate")
    timeline.add_argument("--at", default=None, help="ISO YYYY-MM-DD cutoff; defaults to the snapshot date")
    note = commands.add_parser("note", help="append a timestamped session note to a document")
    note.add_argument("path", help="document path to create or append, e.g. notes/session.md")
    note.add_argument("--text", required=True, help="note text to append")
    candidates = commands.add_parser("candidates", help="list proposed-but-unverified relationship candidates")
    candidates.add_argument("--root", default=".", help="workspace root the database was ingested from")
    frontier = commands.add_parser("frontier", help="list frontier ledger entries (unresolved observations)")
    frontier.add_argument("--root", default=".", help="workspace root the database was ingested from")
    frontier.add_argument("--status", default=None, choices=["open", "investigating", "resolved", "dismissed"],
                          help="filter by entry status; default lists all")
    frontier.add_argument("--replay", default=None, metavar="ENTRY_ID",
                          help="re-check one entry against current graph state")
    frontier.add_argument("--export-regression", default=None, metavar="ENTRY_ID",
                          help="export a resolved entry as a durable regression case")
    frontier.add_argument("--out", default=None, help="output path for --export-regression")
    frontier.add_argument("--record-miss", default=None, metavar="SYMBOL",
                          help="file an audited retrieval miss for a verified symbol")
    frontier.add_argument("--question", default=None, help="question the system answered wrong")
    frontier.add_argument("--authority", default=None, help="audit authority filing the miss")
    draft = commands.add_parser("draft", help="propose claim blocks for co-mentioned entities (review input only)")
    draft.add_argument("document", help="repository-relative document path, e.g. notes.md")
    review = commands.add_parser("review", help="structured acquisition report for one document (read-only)")
    review.add_argument("document", help="repository-relative document path, e.g. notes/session.md")
    impact.add_argument("--snapshot")
    impact.add_argument("--max-depth", type=int, default=8)
    impact.add_argument("--max-nodes", type=int, default=100)
    validate = commands.add_parser("validate", help="check an artifact against stored evidence")
    validate.add_argument("artifact")
    validate.add_argument("--allow-historical", action="store_true", help="validate old snapshot without requiring current freshness")
    render = commands.add_parser("render", help="faithfully render current IR; byte bound is not a token count")
    render.add_argument("artifact")
    render.add_argument("--max-bytes", type=int, default=65536)
    models = commands.add_parser("models", help="list installed local Ollama model names/digests")
    models.add_argument("--endpoint", default="http://127.0.0.1:11434")
    pin = commands.add_parser("pin", help="pin installed llama3.2/dense qwen3 and verify nontruncating overflow rejection")
    pin.add_argument("model")
    pin.add_argument("--endpoint", default="http://127.0.0.1:11434")
    experiment = commands.add_parser("experiment", help="compare upfront versus boundary-triggered evidence on a pinned local model")
    experiment.add_argument("symbol")
    experiment.add_argument("--task", required=True)
    experiment.add_argument("--profile", required=True)
    experiment.add_argument("--repeats", type=int, default=1)
    experiment.add_argument("--plan-tokens", type=int, default=32)
    experiment.add_argument("--answer-tokens", type=int, default=192)
    experiment.add_argument("--context-tokens", type=int, default=8192)
    experiment.add_argument("--max-input-tokens", type=int, default=6000)
    benchmark = commands.add_parser("benchmark", help="run public W1 labels/baselines, optionally with real pinned inference")
    benchmark.add_argument("--suite", default="benchmarks/w1/suite.json")
    benchmark.add_argument("--top-k", type=int, default=5)
    benchmark.add_argument("--profile", help="optional existing local Ollama profile; absent means no model calls")
    benchmark.add_argument("--model-cases", help="comma-separated case IDs for model track; default all")
    benchmark.add_argument("--model-repeats", type=int, default=1)
    benchmark.add_argument("--answer-tokens", type=int, default=768)
    benchmark.add_argument("--check", action="store_true", help="fail on core regression or infrastructure failure; known gaps remain reported")
    benchmark.add_argument("--memory", action="store_true", help="run the held-out memory recall evaluation instead of the W1 suite")
    benchmark.add_argument("--agent", action="store_true", help="run the held-out agent evaluation (none vs lexical vs tunic) instead of the W1 suite")
    mcp = commands.add_parser("mcp", help="serve the agent-memory boundary over stdio (MCP-compatible JSON-RPC)")
    mcp.add_argument("--root", default=".", help="fixed workspace root for this server session")
    args = parser.parse_args(argv)
    try:
        if args.command == "benchmark":
            if args.agent:
                result = run_agent_suite(args.suite)
                print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
                tunic = result["conditions"]["tunic"]
                return 0 if tunic["passed"] == tunic["total"] else 2
            if args.memory:
                result = run_memory_suite(args.suite, top_k=args.top_k)
                print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
                return 0 if result["outcome"] == "complete" else 2
            profile = json.loads(Path(args.profile).read_text(encoding="utf-8-sig")) if args.profile else None
            if args.model_cases and not profile:
                parser.error("--model-cases requires --profile")
            result = run_benchmark(args.suite, top_k=args.top_k, profile=profile,
                backend=Ollama(profile["endpoint"]) if profile else None,
                model_cases=args.model_cases.split(",") if args.model_cases else None,
                model_repeats=args.model_repeats, answer_tokens=args.answer_tokens)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
            return 1 if args.check and (not result["core_regression_pass"] or result["outcome"] != "complete") else 0
        if args.command in {"models", "pin"}:
            client = Ollama(args.endpoint)
            result = {"models": client.models()} if args.command == "models" else client.pin(args.model)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
            return 0
        if args.command == "note":
            target = Path(args.path)
            if str(target.parent) not in (".", ""):
                target.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            with target.open("a", encoding="utf-8") as handle:
                handle.write("\n\n## " + stamp + "\n\n" + args.text.rstrip("\n") + "\n")
            print(json.dumps({"outcome": "appended", "path": str(target)}, ensure_ascii=True, sort_keys=True))
            return 0
        if not args.db:
            parser.error("--db is required for this command")
        if args.command == "mcp":
            from .mcp import serve
            return serve(args.db, args.root)
        if args.command in {"candidates", "frontier"}:
            from .memory import Memory
            memory = Memory(args.db, args.root)
            try:
                if args.command == "candidates":
                    result = memory.candidates()
                elif args.replay:
                    status, verification = memory.frontier_replay(args.replay)
                    result = {"entry": args.replay, "status": status,
                              "verification": verification}
                elif args.record_miss:
                    if not args.question or not args.authority:
                        parser.error("--record-miss requires --question and --authority")
                    entry_id, _ = memory.frontier_record_miss(
                        args.question, args.record_miss, authority=args.authority)
                    result = {"recorded": entry_id,
                              "retrieval": [e for e in memory.frontier_list(status="open")
                                            if e["id"] == entry_id][0]["retrieval"]}
                elif args.export_regression:
                    if not args.out:
                        parser.error("--export-regression requires --out")
                    case = memory.frontier_export_regression(
                        args.export_regression, args.out)
                    result = {"exported": args.out,
                              "predicate": case["predicate"]}
                else:
                    result = {"status": args.status or "all",
                              "entries": memory.frontier_list(status=args.status)}
            finally:
                memory.close()
            print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
            return 0
        with GraphStore(args.db) as store:
            if args.command == "ingest":
                result = store.ingest(args.root)
            elif args.command == "refresh":
                result = store.refresh(args.root)
                print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
                return 0
            elif args.command == "impact":
                result = compile_impact(store, args.symbol, snapshot=args.snapshot, policy=args.policy,
                                        max_depth=args.max_depth, max_nodes=args.max_nodes)
            elif args.command == "mentions":
                result = compile_mentions(store, args.symbol)
            elif args.command == "claims":
                result = compile_claims(store, args.symbol)
            elif args.command == "related":
                result = compile_related(store, args.symbol, policy=args.policy)
            elif args.command == "timeline":
                result = compile_timeline(store, args.symbol, at=args.at)
            elif args.command == "draft":
                print(json.dumps(compile_drafts(store, args.document), ensure_ascii=True, sort_keys=True, indent=2))
                return 0
            elif args.command == "review":
                print(json.dumps(compile_review(store, args.document), ensure_ascii=True, sort_keys=True, indent=2))
                return 0
            elif args.command in {"validate", "render"}:
                artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8-sig"))
                if args.command == "render":
                    result = render_context(store, artifact, max_bytes=args.max_bytes)
                else:
                    result = validate_artifact(store, artifact, expected_snapshot=None if args.allow_historical else store.current())
            else:
                profile = json.loads(Path(args.profile).read_text(encoding="utf-8-sig"))
                result = compare_continuation(store, Ollama(profile["endpoint"]), profile, args.symbol, args.task,
                    repeats=args.repeats, plan_tokens=args.plan_tokens, answer_tokens=args.answer_tokens,
                    context_tokens=args.context_tokens, max_input_tokens=args.max_input_tokens)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        return 1 if result.get("valid") is False or result.get("outcome") in {"failed", "output_limit_reached", "unsupported_answer"} else 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({"outcome": "failed", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
