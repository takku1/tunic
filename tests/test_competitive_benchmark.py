"""Anti-gaming invariants for common benchmark scoring and release identity."""

import copy
from pathlib import Path
import shutil
import tempfile
import unittest

from benchmarks.competitive.release import ROOT, create_lock, inventory, load, verify
from benchmarks.competitive.score import score


class CompetitiveBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "release"
        shutil.copytree(ROOT, self.root,
                        ignore=shutil.ignore_patterns("release.lock.json", "__pycache__"))
        self.digest = create_lock(self.root)["digest"]
        self.tasks = load(self.root / "tasks.json")
        self.gold = load(self.root / "gold.json")

    def result(self, track="agent"):
        return {"benchmark_digest": self.digest, "arm": "tunic", "track": track,
                "cohort": {"model": "test-double; not an inference result"}, "rows": []}

    def oracle(self):
        result = self.result()
        result["rows"] = [{"id": task["id"], "status": "complete",
                           "answer": self.gold[task["id"]]["answer"],
                           "abstain": not self.gold[task["id"]]["answer"],
                           "evidence": self.gold[task["id"]]["evidence"],
                           "measurements": {"retrieval_latency_ms": 10}}
                          for task in self.tasks]
        return result

    def test_oracle_is_scoring_check_only(self):
        report = score(self.root, self.oracle())
        self.assertEqual(report["task_success"], 1)
        self.assertEqual(report["evidence_source_recall"], 1)

    def test_missing_task_stays_in_denominator_and_telemetry_is_unknown(self):
        result = self.oracle()
        result["rows"].pop()
        report = score(self.root, result)
        self.assertEqual(report["task_success"], (len(self.tasks) - 1) / len(self.tasks))
        self.assertIsNone(report["resources"]["retrieval_latency_ms"]["p95"])
        self.assertEqual(report["tasks"][-1]["status"], "missing")

    def test_error_with_correct_answer_is_failure(self):
        result = self.oracle()
        result["rows"][0]["status"] = "error"
        self.assertFalse(score(self.root, result)["tasks"][0]["success"])

    def test_timeout_is_not_cheap_missing_telemetry(self):
        result = self.oracle()
        result["rows"][0].update(status="timeout", measurements={})
        report = score(self.root, result)
        self.assertIsNone(report["resources"]["retrieval_latency_ms"]["mean"])

    def test_retrieval_never_becomes_agent_accuracy(self):
        result = self.oracle()
        result["track"] = "retrieval"
        report = score(self.root, result)
        self.assertIsNone(report["task_success"])
        self.assertIsNone(report["abstention_accuracy"])

    def test_bad_extra_answer_fails_exact_match(self):
        result = copy.deepcopy(self.oracle())
        result["rows"][0]["answer"].append("invented")
        self.assertFalse(score(self.root, result)["tasks"][0]["success"])

    def test_duplicates_unknown_ids_and_foreign_releases_are_rejected(self):
        cases = []
        duplicate = self.oracle()
        duplicate["rows"].append(duplicate["rows"][0])
        cases.append(duplicate)
        unknown = self.oracle()
        unknown["rows"][0]["id"] = "not-scheduled"
        cases.append(unknown)
        foreign = self.oracle()
        foreign["benchmark_digest"] = "another-release"
        cases.append(foreign)
        for result in cases:
            with self.assertRaises(ValueError):
                score(self.root, result)

    def test_invalid_measurements_are_rejected(self):
        for value in (-1, True, float("nan"), float("inf"), "10"):
            result = self.oracle()
            result["rows"][0]["measurements"]["cost_usd"] = value
            with self.assertRaises(ValueError):
                score(self.root, result)

    def test_lock_cannot_be_overwritten(self):
        with self.assertRaises(FileExistsError):
            create_lock(self.root)

    def test_drift_of_sources_tasks_gold_or_scorer_is_rejected(self):
        for relative in ("corpus/01-launch.md", "tasks.json", "gold.json", "score.py"):
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.assertRaises(ValueError):
                verify(self.root)
            path.write_bytes(original)
        self.assertEqual(verify(self.root), self.digest)

    def test_undeclared_corpus_file_is_rejected(self):
        (self.root / "corpus/extra.md").write_text("leaked or unversioned context")
        with self.assertRaises(ValueError):
            inventory(self.root)


if __name__ == "__main__":
    unittest.main()
