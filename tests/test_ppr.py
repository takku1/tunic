"""Exact personalized PageRank: deterministic query-dependent ranking."""

import unittest

from tunic.retrieval import ppr_scores


class PprTest(unittest.TestCase):
    def test_chain_orders_by_distance(self):
        scores = ppr_scores(["a", "b", "c"],
                            [("a", "b"), ("b", "c")], ["a"])
        self.assertAlmostEqual(sum(scores.values()), 1.0)
        ordered = sorted(scores, key=lambda key: -scores[key])
        self.assertEqual(ordered, ["a", "b", "c"])

    def test_subject_outranks_dangling_sink(self):
        scores = ppr_scores(["a", "b"], [("a", "b")], ["a"])
        self.assertGreater(scores["a"], scores["b"])
        self.assertAlmostEqual(sum(scores.values()), 1.0)

    def test_deterministic(self):
        edges = [("a", "b"), ("b", "c"), ("c", "a"), ("a", "c")]
        first = ppr_scores(["a", "b", "c"], edges, ["a", "b"])
        second = ppr_scores(["c", "b", "a"], list(reversed(edges)),
                            ["b", "a"])
        self.assertEqual(first, second)

    def test_empty_graph_yields_teleport(self):
        self.assertEqual(ppr_scores([], [], ["a"]), {"a": 1.0})
        self.assertEqual(ppr_scores([], [], []), {})

    def test_isolated_subject_keeps_mass(self):
        scores = ppr_scores(["a", "b"], [], ["a"])
        self.assertAlmostEqual(sum(scores.values()), 1.0)
        for value in scores.values():
            self.assertFalse(value != value)


if __name__ == "__main__":
    unittest.main()
