"""Constructor-call candidates and class-symbol coverage (v1).

A bare call resolving unambiguously to a workspace class
(`FractalPatternLearner()`) emits a proposed depends_on candidate
(via constructor_call, rule constructor_call_candidate_v1) from the
calling function to the class symbol — never an assertion, and never a
claim about __init__ dispatch. Class declarations (including
NamedTuple-style annotation-only bodies) become graph symbols of
kind "class", invisible to the mention/claim passes.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory
from tunic.store import GraphStore


class ConstructorCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "lib.py").write_bytes(
            b"class Widget:\n"
            b"    def render(self):\n        return 1\n\n"
            b"class Empty:\n"
            b"    pass\n\n"
            b"class TaggedResult:\n"  # NamedTuple-style annotation body
            b"    from typing import NamedTuple\n"
            b"    value: float\n"
            b"    label: str\n\n"
            b"def build(value):\n"
            b"    return value\n")
        self.db = str(self.tmp / "memory.sqlite")

    def _memory(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        return memory

    def _ctor_candidates(self, memory):
        return [c for c in memory.candidates()["candidates"]
                if c.get("via") == "constructor_call"]

    def test_local_class_call_proposes_candidate(self):
        (self.root / "use.py").write_bytes(
            b"from lib import Widget\n\n\n"
            b"def create():\n"
            b"    return Widget()\n")
        memory = self._memory()
        candidates = self._ctor_candidates(memory)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["relation"], "depends_on")
        self.assertEqual(candidate["caller"], "use.py::create")
        self.assertEqual(candidate["callee"], "lib.py::Widget")
        self.assertEqual(candidate["status"], "proposed")
        self.assertEqual(candidate["rule"], "constructor_call_candidate_v1")
        self.assertEqual(candidate["derivation"]["constructor"], "Widget")
        self.assertEqual(candidate["derivation"]["how"], "import")
        # the gap is bridged: no unresolved_call diagnostic for the ctor
        self.assertFalse(any(e["failure"] == "ingest_unresolved_call"
                             for e in memory.frontier_list(status="open")))

    def test_imported_alias_resolves(self):
        (self.root / "use.py").write_bytes(
            b"from lib import Widget as Gadget\n\n\n"
            b"def create():\n"
            b"    return Gadget()\n")
        memory = self._memory()
        candidates = self._ctor_candidates(memory)
        self.assertEqual([(c["caller"], c["callee"]) for c in candidates],
                         [("use.py::create", "lib.py::Widget")])
        self.assertEqual(candidates[0]["derivation"]["constructor"],
                         "Gadget")

    def test_same_file_class_resolves_locally(self):
        (self.root / "solo.py").write_bytes(
            b"class Local:\n"
            b"    def go(self):\n        return 0\n\n\n"
            b"def create():\n"
            b"    return Local()\n")
        memory = self._memory()
        candidates = self._ctor_candidates(memory)
        self.assertEqual([(c["caller"], c["callee"]) for c in candidates],
                         [("solo.py::create", "solo.py::Local")])
        self.assertEqual(candidates[0]["derivation"]["how"], "local")

    def test_methodless_imported_class_resolves(self):
        (self.root / "use.py").write_bytes(
            b"from lib import Empty\n\n\n"
            b"def create():\n"
            b"    return Empty()\n")
        memory = self._memory()
        candidates = self._ctor_candidates(memory)
        self.assertEqual([(c["caller"], c["callee"]) for c in candidates],
                         [("use.py::create", "lib.py::Empty")])

    def test_annotation_only_class_becomes_symbol(self):
        # NamedTuple-style declarations were previously invisible in the
        # graph (methods only); the class itself is now a symbol.
        memory = self._memory()
        keys = {s["key"] for s in memory.store.view()[1]["symbols"]}
        self.assertIn("lib.py::TaggedResult", keys)
        record = next(s for s in memory.store.view()[1]["symbols"]
                      if s["key"] == "lib.py::TaggedResult")
        self.assertEqual(record.get("kind"), "class")

    def test_candidates_not_promoted(self):
        (self.root / "use.py").write_bytes(
            b"from lib import Widget\n\n\n"
            b"def create():\n"
            b"    return Widget()\n")
        memory = self._memory()
        # proposed only: no asserted edge exists, impact cannot traverse
        from tunic.compiler import compile_impact
        impact = compile_impact(memory.store, "lib.py::Widget")
        reached = {unit["assertion"]["proposition"]["caller"]
                   for unit in impact["units"]}
        self.assertEqual(reached, set())


class ConstructorNegativeControlTests(unittest.TestCase):
    """The spec's ten controls: no candidate without the full proof."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "lib.py").write_bytes(
            b"class Widget:\n"
            b"    def render(self):\n        return 1\n\n"
            b"def build(value):\n"
            b"    return value\n")

    def _ctor_candidates_and_calls(self, body, name="use.py"):
        (self.root / name).write_bytes(body.encode("utf-8"))
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        candidates = [c for c in memory.candidates()["candidates"]
                      if c.get("via") == "constructor_call"]
        calls = {e["target"] for e in memory.frontier_list(status="open")
                 if e["failure"] == "ingest_unresolved_call"}
        return candidates, calls

    def test_1_regular_function_call_is_static_not_candidate(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "from lib import build\n\n\n"
            "def create():\n"
            "    return build(1)\n")
        self.assertEqual(candidates, [])
        self.assertEqual(calls, set())

    def test_2_unresolved_external_class_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "def create():\n"
            "    return UnknownThing()\n")
        self.assertEqual(candidates, [])
        self.assertEqual(calls, {"UnknownThing"})

    def test_3_shadowed_class_name_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "from lib import Widget\n\n\n"
            "def create():\n"
            "    Widget = 42\n"
            "    return Widget()\n")
        self.assertEqual(candidates, [])
        self.assertEqual(calls, {"Widget"})

    def test_4_reassigned_constructor_binding_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "from lib import Widget\n\n\n"
            "def create(flag):\n"
            "    Widget = build_a() if flag else build_b()\n"
            "    return Widget()\n")
        self.assertEqual(candidates, [])
        self.assertIn("Widget", calls)

    def test_5_parameter_sharing_class_name_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "def create(Widget):\n"
            "    return Widget()\n")
        self.assertEqual(candidates, [])
        self.assertEqual(calls, {"Widget"})

    def test_7_ambiguous_import_proposes_nothing(self):
        # an alias that resolves to a module member which is a function,
        # not a class: positive class membership is required
        candidates, calls = self._ctor_candidates_and_calls(
            "from lib import build as make\n\n\n"
            "def create():\n"
            "    return make(1)\n")
        self.assertEqual(candidates, [])
        self.assertEqual(calls, set())  # static_call, not a ctor

    def test_7b_unknown_module_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "def create():\n"
            "    return missing.Widget()\n")
        self.assertEqual(candidates, [])
        self.assertIn("missing.Widget", calls)

    def test_8_dynamic_factory_unknown_type_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "def create():\n"
            "    w = make_widget()\n"
            "    return w\n")
        self.assertEqual(candidates, [])
        self.assertEqual(calls, {"make_widget"})

    def test_9_class_reference_without_invocation_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "from lib import Widget\n\n\n"
            "def describe():\n"
            "    return Widget\n")
        self.assertEqual(candidates, [])
        self.assertEqual(calls, set())

    def test_10_dynamic_dispatch_constructor_proposes_nothing(self):
        candidates, calls = self._ctor_candidates_and_calls(
            "def create(name):\n"
            "    cls = globals()[name]\n"
            "    return cls()\n")
        self.assertEqual(candidates, [])
        self.assertTrue(calls)

    def test_nested_scope_constructor_proposes_nothing(self):
        # functions containing nested defs are skipped entirely
        candidates, calls = self._ctor_candidates_and_calls(
            "from lib import Widget\n\n\n"
            "def create():\n"
            "    def inner():\n"
            "        return Widget()\n"
            "    return inner()\n")
        self.assertEqual(candidates, [])
        # nested_scope diagnostic, not an unresolved_call
        self.assertEqual(calls, set())

    def test_forward_reference_resolves(self):
        # module-level order is irrelevant to class resolution
        candidates, calls = self._ctor_candidates_and_calls(
            "def create():\n"
            "    return Later()\n\n\n"
            "class Later:\n"
            "    def go(self):\n        return 1\n")
        self.assertEqual([(c["caller"], c["callee"]) for c in candidates],
                         [("use.py::create", "use.py::Later")])
        self.assertEqual(calls, set())

    def test_inherited_class_still_a_class(self):
        candidates, _calls = self._ctor_candidates_and_calls(
            "class Base(Widget):\n"
            "    def extra(self):\n        return 2\n\n\n"
            "def create():\n"
            "    return Base()\n", name="sub.py")
        self.assertEqual([(c["caller"], c["callee"]) for c in candidates],
                         [("sub.py::create", "sub.py::Base")])


class ConstructorRefreshEquivalenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "lib.py").write_bytes(
            b"class Widget:\n"
            b"    def render(self):\n        return 1\n")
        (self.root / "use.py").write_bytes(
            b"from lib import Widget\n\n\n"
            b"def create():\n"
            b"    return Widget()\n")
        self.db = str(self.tmp / "memory.sqlite")

    def _ctor_candidates(self, db):
        memory = Memory(db, self.root)
        self.addCleanup(memory.close)
        return [c for c in memory.candidates()["candidates"]
                if c.get("via") == "constructor_call"]

    def test_caller_change_refresh_matches_full_rebuild(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        (self.root / "use.py").write_bytes(
            b"from lib import Widget\n\n\n"
            b"def create():\n"
            b"    return Widget()\n\n\n"
            b"def duplicate():\n"
            b"    return Widget()\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._ctor_candidates(self.db)
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "constructor_call"]
        self.assertEqual(len(refreshed), 2)
        self.assertEqual(refreshed, rebuilt)

    def test_class_rename_refresh_matches_full_rebuild(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        (self.root / "lib.py").write_bytes(
            b"class Renamed:\n"
            b"    def render(self):\n        return 1\n")
        (self.root / "use.py").write_bytes(
            b"from lib import Renamed\n\n\n"
            b"def create():\n"
            b"    return Renamed()\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._ctor_candidates(self.db)
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "constructor_call"]
        self.assertEqual(refreshed, rebuilt)
        self.assertEqual([(c["callee"]) for c in refreshed],
                         ["lib.py::Renamed"])

    def test_class_removal_drops_candidate_on_refresh(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        (self.root / "lib.py").write_bytes(b"VALUE = 1\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._ctor_candidates(self.db)
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "constructor_call"]
        self.assertEqual(refreshed, rebuilt)
        self.assertEqual([], rebuilt)

    def test_shadowing_introduced_refresh_matches_full_rebuild(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        (self.root / "use.py").write_bytes(
            b"from lib import Widget\n\n\n"
            b"def create():\n"
            b"    Widget = 42\n"
            b"    return Widget()\n")
        with GraphStore(self.db) as store:
            store.refresh(str(self.root))
        refreshed = self._ctor_candidates(self.db)
        memory = Memory(str(self.tmp / "rebuilt.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        rebuilt = [c for c in memory.candidates()["candidates"]
                   if c.get("via") == "constructor_call"]
        self.assertEqual(refreshed, rebuilt)
        self.assertEqual([], rebuilt)
