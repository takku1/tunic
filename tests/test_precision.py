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

    def test_structural_signals_detected_from_key_and_line(self):
        from tunic.extract import _context_hits
        self.assertEqual(
            _context_hits("The Parser.parse routine handles input.",
                          "pkg/mod.py::Parser.parse"), (True, False, False))
        self.assertEqual(
            _context_hits("see pkg/mod.py for the render helper.",
                          "pkg/mod.py::render"), (False, True, False))
        self.assertEqual(
            _context_hits("  - defs: render, parse", "pkg/mod.py::render"),
            (False, False, True))
        self.assertEqual(
            _context_hits("Read the documentation first.", "core.py::read"),
            (False, False, False))

    def test_class_context_qualifies_without_frequency(self):
        from tunic.extract import _mention_signals, _requalify
        signals = _mention_signals("The Parser.parse routine handles input.",
                                   "parse", False, False, True, False, False)
        self.assertIn("class_context", signals)
        self.assertTrue(_requalify("parse", "The Parser.parse routine handles input.",
                                  "src", "pkg/mod.py::Parser.parse",
                                  {"parse": 1.0}, set()))

    def test_structural_signals_qualify_low_frequency_leaves(self):
        from tunic.extract import _requalify
        self.assertTrue(_requalify("render", "see pkg/mod.py for the render helper.",
                                  "src", "pkg/mod.py::render",
                                  {"render": 1 / 21}, set()))
        self.assertTrue(_requalify("render", "  - defs: render, parse",
                                  "src", "pkg/mod.py::render",
                                  {"render": 1 / 21}, set()))
        self.assertFalse(_requalify("read", "Read the documentation first.",
                                   "src", "core.py::read", {"read": 0.9}, set()))

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


class StructuralSignalIntegrationTests(unittest.TestCase):
    """End-to-end: class, file-path, and inventory signals qualify evidence.

    Twenty filler documents keep every leaf at document frequency 1/21,
    under the 0.05 gate, so the signals — not frequency — decide.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        (self.root / "pkg").mkdir(parents=True)
        (self.root / "pkg" / "mod.py").write_text(
            "class Parser:\n"
            "    def parse(self):\n"
            "        return 1\n"
            "\n"
            "\n"
            "def render():\n"
            "    return 2\n")
        (self.root / "guide.md").write_text(
            "The Parser.parse routine handles input.\n"
            "see pkg/mod.py for the render helper.\n"
            "  - defs: render\n")
        for i in range(20):
            (self.root / f"filler{i:02d}.md").write_text(
                f"Unrelated filler note number {i} about release process.\n")
        self.store = GraphStore(str(self.tmp / "graph.sqlite"))
        self.addCleanup(self.store.close)
        self.store.ingest(str(self.root))

    def test_signals_become_evidence_with_trace(self):
        _, view = self.store.view()
        bare = [a for a in view["assertions"]
                if a.get("resolution", {}).get("rule") == "name_match_proposed_v1"]
        by_callee = {}
        for a in bare:
            by_callee.setdefault(a["proposition"]["callee"], []).append(a)
        parse = by_callee.get("pkg/mod.py::Parser.parse", [])
        self.assertEqual(len(parse), 1)
        self.assertIn("class_context",
                      parse[0]["resolution"]["qualification"]["signals"])
        render = by_callee.get("pkg/mod.py::render", [])
        self.assertEqual(len(render), 2)
        signals = [r["resolution"]["qualification"]["signals"] for r in render]
        self.assertTrue(any("module_path_context" in s for s in signals))
        self.assertTrue(any("inventory_list" in s for s in signals))


if __name__ == "__main__":
    unittest.main()
