"""Incremental graph maintenance: refresh() must equal full rebuilds.

The differential contract: for any tree state, refresh() on a live database
must publish the identical snapshot identity a fresh full ingest publishes,
while parsing measurably less on document-only changes.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory
from tunic.store import GraphStore


def _write(root, rel, text):
    target = Path(root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


BASE_CODE = "def calculate(value):\n    return value + 1\n"
BASE_NOTE = "Tracing `core.py::calculate`.\n"


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        _write(self.root, "core.py", BASE_CODE)
        _write(self.root, "notes/session.md", BASE_NOTE)
        self.store = GraphStore(str(self.tmp / "graph.sqlite"))
        self.addCleanup(self.store.close)
        self.store.ingest(str(self.root))

    def _fresh_snapshot(self):
        oracle = GraphStore(str(self.tmp / "oracle.sqlite"))
        self.addCleanup(oracle.close)
        oracle.ingest(str(self.root))
        snapshot, manifest = oracle.view()
        return snapshot, manifest

    def _current_manifest(self):
        return self.store.view()[1]

    def test_refresh_without_changes_returns_current_without_parsing(self):
        before = self.store.current()
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["snapshot"], before)
        self.assertEqual(report["path"], "unchanged")
        self.assertEqual(report["work"]["files_parsed"], 0)
        self.assertEqual(self.store.current(), before)

    def test_refresh_doc_only_matches_full_rebuild(self):
        _write(self.root, "notes/session.md",
               BASE_NOTE + "Also `core.py::calculate` feeds the report.\n")
        report = self.store.refresh(str(self.root))
        expected, expected_manifest = self._fresh_snapshot()
        self.assertEqual(report["path"], "documents")
        self.assertEqual(report["snapshot"], expected)
        self.assertEqual(report["work"]["code_files_parsed"], 0)
        self.assertGreater(report["work"]["doc_files_processed"], 0)
        self.assertEqual(self._current_manifest(), expected_manifest)

    def test_refresh_code_change_matches_full_rebuild(self):
        _write(self.root, "core.py",
               BASE_CODE + "\n\ndef validate(value):\n    return value > 0\n")
        report = self.store.refresh(str(self.root))
        expected, expected_manifest = self._fresh_snapshot()
        self.assertEqual(report["path"], "code")
        self.assertEqual(report["snapshot"], expected)
        self.assertEqual(self._current_manifest(), expected_manifest)
        self.assertGreaterEqual(report["work"].get("parse_skipped", 0), 0)

    def test_refresh_added_and_deleted_docs(self):
        _write(self.root, "notes/extra.md", "Nothing to link here.\n")
        (self.root / "notes" / "session.md").unlink()
        report = self.store.refresh(str(self.root))
        expected, expected_manifest = self._fresh_snapshot()
        self.assertEqual(report["path"], "documents")
        self.assertEqual(report["snapshot"], expected)
        self.assertEqual(self._current_manifest(), expected_manifest)

    def test_refresh_without_snapshot_falls_back_to_full(self):
        fresh = GraphStore(str(self.tmp / "empty.sqlite"))
        self.addCleanup(fresh.close)
        report = fresh.refresh(str(self.root))
        self.assertEqual(report["path"], "full")
        self.assertEqual(fresh.view()[1], self._current_manifest())

    def test_memory_note_refresh_takes_documents_path(self):
        memory = Memory(str(self.tmp / "agent.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        memory.note("Verified `core.py::calculate` handles the refund path.",
                    document="notes/session.md")
        report = memory.refresh()
        self.assertEqual(report["path"], "documents")
        self.assertEqual(report["work"]["code_files_parsed"], 0)
        expected, _ = self._fresh_snapshot()
        self.assertEqual(report["snapshot"], expected)
        self.assertTrue(any(e["symbol"] == "core.py::calculate"
                            for e in memory.review("notes/session.md")["entities"]))

    def test_refresh_preserves_cross_document_mention_dedup(self):
        line = "See `core.py::calculate`.\n"
        _write(self.root, "notes/a.md", line)
        _write(self.root, "notes/b.md", line)
        (self.root / "notes" / "session.md").unlink()
        self.store.refresh(str(self.root))
        mentions = [a for a in self._current_manifest()["assertions"]
                    if a["proposition"].get("relation") == "text_mention"
                    and a["proposition"].get("callee") == "core.py::calculate"
                    and a["start"] == 0]
        self.assertEqual(len({a["source"] for a in mentions}), 1)
        _write(self.root, "notes/b.md", line + "Also `core.py::calculate` again.\n")
        report = self.store.refresh(str(self.root))
        expected, expected_manifest = self._fresh_snapshot()
        self.assertEqual(report["path"], "documents")
        self.assertEqual(report["snapshot"], expected)
        self.assertEqual(self._current_manifest(), expected_manifest)

    def test_refresh_rejects_foreign_root_like_ingest(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        _write(other, "core.py", BASE_CODE)
        with self.assertRaises(ValueError):
            self.store.refresh(str(other))


if __name__ == "__main__":
    unittest.main()
