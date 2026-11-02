"""Arena v2 stage-1 validation, checked in.

Frozen task definitions (mirroring the validated v2 set) with
source-derived expectations, executed against a throwaway ingest of
the live ``tunic/`` package. Deterministic, no model.

Contract (same as the nested-scope dogfood pins): if the repository
changes these facts, this test fails until the expectation is
re-verified by inspection -- it must never be edited to match new
output blindly.
"""
import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "benchmarks" / "arena"))

from tunic.memory import Memory  # noqa: E402
import taskgen_v2 as tg  # noqa: E402

sys.path.pop(0)
sys.path.pop(0)


def manifests():
    return [
        tg.build_callers_task(
            "memory.py::_dispatch_covering",
            [("investigate.py::investigate", "investigate.py", 120),
             ("triage.py::_assess", "triage.py", 252),
             ("triage.py::_assess_v2", "triage.py", 370)],
            "cross-file", "memory.py"),
        tg.build_uniqueness_task(
            "__main__.py", 80, "memory", "ingest",
            "__main__.py::_run_zero_config",
            ["memory.py::Memory.ingest", "store.py::GraphStore.ingest"],
            False, None),
        tg.build_chain_task(
            "__main__.py::main",
            "follow the zero-config shortcut: main -> _run_zero_config "
            "(line 115) -> _zero_config_db (line 69)",
            [("__main__.py::main", "__main__.py::_run_zero_config",
              "__main__.py", 115),
             ("__main__.py::_run_zero_config", "__main__.py::_zero_config_db",
              "__main__.py", 69)],
            "enumerate", "stdlib-no-import-no-samefile"),
        tg.build_decoy_task("baselines.py::BM25.__init__",
                            "compiler.py::_evidence", "no-call"),
        tg.build_abstention_task("baselines.py::pagerank"),
    ]


class ArenaStage1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.memory = Memory(str(Path(cls.tmp) / "stage1.sqlite"),
                            REPO / "tunic")
        cls.memory.ingest()
        _snap, cls.view = cls.memory.store.view()
        cls.symbols = {s["key"] for s in cls.view["symbols"]}
        cls.index = tg.index_trees(REPO / "tunic")

    @classmethod
    def tearDownClass(cls):
        cls.memory.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_manifests_validate(self):
        for spec in manifests():
            self.assertEqual(tg.validate_task(spec, self.index), [],
                             "v2 manifest rejected: %s" % spec.id)

    def test_callers_match_key_cross_file_only(self):
        got = {a["proposition"].get("caller")
               for a in self.view["assertions"]
               if a["proposition"].get("relation") == "static_call"
               and a["proposition"].get("callee")
               == "memory.py::_dispatch_covering"
               and not a["proposition"].get("caller", "")
               .startswith("memory.py::")}
        self.assertEqual(got, {"investigate.py::investigate",
                               "triage.py::_assess",
                               "triage.py::_assess_v2"})

    def test_uniqueness_defs_and_receiver(self):
        self.assertLessEqual({"memory.py::Memory.ingest",
                              "store.py::GraphStore.ingest"},
                             self.symbols)
        tree = ast.parse((REPO / "tunic" / "__main__.py")
                         .read_text(encoding="utf-8"))
        call80 = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.Call)
                      and getattr(n, "lineno", 0) == 80)
        receiver = (call80.func.value.id
                    if isinstance(call80.func, ast.Attribute) else None)
        self.assertEqual(receiver, "memory")

    def test_chain_hops_via_callees(self):
        first = self.memory.callees("__main__.py::main")
        hop1 = [d for d in first["direct"]
                if d["callee"] == "__main__.py::_run_zero_config"]
        self.assertEqual(len(hop1), 1)
        self.assertEqual(hop1[0]["status"], "syntactic_observation")
        self.assertTrue(hop1[0]["evidence"])
        self.assertTrue(hop1[0]["resolution_support"])
        second = self.memory.callees("__main__.py::_run_zero_config")
        self.assertIn("__main__.py::_zero_config_db",
                      {d["callee"] for d in second["direct"]})
        self.assertIn(("__main__.py::_zero_config_db", "enumerate"),
                      {(c["caller"], c["expression"]) for c in second["cold"]})

    def test_chain_reachable_depth(self):
        deep = self.memory.callees("__main__.py::main", max_depth=4)
        depths = {r["callee"]: r["depth"] for r in deep["reachable"]}
        self.assertEqual(depths.get("__main__.py::_zero_config_db"), 2)

    def test_decoy_absent_both_directions(self):
        fwd = self.memory.callees("baselines.py::BM25.__init__")
        everything = ({d["callee"] for d in fwd["direct"]} |
                      {r["callee"] for r in fwd["reachable"]} |
                      {c["callee"] for c in fwd["candidates"]})
        self.assertNotIn("compiler.py::_evidence", everything)
        callers = {a["proposition"].get("caller")
                   for a in self.view["assertions"]
                   if a["proposition"].get("callee") == "compiler.py::_evidence"}
        self.assertNotIn("baselines.py::BM25.__init__", callers)

    def test_abstention_empty_reverse_true_forward(self):
        related = self.memory.related("baselines.py::pagerank")
        self.assertEqual(related["impact"]["impacts"], [])
        self.assertEqual(related["claims"]["claims"], [])
        self.assertEqual(related["mentions"]["mentions"], [])
        fwd = self.memory.callees("baselines.py::pagerank")
        self.assertEqual([d["callee"] for d in fwd["direct"]],
                         ["baselines.py::_evidence_edges"])
        self.assertEqual(fwd["candidates"], [])


if __name__ == "__main__":
    unittest.main()
