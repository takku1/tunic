"""Selection-aware supersession: named history is kept, not withheld.

Regression focus: select=True under policy=current used to drop every
superseded candidate as out_of_scope before checking whether the query
names it. A symbol the query names explicitly is identity-established
historical context, not a stale discovery to suppress; an impact or
provenance query whose only viable evidence is superseded should keep
that evidence flagged rather than abstain. Discovery-ranked superseded
symbols stay excluded, so unrelated stale decoys cannot leak in.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.selection import select

SUITE_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "agent"


def assertion(caller, callee, relation="static_call",
              status="syntactic_observation"):
    return {"proposition": {"relation": relation, "caller": caller,
                            "callee": callee},
            "source": "s", "start": 0, "end": 1, "status": status,
            "resolution": {"rule": "test"}, "producer": "test"}


def supersedes(caller, callee):
    return {"proposition": {"relation": "supersedes", "caller": caller,
                            "callee": callee},
            "source": "s", "start": 0, "end": 1,
            "status": "asserted_in_source",
            "resolution": {"rule": "test"}, "producer": "test"}


def view_for(symbols, assertions):
    return {"symbols": [{"key": key} for key in symbols],
            "assertions": assertions}


def retired_view():
    return view_for(
        ["a.py::old", "a.py::new", "a.py::caller"],
        [assertion("a.py::caller", "a.py::old", "depends_on",
                   "asserted_in_source"),
         supersedes("a.py::new", "a.py::old")])


class NamedHistoryTests(unittest.TestCase):
    def test_named_superseded_is_kept_as_historical_context(self):
        fused = [(0.05, "a.py::old", {"name": 1})]
        result = select("What did we decide about old?",
                        fused, view=retired_view(),
                        policy="current", limit=3)
        self.assertEqual(result["kept"], ["a.py::old"])
        decision = result["trace"]["decisions"][0]
        self.assertEqual(decision["outcome"], "supported")
        reasons = " ".join(decision["reasons"])
        self.assertIn("superseded by a.py::new", reasons)
        self.assertIn("historical context", reasons)
        self.assertIn("not current", reasons)

    def test_exact_named_superseded_is_kept(self):
        fused = [(0.05, "a.py::old", {"exact": 1})]
        result = select("change a.py::old now", fused, view=retired_view(),
                        policy="current", limit=3)
        self.assertEqual(result["kept"], ["a.py::old"])

    def test_named_superseded_open_policy_has_no_note(self):
        fused = [(0.05, "a.py::old", {"name": 1})]
        result = select("What did we decide about old?",
                        fused, view=retired_view(),
                        policy="transparent", limit=3)
        self.assertEqual(result["kept"], ["a.py::old"])
        reasons = " ".join(result["trace"]["decisions"][0]["reasons"])
        self.assertNotIn("superseded by", reasons)

    def test_discovery_superseded_locator_stays_excluded(self):
        fused = [(0.03, "a.py::old", {"lexical": 1})]
        result = select("Where is the old handler?", fused,
                        view=retired_view(), policy="current", limit=3)
        self.assertEqual(result["kept"], [])
        decision = result["trace"]["decisions"][0]
        self.assertEqual(decision["outcome"], "out_of_scope")
        self.assertIn("superseded by a.py::new",
                      " ".join(decision["reasons"]))

    def test_discovery_superseded_impact_viable_is_proposed(self):
        fused = [(0.03, "a.py::old", {"lexical": 1})]
        result = select("What breaks if old changes?", fused,
                        view=retired_view(), policy="current", limit=3)
        self.assertEqual(result["kept"], ["a.py::old"])
        decision = result["trace"]["decisions"][0]
        self.assertEqual(decision["outcome"], "proposed")
        reasons = " ".join(decision["reasons"])
        self.assertIn("superseded by a.py::new", reasons)
        self.assertIn("historical context", reasons)

    def test_discovery_superseded_impact_tieless_stays_excluded(self):
        view = view_for(["a.py::old", "a.py::new"],
                        [supersedes("a.py::new", "a.py::old")])
        fused = [(0.03, "a.py::old", {"lexical": 1})]
        result = select("What breaks if old changes?", fused, view=view,
                        policy="current", limit=3)
        self.assertEqual(result["kept"], [])
        self.assertEqual(result["trace"]["decisions"][0]["outcome"],
                         "out_of_scope")

    def test_unrelated_stale_decoy_stays_excluded(self):
        view = view_for(
            ["a.py::live", "a.py::user", "a.py::retired", "a.py::next"],
            [assertion("a.py::user", "a.py::live", "depends_on",
                       "asserted_in_source"),
             assertion("a.py::user", "a.py::retired", "depends_on",
                       "asserted_in_source"),
             supersedes("a.py::next", "a.py::retired")])
        fused = [(0.05, "a.py::live", {"name": 1}),
                 (0.03, "a.py::retired", {"lexical": 1})]
        result = select("What breaks if live changes?", fused, view=view,
                        policy="current", limit=3)
        self.assertIn("a.py::live", result["kept"])
        self.assertNotIn("a.py::retired", result["kept"])
        decisions = {d["symbol"]: d["outcome"]
                     for d in result["trace"]["decisions"]}
        self.assertEqual(decisions["a.py::retired"], "out_of_scope")

    def test_renamed_symbol_named_old_form_is_kept(self):
        fused = [(0.05, "a.py::old", {"exact": 1})]
        result = select("Callers of a.py::old?", fused, view=retired_view(),
                        policy="current", limit=3)
        self.assertEqual(result["kept"], ["a.py::old"])
        self.assertIn("superseded by a.py::new",
                      " ".join(result["trace"]["decisions"][0]["reasons"]))

    def test_multiple_successors_all_named_in_note(self):
        view = view_for(
            ["a.py::old", "a.py::new1", "a.py::new2"],
            [supersedes("a.py::new1", "a.py::old"),
             supersedes("a.py::new2", "a.py::old")])
        fused = [(0.05, "a.py::old", {"name": 1})]
        result = select("What did we decide about old?", fused, view=view,
                        policy="current", limit=3)
        self.assertEqual(result["kept"], ["a.py::old"])
        reasons = " ".join(result["trace"]["decisions"][0]["reasons"])
        self.assertIn("a.py::new1", reasons)
        self.assertIn("a.py::new2", reasons)
        self.assertIn("historical context", reasons)

    def test_disputed_superseded_named_keeps_dispute(self):
        view = view_for(
            ["a.py::old", "a.py::new"],
            [supersedes("a.py::new", "a.py::old"),
             {"proposition": {"relation": "contradicts",
                              "caller": "a.py::old", "callee": "a.py::new"},
              "source": "s", "start": 0, "end": 1,
              "status": "asserted_in_source",
              "resolution": {"rule": "test"}, "producer": "test"}])
        fused = [(0.05, "a.py::old", {"name": 1})]
        result = select("Is old still valid?", fused, view=view,
                        policy="current", limit=3)
        self.assertEqual(result["kept"], ["a.py::old"])
        decision = result["trace"]["decisions"][0]
        self.assertEqual(decision["outcome"], "contradicted")
        reasons = " ".join(decision["reasons"])
        self.assertIn("superseded by a.py::new", reasons)
        self.assertIn("contradicts", reasons)


class SuiteRegressionTests(unittest.TestCase):
    def evolve_memory(self, tmp):
        from tunic.memory import Memory
        root = Path(tmp) / "repo"
        shutil.copytree(SUITE_DIR / "evolve", root)
        memory = Memory(str(Path(tmp) / "agent.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        return memory

    def returned(self, result):
        symbols = [row["symbol"] for row in result["candidates"]]
        symbols.extend(entry["symbol"] for entry in result["impacts"]
                       if entry["symbol"] not in symbols)
        return symbols

    def test_stale_task_named_legacy_is_returned(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        memory = self.evolve_memory(tmp)
        result = memory.answer(
            "Which functions break if handle_legacy changes?",
            policy="current", select=True)
        self.assertIn("api.py::handle_legacy", self.returned(result))
        self.assertIn("api.py::handle_modern", self.returned(result))
        decisions = {d["symbol"]: d
                     for d in result["selection"]["trace"]["decisions"]}
        self.assertEqual(decisions["api.py::handle_legacy"]["outcome"],
                         "supported")
        self.assertIn(
            "historical context",
            " ".join(decisions["api.py::handle_legacy"]["reasons"]))

    def test_evolve_para_legacy_is_returned(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        memory = self.evolve_memory(tmp)
        result = memory.answer("If the legacy handler shifts, what breaks?",
                               policy="current", select=True)
        self.assertIn("api.py::handle_legacy", self.returned(result))


if __name__ == "__main__":
    unittest.main()
