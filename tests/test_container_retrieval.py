"""Container-aware retrieval v1: a method question reaches asserted
class-callee edges through the verified containing class.

Navigational only: no edge is synthesized or asserted; the original
committed edge (caller -> class) is unchanged. Deterministic, no model,
dev fixtures only -- heldout is never touched.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from acqgen2 import build  # noqa: E402
from run_model_ab_multisession_v2 import bundle_tunic  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.render import render_answer_text  # noqa: E402

sys.path.pop(0)


def build_cell(suite, scenario, rep, tmp):
    spec = build(scenario, rep, suite)
    root = tmp / ("%s-rep%d" % (scenario, rep)) / "repo"
    root.mkdir(parents=True)
    for path, text in spec["files"].items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    memory = Memory(str(root.parent / "sessions.sqlite"), root)
    memory.ingest()
    return memory, spec


def commit_gold_constructor(memory, spec):
    gold = spec["gold"]
    cands = [c for c in memory.candidates()["candidates"]
             if c.get("via") == "constructor_call"]
    match = [c for c in cands
             if c["caller"] == gold[1] and c["callee"] == gold[2]]
    assert len(match) == 1, "gold candidate must be live and unique"
    receipt = memory.commit_candidate(
        match[0]["id"], "test justification selecting the gold candidate",
        task=spec["question"])
    assert receipt["committed"], receipt
    memory.ingest()
    return match[0]


def impacts_of(result):
    return {e["symbol"]: e for e in result["impacts"]}


def apply_question(spec):
    checks = spec.get("apply")
    if isinstance(checks, dict):
        return checks["question"]
    return checks[0]["question"]


class ContainerRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_constructor_apply_retrieves_gold_caller_after_commit(self):
        memory, spec = build_cell("dev", "constructor", 1, self.tmp)
        self.addCleanup(memory.close)
        commit_gold_constructor(memory, spec)
        result = memory.answer(apply_question(spec),
            policy="current", select=True)
        impacts = impacts_of(result)
        self.assertIn("shop.py::create", impacts)
        entry = impacts["shop.py::create"]
        self.assertEqual(
            (entry.get("derivation") or {}).get("verification"),
            "asserted_not_observed")
        context = result.get("container_expansion") or {}
        self.assertEqual(context.get("expanded"),
                         {"shop.py::Gadget.start": "shop.py::Gadget"})
        text = bundle_tunic(memory, apply_question(spec), select=True)
        self.assertIn("shop.py::create", text)
        self.assertIn("CONTAINER CONTEXT", text)

    def test_before_commit_nothing_is_misrepresented_as_asserted(self):
        memory, spec = build_cell("dev", "constructor", 1, self.tmp)
        self.addCleanup(memory.close)
        result = memory.answer(apply_question(spec),
                               policy="current", select=True)
        self.assertEqual(result["impacts"], [])
        text = render_answer_text(result, cap=4000)
        self.assertIn("ASSERTED DEPENDENCIES (source claims, "
                      "NOT independently verified):\n  (none)", text)
        self.assertTrue(any(e["symbol"] == "shop.py::Gadget.start"
                            for e in result["candidates"]))

    def test_retracted_edge_does_not_reappear_via_container(self):
        memory, spec = build_cell("dev", "constructor", 1, self.tmp)
        self.addCleanup(memory.close)
        commit_gold_constructor(memory, spec)
        memory.record_outcome("shop.py::create", "shop.py::Gadget",
                              rel="retracts", retract="depends_on",
                              document="notes/out.md", authority="reviewer")
        memory.ingest()
        result = memory.answer(apply_question(spec),
                               policy="current", select=True)
        self.assertNotIn("shop.py::create", impacts_of(result))

    def test_unrelated_class_does_not_leak(self):
        root = self.tmp / "two" / "repo"
        root.mkdir(parents=True)
        (root / "a.py").write_text(
            "class A:\n    def run(self):\n        return 1\n\n\n"
            "def make_a():\n    return A()\n", encoding="utf-8")
        (root / "b.py").write_text(
            "class B:\n    def walk(self):\n        return 2\n\n\n"
            "def make_b():\n    return B()\n", encoding="utf-8")
        memory = Memory(str(root.parent / "sessions.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        cands = [c for c in memory.candidates()["candidates"]
                 if c.get("via") == "constructor_call"]
        only_a = [c for c in cands if c["caller"] == "a.py::make_a"]
        self.assertEqual(len(only_a), 1)
        memory.commit_candidate(only_a[0]["id"], "test selects A",
                                task="what breaks if run changes")
        memory.ingest()
        result = memory.answer("what breaks if run changes",
                               policy="current", select=True)
        got = impacts_of(result)
        self.assertIn("a.py::make_a", got)
        self.assertFalse([s for s in got if s.startswith("b.py::")],
                         "unrelated class B leaked: %s" % sorted(got))

    def test_zero_container_budget_disables_expansion(self):
        memory, spec = build_cell("dev", "constructor", 1, self.tmp)
        self.addCleanup(memory.close)
        commit_gold_constructor(memory, spec)
        result = memory.answer(apply_question(spec),
                               policy="current", select=True,
                               container_limit=0)
        context = result.get("container_expansion") or {}
        self.assertEqual(context.get("expanded"), {})
        self.assertEqual(context.get("budget"), 0)
        self.assertNotIn("shop.py::create", impacts_of(result))

    def test_expansion_never_bypasses_selection(self):
        memory, spec = build_cell("dev", "constructor", 1, self.tmp)
        self.addCleanup(memory.close)
        commit_gold_constructor(memory, spec)
        for select in (True, False):
            result = memory.answer(apply_question(spec),
                                   policy="current", select=select,
                                   container_limit=2)
            expanded = (result.get("container_expansion") or {}).get(
                "expanded") or {}
            if result.get("selection") is not None:
                allowed = set(result["selection"]["kept"])
            else:
                allowed = {c["symbol"] for c in result["candidates"]}
            self.assertTrue(set(expanded) <= allowed,
                            "container expanded from unselected symbols")

    def test_snapshot_replay_is_deterministic(self):
        memory, spec = build_cell("dev", "constructor", 1, self.tmp)
        self.addCleanup(memory.close)
        commit_gold_constructor(memory, spec)
        question = apply_question(spec)
        first = memory.answer(question, policy="current", select=True)
        second = memory.answer(question, policy="current", select=True)
        for key in ("impacts", "mentions", "claims", "candidates",
                    "container_expansion"):
            self.assertEqual(first[key], second[key])

    def test_plain_function_question_expands_nothing(self):
        memory, spec = build_cell("dev", "callback", 0, self.tmp)
        self.addCleanup(memory.close)
        result = memory.answer(spec["question"], policy="current",
                               select=True)
        context = result.get("container_expansion") or {}
        self.assertEqual(context.get("used"), 0)
        self.assertEqual(context.get("expanded"), {})


if __name__ == "__main__":
    unittest.main()
