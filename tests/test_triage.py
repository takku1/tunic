"""Read-only frontier triage: clustering, queues, rankings, mutation check."""

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tunic.memory import Memory
from tunic.triage import analyze


class TriageTests(unittest.TestCase):
    """Clusters, queues, weighting passes, and the read-only guarantee."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        (self.root / "dynamic.py").write_bytes(
            b"def run(handler, value):\n    return handler(value)\n")
        (self.root / "broken.py").write_bytes(b"def oops(:\n    pass\n")
        self.db = str(self.tmp / "memory.sqlite")
        self.memory = Memory(self.db, self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()
        self.memory.answer("Zebra xylophone quark jigsaw zephyr quandary?")

    def test_clusters_queues_rankings_and_mutation_check(self):
        report = analyze(self.db, root=str(self.root))
        self.assertEqual(report["version"], 1)
        failures = report["counts"]["by_failure"]
        for failure in ("ingest_unresolved_call", "ingest_parse_error",
                        "retrieval_abstention"):
            self.assertIn(failure, failures)
        self.assertEqual(report["counts"]["entries"],
                         sum(failures.values()))
        # local unresolved call, parse error, and abstention are all
        # investigable; nothing is closable without asserted cover and
        # nothing is blocked without an external target.
        queues = report["queues"]
        self.assertEqual(queues["investigable"]["clusters"], 3)
        self.assertNotIn("closable_now", queues)
        self.assertNotIn("blocked", queues)
        # four weighting passes, base identical to itself.
        self.assertEqual(set(report["rankings_overlap_vs_base"]),
                         {"base", "recall_heavy", "effort_averse",
                          "impact_first"})
        self.assertEqual(report["rankings_overlap_vs_base"]["base"], 1.0)
        # every ranked row carries a reason and a positive score.
        self.assertTrue(report["top"])
        for row in report["top"]:
            self.assertGreater(row["score"], 0)
            self.assertTrue(row["reason"])
            self.assertIn(row["kind"], report["counts"]["by_kind"])
        # abstention outranks the single-occurrence extractor failures.
        self.assertEqual(report["top"][0]["kind"], "retrieval_abstention")
        # no closable cluster: recommendation points at the closest
        # investigable entry, with no verifiable target yet.
        self.assertIsNone(report["recommendation"]["verifiable_target"])
        self.assertIn(report["top"][0]["reason"],
                      report["recommendation"]["plan"])
        # read-only proof: digests and counts identical, zero writes.
        check = report["mutation_check"]
        self.assertEqual(check["before"], check["after"])
        self.assertEqual(check["writes"], 0)

    def test_tampered_ledger_fails_closed(self):
        real = Memory.frontier_list

        def tampering(self, status=None):
            self.store.connection.execute(
                "UPDATE frontier SET record = record || ' '")
            self.store.connection.commit()
            return real(self, status=status)

        with mock.patch.object(Memory, "frontier_list", tampering):
            with self.assertRaises(AssertionError):
                analyze(self.db, root=str(self.root))

    def test_external_stdlib_call_is_blocked_not_actionable(self):
        self.memory.frontier_record(
            "unresolved", "resolve call os.path.join",
            "ingest_unresolved_call", target="os.path.join('a', 'b')")
        report = analyze(self.db, root=str(self.root))
        external = [c for c in report["top"] if c["kind"] == "unresolved_call"
                    and any("os.path" in t for t in c["targets"])]
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0]["queue"], "blocked")
        self.assertIn("external stdlib symbol", external[0]["reason"])
        self.assertEqual(report["queues"]["blocked"]["clusters"], 1)
        # a high-priority blocked entry still recommends an investigable
        # cluster, never a fabricated proof.
        self.assertEqual(report["top"][0]["queue"], "investigable")
        self.assertIsNone(report["recommendation"]["verifiable_target"])


class TriageDispatchTests(unittest.TestCase):
    """Covering asserted edges surface as closable_now with a replay plan."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "pricing.py").write_bytes(b"def round_total(cart):\n    return cart\n")
        (self.root / "dispatch.py").write_bytes(
            b"from pricing import round_total\n\nSTRATEGIES = {\"standard\": round_total}\n\n\n"
            b"def apply(strategy, cart):\n    return STRATEGIES[strategy](cart)\n")
        (self.root / "checkout.py").write_bytes(
            b"from dispatch import apply\n\n\ndef place_order(cart):\n"
            b"    return apply(\"standard\", cart)\n")
        self.db = str(self.tmp / "memory.sqlite")

    def test_covered_dispatch_cluster_is_closable_now(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        report = analyze(self.db, root=str(self.root))
        cluster = next(c for c in report["top"]
                       if c["kind"] == "unresolved_dispatch")
        self.assertEqual(cluster["queue"], "investigable")
        self.assertIn("candidate present without asserted cover",
                      cluster["reason"])
        committed, action, _ = memory.commit_proposal(
            "checkout.py::place_order", "pricing.py::round_total")
        self.assertEqual((committed, action), (True, "add"))
        memory.ingest()
        report = analyze(self.db, root=str(self.root))
        cluster = next(c for c in report["top"]
                       if c["kind"] == "unresolved_dispatch")
        self.assertEqual(cluster["queue"], "closable_now")
        self.assertIn("asserted edge already covers", cluster["reason"])
        recommendation = report["recommendation"]
        self.assertEqual(recommendation["candidate"]["kind"],
                         "unresolved_dispatch")
        self.assertEqual(recommendation["verifiable_target"],
                         "dispatch.py::STRATEGIES")
        self.assertIn("frontier_replay", recommendation["plan"])


class TriageCLITests(unittest.TestCase):
    """The triage command prints the report through the CLI."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        self.db = str(self.tmp / "memory.sqlite")
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()

    def _run(self, *argv):
        from tunic.__main__ import main
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--db", self.db, *argv])
        self.assertEqual(code, 0)
        return json.loads(buffer.getvalue())

    def test_triage_command_reports_empty_ledger(self):
        result = self._run("triage", "--root", str(self.root))
        self.assertEqual(result["counts"]["entries"], 0)
        self.assertEqual(result["top"], [])
        self.assertEqual(result["mutation_check"]["writes"], 0)
        self.assertEqual(result["rankings_overlap_vs_base"]["base"], 1.0)

    def test_triage_command_reports_filed_entries(self):
        memory = Memory(self.db, self.root)
        memory.frontier_record(
            "unsupported", "resolve registry dispatch",
            "static_edge_unresolved", target="dispatch.py::STRATEGIES")
        memory.close()
        result = self._run("triage", "--root", str(self.root), "--top", "5")
        self.assertEqual(result["counts"]["entries"], 1)
        self.assertEqual(result["top"][0]["kind"], "unresolved_dispatch")
        self.assertLessEqual(len(result["top"]), 5)
