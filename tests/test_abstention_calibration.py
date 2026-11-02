"""Abstention calibration: verified absence vs unknown vs cold.

Pins the evidence primitives an agent must use before asserting:
an indexed-but-isolated symbol yields empty relations (verified
absence); an unindexed symbol raises or lands in unknown_symbols
(unknown, never asserted); an unresolvable call yields a cold
receipt (insufficient evidence, not a negative); ambiguous names
are kept, never silently dropped. Deterministic fixtures, no model.
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

CORE = ("def cover(table):\n"
        "    return table\n"
        "\n\n"
        "def lonely():\n"
        "    return 0\n")

SHOP = ("from core import cover\n"
        "\n\n"
        "def run(table):\n"
        "    return cover(table)\n"
        "\n\n"
        "def user(name):\n"
        "    return missing(name)\n")

BILLING = ("def cover(log):\n"
           "    return log\n"
           "\n\n"
           "def invoice(order):\n"
           "    return cover(order)\n")

FILES = {"__init__.py": "", "core.py": CORE, "shop.py": SHOP,
         "billing.py": BILLING}


class AbstentionCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        root = Path(cls.tmp) / "repo"
        root.mkdir()
        for name, text in FILES.items():
            (root / name).write_text(text, encoding="utf-8")
        cls.memory = Memory(str(Path(cls.tmp) / "cal.sqlite"), root)
        cls.memory.ingest()

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_verified_absence_is_empty_not_error(self):
        related = self.memory.related("core.py::lonely")
        self.assertEqual(related["impact"]["impacts"], [])
        self.assertEqual(related["claims"]["claims"], [])
        self.assertEqual(related["mentions"]["mentions"], [])

    def test_unknown_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.memory.related("nope.py::Missing")

    def test_unknown_symbol_listed_not_asserted(self):
        out = self.memory.answer("nope.py::Missing handles refunds",
                                 limit=5)
        self.assertIn("nope.py::Missing", out["unknown_symbols"])
        self.assertNotIn("nope.py::Missing",
                         [c["symbol"] for c in out["candidates"]])

    def test_decoy_has_no_forward_or_reverse_edge(self):
        fwd = self.memory.callees("shop.py::run")
        reached = ({d["callee"] for d in fwd["direct"]} |
                   {r["callee"] for r in fwd["reachable"]})
        self.assertNotIn("billing.py::cover", reached)

    def test_unsupported_call_yields_cold_receipt(self):
        out = self.memory.callees("shop.py::user")
        self.assertEqual(out["direct"], [])
        self.assertEqual(len(out["cold"]), 1)
        receipt = out["cold"][0]
        self.assertEqual(receipt["caller"], "shop.py::user")
        self.assertEqual(receipt["expression"], "missing")
        self.assertEqual(out["outcome"], "partial")

    def test_ambiguous_names_kept_not_dropped(self):
        out = self.memory.answer("cover implementation", limit=5)
        got = {c["symbol"] for c in out["candidates"]}
        self.assertLessEqual({"core.py::cover", "billing.py::cover"}, got)


if __name__ == "__main__":
    unittest.main()
