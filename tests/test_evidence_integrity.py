"""Cross-feature evidence integrity audit (Sprint A).

Pins the trust boundary between proven relationships and investigation
leads across every retrieval route: suppression must hold in PPR and
compilers, unreviewed config/schema edges must never traverse impact,
textual references must respect scope and snapshots, MMR must reorder
without dropping, and every rendered candidate must carry its origin.
"""
import tempfile
import unittest
from pathlib import Path

from tunic.lifecycle import suppress
from tunic.memory import Memory

FILES = {
    "a.py": ("def target():\n"
             "    return 1\n"
             "\n\n"
             "def caller():\n"
             "    return target()\n"
             "\n\n"
             "def unrelated():\n"
             "    return 0\n"),
    "site/page.astro": "---\nimport { target } from '../a';\n---\n<p>{target()}</p>\n",
    "exc/skip.py": "def sneak():\n    return target()\n",
    "exc/note.astro": "<p>{target}</p>\n",
    "cfg.json": '{"server": {"host": "db.local"}, "$ref": "cfg.json#/server/host"}',
    "s.sql": ("CREATE TABLE orgs (id INTEGER PRIMARY KEY);\n"
              "CREATE TABLE users (org_id INT REFERENCES orgs(id));\n"),
    "notes.md": ("# Field notes\n\nThe host field matters here.\n"
                 "See `cfg.json::server.host` for the canonical value.\n"),
}


def _ingest(tmp, exclude=()):
    work = Path(tmp) / "ws"
    for name, text in FILES.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "audit.sqlite"), str(work))
    receipt = memory.ingest(exclude=exclude)
    return memory, receipt


def _static_id(view, caller, callee):
    ids = [a["id"] for a in view["assertions"]
           if a["proposition"].get("relation") == "static_call"
           and a["proposition"].get("caller") == caller
           and a["proposition"].get("callee") == callee]
    assert len(ids) == 1, (caller, callee, ids)
    return ids


class EvidenceIntegrityTest(unittest.TestCase):
    def test_mechanical_edges_labeled_syntactic_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                impact = memory.related("a.py::target")["impact"]
                callers = [e for e in impact["impacts"]
                           if e["symbol"] == "a.py::caller"]
                self.assertEqual(len(callers), 1)
                self.assertEqual(callers[0]["derivation"]["verification"],
                                 "syntactic_path_only")
            finally:
                memory.close()

    def test_suppressed_caller_absent_from_impact_and_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                _, view = memory.store.view()
                ids = _static_id(view, "a.py::caller", "a.py::target")
                suppress(memory, ids, policy="governance-review",
                         reason="audit probe")
                impact = memory.related("a.py::target")["impact"]
                self.assertFalse([e for e in impact["impacts"]
                                  if e["symbol"] == "a.py::caller"])
                out = memory.answer("a.py::caller", limit=10, select=False,
                                    budget={"graph": 5})
                self.assertFalse([c for c in out["candidates"]
                                  if c["symbol"] == "a.py::target"
                                  and "graph" in c["origins"]])
            finally:
                memory.close()

    def test_excluded_sources_absent_from_impact_and_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp, exclude=("exc",))
            try:
                impact = memory.related("a.py::target")["impact"]
                self.assertFalse([e for e in impact["impacts"]
                                  if e["symbol"].startswith("exc/")])
                refs = memory.references("a.py::target")
                self.assertTrue(refs["references"])
                self.assertFalse([r for r in refs["references"]
                                  if r["path"].startswith("exc/")])
            finally:
                memory.close()

    def test_config_edges_labeled_syntactic_never_asserted(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                _, view = memory.store.view()
                edges = [a for a in view["assertions"]
                         if a["proposition"].get("relation") == "depends_on"
                         and a["proposition"].get("caller")
                         == "cfg.json::$ref"]
                self.assertTrue(edges)
                self.assertTrue(all(a["status"] == "syntactic_observation"
                                    for a in edges))
                related = memory.related("cfg.json::server.host")
                impact = related["impact"]
                self.assertFalse([e for e in impact["impacts"]
                                  if e["symbol"] == "cfg.json::$ref"])
                self.assertFalse(
                    [e for e in impact["impacts"]
                     if e["derivation"]["rule"] == "asserted_dependency_v1"])
                visible = [c for c in related["claims"]["claims"]
                           if c["relation"] == "depends_on"
                           and c["caller"] == "cfg.json::$ref"]
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0]["status"],
                                 "syntactic_observation")
                callees = memory.callees("cfg.json::$ref")
                self.assertEqual(callees["direct"], [])
                self.assertEqual(callees["reachable"], [])
            finally:
                memory.close()

    def test_schema_refs_do_not_traverse_impact(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                _, view = memory.store.view()
                edges = [a for a in view["assertions"]
                         if a["proposition"].get("relation") == "references"]
                self.assertTrue(edges)
                self.assertTrue(all(a["status"] == "syntactic_observation"
                                    for a in edges))
                related = memory.related("s.sql::users.org_id")
                impact = related["impact"]
                self.assertEqual(impact["impacts"], [])
                visible = [c for c in related["claims"]["claims"]
                           if c["relation"] == "references"]
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0]["status"],
                                 "syntactic_observation")
            finally:
                memory.close()

    def test_impact_units_never_carry_proposed_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                memory.promote("a.py::caller", "a.py::target")
                memory.ingest()
                impact = memory.related("a.py::target")["impact"]
                statuses = set()
                for unit in impact["units"]:
                    status = unit["assertion"]["status"]
                    statuses.add(status)
                    if unit["kind"] == "asserted_claim_observation":
                        self.assertEqual(status, "asserted_in_source")
                self.assertNotIn("proposed", statuses)
                self.assertNotIn("proposed_ambiguous", statuses)
            finally:
                memory.close()

    def test_claims_rows_always_carry_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                memory.promote("a.py::caller", "a.py::target")
                memory.ingest()
                claims = memory.related("a.py::target")["claims"]["claims"]
                self.assertTrue(claims)
                for claim in claims:
                    self.assertIn(claim["status"],
                                  ("asserted_in_source", "proposed",
                                   "proposed_ambiguous"))
            finally:
                memory.close()

    def test_mmr_reorders_without_dropping(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                plain = memory.answer("target", limit=5, select=False)
                diverse = memory.answer("target", limit=5, select=False,
                                        mmr_lambda=0.0)
                self.assertEqual({c["symbol"] for c in plain["candidates"]},
                                 {c["symbol"] for c in diverse["candidates"]})
                self.assertEqual(diverse["mmr"],
                                 {"lambda": 0.0, "applied": True})
            finally:
                memory.close()

    def test_graph_origin_labeled_not_asserted(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                out = memory.answer("a.py::caller", limit=10, select=False,
                                    budget={"graph": 5})
                graph_touched = [c for c in out["candidates"]
                                 if "graph" in c["origins"]]
                self.assertTrue(graph_touched)
                for entry in out["candidates"]:
                    self.assertIn("origins", entry)
                    self.assertIn("match", entry)
                self.assertTrue(out["stages"]["graph"]["candidates"] >= 1)
                self.assertFalse(out["stages"]["graph"]["skipped"])
            finally:
                memory.close()

    def test_historical_snapshot_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, receipt = _ingest(tmp)
            try:
                first = receipt["snapshot"]
                work = Path(tmp) / "ws"
                astro = work / "site" / "page.astro"
                astro.write_text(astro.read_text(encoding="utf-8")
                                 + "<p>{target} again</p>\n",
                                 encoding="utf-8")
                second = memory.ingest()["snapshot"]
                self.assertNotEqual(first, second)
                old = memory.references("a.py::target", snapshot=first)
                new = memory.references("a.py::target", snapshot=second)
                self.assertTrue(old["complete"])
                self.assertEqual(old["total"] + 1, new["total"])
            finally:
                memory.close()

    def test_evidence_only_policy_excludes_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                memory.promote("a.py::caller", "a.py::target")
                memory.ingest()
                impact = memory.related(
                    "a.py::target",
                    policy="evidence_only")["impact"]
                kinds = {u["kind"] for u in impact["units"]}
                self.assertTrue(kinds)
                self.assertEqual(kinds, {"static_call_observation"})
            finally:
                memory.close()

    def test_bare_data_label_without_signals_not_filed(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                _, view = memory.store.view()
                cands = [d for d in view["diagnostics"]
                         if d.get("kind") == "mention_candidate"
                         and d.get("expression") == "host"
                         and d.get("callee", "").startswith("cfg.json")]
                self.assertFalse(
                    cands,
                    "bare data-label word with no signals is not a lead")
                mentions = [
                    a for a in view["assertions"]
                    if a["proposition"].get("relation") == "text_mention"
                    and a["proposition"].get("callee")
                    == "cfg.json::server.host"]
                self.assertEqual(len(mentions), 1)
                # Coverage-ladder policy requires an exact key for ordinary
                # data labels. This observes the citation, not a dependency.
                self.assertEqual(mentions[0]["status"], "syntactic_observation")
                self.assertEqual(mentions[0]["resolution"]["rule"], "exact_key_match_v1")
            finally:
                memory.close()

    def test_references_outside_default_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                out = memory.answer("target", limit=5)
                blob = repr(out)
                self.assertNotIn("textual_reference", blob)
                self.assertNotIn("'references'", blob)
            finally:
                memory.close()


class RefreshParityTest(unittest.TestCase):
    def _snapshot(self, view):
        symbols = sorted(s["key"] for s in view["symbols"])
        edges = sorted((a["proposition"].get("relation"),
                        a["proposition"].get("caller"),
                        a["proposition"].get("callee"),
                        a["status"])
                       for a in view["assertions"])
        diags = sorted((d.get("kind"), d.get("path"))
                       for d in view["diagnostics"])
        return symbols, edges, diags

    def test_refresh_matches_rebuild_across_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                work = Path(tmp) / "ws"
                (work / "a.py").write_text(
                    (work / "a.py").read_text(encoding="utf-8")
                    + "\ndef extra():\n    return target()\n",
                    encoding="utf-8")
                (work / "cfg.json").write_text(
                    (work / "cfg.json").read_text(encoding="utf-8").replace(
                        "db.local", "db2.local"), encoding="utf-8")
                (work / "site" / "page.astro").write_text(
                    (work / "site" / "page.astro").read_text(
                        encoding="utf-8") + "<p>{target}</p>\n",
                    encoding="utf-8")
                (work / "s.sql").write_text(
                    (work / "s.sql").read_text(encoding="utf-8")
                    + "CREATE TABLE audit (oid INT REFERENCES orgs(id));\n",
                    encoding="utf-8")
                memory.refresh()
                _, refreshed = memory.store.view()
            finally:
                memory.close()
            with tempfile.TemporaryDirectory() as tmp2:
                memory2, _ = _ingest(tmp2)
                try:
                    work2 = Path(tmp2) / "ws"
                    (work2 / "a.py").write_text(
                        (work / "a.py").read_text(encoding="utf-8"),
                        encoding="utf-8")
                    (work2 / "cfg.json").write_text(
                        (work / "cfg.json").read_text(encoding="utf-8"),
                        encoding="utf-8")
                    astro2 = work2 / "site" / "page.astro"
                    astro2.parent.mkdir(parents=True, exist_ok=True)
                    astro2.write_text(
                        (work / "site" / "page.astro").read_text(
                            encoding="utf-8"), encoding="utf-8")
                    (work2 / "s.sql").write_text(
                        (work / "s.sql").read_text(encoding="utf-8"),
                        encoding="utf-8")
                    for name in ("exc/skip.py", "exc/note.astro"):
                        dest = work2 / name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text(
                            (work / name).read_text(encoding="utf-8"),
                            encoding="utf-8")
                    memory2.refresh()
                    _, rebuilt = memory2.store.view()
                finally:
                    memory2.close()
            self.assertEqual(self._snapshot(refreshed),
                             self._snapshot(rebuilt))

    def test_community_summaries_outside_answer_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _ = _ingest(tmp)
            try:
                out = memory.answer("target", limit=5)
                related = memory.related("a.py::target")
                callees = memory.callees("a.py::caller")
                for blob in (out, related, callees):
                    self.assertNotIn("community", blob)
                    self.assertNotIn("communities", blob)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
