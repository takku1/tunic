"""Temporal correctness: bitemporal views, backdated corrections,
multi-support retraction, reproducible history.

Clock-proof dates: 2000-01-01 is always past (active), 2999-01-01 is
always future (inactive). Snapshots commit on the real clock; tests
only rely on their order, never their value.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic import (GraphStore, compile_claims, compile_history, compile_impact,
                   compile_timeline)
from tunic.memory import Memory

PAST = "2000-01-01"
FUTURE = "2999-01-01"

CLAIM = ("```tunic-claims\n%s\n```\n")


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TemporalFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        write(self.root / "svc.py",
              "def pay():\n    return 1\n\n\ndef pay_v2():\n    return 2\n\n\n"
              "def checkout():\n    return pay()\n")
        self.store = GraphStore(str(self.tmp / "graph.sqlite"))
        self.addCleanup(self.store.close)

    def ingest_notes(self, text, name="notes/outcomes.md"):
        write(self.root / name, text)
        return self.store.ingest(str(self.root))

    def claim_block(self, rel, subject, obj, effective=PAST, extra=""):
        import json
        claim = {"rel": rel, "subject": subject, "object": obj,
                 "effective": effective, "authority": "test"}
        if extra:
            claim.update(extra)
        return "# t\n\n" + CLAIM % json.dumps([claim])


class BitemporalTests(TemporalFixture):
    def test_history_reproduces_what_was_known(self):
        first = self.ingest_notes(self.claim_block(
            "depends_on", "svc.py::checkout", "svc.py::pay"))
        snap1 = first["snapshot"]
        second = self.ingest_notes(self.claim_block(
            "supersedes", "svc.py::pay_v2", "svc.py::pay"),
            name="notes/change.md")
        snap2 = second["snapshot"]
        self.assertNotEqual(snap1, snap2)
        old_view = compile_timeline(self.store, "svc.py::pay", snapshot=snap1,
                                    at="2999-06-01")
        self.assertEqual(old_view["superseded_by"], [])
        self.assertTrue(any(e["kind"] == "claim"
                            and e["relation"] == "depends_on"
                            for e in old_view["entries"]))
        new_view = compile_timeline(self.store, "svc.py::pay", snapshot=snap2,
                                    at="2999-06-01")
        self.assertTrue(new_view["superseded_by"])
        for entry in old_view["entries"]:
            self.assertEqual(entry["recorded"], self.store.committed_on(snap1))
        for entry in new_view["entries"]:
            self.assertEqual(entry["recorded"], self.store.committed_on(snap2))

    def test_future_entries_filtered_never_deleted(self):
        self.ingest_notes(self.claim_block(
            "depends_on", "svc.py::checkout", "svc.py::pay", effective=FUTURE))
        early = compile_timeline(self.store, "svc.py::checkout", at=PAST)
        self.assertFalse(any(e["kind"] == "claim" for e in early["entries"]))
        late = compile_timeline(self.store, "svc.py::checkout", at=FUTURE)
        self.assertTrue(any(e["kind"] == "claim" for e in late["entries"]))


class CorrectionTests(TemporalFixture):
    def test_backdated_correction_blocks_and_marks(self):
        self.ingest_notes(self.claim_block(
            "depends_on", "svc.py::checkout", "svc.py::pay"))
        self.ingest_notes(self.claim_block(
            "corrects", "svc.py::pay_v2", "svc.py::pay"),
            name="notes/correction.md")
        timeline = compile_timeline(self.store, "svc.py::pay")
        self.assertTrue(timeline["corrected_by"])
        self.assertEqual(timeline["superseded_by"], [])
        by = timeline["corrected_by"][0]
        self.assertEqual(by["by"], "svc.py::pay_v2")
        self.assertIn("recorded", by)
        impact = compile_impact(self.store, "svc.py::pay", policy="current")
        self.assertFalse(any(e["symbol"] == "svc.py::checkout" for e in impact["impacts"]))
        open_impact = compile_impact(self.store, "svc.py::pay",
                                     policy="transparent")
        self.assertTrue(any(e["symbol"] == "svc.py::checkout" for e in open_impact["impacts"]))


class RetractionTests(TemporalFixture):
    def support(self, name, effective=PAST):
        return self.claim_block("depends_on", "svc.py::checkout", "svc.py::pay",
                                effective=effective)

    def retract(self):
        return self.claim_block("retracts", "svc.py::checkout", "svc.py::pay",
                                extra={"retract": "depends_on"})

    def asserted_depends(self, result):
        return [u for u in result["units"]
                if u["assertion"]["proposition"].get("relation") == "depends_on"]

    def test_one_retraction_kills_single_support(self):
        self.ingest_notes(self.support("a"))
        self.ingest_notes(self.retract(), name="notes/retract.md")
        impact = compile_impact(self.store, "svc.py::pay", policy="current")
        self.assertEqual(self.asserted_depends(impact), [])
        self.assertTrue(any(e["symbol"] == "svc.py::checkout" for e in impact["impacts"]))
        open_impact = compile_impact(self.store, "svc.py::pay", policy="transparent")
        self.assertTrue(self.asserted_depends(open_impact))
        claims = compile_claims(self.store, "svc.py::pay")
        edge = [c for c in claims["claims"] if c["relation"] == "depends_on"][0]
        self.assertEqual((edge["supports"], edge["retractions"]), (1, 1))

    def test_second_support_survives_one_retraction(self):
        self.ingest_notes(self.support("a"))
        self.ingest_notes(self.support("b"), name="notes/second.md")
        self.ingest_notes(self.retract(), name="notes/retract.md")
        impact = compile_impact(self.store, "svc.py::pay", policy="current")
        self.assertTrue(self.asserted_depends(impact))
        claims = compile_claims(self.store, "svc.py::pay")
        edge = [c for c in claims["claims"] if c["relation"] == "depends_on"][0]
        self.assertEqual((edge["supports"], edge["retractions"]), (2, 1))
        self.ingest_notes(self.retract(), name="notes/retract2.md")
        impact = compile_impact(self.store, "svc.py::pay", policy="current")
        self.assertEqual(self.asserted_depends(impact), [])

    def test_retracting_observation_is_rejected(self):
        report = self.ingest_notes(self.claim_block(
            "retracts", "svc.py::checkout", "svc.py::pay", extra={"retract": "static_call"}))
        kinds = [d["kind"] for d in report["diagnostics"]]
        self.assertIn("invalid_retract", kinds)
        impact = compile_impact(self.store, "svc.py::pay", policy="current")
        self.assertTrue(any(e["symbol"] == "svc.py::checkout" for e in impact["impacts"]))

    def test_named_retraction_stays_proposal(self):
        self.ingest_notes(self.support("a"))
        self.ingest_notes("# t\n\n```tunic-claims\n"
                          '[{"rel": "retracts", "subject": "checkout", '
                          '"object": "svc.py::pay", "retract": "depends_on", '
                          '"effective": "%s"}]\n```\n' % PAST,
                          name="notes/named.md")
        impact = compile_impact(self.store, "svc.py::pay", policy="current")
        self.assertTrue(self.asserted_depends(impact))


class HistoryTests(TemporalFixture):
    def test_history_orders_snapshots_and_skips_unknown(self):
        self.ingest_notes(self.claim_block(
            "depends_on", "svc.py::checkout", "svc.py::pay"))
        write(self.root / "extra.py", "def helper():\n    return 2\n")
        self.store.ingest(str(self.root))
        history = compile_history(self.store, "extra.py::helper")
        self.assertEqual(len(history["snapshots"]), 1)
        self.assertIn("committed_on", history["snapshots"][0])
        full = compile_history(self.store, "svc.py::pay")
        self.assertGreaterEqual(len(full["snapshots"]), 2)
        dates = [s["committed_on"] for s in full["snapshots"]]
        self.assertEqual(dates, sorted(dates))

    def test_outcome_empty_without_snapshots(self):
        history = compile_history(self.store, "svc.py::pay")
        self.assertEqual((history["outcome"], history["snapshots"]), ("empty", []))


class RecordOutcomeTests(TemporalFixture):
    def test_record_outcome_validates_retract(self):
        memory = Memory(str(self.tmp / "m.sqlite"), self.root)
        self.addCleanup(memory.close)
        with self.assertRaises(ValueError):
            memory.record_outcome("svc.py::checkout", "svc.py::pay",
                                  rel="retracts")
        with self.assertRaises(ValueError):
            memory.record_outcome("svc.py::checkout", "svc.py::pay",
                                  rel="depends_on", retract="depends_on")
        block = memory.record_outcome("svc.py::checkout", "svc.py::pay",
                                      rel="retracts", retract="depends_on")
        self.assertIn('"retract": "depends_on"', block)


if __name__ == "__main__":
    unittest.main()
