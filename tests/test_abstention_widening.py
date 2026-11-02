"""Abstention widening + historical-evidence safety.

Acceptance (predeclared): every empty answer carries a cause matching
ground truth; zero gold-bearing dev answers abstain; zero historical
candidates render without a HISTORICAL marker; zero superseded symbols
render under OBSERVED; the frozen suite, pilot verdict, and fusion
baseline are untouched (no heldout tuning).

Cause taxonomy: no_discovery (nothing matched any stage),
unknown_subject (the question names unindexed symbols),
all_withheld (discovery happened, selection kept nothing),
budget_exhausted (a zero/truncating budget left nothing).
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tunic.memory import Memory  # noqa: E402
from tunic.render import render_answer_text  # noqa: E402

sys.path.pop(0)

SUITE = REPO / "benchmarks" / "agent" / "suite.json"

FILES = {"__init__.py": "",
         "core.py": ("def calibrate(quasar):\n"
                     "    return quasar\n"
                     "\n\n"
                     "def lonely():\n"
                     "    return 0\n"),
         "shop.py": ("def run(table):\n"
                     "    return table\n")}


def candidates_section(text):
    return text.split("CANDIDATES")[1].split("OBSERVED")[0]


def observed_section(text):
    return text.split("OBSERVED DEPENDENCIES")[1].split("ASSERTED")[0]


class AbstentionCauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        root = Path(cls.tmp) / "repo"
        root.mkdir()
        for name, text in FILES.items():
            (root / name).write_text(text, encoding="utf-8")
        cls.memory = Memory(str(Path(cls.tmp) / "cal.sqlite"), root)
        cls.memory.ingest()

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_discovery_cause(self):
        out = self.memory.answer("xyzzy qqq zzz", limit=5,
                                 policy="current", select=True)
        self.assertEqual(out["outcome"], "empty")
        self.assertEqual(out["abstention"]["cause"], "no_discovery")

    def test_unknown_subject_cause(self):
        out = self.memory.answer("nope.py::Missing handles refunds",
                                 limit=5, policy="current", select=True)
        self.assertEqual(out["outcome"], "empty")
        self.assertEqual(out["abstention"]["cause"], "unknown_subject")
        self.assertIn("nope.py::Missing", out["abstention"]["unknown_symbols"])

    def test_all_withheld_cause(self):
        out = self.memory.answer("quasar setting", limit=5,
                                 policy="current", select=True)
        self.assertEqual(out["outcome"], "empty")
        self.assertEqual(out["abstention"]["cause"], "all_withheld")
        self.assertGreaterEqual(
            out["abstention"]["withheld"].get("insufficient_evidence", 0), 1)

    def test_budget_exhausted_cause(self):
        out = self.memory.answer("quasar setting", limit=0,
                                 policy="current", select=True)
        self.assertEqual(out["outcome"], "empty")
        self.assertEqual(out["abstention"]["cause"], "budget_exhausted")

    def test_complete_answer_has_no_abstention(self):
        out = self.memory.answer("core.py::calibrate", limit=5,
                                 policy="current", select=True)
        self.assertEqual(out["outcome"], "complete")
        self.assertIsNone(out["abstention"])

    def test_empty_render_names_cause(self):
        out = self.memory.answer("quasar setting", limit=5,
                                 policy="current", select=True)
        text = render_answer_text(out)
        self.assertIn("abstention: all_withheld", text)


class HistoricalRenderTests(unittest.TestCase):
    def evolve_memory(self, tmp):
        import json
        from tunic.eval import load_agent_suite
        suite = load_agent_suite(SUITE)
        root = Path(tmp) / "repo"
        shutil.copytree(SUITE.parent / "evolve", root)
        memory = Memory(str(Path(tmp) / "agent.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        return memory

    def test_historical_candidate_marked_in_render(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        memory = self.evolve_memory(tmp)
        out = memory.answer(
            "Which functions break if handle_legacy changes?",
            limit=5, policy="current", select=True)
        text = render_answer_text(out)
        section = candidates_section(text)
        legacy = [line for line in section.splitlines()
                  if line.strip().startswith("api.py::handle_legacy ")]
        modern = [line for line in section.splitlines()
                  if line.strip().startswith("api.py::handle_modern ")]
        self.assertEqual(len(legacy), 1)
        self.assertIn("HISTORICAL", legacy[0])
        self.assertIn("superseded by api.py::handle_modern", legacy[0])
        self.assertEqual(len(modern), 1)
        self.assertNotIn("HISTORICAL", modern[0])

    def test_historical_never_observed_and_replacement_named(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        memory = self.evolve_memory(tmp)
        out = memory.answer(
            "Which functions break if handle_legacy changes?",
            limit=5, policy="current", select=True)
        text = render_answer_text(out)
        self.assertNotIn("api.py::handle_legacy", observed_section(text))
        corrections = text.split("CORRECTIONS")[1].split("DISPUTED")[0]
        self.assertIn("api.py::handle_legacy is obsolete", corrections)
        self.assertIn("api.py::handle_modern may be current", corrections)

    def test_withheld_history_still_names_successor(self):
        result = {
            "candidates": [{"symbol": "a.py::live", "score": 0.9,
                            "match": "name", "origins": ["name"]}],
            "impacts": [], "mentions": [], "claims": [],
            "selection": {"kept": ["a.py::live"],
                          "trace": {"decisions": [
                              {"symbol": "a.py::live", "outcome": "supported",
                               "reasons": ["entity named in the query"]},
                              {"symbol": "a.py::retired",
                               "outcome": "out_of_scope",
                               "reasons": ["superseded by a.py::next under "
                                           "current policy"]}]}}}
        text = render_answer_text(result)
        withheld = text.split("WITHHELD")[1].split("TEXT MENTIONS")[0]
        self.assertIn("a.py::retired", withheld)
        self.assertIn("superseded by a.py::next", withheld)
        live = [line for line in candidates_section(text).splitlines()
                if "a.py::live" in line]
        self.assertEqual(len(live), 1)
        self.assertNotIn("HISTORICAL", live[0])

    def test_disputed_historical_carries_both_flags(self):
        result = {
            "candidates": [{"symbol": "a.py::old", "score": 0.9,
                            "match": "name", "origins": ["name"]}],
            "impacts": [], "mentions": [], "claims": [],
            "selection": {"kept": ["a.py::old"],
                          "trace": {"decisions": [
                              {"symbol": "a.py::old",
                               "outcome": "contradicted",
                               "reasons": ["a live contradicts claim touches "
                                           "this symbol",
                                           "superseded by a.py::new under "
                                           "current policy; returned as named "
                                           "historical context, "
                                           "not current"]}]}}}
        text = render_answer_text(result)
        disputed = text.split("DISPUTED")[1].split("WITHHELD")[0]
        self.assertIn("a.py::old", disputed)
        old = [line for line in candidates_section(text).splitlines()
               if "a.py::old" in line]
        self.assertEqual(len(old), 1)
        self.assertIn("HISTORICAL", old[0])


class SuiteNoRegressionTests(unittest.TestCase):
    def test_non_experimental_tasks_hold_under_select(self):
        import json
        from tunic.eval import load_agent_suite
        suite = load_agent_suite(SUITE)
        work = Path(tempfile.mkdtemp(prefix="tunic-abswide-"))
        self.addCleanup(shutil.rmtree, work, True)
        prepared = {}
        for repo in sorted({t["repo"] for t in suite["tasks"]}):
            root = work / repo
            shutil.copytree(SUITE.parent / repo, root)
            memory = Memory(str(work / (repo + ".sqlite")), root)
            self.addCleanup(memory.close)
            memory.ingest()
            prepared[repo] = memory
        failures = []
        for task in suite["tasks"]:
            if task["experimental"]:
                continue
            out = prepared[task["repo"]].answer(
                task["question"], limit=5, policy="current", select=True)
            symbols = [r["symbol"] for r in out["candidates"]]
            symbols.extend(e["symbol"] for e in out["impacts"]
                           if e["symbol"] not in symbols)
            kinds = ["mention"] if out["mentions"] else []
            kinds.extend(r["relation"] for r in out["claims"])
            ok = (set(task["gold"]) <= set(symbols)
                  and not (set(task["forbidden"]) & set(symbols))
                  and set(task["evidence"]) <= set(kinds))
            if not ok:
                failures.append((task["id"], sorted(symbols)))
        self.assertEqual(failures, [])

    def test_zero_unmarked_historical_candidates(self):
        import json
        from tunic.eval import load_agent_suite
        suite = load_agent_suite(SUITE)
        work = Path(tempfile.mkdtemp(prefix="tunic-abshist-"))
        self.addCleanup(shutil.rmtree, work, True)
        prepared = {}
        for repo in sorted({t["repo"] for t in suite["tasks"]}):
            root = work / repo
            shutil.copytree(SUITE.parent / repo, root)
            memory = Memory(str(work / (repo + ".sqlite")), root)
            self.addCleanup(memory.close)
            memory.ingest()
            prepared[repo] = memory
        unmarked = []
        observed_violations = []
        for task in suite["tasks"]:
            if task["experimental"]:
                continue
            out = prepared[task["repo"]].answer(
                task["question"], limit=5, policy="current", select=True)
            text = render_answer_text(out)
            section = candidates_section(text)
            observed = observed_section(text)
            decisions = {(d.get("symbol"), d.get("outcome")): d.get("reasons")
                         for d in ((out.get("selection") or {})
                                   .get("trace", {}).get("decisions") or [])}
            for (symbol, outcome), reasons in decisions.items():
                joined = " ".join(reasons or [])
                if ("historical context" in joined and outcome in (
                        "supported", "proposed", "ambiguous",
                        "contradicted")):
                    lines = [l for l in section.splitlines()
                             if l.strip().startswith(symbol + " ")]
                    if len(lines) != 1 or "HISTORICAL" not in lines[0]:
                        unmarked.append((task["id"], symbol))
                    if any(l.strip().split(" ")[0] == symbol
                           for l in observed.splitlines()[1:]):
                        observed_violations.append((task["id"], symbol))
        self.assertEqual(unmarked, [])
        self.assertEqual(observed_violations, [])


if __name__ == "__main__":
    unittest.main()
