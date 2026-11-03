"""Universal ingest v1: scope control + any-language inventory.

Tunic must work on any repository: build output and vendored code stay
out (defaults + .gitignore + explicit excludes), and non-Python code
files contribute an honest heuristic symbol inventory (no invented
call edges) so lookup, mentions, and review work across languages.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory

APP_TS = """import { format } from "./util";

export function render(name: string): string {
  return format(name);
}

export class Greeter {
  greet(who: string): string {
    return render(who);
  }
}
"""

UTIL_TS = """export function format(name: string): string {
  return `hello ${name}`;
}

export const upper = (s: string): string => s.toUpperCase();
"""

GUIDE_MD = "# Guide\n\nThe `render` function formats a greeting.\n"


def _build_polyglot(root,
                    gitignore="dist/\n*.log\n*.md\n!docs/keep.md\n"):
    root = Path(root)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / ".astro").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / "src" / "app.ts").write_text(APP_TS, encoding="utf-8")
    (root / "src" / "util.ts").write_text(UTIL_TS, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(GUIDE_MD, encoding="utf-8")
    (root / "docs" / "keep.md").write_text("Kept note.\n", encoding="utf-8")
    (root / "dist" / "bundle.js").write_text(
        "function render(name){return name}\n", encoding="utf-8")
    (root / ".astro" / "cache.mjs").write_text(
        "export default {};\n", encoding="utf-8")
    (root / "node_modules" / "pkg" / "index.js").write_text(
        "module.exports = {};\n", encoding="utf-8")
    (root / "debug.log").write_text("noise\n", encoding="utf-8")
    (root / "config.json").write_text('{"render": true}\n', encoding="utf-8")


def _ingest(tmp, **kwargs):
    work = Path(tmp) / "ws"
    _build_polyglot(work)
    db_path = str(Path(tmp) / "poly.sqlite")
    memory = Memory(db_path, str(work))
    report = memory.ingest(**kwargs)
    return memory, report, str(work)


def _source_paths(memory):
    _snapshot, view = memory.store.view()
    rows = memory.store.connection.execute(
        "SELECT path FROM sources s JOIN members m ON m.source=s.id "
        "WHERE m.snapshot=?", (_snapshot,)).fetchall()
    return sorted(r[0] for r in rows)


class ScopeControlTest(unittest.TestCase):
    def test_default_excludes_skip_build_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _ws = _ingest(tmp)
            try:
                paths = _source_paths(memory)
                self.assertFalse([p for p in paths
                                  if p.startswith("dist/")
                                  or p.startswith(".astro/")
                                  or p.startswith("node_modules/")
                                  or p.startswith("build/")],
                                 "build output ingested: %r" % (paths,))
            finally:
                memory.close()

    def test_gitignore_honored_with_negation(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, report, _ws = _ingest(tmp)
            try:
                paths = _source_paths(memory)
                self.assertNotIn("debug.log", paths)
                self.assertNotIn("docs/guide.md", paths)
                self.assertIn("docs/keep.md", paths)
                self.assertGreaterEqual(report["excluded"]["files"], 1)
            finally:
                memory.close()

    def test_exclude_param_skips_named_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _ws = _ingest(tmp, exclude=["docs"])
            try:
                paths = _source_paths(memory)
                self.assertFalse([p for p in paths
                                  if p.startswith("docs/")],
                                 "excluded scope ingested: %r" % (paths,))
                self.assertIn("src/app.ts", paths)
            finally:
                memory.close()

    def test_refresh_equivalence_with_excludes(self):
        # Excludes are explicit per operation (the publication protocol
        # stays single-writer); refresh repeats the ingest scope.
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _ws = _ingest(tmp, exclude=["docs"])
            try:
                first = memory.store.current()
                result = memory.refresh(exclude=["docs"])
                self.assertEqual(memory.store.current(), first)
                self.assertTrue(result.get("reused", False)
                                or result.get("path") == "unchanged")
            finally:
                memory.close()


class InventoryTest(unittest.TestCase):
    def test_typescript_symbols_inventoried_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _ws = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                keys = {s["key"] for s in view["symbols"]}
                for expected in ("src/app.ts::render",
                                 "src/app.ts::Greeter",
                                 "src/util.ts::format",
                                 "src/util.ts::upper"):
                    self.assertIn(expected, keys)
            finally:
                memory.close()

    def test_no_assertions_from_harvested_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_polyglot(work)
            (work / "src" / "tool.go").write_text(
                "package main\n\nfunc helper() int {\n\treturn 1\n}\n",
                encoding="utf-8")
            memory = Memory(str(Path(tmp) / "poly.sqlite"), str(work))
            memory.ingest()
            try:
                _snapshot, view = memory.store.view()
                by_id = {a["id"]: a for a in view["assertions"]}
                _ = by_id
                sources = {s["key"]: s for s in view["symbols"]}
                harvested = {s["source"] for s in view["symbols"]
                             if s.get("language") == "ts"}
                self.assertTrue(harvested)
                rows = memory.store.connection.execute(
                    "SELECT caller, callee FROM assertions").fetchall()
                # TypeScript/JavaScript now contributes VERIFIED static-call
                # edges (ts_static_binding_v1 proof rule); every other
                # harvested language must stay edge-free.
                self.assertIn(("src/app.ts::render", "src/util.ts::format"),
                              [tuple(r) for r in rows])
                rules = {a["resolution"]["rule"] for a in view["assertions"]}
                self.assertEqual(rules, {"ts_static_binding_v1"})
                self.assertIn("src/tool.go::helper", sources)
                self.assertFalse(
                    [r for r in rows
                     if "tool.go" in r[0] or "tool.go" in r[1]],
                    "harvester invented call edges")
                self.assertEqual(sources["src/app.ts::render"]
                                 ["derivation"], "heuristic_inventory_v1")
            finally:
                memory.close()

    def test_deterministic_reingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_polyglot(work)
            first_db = str(Path(tmp) / "a.sqlite")
            second_db = str(Path(tmp) / "b.sqlite")
            first = Memory(first_db, str(work))
            second = Memory(second_db, str(work))
            try:
                first.ingest()
                second.ingest()
                self.assertEqual(first.store.current(),
                                 second.store.current())
            finally:
                first.close()
                second.close()

    def test_data_files_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _ws = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                keys = {s["key"] for s in view["symbols"]}
                self.assertFalse([k for k in keys
                                  if k.startswith("config.json")],
                                 "data file inventoried")
            finally:
                memory.close()

    def test_exact_lookup_finds_harvested_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _ws = _ingest(tmp)
            try:
                result = memory.answer(
                    "Where is src/app.ts::render defined?", select=True)
                kept = {c["symbol"] for c in result["candidates"]}
                self.assertIn("src/app.ts::render", kept)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
