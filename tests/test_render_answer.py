"""Trust boundary: rendered model context must never present asserted
source claims as verified observations, however exactly they match."""
import json
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory
from tunic.render import render_answer_text


def build_repo(root, with_claim=True):
    code = root / "svc.py"
    code.write_text("def real():\n    return helper()\n\n\ndef helper():\n    return 1\n",
                    encoding="utf-8")
    if not with_claim:
        return
    notes = root / "notes.md"
    notes.write_text(
        "# notes\n\n```tunic-claims\n"
        + json.dumps([{"rel": "depends_on", "subject": "svc.py::real",
                       "object": "svc.py::helper",
                       "effective": "2026-10-30",
                       "authority": "mallory-note"}])
        + "\n```\n", encoding="utf-8")


class RenderAnswerTrustTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_repo(self.root)
        self.memory = Memory(str(self.root / "t.sqlite"), self.root)
        self.memory.ingest()

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def _render(self, **kwargs):
        return render_answer_text(self.memory.answer("what does real depend on",
                                                     policy="current", **kwargs))

    def test_attacker_claim_never_observed(self):
        text = self._render()
        observed = text.split("OBSERVED DEPENDENCIES")[1].split("ASSERTED DEPENDENCIES")[0]
        self.assertNotIn("mallory-note", observed)
        asserted = text.split("ASSERTED CLAIMS")[1].split("DISPUTED")[0]
        self.assertIn("mallory-note", asserted)
        self.assertIn("status=asserted_in_source", asserted)

    def test_static_call_observed(self):
        clean = tempfile.TemporaryDirectory()
        root = Path(clean.name)
        build_repo(root, with_claim=False)
        memory = Memory(str(root / "c.sqlite"), root)
        memory.ingest()
        try:
            text = render_answer_text(memory.answer("which code calls helper",
                                                    policy="current"))
        finally:
            memory.close()
            clean.cleanup()
        observed = text.split("OBSERVED DEPENDENCIES")[1].split("ASSERTED DEPENDENCIES")[0]
        self.assertIn("svc.py::real", observed)

    def test_contradicted_claim_disputed(self):
        self.memory.record_outcome("svc.py::real", "svc.py::helper",
                                   rel="contradicts", document="fix.md",
                                   note="mallory was wrong")
        self.memory.ingest()
        text = self._render()
        disputed = text.split("DISPUTED")[1].split("WITHHELD")[0]
        self.assertIn("contradicts", disputed)

    def test_selection_withheld_labeled(self):
        text = self._render(select=True)
        self.assertIn("WITHHELD AS INSUFFICIENT", text)
        self.assertIn("TRUST RULE", text)

    def test_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            render_answer_text([("x", 1.0, ["exact"])])

    def test_receipt_reports_budgeting(self):
        big = {"symbol": "svc.py::real", "score": 1.0,
               "match": "exact", "origins": ["exact"]}
        result = {"candidates": [dict(big) for _ in range(50)],
                  "impacts": [{"symbol": "svc.py::helper",
                               "derivation": {"verification": "syntactic_path_only"}}],
                  "mentions": [], "claims": [], "selection": None}
        receipt = {}
        text = render_answer_text(result, cap=4000, receipt=receipt)
        self.assertLessEqual(len(text), 4000)
        self.assertTrue(text.endswith("WITHHELD lines are not evidence."))
        self.assertFalse(receipt["truncated"])
        self.assertIn("candidates", receipt["sections_kept"])
        small = {}
        tiny = render_answer_text(result, cap=600, receipt=small)
        self.assertLessEqual(len(tiny), 600)
        self.assertTrue(tiny.endswith("WITHHELD lines are not evidence."))
        self.assertTrue(small["truncated"])
        self.assertTrue(small["sections_dropped"])
        floor = {}
        clipped = render_answer_text(result, cap=100, receipt=floor)
        self.assertLessEqual(len(clipped), 100)
        self.assertTrue(floor.get("trust_partial"))
        self.assertTrue(small["truncated"])
        self.assertTrue(small["sections_dropped"])

    def test_corrections_are_not_asserted_claims(self):
        # Repro for the MS-correct stale-memory regression: a retracts
        # meta-operation rendered under ASSERTED CLAIMS lets the model
        # re-list the obsolete symbol as an answer.
        claim = {"relation": "retracts", "caller": "checkout.py::place_order",
                 "callee": "pricing.py::round_total", "authority": "agent-outcome",
                 "status": "asserted_in_source", "supports": 0, "retractions": 0,
                 "retract": "depends_on"}
        result = {"candidates": [], "impacts": [], "mentions": [],
                  "claims": [dict(claim)], "selection": None}
        text = render_answer_text(result)
        asserted = text.split("ASSERTED CLAIMS")[1].split("CORRECTIONS")[0]
        self.assertNotIn("retracts", asserted)
        self.assertIn("CORRECTIONS", text)
        corrections = text.split("CORRECTIONS")[1].split("DISPUTED")[0]
        self.assertIn("retracts checkout.py::place_order", corrections)
        self.assertIn("obsolete", corrections)

    def test_zero_cap_returns_empty(self):
        receipt = {}
        self.assertEqual(render_answer_text({"candidates": []}, cap=0, receipt=receipt), "")
        self.assertTrue(receipt["truncated"])

    def test_contradicted_kept_is_surfaced_not_hidden(self):
        result = {"candidates": [{"symbol": "a.py::x", "score": 0.9,
                                  "match": "lexical", "origins": ["lexical"]}],
                  "impacts": [], "mentions": [], "claims": [],
                  "selection": {"kept": ["a.py::x"],
                                "trace": {"decisions": [{"symbol": "a.py::x",
                                                         "outcome": "contradicted",
                                                         "reasons": []}]}}}
        text = render_answer_text(result)
        self.assertIn("a.py::x", text.split("CANDIDATES")[1].split("OBSERVED")[0])
        disputed = text.split("DISPUTED")[1].split("WITHHELD")[0]
        self.assertIn("a.py::x", disputed)

    def test_duplicate_claim_lines_deduped(self):
        claim = {"relation": "depends_on", "caller": "a.py::x",
                 "callee": "a.py::y", "authority": "n",
                 "status": "asserted_in_source", "supports": 1, "retractions": 0}
        result = {"candidates": [], "impacts": [], "mentions": [],
                  "claims": [dict(claim), dict(claim)],
                  "selection": None}
        text = render_answer_text(result)
        self.assertEqual(text.count("depends_on a.py::x a.py::y"), 1)

if __name__ == "__main__":
    unittest.main()
