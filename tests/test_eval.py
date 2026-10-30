"""Agent held-out evaluation: none vs lexical vs Tunic graph retrieval."""

import json
import unittest
from pathlib import Path

from tunic.baselines import BM25
from tunic.eval import CONDITIONS, load_agent_suite, run_agent_suite

SUITE = Path(__file__).resolve().parents[1] / "benchmarks" / "agent" / "suite.json"


class AgentSuiteTests(unittest.TestCase):
    def test_conditions_beat_abstention_and_graph_beats_lexical(self):
        result = run_agent_suite(SUITE)
        self.assertEqual(result["suite"], "agent-heldout-v1")
        self.assertEqual(set(result["conditions"]), set(CONDITIONS))
        scores = {name: (row["passed"], row["total"])
                  for name, row in result["conditions"].items()}
        self.assertEqual(scores["none"], (0, 4))
        self.assertEqual(scores["lexical"], (3, 4))
        self.assertEqual(scores["tunic"], (4, 4))
        outcome = next(t for t in result["conditions"]["tunic"]["tasks"]
                       if t["id"] == "outcome-task")
        self.assertTrue(outcome["pass"])
        self.assertIn("depends_on", outcome["evidence_kinds"])
        decision = next(t for t in result["conditions"]["tunic"]["tasks"]
                        if t["id"] == "decision-task")
        self.assertIn("mention", decision["evidence_kinds"])
        lexical_gap = next(t for t in result["conditions"]["lexical"]["tasks"]
                           if t["id"] == "outcome-task")
        self.assertFalse(lexical_gap["pass"])

    def test_suite_rejects_malformed_fixtures(self):
        import tempfile
        bad = json.loads(SUITE.read_text(encoding="utf-8-sig"))
        bad["version"] = 2
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(bad, handle)
            version_path = handle.name
        with self.assertRaises(ValueError):
            load_agent_suite(version_path)
        bad["version"] = 1
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
