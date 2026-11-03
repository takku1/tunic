"""MMR diversity reorder: pure function plus answer() wiring."""

import tempfile
import unittest
from pathlib import Path

from tunic import retrieval
from tunic.memory import Memory


class MmrOrderTest(unittest.TestCase):
    def test_identity_at_lambda_one(self):
        fused = [(0.9, "a.py::x", {"exact": 1}),
                 (0.8, "a.py::y", {"name": 1}),
                 (0.5, "b.py::z", {"lexical": 1})]
        texts = {"a.py::x": "a x", "a.py::y": "a y", "b.py::z": "b z"}
        self.assertEqual(retrieval.mmr_order(fused, texts, lambda_=1.0),
                         fused)

    def test_diversity_prefers_other_file(self):
        fused = [(0.9, "a.py::x", {"lexical": 1}),
                 (0.85, "a.py::y", {"lexical": 2}),
                 (0.5, "b.py::z", {"lexical": 3})]
        texts = {"a.py::x": "a x", "a.py::y": "a y", "b.py::z": "b z"}
        ordered = [key for _, key, _ in
                   retrieval.mmr_order(fused, texts, lambda_=0.0)]
        self.assertEqual(ordered, ["a.py::x", "b.py::z", "a.py::y"])

    def test_practical_lambda_keeps_relevance_first(self):
        fused = [(0.9, "a.py::x", {"lexical": 1}),
                 (0.85, "a.py::y", {"lexical": 2}),
                 (0.5, "b.py::z", {"lexical": 3})]
        texts = {"a.py::x": "a x", "a.py::y": "a y", "b.py::z": "b z"}
        ordered = [key for _, key, _ in
                   retrieval.mmr_order(fused, texts, lambda_=0.7)]
        self.assertEqual(ordered[0], "a.py::x")

    def test_deterministic_and_total(self):
        fused = [(0.7, "b.py::m", {"lexical": 2}),
                 (0.7, "a.py::m", {"lexical": 1})]
        texts = {"a.py::m": "a m", "b.py::m": "b m"}
        first = retrieval.mmr_order(fused, texts, lambda_=0.5)
        second = retrieval.mmr_order(list(reversed(fused)), texts,
                                     lambda_=0.5)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)

    def test_empty_and_invalid(self):
        self.assertEqual(retrieval.mmr_order([], {}, lambda_=0.5), [])
        with self.assertRaises(ValueError):
            retrieval.mmr_order([(1.0, "a", {})], {"a": "a"},
                                lambda_=1.5)
        with self.assertRaises(ValueError):
            retrieval.mmr_order([(1.0, "a", {})], {"a": "a"},
                                lambda_=-0.1)


FILES = {
    "a.py": "def alpha():\n    return 1\n",
    "b.py": "def beta():\n    return alpha()\n",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    work.mkdir(parents=True, exist_ok=True)
    for name, text in FILES.items():
        (work / name).write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "mmr.sqlite"), str(work))
    memory.ingest()
    return memory


class MmrAnswerTest(unittest.TestCase):
    def test_default_is_identity_with_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("alpha", select=False)
                self.assertEqual(result["mmr"],
                                 {"lambda": 1.0, "applied": False})
            finally:
                memory.close()

    def test_invalid_lambda_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                with self.assertRaises(ValueError):
                    memory.answer("alpha", mmr_lambda=2.0)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
