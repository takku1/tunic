"""Nested-scope extraction v1: outer functions and methods are indexed
even when they contain nested definitions; calls inside nested scopes
are never attributed to the outer function.

A closure calling foo() does not mean the enclosing function directly
calls foo(): the call may run much later, elsewhere, or never. The
extractor therefore indexes the outer symbol, resolves only direct
calls for it, and records nested-scope calls as distinct
`nested_call` diagnostics -- never as outer edges, never silently.
Deterministic, no model. Dev-scale fixtures only.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tunic.memory import Memory  # noqa: E402

sys.path.pop(0)

SHOP = """
class Gadget:
    def start(self):
        def helper():
            return format_status()
        return helper()

    def stop(self):
        return shutdown()


def create():
    return Gadget()


def shutdown():
    return 0


def helper():
    return "module-level"


def use_nested_name():
    def helper():
        return "shadowed"
    return helper()
"""


def build(contents, tmp):
    root = Path(tmp) / "repo"
    root.mkdir(parents=True)
    (root / "shop.py").write_text(contents, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "sessions.sqlite"), root)
    memory.ingest()
    return memory


def symbols(memory):
    _snap, view = memory.store.view()
    return {s["key"]: s for s in view["symbols"]}


def edges(memory):
    _snap, view = memory.store.view()
    return [(a["proposition"].get("caller"), a["proposition"].get("callee"),
             a.get("status"))
            for a in view["assertions"]
            if a["proposition"].get("relation") in ("static_call",
                                                    "depends_on")]


def diagnostics(memory, kind):
    _snap, view = memory.store.view()
    return [d for d in view.get("diagnostics", []) if d.get("kind") == kind]


class NestedScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_outer_method_indexed_despite_nested_def(self):
        memory = build(SHOP, self.tmp)
        self.addCleanup(memory.close)
        self.assertIn("shop.py::Gadget.start", symbols(memory))

    def test_direct_calls_of_outer_body_still_resolve(self):
        memory = build(SHOP, self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::Gadget.stop", "shop.py::shutdown",
                       "syntactic_observation"), edges(memory))
        self.assertIn(("shop.py::create", "shop.py::Gadget",
                       "proposed"), edges(memory))

    def test_nested_calls_never_attributed_to_outer(self):
        memory = build(SHOP, self.tmp)
        self.addCleanup(memory.close)
        outer_edges = [e for e in edges(memory)
                       if e[0] == "shop.py::Gadget.start"]
        self.assertEqual(outer_edges, [])
        nested = diagnostics(memory, "nested_call")
        self.assertTrue(any(d.get("expression") == "format_status"
                            for d in nested),
                        "nested-scope call must be distinguished, not silent")

    def test_nested_name_shadows_module_function(self):
        memory = build(SHOP, self.tmp)
        self.addCleanup(memory.close)
        # use_nested_name calls its own nested helper, NOT the
        # module-level one: no false edge to shop.py::helper.
        self.assertNotIn(("shop.py::use_nested_name", "shop.py::helper",
                          "syntactic_observation"), edges(memory))
        nested = diagnostics(memory, "nested_call")
        self.assertTrue(any(d.get("expression") == "helper"
                            for d in nested))

    def test_lambda_body_calls_not_attributed(self):
        memory = build("def pick(items):\n"
                       "    return sorted(items, key=lambda x: rank(x))\n"
                       "\n\n"
                       "def rank(item):\n"
                       "    return 0\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn("shop.py::pick", symbols(memory))
        self.assertNotIn(("shop.py::pick", "shop.py::rank",
                          "syntactic_observation"), edges(memory))

    def test_scope_diagnostic_still_recorded(self):
        memory = build(SHOP, self.tmp)
        self.addCleanup(memory.close)
        scoped = diagnostics(memory, "nested_scope")
        self.assertTrue(any(d.get("line") for d in scoped),
                        "nested_scope stays visible as scope metadata")


class RealRepoRegressionTests(unittest.TestCase):
    """Tunic indexes its own nested-scope methods.

    Live-repo dogfood: ingest the shipped ``tunic/`` package and pin
    the flagship case. Exact-callee pins are intentional -- if
    ``Memory.answer``'s direct calls change, this test should fail
    until the expectation is re-verified by inspection.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.memory = Memory(str(Path(cls.tmp) / "sessions.sqlite"),
                            REPO / "tunic")
        cls.memory.ingest()
        _snap, view = cls.memory.store.view()
        cls.keys = {s["key"] for s in view["symbols"]}
        cls.edges = {(a["proposition"].get("caller"),
                      a["proposition"].get("callee"))
                     for a in view["assertions"]
                     if a["proposition"].get("relation") == "static_call"}

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_memory_answer_is_indexed(self):
        self.assertIn("memory.py::Memory.answer", self.keys)

    def test_answer_direct_callees_accurate(self):
        direct = {callee for caller, callee in self.edges
                  if caller == "memory.py::Memory.answer"}
        # compile_impact / compile_related recovered by the
        # cross-module import fix: memory.py:16 `from . import compiler`
        # with direct calls at lines 1217, 1284, 1292 (verified by
        # inspection 2026-11-02). lifecycle.suppressed_ids joined the
        # set when suppression enforcement landed in answer()
        # (verified by inspection 2026-11-02: the call computes the
        # suppressed-id set passed to selection and successor maps).
        # compile_documents joined when the R2 documents channel landed
        # in answer() (verified by inspection 2026-11-02: the call at
        # memory.py:1197 builds the cited prose passages; read-only,
        # adds no edges). _graph_neighbors joined when the graph
        # expansion stage landed in answer() (verified by inspection
        # 2026-11-03: the call at memory.py:1340 builds the one-hop
        # neighborhood; read-only, adds no edges). _snapshot_index joined
        # when non-Python lexical reach landed in answer() (verified by
        # inspection 2026-11-04: two calls build the fill-only symbol
        # candidates and the additive file passages; read-only, adds no
        # edges).
        self.assertEqual(direct, {"memory.py::Memory._ensure_discovery",
                                  "memory.py::Memory._graph_neighbors",
                                  "memory.py::Memory._snapshot_index",
                                  "memory.py::Memory._frontier_upsert",
                                  "memory.py::_container_of",
                                  "memory.py::_dedup_dicts",
                                  "memory.py::authorize",
                                  "compiler.py::compile_documents",
                                  "compiler.py::compile_impact",
                                  "compiler.py::compile_related",
                                  "lifecycle.py::suppressed_ids"})

    def test_nested_only_calls_not_attributed(self):
        # _name_pattern (nested in answer) is the only user of `re`
        # inside answer: no outer edge to the regex module.
        self.assertFalse(any(caller == "memory.py::Memory.answer"
                             and (callee or "").startswith("re.")
                             for caller, callee in self.edges))

    def test_related_resolves_indexed_method(self):
        related = self.memory.related("memory.py::Memory.answer",
                                      policy="current")
        self.assertTrue(related["impact"]["impacts"])

    def test_self_answer_is_consistent(self):
        result = self.memory.answer(
            "which functions expand container classes", policy="current")
        self.assertEqual(result["outcome"], "complete")
        for entry in result["impacts"]:
            self.assertIn(entry["symbol"], self.keys)


class EagerEvaluationTests(unittest.TestCase):
    """Decorators and default arguments execute when the enclosing
    function runs -- even on nested definitions. Bodies stay deferred."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_nested_def_decorators_and_defaults_are_eager(self):
        memory = build("def outer():\n"
                       "    @register(make_handler())\n"
                       "    def inner(x=build_default()):\n"
                       "        return deferred_work()\n"
                       "    return inner\n"
                       "\n\n"
                       "def register(fn):\n"
                       "    return fn\n"
                       "\n\n"
                       "def make_handler():\n"
                       "    return 1\n"
                       "\n\n"
                       "def build_default():\n"
                       "    return 2\n"
                       "\n\n"
                       "def deferred_work():\n"
                       "    return 3\n", self.tmp)
        self.addCleanup(memory.close)
        # Decorator, decorator argument, and default argument all run
        # when outer() executes its def statement.
        self.assertIn(("shop.py::outer", "shop.py::register",
                       "syntactic_observation"), edges(memory))
        self.assertIn(("shop.py::outer", "shop.py::make_handler",
                       "syntactic_observation"), edges(memory))
        self.assertIn(("shop.py::outer", "shop.py::build_default",
                       "syntactic_observation"), edges(memory))
        # The deferred body never attributes to the outer function.
        self.assertNotIn(("shop.py::outer", "shop.py::deferred_work",
                          "syntactic_observation"), edges(memory))
        self.assertTrue(any(d.get("expression") == "deferred_work"
                            for d in diagnostics(memory, "nested_call")))

    def test_lambda_defaults_are_eager_body_is_not(self):
        memory = build("def outer(flag=True):\n"
                       "    def inner(cb=make_default()):\n"
                       "        return sorted([3, 1], key=lambda i: use(i))\n"
                       "    return inner(flag)\n"
                       "\n\n"
                       "def make_default():\n"
                       "    return None\n"
                       "\n\n"
                       "def use(i):\n"
                       "    return i\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::outer", "shop.py::make_default",
                       "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::use",
                          "syntactic_observation"), edges(memory))

    def test_nested_class_bases_are_eager_body_is_not(self):
        memory = build("def outer():\n"
                       "    class Inner(make_base()):\n"
                       "        def method(self):\n"
                       "            return deferred_work()\n"
                       "    return Inner\n"
                       "\n\n"
                       "def make_base():\n"
                       "    return object\n"
                       "\n\n"
                       "def deferred_work():\n"
                       "    return 1\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::outer", "shop.py::make_base",
                       "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::deferred_work",
                          "syntactic_observation"), edges(memory))


class ComprehensionSemanticsTests(unittest.TestCase):
    """Eager comprehensions resolve; generator elements defer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_eager_comprehensions_resolve(self):
        memory = build("def outer(items):\n"
                       "    a = [load(x) for x in items]\n"
                       "    b = {load(x): x for x in items}\n"
                       "    c = {load(x) for x in items}\n"
                       "    return a, b, c\n"
                       "\n\n"
                       "def load(x):\n"
                       "    return x\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::outer", "shop.py::load",
                       "syntactic_observation"), edges(memory))

    def test_generator_element_defers(self):
        memory = build("def outer(items):\n"
                       "    return (load(x) for x in items)\n"
                       "\n\n"
                       "def load(x):\n"
                       "    return x\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertNotIn(("shop.py::outer", "shop.py::load",
                          "syntactic_observation"), edges(memory))
        self.assertTrue(any(d.get("expression") == "load"
                            for d in diagnostics(memory, "nested_call")))

    def test_generator_iterable_is_eager(self):
        memory = build("def outer():\n"
                       "    return (x for x in fetch_all())\n"
                       "\n\n"
                       "def fetch_all():\n"
                       "    return []\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::outer", "shop.py::fetch_all",
                       "syntactic_observation"), edges(memory))


class RootHeaderTests(unittest.TestCase):
    """The target's own header evaluates at definition time, not when
    the target executes. Root decorators, defaults, and annotations
    are definition-time scope: never attributed to the target."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_root_defaults_not_attributed(self):
        memory = build("def outer(x=make_default()):\n"
                       "    return x\n"
                       "\n\n"
                       "def make_default():\n"
                       "    return 42\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertNotIn(("shop.py::outer", "shop.py::make_default",
                          "syntactic_observation"), edges(memory))
        self.assertFalse([d for d in diagnostics(memory, "nested_call")
                          if d.get("expression") == "make_default"])

    def test_root_decorators_not_attributed(self):
        memory = build("@register(make_handler())\n"
                       "def outer():\n"
                       "    return 1\n"
                       "\n\n"
                       "def register(fn):\n"
                       "    return fn\n"
                       "\n\n"
                       "def make_handler():\n"
                       "    return 2\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertNotIn(("shop.py::outer", "shop.py::register",
                          "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::make_handler",
                          "syntactic_observation"), edges(memory))

    def test_root_body_still_resolves(self):
        memory = build("def outer(x=make_default()):\n"
                       "    def inner():\n"
                       "        return 1\n"
                       "    direct_dep()\n"
                       "    return inner\n"
                       "\n\n"
                       "def make_default():\n"
                       "    return 0\n"
                       "\n\n"
                       "def direct_dep():\n"
                       "    return 3\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::outer", "shop.py::direct_dep",
                       "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::make_default",
                          "syntactic_observation"), edges(memory))


class NestedClassBodyTests(unittest.TestCase):
    """Nested class bodies execute when the class statement runs, so
    their direct calls are eager. Nested methods stay deferred."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_class_body_assignments_are_eager(self):
        memory = build("def outer():\n"
                       "    class Inner(make_base()):\n"
                       "        value = initialize()\n"
                       "\n"
                       "        @mark(\"method\")\n"
                       "        def method(self):\n"
                       "            return deferred_work()\n"
                       "    return Inner\n"
                       "\n\n"
                       "def make_base():\n"
                       "    return object\n"
                       "\n\n"
                       "def initialize():\n"
                       "    return 1\n"
                       "\n\n"
                       "def mark(fn):\n"
                       "    return fn\n"
                       "\n\n"
                       "def deferred_work():\n"
                       "    return 2\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::outer", "shop.py::make_base",
                       "syntactic_observation"), edges(memory))
        self.assertIn(("shop.py::outer", "shop.py::initialize",
                       "syntactic_observation"), edges(memory))
        self.assertIn(("shop.py::outer", "shop.py::mark",
                       "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::deferred_work",
                          "syntactic_observation"), edges(memory))
        self.assertTrue(any(d.get("expression") == "deferred_work"
                            for d in diagnostics(memory, "nested_call")))


class GeneratorTimingTests(unittest.TestCase):
    """Only the outermost iterable of a generator expression evaluates
    at creation. Later iterables, conditions, and the element defer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_only_outermost_iterable_is_eager(self):
        memory = build("def outer():\n"
                       "    return (transform(x, y) for x in first()\n"
                       "            for y in second(x))\n"
                       "\n\n"
                       "def transform(x, y):\n"
                       "    return x\n"
                       "\n\n"
                       "def first():\n"
                       "    return []\n"
                       "\n\n"
                       "def second(x):\n"
                       "    return x\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn(("shop.py::outer", "shop.py::first",
                       "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::second",
                          "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::transform",
                          "syntactic_observation"), edges(memory))
        nested = {d.get("expression")
                  for d in diagnostics(memory, "nested_call")}
        self.assertIn("second", nested)
        self.assertIn("transform", nested)

    def test_genexp_condition_defers(self):
        memory = build("def outer(items):\n"
                       "    return (x for x in items if check(x))\n"
                       "\n\n"
                       "def check(x):\n"
                       "    return True\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertNotIn(("shop.py::outer", "shop.py::check",
                          "syntactic_observation"), edges(memory))
        self.assertTrue(any(d.get("expression") == "check"
                            for d in diagnostics(memory, "nested_call")))


class AsyncParityTests(unittest.TestCase):
    """Async definitions follow the same eager/deferred model."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_async_outer_with_nested_def(self):
        memory = build("async def outer():\n"
                       "    def inner(x=build_default()):\n"
                       "        return deferred_work()\n"
                       "    direct_dep()\n"
                       "    return inner\n"
                       "\n\n"
                       "def build_default():\n"
                       "    return 1\n"
                       "\n\n"
                       "def direct_dep():\n"
                       "    return 2\n"
                       "\n\n"
                       "def deferred_work():\n"
                       "    return 3\n", self.tmp)
        self.addCleanup(memory.close)
        self.assertIn("shop.py::outer", symbols(memory))
        self.assertIn(("shop.py::outer", "shop.py::direct_dep",
                       "syntactic_observation"), edges(memory))
        self.assertIn(("shop.py::outer", "shop.py::build_default",
                       "syntactic_observation"), edges(memory))
        self.assertNotIn(("shop.py::outer", "shop.py::deferred_work",
                          "syntactic_observation"), edges(memory))


if __name__ == "__main__":
    unittest.main()
