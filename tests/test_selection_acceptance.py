"""Selection acceptance evaluation, pinned to the frozen artifact.

Executes the acceptance clause from docs/architecture/evidence-selection.md
against benchmarks/arena/runs/semantic-selection-v1.json (frozen,
never tuned): precision and F1 must rise above the current hybrid
baseline with Recall@1/@3 held, negatives must behave, and no
threshold sweep may explain the gain.

A PASS here licenses documenting the verdict -- it does not flip the
default pipeline. Adoption remains a separate decision.
"""
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = (REPO / "benchmarks" / "arena" / "runs" /
            "semantic-selection-v1.json")


def load():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


class SelectionAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = load()
        cls.suite = cls.record["suite_summary"]
        cls.heldout = cls.record["heldout_summary"]

    def test_frozen_artifact_identity(self):
        self.assertEqual(self.record["benchmark"], "selection-v1")
        for section in (self.suite, self.heldout):
            self.assertEqual(set(section),
                             {"bm25-only", "full-hybrid", "hybrid-select"})

    def test_precision_and_f1_rise_above_hybrid(self):
        for section in (self.suite, self.heldout):
            base = section["full-hybrid"]
            selected = section["hybrid-select"]
            self.assertGreater(selected["micro_precision"],
                               base["micro_precision"])
            self.assertGreater(selected["micro_f1"], base["micro_f1"])

    def test_recall_held_or_better(self):
        for section in (self.suite, self.heldout):
            base = section["full-hybrid"]
            selected = section["hybrid-select"]
            self.assertGreaterEqual(selected["mean_recall_at_1"],
                                    base["mean_recall_at_1"])
            self.assertGreaterEqual(selected["mean_recall_at_3"],
                                    base["mean_recall_at_3"])

    def test_negatives_behave_identically(self):
        # Selection reclassifies within one candidate set; the
        # threshold sweep must move both hybrid arms together, so no
        # sweep explains the select gain.
        for row in self.record["threshold_sweep"]:
            arms = row["arms"]
            self.assertEqual(arms["full-hybrid"], arms["hybrid-select"])

    def test_scope_recorded_not_hidden(self):
        # Scoped verdict: select beats the hybrid pipeline it filters
        # on every clause metric, and beats BM25 on suite recall --
        # but BM25 still leads suite precision/F1, so this is not a
        # general retrieval claim.
        suite = self.suite
        self.assertGreater(suite["hybrid-select"]["mean_recall_at_1"],
                           suite["bm25-only"]["mean_recall_at_1"])
        self.assertGreater(suite["bm25-only"]["micro_f1"],
                           suite["hybrid-select"]["micro_f1"])
        self.assertGreater(suite["bm25-only"]["micro_precision"],
                           suite["hybrid-select"]["micro_precision"])
        self.assertIn("14 small synthetic suite tasks",
                      self.record["limitations"][0])


if __name__ == "__main__":
    unittest.main()
