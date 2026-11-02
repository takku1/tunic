"""Arena v2 runner tests: trusted dispatch, strict scoring, evidence.

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


def scripted(answer, actions=1):
    def agent(task_text, workspace, budget, ctx):
        for _ in range(actions):
            ctx.action("list_files")
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
            rn.score_answer(abstention_spec(), "ABSTAIN\nshop.py::run"), 0.0)

    def test_abstention_clean(self):
        self.assertEqual(
            rn.score_answer(abstention_spec(),
                            "ABSTAIN\nREASON: zero call sites"), 1.0)

    def test_unknown_kind_raises(self):
        spec = rn.TaskManifest(id="t:x", kind="nope", text="?", key={})
        with self.assertRaises(ValueError):
            rn.score_answer(spec, "anything")


class DispatchBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = fixture_root(cls.tmp)
        cls.work = Path(cls.tmp) / "work"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def cell(self, spec, arm, agent, **kwargs):
        kwargs.setdefault("toolsets", ("repo",))
        kwargs.setdefault("runs_dir", self.work)
        return rn.run_cell(spec, arm, agent, self.src, self.work, **kwargs)

    def test_dispatched_read_returns_content(self):
        def agent(task_text, workspace, budget, ctx):
            text = ctx.action("read_file", path="core.py")
            assert "def cover" in text
            return "shop.py::run"
        cell = self.cell(callers_spec(), "reader", agent)
        self.assertEqual(cell.score, 1.0)
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "action"]
        self.assertEqual(rows[0]["outcome"], "completed")
        self.assertIn("result_preview", rows[0])

    def test_workspace_escape_denied(self):
        def agent(task_text, workspace, budget, ctx):
            try:
                ctx.action("read_file", path="../escape.py")
            except PermissionError:
                pass
            return "ABSTAIN"
        cell = self.cell(abstention_spec(), "escapee", agent)
        self.assertEqual(cell.status, "complete")
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "action"]
        self.assertEqual(rows[0]["outcome"], "failed")

    def test_handler_exception_recorded_agent_recovers(self):
        def agent(task_text, workspace, budget, ctx):
            try:
                ctx.action("search", pattern="([invalid")
            except ValueError:
                pass
            return "shop.py::run"
        cell = self.cell(callers_spec(), "recover", agent)
        self.assertEqual(cell.status, "complete")
        self.assertEqual(cell.score, 1.0)
        self.assertEqual(cell.actions, 1)
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "action"]
        self.assertEqual(rows[0]["outcome"], "failed")

    def test_failure_transcript_preserves_actions(self):
        def crashy(task_text, workspace, budget, ctx):
            ctx.action("list_files")
            ctx.action("search", pattern="cover")
            raise RuntimeError("boom")
        cell = self.cell(callers_spec(), "crashy", crashy)
        self.assertEqual(cell.status, "error")
        self.assertEqual(cell.actions, 2)
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()]
        actions = [r for r in rows if r.get("event") == "action"]
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["tool"], "list_files")
        error = next(r for r in rows if r.get("event") == "error")
        self.assertEqual(error["category"], "agent-crash")

    def test_budget_exhaustion_preserves_actions(self):
        def chatty(task_text, workspace, budget, ctx):
            for _ in range(budget + 1):
                ctx.action("list_files")
            return "shop.py::run"
        cell = self.cell(callers_spec(), "chatty", chatty)
        self.assertEqual(cell.status, "budget-exceeded")
        self.assertEqual(cell.score, 0.0)
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()]
        actions = [r for r in rows if r.get("event") == "action"]
        self.assertTrue(any(a.get("outcome") == "budget-exceeded"
                            for a in actions))
        self.assertTrue(any(a.get("outcome") == "completed"
                            for a in actions))

    def test_caught_budget_still_invalid(self):
        def swallower(task_text, workspace, budget, ctx):
            try:
                for _ in range(budget + 3):
                    ctx.action("list_files")
            except rn.BudgetExhausted:
                pass
            return "shop.py::run"
        cell = self.cell(callers_spec(), "swallower", swallower)
        self.assertEqual(cell.status, "budget-exceeded")
        self.assertEqual(cell.score, 0.0)

    def test_unknown_tool_rejected(self):
        def sneaky(task_text, workspace, budget, ctx):
            ctx.action("nuke", path="/")
            return "shop.py::run"
        cell = self.cell(callers_spec(), "sneaky", sneaky,
                        allowed_tools={"list_files", "read_file", "search"})
        self.assertEqual(cell.status, "error")
        self.assertEqual(cell.score, 0.0)
        self.assertEqual(cell.actions, 0)

    def test_denied_tool_rejected(self):
        def agent(task_text, workspace, budget, ctx):
            ctx.action("read_file", path="core.py")
            return "shop.py::run"
        cell = self.cell(callers_spec(), "denied", agent,
                        allowed_tools={"list_files"})
        self.assertEqual(cell.status, "error")
        self.assertEqual(cell.actions, 0)

    def test_unlogged_work_not_counted(self):
        def quiet(task_text, workspace, budget, ctx):
            list(Path(workspace).glob("*.py"))
            return "shop.py::run"
        cell = self.cell(callers_spec(), "quiet", quiet)
        self.assertEqual(cell.actions, 0)
        self.assertEqual(cell.score, 1.0)

    def test_direct_bypass_documents_residual_gap(self):
        def bypass(task_text, workspace, budget, ctx):
            (Path(workspace) / "SNEAK").write_text("x", encoding="utf-8")
            return "shop.py::run"
        cell = self.cell(callers_spec(), "bypass", bypass)
        self.assertEqual(cell.actions, 0)
        self.assertTrue((Path(self.work).glob("t_callers-bypass-*")))
        # The runner cannot see raw filesystem I/O: sandbox is
        # workspace-copy isolation only, recorded in provenance.
        self.assertEqual(cell.provenance["sandbox"],
                         "none-workspace-copy-only")

    def test_repeated_runs_never_overwrite(self):
        stub = scripted("shop.py::run")
        first = self.cell(callers_spec(), "rep", stub)
        second = self.cell(callers_spec(), "rep", stub)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertNotEqual(first.transcript_path, second.transcript_path)
        self.assertTrue(Path(first.transcript_path).exists())
        self.assertTrue(Path(second.transcript_path).exists())

    def test_provenance_recorded(self):
        stub = scripted("shop.py::run")
        cell = self.cell(callers_spec(), "prov", stub,
                        allowed_tools=["list_files"])
        prov = cell.provenance
        self.assertEqual(prov["runner_version"], rn.RUNNER_VERSION)
        self.assertEqual(prov["scoring_version"], rn.SCORING_VERSION)
        self.assertIn("manifest_digest", prov)
        self.assertEqual(prov["allowed_tools"], ["list_files"])
        self.assertIn("list_files", prov["handlers"])
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
            ctx.action("list_files")
            return "ABSTAIN"
        cell = self.cell(callers_spec(), "spy", spy)
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
    """Deterministic paired rehearsal with actual dispatched tools.

    The derived arm answers from live ``related`` output; the stub arm
    abstains. Tunic toolsets ingest each arm workspace on setup.
    """

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

    @staticmethod
    def derived_agent(task_text, workspace, budget, ctx):
        symbol = ("core.py::lonely" if "lonely" in task_text
                  else "core.py::cover")
        related = ctx.action("related", symbol=symbol)
        impacts = sorted(i["symbol"]
                         for i in related["impact"]["impacts"])
        if not impacts:
            return "ABSTAIN\nREASON: no callers in graph"
        return "\n".join(impacts)

    def attempt(self, task, arm, agent):
        return rn.run_cell(task, arm, agent, self.src, self.work,
                           runs_dir=self.work,
                           toolsets=("repo", "tunic"))

    def test_derived_beats_abstain_on_answerable_task(self):
        exact = self.attempt(self.tasks[0], "derived", self.derived_agent)
        base = self.attempt(self.tasks[0], "abstain", rn.abstain_stub_agent)
        self.assertEqual(exact.score, 1.0)
        self.assertEqual(base.score, 0.0)
        self.assertGreater(exact.actions, 0)

    def test_both_abstain_correctly_on_empty_task(self):
        exact = self.attempt(self.tasks[1], "derived", self.derived_agent)
        base = self.attempt(self.tasks[1], "abstain", rn.abstain_stub_agent)
        self.assertEqual(exact.score, 1.0)
        self.assertEqual(base.score, 1.0)

    def test_rehearsal_transcripts_are_complete(self):
        cell = self.attempt(self.tasks[0], "derived", self.derived_agent)
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(
                    encoding="utf-8").splitlines()]
        kinds = {r.get("event") for r in rows}
        self.assertTrue({"start", "action", "result"} <= kinds)
        actions = [r for r in rows if r.get("event") == "action"]
        self.assertTrue(all(a["outcome"] == "completed" for a in actions))
        self.assertEqual(cell.status, "complete")


if __name__ == "__main__":
    unittest.main()
