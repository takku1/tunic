"""ACQ v2 runner hardening: resume/checkpoints, quota handling, backend
injection, candidate-ID validation, and scoring rules.

All hermetic: CLI subprocesses and model calls are stubbed or bypassed.
No model, no network, no heldout, no artifact writes outside tmp dirs.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from cli_backend import BackendError, CLIBackend, QuotaError, kimi_backend  # noqa: E402
from run_acq_v2 import (  # noqa: E402
    QUOTA_EXIT_CODE,
    apply_pass,
    done_cells,
    load_resume,
    pending_cells,
    resolve_candidate_id,
    summarize_acq,
    write_record_atomic,
)
import run_model_ab_multisession_v2 as ms_v2  # noqa: E402

sys.path.pop(0)

CANDIDATES = [
    {"id": "assertion:aaa", "caller": "main.py::entry",
     "callee": "ops.py::process", "relation": "depends_on"},
    {"id": "assertion:bbb", "caller": "shop.py::ignite",
     "callee": "shop.py::handle", "relation": "depends_on"},
]


class CandidateIdTests(unittest.TestCase):
    def test_exact_id_resolves(self):
        self.assertEqual(resolve_candidate_id("assertion:aaa", CANDIDATES),
                         "assertion:aaa")

    def test_id_prefix_tolerance(self):
        # Models echo the candidate-block line format ("- id <id> | ...").
        self.assertEqual(
            resolve_candidate_id("id assertion:aaa", CANDIDATES),
            "assertion:aaa")
        self.assertEqual(
            resolve_candidate_id("  ID assertion:bbb  ", CANDIDATES),
            "assertion:bbb")

    def test_unique_suffix_resolves(self):
        self.assertEqual(resolve_candidate_id("aaa", CANDIDATES),
                         "assertion:aaa")

    def test_symbol_fallback_requires_uniqueness(self):
        self.assertEqual(
            resolve_candidate_id("main.py::entry", CANDIDATES),
            "assertion:aaa")
        both = CANDIDATES + [{"id": "assertion:ccc",
                              "caller": "main.py::entry",
                              "callee": "ops.py::other",
                              "relation": "depends_on"}]
        self.assertIsNone(resolve_candidate_id("main.py::entry", both))
        self.assertIsNone(resolve_candidate_id("assertion:zzz", CANDIDATES))
        self.assertIsNone(resolve_candidate_id("", CANDIDATES))
        self.assertIsNone(resolve_candidate_id("  id  ", CANDIDATES))


class CheckpointResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "run.json"
        self.config = {"suite": "dev", "scenarios": ["constructor"],
                       "arms": ["tunic-commit-cand"], "backend": "cli",
                       "model": "test"}

    def _record(self, rows, complete=False, config=None):
        return {"benchmark": "x", "config": config or self.config,
                "terminated": None, "complete": complete,
                "summary": {}, "tasks": rows}

    def test_atomic_write_round_trip(self):
        rows = [{"id": "dev-constructor-rep0-propose", "arm": "tunic-commit-cand"}]
        write_record_atomic(self.out, self._record(rows))
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["tasks"], rows)
        self.assertFalse((self.tmp / "run.json.tmp").exists())

    def test_resume_skips_recorded_cells_only(self):
        rows = [{"id": "dev-constructor-rep0-propose", "arm": "tunic-commit-cand"}]
        write_record_atomic(self.out, self._record(rows))
        loaded_rows, done = load_resume(self.out, self.config)
        self.assertEqual(loaded_rows, rows)
        pending = pending_cells("dev", ["constructor"], range(2),
                                ["tunic-commit-cand", "tunic-candidates"], done)
        # rep0/commit-cand done; same id on the other arm still pending.
        self.assertNotIn(("constructor", 0, "tunic-commit-cand"), pending)
        self.assertIn(("constructor", 0, "tunic-candidates"), pending)
        self.assertIn(("constructor", 1, "tunic-commit-cand"), pending)
        # Ordering is scenario -> rep -> arm.
        self.assertEqual(pending[0], ("constructor", 0, "tunic-candidates"))

    def test_done_key_includes_arm(self):
        rows = [{"id": "dev-constructor-rep0-propose", "arm": "tunic-commit-cand"}]
        done = done_cells(rows)
        self.assertIn(("dev-constructor-rep0-propose", "tunic-commit-cand"), done)
        self.assertNotIn(("dev-constructor-rep0-propose", "tunic-candidates"), done)

    def test_complete_run_is_not_rerun(self):
        write_record_atomic(self.out, self._record([], complete=True))
        self.assertIsNone(load_resume(self.out, self.config))

    def test_config_mismatch_exits_loudly(self):
        write_record_atomic(self.out, self._record([]))
        with self.assertRaises(SystemExit):
            load_resume(self.out, dict(self.config, suite="heldout"))

    def test_corrupt_partial_exits_loudly(self):
        self.out.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SystemExit):
            load_resume(self.out, self.config)

    def test_quota_exit_code_is_distinct(self):
        self.assertIsInstance(QUOTA_EXIT_CODE, int)
        self.assertNotIn(QUOTA_EXIT_CODE, (0, 1, 75))


class ApplyPassTests(unittest.TestCase):
    def test_positive_gates_on_commit(self):
        self.assertTrue(apply_pass("positive", True, True))
        self.assertFalse(apply_pass("positive", True, False))
        self.assertFalse(apply_pass("positive", False, True))

    def test_correction_and_duplicate_score_retrieval_only(self):
        self.assertTrue(apply_pass("correct", True, False))
        self.assertTrue(apply_pass("duplicate", True, False))
        self.assertFalse(apply_pass("correct", False, False))

    def test_summarize_counts_apply_rates(self):
        rows = [
            {"id": "dev-constructor-rep0-propose", "arm": "tunic-commit-cand",
             "suite": "dev", "scenario": "constructor", "pass": False,
             "committed": False},
            {"id": "dev-constructor-rep0-apply", "arm": "tunic-commit-cand",
             "suite": "dev", "scenario": "constructor", "pass": False},
        ]
        summary = summarize_acq(rows)
        cell = summary["dev/constructor/tunic-commit-cand"]
        self.assertEqual(cell["n"], 1)
        self.assertEqual(cell["apply_pass_rate"], 0.0)


class QuotaTests(unittest.TestCase):
    def setUp(self):
        sleeper = mock.patch("cli_backend.time.sleep", return_value=None)
        self.addCleanup(sleeper.stop)
        sleeper.start()

    def test_quota_text_raises_without_retry(self):
        backend = CLIBackend(name="t", exe="t", agent_file="a",
                             quota_markers=("quota", "429"))
        calls = []

        def fake_invoke(prompt, timeout_s):
            calls.append(prompt)
            return 1, "", "Error 429: quota exceeded for the window", 5.0

        with mock.patch.object(backend, "_invoke", side_effect=fake_invoke):
            with self.assertRaises(QuotaError):
                backend.chat("hello")
        self.assertEqual(len(calls), 1)

    def test_transient_exit_still_retries_then_succeeds(self):
        backend = CLIBackend(name="t", exe="t", agent_file="a")
        with mock.patch.object(
                backend, "_invoke",
                side_effect=[(75, "", "busy", 1.0),
                             (0, "SELECT assertion:aaa | ok", "", 2.0)]) as inv:
            response = backend.chat("hello")
        self.assertEqual(inv.call_count, 2)
        self.assertIn("SELECT", response["text"])

    def test_kimi_identity_format(self):
        backend = kimi_backend(model="m")
        self.assertTrue(backend.identity().startswith("kimi-cli:"))
        self.assertIn("subscription", backend.identity())


class BackendInjectionTests(unittest.TestCase):
    def test_run_arm_uses_injected_chat(self):
        seen = []
        response, context = ms_v2.run_arm(
            None, None, None, "none", "What breaks?",
            chat_fn=lambda prompt: seen.append(prompt) or {"text": "stub"})
        self.assertEqual(response, {"text": "stub"})
        self.assertIn("What breaks?", seen[0])

    def test_run_arm_default_still_module_chat(self):
        with mock.patch.object(ms_v2, "chat",
                               side_effect=AssertionError("must not run")):
            with self.assertRaises(AssertionError):
                ms_v2.run_arm(None, None, None, "none", "q")
        # ...while an injected function bypasses it entirely.
        response, _ = ms_v2.run_arm(
            None, None, None, "none", "q",
            chat_fn=lambda prompt: {"text": "ok"})
        self.assertEqual(response, {"text": "ok"})


class SessionIsolationTests(unittest.TestCase):
    def _popen_stub(self, calls):
        proc = mock.Mock()
        proc.pid = 4242
        proc.returncode = 0
        proc.communicate.return_value = ("answer text", "")

        def make(argv, **kwargs):
            calls.append(argv)
            return proc
        return make

    def test_two_calls_are_two_subprocesses_same_scratch_no_session_flags(self):
        backend = kimi_backend(model="m")
        calls = []
        with mock.patch("cli_backend.shutil.which", return_value="/fake/kimi"), \
             mock.patch("cli_backend.subprocess.Popen",
                        side_effect=self._popen_stub(calls)):
            first = backend.chat("one")
            second = backend.chat("two")
        self.assertEqual(len(calls), 2)
        self.assertEqual(first["text"], "answer text")
        self.assertEqual(second["text"], "answer text")
        for argv in calls:
            self.assertNotIn("--session", argv)
            self.assertNotIn("--continue", argv)
            self.assertIn("-p", argv)
        workdirs = [calls[i][calls[i].index("--work-dir") + 1] for i in (0, 1)]
        self.assertEqual(workdirs[0], workdirs[1])

    def test_missing_executable_fails_loudly(self):
        backend = CLIBackend(name="t", exe="no-such-exe-xyz", agent_file="a")
        with mock.patch("cli_backend.shutil.which", return_value=None), \
             mock.patch("cli_backend.time.sleep", return_value=None):
            with self.assertRaises(BackendError):
                backend.chat("hello")


if __name__ == "__main__":
    unittest.main()
