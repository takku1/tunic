"""Scorecard contract: benchmarks/competitive-scorecard.json stays versioned,
every entry carries the canonical provenance fields, frozen-claim artifacts
exist on disk, and no competitor figure can pose as a measured result."""

import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCORECARD = REPO / "benchmarks" / "competitive-scorecard.json"

REQUIRED_RESULT_FIELDS = {
    "benchmark",
    "dataset_version",
    "system",
    "system_version",
    "model",
    "hardware",
    "retrieval_budget",
    "metric",
    "accuracy",
    "confidence_interval",
    "latency",
    "tokens_per_answer",
    "cost_per_1k_queries",
    "evidence_status",
    "artifact",
}

EVIDENCE_WITH_ARTIFACT = {"frozen-artifact", "development-smoke"}


def load():
    return json.loads(SCORECARD.read_text(encoding="utf-8"))


class TestCompetitiveScorecard(unittest.TestCase):
    def test_file_is_versioned(self):
        data = load()
        self.assertEqual(data["version"], "1")
        self.assertTrue(data["date"])
        self.assertTrue(data["tunic_commit_head"])
        self.assertIn("reading_rule", data)

    def test_tunic_entries_have_full_provenance(self):
        data = load()
        self.assertTrue(data["tunic_results"])
        for entry in data["tunic_results"]:
            missing = REQUIRED_RESULT_FIELDS - set(entry)
            self.assertFalse(missing, "tunic entry missing %s" % sorted(missing))

    def test_measured_claims_link_to_committed_artifacts(self):
        data = load()
        for entry in data["tunic_results"]:
            if entry["evidence_status"] in EVIDENCE_WITH_ARTIFACT:
                self.assertTrue(entry["artifact"], "measured entry needs artifact")
                self.assertTrue(
                    (REPO / entry["artifact"]).is_file(),
                    "missing artifact %s" % entry["artifact"],
                )

    def test_competitor_figures_marked_unverified_without_artifacts(self):
        data = load()
        self.assertTrue(data["competitor_published"])
        for entry in data["competitor_published"]:
            self.assertEqual(
                entry["evidence_status"],
                "user-supplied-unverified",
                "competitor figure must not pose as measured: %s/%s"
                % (entry["system"], entry["benchmark"]),
            )
            self.assertIsNone(entry["artifact"])

    def test_no_cross_benchmark_ranking(self):
        data = load()
        self.assertIn("denominator", data["reading_rule"].lower())
        self.assertTrue(data["not_tested"])
        self.assertTrue(data["next_metrics"])
        for row in data["next_metrics"]:
            self.assertIn("metric", row)
            self.assertIn("status", row)
            self.assertIn(row["status"], {"not-tested", "partial"})


if __name__ == "__main__":
    unittest.main()
