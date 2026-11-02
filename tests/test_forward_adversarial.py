"""Adversarial audit of forward traversal.

Cycles, diamonds, truncation, policy-hidden edges, retractions,
candidate isolation, and cold-receipt honesty: every traversal
guarantee the runner will rely on, stressed against hostile graph
shapes. Deterministic fixtures, no model.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tunic.memory import Memory  # noqa: E402

sys.path.pop(0)

CORE = ("def x():\n"
        "    return z()\n"
        "\n\n"
        "def y():\n"
        "    return z()\n"
        "\n\n"
        "def z():\n"
        "    return 0\n"
        "\n\n"
        "def mid():\n"
        "    return leaf()\n"
        "\n\n"
        "def leaf():\n"
        "    return 0\n"
        "\n\n"
        "class Thing:\n"
        "    def start(self):\n"
        "        return 1\n")

ADV = ("from .core import Thing, mid, x, y\n"
       "\n\n"
       "def run():\n"
       "    x()\n"
       "    return y()\n"
       "\n\n"
       "def hop():\n"
       "    return mid()\n"
       "\n\n"
       "def mk():\n"
       "    return Thing()\n"
       "\n\n"
       "def run_cold():\n"
       "    return missing()\n")

LOOP = ("def s():\n"
        "    return s()\n")

FAN = ("def f1():\n"
       "    return 1\n"
       "\n\n"
       "def f2():\n"
       "    return 2\n"
       "\n\n"
       "def f3():\n"
       "    return 3\n"
       "\n\n"
       "def f4():\n"
       "    return 4\n"
       "\n\n"
       "def f5():\n"
       "    return 5\n"
       "\n\n"
       "def fan():\n"
       "    f1()\n"
       "    f2()\n"
       "    f3()\n"
       "    f4()\n"
       "    return f5()\n")

FILES = {"__init__.py": "", "core.py": CORE, "adv.py": ADV,
         "loop.py": LOOP, "fan.py": FAN}


def build(files, tmp):
    root = Path(tmp) / "repo"
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "sessions.sqlite"), root)
    memory.ingest()
    return memory


class ForwardAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.memory = build(FILES, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_diamond_reached_once_shortest_first(self):
        out = self.memory.callees("adv.py::run")
        self.assertEqual({d["callee"] for d in out["direct"]},
                         {"core.py::x", "core.py::y"})
        reached = {r["callee"]: r for r in out["reachable"]}
        self.assertEqual(set(reached), {"core.py::z"})
        self.assertEqual(reached["core.py::z"]["depth"], 2)
        self.assertEqual(len(reached["core.py::z"]["derivation"]["premises"]),
                         2)

    def test_self_loop_terminates_and_reports_cycle(self):
        out = self.memory.callees("loop.py::s")
        self.assertEqual(out["direct"], [])
        self.assertEqual(out["reachable"], [])
        self.assertTrue(out["selection_receipt"]["cycles_observed"])
        # The cycle leads only back to an already-reached node, so no
        # knowledge is missing: complete, with the cycle reported.
        self.assertEqual(out["outcome"], "complete")

    def test_max_nodes_truncates_with_receipt(self):
        out = self.memory.callees("fan.py::fan", max_nodes=3)
        self.assertIn("max_nodes",
                      out["selection_receipt"]["bounds_hit"])
        self.assertLess(len(out["direct"]) + len(out["reachable"]), 5)
        self.assertEqual(out["outcome"], "partial")

    def test_evidence_only_hides_candidates(self):
        full = self.memory.callees("adv.py::mk")
        self.assertTrue(full["candidates"])
        strict = self.memory.callees("adv.py::mk", policy="evidence_only")
        self.assertEqual(strict["candidates"], [])
        self.assertEqual(strict["direct"], [])

    def test_candidates_never_traversed(self):
        out = self.memory.callees("adv.py::mk")
        self.assertEqual(len(out["candidates"]), 1)
        self.assertEqual(out["candidates"][0]["callee"], "core.py::Thing")
        self.assertEqual(out["candidates"][0]["status"], "proposed")
        reached = {d["callee"] for d in out["direct"]} | \
            {r["callee"] for r in out["reachable"]}
        self.assertNotIn("core.py::Thing", reached)
        self.assertFalse([c for c in reached if "Thing" in c])

    def test_cold_receipt_is_exact_and_implies_nothing(self):
        out = self.memory.callees("adv.py::run_cold")
        self.assertEqual(len(out["cold"]), 1)
        receipt = out["cold"][0]
        self.assertEqual(receipt["caller"], "adv.py::run_cold")
        self.assertEqual(receipt["expression"], "missing")
        self.assertTrue(receipt["path"].endswith("adv.py"))
        self.assertIsInstance(receipt["line"], int)
        self.assertEqual(out["direct"], [])
        self.assertEqual(out["reachable"], [])
        self.assertEqual(out["outcome"], "partial")


class ForwardRetractionTests(unittest.TestCase):
    """A promoted-then-retracted claim traverses under transparent but
    disappears under current -- the runner grades against current
    knowledge. (Raw static_call observations are not retractable
    through the governed path; claims are.)"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.memory = build(FILES, cls.tmp)
        cls.memory.promote("adv.py::mk", "core.py::Thing",
                           authority="adversarial-test")
        cls.memory.ingest()
        promoted = cls.memory.callees("adv.py::mk", policy="current")
        cls.before = {r["callee"] for r in promoted["reachable"]}
        cls.memory.record_outcome("adv.py::mk", "core.py::Thing",
                                  rel="retracts", retract="depends_on",
                                  authority="adversarial-test")
        cls.memory.ingest()

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_promoted_claim_traverses_before_retraction(self):
        self.assertIn("core.py::Thing", self.before)

    def test_retracted_claim_hidden_under_current(self):
        current = self.memory.callees("adv.py::mk", policy="current")
        reached = ({d["callee"] for d in current["direct"]} |
                   {r["callee"] for r in current["reachable"]})
        self.assertNotIn("core.py::Thing", reached)

    def test_retracted_claim_visible_under_transparent(self):
        open_view = self.memory.callees("adv.py::mk", policy="transparent")
        reached = ({d["callee"] for d in open_view["direct"]} |
                   {r["callee"] for r in open_view["reachable"]})
        self.assertIn("core.py::Thing", reached)


if __name__ == "__main__":
    unittest.main()
