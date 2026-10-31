"""File-level incremental code refresh: differential equivalence with full rebuild.

For every tree state, refresh() must publish the identical snapshot identity
(and identical manifest contents) a fresh full ingest publishes, while
parsing only the affected subset on code changes.
"""

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.store import GraphStore

CORE = "def calculate(value):\n    return value + 1\n"
SERVICE = ("from core import calculate as compute\n"
           "from helper import check\n\n"
           "def process(value):\n    return check(compute(value))\n")
HELPER = "def check(value):\n    return value > 0\n"
STAR = "from core import *\n\ndef probe(value):\n    return calculate(value)\n"
NOTES = "Tracing `core.py::calculate` and `service.py::process`.\n"


def _write(root, rel, text):
    target = Path(root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


def _seed_repo(root):
    _write(root, "core.py", CORE)
    _write(root, "service.py", SERVICE)
    _write(root, "helper.py", HELPER)
    _write(root, "star.py", STAR)
    _write(root, "notes.md", NOTES)


class IncrementalCodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        _seed_repo(self.root)
        self.store = GraphStore(str(self.tmp / "graph.sqlite"))
        self.addCleanup(self.store.close)
        self.store.ingest(str(self.root))
        self.oracle_count = 0

    def _oracle_manifest(self):
        self.oracle_count += 1
        oracle = GraphStore(str(self.tmp / ("oracle-%d.sqlite" % self.oracle_count)))
        self.addCleanup(oracle.close)
        oracle.ingest(str(self.root))
        return oracle.view()

    def _check_equivalent(self, report):
        expected_snapshot, expected_manifest = self._oracle_manifest()
        self.assertEqual(report["snapshot"], expected_snapshot)
        self.assertEqual(self.store.view()[1], expected_manifest)
        return expected_snapshot

    def test_body_only_change_reparses_importers(self):
        _write(self.root, "core.py", CORE.replace("value + 1", "value + 2"))
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self.assertEqual(sorted(report["affected_files"]),
                         ["core.py", "service.py", "star.py"])
        self.assertGreater(report["work"].get("parse_skipped", 0), 0)
        first = self._check_equivalent(report)
        again = self.store.refresh(str(self.root))
        self.assertEqual(again["path"], "unchanged")
        self.assertEqual(again["snapshot"], first)

    def test_add_and_delete_function(self):
        _write(self.root, "helper.py",
               HELPER + "\ndef validate(value):\n    return value is not None\n")
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self._check_equivalent(report)
        _write(self.root, "helper.py", "def validate(value):\n    return True\n")
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self._check_equivalent(report)

    def test_add_and_delete_file(self):
        _write(self.root, "extra.py", "def bonus(value):\n    return value * 2\n")
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self._check_equivalent(report)
        (self.root / "extra.py").unlink()
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self._check_equivalent(report)

    def test_rename_breaks_importers_identically(self):
        _write(self.root, "core.py",
               CORE.replace("def calculate(", "def compute_total("))
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self._check_equivalent(report)
        self.assertIn("service.py", report["affected_files"])

    def test_syntax_error_matches_full_rebuild(self):
        _write(self.root, "helper.py", "def broken(:\n")
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self._check_equivalent(report)
        _write(self.root, "helper.py", HELPER)
        report = self.store.refresh(str(self.root))
        self._check_equivalent(report)

    def test_code_and_doc_change_together(self):
        _write(self.root, "core.py", CORE.replace("value + 1", "value + 3"))
        _write(self.root, "notes.md",
               NOTES + "\n```tunic-claims\n[{\"rel\": \"depends_on\", "
               "\"subject\": \"service.py::process\", "
               "\"object\": \"core.py::calculate\"}]\n```\n")
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self._check_equivalent(report)

    def test_bare_leaf_candidate_survives_body_change(self):
        _write(self.root, "bare.md",
               "Some context here.\ncalculate handles the increment.\n")
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "documents")
        self._check_equivalent(report)
        candidates = [d for d in self.store.view()[1]["diagnostics"]
                      if d.get("kind") == "mention_candidate"
                      and d.get("path") == "bare.md"]
        self.assertTrue(
            any(d.get("callee") == "core.py::calculate" for d in candidates),
            "bare 'calculate' should be a recorded candidate")
        self.assertFalse(
            [a for a in self.store.view()[1]["assertions"]
             if a.get("proposition", {}).get("caller", "") == "bare.md::document"
             and a.get("proposition", {}).get("relation") == "text_mention"],
            "uncorroborated bare mention must not become evidence")
        _write(self.root, "core.py", CORE.replace("value + 1", "value + 2"))
        report = self.store.refresh(str(self.root))
        self.assertEqual(report["path"], "code")
        self.assertGreaterEqual(report["work"].get("docs_reprocessed", 0), 1)
        self._check_equivalent(report)


class RandomizedEditTests(unittest.TestCase):
    def test_randomized_sequences_match_full_rebuild(self):
        rng = random.Random(20261030)
        bodies = ["return value + %d\n" % i for i in range(5)]
        skipped_runs = 0
        for step in range(25):
            tmp = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, tmp, True)
            root = tmp / "repo"
            root.mkdir()
            _seed_repo(root)
            store = GraphStore(str(tmp / "graph.sqlite"))
            self.addCleanup(store.close)
            store.ingest(str(root))
            for _ in range(rng.randint(1, 3)):
                choice = rng.randint(0, 7)
                if choice == 0:
                    _write(root, "core.py",
                           "def calculate(value):\n    " + rng.choice(bodies))
                elif choice == 1:
                    _write(root, "helper.py", HELPER + "\ndef extra_%d():\n    return %d\n"
                           % (rng.randint(0, 99), rng.randint(0, 99)))
                elif choice == 2:
                    _write(root, "service.py", SERVICE.replace(
                        "return check(compute(value))",
                        "return compute(value)"))
                elif choice == 3:
                    _write(root, "notes.md", NOTES + "<!-- %d -->\n" % rng.randint(0, 99))
                elif choice == 4:
                    extra = root / ("mod_%d.py" % rng.randint(0, 99))
                    if not extra.exists():
                        _write(root, extra.name,
                               "def f_%d():\n    return %d\n" % (rng.randint(0, 99), rng.randint(0, 99)))
                elif choice == 5:
                    _write(root, "helper.py", HELPER.replace(
                        "def check(", "def checked("))
                elif choice == 6:
                    _write(root, "service.py", SERVICE)
                    _write(root, "core.py", CORE)
                    _write(root, "helper.py", HELPER)
                else:
                    _write(root, "notes.md", NOTES + "\n```tunic-claims\n[{\"rel\": \"contradicts\", \"subject\": \"service.py::process\", \"object\": \"core.py::calculate\"}]\n```\n")
            report = store.refresh(str(root))
            self.assertIn(report["path"], ("code", "documents", "unchanged"), msg="step %d" % step)
            if report["work"].get("parse_skipped", 0) > 0:
                skipped_runs += 1
            oracle = GraphStore(str(tmp / "oracle.sqlite"))
            self.addCleanup(oracle.close)
            oracle.ingest(str(root))
            self.assertEqual(report["snapshot"], oracle.current(), msg="step %d" % step)
            self.assertEqual(store.view()[1], oracle.view()[1], msg="step %d" % step)
        self.assertGreater(skipped_runs, 0)


if __name__ == "__main__":
    unittest.main()
