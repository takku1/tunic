"""Discovery scope unification: one scope for ingest and retrieval.

The query path must obey the same effective scope as ingest (defaults
+ root .gitignore + explicit excludes) and must prune excluded trees
before descent: sharing an is_excluded() predicate while still
rglob()-ing node_modules fixes nothing. Scope stats ride on the
index objects so tests and benchmarks observe pruned work.
"""

import tempfile
import unittest
from pathlib import Path

from tunic.baselines import BM25, TextNotes
from tunic.memory import Memory

PY_MAIN = 'from util import helper\n\n\ndef run():\n    return helper()\n'
PY_UTIL = 'def helper():\n    return 1\n'
TS_APP = 'export function render(name: string): string {\n  return name;\n}\n'
GUIDE_MD = "# Guide\n\nThe `render` function formats a greeting.\n"
KEEP_MD = "# Kept\n\nRetention note.\n"


def _build_site(root, gitignore="dist/\nreference/\n*.log\n*.md\n!docs/keep.md\n"):
    root = Path(root)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "dist").mkdir(parents=True, exist_ok=True)
    (root / "reference").mkdir(parents=True, exist_ok=True)
    (root / "node_modules" / "pkg" / "deep").mkdir(parents=True,
                                                  exist_ok=True)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / "src" / "app.py").write_text(PY_MAIN, encoding="utf-8")
    (root / "src" / "util.py").write_text(PY_UTIL, encoding="utf-8")
    (root / "src" / "app.ts").write_text(TS_APP, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(GUIDE_MD, encoding="utf-8")
    (root / "docs" / "keep.md").write_text(KEEP_MD, encoding="utf-8")
    (root / "dist" / "bundle.js").write_text(
        "function render(name){return name}\n", encoding="utf-8")
    (root / "reference" / "crawl.md").write_text(
        "# Crawl\n\nVendored crawl mentioning render many times render.\n",
        encoding="utf-8")
    (root / "node_modules" / "pkg" / "index.js").write_text(
        "module.exports = {};\n", encoding="utf-8")
    (root / "node_modules" / "pkg" / "deep" / "more.js").write_text(
        "module.exports = {};\n", encoding="utf-8")
    (root / "debug.log").write_text("noise\n", encoding="utf-8")


def _ingest_site(tmp, **kwargs):
    work = Path(tmp) / "ws"
    _build_site(work)
    db_path = str(Path(tmp) / "site.sqlite")
    memory = Memory(db_path, str(work))
    report = memory.ingest(**kwargs)
    return memory, report, str(work)


class DiscoveryScopeTest(unittest.TestCase):
    def test_indexes_prune_before_descent(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, work = _ingest_site(tmp)
            try:
                code = BM25(work)
                notes = TextNotes(work)
                indexed = {d["symbol"] for d in code.documents}
                noted = {d["symbol"] for d in notes.documents}
                self.assertTrue(indexed, "in-scope python not indexed")
                self.assertTrue(
                    all(not s.startswith(("node_modules/", "dist/"))
                        for s in indexed),
                    "excluded trees indexed by BM25")
                self.assertIn("docs/keep.md", noted)
                self.assertNotIn("docs/guide.md", noted)
                self.assertFalse([s for s in noted
                                  if s.startswith(("node_modules/",
                                                   "dist/", "reference/"))],
                                 "excluded trees indexed by TextNotes")
                self.assertGreater(code.scope_stats["dirs_pruned"], 0)
                self.assertGreater(notes.scope_stats["dirs_pruned"], 0)
            finally:
                memory.close()

    def test_negation_reinclude_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, work = _ingest_site(tmp)
            try:
                notes = TextNotes(work)
                noted = {d["symbol"] for d in notes.documents}
                self.assertIn("docs/keep.md", noted)
            finally:
                memory.close()

    def test_negation_beneath_excluded_parent_stays_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_site(work, gitignore="docs/\n!docs/keep.md\n")
            notes = TextNotes(str(work))
            noted = {d["symbol"] for d in notes.documents}
            self.assertNotIn("docs/keep.md", noted)

    def test_explicit_exclude_param(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, work = _ingest_site(tmp)
            try:
                code = BM25(work, exclude=["src"])
                indexed = {d["symbol"] for d in code.documents}
                self.assertFalse([s for s in indexed
                                  if s.startswith("src/")],
                                 "explicit exclude ignored")
                notes = TextNotes(work, exclude=["docs"])
                noted = {d["symbol"] for d in notes.documents}
                self.assertFalse([s for s in noted
                                  if s.startswith("docs/")],
                                 "explicit exclude ignored")
            finally:
                memory.close()

    def test_answer_obeys_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _ws = _ingest_site(tmp)
            try:
                result = memory.answer(
                    "Where is the render function defined?", select=True)
                seen = ({c["symbol"] for c in result["candidates"]} |
                        {i["symbol"] for i in result["impacts"]})
                self.assertFalse(
                    [s for s in seen
                     if s.startswith(("node_modules/", "dist/",
                                      "reference/"))],
                    "answer cites excluded scope: %r" % (seen,))
                self.assertIn("src/app.ts::render", seen)
            finally:
                memory.close()

    def test_cache_key_ignores_excluded_churn(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, work = _ingest_site(tmp)
            try:
                memory.answer("ping", select=True)
                key_before = memory._discovery_key
                self.assertIsNotNone(key_before)
                Path(work, "node_modules", "pkg", "new.js").write_text(
                    "module.exports = {};\n", encoding="utf-8")
                memory.answer("ping", select=True)
                self.assertEqual(memory._discovery_key, key_before)
            finally:
                memory.close()

    def test_cache_key_tracks_gitignore_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, work = _ingest_site(tmp)
            try:
                memory.answer("ping", select=True)
                key_before = memory._discovery_key
                with open(Path(work) / ".gitignore", "a",
                           encoding="utf-8") as handle:
                    handle.write("docs/\n")
                memory.answer("ping", select=True)
                self.assertNotEqual(memory._discovery_key, key_before)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
