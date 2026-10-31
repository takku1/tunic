"""Hybrid retrieval: staged fusion, budgets, abstention, provider contract."""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.baselines import TextNotes
from tunic.memory import Memory
from tunic.retrieval import EmbeddingProvider, fuse


class StubProvider(EmbeddingProvider):
    """Deterministic test double: ranks candidates containing 'modern' first."""

    name = "stub"

    def available(self):
        return True

    def rank(self, query, candidates, top_k):
        ordered = sorted(candidates,
                         key=lambda text: (0 if "modern" in text else 1, text))
        return ordered[:max(top_k, 0)]


class FusionTests(unittest.TestCase):
    def test_exact_outranks_name_outranks_discovery(self):
        fused = fuse({"exact": ["b"], "name": ["a"], "lexical": ["c"],
                      "semantic": []})
        self.assertEqual([key for _, key, _ in fused], ["b", "a", "c"])

    def test_multi_stage_candidates_rank_first_and_ties_break_by_key(self):
        fused = fuse({"exact": [], "name": ["b", "a"], "lexical": ["a"],
                      "semantic": []})
        self.assertEqual([key for _, key, _ in fused], ["a", "b"])
        only_b = next(score for score, key, _ in fused if key == "b")
        only_a = next(score for score, key, _ in fused if key == "a")
        self.assertGreater(only_a, only_b)

    def test_total_and_threshold_bound_the_fusion(self):
        rankings = {"exact": [], "name": ["a", "b", "c"], "lexical": [],
                    "semantic": []}
        self.assertEqual(len(fuse(rankings, total=2)), 2)
        self.assertEqual(fuse(rankings, threshold=1.0), [])
        self.assertEqual(fuse(rankings, total=0), [])

    def test_unknown_stage_and_bad_threshold_rejected(self):
        with self.assertRaises(ValueError):
            fuse({"dense": ["a"]})
        with self.assertRaises(ValueError):
            fuse({"exact": ["a"]}, threshold=-1.0)


class AnswerRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(
            b"def calculate(value):\n    return value + 1\n")
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")

    def test_origins_stages_and_budgets_are_reported(self):
        memory = Memory(str(self.tmp / "graph.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        result = memory.answer("Which functions break if calculate changes?")
        self.assertEqual(result["fusion"], "weighted_rrf_k60")
        self.assertEqual(result["provider"], "none")
        self.assertTrue(result["stages"]["semantic"]["skipped"])
        self.assertEqual(result["candidates"][0]["match"], "name")
        self.assertIn("name", result["candidates"][0]["origins"])
        budgeted = memory.answer("Which functions break if calculate changes?",
                                 budget={"lexical": 0})
        self.assertEqual([c["symbol"] for c in budgeted["candidates"]],
                         ["core.py::calculate"])

    def test_threshold_abstains_and_empty_query_abstains(self):
        memory = Memory(str(self.tmp / "graph.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        high = memory.answer("Which functions break if calculate changes?",
                             threshold=1.0)
        self.assertEqual(high["outcome"], "empty")
        self.assertEqual(high["candidates"], [])
        empty = memory.answer("How do I bake bread?")
        self.assertEqual(empty["outcome"], "empty")

    def test_semantic_stage_proposes_but_never_asserts(self):
        memory = Memory(str(self.tmp / "graph.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.note("Modernize `core.py::calculate` for the new engine.",
                    document="notes/modern.md")
        memory.ingest()
        result = memory.answer("Which engine path is modern?",
                               provider=StubProvider())
        self.assertFalse(result["stages"]["semantic"]["skipped"])
        self.assertIn("semantic", result["candidates"][0]["origins"])
        self.assertEqual(result["claims"], [])
        self.assertTrue(all(entry["derivation"]["rule"] == "reverse_static_reachability_v1"
                            for entry in result["impacts"]))

    def test_bad_budget_limit_and_provider_shape_rejected(self):
        memory = Memory(str(self.tmp / "graph.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        with self.assertRaises(ValueError):
            memory.answer("Which functions break if calculate changes?",
                          budget={"dense": 2})
        with self.assertRaises(ValueError):
            memory.answer("Which functions break if calculate changes?",
                          budget={"lexical": -1})
        with self.assertRaises(ValueError):
            memory.answer("Which functions break if calculate changes?",
                          limit=-1)


class LexicalCacheTest(unittest.TestCase):
    def test_index_reused_until_tree_changes(self):
        import time
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        memory = Memory(str(root / "c.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        first = memory.answer("which code mentions alpha", limit=5,
                             budget={"exact": 0, "name": 0, "semantic": 0})
        index = memory._lexical_index(memory.store.current())
        second = memory.answer("which code mentions alpha", limit=5,
                              budget={"exact": 0, "name": 0, "semantic": 0})
        self.assertIs(memory._lexical_index(memory.store.current()), index)
        self.assertEqual(first["candidates"], second["candidates"])
        time.sleep(0.01)
        (root / "a.py").write_text("def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n",
                                   encoding="utf-8")
        # Touch mtime explicitly: coarse filesystems may keep the stamp.
        import os
        stamp = time.time() + 5
        os.utime(root / "a.py", (stamp, stamp))
        memory.answer("which code mentions beta", limit=5,
                        budget={"exact": 0, "name": 0, "semantic": 0})
        # Un-ingested code is never a candidate (unknown to the graph), but
        # the changed tree must still trigger a re-parse for parity with a
        # fresh BM25 build.
        self.assertIsNot(memory._lexical_index(memory.store.current()), index)
        from tunic.baselines import BM25
        fresh = BM25(root)
        cached = memory._lexical_index(memory.store.current())
        self.assertEqual(
            sorted(d["symbol"] for d in cached.documents),
            sorted(d["symbol"] for d in fresh.documents))


class TextNotesTest(unittest.TestCase):
    def test_notes_are_retrievable_and_code_is_not(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "outcome.md").write_text(
            "# Verified outcome\nrounding applies at order time", encoding="utf-8")
        (root / "code.py").write_text("x = 1\n", encoding="utf-8")
        notes = TextNotes(root)
        self.assertEqual([d["symbol"] for d in notes.documents], ["outcome.md"])
        hits = notes.search_text("verified rounding outcome")
        self.assertEqual([h["symbol"] for h in hits], ["outcome.md"])
        self.assertIn("rounding", hits[0]["text"])
        with self.assertRaises(ValueError):
            notes.search_text("   ")


if __name__ == "__main__":
    unittest.main()
