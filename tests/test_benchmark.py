import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from tunic.benchmark import aggregate, load_suite, run_benchmark, score_response, set_scores
from tunic.compiler import compile_impact
from tunic.ollama import BackendError
from tunic.render import TEMPLATE

SUITE = Path(__file__).resolve().parents[1]/"benchmarks"/"w1"/"suite.json"


class ModelFixture:
    """Protocol double that copies visible context; no inference/quality claim."""
    def check_profile(self, profile):
        pass

    def generate(self, profile, prompt, **kwargs):
        text = "fixture plan"
        if kwargs.get("output_schema"):
            start = prompt.index("Evidence JSON: ")+len("Evidence JSON: ")
            payload, _ = json.JSONDecoder().raw_decode(prompt[start:])
            # Return a supported subset deliberately, independently of hidden gold.
            text = json.dumps({"coverage": "partial", "impacts": [
                {"symbol": i["symbol"], "kind": i["kind"], "path": i["path"], "citations": i["required_citations"]}
                for i in payload["impacts"][:1]]})
        return {"outcome": "complete", "generation": {"text": text}}


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.allowed = (Path(__file__).resolve().parents[1]/".tunic"/"test-runs").resolve()
        self.allowed.mkdir(parents=True, exist_ok=True)
        self.base = self.allowed/("benchmark-"+uuid.uuid4().hex)
        self.base.mkdir()

    def tearDown(self):
        assert self.base.resolve().is_relative_to(self.allowed) and self.base.resolve() != self.allowed
        shutil.rmtree(self.base)

    def run_suite(self, **kwargs):
        return run_benchmark(SUITE, scratch_root=self.base/"runs", **kwargs)

    def test_hand_scored_sets_and_empty_failure_semantics(self):
        score = set_scores(["a", "wrong"], ["a", "b"])
        self.assertEqual((score["precision"],score["recall"],score["f1"]), (0.5,0.5,0.5))
        self.assertEqual(score["missing"], ["b"])
        empty = set_scores([], [])
        self.assertIsNone(empty["precision"])
        self.assertIsNone(empty["recall"])
        self.assertTrue(empty["exact_match"])
        self.assertFalse(set_scores([], [], available=False)["exact_match"])
        combined = aggregate([score, set_scores([], ["c"], available=False)])
        self.assertEqual(combined["micro_recall"], 1/3)
        self.assertEqual(combined["failed_tasks"], 1)

    def test_default_suite_runs_all_updates_and_retains_known_gaps(self):
        result = self.run_suite()
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual(len(result["tasks"]), 13)
        self.assertTrue(result["core_regression_pass"])
        self.assertFalse(result["all_tasks_exact"])
        self.assertEqual(result["summary"]["graph"]["tp"], 21)
        self.assertEqual(result["summary"]["graph"]["fn"], 2)
        self.assertIn("hybrid", result["summary"])
        self.assertTrue(all("hybrid_ranking" in r for r in result["tasks"]))
        self.assertEqual(len(result["known_gap_tasks"]), 1)
        self.assertTrue(all(r["fresh_rebuild_equal"] for r in result["tasks"]))
        self.assertFalse(list((self.base/"runs").iterdir()))
        self.assertNotIn("model_summary", result)
        self.assertEqual(result["tasks"][2]["stage"], "new_incoming")
        self.assertIn("late.py::newcomer", result["tasks"][2]["graph"]["predicted"])
        self.assertEqual(result["tasks"][3]["graph"]["predicted"], ["late.py::newcomer"])

    def test_valid_shallow_ir_passes_support_but_fails_independent_recall(self):
        original = compile_impact
        def shallow(store, symbol, **kwargs):
            return original(store, symbol, max_depth=1, **kwargs)
        with patch("tunic.benchmark.compile_impact", side_effect=shallow):
            result = self.run_suite()
        self.assertFalse(result["core_regression_pass"])
        row = result["tasks"][0]
        self.assertTrue(row["validation"]["valid"])
        self.assertTrue(row["fresh_rebuild_equal"])
        self.assertEqual(row["path_support_against_gold"]["precision"], 1)
        self.assertEqual(row["graph"]["recall"], 0.5)

    def test_supported_subset_and_invented_answer_score_separately(self):
        rendered = self.run_suite()["tasks"][0]["rendered"]
        payload = json.loads(rendered["payload"])
        candidate = payload["impacts"][0]
        answer = {"coverage": "partial", "impacts": [{"symbol": candidate["symbol"], "kind": candidate["kind"],
                  "path": candidate["path"], "citations": candidate["required_citations"]}]}
        gold = ["service.py::process", "test_service.py::test_process"]
        score = score_response(rendered, json.dumps(answer), gold)
        self.assertEqual(score["support_precision"], 1)
        self.assertEqual(score["gold"]["recall"], 0.5)
        self.assertEqual(score["accepted_gold"]["recall"], 0.5)
        self.assertFalse(score["task_complete"])
        forged = copy.deepcopy(answer)
        forged["impacts"][0]["symbol"] = "imaginary.py::invented"
        rejected = score_response(rendered, json.dumps(forged), gold)
        self.assertEqual(rejected["support_precision"], 0)
        self.assertEqual(rejected["gold"]["fp"], 1)
        self.assertFalse(rejected["support_gate"]["accepted"])
        self.assertEqual(rejected["accepted_gold"]["recall"], 0)
        malformed_negative = score_response(rendered, "not json", [])
        self.assertFalse(malformed_negative["gold"]["exact_match"])
        self.assertFalse(malformed_negative["task_complete"])
        capped = score_response(rendered, json.dumps(answer), gold, generation_complete=False)
        self.assertFalse(capped["task_complete"])

    def test_model_failure_keeps_every_unrun_slot_in_denominator(self):
        class BrokenModel(ModelFixture):
            def generate(self, *args, **kwargs):
                raise BackendError("model removed during update; repin")
        result = self.run_suite(backend=BrokenModel(), profile={"id": "fixture", "template": TEMPLATE},
                                model_cases=["chain"], model_repeats=2)
        summary = result["model_summary"]
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(summary["expected_runs"], 20)
        self.assertEqual(summary["recorded_runs"], 1)
        self.assertEqual(summary["unrun_or_unscored_runs"], 19)
        self.assertEqual(summary["task_complete_rate"], 0)
        self.assertEqual(summary["upfront"]["gold"]["tasks"], 10)
        self.assertEqual(summary["boundary"]["gold"]["tasks"], 10)
        self.assertEqual(summary["upfront"]["gold"]["micro_recall"], 0)
        self.assertEqual(summary["boundary"]["gold"]["micro_recall"], 0)

    def test_model_track_does_not_equate_support_and_task_completion(self):
        result = self.run_suite(backend=ModelFixture(), profile={"id": "fixture", "template": TEMPLATE}, model_cases=["chain"])
        self.assertEqual(result["model_summary"]["upfront"]["support_precision"], 1)
        self.assertLess(result["model_summary"]["task_complete_rate"], 1)
        self.assertEqual(result["model_summary"]["recorded_runs"], 10)

    def test_fixture_drift_changes_dataset_id_and_unsafe_paths_are_rejected(self):
        _, original = load_suite(SUITE)
        copied = self.base/"suite"
        shutil.copytree(SUITE.parent, copied, ignore=shutil.ignore_patterns("__pycache__"))
        source = copied/"cases/chain/core.py"
        source.write_text(source.read_text()+"\n# deliberate fixture drift\n", encoding="utf-8")
        _, changed = load_suite(copied/"suite.json")
        self.assertNotEqual(original["id"], changed["id"])
        data = json.loads((copied/"suite.json").read_text())
        data["cases"][0]["updates"][0]["writes"] = {"../escape.py": ""}
        (copied/"suite.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "contained"):
            load_suite(copied/"suite.json")

    def test_independent_edge_and_impact_labels_must_agree(self):
        copied = self.base/"suite"
        shutil.copytree(SUITE.parent, copied, ignore=shutil.ignore_patterns("__pycache__"))
        data = json.loads((copied/"suite.json").read_text())
        data["cases"][0]["expected_impacts"] = ["service.py::process"]
        data["cases"][0]["expected_tests"] = []
        (copied/"suite.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "labels disagree"):
            load_suite(copied/"suite.json")

    def test_trusted_gap_fixture_runtime_witnesses(self):
        # Deliberately execute only our checked-in trusted fixtures in subprocesses.
        # The normal ingestion and benchmark engine do not execute analyzed code.
        for case,module,binding in [("method_gap", "worker", "target"), ("dynamic_gap", "dynamic", "target")]:
            script = (f"import {module} as m\nseen=[]\noriginal=m.{binding}\n"
                      "def observed(value):\n    seen.append(value)\n    return original(value)\n"
                      f"m.{binding}=observed\nassert m.entry()==3\nassert seen==[2]\n")
            proc = subprocess.run([sys.executable, "-c", script], cwd=SUITE.parent/"cases"/case,
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_cli_benchmark_check_and_unknown_selector(self):
        proc = subprocess.run([sys.executable, "-m", "tunic", "benchmark", "--check"],
                              capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["core_regression_pass"])
        with self.assertRaisesRegex(ValueError, "selector"):
            self.run_suite(model_cases=["nonexistent"])


if __name__ == "__main__":
    unittest.main()
