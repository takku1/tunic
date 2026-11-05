"""Snapshot freshness receipts + storage backup/restore.

Track A: a read-only freshness check compares indexed source digests
against the live workspace without publishing anything. Track B: backup
produces an atomic consistent copy; restore validates before handing
back a database; schema versions gate opening.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tunic.__main__ import main as tunic_main
from tunic.extract import PROFILE
from tunic.memory import Memory
from tunic.store import GraphStore


def _frontier_count(store):
    return store.connection.execute(
        "SELECT COUNT(*) FROM frontier").fetchone()[0]

FILES = {
    "a.py": "def alpha():\n    return 1\n",
    "notes.md": "# notes\n\nThe alpha helper matters.\n",
}


def _ws(tmp, extra=None):
    work = Path(tmp) / "ws"
    work.mkdir()
    for name, text in dict(FILES, **(extra or {})).items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return str(work)


class FreshnessTest(unittest.TestCase):
    def test_fresh_tree_reports_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _ws(tmp)
            db = str(Path(tmp) / "g.sqlite")
            memory = Memory(db, root)
            try:
                report = memory.ingest()
                receipt = memory.store.freshness(root)
                self.assertTrue(receipt["fresh"])
                self.assertEqual(receipt["added"], [])
                self.assertEqual(receipt["deleted"], [])
                self.assertEqual(receipt["changed"], [])
                self.assertEqual(receipt["snapshot"], report["snapshot"])
                self.assertEqual(receipt["profile"], PROFILE)
                self.assertIn("committed_at", receipt)
            finally:
                memory.close()

    def test_changes_listed_without_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _ws(tmp)
            db = str(Path(tmp) / "g.sqlite")
            memory = Memory(db, root)
            try:
                report = memory.ingest()
                before = memory.store.snapshots()
                (Path(root) / "a.py").write_text(
                    "def alpha():\n    return 2\n", encoding="utf-8")
                (Path(root) / "b.py").write_text(
                    "def beta():\n    return 3\n", encoding="utf-8")
                (Path(root) / "notes.md").unlink()
                receipt = memory.store.freshness(root)
                self.assertFalse(receipt["fresh"])
                self.assertEqual(receipt["added"], ["b.py"])
                self.assertEqual(receipt["deleted"], ["notes.md"])
                self.assertEqual(receipt["changed"], ["a.py"])
                self.assertEqual(memory.store.snapshots(), before)
                self.assertEqual(receipt["snapshot"], report["snapshot"])
            finally:
                memory.close()

    def test_wrong_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _ws(tmp)
            db = str(Path(tmp) / "g.sqlite")
            memory = Memory(db, root)
            try:
                memory.ingest()
                with self.assertRaises(ValueError):
                    memory.store.freshness(str(Path(tmp)))
            finally:
                memory.close()

    def test_answer_carries_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _ws(tmp)
            db = str(Path(tmp) / "g.sqlite")
            memory = Memory(db, root)
            try:
                report = memory.ingest()
                result = memory.answer("alpha")
                provenance = result.get("provenance", {})
                self.assertEqual(provenance.get("snapshot"),
                                 report["snapshot"])
                self.assertEqual(provenance.get("profile"), PROFILE)
                self.assertTrue(provenance.get("root", "").endswith("ws"))
                self.assertIn("committed_at", provenance)
            finally:
                memory.close()

    def test_freshness_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _ws(tmp)
            db = str(Path(tmp) / "g.sqlite")
            code = tunic_main(["--db", db, "ingest", root])
            self.assertEqual(code, 0)
            code = tunic_main(["--db", db, "freshness", root])
            self.assertEqual(code, 0)


class BackupRestoreTest(unittest.TestCase):
    def _ingested(self, tmp):
        root = _ws(tmp)
        db = str(Path(tmp) / "g.sqlite")
        memory = Memory(db, root)
        memory.ingest()
        memory.answer("no-such-symbol-anywhere")
        _, view = memory.store.view()
        counts = {
            "snapshot": memory.store.current(),
            "symbols": len(view["symbols"]),
            "assertions": len(view["assertions"]),
            "diagnostics": len(view["diagnostics"]),
            "frontier": _frontier_count(memory.store),
        }
        memory.close()
        return db, root, counts

    def test_backup_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, counts = self._ingested(tmp)
            backup = str(Path(tmp) / "backup.sqlite")
            with GraphStore(db) as store:
                receipt = store.backup(backup)
            self.assertTrue(Path(backup).exists())
            self.assertEqual(receipt["snapshot"], counts["snapshot"])
            restored = str(Path(tmp) / "restored.sqlite")
            with GraphStore(db) as store:
                verify = store.restore(backup, restored)
            self.assertEqual(verify["snapshot"], counts["snapshot"])
            self.assertEqual(verify["integrity"], "ok")
            with GraphStore(restored) as store:
                _, view = store.view()
                self.assertEqual(store.current(), counts["snapshot"])
                self.assertEqual(len(view["symbols"]), counts["symbols"])
                self.assertEqual(len(view["assertions"]),
                                 counts["assertions"])
                self.assertEqual(len(view["diagnostics"]),
                                 counts["diagnostics"])
                self.assertEqual(_frontier_count(store), counts["frontier"])

    def test_restore_refuses_existing_dest(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, _counts = self._ingested(tmp)
            backup = str(Path(tmp) / "backup.sqlite")
            with GraphStore(db) as store:
                store.backup(backup)
                with self.assertRaises(ValueError):
                    store.restore(backup, db)

    def test_restore_rejects_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, _counts = self._ingested(tmp)
            bad = str(Path(tmp) / "bad.sqlite")
            Path(bad).write_bytes(b"not a database at all")
            with GraphStore(db) as store:
                with self.assertRaises(ValueError):
                    store.restore(bad, str(Path(tmp) / "out.sqlite"))

    def test_future_schema_version_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "g.sqlite")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, "
                        "value TEXT NOT NULL)")
            con.execute("PRAGMA user_version=99")
            con.commit()
            con.close()
            with self.assertRaises(ValueError):
                GraphStore(db)

    def test_empty_db_migrates_to_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "fresh.sqlite")
            with GraphStore(db) as store:
                version = store.connection.execute(
                    "PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, 1)

    def test_backup_restore_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _root, counts = self._ingested(tmp)
            backup = str(Path(tmp) / "backup.sqlite")
            code = tunic_main(["--db", db, "backup", "--to", backup])
            self.assertEqual(code, 0)
            restored = str(Path(tmp) / "restored.sqlite")
            code = tunic_main(["--db", db, "restore", "--from", backup,
                               "--to", restored])
            self.assertEqual(code, 0)
            with GraphStore(restored) as store:
                self.assertEqual(store.current(), counts["snapshot"])
