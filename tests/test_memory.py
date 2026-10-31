"""Agent-facing memory sessions sharing one database across processes."""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tunic.baselines import BM25, direct_callers
from tunic.compiler import validate_artifact
from tunic.memory import Memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self.db = str(self.tmp / "memory.sqlite")
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")

    def test_outcome_recorded_in_one_session_reads_back_in_another(self):
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")
        first = Memory(self.db, self.root)
        self.addCleanup(first.close)
        first.note("Investigating `core.py::calculate`.", document="notes/session.md")
        first.ingest()
        self.assertTrue(any(m["status"] == "syntactic_observation"
                            for m in first.related("core.py::calculate")["mentions"]["mentions"]))
        first.record_outcome("service.py::process", "core.py::calculate")
        first.ingest()
        first.close()
        second = Memory(self.db, self.root)
        self.addCleanup(second.close)
        related = second.related("core.py::calculate")
        self.assertTrue(any(c["relation"] == "depends_on" and c["authority"] == "agent-outcome"
                            for c in related["claims"]["claims"]))

    def test_recorded_outcome_survives_sessions_and_can_be_corrected(self):
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")
        first = Memory(self.db, self.root)
        self.addCleanup(first.close)
        first.record_outcome("service.py::process", "core.py::calculate",
                             authority="session-a")
        first.ingest()
        first.close()
        second = Memory(self.db, self.root)
        self.addCleanup(second.close)
        claims = [c for c in second.related("core.py::calculate")["claims"]["claims"]
                  if c["relation"] == "depends_on"]
        self.assertTrue(claims)
        self.assertTrue(all(c["authority"] == "session-a" for c in claims))
        found = second.answer("What else must change if calculate changes?")
        self.assertTrue(any(c["symbol"] == "service.py::process"
                            for c in found["candidates"]))
        second.record_outcome("service.py::process", "core.py::calculate",
                              rel="corrects", authority="session-b")
        second.ingest()
        second.close()
        third = Memory(self.db, self.root)
        self.addCleanup(third.close)
        timeline = third.timeline("core.py::calculate")
        self.assertTrue(timeline["corrected_by"])
        self.assertTrue(all(e.get("authority") in ("session-a", "session-b")
                            or e["kind"] != "claim"
                            for e in timeline["entries"]))

    def test_record_outcome_validates_and_defaults(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        with self.assertRaises(ValueError):
            memory.record_outcome("core.py::calculate", "core.py::calculate", rel="teleports")
        with self.assertRaises(ValueError):
            memory.record_outcome("not a key!", "core.py::calculate")
        block = memory.record_outcome("service.py::process", "core.py::calculate")
        self.assertIn('"rel": "depends_on"', block)
        self.assertRegex(block, r'"effective": "\d{4}-\d{2}-\d{2}"')

    def test_answer_resolves_task_without_exact_symbol(self):
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        result = memory.answer("Which functions break if calculate changes?")
        self.assertEqual(result["outcome"], "complete")
        self.assertEqual([c["symbol"] for c in result["candidates"]],
                         ["core.py::calculate", "service.py::process"])
        self.assertEqual(result["candidates"][0]["match"], "name")
        self.assertEqual(result["candidates"][1]["match"], "lexical")
        self.assertIn("service.py::process", [i["symbol"] for i in result["impacts"]])
        graph_only = memory.answer("Which functions break if calculate changes?",
                                   mode="graph")
        self.assertEqual([c["symbol"] for c in graph_only["candidates"]],
                         ["core.py::calculate"])
        with self.assertRaises(ValueError):
            memory.answer("Which functions break if calculate changes?", mode="semantic")
        empty = memory.answer("How do I bake bread?")
        self.assertEqual(empty["outcome"], "empty")
        self.assertEqual(empty["candidates"], [])

    def test_verified_learning_pipeline_propose_approve_correct(self):
        (self.root / "mid.py").write_bytes(b"def transform(value):\n    return value * 2\n")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.note("Compare `mid.py::transform` with `core.py::calculate`.", document="notes.md")
        memory.ingest()
        drafts = memory.draft("notes.md")
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        memory.record_outcome(draft["subject"], draft["object"], authority="agent-review")
        memory.ingest()
        asserted = [c for c in memory.claims("core.py::calculate")["claims"]
                    if c["status"] == "asserted_in_source"]
        self.assertEqual(len(asserted), 1)
        memory.record_outcome(draft["subject"], draft["object"], rel="contradicts",
                              authority="agent-correction")
        memory.ingest()
        claims = memory.claims("core.py::calculate")["claims"]
        by_relation = {}
        for claim in claims:
            by_relation.setdefault(claim["relation"], []).append(claim["status"])
        self.assertEqual(by_relation["depends_on"], ["asserted_in_source"])
        self.assertEqual(by_relation["contradicts"], ["asserted_in_source"])
        line = memory.timeline("core.py::calculate")
        self.assertEqual(line["conflicts"], [["mid.py::transform", "core.py::calculate"]])
        self.assertIn("mid.py::transform",
                      [i["symbol"] for i in memory.impact("core.py::calculate")["impacts"]])

    def test_memory_writes_stay_inside_the_workspace_root(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        outside = self.tmp / "outside.md"
        with self.assertRaises(ValueError):
            memory.note("escape attempt", document=str(outside))
        with self.assertRaises(ValueError):
            memory.note("escape attempt", document="../outside.md")
        with self.assertRaises(ValueError):
            memory.record_outcome("service.py::process", "core.py::calculate",
                                  document="/tmp/outside.md")
        with self.assertRaises(ValueError):
            memory.record_outcome("service.py::process", "core.py::calculate",
                                  document="sub/../../outside.md")
        self.assertFalse(outside.exists())
        self.assertEqual(list(self.root.rglob("*.md")), [])

    def test_note_and_draft_round_trip_through_api(self):
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        (self.root / "mid.py").write_bytes(b"def transform(value):\n    return value * 2\n")
        memory.note("Compare `mid.py::transform` with `core.py::calculate`.", document="notes.md")
        memory.ingest()
        drafts = memory.draft("notes.md")
        self.assertEqual([(d["subject"], d["object"]) for d in drafts],
                         [("mid.py::transform", "core.py::calculate")])

    def test_review_separates_observations_proposals_and_novel_drafts(self):
        (self.root / "core.py").write_bytes(b"def calculate(value):\n    return value + 1\n"
                                            b"\n\ndef validate(value):\n    return value > 0\n")
        (self.root / "mid.py").write_bytes(b"def transform(value):\n    return value * 2\n")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.note("Tracing `core.py::calculate` alongside `mid.py::transform`.",
                    document="notes/session.md")
        memory.note("# Ruling\n\n```tunic-claims\n"
                    '[{"rel": "depends_on", "subject": "core.py::calculate", '
                    '"object": "validate"}]\n```', document="notes/claims.md")
        memory.ingest()
        session = memory.review("notes/session.md")
        self.assertEqual(session["outcome"], "complete")
        self.assertEqual({e["symbol"] for e in session["entities"]},
                         {"core.py::calculate", "mid.py::transform"})
        self.assertEqual(session["proposals"], [])
        self.assertEqual(session["conflicts"], [])
        self.assertEqual(len(session["drafts"]), 1)
        draft = session["drafts"][0]
        self.assertEqual((draft["rel"], draft["subject"], draft["object"], draft["status"]),
                         ("depends_on", "core.py::calculate", "mid.py::transform", "novel"))
        self.assertEqual(len(draft["support"]), 2)
        claims = memory.review("notes/claims.md")
        self.assertEqual(claims["entities"], [])
        self.assertEqual(claims["asserted"], [])
        self.assertEqual(len(claims["proposals"]), 1)
        proposal = claims["proposals"][0]
        self.assertEqual(proposal["candidate"], "core.py::validate")
        self.assertEqual(proposal["status"], "proposed")
        with self.assertRaises(ValueError):
            memory.review("notes/missing.md")

    def test_review_flags_contradicted_pairs_as_conflicts(self):
        (self.root / "core.py").write_bytes(b"def calculate(value):\n    return value + 1\n")
        (self.root / "mid.py").write_bytes(b"def transform(value):\n    return value * 2\n")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.record_outcome("core.py::calculate", "mid.py::transform",
                              rel="contradicts", authority="reviewer",
                              document="notes/ruling.md")
        memory.note("Re-examine `core.py::calculate` and `mid.py::transform`.",
                    document="notes/session.md")
        memory.ingest()
        report = memory.review("notes/session.md")
        self.assertEqual(report["drafts"], [])
        self.assertEqual(len(report["conflicts"]), 1)
        conflict = report["conflicts"][0]
        self.assertEqual({conflict["subject"], conflict["object"]},
                         {"core.py::calculate", "mid.py::transform"})
        self.assertEqual(conflict["claim"]["relation"], "contradicts")

    def test_promote_records_novel_and_refuses_duplicates_and_conflicts(self):
        (self.root / "core.py").write_bytes(b"def calculate(value):\n    return value + 1\n")
        (self.root / "mid.py").write_bytes(b"def transform(value):\n    return value * 2\n")
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.note("Tracing `core.py::calculate` alongside `mid.py::transform`.",
                    document="notes/session.md")
        memory.ingest()
        block = memory.promote("core.py::calculate", "mid.py::transform",
                               authority="reviewer")
        self.assertIn("depends_on", block)
        memory.ingest()
        with self.assertRaises(ValueError):
            memory.promote("core.py::calculate", "mid.py::transform",
                           authority="reviewer")
        with self.assertRaises(ValueError):
            memory.promote("core.py::calculate", "transform", authority="reviewer")
        second = Memory(self.db, self.root)
        self.addCleanup(second.close)
        claims = second.claims("mid.py::transform")["claims"]
        self.assertTrue(any(c["relation"] == "depends_on" and c["authority"] == "reviewer"
                            for c in claims))
        memory.record_outcome("mid.py::transform", "core.py::calculate",
                              rel="contradicts", authority="reviewer",
                              document="notes/ruling.md")
        memory.ingest()
        with self.assertRaises(ValueError) as caught:
            memory.promote("mid.py::transform", "core.py::calculate",
                           authority="reviewer")
        self.assertIn("contradicts", str(caught.exception))


    def test_memory_repair_beats_static_and_lexical_baselines(self):
        shutil.copytree(Path(__file__).resolve().parents[1] / "benchmarks" / "w1" / "cases" / "dynamic_gap",
                        self.root / "gap", ignore=shutil.ignore_patterns("__pycache__"))
        memory = Memory(self.db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        target = "gap/dynamic.py::target"
        gold = {"gap/dynamic.py::indirect", "gap/dynamic.py::entry"}
        self.assertEqual(memory.impact(target)["impacts"], [])
        self.assertEqual(direct_callers(memory.store, target, memory.store.current()), [])
        ranking = BM25(str(self.root / "gap")).search("gap/dynamic.py::target")
        self.assertNotEqual({d["symbol"] for d in ranking}, gold)
        memory.record_outcome("gap/dynamic.py::indirect", target, authority="verified-outcome")
        memory.record_outcome("gap/dynamic.py::entry", "gap/dynamic.py::indirect",
                              authority="verified-outcome")
        memory.ingest()
        repaired = memory.impact(target)
        self.assertEqual({i["symbol"] for i in repaired["impacts"]}, gold)
        self.assertTrue(all(i["derivation"]["verification"] == "asserted_not_observed"
                            for i in repaired["impacts"]))
        self.assertTrue(validate_artifact(memory.store, repaired)["valid"])


class UnrelatedNoteTests(unittest.TestCase):
    """An unrelated note is discoverable but gains no authority."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        (self.root / "notes").mkdir(parents=True)
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")

    def _memory(self, name):
        db = str(self.tmp / (name + ".sqlite"))
        memory = Memory(db, self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        return memory

    def test_unrelated_note_changes_neither_impacts_nor_claims(self):
        question = "What else must change if calculate changes?"
        before = self._memory("before")
        impacts_before = before.impact("core.py::calculate")
        claims_before = before.claims("core.py::calculate")
        # Exact-key mention proves the note is ingested, but its tokens share
        # nothing with the question, so it must not influence this query.
        (self.root / "notes" / "deployment.md").write_text(
            "Zebra xylophone quark jigsaw Zephyr.\n\nSee service.py::process.\n",
            encoding="utf-8")
        after = self._memory("after")
        self.assertEqual(after.impact("core.py::calculate")["impacts"],
                         impacts_before["impacts"])
        self.assertEqual(after.claims("core.py::calculate")["claims"],
                         claims_before["claims"])
        found = after.answer(question)
        hits = [m for m in found["mentions"]
                if m["mentioner"] == "notes/deployment.md::document"]
        # Discovered as attributed text evidence ...
        self.assertTrue(hits)
        self.assertTrue(all(m["status"] == "syntactic_observation" for m in hits))
        # ... but granted no authority: no claims, no new impacts.
        self.assertEqual(found["claims"], [])
        self.assertEqual(found["impacts"], impacts_before["impacts"])

    def test_code_query_candidates_unchanged_by_unrelated_note(self):
        question = "What else must change if calculate changes?"
        before = self._memory("before").answer(question)
        (self.root / "notes" / "deployment.md").write_text(
            "Zebra xylophone quark jigsaw Zephyr.\n\nSee service.py::process.\n",
            encoding="utf-8")
        after = self._memory("after").answer(question)
        self.assertEqual([c["symbol"] for c in after["candidates"]],
                         [c["symbol"] for c in before["candidates"]])


class ReconcileTests(unittest.TestCase):
    """Deterministic acquisition gate: ADD / DUPLICATE / CONFLICT / ABSTAIN."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")
        self.memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def _assertion_count(self):
        _, view = self.memory.store.view()
        return len(view["assertions"])

    def test_clean_proposal_is_add_and_writes_nothing(self):
        before = self._assertion_count()
        action, reason = self.memory.reconcile_proposal(
            "service.py::process", "core.py::calculate")
        self.assertEqual(action, "add")
        self.assertTrue(reason)
        self.assertEqual(self._assertion_count(), before)

    def test_asserted_triple_is_duplicate_noop(self):
        self.memory.record_outcome("service.py::process", "core.py::calculate",
                                   authority="session-a")
        self.memory.ingest()
        before = self._assertion_count()
        action, reason = self.memory.reconcile_proposal(
            "service.py::process", "core.py::calculate")
        self.assertEqual(action, "duplicate")
        self.assertIn("already asserted", reason)
        self.assertEqual(self._assertion_count(), before)

    def test_contradicted_pair_is_conflict(self):
        self.memory.record_outcome("service.py::process", "core.py::calculate",
                                   authority="session-a")
        self.memory.record_outcome("core.py::calculate", "service.py::process",
                                   rel="contradicts", authority="session-b")
        self.memory.ingest()
        action, reason = self.memory.reconcile_proposal(
            "service.py::process", "core.py::calculate")
        self.assertEqual(action, "conflict")
        self.assertIn("contradicts", reason)

    def test_retracted_triple_is_conflict(self):
        self.memory.record_outcome("service.py::process", "core.py::calculate",
                                   authority="session-a")
        self.memory.record_outcome("service.py::process", "core.py::calculate",
                                   rel="retracts", retract="depends_on",
                                   authority="session-b")
        self.memory.ingest()
        action, reason = self.memory.reconcile_proposal(
            "service.py::process", "core.py::calculate")
        self.assertEqual(action, "conflict")
        self.assertIn("retraction", reason)

    def test_unknown_relation_bare_name_and_ghost_endpoints_are_invalid(self):
        action, _ = self.memory.reconcile_proposal(
            "service.py::process", "core.py::calculate", rel="blames")
        self.assertEqual(action, "invalid")
        action, _ = self.memory.reconcile_proposal("process", "core.py::calculate")
        self.assertEqual(action, "invalid")
        action, reason = self.memory.reconcile_proposal(
            "ghost.py::phantom", "core.py::calculate")
        self.assertEqual(action, "invalid")
        self.assertIn("ghost.py::phantom", reason)

    def test_existing_note_document_is_a_known_endpoint(self):
        (self.root / "notes").mkdir(exist_ok=True)
        (self.root / "notes" / "decisions.md").write_text(
            "We round at order time.\n", encoding="utf-8")
        self.memory.ingest()
        action, _ = self.memory.reconcile_proposal(
            "service.py::process", "notes/decisions.md::document",
            rel="decided_by")
        self.assertEqual(action, "add")

    def test_missing_note_document_is_invalid(self):
        action, reason = self.memory.reconcile_proposal(
            "service.py::process", "notes/missing.md::document",
            rel="decided_by")
        self.assertEqual(action, "invalid")
        self.assertIn("notes/missing.md::document", reason)


class CommitProposalTests(unittest.TestCase):
    """commit_proposal: reconcile first, promote only clean ADDs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        (self.root / "service.py").write_bytes(
            b"from core import calculate as compute\n\ndef process(value):\n    return compute(value)\n")
        self.memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def _assertion_count(self):
        _, view = self.memory.store.view()
        return len(view["assertions"])

    def test_clean_proposal_promotes(self):
        committed, action, reason = self.memory.commit_proposal(
            "service.py::process", "core.py::calculate")
        self.assertTrue(committed)
        self.assertEqual(action, "add")
        self.assertTrue(reason)

    def test_duplicate_is_noop_without_write(self):
        self.memory.commit_proposal("service.py::process", "core.py::calculate")
        self.memory.ingest()
        before = self._assertion_count()
        committed, action, reason = self.memory.commit_proposal(
            "service.py::process", "core.py::calculate")
        self.assertFalse(committed)
        self.assertEqual(action, "duplicate")
        self.assertEqual(self._assertion_count(), before)
        self.assertEqual(
            [e for e in self.memory.frontier_list() if e["failure"].startswith("reconcile_")], [])

    def test_invalid_and_conflict_refuse_and_file_frontier(self):
        committed, action, _ = self.memory.commit_proposal(
            "ghost.py::phantom", "core.py::calculate")
        self.assertEqual((committed, action), (False, "invalid"))
        self.memory.record_outcome("service.py::process", "core.py::calculate",
                                   authority="session-a")
        self.memory.record_outcome("core.py::calculate", "service.py::process",
                                   rel="contradicts", authority="session-b")
        self.memory.ingest()
        committed, action, reason = self.memory.commit_proposal(
            "service.py::process", "core.py::calculate")
        self.assertEqual((committed, action), (False, "conflict"))
        kinds = {e["kind"] for e in self.memory.frontier_list()}
        self.assertIn("unsupported", kinds)
        self.assertIn("contradictory", kinds)

    def test_candidates_surface_dispatch_proposals(self):
        (self.root / "pricing.py").write_bytes(b"def round_total(cart):\n    return cart\n")
        (self.root / "dispatch.py").write_bytes(
            b"from pricing import round_total\n\nSTRATEGIES = {\"standard\": round_total}\n\n\n"
            b"def apply(strategy, cart):\n    return STRATEGIES[strategy](cart)\n")
        (self.root / "checkout.py").write_bytes(
            b"from dispatch import apply\n\n\ndef place_order(cart):\n"
            b"    return apply(\"standard\", cart)\n")
        self.memory.ingest()
        found = self.memory.candidates()
        dispatch = [c for c in found["candidates"]
                    if c.get("via") == "registry_dispatch"]
        self.assertEqual(len(dispatch), 1)
        self.assertEqual(dispatch[0]["caller"], "checkout.py::place_order")
        self.assertEqual(dispatch[0]["callee"], "pricing.py::round_total")
        self.assertEqual(dispatch[0]["status"], "proposed")
        self.assertTrue(dispatch[0]["derivation"]["table"])


class FrontierTests(unittest.TestCase):
    """Frontier ledger v0: record, dedupe, transitions, abstention hook."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        self.memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def test_record_list_resolve_cycle(self):
        entry_id, created = self.memory.frontier_record(
            "unsupported", "resolve registry dispatch", "static_edge_unresolved",
            required_evidence=("registry assignment",))
        self.assertTrue(created)
        entries = self.memory.frontier_list(status="open")
        self.assertEqual([e["id"] for e in entries], [entry_id])
        self.assertEqual(entries[0]["promotion_policy"], "review_required")
        again_id, created = self.memory.frontier_record(
            "unsupported", "resolve registry dispatch", "static_edge_unresolved",
            required_evidence=("registry assignment",))
        self.assertEqual((again_id, created), (entry_id, False))
        self.assertEqual(self.memory.frontier_list()[0]["recurrence"], 2)
        resolved = self.memory.frontier_transition(
            entry_id, "resolved", resolution="dispatch candidate verified",
            resolved_by="reviewer")
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(self.memory.frontier_list(status="open"), [])
        with self.assertRaises(ValueError):
            self.memory.frontier_transition(entry_id, "dismissed", resolution="late")

    def test_abstention_files_unresolved_entry(self):
        self.assertEqual(self.memory.frontier_list(), [])
        found = self.memory.answer("Zebra xylophone quark jigsaw zephyr quandary?")
        self.assertEqual(found["outcome"], "empty")
        entries = self.memory.frontier_list(status="open")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "unresolved")
        self.assertEqual(entries[0]["failure"], "retrieval_abstention")
        self.memory.answer("Zebra xylophone quark jigsaw zephyr quandary?")
        self.assertEqual(self.memory.frontier_list()[0]["recurrence"], 2)


class CLITests(unittest.TestCase):
    """candidates/frontier read paths through the CLI."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        self.db = str(self.tmp / "memory.sqlite")

    def _run(self, *argv):
        import io
        from contextlib import redirect_stdout
        from tunic.__main__ import main
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--db", self.db, *argv])
        self.assertEqual(code, 0)
        return json.loads(buffer.getvalue())

    def test_frontier_lists_recorded_entry(self):
        memory = Memory(self.db, self.root)
        memory.ingest()
        entry_id, _ = memory.frontier_record(
            "unsupported", "resolve registry dispatch", "static_edge_unresolved")
        memory.close()
        result = self._run("frontier")
        self.assertEqual([e["id"] for e in result["entries"]], [entry_id])
        self.assertEqual(self._run("frontier", "--status", "resolved")["entries"], [])

    def test_candidates_lists_dispatch_proposals(self):
        (self.root / "pricing.py").write_bytes(b"def round_total(cart):\n    return cart\n")
        (self.root / "dispatch.py").write_bytes(
            b"from pricing import round_total\n\nSTRATEGIES = {\"standard\": round_total}\n\n\n"
            b"def apply(strategy, cart):\n    return STRATEGIES[strategy](cart)\n")
        (self.root / "checkout.py").write_bytes(
            b"from dispatch import apply\n\n\ndef place_order(cart):\n"
            b"    return apply(\"standard\", cart)\n")
        memory = Memory(self.db, self.root)
        memory.ingest()
        memory.close()
        result = self._run("candidates")
        dispatch = [c for c in result["candidates"] if c.get("via") == "registry_dispatch"]
        self.assertEqual(len(dispatch), 1)
        self.assertEqual(dispatch[0]["derivation"]["table"], "dispatch.py::STRATEGIES")


class FrontierIngestTests(unittest.TestCase):
    """Ingest-diagnostic auto-filing: extractor failures enter the ledger."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        (self.root / "dynamic.py").write_bytes(
            b"def run(handler, value):\n    return handler(value)\n")
        (self.root / "broken.py").write_bytes(b"def oops(:\n    pass\n")

    def test_ingest_files_failures_and_reingest_bumps(self):
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        result = memory.ingest()
        self.assertGreater(result["frontier_filed"], 0)
        entries = memory.frontier_list(status="open")
        kinds = {(e["kind"], e["failure"]) for e in entries}
        self.assertIn(("unresolved", "ingest_unresolved_call"), kinds)
        self.assertIn(("unsupported", "ingest_parse_error"), kinds)
        by_kind = {e["failure"]: e for e in entries}
        self.assertTrue(by_kind["ingest_unresolved_call"]["source_refs"])
        first = {e["id"]: e["recurrence"] for e in entries}
        again = memory.ingest()
        self.assertGreater(again["frontier_filed"], 0)
        second = memory.frontier_list(status="open")
        self.assertEqual({e["id"] for e in second}, set(first))
        self.assertTrue(all(e["recurrence"] == first[e["id"]] + 1 for e in second))

    def test_dispatch_diagnostic_maps_to_unsupported(self):
        (self.root / "pricing.py").write_bytes(b"def round_total(cart):\n    return cart\n")
        (self.root / "dispatch.py").write_bytes(
            b"from pricing import round_total\n\nSTRATEGIES = {\"standard\": round_total}\n")
        (self.root / "user.py").write_bytes(
            b"from dispatch import STRATEGIES\n\n\ndef dynamic(cart, name):\n"
            b"    return STRATEGIES[name](cart)\n")
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        entries = [e for e in memory.frontier_list(status="open")
                   if e["failure"] == "ingest_dispatch_unresolved"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "unsupported")
        self.assertIn("STRATEGIES", entries[0]["task"])


class FrontierReplayTests(unittest.TestCase):
    """Resolution requires proof: manual resolve needs evidence, replay re-checks."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")

    def _memory(self):
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        return memory

    def test_resolve_without_evidence_is_refused(self):
        memory = self._memory()
        memory.ingest()
        entry_id, _ = memory.frontier_record(
            "unsupported", "resolve dispatch", "static_edge_unresolved")
        with self.assertRaises(ValueError):
            memory.frontier_transition(entry_id, "resolved")
        self.assertEqual(memory.frontier_list(status="open")[0]["id"], entry_id)

    def test_abstention_replay_resolves_when_evidence_arrives(self):
        memory = self._memory()
        memory.ingest()
        question = "Zebra xylophone quark jigsaw zephyr quandary?"
        self.assertEqual(memory.answer(question)["outcome"], "empty")
        entry_id = memory.frontier_list(status="open")[0]["id"]
        self.assertEqual(memory.frontier_replay(entry_id)[0], "open")
        (self.root / "zebra.py").write_bytes(
            b"def xylophone(quark):\n    return quark\n")
        memory.ingest()
        status, verification = memory.frontier_replay(entry_id)
        self.assertEqual(status, "resolved")
        self.assertIn("zebra.py::xylophone", verification)
        self.assertEqual(memory.frontier_list(status="resolved")[0]["id"], entry_id)

    def test_dispatch_replay_tracks_candidate_then_commit(self):
        (self.root / "pricing.py").write_bytes(b"def round_total(cart):\n    return cart\n")
        (self.root / "dispatch.py").write_bytes(
            b"from pricing import round_total\n\nSTRATEGIES = {\"standard\": round_total}\n\n\n"
            b"def apply(strategy, cart):\n    return STRATEGIES[strategy](cart)\n")
        (self.root / "checkout.py").write_bytes(
            b"from dispatch import apply\n\n\ndef place_order(cart):\n"
            b"    return apply(\"standard\", cart)\n")
        memory = self._memory()
        memory.ingest()
        entries = [e for e in memory.frontier_list(status="open")
                   if e["failure"] == "ingest_dispatch_unresolved"
                   and e["target"] == "dispatch.py::STRATEGIES"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(memory.frontier_replay(entries[0]["id"])[0], "investigating")
        committed, action, _ = memory.commit_proposal(
            "checkout.py::place_order", "pricing.py::round_total")
        self.assertEqual((committed, action), (True, "add"))
        memory.ingest()
        status, verification = memory.frontier_replay(entries[0]["id"])
        self.assertEqual(status, "resolved")
        self.assertIn("checkout.py::place_order", verification)

    def test_parse_error_replay(self):
        (self.root / "broken.py").write_bytes(b"def oops(:\n    pass\n")
        memory = self._memory()
        memory.ingest()
        entries = [e for e in memory.frontier_list(status="open")
                   if e["failure"] == "ingest_parse_error"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(memory.frontier_replay(entries[0]["id"])[0], "open")
        (self.root / "broken.py").write_bytes(b"def oops():\n    pass\n")
        memory.ingest()
        status, verification = memory.frontier_replay(entries[0]["id"])
        self.assertEqual(status, "resolved")
        self.assertIn("symbols", verification)


class UnicodeWindowTests(unittest.TestCase):
    """Evidence windows must not split multibyte characters."""

    def test_window_around_mention_in_non_ascii_note(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        (root / "notes").mkdir(parents=True)
        (root / "core.py").write_bytes("def calculate(x):\n    return x + 1\n".encode("utf-8"))
        (root / "notes" / "ctx.md").write_bytes(
            ("Café naïve über — rounding notes. " * 12
             + "\nSee core.py::calculate.\n").encode("utf-8"))
        memory = Memory(str(tmp / "memory.sqlite"), root)
        self.addCleanup(memory.close)
        memory.ingest()
        import json as _json
        blob = _json.dumps(memory.related("core.py::calculate"))
        self.assertIn("core.py::calculate", blob)
        windows = []

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "window":
                        windows.append((node.get("excerpt"), value))
                    else:
                        walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(memory.related("core.py::calculate"))
        self.assertTrue(windows)
        for excerpt, window in windows:
            self.assertIsInstance(window, str)
            self.assertIn(excerpt, window)


if __name__ == "__main__":
    unittest.main()
