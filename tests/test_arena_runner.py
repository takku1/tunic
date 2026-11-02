"""Arena v2 runner tests: manifests, isolation, transcripts, scoring.

Exercised entirely with deterministic stub agents on a hermetic
fixture repo -- no model calls. Proves the pipeline is ready to
execute real-agent comparisons later without changing its shape.
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

CORE = "def cover():\n    return 0\n"
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


def manifests():
    return [
        tg.build_callers_task("core.py::cover",
                              [("shop.py::run", "shop.py", 4)],
                              "cross-file", "core.py"),
        tg.build_abstention_task("core.py::cover",
                                 expectation="abstain"),
    ]


def exact_stub(answer):
    def agent(task_text, workspace, budget, log):
        log("action", tool="answer", key="scripted")
        return answer
    return agent


class ArenaRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.src = fixture_root(cls.tmp)
        cls.work = Path(cls.tmp) / "work"
        cls.idx = tg.index_trees(cls.src)
        cls.specs = []
        for task_id, kind, text, key in [
                ("callers", "callers", manifests()[0].text,
                 manifests()[0].key),
                ("abstention", "abstention", manifests()[1].text,
                 manifests()[1].key)]:
            spec = rn.TaskManifest(id="t:" + task_id, kind=kind,
                                   text=text, key=key)
            errors = tg.validate_task(
                tg.TaskSpec(id=spec.id, kind=spec.kind, text=spec.text,
                            key=spec.key, budget_actions=spec.budget_actions),
                cls.idx)
            assert errors == [], errors
            cls.specs.append(spec)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exact_answer_scores_full(self):
        stub = exact_stub("shop.py::run")
        cell = rn.run_cell(self.specs[0], "exact", stub, self.src,
                           self.work, runs_dir=self.work)
        self.assertEqual(cell.score, 1.0)
        self.assertEqual(cell.actions, 1)
        self.assertFalse(cell.over_budget)
        self.assertGreaterEqual(cell.wall_s, 0.0)

    def test_wrong_answer_scores_zero(self):
        stub = exact_stub("shop.py::nope")
        cell = rn.run_cell(self.specs[0], "wrong", stub, self.src,
                           self.work, runs_dir=self.work)
        self.assertEqual(cell.score, 0.0)

    def test_grep_stub_abstains_and_scores(self):
        cell = rn.run_cell(self.specs[1], "grep", rn.grep_stub_agent,
                           self.src, self.work, runs_dir=self.work)
        self.assertEqual(cell.score, 1.0)
        self.assertLessEqual(cell.actions, self.specs[1].budget_actions)
        rows = [json.loads(line) for line in
                Path(cell.transcript_path).read_text(encoding="utf-8")
                .splitlines()]
        self.assertTrue(any(r.get("event") == "action" for r in rows))
        result = rows[-1]
        self.assertEqual(result["event"], "result")
        self.assertEqual(result["score"], 1.0)
        self.assertIn("wall_s", result)

    def test_over_budget_flagged_but_scored(self):
        def chatty(task_text, workspace, budget, log):
            for _ in range(budget + 1):
                log("action", tool="search", path="x.py")
            return "shop.py::run"
        cell = rn.run_cell(self.specs[0], "chatty", chatty, self.src,
                           self.work, runs_dir=self.work)
        self.assertTrue(cell.over_budget)
        self.assertEqual(cell.score, 1.0)

    def test_isolation_probe_no_leak(self):
        report = rn.isolation_probe(rn.abstain_stub_agent, self.src,
                                    self.work)
        self.assertFalse(report["leaked"])
        self.assertEqual(report["second"].score, 1.0)

    def test_repeat_runs_deterministic(self):
        stub = exact_stub("shop.py::run")
        first = rn.run_cell(self.specs[0], "rep", stub, self.src,
                            self.work, runs_dir=self.work)
        second = rn.run_cell(self.specs[0], "rep", stub, self.src,
                             self.work, runs_dir=self.work)
        self.assertEqual(first.score, second.score)
        self.assertEqual(first.actions, second.actions)


if __name__ == "__main__":
    unittest.main()
