"""Frontier Triage Ranking v1: verifiability over noise.

A cluster with dozens of raw occurrences but no verifiable
relationship must rank below a small cluster whose gaps can be
closed. Raw diagnostic counts are never evidence: the score uses
expected new verifiable relationships (Y), corroborated evidence
quality (Q), retrieval/impact leverage (L), investigation cost (E),
and governance risk (G). Ranking dedupes by (source location,
diagnostic identity) without touching stored records.
"""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory
from tunic import triage as T


class RankingModelTests(unittest.TestCase):
    """Verifiable gaps outrank noisy ones; scores are explainable."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        # Verifiable targets live here; nothing calls them with imports.
        (self.root / "quiet.py").write_text(
            "def helper():\n    return 1\n\n\n"
            "def worker():\n    return 2\n", encoding="utf-8")
        # Two unresolved calls whose heads ARE graph symbols.
        (self.root / "gap.py").write_text(
            "def use():\n    return helper()\n\n\n"
            "def use_two():\n    return worker()\n", encoding="utf-8")
        # Thirty builtin calls: loud, worthless.
        noisy = "".join(
            "def n%d(x):\n    return len(x)\n\n\n" % i for i in range(30))
        (self.root / "noisy.py").write_text(noisy, encoding="utf-8")
        self.db = str(self.tmp / "memory.sqlite")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()

    def _report(self):
        return T.analyze(self.db, root=str(self.root))

    def test_verifiable_cluster_outranks_noisy_cluster(self):
        report = self._report()
        order = [c["key"] for c in report["top"]]
        gap = next(i for i, k in enumerate(order) if k[1].endswith("gap.py"))
        noisy = next(i for i, k in enumerate(order) if k[1].endswith("noisy.py"))
        self.assertLess(gap, noisy)

    def test_raw_occurrences_do_not_decide(self):
        report = self._report()
        noisy = next(c for c in report["top"] if c["key"][1].endswith("noisy.py"))
        gap = next(c for c in report["top"] if c["key"][1].endswith("gap.py"))
        self.assertGreater(noisy["occurrences"], gap["occurrences"])
        self.assertGreater(gap["score"], noisy["score"])

    def test_every_row_is_explainable(self):
        report = self._report()
        for row in report["top"]:
            factors = row.get("factors")
            self.assertIsNotNone(factors)
            for name in ("Y", "Q", "L", "E", "G"):
                self.assertIn(name, factors)
            expect = (factors["Y"] * (1 + factors["Q"]) * (1 + factors["L"])
                      * factors["G"] / factors["E"])
            self.assertAlmostEqual(row["score"], round(expect, 4))
            self.assertTrue(row["reason"])

    def test_unresolved_counts_are_not_evidence(self):
        report = self._report()
        noisy = next(c for c in report["top"] if c["key"][1].endswith("noisy.py"))
        # Thirty builtins corroborate nothing: quality stays empty even
        # though the occurrence count dominates the ledger.
        self.assertEqual(noisy["factors"]["Q"], 0)
        self.assertEqual(noisy["factors"]["Y"], 0)

    def test_yield_is_budget_scoped_not_volume_driven(self):
        # Eight verifiable sites: expected yield caps at what one
        # standard investigation (3 commits) can realize, and cost
        # counts investigated sites, not the whole cluster.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        (root / "quiet.py").write_text(
            "".join("def h%d():\n    return %d\n\n\n" % (i, i)
                     for i in range(8)), encoding="utf-8")
        (root / "big.py").write_text(
            "".join("def u%d():\n    return h%d()\n\n\n" % (i, i)
                     for i in range(8)), encoding="utf-8")
        db = str(tmp / "memory.sqlite")
        memory = Memory(db, root)
        self.addCleanup(memory.close)
        memory.ingest()
        report = T.analyze(db, root=str(root))
        cluster = next(c for c in report["top"]
                       if c["key"][1].endswith("big.py"))
        self.assertLessEqual(cluster["factors"]["Y"], 3.0)
        self.assertEqual(cluster["factors"]["E"], 4 + 5)

    def test_recurrence_without_new_evidence_adds_no_votes(self):
        before = self._report()
        gap_before = next(c for c in before["top"]
                          if c["key"][1].endswith("gap.py"))
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        memory.ingest()
        after = T.analyze(self.db, root=str(self.root))
        gap_after = next(c for c in after["top"]
                         if c["key"][1].endswith("gap.py"))
        self.assertGreater(gap_after["occurrences"], gap_before["occurrences"])
        self.assertEqual(gap_after["score"], gap_before["score"])
        self.assertEqual(gap_after["factors"], gap_before["factors"])


class RankingDedupTests(unittest.TestCase):
    """Ranking dedup never mutates the ledger."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        body = "def first(cb):\n    return cb(1)\n\n\ndef second(cb):\n    return cb(2)\n"
        (self.root / "multi.py").write_text(body, encoding="utf-8")
        self.db = str(self.tmp / "memory.sqlite")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()

    def test_same_site_counts_once_for_ranking(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        entries = [e for e in memory.frontier_list(status="open")]
        sites = T.dedup_sites(entries)
        self.assertEqual(len(sites), 2)
        keys = {(s["path"], s["line"], s["start"]) for s in sites}
        self.assertEqual(len(keys), 2)

    def test_ranking_reads_leave_everything_untouched(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        rows_before = memory.store.connection.execute(
            "SELECT id, record FROM frontier ORDER BY id").fetchall()
        report = T.analyze(self.db, root=str(self.root))
        self.assertTrue(report["top"])
        rows_after = memory.store.connection.execute(
            "SELECT id, record FROM frontier ORDER BY id").fetchall()
        self.assertEqual(rows_before, rows_after)


class GovernanceDiscountTests(unittest.TestCase):
    """Conflict-adjacent clusters rank below clean ones with equal yield."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "quiet.py").write_text(
            "def helper():\n    return 1\n", encoding="utf-8")
        (self.root / "gap.py").write_text(
            "def use():\n    return helper()\n", encoding="utf-8")
        self.db = str(self.tmp / "memory.sqlite")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        memory.frontier_record(
            "contradictory", "reconcile gap.py::use against helper",
            "reconcile_conflict", target="gap.py::use")

    def test_conflict_cluster_discounts_below_clean(self):
        report = T.analyze(self.db, root=str(self.root))
        clean = next(c for c in report["top"]
                     if c["kind"] == "unresolved_call"
                     and c["key"][1].endswith("gap.py"))
        risky = next(c for c in report["top"]
                     if c["kind"] == "refused_proposal"
                     and any("gap.py" in t for t in c["targets"]))
        self.assertLess(risky["factors"]["G"], clean["factors"]["G"])
        self.assertLess(risky["score"], clean["score"])


class ExperimentHarnessTests(unittest.TestCase):
    """Ranked-vs-baseline comparison mechanics on fixtures."""

    def test_arms_never_write_into_the_source_tree(self):
        from tunic import investigate as inv
        from tunic.investigate import compare_arms
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        # helper() without an import: unresolved, head matches exactly
        # one symbol, so the harness attempts a governed commit -- which
        # files a claim block. None of that may land in the source tree.
        (root / "a.py").write_text(
            "def use():\n    return helper()\n", encoding="utf-8")
        (root / "b.py").write_text(
            "def helper():\n    return 1\n", encoding="utf-8")
        (root / "notes").mkdir()
        sentinel = root / "notes" / "session.md"
        sentinel.write_text("do not touch\n", encoding="utf-8")
        db = str(tmp / "memory.sqlite")
        memory = Memory(db, root)
        memory.ingest()
        open_entries = memory.frontier_list(status="open")
        self.assertTrue(any(e.get("failure") == "ingest_unresolved_call"
                            for e in open_entries))
        memory.close()
        before = {p.name: p.read_bytes()
                  for p in (root / "notes").iterdir() if p.is_file()}
        inv.compare_arms(db, str(root), top_n=1,
                         budget={"max_sites": 5, "max_commits": 3})
        after = {p.name: p.read_bytes()
                 for p in (root / "notes").iterdir() if p.is_file()}
        self.assertEqual(before, after)

    def test_investigation_publishes_then_closes(self):
        # No pre-commit, no pre-ingest: the harness alone must commit
        # the single candidate, publish it, and resolve with proof.
        from tunic.investigate import investigate
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        (root / "a.py").write_text(
            "def use():\n    return helper()\n", encoding="utf-8")
        (root / "b.py").write_text(
            "def helper():\n    return 1\n", encoding="utf-8")
        db = str(tmp / "memory.sqlite")
        memory = Memory(db, root)
        memory.ingest()
        report = T.analyze(db, root=str(root))
        key = tuple(report["top"][0]["key"])
        outcome = investigate(memory, key,
                              {"max_sites": 5, "max_commits": 3})
        memory.close()
        self.assertEqual(outcome["resolved"], 1)
        self.assertEqual(outcome["edges_added"], 1)
        self.assertEqual(outcome["yield"], 2)
        kinds = [a["action"] for a in outcome["actions"]]
        self.assertIn("refresh", kinds)
        self.assertIn("close", kinds)

    def test_crash_mid_arm_leaves_source_clean(self):
        from tunic import investigate as inv
        import unittest.mock as mock
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        (root / "a.py").write_text(
            "def use():\n    return helper()\n", encoding="utf-8")
        (root / "b.py").write_text(
            "def helper():\n    return 1\n", encoding="utf-8")
        db = str(tmp / "memory.sqlite")
        memory = Memory(db, root)
        memory.ingest()
        memory.close()
        real_investigate = inv.investigate

        def crashing(memory, cluster_key, budget):
            outcome = real_investigate(memory, cluster_key, budget)
            raise RuntimeError("simulated mid-arm crash")

        with mock.patch.object(inv, "investigate", crashing):
            with self.assertRaises(RuntimeError):
                inv.compare_arms(db, str(root), top_n=1,
                                 budget={"max_sites": 5, "max_commits": 3})
        self.assertFalse((root / "notes").exists())

    def test_ranked_arm_beats_raw_count_arm(self):
        from tunic.investigate import compare_arms
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        (root / "pricing.py").write_text(
            "def round_total(cart):\n    return cart\n", encoding="utf-8")
        (root / "dispatch.py").write_text(
            "from pricing import round_total\n\n"
            "STRATEGIES = {\"standard\": round_total}\n\n\n"
            "def apply(strategy, cart):\n    return STRATEGIES[strategy](cart)\n",
            encoding="utf-8")
        (root / "checkout.py").write_text(
            "from dispatch import apply\n\n\n"
            "def place_order(cart):\n    return apply(\"standard\", cart)\n",
            encoding="utf-8")
        noisy = "".join(
            "def n%d(x):\n    return len(x)\n\n\n" % i for i in range(12))
        (root / "noisy.py").write_text(noisy, encoding="utf-8")
        db = str(tmp / "memory.sqlite")
        memory = Memory(db, root)
        memory.ingest()
        memory.commit_proposal(
            "checkout.py::place_order", "pricing.py::round_total")
        memory.ingest()
        memory.close()
        result = compare_arms(db, str(root), top_n=3,
                              budget={"max_sites": 5, "max_commits": 3})
        self.assertIn("ranked", result)
        self.assertIn("baseline", result)
        self.assertEqual(result["budget"], {"max_sites": 5, "max_commits": 3})
        self.assertGreaterEqual(
            result["ranked"]["yield"], result["baseline"]["yield"])
        self.assertGreater(result["ranked"]["yield"], 0)
        for arm in ("ranked", "baseline"):
            self.assertLessEqual(
                result[arm]["actions"], 3 * 5 * 3)


if __name__ == "__main__":
    unittest.main()
