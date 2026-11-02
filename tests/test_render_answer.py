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


class MethodContainerAnnotationTests(unittest.TestCase):
    """Method candidates carry their class so renderings bridge
    method questions to the class (and through it to construction
    candidates) — the ACQ v2 constructor-gap link."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "shop.py").write_text(
            "class Gadget:\n"
            "    def start(self):\n"
            "        return 1\n"
            "\n\n"
            "def create():\n"
            "    return Gadget()\n", encoding="utf-8")
        self.memory = Memory(str(self.root / "t.sqlite"), self.root)
        self.memory.ingest()

    def tearDown(self):
        self.memory.close()
        self.tmp.cleanup()

    def test_method_candidate_carries_container(self):
        result = self.memory.answer("what uses start", policy="current")
        method = next(c for c in result["candidates"]
                      if c["symbol"] == "shop.py::Gadget.start")
        self.assertEqual(method.get("container"), "shop.py::Gadget")

    def test_render_annotates_method_of_class(self):
        text = render_answer_text(
            self.memory.answer("what uses start", policy="current"))
        self.assertIn("shop.py::Gadget.start [best match: name; stages: name,lexical; "
                      "method of shop.py::Gadget]", text)

    def test_plain_function_has_no_container(self):
        result = self.memory.answer("what uses create", policy="current")
        func = next(c for c in result["candidates"]
                    if c["symbol"] == "shop.py::create")
        self.assertNotIn("container", func)

    def test_nested_function_never_a_candidate(self):
        # nested scopes are excluded from the static profile entirely
        # (nested_scope diagnostic), so a dotted non-method key cannot
        # arise and the container annotation can only name classes
        (self.root / "nest.py").write_text(
            "def outer():\n"
            "    def inner():\n"
            "        return 1\n"
            "    return inner()\n", encoding="utf-8")
        self.memory.ingest()
        result = self.memory.answer("what uses inner", policy="current")
        self.assertNotIn("nest.py::outer.inner",
                         [c["symbol"] for c in result["candidates"]])


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
        # The trust rule itself grew (container-context sentence), so at
        # this cap the rule may not fit whole. The contract is no SILENT
        # truncation: either the full rule is emitted or the receipt flags
        # trust_partial. Pinning the exact cap where the full rule fits
        # would couple the test to trust wording length, not the contract.
        if tiny.endswith("WITHHELD lines are not evidence."):
            self.assertTrue(small["truncated"])
        else:
            self.assertTrue(small.get("trust_partial"))
            self.assertTrue(tiny)
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

    def test_mention_lines_carry_prose_window(self):
        mention = {"mentioner": "notes/decisions.md::document",
                   "candidate": "pricing.py::round_total",
                   "status": "syntactic_observation",
                   "evidence": {"window": "We round down at totals; see more here."}}
        result = {"candidates": [], "impacts": [], "mentions": [mention],
                  "claims": [], "selection": None}
        text = render_answer_text(result)
        mentions = text.split("TEXT MENTIONS")[1].split("TRUST RULE")[0]
        self.assertIn("We round down at totals", mentions)

    def test_zero_cap_returns_empty(self):
        receipt = {}
        self.assertEqual(render_answer_text({"candidates": []}, cap=0, receipt=receipt), "")
        self.assertTrue(receipt["truncated"])

    def test_live_withdrawal_is_history_not_prohibition(self):
        live = {"relation": "depends_on", "caller": "a.py::x",
                "callee": "a.py::y", "authority": "n",
                "status": "asserted_in_source", "supports": 2, "retractions": 1}
        drop = {"relation": "retracts", "caller": "a.py::x",
                "callee": "a.py::y", "authority": "n",
                "status": "asserted_in_source", "supports": 0, "retractions": 0,
                "retract": "depends_on"}
        result = {"candidates": [], "impacts": [], "mentions": [],
                  "claims": [dict(live), dict(drop)], "selection": None}
        text = render_answer_text(result)
        corrections = text.split("CORRECTIONS")[1].split("DISPUTED")[0]
        self.assertIn("survives on remaining support", corrections)
        self.assertNotIn("neither endpoint is an answer", corrections)

    def test_dead_edge_withdrawal_forbids_endpoints(self):
        dead = {"relation": "depends_on", "caller": "a.py::x",
                "callee": "a.py::y", "authority": "n",
                "status": "asserted_in_source", "supports": 1, "retractions": 1}
        drop = {"relation": "retracts", "caller": "a.py::x",
                "callee": "a.py::y", "authority": "n",
                "status": "asserted_in_source", "supports": 0, "retractions": 0,
                "retract": "depends_on"}
        result = {"candidates": [], "impacts": [], "mentions": [],
                  "claims": [dict(dead), dict(drop)], "selection": None}
        text = render_answer_text(result)
        corrections = text.split("CORRECTIONS")[1].split("DISPUTED")[0]
        self.assertIn("neither endpoint is an answer", corrections)

    def test_supersession_names_current_successor(self):
        old = {"relation": "decided_by", "caller": "a.py::x",
               "callee": "a.py::old", "authority": "n",
               "status": "asserted_in_source", "supports": 1, "retractions": 0}
        new = {"relation": "supersedes", "caller": "a.py::new",
               "callee": "a.py::old", "authority": "n",
               "status": "asserted_in_source", "supports": 0, "retractions": 0}
        result = {"candidates": [], "impacts": [], "mentions": [],
                  "claims": [dict(old), dict(new)], "selection": None}
        text = render_answer_text(result)
        corrections = text.split("CORRECTIONS")[1].split("DISPUTED")[0]
        self.assertIn("a.py::old is obsolete", corrections)
        self.assertIn("a.py::new may be current", corrections)
        self.assertNotIn("neither endpoint is an answer", corrections)

    def test_withheld_carries_successor_pointer(self):
        result = {"candidates": [], "impacts": [], "mentions": [],
                  "claims": [],
                  "selection": {"kept": [],
                                "trace": {"decisions": [{"symbol": "a.py::old",
                                                         "outcome": "out_of_scope",
                                                         "reasons": ["superseded by a.py::new",
                                                                     "under current policy"]}]}}}
        text = render_answer_text(result)
        withheld = text.split("WITHHELD")[1].split("TEXT MENTIONS")[0]
        self.assertIn("a.py::old", withheld)
        self.assertIn("superseded by a.py::new", withheld)

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
