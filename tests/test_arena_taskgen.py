"""Arena v2 validation tests: each v1 defect class is rejected.

Uses inline fixtures (no ingest): the validator works on parsed
trees and symbol sets, so task-shape defects are caught without
model calls or repository access.
"""
import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks" / "arena"))

import taskgen_v2 as tg  # noqa: E402

sys.path.pop(0)

SHOP = {"shop.py": ast.parse("from .core import cover\n"
                             "\n\n"
                             "def run(table):\n"
                             "    return cover(table)\n"),
        "core.py": ast.parse("def cover(table):\n"
                             "    return table\n")}


def index():
    symbols = set()
    for path, tree in SHOP.items():
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.add("%s::%s" % (path, node.name))
    return {"symbols": symbols, "trees": dict(SHOP)}


class TaskgenV2Tests(unittest.TestCase):
    def test_phantom_symbol_rejected(self):
        spec = tg.TaskSpec(id="q", kind="callers",
                           text="Which functions call nope.py::Missing.publish?",
                           key={"scope": "cross-file", "sites": [],
                                "target_path": "nope.py"})
        errors = tg.validate_task(spec, index())
        self.assertTrue(any("phantom symbol" in e for e in errors))

    def test_unstated_scope_rejected(self):
        spec = tg.build_callers_task("core.py::cover", [], "cross-file",
                                     "core.py")
        spec.text = spec.text.replace("Scope: callers in other files "
                                      "(cross-file only).", "")
        errors = tg.validate_task(spec, index())
        self.assertTrue(any("scope" in e for e in errors))

    def test_cross_file_with_same_file_site_rejected(self):
        spec = tg.build_callers_task(
            "core.py::cover", [("core.py::other", "core.py", 9)],
            "cross-file", "core.py")
        errors = tg.validate_task(spec, index())
        self.assertTrue(any("same-file site" in e for e in errors))

    def test_undisclosed_uniqueness_rule_rejected(self):
        spec = tg.build_uniqueness_task("w.py", 80, "memory", "ingest",
                                        "w.py::f", ["a.py::C.ingest"],
                                        False, None)
        spec.text = spec.text.replace(
            "under this rule: unique only when the receiver is self/cls "
            "and the enclosing class defines the name.", "")
        errors = tg.validate_task(spec, index())
        self.assertTrue(any("rule" in e for e in errors))

    def test_branchless_chain_rejected(self):
        spec = tg.build_chain_task("m.py::main", "",
                                   [("m.py::main", "m.py::next",
                                      "m.py", 10)],
                                   "missing", "no-import-no-samefile")
        errors = tg.validate_task(spec, index())
        self.assertTrue(any("branch" in e for e in errors))

    def test_unsupported_hop_line_rejected(self):
        spec = tg.build_chain_task(
            "shop.py::run", "follow run",
            [("shop.py::run", "core.py::cover", "shop.py", 99)],
            "cover", "no-import-no-samefile")
        errors = tg.validate_task(spec, index())
        self.assertTrue(any("calls no" in e for e in errors))

    def test_valid_specs_pass(self):
        idx = index()
        idx["trees"]["m.py"] = ast.parse("def main():\n"
                                         "    return helper()\n")
        idx["symbols"].update({"m.py::main", "m.py::helper"})
        idx["trees"]["w.py"] = ast.parse("class C:\n"
                                         "    def f(self):\n"
                                         "        return self.ingest()\n"
                                         "    def ingest(self):\n"
                                         "        return 1\n")
        idx["symbols"].update({"w.py::C.f", "w.py::C.ingest"})
        idx["symbols"].update({"a.py::f", "b.py::g"})
        ok_specs = [
            tg.build_callers_task("core.py::cover",
                                  [("shop.py::run", "shop.py", 5)],
                                  "cross-file", "core.py"),
            tg.build_uniqueness_task("w.py", 80, "self", "ingest",
                                      "w.py::C.f", ["w.py::C.ingest"],
                                      True, "w.py::C.ingest"),
            tg.build_chain_task("m.py::main", "follow main",
                                [("m.py::main", "m.py::helper", "m.py", 2)],
                                "missing", "stdlib"),
            tg.build_decoy_task("a.py::f", "b.py::g", "no-call"),
            tg.build_abstention_task("core.py::cover"),
        ]
        for spec in ok_specs:
            self.assertEqual(tg.validate_task(spec, idx), [],
                             "valid spec rejected: %s" % spec.id)


if __name__ == "__main__":
    unittest.main()
