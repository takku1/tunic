"""Cross-Session Verified Memory Reuse v1 acceptance tests.

Paired Session-B arms (cold fresh ingest vs warm Session-A database)
run the same fixed scripted agent through the trusted Arena runner.
Acceptance: every reuse win scores warm=1/cold=0 with the acquired
assertion cited, every guard passes in both arms, warm is never worse,
no stale or proposed-only knowledge is reused, the Session-A database
reopens deterministically, and code-derived edges are identical across
arms (memory differs only by the audited acquisitions).
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from benchmarks.agent.reuse_v1 import (REUSE_TASKS, build_session_a,
                                       run_reuse_experiment)
from tunic.memory import Memory


def _code_edges(db_path):
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT a.caller, a.callee FROM assertions a "
            "JOIN sources s ON s.id = a.source WHERE s.path = 'svc.py'"
        ).fetchall()
    finally:
        con.close()
    return sorted(rows)


def _acquired_rows(db_path):
    """(caller, callee) asserted from reviewer/scheduler notes."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT a.caller, a.callee FROM assertions a "
            "JOIN sources s ON s.id = a.source "
            "WHERE s.path LIKE 'notes/%'"
        ).fetchall()
    finally:
        con.close()
    return sorted(set(rows))


class ReuseExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.report = run_reuse_experiment(cls._tmp.name)
        cls.cells = {(c["task"], c["arm"]): c
                     for c in cls.report["cells"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_all_cells_complete(self):
        self.assertEqual(len(self.report["cells"]), 2 * len(REUSE_TASKS))
        for cell in self.report["cells"]:
            self.assertEqual(
                cell["status"], "complete",
                "%s/%s status %s answer %r"
                % (cell["task"], cell["arm"], cell["status"],
                   cell["answer"]))

    def test_verified_reuse_wins(self):
        wins = [t for t in REUSE_TASKS if t[4] == "win"]
        self.assertEqual(len(wins), 4)
        for task_id, _kind, _text, _key, _role in wins:
            warm = self.cells[(task_id, "warm")]
            cold = self.cells[(task_id, "cold")]
            self.assertEqual(warm["score"], 1.0,
                             "%s warm answer %r" % (task_id,
                                                    warm["answer"]))
            self.assertEqual(cold["score"], 0.0,
                             "%s cold unexpectedly passed: %r"
                             % (task_id, cold["answer"]))

    def test_guards_hold_both_arms(self):
        guards = [t for t in REUSE_TASKS if t[4] == "guard"]
        self.assertEqual(len(guards), 5)
        for task_id, _kind, _text, _key, _role in guards:
            for arm in ("warm", "cold"):
                cell = self.cells[(task_id, arm)]
                self.assertEqual(cell["score"], 1.0,
                                 "%s/%s answer %r"
                                 % (task_id, arm, cell["answer"]))

    def test_warm_never_worse(self):
        self.assertTrue(self.report["metrics"]["warm_never_worse"])

    def test_no_stale_reuse(self):
        """Retired handle_old never appears as a current warm answer."""
        for task_id in ("reuse-route", "reuse-correction",
                        "reuse-traverse", "reuse-static", "reuse-caller"):
            answer = self.cells[(task_id, "warm")]["answer"]
            self.assertNotIn("svc.py::handle_old", answer.splitlines(),
                             "%s reuses retired symbol: %r" % (task_id,
                                                               answer))

    def test_metrics(self):
        metrics = self.report["metrics"]
        self.assertEqual(metrics["verified_reuse_rate"], 1.0)
        self.assertEqual(metrics["guard_hold_rate"], 1.0)
        self.assertLessEqual(metrics["mean_actions_warm"],
                             metrics["mean_actions_cold"])

    def test_session_a_reopens_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = build_session_a(tmp)
            db_path = session["db_path"]
            first = Memory(db_path, session["session_ws"])
            answer_first = first.answer(
                "Which function does dispatch route legacy requests to?",
                select=True)
            snapshot_first = first.store.current()
            first.close()
            second = Memory(db_path, session["session_ws"])
            answer_second = second.answer(
                "Which function does dispatch route legacy requests to?",
                select=True)
            snapshot_second = second.store.current()
            second.close()
        self.assertEqual(snapshot_first, snapshot_second)
        self.assertEqual(
            sorted(c["symbol"] for c in answer_first["candidates"]),
            sorted(c["symbol"] for c in answer_second["candidates"]))

    def test_code_edges_identical_across_sessions(self):
        """Same code snapshot: svc.py-derived edges equal; only audited
        reviewer/scheduler acquisitions differ."""
        with tempfile.TemporaryDirectory() as tmp:
            session = build_session_a(tmp)
            warm_db = session["db_path"]
            cold_db = str(Path(tmp) / "cold.sqlite")
            cold_ws = str(Path(tmp) / "cold-ws")
            import shutil
            from benchmarks.agent.reuse_v1 import FIXTURE
            shutil.copytree(str(FIXTURE), cold_ws)
            cold = Memory(cold_db, cold_ws)
            cold.ingest()
            cold.close()
            self.assertEqual(_code_edges(warm_db), _code_edges(cold_db))
            acquired = _acquired_rows(warm_db)
            self.assertIn(("svc.py::dispatch", "svc.py::do_legacy"),
                          acquired)
            self.assertIn(("svc.py::handle_new", "svc.py::handle_old"),
                          acquired)
            self.assertIn(("svc.py::do_new", "svc.py::do_legacy"),
                          acquired)

    def test_permission_boundary_blocks_stranger_reads(self):
        """Session-B memory refuses unknown principals without changes."""
        from tunic.memory import authorize
        with tempfile.TemporaryDirectory() as tmp:
            session = build_session_a(tmp)
            db_path = session["db_path"]
            before = _acquired_rows(db_path)
            with self.assertRaises(PermissionError):
                authorize("mallory", "answer")
            stranger = Memory(db_path, session["session_ws"],
                              principal="mallory")
            try:
                with self.assertRaises(PermissionError):
                    stranger.answer("Which function does dispatch route?")
            finally:
                stranger.close()
            self.assertEqual(before, _acquired_rows(db_path))

    def test_failed_acquisition_leaves_no_assertion(self):
        """Unknown endpoints refuse (invalid) without storing claims."""
        with tempfile.TemporaryDirectory() as tmp:
            session = build_session_a(tmp)
            memory = Memory(session["db_path"], session["session_ws"])
            try:
                before = _acquired_rows(session["db_path"])
                committed, action, _reason = memory.commit_proposal(
                    "svc.py::main", "svc.py::nope", rel="depends_on")
                self.assertFalse(committed)
                self.assertIn(action, ("invalid", "conflict"))
                self.assertEqual(before,
                                 _acquired_rows(session["db_path"]))
            finally:
                memory.close()

    def test_warm_answers_cite_acquired_assertions(self):
        """Each win's warm answer is backed by asserted Session-A memory."""
        with tempfile.TemporaryDirectory() as tmp:
            session = build_session_a(tmp)
            memory = Memory(session["db_path"], session["session_ws"])
            try:
                route = memory.answer(
                    "Which function does dispatch route legacy requests to?",
                    select=True)
                self.assertIn(
                    ("svc.py::dispatch", "depends_on", "svc.py::do_legacy"),
                    {(c["caller"], c["relation"], c["callee"])
                     for c in route["claims"]
                     if c.get("status") == "asserted_in_source"})
                correction = memory.answer("What should replace handle_old?",
                                           select=True)
                self.assertIn(
                    ("svc.py::handle_new", "supersedes",
                     "svc.py::handle_old"),
                    {(c["caller"], c["relation"], c["callee"])
                     for c in correction["claims"]
                     if c.get("status") == "asserted_in_source"})
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
