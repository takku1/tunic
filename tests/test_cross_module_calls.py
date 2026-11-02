"""Cross-module static-call coverage v1: bare calls through from-imports.

A bare ``cover()`` call in a file holding ``from .core import cover``
(or ``from core import cover``) is a statically resolvable edge to
``core.py::cover`` -- not an unresolved call. Previously the extractor
dropped these because the relative-import guard rejected level-1
imports in top-level modules, so the import map never held the
binding. (An early hypothesis blamed bare-name resolution as well;
failing-first tests disproved it -- the ``head in imports`` branch
already resolves absolute from-imports.)

Same-file definitions keep precedence, genuinely unresolvable names
stay ``unresolved_call``, and beyond-top-level relative imports stay
unresolved. Deterministic, no model.
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

CORE = ("def cover(table):\n"
        "    return table\n"
        "\n\n"
        "def local_only():\n"
        "    return 0\n")

AGENT = ("from .core import cover\n"
         "\n\n"
         "def run(table):\n"
         "    return cover(table)\n")

ABS = ("from core import cover\n"
       "\n\n"
       "def run_abs(table):\n"
       "    return cover(table)\n")

SHADOW = ("from .core import cover\n"
          "\n\n"
          "def cover(table):\n"
          "    return []\n"
          "\n\n"
          "def run_shadow(table):\n"
          "    return cover(table)\n")

LONELY = ("def run_lonely(table):\n"
          "    return cover(table)\n")

DEEP = ("from ...core import cover\n"
        "\n\n"
        "def run_deep(table):\n"
        "    return cover(table)\n")

FILES = {"__init__.py": "", "core.py": CORE, "agent.py": AGENT,
         "abs.py": ABS, "shadow.py": SHADOW, "lonely.py": LONELY,
         "deep.py": DEEP}


def build(files, tmp):
    root = Path(tmp) / "repo"
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "sessions.sqlite"), root)
    memory.ingest()
    return memory


def edge_tuples(memory):
    _snap, view = memory.store.view()
    return [(a["proposition"].get("caller"),
             a["proposition"].get("callee"), a.get("status"))
            for a in view["assertions"]
            if a["proposition"].get("relation") == "static_call"]


def assertions(memory):
    _snap, view = memory.store.view()
    return [a for a in view["assertions"]
            if a["proposition"].get("relation") == "static_call"]


def unresolved(memory):
    _snap, view = memory.store.view()
    return [d for d in view.get("diagnostics", [])
            if d.get("kind") == "unresolved_call"]


class CrossModuleCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.memory = build(FILES, cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_relative_from_import_resolves(self):
        self.assertIn(("agent.py::run", "core.py::cover",
                       "syntactic_observation"),
                      edge_tuples(self.memory))
        self.assertFalse([d for d in unresolved(self.memory)
                          if d.get("caller") == "agent.py::run"])

    def test_absolute_from_import_resolves(self):
        self.assertIn(("abs.py::run_abs", "core.py::cover",
                       "syntactic_observation"),
                      edge_tuples(self.memory))

    def test_double_bound_name_stays_ambiguous(self):
        # cover is bound twice at module level (import + def): the
        # extractor conservatively resolves neither, recording
        # ambiguous_module_bindings instead of guessing last-wins.
        edges = edge_tuples(self.memory)
        self.assertNotIn(("shadow.py::run_shadow", "shadow.py::cover",
                          "syntactic_observation"), edges)
        self.assertNotIn(("shadow.py::run_shadow", "core.py::cover",
                          "syntactic_observation"), edges)
        _snap, view = self.memory.store.view()
        ambiguous = [d for d in view.get("diagnostics", [])
                     if d.get("kind") == "ambiguous_module_bindings"
                     and d.get("path") == "shadow.py"]
        self.assertTrue(any("cover" in d.get("names", [])
                            for d in ambiguous))

    def test_unimported_bare_call_stays_unresolved(self):
        self.assertFalse([e for e in edge_tuples(self.memory)
                          if e[0] == "lonely.py::run_lonely"])
        self.assertTrue(any(d.get("caller") == "lonely.py::run_lonely"
                            and d.get("expression") == "cover"
                            for d in unresolved(self.memory)))

    def test_beyond_top_level_relative_stays_unresolved(self):
        self.assertFalse([e for e in edge_tuples(self.memory)
                          if e[0] == "deep.py::run_deep"])
        self.assertTrue(any(d.get("caller") == "deep.py::run_deep"
                            for d in unresolved(self.memory)))

    def test_import_statement_cited_as_support(self):
        by_caller = {a["proposition"].get("caller"): a
                     for a in assertions(self.memory)}
        for caller in ("agent.py::run", "abs.py::run_abs"):
            support = by_caller[caller]["resolution"]["support"]
            self.assertEqual(len(support), 2,
                             "import-backed edge cites import + definition")
            self.assertEqual(support[0]["start"], 0,
                             "first support entry is the line-1 import")
            self.assertNotEqual(support[0], support[1])


class CrossModuleDogfoodTests(unittest.TestCase):
    """The Arena-missing edges: _dispatch_covering's cross-file callers."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.memory = Memory(str(Path(cls.tmp) / "sessions.sqlite"),
                            REPO / "tunic")
        cls.memory.ingest()
        _snap, view = cls.memory.store.view()
        cls.edges = {(a["proposition"].get("caller"),
                      a["proposition"].get("callee"))
                     for a in view["assertions"]
                     if a["proposition"].get("relation") == "static_call"}

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_investigate_calls_dispatch_covering(self):
        self.assertIn(("investigate.py::investigate",
                       "memory.py::_dispatch_covering"), self.edges)

    def test_assess_calls_dispatch_covering(self):
        self.assertIn(("triage.py::_assess",
                       "memory.py::_dispatch_covering"), self.edges)

    def test_assess_v2_calls_dispatch_covering(self):
        self.assertIn(("triage.py::_assess_v2",
                       "memory.py::_dispatch_covering"), self.edges)


if __name__ == "__main__":
    unittest.main()
