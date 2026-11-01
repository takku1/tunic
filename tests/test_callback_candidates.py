"""Callback-parameter candidates: proposed edges with negative controls.

A function that directly invokes one of its parameters is a callback
site. Call sites that pass a statically resolvable bare function name
at the callback parameter index produce proposed depends_on candidates
(via callback_parameter) with derivation chains. Candidates never
traverse, corroborate, or render as evidence until promoted.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.compiler import compile_impact
from tunic.memory import Memory
from tunic.store import GraphStore


class CallbackCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(
            b"def process(value):\n    return value * 2\n")
        (self.root / "hooks.py").write_bytes(
            b"def run(handler, value):\n    return handler(value)\n")

    def _write_main(self, body):
        (self.root / "main.py").write_bytes(body.encode("utf-8"))

    def _memory(self):
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        return memory

    def _callback_candidates(self, memory):
        return [c for c in memory.candidates()["candidates"]
                if c.get("via") == "callback_parameter"]

    def test_resolvable_argument_proposes_candidate_with_derivation(self):
        self._write_main(
            "from core import process\nfrom hooks import run\n\n\n"
            "def main(value):\n    return run(process, value)\n")
        memory = self._memory()
        candidates = self._callback_candidates(memory)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["relation"], "depends_on")
        self.assertEqual(candidate["caller"], "hooks.py::run")
        self.assertEqual(candidate["callee"], "core.py::process")
        self.assertEqual(candidate["status"], "proposed")
        self.assertEqual(candidate["rule"], "callback_parameter_candidate_v1")
        self.assertEqual(candidate["derivation"]["parameter"], "handler")
        self.assertEqual(candidate["derivation"]["index"], 0)
        self.assertIn("binding", candidate["derivation"])
        self.assertIn("call", candidate["derivation"])
        # the unresolved-call diagnostic is still filed: a candidate is
        # never an assertion, and the gap stays visible.
        entries = memory.frontier_list(status="open")
        self.assertTrue(any(e["failure"] == "ingest_unresolved_call"
                            for e in entries))

    def test_candidates_never_traverse_impact(self):
        self._write_main(
            "from core import process\nfrom hooks import run\n\n\n"
            "def main(value):\n    return run(process, value)\n")
        memory = self._memory()
        impact = compile_impact(memory.store, "core.py::process")
        reached = {unit["assertion"]["proposition"]["caller"]
                   for unit in impact["units"]}
        # the callback edge is proposed only: nothing reaches process
        # through it — not even main, whose static edge targets run.
        self.assertEqual(reached, set())

    def test_promoted_candidate_traverses_after_commit(self):
        self._write_main(
            "from core import process\nfrom hooks import run\n\n\n"
            "def main(value):\n    return run(process, value)\n")
        memory = self._memory()
        committed, action, _ = memory.commit_proposal(
            "hooks.py::run", "core.py::process")
        self.assertEqual((committed, action), (True, "add"))
        memory.ingest()
        impact = compile_impact(memory.store, "core.py::process")
        reached = {unit["assertion"]["proposition"]["caller"]
                   for unit in impact["units"]}
        self.assertIn("hooks.py::run", reached)


class CallbackNegativeControlTests(unittest.TestCase):
    """Arguments that cannot be bound statically propose nothing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(
            b"def process(value):\n    return value * 2\n")
        (self.root / "hooks.py").write_bytes(
            b"def run(handler, value):\n    return handler(value)\n")

    def _candidates_after(self, main_body):
        (self.root / "main.py").write_bytes(main_body.encode("utf-8"))
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        return [c for c in memory.candidates()["candidates"]
                if c.get("via") == "callback_parameter"]

    def test_unresolvable_argument_proposes_nothing(self):
        self.assertEqual(self._candidates_after(
            "from hooks import run\n\n\n"
            "def main(value):\n    return run(unknown_fn, value)\n"), [])

    def test_keyword_argument_proposes_nothing(self):
        self.assertEqual(self._candidates_after(
            "from core import process\nfrom hooks import run\n\n\n"
            "def main(value):\n    return run(handler=process, value=value)\n"), [])

    def test_shadowed_argument_proposes_nothing(self):
        self.assertEqual(self._candidates_after(
            "from core import process\nfrom hooks import run\n\n\n"
            "def main(handler):\n    return run(handler, 1)\n"), [])

    def test_non_name_argument_proposes_nothing(self):
        self.assertEqual(self._candidates_after(
            "from hooks import run\n\n\n"
            "def main(value):\n    return run(lambda x: x, value)\n"), [])

    def test_attribute_argument_proposes_nothing(self):
        (self.root / "tool.py").write_bytes(
            b"class Tool:\n    def work(self, value):\n        return value\n")
        self.assertEqual(self._candidates_after(
            "from tool import Tool\nfrom hooks import run\n\n\n"
            "def main(value):\n    return run(Tool().work, value)\n"), [])


class CallbackRefreshEquivalenceTests(unittest.TestCase):
    """Refresh reproduces full-rebuild candidate output exactly."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(
            b"def process(value):\n    return value * 2\n")
        (self.root / "hooks.py").write_bytes(
            b"def run(handler, value):\n    return handler(value)\n")
        (self.root / "main.py").write_bytes(
            b"from core import process\nfrom hooks import run\n\n\n"
            b"def main(value):\n    return run(process, value)\n")
        self.db = str(self.tmp / "memory.sqlite")

    def _candidates(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        result = memory.candidates()["candidates"]
        return [c for c in result if c.get("via") == "callback_parameter"]

    def test_caller_change_refresh_matches_full_rebuild(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        # caller changes: a second callback argument appears.
        (self.root / "main.py").write_bytes(
            b"from core import process\nfrom hooks import run\n\n\n"
            b"def main(value):\n    return run(process, value)\n\n\n"
            b"def other(value):\n    return run(process, value + 1)\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._candidates()
        # oracle: full rebuild from the same tree.
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "callback_parameter"]
        self.assertEqual(len(refreshed), 2)
        self.assertEqual(refreshed, rebuilt)

    def test_callback_definition_change_refresh_matches_full_rebuild(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        # the callback parameter moves to index 1; full rebuild drops the
        # old candidate (positional argument no longer binds the site).
        (self.root / "hooks.py").write_bytes(
            b"def run(value, handler):\n    return handler(value)\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._candidates()
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "callback_parameter"]
        self.assertEqual(refreshed, rebuilt)
        self.assertEqual([], rebuilt)
