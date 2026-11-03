"""Graph expansion stage: one-hop dependency neighborhood as fused candidates."""

import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory

FILES = {
    "a.py": "from b import beta\ndef alpha():\n    return beta()\n",
    "b.py": "from c import gamma\ndef beta():\n    return gamma()\n",
    "c.py": "def gamma():\n    return 1\n",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    work.mkdir(parents=True, exist_ok=True)
    for name, text in FILES.items():
        (work / name).write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "expand.sqlite"), str(work))
    memory.ingest()
    return memory


def _symbols(result):
    return [entry["symbol"] for entry in result["candidates"]]


class ExpansionStageTest(unittest.TestCase):
    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("alpha", select=False)
                self.assertTrue(result["stages"]["graph"]["skipped"])
                self.assertEqual(
                    result["stages"]["graph"]["candidates"], 0)
                self.assertNotIn("b.py::beta", _symbols(result))
            finally:
                memory.close()

    def test_budget_adds_one_hop_neighbor(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("alpha", select=False,
                                       budget={"graph": 5})
                self.assertFalse(result["stages"]["graph"]["skipped"])
                self.assertIn("b.py::beta", _symbols(result))
                entry = [entry for entry in result["candidates"]
                         if entry["symbol"] == "b.py::beta"][0]
                self.assertIn("graph", entry["origins"])
            finally:
                memory.close()

    def test_two_hop_neighbor_reached(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("alpha", select=False,
                                       budget={"graph": 5})
                symbols = [entry["symbol"] for entry in
                           result["candidates"]]
                self.assertIn("c.py::gamma", symbols)
                entry = [entry for entry in result["candidates"]
                         if entry["symbol"] == "c.py::gamma"][0]
                self.assertIn("graph", entry["origins"])
            finally:
                memory.close()

    def test_budget_caps_neighbors(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("alpha", select=False,
                                       budget={"graph": 0})
                self.assertNotIn("b.py::beta", _symbols(result))
            finally:
                memory.close()

    def test_expansion_flows_through_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("alpha", select=True,
                                       budget={"graph": 5})
                trace = result["selection"]["trace"]["decisions"]
                self.assertTrue([d for d in trace
                                 if d.get("symbol") == "b.py::beta"])
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
