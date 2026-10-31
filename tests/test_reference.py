import json
import re
from pathlib import Path
import sqlite3
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

from tunic import GraphStore, compile_claims, compile_impact, compile_mentions, compile_related, compile_timeline, validate_artifact
from tunic.identity import identity


PRICING = "def round_total(cart):\n    return cart\n"
DISPATCH = ("from pricing import round_total\n\nSTRATEGIES = {\"standard\": round_total}\n\n\n"
            "def apply(strategy, cart):\n    return STRATEGIES[strategy](cart)\n")
PLACE_ORDER = ("from dispatch import apply\n\n\ndef place_order(cart):\n"
               "    return apply(\"standard\", cart)\n")


class DispatchTests(unittest.TestCase):
    """Registry/dict-dispatch indirection yields proposed candidates, never assertions."""

    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".tunic" / "test-runs"
        scratch.mkdir(parents=True, exist_ok=True)
        self.base = (scratch / ("run-" + uuid.uuid4().hex)).resolve()
        self.base.mkdir()
        self.root = self.base / "repo"
        self.root.mkdir()
        self.db = self.base / "graph.sqlite"
        self._stores = []

    def tearDown(self):
        for store in self._stores:
            store.close()
        allowed = (Path(__file__).resolve().parents[1] / ".tunic" / "test-runs").resolve()
        assert self.base.is_relative_to(allowed) and self.base != allowed
        shutil.rmtree(self.base)

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def ingest(self):
        store = GraphStore(self.db)
        self._stores.append(store)
        store.ingest(self.root)
        _, self.view = store.view()
        return store

    def candidates(self):
        return [a for a in self.view["assertions"]
                if a.get("proposition", {}).get("relation") == "depends_on"
                and a.get("proposition", {}).get("via") == "registry_dispatch"]

    def test_registry_indirection_produces_candidate_with_derivation(self):
        self.write("pricing.py", PRICING)
        self.write("dispatch.py", DISPATCH)
        self.write("checkout.py", PLACE_ORDER)
        store = self.ingest()
        found = self.candidates()
        self.assertEqual(len(found), 1)
        candidate = found[0]
        self.assertEqual(candidate["status"], "proposed")
        self.assertEqual(candidate["proposition"]["caller"], "checkout.py::place_order")
        self.assertEqual(candidate["proposition"]["callee"], "pricing.py::round_total")
        self.assertEqual(candidate["resolution"]["rule"], "registry_dispatch_candidate_v1")
        derivation = candidate.get("derivation", {})
        self.assertEqual(derivation.get("table"), "dispatch.py::STRATEGIES")
        self.assertEqual(derivation.get("key"), "standard")
        self.assertTrue(derivation.get("binding") and derivation.get("lookup")
                        and derivation.get("call"))
        asserted = [a for a in self.view["assertions"]
                    if a.get("proposition", {}).get("relation") == "depends_on"
                    and a.get("proposition", {}).get("caller") == "checkout.py::place_order"
                    and a.get("proposition", {}).get("callee") == "pricing.py::round_total"
                    and a.get("status") == "asserted_in_source"]
        self.assertEqual(asserted, [])
        impacts = compile_impact(store, "pricing.py::round_total")["impacts"]
        self.assertNotIn("checkout.py::place_order",
                         [i["symbol"] for i in impacts])

    def test_dynamic_dispatch_patterns_yield_no_candidates(self):
        self.write("pricing.py", PRICING)
        self.write("dispatch.py", DISPATCH + "\nTABLE2 = {}\n")
        self.write("checkout.py",
                   "from dispatch import apply, STRATEGIES\n"
                   "from pricing import round_total\n"
                   "import pricing\n\n\n"
                   "def dynamic(cart, name):\n"
                   "    return STRATEGIES[name](cart)\n\n\n"
                   "def reassigned(cart):\n"
                   "    STRATEGIES[\"x\"] = round_total\n"
                   "    return apply(\"x\", cart)\n\n\n"
                   "def reflected(cart, name):\n"
                   "    return getattr(pricing, name)(cart)\n\n\n"
                   "def aliased(cart):\n"
                   "    table = STRATEGIES\n"
                   "    return table[\"standard\"](cart)\n")
        self.ingest()
        self.assertEqual(self.candidates(), [])
        kinds = {(d.get("kind"), d.get("reason")) for d in self.view.get("diagnostics", [])}
        self.assertIn(("dispatch_unresolved", "dynamic_key"), kinds)
        self.assertIn(("dispatch_unresolved", "reassigned_table"), kinds)
        self.assertIn(("dispatch_unresolved", "getattr_dispatch"), kinds)
        self.assertIn(("dispatch_unresolved", "indirect_table"), kinds)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / ".tunic" / "test-runs"
        scratch.mkdir(parents=True, exist_ok=True)
        self.base = (scratch / ("run-" + uuid.uuid4().hex)).resolve()
        self.base.mkdir()
        self.root = self.base / "repo"
        self.root.mkdir()
        self.db = self.base / "graph.sqlite"
        self.write("core.py", "def calculate(x):\n    return x + 1\n")
        self.write("service.py", "from core import calculate as compute\n\ndef process(x):\n    return compute(x)\n")
        self.write("test_service.py", "import service as svc\n\ndef test_process():\n    assert svc.process(3) == 4\n")
        self.write("unrelated.py", 'def text():\n    return "calculate(3)"\n\ndef dynamic(callback):\n    return callback(3)\n')
        self.store = GraphStore(self.db)
        self._stores = []

    def tearDown(self):
        self.store.close()
        allowed = (Path(__file__).resolve().parents[1] / ".tunic" / "test-runs").resolve()
        assert self.base.is_relative_to(allowed) and self.base != allowed
        shutil.rmtree(self.base)

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))

    def artifact(self, **kwargs):
        return compile_impact(self.store, "core.py::calculate", **kwargs)

    def test_independent_expected_paths_and_utf8_evidence(self):
        self.write("unicode.py", 'from core import calculate\n\ndef caller():\n    label = "猫"; return calculate(2)\n')
        self.store.ingest(self.root)
        a = self.artifact()
        self.assertEqual([i["symbol"] for i in a["impacts"]],
                         ["service.py::process", "test_service.py::test_process", "unicode.py::caller"])
        paths = {i["symbol"]: i["derivation"]["premises"] for i in a["impacts"]}
        self.assertEqual(len(paths["service.py::process"]), 1)
        self.assertEqual(len(paths["test_service.py::test_process"]), 2)
        observations = {u["id"]:u["assertion"]["proposition"] for u in a["units"]}
        self.assertEqual([observations[p] for p in paths["test_service.py::test_process"]], [
            {"relation": "static_call", "caller": "test_service.py::test_process", "callee": "service.py::process"},
            {"relation": "static_call", "caller": "service.py::process", "callee": "core.py::calculate"}])
        self.assertTrue(next(i for i in a["impacts"] if i["symbol"].startswith("test_"))["is_test"])
        for unit in a["units"]:
            e = unit["evidence"]
            raw = (self.root/e["path"]).read_bytes()
            self.assertEqual(raw[e["start"]:e["end"]].decode("utf-8"), e["excerpt"])
        self.assertIn("calculate(2)", [u["evidence"]["excerpt"] for u in a["units"]])
        alias = next(u for u in a["units"] if u["evidence"]["excerpt"] == "compute(x)")
        self.assertEqual(alias["resolution_support"][0]["excerpt"], "from core import calculate as compute")
        self.assertEqual(alias["resolution_support"][1]["path"], "core.py")
        self.assertTrue(any(d["kind"] == "unresolved_call" for d in a["selection_receipt"]["diagnostics"]))
        self.assertTrue(validate_artifact(self.store, a)["valid"])

    def test_determinism_and_persistence(self):
        first = self.store.ingest(self.root)
        artifact = self.artifact()
        self.assertEqual(self.store.ingest(self.root), first)
        self.assertEqual(self.artifact(), artifact)
        self.store.close()
        self.store = GraphStore(self.db)
        self.assertEqual(self.artifact(), artifact)

    def test_updates_deletions_new_callers_match_fresh_rebuild(self):
        self.store.ingest(self.root)
        old = self.artifact()
        changes = [lambda: self.write("core.py", "def calculate(x):\n    return x + 7\n"),
                   lambda: self.write("new.py", "from core import calculate\ndef fresh():\n    return calculate(8)\n"),
                   lambda: (self.root/"service.py").unlink(),
                   lambda: self.write("unrelated.py", "def unrelated():\n    return 0\n")]
        for number, change in enumerate(changes):
            change()
            self.store.ingest(self.root)
            with GraphStore(self.base/f"fresh{number}.sqlite") as fresh:
                fresh.ingest(self.root)
                self.assertEqual(self.artifact(), compile_impact(fresh, "core.py::calculate"))
            expected = (["service.py::process", "test_service.py::test_process"] if number == 0 else
                        ["new.py::fresh", "service.py::process", "test_service.py::test_process"] if number == 1 else
                        ["new.py::fresh"])
            self.assertEqual([i["symbol"] for i in self.artifact()["impacts"]], expected)
            self.assertIn("return x + 7", self.artifact()["target"]["evidence"]["excerpt"])
        self.assertEqual([i["symbol"] for i in self.artifact()["impacts"]], ["new.py::fresh"])
        self.assertFalse(validate_artifact(self.store, old, expected_snapshot=self.store.current())["valid"])
        self.assertTrue(validate_artifact(self.store, old)["valid"])

    def test_tamper_and_rehashed_forgery_rejected(self):
        self.store.ingest(self.root)
        artifact = self.artifact()
        artifact["units"][0]["evidence"]["excerpt"] = "fabricated()"
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])
        artifact["id"] = identity("context", {k:v for k,v in artifact.items() if k != "id"})
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])
        self.assertFalse(validate_artifact(self.store, {"id": "bad"})["valid"])

    def test_source_corruption_rejected(self):
        self.store.ingest(self.root)
        artifact = self.artifact()
        with self.store.connection:
            self.store.connection.execute("UPDATE sources SET data=? WHERE path='service.py'", (b"corrupted",))
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])

    def test_target_integrity_even_without_callers(self):
        self.store.ingest(self.root)
        artifact = compile_impact(self.store, "unrelated.py::text")
        self.assertEqual(artifact["impacts"], [])
        with self.store.connection:
            self.store.connection.execute("UPDATE sources SET data=? WHERE path='unrelated.py'", (b"bad",))
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])

    def test_graph_row_corruption_rejected(self):
        self.store.ingest(self.root)
        artifact = self.artifact()
        with self.store.connection:
            self.store.connection.execute("DELETE FROM snapshot_assertions")
        self.assertFalse(validate_artifact(self.store, artifact)["valid"])

    def test_reader_sees_old_snapshot_until_publish_commits(self):
        before = self.store.ingest(self.root)["snapshot"]
        self.write("new.py", "from core import calculate\ndef new():\n    return calculate(4)\n")
        with GraphStore(self.db) as reader:
            observations = []
            def during_publication(sql):
                if sql.startswith("INSERT OR REPLACE INTO metadata"):
                    observations.append(reader.current())
            self.store.connection.set_trace_callback(during_publication)
            after = self.store.ingest(self.root)["snapshot"]
            self.store.connection.set_trace_callback(None)
            self.assertEqual(observations, [before])
            self.assertEqual(reader.current(), after)
            self.assertNotEqual(before, after)

    def test_crlf_bom_and_multiline_call_coordinates(self):
        data = b'\xef\xbb\xbffrom core import calculate\r\ndef caller():\r\n    return calculate(\r\n        2\r\n    )\r\n'
        (self.root/"bom.py").write_bytes(data)
        self.store.ingest(self.root)
        unit = next(u for u in self.artifact()["units"] if u["evidence"]["path"] == "bom.py")
        self.assertEqual(unit["evidence"]["excerpt"], "calculate(\r\n        2\r\n    )")

    def test_no_source_execution_and_encoding_gap(self):
        self.write("hostile.py", 'raise RuntimeError("do not execute")\ndef innocent():\n    return 1\n')
        (self.root/"legacy.py").write_bytes(b'# coding: latin-1\n# \xe9\ndef legacy():\n    return 1\n')
        receipt = self.store.ingest(self.root)
        self.assertTrue(any(d["kind"] == "unsupported_encoding" for d in receipt["diagnostics"]))
        self.assertTrue(validate_artifact(self.store, self.artifact())["valid"])

    def test_failed_publication_rolls_back_and_restart_keeps_previous(self):
        before = self.store.ingest(self.root)["snapshot"]
        self.write("new.py", "from core import calculate\ndef new():\n    return calculate(1)\n")
        self.store.connection.execute("""CREATE TEMP TRIGGER fail_publish BEFORE INSERT ON snapshot_assertions
            BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.ingest(self.root)
        self.assertEqual(self.store.current(), before)
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)
        self.store.close()
        self.store = GraphStore(self.db)
        self.assertEqual(self.store.current(), before)
        self.assertEqual(len(self.artifact()["impacts"]), 2)

    def test_read_failure_does_not_publish(self):
        before = self.store.ingest(self.root)
        with patch("tunic.extract.Path.read_bytes", side_effect=OSError("unreadable")):
            with self.assertRaises(OSError):
                self.store.ingest(self.root)
        self.assertEqual(self.store.current(), before["snapshot"])

    def test_methods_resolve_self_and_constructor_calls(self):
        self.write("worker.py", "from core import calculate\n\nclass Worker:\n    def run(self):\n        return calculate(2)\n\ndef entry():\n    return Worker().run()\n")
        self.store.ingest(self.root)
        a = self.artifact()
        self.assertEqual([i["symbol"] for i in a["impacts"]],
                         ["service.py::process", "test_service.py::test_process",
                          "worker.py::Worker.run", "worker.py::entry"])
        observations = {u["id"]: u["assertion"]["proposition"] for u in a["units"]}
        paths = {i["symbol"]: i["derivation"]["premises"] for i in a["impacts"]}
        self.assertEqual([observations[p] for p in paths["worker.py::entry"]], [
            {"relation": "static_call", "caller": "worker.py::entry", "callee": "worker.py::Worker.run"},
            {"relation": "static_call", "caller": "worker.py::Worker.run", "callee": "core.py::calculate"}])
        self.assertTrue(validate_artifact(self.store, a)["valid"])

    def test_self_calls_resolve_while_shadowed_names_stay_unresolved(self):
        self.write("tracker.py", "class Tracker:\n    def score(self):\n        return self.total()\n    def total(self):\n        return 1\n    def shadowed(self, total):\n        return total(2)\n")
        self.store.ingest(self.root)
        inner = compile_impact(self.store, "tracker.py::Tracker.total")["impacts"]
        self.assertEqual([i["symbol"] for i in inner], ["tracker.py::Tracker.score"])
        self.assertTrue(all(i["derivation"]["rule"] == "reverse_static_reachability_v1"
                            for i in inner))
        receipt = self.store.ingest(self.root)
        kinds = {(d.get("caller"), d.get("expression")) for d in receipt["diagnostics"]
                 if d["kind"] == "unresolved_call"}
        self.assertNotIn(("tracker.py::Tracker.score", "self.total"), kinds)
        self.assertIn(("tracker.py::Tracker.shadowed", "total"), kinds)

    def test_document_mentions_link_prose_to_code_symbols(self):
        self.write("notes.md", "# Notes\n\n`core.py::calculate` is used by service.\n\nSee unrelated.py::dynamic for contrast.\n")
        self.store.ingest(self.root)
        mentions = compile_mentions(self.store, "core.py::calculate")
        self.assertEqual(len(mentions["mentions"]), 1)
        evidence = mentions["mentions"][0]["evidence"]
        self.assertEqual(evidence["path"], "notes.md")
        raw = (self.root / evidence["path"]).read_bytes()
        self.assertEqual(raw[evidence["start"]:evidence["end"]].decode("utf-8"), evidence["excerpt"])
        self.assertIn("core.py::calculate", evidence["excerpt"])
        # Mentions are not callers: impact results are unchanged.
        self.assertEqual([i["symbol"] for i in self.artifact()["impacts"]],
                         ["service.py::process", "test_service.py::test_process"])
        self.assertTrue(validate_artifact(self.store, self.artifact())["valid"])

    def test_doc_claims_traverse_asserted_dependencies(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Design\n\n```tunic-claims\n"
                 + '[{"rel": "depends_on", "subject": "mid.py::transform", '
                 + '"object": "core.py::calculate"}]\n```\n')
        self.store.ingest(self.root)
        a = compile_impact(self.store, "core.py::calculate")
        paths = {i["symbol"]: i["derivation"] for i in a["impacts"]}
        self.assertIn("mid.py::transform", paths)
        self.assertEqual(paths["mid.py::transform"]["rule"], "asserted_dependency_v1")
        self.assertEqual(paths["mid.py::transform"]["verification"], "asserted_not_observed")
        self.assertTrue(validate_artifact(self.store, a)["valid"])
        claims = compile_claims(self.store, "core.py::calculate")
        self.assertEqual(len(claims["claims"]), 1)
        self.assertEqual(claims["claims"][0]["relation"], "depends_on")

    def test_ambiguous_references_become_proposals_not_merges(self):
        self.write("worker.py", "def process(value):\n    return value\n")
        self.write("notes.md", "# Design\n\n```tunic-claims\n"
                 + '[{"rel": "depends_on", "subject": "process", '
                 + '"object": "core.py::calculate"}]\n```\n')
        receipt = self.store.ingest(self.root)
        self.assertTrue(any(d["kind"] == "ambiguous_reference" for d in receipt["diagnostics"]))
        a = compile_impact(self.store, "core.py::calculate")
        self.assertNotIn("worker.py::process",
                         [i["symbol"] for i in a["impacts"]])
        claims = compile_claims(self.store, "core.py::calculate")
        proposals = [c for c in claims["claims"] if c["status"] == "proposed_ambiguous"]
        self.assertEqual(len(proposals), 2)
        self.assertEqual({c["candidate"] for c in proposals},
                         {"service.py::process", "worker.py::process"})

    def test_contradicts_recorded_not_traversed(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Design\n\n```tunic-claims\n"
                 + '[{"rel": "contradicts", "subject": "mid.py::transform", '
                 + '"object": "core.py::calculate"}]\n```\n')
        self.store.ingest(self.root)
        a = compile_impact(self.store, "core.py::calculate")
        self.assertNotIn("mid.py::transform", [i["symbol"] for i in a["impacts"]])
        claims = compile_claims(self.store, "core.py::calculate")
        self.assertEqual([(c["relation"], c["status"]) for c in claims["claims"]],
                         [("contradicts", "asserted_in_source")])

    def test_bare_names_propose_with_verification_gate(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Design\n\ntransform the input before storing.\n\n`mid.py::transform` is the entry point.\n")
        self.store.ingest(self.root)
        mentions = compile_mentions(self.store, "mid.py::transform")
        by_status = {}
        for entry in mentions["mentions"]:
            by_status.setdefault(entry["status"], []).append(entry)
        self.assertEqual(len(by_status["syntactic_observation"]), 1)
        self.assertEqual(len(by_status["proposed"]), 1)
        proposed = by_status["proposed"][0]
        self.assertEqual(proposed["candidate"], "mid.py::transform")
        self.assertTrue(proposed["corroborated"])
        self.assertEqual(compile_impact(self.store, "mid.py::transform")["impacts"], [])

    def test_ambiguous_bare_names_become_reviewable_candidates(self):
        self.write("worker.py", "def process(value):\n    return value\n")
        self.write("notes.md", "# Design\n\nThe process needs review.\n")
        self.store.ingest(self.root)
        from tunic.compiler import compile_review
        mentions = compile_mentions(self.store, "service.py::process")
        self.assertEqual(mentions["mentions"], [])
        candidates = compile_review(self.store, "notes.md")["candidates"]
        self.assertEqual({c["callee"] for c in candidates},
                         {"service.py::process", "worker.py::process"})
        self.assertNotIn("worker.py::process",
                         [i["symbol"] for i in compile_impact(self.store, "core.py::calculate")["impacts"]])

    def test_doc_to_doc_claims_record_without_traversal(self):
        self.write("notes.md", "# Old\n")
        self.write("notes_v2.md", "# New\n\n```tunic-claims\n"
                 + '[{"rel": "supersedes", "subject": "notes_v2.md::document", '
                 + '"object": "notes.md::document"}]\n```\n')
        self.store.ingest(self.root)
        claims = compile_claims(self.store, "notes.md::document")
        self.assertEqual([(c["relation"], c["status"]) for c in claims["claims"]],
                         [("supersedes", "asserted_in_source")])
        self.assertEqual([i["symbol"] for i in compile_impact(self.store, "core.py::calculate")["impacts"]],
                         ["service.py::process", "test_service.py::test_process"])

    def test_related_combines_code_and_doc_evidence(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Design\n\nUse transform for shaping.\n\nThe calculate step is cheap.\n\n```tunic-claims\n"
                 + '[{"rel": "depends_on", "subject": "mid.py::transform", '
                 + '"object": "core.py::calculate"}]\n```\n')
        self.store.ingest(self.root)
        related = compile_related(self.store, "core.py::calculate")
        self.assertEqual(related["outcome"], "complete")
        self.assertIn("mid.py::transform",
                      [i["symbol"] for i in related["impact"]["impacts"]])
        self.assertEqual(len(related["mentions"]["mentions"]), 1)
        mention = related["mentions"]["mentions"][0]
        self.assertEqual(mention["candidate"], "core.py::calculate")
        self.assertEqual(mention["status"], "proposed")
        self.assertTrue(mention["corroborated"])
        self.assertEqual([(c["relation"], c["status"]) for c in related["claims"]["claims"]],
                         [("depends_on", "asserted_in_source")])

    def test_timeline_filters_claims_by_valid_time(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Decisions\n\n```tunic-claims\n"
                 + '[{"rel": "decided_by", "subject": "mid.py::transform", "object": "core.py::calculate", '
                 + '"effective": "2026-01-15", "authority": "arch-board"}, '
                 + '{"rel": "depends_on", "subject": "mid.py::transform", "object": "core.py::calculate", '
                 + '"effective": "2026-09-01"}]\n```\n')
        self.store.ingest(self.root)
        full = compile_timeline(self.store, "mid.py::transform")
        self.assertEqual([(e["relation"], e["effective"]) for e in full["entries"]],
                         [("decided_by", "2026-01-15"), ("depends_on", "2026-09-01")])
        self.assertEqual(full["entries"][0]["authority"], "arch-board")
        mid = compile_timeline(self.store, "mid.py::transform", at="2026-06-01")
        self.assertEqual([(e["relation"], e["effective"]) for e in mid["entries"]],
                         [("decided_by", "2026-01-15")])
        self.assertEqual(mid["as_of"], "2026-06-01")
        with self.assertRaises(ValueError):
            compile_timeline(self.store, "mid.py::transform", at="yesterday")

    def test_supersession_marks_entities_stale_without_traversal(self):
        self.write("old.py", "def run(value):\n    return value\n")
        self.write("new.py", "def run(value):\n    return value + 1\n")
        self.write("notes.md", "# Migration\n\n```tunic-claims\n"
                 + '[{"rel": "supersedes", "subject": "new.py::run", "object": "old.py::run", '
                 + '"effective": "2026-05-01"}]\n```\n')
        self.store.ingest(self.root)
        current = compile_timeline(self.store, "old.py::run", at="2026-12-01")
        self.assertEqual([(s["by"], s["effective"]) for s in current["superseded_by"]],
                         [("new.py::run", "2026-05-01")])
        earlier = compile_timeline(self.store, "old.py::run", at="2026-01-01")
        self.assertEqual(earlier["superseded_by"], [])
        self.assertNotIn("new.py::run",
                         [i["symbol"] for i in compile_impact(self.store, "old.py::run")["impacts"]])

    def test_contradictions_surface_with_both_sides_preserved(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Debate\n\n```tunic-claims\n"
                 + '[{"rel": "contradicts", "subject": "mid.py::transform", "object": "core.py::calculate", '
                 + '"effective": "2026-03-01"}]\n```\n')
        self.store.ingest(self.root)
        line = compile_timeline(self.store, "core.py::calculate")
        self.assertEqual(line["conflicts"], [["mid.py::transform", "core.py::calculate"]])
        self.assertNotIn("mid.py::transform",
                         [i["symbol"] for i in compile_impact(self.store, "core.py::calculate")["impacts"]])

    def test_invalid_effective_keeps_claim_with_diagnostic(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Design\n\n```tunic-claims\n"
                 + '[{"rel": "depends_on", "subject": "mid.py::transform", "object": "core.py::calculate", '
                 + '"effective": "someday"}]\n```\n')
        receipt = self.store.ingest(self.root)
        self.assertTrue(any(d["kind"] == "invalid_effective" for d in receipt["diagnostics"]))
        claims = compile_claims(self.store, "core.py::calculate")
        self.assertEqual(len(claims["claims"]), 1)
        self.assertIsNone(claims["claims"][0]["effective"])

    def test_recorded_outcome_changes_later_retrieval(self):
        self.write("notes.md", "# Session 2026-10-30\n\nInvestigating `core.py::calculate` impact. The process helper needs review.\n")
        self.store.ingest(self.root)
        first = compile_related(self.store, "core.py::calculate")
        self.assertEqual(first["claims"]["claims"], [])
        self.assertTrue(any(m["status"] == "syntactic_observation" for m in first["mentions"]["mentions"]))
        self.write("outcome.md", "# Outcome 2026-10-30\n\nVerified: changing calculate requires updating the service layer.\n\n```tunic-claims\n"
                 + '[{"rel": "depends_on", "subject": "service.py::process", "object": "core.py::calculate", '
                 + '"effective": "2026-10-30", "authority": "verified-outcome"}]\n```\n')
        self.store.ingest(self.root)
        second = compile_related(self.store, "core.py::calculate")
        self.assertNotEqual(first["snapshot"], second["snapshot"])
        depends = [c for c in second["claims"]["claims"]
                   if c["relation"] == "depends_on" and c["status"] == "asserted_in_source"]
        self.assertEqual(len(depends), 1)
        self.assertEqual(depends[0]["authority"], "verified-outcome")
        self.assertEqual(depends[0]["effective"], "2026-10-30")
        line = compile_timeline(self.store, "core.py::calculate", at="2026-12-01")
        self.assertTrue(any(e["relation"] == "depends_on" for e in line["entries"]))

    def test_note_appends_timestamped_session_record(self):
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "tunic", *args],
                                  capture_output=True, text=True, check=False)
        first = cli("note", str(self.root / "session.md"), "--text", "Investigating `core.py::calculate`.")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = cli("note", str(self.root / "session.md"), "--text", "Confirmed the service path.")
        self.assertEqual(second.returncode, 0, second.stderr)
        body = (self.root / "session.md").read_text(encoding="utf-8")
        self.assertEqual(body.count("core.py::calculate"), 1)
        self.assertEqual(len(re.findall(r"^## \d{4}-\d{2}-\d{2} ", body, re.MULTILINE)), 2)
        self.store.ingest(self.root)
        mentions = compile_mentions(self.store, "core.py::calculate")
        self.assertTrue(any(m["status"] == "syntactic_observation" for m in mentions["mentions"]))

    def test_draft_proposes_only_unlinked_co_mentions(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Design\n\nCompare `mid.py::transform` with `core.py::calculate`.\n")
        self.store.ingest(self.root)
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "tunic", *args],
                                  capture_output=True, text=True, check=False)
        proc = cli("--db", str(self.db), "draft", "notes.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proposals = json.loads(proc.stdout)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["subject"], "mid.py::transform")
        self.assertEqual(proposals[0]["object"], "core.py::calculate")
        self.assertIn("direction", proposals[0]["note"])

    def test_recorded_outcome_repairs_dynamic_gap(self):
        shutil.copytree(Path(__file__).resolve().parents[1] / "benchmarks" / "w1" / "cases" / "dynamic_gap",
                        self.root / "gap", ignore=shutil.ignore_patterns("__pycache__"))
        self.store.ingest(self.root)
        before = compile_impact(self.store, "gap/dynamic.py::target")
        self.assertEqual(before["impacts"], [])
        self.write("gap/outcome.md", "# Verified\n\n```tunic-claims\n"
                 + '[{"rel": "depends_on", "subject": "gap/dynamic.py::indirect", "object": "gap/dynamic.py::target", "authority": "verified-outcome"}, '
                 + '{"rel": "depends_on", "subject": "gap/dynamic.py::entry", "object": "gap/dynamic.py::indirect", "authority": "verified-outcome"}]\n```\n')
        self.store.ingest(self.root)
        after = compile_impact(self.store, "gap/dynamic.py::target")
        self.assertEqual([i["symbol"] for i in after["impacts"]],
                         ["gap/dynamic.py::entry", "gap/dynamic.py::indirect"])
        self.assertTrue(all(i["derivation"]["rule"] == "asserted_dependency_v1" for i in after["impacts"]))
        self.assertTrue(validate_artifact(self.store, after)["valid"])

    def test_evidence_only_policy_ignores_asserted_edges(self):
        self.write("mid.py", "def transform(value):\n    return value * 2\n")
        self.write("notes.md", "# Design\n\n```tunic-claims\n"
                 + '[{"rel": "depends_on", "subject": "mid.py::transform", "object": "core.py::calculate"}]\n```\n')
        self.store.ingest(self.root)
        strict = compile_impact(self.store, "core.py::calculate", policy="evidence_only")
        self.assertNotIn("mid.py::transform", [i["symbol"] for i in strict["impacts"]])
        self.assertIn("service.py::process", [i["symbol"] for i in strict["impacts"]])
        self.assertEqual(strict["policy"], "evidence_only")
        open_view = compile_impact(self.store, "core.py::calculate", policy="transparent")
        self.assertIn("mid.py::transform", [i["symbol"] for i in open_view["impacts"]])
        with self.assertRaises(ValueError):
            compile_impact(self.store, "core.py::calculate", policy="trust-me")

    def test_current_policy_excludes_superseded_nodes(self):
        self.write("old.py", "from core import calculate\n\ndef run(value):\n    return calculate(value)\n")
        self.write("new.py", "from core import calculate\n\ndef run(value):\n    return calculate(value) + 1\n")
        self.write("notes.md", "# Migration\n\n```tunic-claims\n"
                 + '[{"rel": "supersedes", "subject": "new.py::run", "object": "old.py::run", '
                 + '"effective": "2026-01-01"}]\n```\n')
        self.store.ingest(self.root)
        current = compile_impact(self.store, "core.py::calculate", policy="current")
        self.assertNotIn("old.py::run", [i["symbol"] for i in current["impacts"]])
        self.assertIn("new.py::run", [i["symbol"] for i in current["impacts"]])
        self.assertIn("service.py::process", [i["symbol"] for i in current["impacts"]])
        self.assertEqual(current["policy"], "current")
        open_view = compile_impact(self.store, "core.py::calculate", policy="transparent")
        self.assertIn("old.py::run", [i["symbol"] for i in open_view["impacts"]])

    def test_shadowing_dynamic_scopes_and_parse_errors(self):
        self.write("shadow.py", "from core import calculate\ndef wrong(calculate):\n    return calculate(9)\n")
        self.write("broken.py", "def bad(:\n")
        self.write("nested.py", "from core import calculate\ndef outer():\n    def inner():\n        return calculate(2)\n    return inner()\n")
        self.write("rebound.py", "from core import calculate\ncalculate = None\ndef wrong():\n    return calculate(3)\n")
        receipt = self.store.ingest(self.root)
        self.assertEqual([i["symbol"] for i in self.artifact()["impacts"]],
                         ["service.py::process", "test_service.py::test_process"])
        kinds = {d["kind"] for d in receipt["diagnostics"]}
        self.assertTrue({"unresolved_call", "parse_error", "nested_scope"} <= kinds)

    def test_relative_imports_and_duplicate_module_names(self):
        self.write("pkg/__init__.py", "")
        self.write("pkg/consumer.py", "from .leaf import operation as op\ndef use():\n    return op()\n")
        self.write("pkg/leaf.py", "def operation():\n    return 1\n")
        self.store.ingest(self.root)
        a = compile_impact(self.store, "pkg/leaf.py::operation")
        self.assertEqual([i["symbol"] for i in a["impacts"]], ["pkg/consumer.py::use"])
        self.write("pkg/leaf/__init__.py", "def operation():\n    return 2\n")
        self.store.ingest(self.root)
        self.assertEqual(compile_impact(self.store, "pkg/leaf.py::operation")["impacts"], [])

    def test_global_rebinding_blocks_static_resolution(self):
        self.write("mutable.py", "from core import calculate\ndef replace():\n    global calculate\n    calculate = None\ndef caller():\n    return calculate(2)\n")
        self.store.ingest(self.root)
        self.assertNotIn("mutable.py::caller", [i["symbol"] for i in self.artifact()["impacts"]])

    def test_cycles_and_bounds(self):
        self.write("cycle.py", "def a():\n    return b()\ndef b():\n    return a()\n")
        self.store.ingest(self.root)
        self.assertTrue(compile_impact(self.store, "cycle.py::a")["selection_receipt"]["cycles_observed"])
        bounded = self.artifact(max_depth=1)
        self.assertEqual([i["symbol"] for i in bounded["impacts"]], ["service.py::process"])
        self.assertEqual(bounded["selection_receipt"]["bounds_hit"], ["max_depth"])
        self.assertEqual(self.artifact(max_nodes=1)["selection_receipt"]["bounds_hit"], ["max_nodes"])
        with self.assertRaises(ValueError):
            self.artifact(max_depth=0)

    def test_empty_snapshot_unknown_symbol_and_wrong_repository(self):
        with self.assertRaises(ValueError):
            self.artifact()
        self.store.ingest(self.root)
        with self.assertRaises(ValueError):
            compile_impact(self.store, "missing.py::f")
        other = self.base/"other"
        other.mkdir()
        with self.assertRaises(ValueError):
            self.store.ingest(other)

    def test_cli_across_processes(self):
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "tunic", "--db", str(self.db), *args],
                                  capture_output=True, text=True, check=False)
        ingest = cli("ingest", str(self.root))
        self.assertEqual(ingest.returncode, 0, ingest.stderr)
        query = cli("impact", "core.py::calculate")
        self.assertEqual(query.returncode, 0, query.stderr)
        output = self.base/"artifact.json"
        output.write_text(query.stdout, encoding="utf-8")
        validation = cli("validate", str(output))
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertTrue(json.loads(validation.stdout)["valid"])
        output.write_text("{}", encoding="utf-8")
        self.assertEqual(cli("validate", str(output)).returncode, 1)
        self.assertEqual(cli("impact", "missing").returncode, 2)


if __name__ == "__main__":
    unittest.main()
