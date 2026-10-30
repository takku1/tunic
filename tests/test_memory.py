"""Agent-facing memory sessions sharing one database across processes."""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tunic.baselines import BM25, direct_callers
from tunic.compiler import validate_artifact
from tunic.memory import Memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self.db = str(self.tmp / "memory.sqlite")
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")

    def test_outcome_recorded_in_one_session_reads_back_in_another(self):
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")
        first = Memory(self.db, self.root)
        self.addCleanup(first.close)
        first.note("Investigating `core.py::calculate`.", document="notes/session.md")
        first.ingest()
        self.assertTrue(any(m["status"] == "syntactic_observation"
                            for m in first.related("core.py::calculate")["mentions"]["mentions"]))
        first.record_outcome("service.py::process", "core.py::calculate")
        first.ingest()
        first.close()
        second = Memory(self.db, self.root)
        self.addCleanup(second.close)
        related = second.related("core.py::calculate")
        self.assertTrue(any(c["relation"] == "depends_on" and c["authority"] == "agent-outcome"
                            for c in related["claims"]["claims"]))

    def test_record_outcome_validates_and_defaults(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        with self.assertRaises(ValueError):
            memory.record_outcome("core.py::calculate", "core.py::calculate", rel="teleports")
        with self.assertRaises(ValueError):
            memory.record_outcome("not a key!", "core.py::calculate")
        block = memory.record_outcome("service.py::process", "core.py::calculate")
        self.assertIn('"rel": "depends_on"', block)
        self.assertRegex(block, r'"effective": "\d{4}-\d{2}-\d{2}"')

    def test_answer_resolves_task_without_exact_symbol(self):
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        result = memory.answer("Which functions break if calculate changes?")
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual([c["symbol"] for c in result["candidates"]], ["core.py::calculate"])
        self.assertEqual(result["candidates"][0]["match"], "name")
        self.assertIn("service.py::process", [i["symbol"] for i in result["impacts"]])
        empty = memory.answer("How do I bake bread?")
        self.assertEqual(empty["outcome"], "empty")
        self.assertEqual(empty["candidates"], [])

    def test_verified_learning_pipeline_propose_approve_correct(self):
        (self.root / "mid.py").write_bytes(b"def transform(value):\n    return value * 2\n")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.note("Compare `mid.py::transform` with `core.py::calculate`.", document="notes.md")
        memory.ingest()
        drafts = memory.draft("notes.md")
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        memory.record_outcome(draft["subject"], draft["object"], authority="agent-review")
        memory.ingest()
        asserted = [c for c in memory.claims("core.py::calculate")["claims"]
                    if c["status"] == "asserted_in_source"]
        self.assertEqual(len(asserted), 1)
        memory.record_outcome(draft["subject"], draft["object"], rel="contradicts",
                              authority="agent-correction")
        memory.ingest()
        claims = memory.claims("core.py::calculate")["claims"]
        by_relation = {}
        for claim in claims:
            by_relation.setdefault(claim["relation"], []).append(claim["status"])
        self.assertEqual(by_relation["depends_on"], ["asserted_in_source"])
        self.assertEqual(by_relation["contradicts"], ["asserted_in_source"])
        line = memory.timeline("core.py::calculate")
        self.assertEqual(line["conflicts"], [["mid.py::transform", "core.py::calculate"]])
        self.assertIn("mid.py::transform",
                      [i["symbol"] for i in memory.impact("core.py::calculate")["impacts"]])

    def test_note_and_draft_round_trip_through_api(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        (self.root / "mid.py").write_bytes(b"def transform(value):\n    return value * 2\n")
        memory.note("Compare `mid.py::transform` with `core.py::calculate`.", document="notes.md")
        memory.ingest()
        drafts = memory.draft("notes.md")
        self.assertEqual([(d["subject"], d["object"]) for d in drafts],
                         [("mid.py::transform", "core.py::calculate")])


    def test_memory_repair_beats_static_and_lexical_baselines(self):
        shutil.copytree(Path(__file__).resolve().parents[1] / "benchmarks" / "w1" / "cases" / "dynamic_gap",
                        self.root / "gap", ignore=shutil.ignore_patterns("__pycache__"))
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        target = "gap/dynamic.py::target"
        gold = {"gap/dynamic.py::indirect", "gap/dynamic.py::entry"}
        self.assertEqual(memory.impact(target)["impacts"], [])
        self.assertEqual(direct_callers(memory.store, target, memory.store.current()), [])
        ranking = BM25(str(self.root / "gap")).search("gap/dynamic.py::target")
        self.assertNotEqual({d["symbol"] for d in ranking}, gold)
        memory.record_outcome("gap/dynamic.py::indirect", target, authority="verified-outcome")
        memory.record_outcome("gap/dynamic.py::entry", "gap/dynamic.py::indirect",
                              authority="verified-outcome")
        memory.ingest()
        repaired = memory.impact(target)
        self.assertEqual({i["symbol"] for i in repaired["impacts"]}, gold)
        self.assertTrue(all(i["derivation"]["verification"] == "asserted_not_observed"
                            for i in repaired["impacts"]))
        self.assertTrue(validate_artifact(memory.store, repaired)["valid"])


if __name__ == "__main__":
    unittest.main()
