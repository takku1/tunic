"""Agent held-out evaluation: none vs lexical vs Tunic graph retrieval."""

import json
import tempfile
import unittest
from pathlib import Path

from tunic.baselines import BM25
from tunic.eval import CONDITIONS, load_agent_suite, run_agent_suite

SUITE = Path(__file__).resolve().parents[1] / "benchmarks" / "agent" / "suite.json"


class AgentSuiteTests(unittest.TestCase):
    def test_conditions_beat_abstention_and_graph_beats_lexical(self):
        result = run_agent_suite(SUITE)
        self.assertEqual(result["suite"], "agent-heldout-v2")
        self.assertEqual(set(result["conditions"]), set(CONDITIONS))
        scores = {name: (row["passed"], row["total"])
                  for name, row in result["conditions"].items()}
        self.assertEqual(scores["none"], (0, 7))
        self.assertEqual(scores["lexical"], (6, 7))
        self.assertEqual(scores["tunic"], (7, 7))
        by_id = {t["id"]: t for t in result["conditions"]["tunic"]["tasks"]}
        stale = by_id["stale-task"]
        self.assertTrue(stale["pass"])
        self.assertNotIn("api.py::route", stale["returned"])
        conflict = by_id["conflict-task"]
        self.assertIn("contradicts", conflict["evidence_kinds"])
        outcome = next(t for t in result["conditions"]["tunic"]["tasks"]
                       if t["id"] == "outcome-task")
        self.assertTrue(outcome["pass"])
        self.assertIn("depends_on", outcome["evidence_kinds"])
        lexical_gap = next(t for t in result["conditions"]["lexical"]["tasks"]
                           if t["id"] == "outcome-task")
        self.assertFalse(lexical_gap["pass"])
        for name, row in result["conditions"].items():
            self.assertGreaterEqual(row["total_latency_ms"], 0.0)
            for task in row["tasks"]:
                self.assertGreaterEqual(task["latency_ms"], 0.0)
                self.assertGreater(task["context_chars"], 0)

    def test_stale_memory_excluded_only_under_current_policy(self):
        import shutil
        from tunic.memory import Memory
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        shutil.copytree(SUITE.parent / "evolve", root)
        memory = Memory(str(tmp / "agent.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        question = "Which functions break if handle_legacy changes?"
        open_view = memory.answer(question)
        self.assertIn("api.py::route",
                      [row["symbol"] for row in open_view["candidates"]]
                      + [entry["symbol"] for entry in open_view["impacts"]])
        current = memory.answer(question, policy="current")
        self.assertNotIn("api.py::route",
                         [row["symbol"] for row in current["candidates"]]
                         + [entry["symbol"] for entry in current["impacts"]])
        with self.assertRaises(ValueError):
            memory.answer(question, policy="trust-me")

    def test_suite_rejects_malformed_fixtures(self):
        bad = json.loads(SUITE.read_text(encoding="utf-8-sig"))
        bad["version"] = 99
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(bad, handle)
            version_path = handle.name
        with self.assertRaises(ValueError):
            load_agent_suite(version_path)
        bad["version"] = 2
        bad["tasks"] = []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(bad, handle)
            empty_path = handle.name
        with self.assertRaises(ValueError):
            load_agent_suite(empty_path)

    def test_search_text_ranks_task_words_and_validates(self):
        index = BM25(SUITE.parent / "repo")
        hits = index.search_text("Which checkout step finalizes the order?")
        self.assertEqual(hits[0]["symbol"], "checkout.py::place_order")
        self.assertEqual(index.search_text("How do I bake bread?"), [])
        with self.assertRaises(ValueError):
            index.search_text("   ")
        with self.assertRaises(ValueError):
            index.search_text("checkout", top_k=0)


if __name__ == "__main__":
    unittest.main()
