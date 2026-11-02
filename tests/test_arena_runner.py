"""Arena v2 runner tests: enforced budgets, strict scoring, evidence.

Exercised with deterministic scripted agents on a hermetic fixture
repo -- no model calls. Proves the pipeline detects successes,
failures, abstentions, and violations before any real-agent run.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmarks" / "arena"))

import runner_v2 as rn  # noqa: E402
import taskgen_v2 as tg  # noqa: E402

sys.path.pop(0)
sys.path.pop(0)

CORE = ("def cover():\n"
        "    return 0\n"
        "\n\n"
        "def lonely():\n"
        "    return 0\n")
SHOP = ("from core import cover\n"
        "\n\n"
        "def run():\n"
        "    return cover()\n")


def fixture_root(tmp):
    root = Path(tmp) / "src"
    root.mkdir(parents=True)
    (root / "core.py").write_text(CORE, encoding="utf-8")
    (root / "shop.py").write_text(SHOP, encoding="utf-8")
    return root


def scripted(answer, actions=1, tools=("answer",)):
    def agent(task_text, workspace, budget, ctx):
        for _ in range(actions):
            ctx.action(tools[0], key="scripted")
        return answer
    return agent


def callers_spec():
    return rn.TaskManifest(
        id="t:callers", kind="callers",
        text="Which functions call core.py::cover?",
        key={"sites": [("shop.py::run", "shop.py", 4)],
             "scope": "cross-file", "target_path": "core.py"})


def uniqueness_spec(unique, resolved=None):
    return rn.TaskManifest(
        id="t:uniq", kind="uniqueness", text="At w.py:80 ...",
        key={"definitions": ["w.py::C.ingest", "store.py::GraphStore.ingest"],
             "unique": unique, "resolved": resolved,
             "rule": "unique only when the receiver is self/cls"})


def chain_spec():
    return rn.TaskManifest(
        id="t:chain", kind="chain", text="Trace ...",
        key={"hops": [("m.py::main", "m.py::next")], "cold": "missing"})


def decoy_spec():
    return rn.TaskManifest(
        id="t:decoy", kind="decoy", text="Does a call b?",
        key={"a": "a.py::f", "b": "b.py::g", "verdict": "no-call"})


def abstention_spec():
    return rn.TaskManifest(
        id="t:abs", kind="abstention",
        text="Which functions call core.py::lonely?",
        key={"target": "core.py::lonely", "expected": "abstain"})


class StrictScoringTests(unittest.TestCase):
    def test_callers_exact_match(self):
        self.assertEqual(rn.score_answer(callers_spec(), "shop.py::run"),
                         1.0)

    def test_callers_extra_symbol_rejected(self):
        self.assertEqual(
            rn.score_answer(callers_spec(), "shop.py::run\nshop.py::other"),
            0.0)

    def test_callers_contradiction_rejected(self):
        self.assertEqual(
            rn.score_answer(callers_spec(), "shop.py::run\nABSTAIN"), 0.0)

    def test_callers_prose_rejected(self):
        self.assertEqual(
            rn.score_answer(callers_spec(),
                            "The caller is shop.py::run, I believe"), 0.0)

    def test_uniqueness_exact_unique(self):
        answer = ("store.py::GraphStore.ingest\nw.py::C.ingest\n"
                  "VERDICT: UNIQUE w.py::C.ingest")
        spec = uniqueness_spec(True, "w.py::C.ingest")
        self.assertEqual(rn.score_answer(spec, answer), 1.0)

    def test_uniqueness_substring_trap_rejected(self):
        # Mentions the right key in prose but gives no VERDICT line.
        answer = ("store.py::GraphStore.ingest\nw.py::C.ingest\n"
                  "The receiver memory might make w.py::C.ingest unique.")
        spec = uniqueness_spec(True, "w.py::C.ingest")
        self.assertEqual(rn.score_answer(spec, answer), 0.0)

    def test_uniqueness_contradiction_rejected(self):
        answer = ("w.py::C.ingest\nVERDICT: UNIQUE w.py::C.ingest\n"
                  "VERDICT: NOT-DETERMINED")
        spec = uniqueness_spec(True, "w.py::C.ingest")
        self.assertEqual(rn.score_answer(spec, answer), 0.0)

    def test_uniqueness_not_determined(self):
        answer = ("store.py::GraphStore.ingest\nw.py::C.ingest\n"
                  "VERDICT: NOT-DETERMINED")
        self.assertEqual(rn.score_answer(uniqueness_spec(False), answer),
                         1.0)

    def test_chain_exact_order(self):
        answer = "m.py::main -> m.py::next\nCOLD: missing\nREASON: no import"
        self.assertEqual(rn.score_answer(chain_spec(), answer), 1.0)

    def test_chain_reordered_rejected(self):
        spec = rn.TaskManifest(
            id="t:chain2", kind="chain", text="Trace ...",
            key={"hops": [("m.py::main", "m.py::mid"),
                          ("m.py::mid", "m.py::next")],
                 "cold": "missing"})
        answer = ("m.py::mid -> m.py::next\nm.py::main -> m.py::mid\n"
                  "COLD: missing")
        self.assertEqual(rn.score_answer(spec, answer), 0.0)

    def test_chain_extra_hop_rejected(self):
        answer = ("m.py::main -> m.py::next\nm.py::next -> m.py::leaf\n"
                  "COLD: missing")
        self.assertEqual(rn.score_answer(chain_spec(), answer), 0.0)

    def test_chain_missing_cold_rejected(self):
        self.assertEqual(
            rn.score_answer(chain_spec(), "m.py::main -> m.py::next"), 0.0)

    def test_decoy_verdict_with_reason(self):
        self.assertEqual(
            rn.score_answer(decoy_spec(), "NO\nREASON: never called"), 1.0)

    def test_decoy_contradiction_rejected(self):
        self.assertEqual(rn.score_answer(decoy_spec(), "NO\nYES"), 0.0)

    def test_decoy_prose_rejected(self):
        self.assertEqual(rn.score_answer(decoy_spec(), "The answer is no"),
                         0.0)

    def test_abstention_with_symbols_rejected(self):
        self.assertEqual(
            rn.score_answer(abstention_spec(),
                            "ABSTAIN\nshop.py::run"), 0.0)

    def test_abstention_clean(self):
        self.assertEqual(
            rn.score_answer(abstention_spec(),
                            "ABSTAIN\nREASON: zero call sites"), 1.0)

    def test_unknown_kind_raises(self):
        spec = rn.TaskManifest(id="t:x", kind="nope", text="?", key={})
        with self.assertRaises(ValueError):
            rn.score_answer(spec, "anything")


class RunnerIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = fixture_root(cls.tmp)
        cls.work = Path(cls.tmp) / "work"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_over_budget_is_invalid_not_scored(self):
        def chatty(task_text, workspace, budget, ctx):
            for _ in range(budget + 1):
                ctx.action("search", path="x.py")
            return "shop.py::run"
        cell = rn.run_cell(callers_spec(), "chatty", chatty, self.src,
                           self.work, runs_dir=self.work)
        self.assertEqual(cell.status, "budget-exceeded")
        self.assertEqual(cell.score, 0.0)
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["event"], "result")
        self.assertEqual(rows[-1]["status"], "budget-exceeded")
        self.assertTrue(any(r.get("event") == "error" and
                            r.get("category") == "budget-exceeded"
                            for r in rows))

    def test_unknown_tool_rejected(self):
        def sneaky(task_text, workspace, budget, ctx):
            ctx.action("nuke", path="/")
            return "shop.py::run"
        cell = rn.run_cell(callers_spec(), "sneaky", sneaky, self.src,
                           self.work, runs_dir=self.work,
                           allowed_tools={"search", "answer"})
        self.assertEqual(cell.status, "error")
        self.assertEqual(cell.score, 0.0)

    def test_unlogged_work_not_counted(self):
        def quiet(task_text, workspace, budget, ctx):
            list(Path(workspace).glob("*.py"))  # no ctx.action call
            return "shop.py::run"
        cell = rn.run_cell(callers_spec(), "quiet", quiet, self.src,
                           self.work, runs_dir=self.work)
        self.assertEqual(cell.actions, 0)
        self.assertEqual(cell.score, 1.0)

    def test_agent_crash_finalizes_error_cell(self):
        def crashy(task_text, workspace, budget, ctx):
            ctx.action("search", path="a.py")
            raise RuntimeError("boom")
        cell = rn.run_cell(callers_spec(), "crashy", crashy, self.src,
                           self.work, runs_dir=self.work)
        self.assertEqual(cell.status, "error")
        self.assertEqual(cell.score, 0.0)
        self.assertEqual(cell.answer, "")
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()]
        error = next(r for r in rows if r.get("event") == "error")
        self.assertEqual(error["category"], "agent-crash")
        self.assertIn("RuntimeError", error["detail"])
        self.assertEqual(error["actions"], 1)

    def test_repeated_runs_never_overwrite(self):
        stub = scripted("shop.py::run")
        first = rn.run_cell(callers_spec(), "rep", stub, self.src,
                            self.work, runs_dir=self.work)
        second = rn.run_cell(callers_spec(), "rep", stub, self.src,
                             self.work, runs_dir=self.work)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertNotEqual(first.transcript_path, second.transcript_path)
        self.assertTrue(Path(first.transcript_path).exists())
        self.assertTrue(Path(second.transcript_path).exists())

    def test_provenance_recorded(self):
        stub = scripted("shop.py::run")
        cell = rn.run_cell(callers_spec(), "prov", stub, self.src,
                           self.work, runs_dir=self.work,
                           allowed_tools={"answer"})
        prov = cell.provenance
        self.assertEqual(prov["runner_version"], rn.RUNNER_VERSION)
        self.assertEqual(prov["scoring_version"], rn.SCORING_VERSION)
        self.assertIn("manifest_digest", prov)
        self.assertEqual(prov["allowed_tools"], ["answer"])
        self.assertEqual(prov["sandbox"], "none-workspace-copy-only")
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["provenance"], prov)

    def test_answer_key_not_exposed_to_agent(self):
        seen = {}

        def spy(task_text, workspace, budget, ctx):
            blob = task_text + repr(sorted(
                str(p) for p in Path(workspace).glob("*.py")))
            seen["key_in_interface"] = "sites" in blob
            ctx.action("answer", key="spy")
            return "ABSTAIN"
        cell = rn.run_cell(callers_spec(), "spy", spy, self.src,
                           self.work, runs_dir=self.work)
        self.assertFalse(seen["key_in_interface"])
        transcript = Path(cell.transcript_path).read_text(encoding="utf-8")
        self.assertNotIn('"sites"', transcript)

    def test_isolation_probe_no_leak(self):
        report = rn.isolation_probe(rn.abstain_stub_agent, self.src,
                                    self.work)
        self.assertFalse(report["leaked"])
        self.assertNotEqual(report["first"].run_id,
                            report["second"].run_id)


class PairedRehearsalTests(unittest.TestCase):
    """Deterministic paired rehearsal: exact vs abstain scripted arms."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = fixture_root(cls.tmp)
        cls.work = Path(cls.tmp) / "work"
        callers = rn.TaskManifest(
            id="r:callers", kind="callers",
            text=tg.build_callers_task(
                "core.py::cover", [("shop.py::run", "shop.py", 4)],
                "cross-file", "core.py").text,
            key={"sites": [("shop.py::run", "shop.py", 4)],
                 "scope": "cross-file", "target_path": "core.py"})
        abstain = rn.TaskManifest(
            id="r:abs", kind="abstention",
            text=tg.build_abstention_task("core.py::lonely").text,
            key={"target": "core.py::lonely", "expected": "abstain"})
        cls.tasks = [callers, abstain]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exact_beats_abstain_on_answerable_task(self):
        exact = scripted("shop.py::run")
        outcome = {(t.id, arm): rn.run_cell(
            t, arm, exact if arm == "exact" else rn.abstain_stub_agent,
            self.src, self.work, runs_dir=self.work)
            for t in self.tasks for arm in ("exact", "abstain")}
        self.assertEqual(outcome[("r:callers", "exact")].score, 1.0)
        self.assertEqual(outcome[("r:callers", "abstain")].score, 0.0)
        self.assertEqual(outcome[("r:abs", "exact")].score, 0.0)
        self.assertEqual(outcome[("r:abs", "abstain")].score, 1.0)
        for cell in outcome.values():
            self.assertEqual(cell.status, "complete")
            self.assertTrue(Path(cell.transcript_path).exists())


if __name__ == "__main__":
    unittest.main()
