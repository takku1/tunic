"""Selection pilot verdict consistency: the report must derive its own verdict.

Reads benchmarks/arena/runs/selection-pilot-dev-v1.json and
recomputes the adoption verdict from the recorded numbers under the
predeclared criteria frozen here. Prevents post-hoc reinterpretation:
regenerating the report re-checks the decision, and changing the
criteria breaks this test by design.
"""
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORT = (REPO / "benchmarks" / "arena" / "runs" /
          "selection-pilot-dev-v1.json")

PREDECLARED_CRITERIA = [
    "micro-F1(select) > micro-F1(fusion) AND micro-P(select) > micro-P(fusion)",
    "micro-R(select) >= micro-R(fusion)",
    "decoy/empty-gold non-empty rate (select) <= (fusion)",
    "false-abstain rate (select) <= (fusion)",
    "p95 latency(select) < 2 * p95 latency(fusion)",
]


class SelectionPilotVerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_artifact_identity(self):
        self.assertEqual(self.report["benchmark"], "selection-pilot-dev-v1")
        self.assertEqual(self.report["criteria"], PREDECLARED_CRITERIA)
        self.assertIn("hybrid-select", self.report["summary"])
        for task in self.report["tasks"]:
            self.assertEqual(set(task["arms"]),
                             {"bm25-only", "full-hybrid", "hybrid-select"})

    def test_checks_match_numbers(self):
        summary = self.report["summary"]
        sel, fus = summary["hybrid-select"], summary["full-hybrid"]
        expected = {
            "f1_up": sel["micro_f1"] > fus["micro_f1"],
            "precision_up": (sel["micro_precision"] >
                             fus["micro_precision"]),
            "recall_held": sel["micro_recall"] >= fus["micro_recall"],
            "decoy_no_worse": (sel["decoy_nonempty_rate"] <=
                               fus["decoy_nonempty_rate"]),
            "abstain_no_worse": (sel["false_abstain_rate"] <=
                                 fus["false_abstain_rate"]),
            "latency_ok": (sel["p95_latency_ms"] <
                           2 * fus["p95_latency_ms"]),
        }
        self.assertEqual(self.report["checks"], expected)

    def test_verdict_matches_checks(self):
        expected = ("ADOPT" if all(self.report["checks"].values())
                    else "RETAIN")
        self.assertEqual(self.report["verdict"], expected)
        self.assertEqual(sorted(self.report["failing_checks"]),
                         sorted(k for k, v in
                                self.report["checks"].items() if not v))

    def test_bm25_gap_reported_as_context(self):
        self.assertIn("bm25_f1_gap_select_minus_bm25", self.report)
        self.assertIsInstance(
            self.report["bm25_f1_gap_select_minus_bm25"], float)


if __name__ == "__main__":
    unittest.main()
