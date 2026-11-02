"""Forward-callee traversal: direct, bounded-reachable, and candidate callees.

Built on the governed assertion set (never a second interpretation of
calls): ``static_call`` edges traverse, proposed ``depends_on``
candidates are listed but never traversed, and unresolved call sites
surface as cold-frontier receipts instead of silent stops.
Deterministic, no model.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tunic.compiler import compile_callees  # noqa: E402
from tunic.mcp import handle_call  # noqa: E402
from tunic.memory import Memory  # noqa: E402

sys.path.pop(0)

CORE = ("def leaf():\n"
        "    return 0\n"
        "\n\n"
        "def mid():\n"
        "    return leaf()\n"
        "\n\n"
        "class Gadget:\n"
        "    def start(self):\n"
        "        return 1\n")

AGENT = ("from .core import Gadget, mid\n"
         "\n\n"
         "def run():\n"
         "    return mid()\n"
         "\n\n"
         "def make():\n"
         "    return Gadget()\n"
         "\n\n"
         "def run_cold():\n"
         "    return missing()\n")

LOOP = ("def a():\n"
        "    return b()\n"
        "\n\n"
        "def b():\n"
        "    return a()\n")

FILES = {"__init__.py": "", "core.py": CORE, "agent.py": AGENT,
         "loop.py": LOOP}


def build(files, tmp):
    root = Path(tmp) / "repo"
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "sessions.sqlite"), root)
    memory.ingest()
    return memory


class ForwardCalleeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.memory = build(FILES, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def result(self, symbol, **kwargs):
        return compile_callees(self.memory.store, symbol, **kwargs)

    def test_direct_callees_carry_evidence_and_status(self):
        out = self.result("agent.py::run")
        direct = {d["callee"]: d for d in out["direct"]}
        self.assertEqual(set(direct), {"core.py::mid"})
        entry = direct["core.py::mid"]
        self.assertEqual(entry["status"], "syntactic_observation")
        self.assertEqual(entry["evidence"]["path"], "agent.py")
        support = entry["resolution_support"]
        self.assertTrue(any(s["path"] == "agent.py" and s["start"] == 0
                            for s in support),
                        "import statement must be cited")

    def test_reachable_is_bounded_by_depth(self):
        full = self.result("agent.py::run")
        reached = {r["callee"]: r for r in full["reachable"]}
        self.assertIn("core.py::leaf", reached)
        self.assertEqual(reached["core.py::leaf"]["depth"], 2)
        shallow = self.result("agent.py::run", max_depth=1)
        self.assertNotIn("core.py::leaf",
                         {r["callee"] for r in shallow["reachable"]})
        self.assertIn("max_depth", shallow["selection_receipt"]["bounds_hit"])

    def test_cycles_terminate_and_are_reported(self):
        out = self.result("loop.py::a")
        self.assertEqual({d["callee"] for d in out["direct"]}, {"loop.py::b"})
        self.assertTrue(out["selection_receipt"]["cycles_observed"])

    def test_candidates_listed_never_traversed(self):
        out = self.result("agent.py::make")
        self.assertEqual(out["direct"], [])
        self.assertEqual(out["reachable"], [])
        self.assertEqual(len(out["candidates"]), 1)
        cand = out["candidates"][0]
        self.assertEqual(cand["callee"], "core.py::Gadget")
        self.assertEqual(cand["status"], "proposed")
        self.assertIn("evidence", cand)
        self.assertIn("resolution", cand)

    def test_cold_receipt_names_unresolved_call(self):
        out = self.result("agent.py::run_cold")
        self.assertEqual(out["direct"], [])
        self.assertEqual(len(out["cold"]), 1)
        receipt = out["cold"][0]
        self.assertEqual(receipt["caller"], "agent.py::run_cold")
        self.assertEqual(receipt["expression"], "missing")
        self.assertTrue(receipt["path"].endswith("agent.py"))
        self.assertIn("needed", receipt)

    def test_unknown_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.result("agent.py::nope")

    def test_budgets_validated(self):
        with self.assertRaises(ValueError):
            self.result("agent.py::run", max_depth=0)
        with self.assertRaises(ValueError):
            self.result("agent.py::run", max_nodes=0)

    def test_memory_wrapper_authorizes(self):
        out = self.memory.callees("agent.py::run")
        self.assertEqual({d["callee"] for d in out["direct"]},
                         {"core.py::mid"})
        foreign = Memory(str(Path(self.tmp) / "foreign.sqlite"),
                         self.memory.root, principal="mallory")
        try:
            with self.assertRaises(PermissionError):
                foreign.callees("agent.py::run")
        finally:
            foreign.close()

    def test_mcp_tool_round_trip_and_validation(self):
        out = handle_call(self.memory, "callees", {"symbol": "agent.py::run"})
        self.assertEqual({d["callee"] for d in out["direct"]},
                         {"core.py::mid"})
        with self.assertRaises(ValueError):
            handle_call(self.memory, "callees", {"symbol": "agent.py::run",
                                                 "max_depth": 0})
        with self.assertRaises(ValueError):
            handle_call(self.memory, "callees", {})


if __name__ == "__main__":
    unittest.main()
