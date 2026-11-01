"""Zero-config ingest: `tunic <path>` per the frontier-ledger spec."""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tunic.__main__ import _zero_config_target, main
from tunic.memory import Memory


class ZeroConfigTargetTests(unittest.TestCase):
    """Detection of the zero-config form versus normal subcommands."""

    def test_bare_path_activates_zero_config(self):
        self.assertEqual(_zero_config_target(["repo/src"]), "repo/src")

    def test_db_override_and_equals_form(self):
        self.assertEqual(_zero_config_target(["--db", "x.sqlite", "repo"]),
                         "repo")
        self.assertEqual(_zero_config_target(["--db=y.sqlite", "repo"]),
                         "repo")

    def test_known_commands_and_multi_positional_do_not(self):
        self.assertIsNone(_zero_config_target(["frontier"]))
        self.assertIsNone(_zero_config_target(["note", "a.md", "--text", "x"]))
        self.assertIsNone(_zero_config_target(["ingest", "repo"]))
        self.assertIsNone(_zero_config_target([]))


class ZeroConfigIngestTests(unittest.TestCase):
    """`tunic <path>` ingests, defaults the database, prints the summary."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        (self.root / "service.py").write_bytes(
            b"from core import calculate\n\n\ndef process(value):\n    return calculate(value)\n")
        (self.root / "dynamic.py").write_bytes(
            b"def run(handler, value):\n    return handler(value)\n")
        (self.root / "broken.py").write_bytes(b"def oops(:\n    pass\n")

    def _run(self, *argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(list(argv))
        self.assertEqual(code, 0, buffer.getvalue())
        return json.loads(buffer.getvalue())

    def test_ingests_with_default_database_and_summary(self):
        summary = self._run(str(self.root))
        db = self.root / ".tunic" / "graph.sqlite"
        self.assertTrue(db.is_file())
        # resolve() both sides: Windows temp dirs may use 8.3 short names
        # (RUNNER~1) while the summary reports the resolved long form.
        self.assertEqual(summary["db"], str(db.resolve()))
        self.assertEqual(summary["root"], str(self.root.resolve()))
        self.assertEqual(summary["files"], 4)
        self.assertEqual(summary["symbols"], 3)
        # raw storage status: extractor-produced edges are
        # syntactic_observation until asserted through a document claim.
        self.assertEqual(summary["assertions_by_status"],
                         {"syntactic_observation": 1})
        self.assertEqual(summary["diagnostics_by_kind"],
                         {"parse_error": 1, "unresolved_call": 1})
        # the summary's open frontier counts are the user-visible proof
        # that unknowns are kept, not dropped.
        self.assertEqual(summary["open_frontier_by_kind"],
                         {"unresolved": 1, "unsupported": 1})
        self.assertEqual(summary["frontier_filed"], 2)
        self.assertEqual(summary["coverage"], "bounded_static_analysis")

    def test_db_flag_overrides_default_location(self):
        custom = str(self.tmp / "custom" / "graph.sqlite")
        summary = self._run("--db", custom, str(self.root))
        self.assertEqual(summary["db"], custom)
        self.assertTrue(Path(custom).is_file())
        self.assertFalse((self.root / ".tunic").exists())

    def test_rerun_is_idempotent_and_bumps_recurrence(self):
        first = self._run(str(self.root))
        second = self._run(str(self.root))
        self.assertEqual(first["open_frontier_by_kind"],
                         second["open_frontier_by_kind"])
        memory = Memory(str(self.root / ".tunic" / "graph.sqlite"), self.root)
        self.addCleanup(memory.close)
        entries = memory.frontier_list()
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e["recurrence"] == 2 for e in entries))

    def test_missing_path_fails_closed(self):
        import sys
        from contextlib import redirect_stderr
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            code = main([str(self.tmp / "nope")])
        self.assertEqual(code, 2)
        self.assertIn("failed", buffer.getvalue())
