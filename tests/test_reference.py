import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from tunic import GraphStore, compile_impact, validate_artifact
from tunic.identity import identity


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".tunic" / "test-runs"
        scratch.mkdir(parents=True, exist_ok=True)
        self.base = (scratch / ("run-" + uuid.uuid4().hex)).resolve()
        self.base.mkdir()
        self.root = self.base / "repo"
        self.root.mkdir()
        self.db = self.base / "graph.sqlite"
        self.write("core.py", "def calculate(x):\n    return x + 1\n")
        self.write("service.py", "from core import calculate as compute\n\ndef process(x):\n    return compute(x)\n")
        self.write("test_service.py", "import service as svc\n\ndef test_process():\n    assert svc.process(3) == 4\n")
        self.write("unrelated.py", 'def text():\n    return "calculate(3)"\n\ndef dynamic(callback):\n    return callback(3)\n')
        self.store = GraphStore(self.db)

    def tearDown(self):
        self.store.close()
        allowed = (Path(__file__).resolve().parents[1] / ".tunic" / "test-runs").resolve()
        assert self.base.is_relative_to(allowed) and self.base != allowed
        shutil.rmtree(self.base)

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def artifact(self, **kwargs):
        return compile_impact(self.store, "core.py::calculate", **kwargs)

    def test_independent_expected_paths_and_utf8_evidence(self):
        self.write("unicode.py", 'from core import calculate\n\ndef caller():\n    label = "猫"; return calculate(2)\n')
        self.store.ingest(self.root)
        a = self.artifact()
        self.assertEqual([i["symbol"] for i in a["impacts"]],
                         ["service.py::process", "test_service.py::test_process", "unicode.py::caller"])
        paths = {i["symbol"]: i["derivation"]["premises"] for i in a["impacts"]}
        self.assertEqual(len(paths["service.py::process"]), 1)
        self.assertEqual(len(paths["test_service.py::test_process"]), 2)
        observations = {u["id"]:u["assertion"]["proposition"] for u in a["units"]}
        self.assertEqual([observations[p] for p in paths["test_service.py::test_process"]], [
            {"relation": "static_call", "caller": "test_service.py::test_process", "callee": "service.py::process"},
            {"relation": "static_call", "caller": "service.py::process", "callee": "core.py::calculate"}])
        self.assertTrue(next(i for i in a["impacts"] if i["symbol"].startswith("test_"))["is_test"])
        for unit in a["units"]:
            e = unit["evidence"]
            raw = (self.root/e["path"]).read_bytes()
            self.assertEqual(raw[e["start"]:e["end"]].decode("utf-8"), e["excerpt"])
        self.assertIn("calculate(2)", [u["evidence"]["excerpt"] for u in a["units"]])
        alias = next(u for u in a["units"] if u["evidence"]["excerpt"] == "compute(x)")
        self.assertEqual(alias["resolution_support"][0]["excerpt"], "from core import calculate as compute")
        self.assertEqual(alias["resolution_support"][1]["path"], "core.py")
        self.assertTrue(any(d["kind"] == "unresolved_call" for d in a["selection_receipt"]["diagnostics"]))
        self.assertTrue(validate_artifact(self.store, a)["valid"])

    def test_determinism_and_persistence(self):
        first = self.store.ingest(self.root)
        artifact = self.artifact()
        self.assertEqual(self.store.ingest(self.root), first)
        self.assertEqual(self.artifact(), artifact)
        self.store.close()
        self.store = GraphStore(self.db)
        self.assertEqual(self.artifact(), artifact)

    def test_updates_deletions_new_callers_match_fresh_rebuild(self):
        self.store.ingest(self.root)
        old = self.artifact()
        changes = [lambda: self.write("core.py", "def calculate(x):\n    return x + 7\n"),
                   lambda: self.write("new.py", "from core import calculate\ndef fresh():\n    return calculate(8)\n"),
                   lambda: (self.root/"service.py").unlink(),
                   lambda: self.write("unrelated.py", "def unrelated():\n    return 0\n")]
        for number, change in enumerate(changes):
            change()
            self.store.ingest(self.root)
            with GraphStore(self.base/f"fresh{number}.sqlite") as fresh:
                fresh.ingest(self.root)
                self.assertEqual(self.artifact(), compile_impact(fresh, "core.py::calculate"))
            expected = (["service.py::process", "test_service.py::test_process"] if number == 0 else
                        ["new.py::fresh", "service.py::process", "test_service.py::test_process"] if number == 1 else
                        ["new.py::fresh"])
            self.assertEqual([i["symbol"] for i in self.artifact()["impacts"]], expected)
            self.assertIn("return x + 7", self.artifact()["target"]["evidence"]["excerpt"])
        self.assertEqual([i["symbol"] for i in self.artifact()["impacts"]], ["new.py::fresh"])
        self.assertFalse(validate_artifact(self.store, old, expected_snapshot=self.store.current())["valid"])
        self.assertTrue(validate_artifact(self.store, old)["valid"])

    def test_tamper_and_rehashed_forgery_rejected(self):
        self.store.ingest(self.root)
        artifact = self.artifact()
        artifact["units"][0]["evidence"]["excerpt"] = "fabricated()"
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])
        artifact["id"] = identity("context", {k:v for k,v in artifact.items() if k != "id"})
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])
        self.assertFalse(validate_artifact(self.store, {"id": "bad"})["valid"])

    def test_source_corruption_rejected(self):
        self.store.ingest(self.root)
        artifact = self.artifact()
        with self.store.connection:
            self.store.connection.execute("UPDATE sources SET data=? WHERE path='service.py'", (b"corrupted",))
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])

    def test_target_integrity_even_without_callers(self):
        self.store.ingest(self.root)
        artifact = compile_impact(self.store, "unrelated.py::text")
        self.assertEqual(artifact["impacts"], [])
        with self.store.connection:
            self.store.connection.execute("UPDATE sources SET data=? WHERE path='unrelated.py'", (b"bad",))
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])

    def test_graph_row_corruption_rejected(self):
        self.store.ingest(self.root)
        artifact = self.artifact()
        with self.store.connection:
            self.store.connection.execute("DELETE FROM snapshot_assertions")
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])

    def test_reader_sees_old_snapshot_until_publish_commits(self):
        before = self.store.ingest(self.root)["snapshot"]
        self.write("new.py", "from core import calculate\ndef new():\n    return calculate(4)\n")
        with GraphStore(self.db) as reader:
            observations = []
            def during_publication(sql):
                if sql.startswith("INSERT OR REPLACE INTO metadata"):
                    observations.append(reader.current())
            self.store.connection.set_trace_callback(during_publication)
            after = self.store.ingest(self.root)["snapshot"]
            self.store.connection.set_trace_callback(None)
            self.assertEqual(observations, [before])
            self.assertEqual(reader.current(), after)
            self.assertNotEqual(before, after)

    def test_crlf_bom_and_multiline_call_coordinates(self):
        data = b'\xef\xbb\xbffrom core import calculate\r\ndef caller():\r\n    return calculate(\r\n        2\r\n    )\r\n'
        (self.root/"bom.py").write_bytes(data)
        self.store.ingest(self.root)
        unit = next(u for u in self.artifact()["units"] if u["evidence"]["path"] == "bom.py")
        self.assertEqual(unit["evidence"]["excerpt"], "calculate(\r\n        2\r\n    )")

    def test_no_source_execution_and_encoding_gap(self):
        self.write("hostile.py", 'raise RuntimeError("do not execute")\ndef innocent():\n    return 1\n')
        (self.root/"legacy.py").write_bytes(b'# coding: latin-1\n# \xe9\ndef legacy():\n    return 1\n')
        receipt = self.store.ingest(self.root)
        self.assertTrue(any(d["kind"] == "unsupported_encoding" for d in receipt["diagnostics"]))
        self.assertTrue(validate_artifact(self.store, self.artifact())["valid"])

    def test_failed_publication_rolls_back_and_restart_keeps_previous(self):
        before = self.store.ingest(self.root)["snapshot"]
        self.write("new.py", "from core import calculate\ndef new():\n    return calculate(1)\n")
        self.store.connection.execute("""CREATE TEMP TRIGGER fail_publish BEFORE INSERT ON snapshot_assertions
            BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.ingest(self.root)
        self.assertEqual(self.store.current(), before)
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)
        self.store.close()
        self.store = GraphStore(self.db)
        self.assertEqual(self.store.current(), before)
        self.assertEqual(len(self.artifact()["impacts"]), 2)

    def test_read_failure_does_not_publish(self):
        before = self.store.ingest(self.root)
        with patch("tunic.extract.Path.read_bytes", side_effect=OSError("unreadable")):
            with self.assertRaises(OSError):
                self.store.ingest(self.root)
        self.assertEqual(self.store.current(), before["snapshot"])

    def test_methods_resolve_self_and_constructor_calls(self):
        self.write("worker.py", "from core import calculate\n\nclass Worker:\n    def run(self):\n        return calculate(2)\n\ndef entry():\n    return Worker().run()\n")
        self.store.ingest(self.root)
        a = self.artifact()
        self.assertEqual([i["symbol"] for i in a["impacts"]],
                         ["service.py::process", "test_service.py::test_process",
                          "worker.py::Worker.run", "worker.py::entry"])
        observations = {u["id"]: u["assertion"]["proposition"] for u in a["units"]}
        paths = {i["symbol"]: i["derivation"]["premises"] for i in a["impacts"]}
        self.assertEqual([observations[p] for p in paths["worker.py::entry"]], [
            {"relation": "static_call", "caller": "worker.py::entry", "callee": "worker.py::Worker.run"},
            {"relation": "static_call", "caller": "worker.py::Worker.run", "callee": "core.py::calculate"}])
        self.assertTrue(validate_artifact(self.store, a)["valid"])

    def test_shadowing_dynamic_scopes_and_parse_errors(self):
        self.write("shadow.py", "from core import calculate\ndef wrong(calculate):\n    return calculate(9)\n")
        self.write("broken.py", "def bad(:\n")
        self.write("nested.py", "from core import calculate\ndef outer():\n    def inner():\n        return calculate(2)\n    return inner()\n")
        self.write("rebound.py", "from core import calculate\ncalculate = None\ndef wrong():\n    return calculate(3)\n")
        receipt = self.store.ingest(self.root)
        self.assertEqual([i["symbol"] for i in self.artifact()["impacts"]],
                         ["service.py::process", "test_service.py::test_process"])
        kinds = {d["kind"] for d in receipt["diagnostics"]}
        self.assertTrue({"unresolved_call", "parse_error", "nested_scope"} <= kinds)

    def test_relative_imports_and_duplicate_module_names(self):
        self.write("pkg/__init__.py", "")
        self.write("pkg/consumer.py", "from .leaf import operation as op\ndef use():\n    return op()\n")
        self.write("pkg/leaf.py", "def operation():\n    return 1\n")
        self.store.ingest(self.root)
        a = compile_impact(self.store, "pkg/leaf.py::operation")
        self.assertEqual([i["symbol"] for i in a["impacts"]], ["pkg/consumer.py::use"])
        self.write("pkg/leaf/__init__.py", "def operation():\n    return 2\n")
        self.store.ingest(self.root)
        self.assertEqual(compile_impact(self.store, "pkg/leaf.py::operation")["impacts"], [])

    def test_global_rebinding_blocks_static_resolution(self):
        self.write("mutable.py", "from core import calculate\ndef replace():\n    global calculate\n    calculate = None\ndef caller():\n    return calculate(2)\n")
        self.store.ingest(self.root)
        self.assertNotIn("mutable.py::caller", [i["symbol"] for i in self.artifact()["impacts"]])

    def test_cycles_and_bounds(self):
        self.write("cycle.py", "def a():\n    return b()\ndef b():\n    return a()\n")
        self.store.ingest(self.root)
        self.assertTrue(compile_impact(self.store, "cycle.py::a")["selection_receipt"]["cycles_observed"])
        bounded = self.artifact(max_depth=1)
        self.assertEqual([i["symbol"] for i in bounded["impacts"]], ["service.py::process"])
        self.assertEqual(bounded["selection_receipt"]["bounds_hit"], ["max_depth"])
        self.assertEqual(self.artifact(max_nodes=1)["selection_receipt"]["bounds_hit"], ["max_nodes"])
        with self.assertRaises(ValueError):
            self.artifact(max_depth=0)

    def test_empty_snapshot_unknown_symbol_and_wrong_repository(self):
        with self.assertRaises(ValueError):
            self.artifact()
        self.store.ingest(self.root)
        with self.assertRaises(ValueError):
            compile_impact(self.store, "missing.py::f")
        other = self.base/"other"
        other.mkdir()
        with self.assertRaises(ValueError):
            self.store.ingest(other)

    def test_cli_across_processes(self):
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "tunic", "--db", str(self.db), *args],
                                  capture_output=True, text=True, check=False)
        ingest = cli("ingest", str(self.root))
        self.assertEqual(ingest.returncode, 0, ingest.stderr)
        query = cli("impact", "core.py::calculate")
        self.assertEqual(query.returncode, 0, query.stderr)
        output = self.base/"artifact.json"
        output.write_text(query.stdout, encoding="utf-8")
        validation = cli("validate", str(output))
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertTrue(json.loads(validation.stdout)["valid"])
        output.write_text("{}", encoding="utf-8")
        self.assertEqual(cli("validate", str(output)).returncode, 1)
        self.assertEqual(cli("impact", "missing").returncode, 2)


if __name__ == "__main__":
    unittest.main()
