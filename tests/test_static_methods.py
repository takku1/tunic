"""Static/class-method call resolution: verified static edges, not candidates.

Scope: @staticmethod/@classmethod with no other decorators are language
semantics, not replaceable callables, so they index like instance
methods and self/cls/ClassName calls to them resolve to static_call
assertions with byte-exact spans. Arbitrarily decorated methods,
properties, attributes, and inherited methods stay unresolved
(inheritance is a deferred item, pinned by test below).
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

MOD_A = ("class Tools:\n"
         "    @staticmethod\n"
         "    def pure(name):\n"
         "        return name\n"
         "\n\n"
         "    @classmethod\n"
         "    def make(cls, name):\n"
         "        return cls.pure(name)\n"
         "\n\n"
         "    @staticmethod\n"
         "    def _hidden(value):\n"
         "        return value\n")


MOD_B = ("from mod_a import Tools\n"
         "\n\n"
         "class Uses:\n"
         "    def run(self, name):\n"
         "        first = self._help(name)\n"
         "        second = Tools.pure(name)\n"
         "        third = Tools.make(name)\n"
         "        return (first, second, third)\n"
         "\n\n"
         "    @staticmethod\n"
         "    def _help(name):\n"
         "        return Tools._hidden(name)\n"
         "\n\n"
         "class Child(Tools):\n"
         "    def use(self, name):\n"
         "        return self.pure(name)\n")


MOD_C = ("import functools\n"
         "\n\n"
         "class Tricky:\n"
         "    def go(self, key):\n"
         "        return self.cached(key)\n"
         "\n\n"
         "    @functools.lru_cache(maxsize=None)\n"
         "    @staticmethod\n"
         "    def cached(key):\n"
         "        return key\n"
         "\n\n"
         "    @property\n"
         "    def label(self):\n"
         "        return 'x'\n"
         "\n\n"
         "    def read(self):\n"
         "        return self.label()\n")


class StaticMethodResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        root = Path(cls.tmp) / "repo"
        root.mkdir()
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / "mod_a.py").write_text(MOD_A, encoding="utf-8")
        (root / "mod_b.py").write_text(MOD_B, encoding="utf-8")
        (root / "mod_c.py").write_text(MOD_C, encoding="utf-8")
        cls.memory = Memory(str(Path(cls.tmp) / "s.sqlite"), root)
        cls.memory.ingest()
        view = cls.memory.store.view(None)[1]
        cls.symbols = {row["key"] for row in view["symbols"]}
        cls.static = {(p.get("caller"), p.get("callee"))
                      for a in view["assertions"]
                      for p in [a.get("proposition", {})]
                      if p.get("relation") == "static_call"}
        cls.diagnostics = list(view.get("diagnostics", []))

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_staticmethod_indexed_as_symbol(self):
        self.assertIn("mod_a.py::Tools.pure", self.symbols)
        self.assertIn("mod_a.py::Tools.make", self.symbols)
        self.assertIn("mod_a.py::Tools._hidden", self.symbols)

    def test_self_call_to_staticmethod_resolves(self):
        self.assertIn(("mod_b.py::Uses.run", "mod_b.py::Uses._help"),
                      self.static)

    def test_class_call_to_staticmethod_resolves(self):
        self.assertIn(("mod_b.py::Uses.run", "mod_a.py::Tools.pure"),
                      self.static)

    def test_class_call_to_classmethod_resolves(self):
        self.assertIn(("mod_b.py::Uses.run", "mod_a.py::Tools.make"),
                      self.static)

    def test_classmethod_body_self_call_resolves(self):
        # cls.pure(name) inside make: cls receiver, staticmethod target.
        self.assertIn(("mod_a.py::Tools.make", "mod_a.py::Tools.pure"),
                      self.static)

    def test_cross_module_static_resolves(self):
        self.assertIn(("mod_b.py::Uses._help", "mod_a.py::Tools._hidden"),
                      self.static)

    def test_stacked_decorator_stays_unresolved(self):
        self.assertNotIn("mod_c.py::Tricky.cached", self.symbols)
        self.assertFalse(
            any(pair[0] == "mod_c.py::Tricky.go" for pair in self.static))

    def test_property_call_stays_unresolved(self):
        self.assertNotIn("mod_c.py::Tricky.label", self.symbols)
        self.assertFalse(
            any(pair == ("mod_c.py::Tricky.read", "mod_c.py::Tricky.label")
                for pair in self.static))

    def test_inherited_method_stays_unresolved_deferred(self):
        # Deferred item (same-file inheritance): documents the boundary.
        # A subclass calling a parent-defined method is not resolved.
        self.assertFalse(
            any(pair[0].endswith("::Child.use") for pair in self.static))


if __name__ == "__main__":
    unittest.main()
