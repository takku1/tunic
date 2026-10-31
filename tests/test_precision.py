"""Precision benchmark collateral: baseline arms and mention-taxonomy pins.

The popularity/pagerank arms are the static reference methods Tunic must
beat on the frozen MochiOS split. The taxonomy test pins what the current
bare-leaf matcher does on ordinary prose; the precision gate is supposed
to change that behavior, at which point this test's expectations change
with an explanation, not a silent edit.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.baselines import pagerank, popularity
from tunic.store import GraphStore


class BaselineArmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        shutil.copytree(Path("benchmarks/precision/cases/fanout"), self.root)
        self.store = GraphStore(str(self.tmp / "graph.sqlite"))
        self.addCleanup(self.store.close)
        self.report = self.store.ingest(str(self.root))

    def test_popularity_ranks_most_mentioned_first(self):
        first = popularity(self.store, self.report["snapshot"], top_k=3)
        second = popularity(self.store, self.report["snapshot"], top_k=3)
        self.assertEqual(first, second)
        # Precision gate: bare prose verbs are candidates, not assertions,
        # so popularity ranks only exact evidence: calculate_total x2
        # across two lines, checkout x1. Raw fan-out volume is measured
        # on the frozen MochiOS split, not here.
        self.assertEqual([row["symbol"] for row in first],
                         ["core.py::calculate_total", "service.py::checkout"])
        counts = [row["score"] for row in first]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_popularity_rejects_bad_top_k(self):
        with self.assertRaises(ValueError):
            popularity(self.store, self.report["snapshot"], top_k=0)

    def test_pagerank_is_deterministic_and_normalized(self):
        first = pagerank(self.store, self.report["snapshot"], top_k=5)
        second = pagerank(self.store, self.report["snapshot"], top_k=5)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 5)
        scores = [row["score"] for row in first]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertAlmostEqual(sum(row["score"] for row in pagerank(
            self.store, self.report["snapshot"], top_k=1000)), 1.0, places=6)

    def test_pagerank_validates_parameters(self):
        with self.assertRaises(ValueError):
            pagerank(self.store, self.report["snapshot"], top_k=0)
        with self.assertRaises(ValueError):
            pagerank(self.store, self.report["snapshot"], damping=1.5)
        with self.assertRaises(ValueError):
            pagerank(self.store, self.report["snapshot"], damping=True)

    def test_exact_reference_is_strong_evidence(self):
        _, view = self.store.view()
        exact = [a for a in view["assertions"]
                 if a.get("resolution", {}).get("rule") == "exact_key_match_v1"]
        self.assertTrue(any(a["proposition"]["callee"] == "service.py::checkout"
                            for a in exact))

    def test_ordinary_prose_becomes_candidate_not_evidence(self):
        """Precision gate: 'read the runbook' is a candidate, not an edge.

        Changed deliberately by the evidence-precision milestone: a bare
        common verb with no corroborating signal must not become a graph
        assertion. The candidate stays inspectable with its frequency
        and (empty) signal list.
        """
        from tunic.compiler import compile_review
        _, view = self.store.view()
        bare = [a for a in view["assertions"]
                if a.get("resolution", {}).get("rule") == "name_match_proposed_v1"
                and a["proposition"].get("callee") == "core.py::read"]
        self.assertEqual(bare, [])
        candidates = [d for d in view["diagnostics"]
                      if d.get("kind") == "mention_candidate"
                      and d.get("callee") == "core.py::read"]
        self.assertTrue(candidates)
        self.assertTrue(all(c["document_frequency"] > 0.05 or c["signals"] == []
                            for c in candidates))
        report = compile_review(self.store, "notes.md")
        self.assertTrue(any(c["callee"] == "core.py::read"
                            for c in report["candidates"]))
        self.assertFalse(any(p.get("candidate") == "core.py::read"
                             for p in report["proposals"]))


if __name__ == "__main__":
    unittest.main()
