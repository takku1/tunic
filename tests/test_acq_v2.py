"""ACQ v2: fixture integrity (dry) and the bounded commit_candidate path.

Runs in CI without a model: builds every dev cell and checks static
visibility, candidate presence per scenario kind, and product-path
mechanics; then exercises Memory.commit_candidate governance directly.
Live five-arm A/B runs are local artifacts (benchmarks/agent/runs).
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from acqgen2 import SCENARIOS, build, digest  # noqa: E402
from run_model_ab_multisession_v4 import static_edge_visible  # noqa: E402
from tunic.memory import Memory  # noqa: E402

sys.path.pop(0)


class AcqV2FixtureTests(unittest.TestCase):
    """Every dev cell builds, digests stably, and produces its candidates."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_all_dev_cells_build_with_expected_candidates(self):
        for scenario in SCENARIOS:
            for rep in range(5):
                spec = build(scenario, rep, "dev")
                root = self.tmp / ("%s-rep%d" % (scenario, rep)) / "repo"
                root.mkdir(parents=True)
                for path, text in spec["files"].items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                memory = Memory(str(root.parent / "sessions.sqlite"), root)
                self.addCleanup(memory.close)
                memory.ingest()
                if spec.get("setup") == "contra":
                    rel, subj, obj = spec["gold"]
                    memory.record_outcome(obj, subj, rel="contradicts",
                                          document="notes/proposed.md",
                                          authority="reviewer")
                elif spec.get("setup") == "duplicate":
                    rel, subj, obj = spec["gold"]
                    memory.record_outcome(subj, obj, rel=rel,
                                          document="notes/proposed.md",
                                          authority="reviewer")
                elif spec.get("setup") == "correct":
                    e1, e2 = spec["stale"], spec["gold"]
                    memory.record_outcome(e1[1], e1[2], rel=e1[0],
                                          document="notes/proposed.md",
                                          authority="reviewer")
                    memory.record_outcome(e1[1], e1[2], rel="retracts",
                                          retract=e1[0],
                                          document="notes/proposed.md",
                                          authority="reviewer")
                    memory.record_outcome(e2[1], e2[2], rel=e2[0],
                                          document="notes/proposed.md",
                                          authority="reviewer")
                memory.ingest()
                if spec["kind"] in ("positive", "contra") and spec["gold"]:
                    rel, subj, obj = spec["gold"]
                    visible = static_edge_visible(memory, subj, obj)
                    want = spec.get("static", "invisible") == "visible"
                    self.assertEqual(visible, want,
                                     "%s rep%d static visibility" % (scenario, rep))
                vias = {c.get("via") for c in memory.candidates()["candidates"]}
                for via in spec.get("expected_vias", ()):
                    self.assertIn(via, vias,
                                  "%s rep%d missing %s" % (scenario, rep, via))
                if spec["kind"] == "duplicate":
                    self.assertIn(spec["gold"][2],
                                  {c.get("object") for c in
                                   memory.claims(spec["gold"][2]).get("claims", [])}
                                  | {s["key"] for s in
                                     memory.store.view()[1]["symbols"]})
                self.assertEqual(digest("dev", scenario, rep),
                                 digest("dev", scenario, rep))

    def test_heldout_fixtures_differ_from_dev(self):
        for scenario in SCENARIOS:
            for rep in range(5):
                dev = build(scenario, rep, "dev")
                held = build(scenario, rep, "heldout")
                self.assertNotEqual(digest("dev", scenario, rep),
                                    digest("heldout", scenario, rep),
                                    "%s rep%d suites must differ" % (scenario, rep))
                self.assertEqual(dev["kind"], held["kind"])


class ResolveCandidateIdTests(unittest.TestCase):
    """Model-echoed selections map back to live candidate ids."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "benchmarks/agent"))
        from run_acq_v2 import resolve_candidate_id  # noqa: E402
        self.resolve = resolve_candidate_id
        sys.path.pop(0)
        self.candidates = [
            {"id": "assertion:aaa111", "caller": "main.py::entry",
             "callee": "ops.py::process"},
            {"id": "assertion:bbb222", "caller": "use.py::create",
             "callee": "lib.py::Widget"},
        ]

    def test_exact_id(self):
        self.assertEqual(self.resolve("assertion:aaa111", self.candidates),
                         "assertion:aaa111")

    def test_suffix_id_prefix_dropped(self):
        self.assertEqual(self.resolve("aaa111", self.candidates),
                         "assertion:aaa111")

    def test_symbol_echo_resolves_when_unique(self):
        self.assertEqual(self.resolve("lib.py::Widget", self.candidates),
                         "assertion:bbb222")

    def test_ambiguous_symbol_resolves_to_nothing(self):
        both = self.candidates + [{"id": "assertion:ccc333",
                                   "caller": "other.py::f",
                                   "callee": "lib.py::Widget"}]
        self.assertIsNone(self.resolve("lib.py::Widget", both))

    def test_unknown_resolves_to_nothing(self):
        self.assertIsNone(self.resolve("nope.py::nope", self.candidates))


class ApplyPassRuleTests(unittest.TestCase):
    """The apply-row gate: positive requires the commit; pre-asserted
    knowledge scenarios (correction, duplicate) must not — their correct
    propose outcome is a no-op."""

    def setUp(self):
        sys.path.insert(0, str(REPO / "benchmarks/agent"))
        from run_acq_v2 import apply_pass  # noqa: E402
        self.apply_pass = apply_pass
        sys.path.pop(0)

    def test_positive_requires_commit(self):
        self.assertTrue(self.apply_pass("positive", True, True))
        self.assertFalse(self.apply_pass("positive", True, False))
        self.assertFalse(self.apply_pass("positive", False, True))

    def test_correction_does_not_gate_on_commit(self):
        # correction cells pre-assert the corrected edge: the model's
        # no-op (duplicate reconcile / abstention) is correct behavior
        self.assertTrue(self.apply_pass("correction", True, False))

    def test_duplicate_does_not_gate_on_commit(self):
        self.assertTrue(self.apply_pass("duplicate", True, False))

    def test_stale_or_wrong_answer_fails_everywhere(self):
        for scenario in ("positive", "correction", "duplicate"):
            self.assertFalse(self.apply_pass(scenario, False, True))


class CommitCandidateTests(unittest.TestCase):
    """The bounded candidate-aided acquisition path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "ops.py").write_bytes(
            b"def process(x):\n    return x\n\n\n"
            b"def run(fn, x):\n    return fn(x)\n")
        (self.root / "main.py").write_bytes(
            b"from ops import run, process\n\n\n"
            b"def entry(x):\n    return run(process, x)\n")
        self.db = str(self.tmp / "memory.sqlite")

    def _memory(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        return memory

    def test_commit_candidate_acquires_exact_gold_edge(self):
        memory = self._memory()
        candidate = next(c for c in memory.candidates()["candidates"]
                         if c.get("via") == "callback_parameter")
        receipt = memory.commit_candidate(
            candidate["id"], "entry passes process to run which invokes it",
            task="acquire")
        self.assertTrue(receipt["committed"], receipt)
        self.assertEqual(receipt["action"], "add")
        self.assertEqual(receipt["proposition"]["caller"], "main.py::entry")
        self.assertEqual(receipt["proposition"]["callee"], "ops.py::process")
        memory.ingest()
        # the edge is now asserted and traversable: subsequent-session
        # retrieval finds the caller
        from tunic.compiler import compile_impact
        impact = compile_impact(memory.store, "ops.py::process")
        reached = {unit["assertion"]["proposition"]["caller"]
                   for unit in impact["units"]}
        self.assertIn("main.py::entry", reached)

    def test_empty_justification_refused(self):
        memory = self._memory()
        candidate = next(c for c in memory.candidates()["candidates"])
        with self.assertRaises(ValueError):
            memory.commit_candidate(candidate["id"], "   ")

    def test_unknown_candidate_refused(self):
        memory = self._memory()
        with self.assertRaises(ValueError):
            memory.commit_candidate("assertion:deadbeef", "looks right")

    def test_second_commit_of_same_candidate_is_duplicate_noop(self):
        # the candidate assertion stays proposed in the graph after a
        # successful commit; recommitting it must reconcile as DUPLICATE
        # (explicit NOOP), never double-promote
        memory = self._memory()
        candidate = next(c for c in memory.candidates()["candidates"])
        first = memory.commit_candidate(candidate["id"], "verified by test", task="t")
        self.assertTrue(first["committed"])
        memory.ingest()
        second = memory.commit_candidate(candidate["id"], "second attempt")
        self.assertFalse(second["committed"])
        self.assertEqual(second["action"], "duplicate")

    def test_conflict_still_refused_through_candidate_path(self):
        memory = self._memory()
        memory.record_outcome("ops.py::process", "main.py::entry",
                              rel="contradicts", document="notes/r.md",
                              authority="reviewer")
        memory.ingest()
        candidate = next(c for c in memory.candidates()["candidates"])
        receipt = memory.commit_candidate(candidate["id"], "supported", task="t")
        self.assertFalse(receipt["committed"])
        self.assertEqual(receipt["action"], "conflict")

    def test_duplicate_reconciles_as_noop(self):
        memory = self._memory()
        memory.record_outcome("main.py::entry", "ops.py::process",
                              rel="depends_on", document="notes/r.md",
                              authority="reviewer")
        memory.ingest()
        candidate = next(c for c in memory.candidates()["candidates"])
        receipt = memory.commit_candidate(candidate["id"], "already known", task="t")
        self.assertFalse(receipt["committed"])
        self.assertEqual(receipt["action"], "duplicate")
