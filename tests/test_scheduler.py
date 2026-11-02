"""Frontier scheduler v1: bounded, resumable, governed investigation.

Failing-first contract: ranked clusters become persisted jobs; scratch
investigation applies home only through commit_proposal + proof-backed
frontier_resolve; interruption resumes without duplicate promotions;
moved snapshots go stale instead of applying stale evidence; blocked
clusters yield capability receipts with entries left open.
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tunic.__main__ import main as tunic_main  # noqa: E402
from tunic.memory import Memory  # noqa: E402
from tunic.schedule import list_jobs, run_schedule  # noqa: E402

sys.path.pop(0)

CLOSE_A = ("def do_run(x):\n"
           "    return x\n"
           "\n\n"
           'HANDLERS = {"run": do_run}\n'
           "\n\n"
           "def dispatch(kind, x):\n"
           "    return HANDLERS[kind](x)\n"
           "\n\n"
           "def main():\n"
           '    return dispatch("run", 1)\n')

CLOSE_B = ("def do_walk(x):\n"
           "    return x\n"
           "\n\n"
           'MOVES = {"walk": do_walk}\n'
           "\n\n"
           "def move(kind, x):\n"
           "    return MOVES[kind](x)\n"
           "\n\n"
           "def start():\n"
           '    return move("walk", 2)\n')

BLOCKED_ONLY = ("import os\n"
                "\n\n"
                "def place(base):\n"
                '    return os.path.join(base, "x")\n')


def make_repo(files):
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "repo"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return tmp, root


def ingest_copy(tmp, root):
    db = str(tmp / "home.sqlite")
    memory = Memory(db, root)
    memory.ingest()
    memory.close()
    return db


class SchedulerTests(unittest.TestCase):
    def test_ranked_closure_resolves_with_proof(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        report = run_schedule(db, str(root), top_n=3)
        self.assertEqual(report["totals"]["done"], 2)
        self.assertGreaterEqual(report["totals"]["resolved"], 1)
        self.assertGreaterEqual(report["totals"]["edges_added"], 1)
        memory = Memory(db, str(root))
        self.addCleanup(memory.close)
        view = memory.store.view()[1]
        pairs = {(p.get("caller"), p.get("callee"))
                 for a in view["assertions"]
                 if a.get("status") == "asserted_in_source"
                 for p in [a.get("proposition", {})]
                 if p.get("relation") == "depends_on"}
        self.assertIn(("close_a.py::main", "close_a.py::do_run"), pairs)
        from tunic.triage import _cluster_key as cluster_of
        remaining = {
            e["id"] for e in memory.frontier_list(status="open")
            if tuple(cluster_of(e)) == ("unresolved_dispatch",
                                        "close_a.py::HANDLERS")}
        self.assertEqual(remaining, set())
        jobs = list_jobs(db)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["state"], "done")
        self.assertTrue(jobs[0]["outcome"]["entries"])

    def test_duplicate_schedule_returns_same_job(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        first = run_schedule(db, str(root), top_n=3, deadline_s=0)
        second = run_schedule(db, str(root), top_n=3, deadline_s=0)
        self.assertEqual([j["id"] for j in first["jobs"]],
                         [j["id"] for j in second["jobs"]])
        self.assertEqual(first["queued_left"], second["queued_left"])
        for job in list_jobs(db):
            self.assertEqual(job["state"], "queued")
            self.assertEqual(job["attempts"], [])

    def test_crash_before_work_resumes_without_duplicate(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        calls = []

        def boom(job):
            calls.append(job["id"])
            raise RuntimeError("simulated crash")

        with self.assertRaises(RuntimeError):
            run_schedule(db, str(root), top_n=3, hooks={"before_job": boom})
        self.assertEqual(len(calls), 1)
        report = run_schedule(db, str(root), top_n=3)
        self.assertEqual(report["totals"]["done"], 2)
        memory = Memory(db, str(root))
        self.addCleanup(memory.close)
        view = memory.store.view()[1]
        edges = [a for a in view["assertions"]
                 if a.get("status") == "asserted_in_source"
                 and a.get("proposition", {}).get("caller")
                 == "close_a.py::main"
                 and a.get("proposition", {}).get("callee")
                 == "close_a.py::do_run"]
        self.assertEqual(len(edges), 1)

    def test_interrupted_running_job_redrives(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        run_schedule(db, str(root), top_n=1, deadline_s=0)
        memory = Memory(db, str(root))
        try:
            ((job_id, record),) = [
                (job_id, record)
                for job_id, record in memory.store.connection.execute(
                    "SELECT id, record FROM schedule_jobs").fetchall()]
            job = json.loads(record)
            job["state"] = "running"
            memory.store.connection.execute(
                "INSERT OR REPLACE INTO schedule_jobs(id, record) "
                "VALUES(?, ?)", (job_id, json.dumps(job)))
            memory.store.connection.commit()
        finally:
            memory.close()
        report = run_schedule(db, str(root), top_n=1)
        self.assertEqual(report["totals"]["done"], 1)
        jobs = list_jobs(db)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], "done")
        self.assertEqual(len(jobs[0]["attempts"]), 1)

    def test_stale_snapshot_withholds_application(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        planned = run_schedule(db, str(root), top_n=3, deadline_s=0)
        self.assertEqual(planned["queued_left"], 2)
        with (root / "close_a.py").open("a", encoding="utf-8") as handle:
            handle.write("\n# touch to move the snapshot\n")
        memory = Memory(db, str(root))
        memory.ingest()
        memory.close()
        report = run_schedule(db, str(root), top_n=3)
        self.assertEqual(report["totals"]["stale"], 2)
        stale_jobs = [j for j in list_jobs(db) if j["state"] == "stale"]
        self.assertEqual(len(stale_jobs), 2)
        self.assertTrue(all("snapshot" in (j["outcome"] or {}).get("reason", "")
                            for j in stale_jobs))
        # Fresh jobs planned on the moved snapshot still execute; the
        # stale evidence is never applied twice: exactly one edge.
        self.assertEqual(report["totals"]["done"], 2)
        memory = Memory(db, str(root))
        self.addCleanup(memory.close)
        view = memory.store.view()[1]
        edges = [a for a in view["assertions"]
                 if a.get("status") == "asserted_in_source"
                 and a.get("proposition", {}).get("caller")
                 == "close_a.py::main"
                 and a.get("proposition", {}).get("callee")
                 == "close_a.py::do_run"]
        self.assertEqual(len(edges), 1)

    def test_budget_max_clusters_caps_planning(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A, "close_b.py": CLOSE_B})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        report = run_schedule(db, str(root), top_n=5, max_clusters=2,
                              budget={"max_sites": 5, "max_commits": 3})
        self.assertEqual(len(report["jobs"]), 2)
        self.assertEqual(report["queued_left"], 0)

    def test_deadline_zero_leaves_all_queued(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        report = run_schedule(db, str(root), top_n=5, deadline_s=0)
        self.assertEqual(len(report["clusters"]), 2)
        self.assertEqual(report["queued_left"], 2)
        self.assertEqual(report["jobs"], [])
        for job in list_jobs(db):
            self.assertEqual(job["state"], "queued")

    def test_blocked_cluster_yields_capability_not_evidence(self):
        tmp, root = make_repo({"only.py": BLOCKED_ONLY})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        report = run_schedule(db, str(root), top_n=3)
        self.assertEqual(report["totals"]["blocked"], 1)
        job = report["jobs"][0]
        self.assertIn("capability_request", job["outcome"])
        memory = Memory(db, str(root))
        self.addCleanup(memory.close)
        self.assertGreater(
            len(memory.frontier_list(status="open")), 0)
        view = memory.store.view()[1]
        self.assertFalse(any(
            a.get("proposition", {}).get("caller", "").endswith("::place")
            and a.get("status") == "asserted_in_source"
            for a in view["assertions"]))

    def test_terminal_entries_skip_execution(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)

        def dismiss_all(job):
            memory = Memory(db, str(root))
            try:
                for entry in memory.frontier_list(status="open"):
                    memory.frontier_transition(
                        entry["id"], "dismissed",
                        resolution="test dismisses before execution")
            finally:
                memory.close()

        report = run_schedule(db, str(root), top_n=3,
                              hooks={"before_job": dismiss_all})
        self.assertEqual(report["totals"]["done"], 2)
        for finished in report["jobs"]:
            self.assertIn("no open entries",
                          finished["outcome"]["reason"])

    def test_invalid_proof_never_closes(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        memory = Memory(db, str(root))
        self.addCleanup(memory.close)
        entry_id = memory.frontier_list(status="open")[0]["id"]
        with self.assertRaises(ValueError):
            memory.frontier_resolve(entry_id, {"bogus": True})
        self.assertEqual(
            memory.frontier_list(status="open")[0]["id"], entry_id)

    def test_gates_reject_bad_config(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        with self.assertRaises(ValueError):
            run_schedule(db, str(root), concurrency_limit=2)
        with self.assertRaises(ValueError):
            run_schedule(db, str(root),
                         budget={"max_sites": 5, "max_commits": -1})
        with self.assertRaises(ValueError):
            run_schedule(db, str(root), top_n=-1)
        with self.assertRaises(ValueError):
            run_schedule(db, str(root), deadline_s=-1)

    def test_cli_schedule_runs(self):
        tmp, root = make_repo({"close_a.py": CLOSE_A})
        self.addCleanup(shutil.rmtree, tmp, True)
        db = ingest_copy(tmp, root)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = tunic_main(["--db", db, "schedule",
                               "--root", str(root), "--top", "1"])
        self.assertEqual(code, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report["totals"]["done"], 1)


if __name__ == "__main__":
    unittest.main()
