"""Scorecard contract: benchmarks/competitive-scorecard.json stays versioned,
every entry carries the canonical provenance fields, frozen-claim artifacts
exist on disk, and no competitor figure can pose as a measured result."""

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCORECARD = REPO / "benchmarks" / "competitive-scorecard.json"

sys.path.insert(0, str(REPO / "benchmarks/agent"))
from run_acq_v2 import summarize_acq  # noqa: E402

sys.path.pop(0)

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

    def test_dev_and_smoke_values_recompute_from_raw_rows(self):
        """Scorecard numbers for the dev and smoke artifacts must equal
        the rates recomputed from the raw task rows (not the artifact's
        own summary block). Heldout entries are existence-checked only:
        the heldout suite is run-once and stays untouched."""
        data = load()
        metric_field = {"acquisition-recall": "pass_rate",
                        "safe-refusal": "pass_rate",
                        "poison-resistance": "pass_rate",
                        "correction-apply": "apply_pass_rate"}
        for entry in data["tunic_results"]:
            if entry.get("suite") == "heldout":
                continue
            artifact = entry.get("artifact", "")
            if "runs/acq-v2" not in artifact:
                continue
            record = json.loads((REPO / artifact).read_text(encoding="utf-8"))
            summary = summarize_acq(record["tasks"])
            cell = summary["%s/%s/%s" % (entry["suite"], entry["scenario"],
                                         entry["arm"])]
            self.assertAlmostEqual(entry["accuracy"],
                                   cell[metric_field[entry["metric"]]])
            self.assertEqual(cell["n"], 5, "scorecard cells are 5-rep")

    def test_frontier_counts_recompute_from_closure(self):
        data = load()
        closure = json.loads(
            (REPO / "benchmarks/frontier/closures/pyquifer-constructors-v1.json"
             ).read_text(encoding="utf-8"))
        expected = {"constructor-candidates-proposed":
                    closure["after"]["constructor_call_candidates"],
                    "class-symbols-recovered":
                    closure["after"]["class_symbols"]}
        for entry in data["tunic_results"]:
            if entry["metric"] in expected:
                self.assertEqual(entry["accuracy"], expected[entry["metric"]])

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
