"""Local single-principal CLI; never imports or executes analyzed source."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .compiler import compile_impact, validate_artifact
from .store import GraphStore


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tunic")
    parser.add_argument("--db", required=True, help="SQLite database path (parent must exist)")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="fully rebuild and atomically publish a Python source snapshot")
    ingest.add_argument("root", help="Python source root; e.g. repository/src for a src layout")
    impact = commands.add_parser("impact", help="compile possible reverse-call impact with evidence")
    impact.add_argument("symbol", help="exact path::function key, e.g. core.py::calculate")
    impact.add_argument("--snapshot")
    impact.add_argument("--max-depth", type=int, default=8)
    impact.add_argument("--max-nodes", type=int, default=100)
    validate = commands.add_parser("validate", help="check an artifact against stored evidence")
    validate.add_argument("artifact")
    validate.add_argument("--allow-historical", action="store_true", help="validate old snapshot without requiring current freshness")
    args = parser.parse_args(argv)
    try:
        with GraphStore(args.db) as store:
            if args.command == "ingest":
                result = store.ingest(args.root)
            elif args.command == "impact":
                result = compile_impact(store, args.symbol, snapshot=args.snapshot,
                                        max_depth=args.max_depth, max_nodes=args.max_nodes)
            else:
                artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8-sig"))
                result = validate_artifact(store, artifact, expected_snapshot=None if args.allow_historical else store.current())
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        return 1 if result.get("valid") is False else 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"outcome": "failed", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
