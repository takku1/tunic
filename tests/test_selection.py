"""Selection layer: intent, outcomes, traces, and answer() opt-in.

Rules are structural, never tuned cutoffs: identity in the query or a
graph tie is required to return a candidate, contradictions are flagged
rather than silenced, supersession excludes only under the current
policy. No frozen gold labels are used here.
"""

import unittest

from tunic.selection import OUTCOMES, candidate_evidence, classify_intent, select


def assertion(caller, callee, relation="static_call", status="syntactic_observation"):
    return {"proposition": {"relation": relation, "caller": caller, "callee": callee},
            "source": "s", "start": 0, "end": 1, "status": status,
            "resolution": {"rule": "test"}, "producer": "test"}


def view_for(symbols, assertions):
    return {"symbols": [{"key": key} for key in symbols],
            "assertions": assertions}


class IntentTests(unittest.TestCase):
    def test_impact_markers(self):
        for task in ("What breaks if I change checkout?",
                     "Which callers depend on rounding?",
                     "What is the blast radius of this edit?"):
            result = classify_intent(task)
            self.assertEqual(result["intent"], "impact", task)
            self.assertTrue(result["reasons"])

    def test_provenance_markers(self):
        for task in ("Who decided the retry policy?",
                     "Why was caching removed?",
                     "Is this decision superseded?"):
            result = classify_intent(task)
            self.assertEqual(result["intent"], "provenance", task)

    def test_locator_default(self):
        result = classify_intent("Who computes late fees?")
        self.assertEqual(result["intent"], "locator")
        self.assertTrue(result["reasons"])


class SelectTests(unittest.TestCase):
    def test_exact_match_supported_without_ties(self):
        view = view_for(["a.py::lonely"], [])
        fused = [(0.05, "a.py::lonely", {"exact": 1})]
        result = select("change a.py::lonely now", fused, view=view,
                        policy="transparent", limit=3)
        self.assertEqual(result["kept"], ["a.py::lonely"])
        decision = result["trace"]["decisions"][0]
        self.assertEqual(decision["outcome"], "supported")
        self.assertFalse(result["trace"]["abstained"])

    def test_tieless_discovery_abstains_with_trace(self):
        view = view_for(["a.py::stray", "a.py::other"], [])
        fused = [(0.02, "a.py::stray", {"lexical": 1}),
                 (0.01, "a.py::other", {"semantic": 2})]
        result = select("quantum banana firmware", fused, view=view,
                        policy="transparent", limit=3)
        self.assertEqual(result["kept"], [])
        self.assertTrue(result["trace"]["abstained"])
        self.assertEqual(result["trace"]["abstention_reason"],
                         "no candidate survived selection")
        self.assertTrue(all(d["outcome"] == "insufficient_evidence"
                            for d in result["trace"]["decisions"]))

    def test_corroborated_semantic_win_is_supported(self):
        view = view_for(["lib.py::overdue_fine"],
                        [assertion("lib.py::checkout", "lib.py::overdue_fine",
                                   "depends_on", "asserted_in_source")])
        fused = [(0.008, "lib.py::overdue_fine", {"semantic": 1})]
        result = select("Who computes late fees?", fused, view=view,
                        policy="transparent", limit=3)
        self.assertEqual(result["kept"], ["lib.py::overdue_fine"])
        self.assertEqual(result["trace"]["decisions"][0]["outcome"], "supported")

    def test_impact_asserted_only_is_proposed_not_supported(self):
        view = view_for(["a.py::x", "a.py::y"],
                        [assertion("a.py::x", "a.py::y", "depends_on",
                                   "asserted_in_source")])
        fused = [(0.03, "a.py::y", {"lexical": 1})]
        result = select("What breaks if I change the input?", fused, view=view,
                        policy="transparent", limit=3)
        self.assertEqual(result["kept"], ["a.py::y"])
        self.assertEqual(result["trace"]["decisions"][0]["outcome"], "proposed")

    def test_impact_observed_is_supported(self):
        view = view_for(["a.py::x", "a.py::y"], [assertion("a.py::x", "a.py::y")])
        fused = [(0.03, "a.py::y", {"lexical": 1})]
        result = select("What breaks if I change the input?", fused, view=view,
                        policy="transparent", limit=3)
        self.assertEqual(result["trace"]["decisions"][0]["outcome"], "supported")

    def test_contradicted_is_kept_and_flagged(self):
        view = view_for(["a.py::x", "a.py::y"],
                        [assertion("a.py::x", "a.py::y"),
                         assertion("a.py::x", "a.py::y", "contradicts",
                                   "asserted_in_source")])
        fused = [(0.03, "a.py::y", {"lexical": 1})]
        result = select("What breaks if I change the input?", fused, view=view,
                        policy="transparent", limit=3)
        self.assertEqual(result["kept"], ["a.py::y"])
        decision = result["trace"]["decisions"][0]
        self.assertEqual(decision["outcome"], "contradicted")

    def test_superseded_excluded_only_under_current(self):
        view = view_for(["a.py::old", "a.py::new"],
                        [assertion("a.py::old", "a.py::new"),
                         {"proposition": {"relation": "supersedes", "caller": "a.py::new",
                                          "callee": "a.py::old"},
                          "source": "s", "start": 0, "end": 1,
                          "status": "asserted_in_source",
                          "resolution": {"rule": "test"}, "producer": "test"}])
        fused = [(0.03, "a.py::old", {"lexical": 1})]
        current = select("Where is the old handler?", fused, view=view,
                         policy="current", limit=3)
        self.assertEqual(current["kept"], [])
        self.assertEqual(current["trace"]["decisions"][0]["outcome"], "out_of_scope")
        open_policy = select("Where is the old handler?", fused, view=view,
                             policy="transparent", limit=3)
        self.assertEqual(open_policy["kept"], ["a.py::old"])

    def test_shared_leaf_is_ambiguous_and_kept(self):
        view = view_for(["a.py::C.run", "b.py::C.run"],
                        [assertion("x.py::q", "a.py::C.run")])
        fused = [(0.04, "a.py::C.run", {"name": 1})]
        result = select("Where does run execute?", fused, view=view,
                        policy="transparent", limit=3)
        decision = result["trace"]["decisions"][0]
        self.assertEqual(decision["outcome"], "ambiguous")
        self.assertEqual(result["kept"], ["a.py::C.run"])

    def test_limit_truncates_and_outcomes_are_valid(self):
        view = view_for(["a.py::x", "a.py::y", "a.py::z"],
                        [assertion("a.py::x", "a.py::y"),
                         assertion("a.py::y", "a.py::z")])
        fused = [(0.05, "a.py::y", {"name": 1}),
                 (0.04, "a.py::z", {"lexical": 1})]
        first = select("name a.py::y and related z", fused, view=view,
                       policy="transparent", limit=1)
        self.assertEqual(first["kept"], ["a.py::y"])
        for decision in first["trace"]["decisions"]:
            self.assertIn(decision["outcome"], OUTCOMES)
            self.assertTrue(decision["reasons"])
            self.assertIn("graph_ties", decision["evidence"])

    def test_deterministic(self):
        view = view_for(["a.py::x", "a.py::y"], [assertion("a.py::x", "a.py::y")])
        fused = [(0.03, "a.py::y", {"lexical": 1, "semantic": 2})]
        kwargs = {"view": view, "policy": "transparent", "limit": 3}
        first = select("What breaks?", fused, **kwargs)
        second = select("What breaks?", fused, **kwargs)
        self.assertEqual(first, second)


class RetractionTests(unittest.TestCase):
    def test_retracted_tie_gives_no_corroboration(self):
        view = view_for(
            ["a.py::x", "a.py::y"],
            [assertion("a.py::x", "a.py::y", relation="depends_on",
                       status="asserted_in_source"),
             {"proposition": {"relation": "retracts", "retract": "depends_on",
                              "caller": "a.py::x", "callee": "a.py::y"},
              "source": "s", "start": 0, "end": 1,
              "status": "asserted_in_source",
              "resolution": {"rule": "test"}, "producer": "test"}])
        evidence = candidate_evidence(view, "a.py::x")
        self.assertEqual(evidence["asserted_ties"], 0)
        self.assertEqual(evidence["retracted_ties"], 1)
        self.assertEqual(evidence["graph_ties"], 0)

    def test_unretracted_tie_still_counts(self):
        view = view_for(
            ["a.py::x", "a.py::y"],
            [assertion("a.py::x", "a.py::y", relation="depends_on",
                       status="asserted_in_source")])
        evidence = candidate_evidence(view, "a.py::x")
        self.assertEqual(evidence["asserted_ties"], 1)
        self.assertEqual(evidence["retracted_ties"], 0)


class AnswerGuardTests(unittest.TestCase):
    """Lexical discovery can name symbols the graph does not publish
    (nested functions, decorated methods). answer() must skip them
    explicitly instead of raising unknown-symbol."""

    def test_unknown_discovery_symbols_are_reported_not_raised(self):
        import shutil
        import tempfile
        from pathlib import Path
        from tunic.memory import Memory
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        (root / "mod.py").write_text(
            "def outer():\n"
            "    def inner_helper():\n"
            "        return 'quantum banana firmware'\n"
            "    return inner_helper()\n")
        memory = Memory(str(tmp / "g.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        result = memory.answer("quantum banana firmware", limit=5)
        self.assertIn("mod.py::outer.inner_helper", result["unknown_symbols"])
        self.assertTrue(all(c["symbol"] != "mod.py::outer.inner_helper"
                            for c in result["candidates"]))
        selected = memory.answer("quantum banana firmware", limit=5, select=True)
        self.assertEqual(selected["outcome"], "empty")
        decisions = {d["symbol"]: d["outcome"]
                     for d in selected["selection"]["trace"]["decisions"]}
        self.assertEqual(decisions.get("mod.py::outer.inner_helper"),
                         "insufficient_evidence")

    def test_failing_semantic_stage_degrades_not_raises(self):
        import shutil
        import tempfile
        from pathlib import Path
        from tunic.memory import Memory
        from tunic.retrieval import EmbeddingProvider
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        (root / "mod.py").write_text(
            "def helper():\n    return 'late fee computation'\n")

        class Broken(EmbeddingProvider):
            name = "broken"
            def available(self):
                return True
            def embed_texts(self, texts):
                raise RuntimeError("backend exploded")

        memory = Memory(str(tmp / "g.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        result = memory.answer("Who computes late fees?", limit=5,
                               provider=Broken())
        self.assertTrue(result["stages"]["semantic"]["skipped"])
        self.assertIn("semantic stage error",
                      result["stages"]["semantic"]["reason"])
        self.assertEqual(result["candidates"][0]["symbol"], "mod.py::helper")


if __name__ == "__main__":
    unittest.main()
