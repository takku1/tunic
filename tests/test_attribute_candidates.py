"""Attribute-dispatch candidates: methods on provably known receivers.

A variable bound exactly once to a resolvable constructor call
(`t = Tool()`), used at `t.method(...)` after the binding, proves the
receiver type: the call emits a proposed depends_on candidate
(via attribute_dispatch) with binding + constructor + call derivation,
and no unresolved_call diagnostic. Reassigned, shadowed, unknown, or
use-before-binding receivers stay unresolved — receiver types are never
guessed.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.compiler import compile_impact
from tunic.memory import Memory, replay_regression_case
from tunic.store import GraphStore


class AttributeCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "tool.py").write_bytes(
            b"class Tool:\n"
            b"    def work(self, value):\n        return value\n\n"
            b"    def rest(self):\n        return 0\n")
        self.db = str(self.tmp / "memory.sqlite")

    def _write_user(self, body):
        (self.root / "user.py").write_bytes(body.encode("utf-8"))

    def _memory(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        return memory

    def _attribute_candidates(self, memory):
        return [c for c in memory.candidates()["candidates"]
                if c.get("via") == "attribute_dispatch"]

    def test_known_receiver_proposes_candidate_without_diagnostic(self):
        self._write_user(
            "from tool import Tool\n\n\n"
            "def drive(value):\n"
            "    t = Tool()\n"
            "    return t.work(value)\n")
        memory = self._memory()
        candidates = self._attribute_candidates(memory)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["relation"], "depends_on")
        self.assertEqual(candidate["caller"], "user.py::drive")
        self.assertEqual(candidate["callee"], "tool.py::Tool.work")
        self.assertEqual(candidate["status"], "proposed")
        self.assertEqual(candidate["rule"], "known_receiver_method_candidate_v1")
        self.assertEqual(candidate["derivation"]["receiver"], "t")
        self.assertEqual(candidate["derivation"]["class"], "Tool")
        self.assertEqual(candidate["derivation"]["method"], "work")
        # the method-call gap is bridged; the only remaining diagnostic
        # is the bare constructor call itself (the separate,
        # out-of-scope class-extraction gap)
        calls = [e for e in memory.frontier_list(status="open")
                 if e["failure"] == "ingest_unresolved_call"]
        self.assertEqual({e["target"] for e in calls}, {"Tool"})

    def test_local_class_receiver(self):
        (self.root / "local.py").write_bytes(
            b"class Box:\n"
            b"    def open(self):\n        return 1\n\n\n"
            b"def use():\n"
            b"    b = Box()\n"
            b"    return b.open()\n")
        memory = self._memory()
        candidates = self._attribute_candidates(memory)
        self.assertEqual([(c["caller"], c["callee"]) for c in candidates],
                         [("local.py::use", "local.py::Box.open")])

    def test_multiple_methods_and_callers(self):
        self._write_user(
            "from tool import Tool\n\n\n"
            "def drive():\n"
            "    t = Tool()\n"
            "    a = t.work(1)\n"
            "    b = t.rest()\n"
            "    return a + b\n\n\n"
            "def ride():\n"
            "    u = Tool()\n"
            "    return u.rest()\n")
        memory = self._memory()
        candidates = self._attribute_candidates(memory)
        self.assertEqual(len(candidates), 3)
        self.assertEqual({c["callee"] for c in candidates},
                         {"tool.py::Tool.work", "tool.py::Tool.rest"})

    def test_candidates_never_traverse_until_promoted(self):
        self._write_user(
            "from tool import Tool\n\n\n"
            "def drive(value):\n"
            "    t = Tool()\n"
            "    return t.work(value)\n")
        memory = self._memory()
        impact = compile_impact(memory.store, "tool.py::Tool.work")
        reached = {unit["assertion"]["proposition"]["caller"]
                   for unit in impact["units"]}
        self.assertEqual(reached, set())
        committed, action, _ = memory.commit_proposal(
            "user.py::drive", "tool.py::Tool.work")
        self.assertEqual((committed, action), (True, "add"))
        memory.ingest()
        impact = compile_impact(memory.store, "tool.py::Tool.work")
        reached = {unit["assertion"]["proposition"]["caller"]
                   for unit in impact["units"]}
        self.assertIn("user.py::drive", reached)


class AttributeNegativeControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "tool.py").write_bytes(
            b"class Tool:\n"
            b"    def work(self, value):\n        return value\n")
        (self.root / "user.py").write_bytes(b"")

    def _candidates_and_diagnostics(self, body):
        (self.root / "user.py").write_bytes(body.encode("utf-8"))
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        candidates = [c for c in memory.candidates()["candidates"]
                      if c.get("via") == "attribute_dispatch"]
        calls = [e for e in memory.frontier_list(status="open")
                 if e["failure"] == "ingest_unresolved_call"]
        return candidates, calls

    def test_reassigned_receiver_proposes_nothing(self):
        candidates, calls = self._candidates_and_diagnostics(
            "from tool import Tool\n\n\n"
            "def drive(flag, value):\n"
            "    t = Tool()\n"
            "    if flag:\n        t = None\n"
            "    return t.work(value)\n")
        self.assertEqual(candidates, [])
        self.assertEqual({e["target"] for e in calls}, {"Tool", "t.work"})

    def test_unknown_receiver_proposes_nothing(self):
        candidates, calls = self._candidates_and_diagnostics(
            "def drive(value):\n"
            "    t = make_thing()\n"
            "    return t.work(value)\n")
        self.assertEqual(candidates, [])
        self.assertEqual({e["target"] for e in calls},
                         {"make_thing", "t.work"})

    def test_unknown_method_proposes_nothing(self):
        candidates, calls = self._candidates_and_diagnostics(
            "from tool import Tool\n\n\n"
            "def drive(value):\n"
            "    t = Tool()\n"
            "    return t.teleport(value)\n")
        self.assertEqual(candidates, [])
        self.assertEqual({e["target"] for e in calls}, {"Tool", "t.teleport"})

    def test_parameter_receiver_proposes_nothing(self):
        candidates, calls = self._candidates_and_diagnostics(
            "def drive(t, value):\n"
            "    return t.work(value)\n")
        self.assertEqual(candidates, [])
        self.assertEqual({e["target"] for e in calls}, {"t.work"})

    def test_use_before_binding_proposes_nothing(self):
        candidates, calls = self._candidates_and_diagnostics(
            "from tool import Tool\n\n\n"
            "def drive(value):\n"
            "    early = t.work(value)\n"
            "    t = Tool()\n"
            "    return early\n")
        self.assertEqual(candidates, [])
        self.assertEqual({e["target"] for e in calls}, {"t.work", "Tool"})

    def test_shadowed_constructor_proposes_nothing(self):
        candidates, calls = self._candidates_and_diagnostics(
            "from tool import Tool\n\n\n"
            "def drive(value):\n"
            "    Tool = 42\n"
            "    t = Tool()\n"
            "    return t.work(value)\n")
        self.assertEqual(candidates, [])
        # the shadowed constructor and the method call stay unresolved
        self.assertEqual({e["target"] for e in calls}, {"Tool", "t.work"})


class AttributeRegressionPredicateTests(unittest.TestCase):
    """candidate_present replays fail-before/pass-after."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "tool.py").write_bytes(
            b"class Tool:\n"
            b"    def work(self, value):\n        return value\n")
        (self.root / "user.py").write_bytes(
            b"from tool import Tool\n\n\n"
            b"def drive(value):\n"
            b"    t = Tool()\n"
            b"    return t.work(value)\n")

    def test_candidate_present_passes_with_candidate_and_fails_without(self):
        db = str(self.tmp / "memory.sqlite")
        case = {"predicate": {"type": "candidate_present",
                              "via": "attribute_dispatch",
                              "caller": "user.py::drive",
                              "callee": "tool.py::Tool.work"}}
        with GraphStore(db) as store:
            store.ingest(str(self.root))
        memory = Memory(db, self.root)
        self.addCleanup(memory.close)
        passed, detail = replay_regression_case(memory, case)
        self.assertTrue(passed, detail)
        # the same predicate against a tree where the caller does not
        # bind a known receiver must fail, not pass vacuously
        (self.root / "user.py").unlink()
        (self.root / "other.py").write_bytes(
            b"def drive(value):\n"
            b"    t = make_thing()\n"
            b"    return t.work(value)\n")
        other_db = str(self.tmp / "other.sqlite")
        with GraphStore(other_db) as store:
            store.ingest(str(self.root))
        memory = Memory(other_db, self.root)
        self.addCleanup(memory.close)
        passed, detail = replay_regression_case(memory, case)
        self.assertFalse(passed, detail)

    def test_candidate_present_requires_all_fields(self):
        db = str(self.tmp / "memory.sqlite")
        memory = Memory(db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        with self.assertRaises(ValueError):
            replay_regression_case(memory, {"predicate": {"type": "candidate_present"}})


class AttributeRefreshEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "tool.py").write_bytes(
            b"class Tool:\n"
            b"    def work(self, value):\n        return value\n\n"
            b"    def rest(self):\n        return 0\n")
        (self.root / "user.py").write_bytes(
            b"from tool import Tool\n\n\n"
            b"def drive(value):\n"
            b"    t = Tool()\n"
            b"    return t.work(value)\n")
        self.db = str(self.tmp / "memory.sqlite")

    def _attribute_candidates(self, db):
        memory = Memory(db, self.root)
        self.addCleanup(memory.close)
        return [c for c in memory.candidates()["candidates"]
                if c.get("via") == "attribute_dispatch"]

    def test_caller_change_refresh_matches_full_rebuild(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        (self.root / "user.py").write_bytes(
            b"from tool import Tool\n\n\n"
            b"def drive(value):\n"
            b"    t = Tool()\n"
            b"    return t.work(value)\n\n\n"
            b"def ride():\n"
            b"    u = Tool()\n"
            b"    return u.rest()\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._attribute_candidates(self.db)
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "attribute_dispatch"]
        self.assertEqual(len(refreshed), 2)
        self.assertEqual(refreshed, rebuilt)

    def test_class_change_refresh_matches_full_rebuild(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        # removing the method must drop the candidate on refresh
        (self.root / "tool.py").write_bytes(
            b"class Tool:\n"
            b"    def rest(self):\n        return 0\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._attribute_candidates(self.db)
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "attribute_dispatch"]
        self.assertEqual(refreshed, rebuilt)
        self.assertEqual([], rebuilt)
