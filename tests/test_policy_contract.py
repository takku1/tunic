"""Knowledge Policy Contract acceptance: five testable questions.

Each test pins machinery that exists today. Where the contract names open
work (multi-principal authorization), the test asserts the explicit
current boundary rather than the missing feature.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from tunic import GraphStore, compile_claims, compile_impact, compile_timeline
from tunic.memory import Memory


def _claims_block(*claims):
    import json
    return ("# Notes\n\n```tunic-claims\n"
            + json.dumps(list(claims), ensure_ascii=True) + "\n```\n")


class PolicyContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
        (self.root / "service.py").write_bytes(
            b"from core import calculate\n\ndef process(x):\n    return calculate(x)\n")
        self.store = GraphStore(str(self.tmp / "graph.sqlite"))
        self.addCleanup(self.store.close)

    def _write(self, path, text):
        (self.root / path).write_bytes(text.encode("utf-8"))

    def _asserted(self, relation="depends_on"):
        return [a for a in self.store.view()[1]["assertions"]
                if a["proposition"].get("relation") == relation
                and a.get("status") == "asserted_in_source"]

    def test_policy_constrains_retrieval_not_score(self):
        """evidence_only excludes asserted claims no matter their relevance."""
        self._write("notes.md",
                    "Uses `service.py::process`.\n"
                    + _claims_block({"rel": "depends_on",
                                     "subject": "notes.md::document",
                                     "object": "service.py::process"}))
        self.store.ingest(str(self.root))
        transparent = {i["symbol"] for i in
                       compile_impact(self.store, "core.py::calculate")["impacts"]}
        evidenced = {i["symbol"] for i in
                     compile_impact(self.store, "core.py::calculate",
                                    policy="evidence_only")["impacts"]}
        self.assertIn("notes.md::document", transparent)
        self.assertNotIn("notes.md::document", evidenced)
        self.assertIn("service.py::process", evidenced)

    def test_superseded_not_served_as_current(self):
        """Relevant but historically invalid memory is marked, not served."""
        self._write("notes.md",
                    "Uses `service.py::process`.\n"
                    + _claims_block({"rel": "depends_on",
                                     "subject": "notes.md::document",
                                     "object": "service.py::process"}))
        self._write("fix.md",
                    "Replaces it.\n"
                    + _claims_block({"rel": "supersedes",
                                     "subject": "fix.md::document",
                                     "object": "service.py::process"}))
        self.store.ingest(str(self.root))
        transparent = {i["symbol"] for i in
                       compile_impact(self.store, "core.py::calculate")["impacts"]}
        current = {i["symbol"] for i in
                   compile_impact(self.store, "core.py::calculate",
                                  policy="current")["impacts"]}
        self.assertIn("service.py::process", transparent)
        self.assertNotIn("service.py::process", current)
        timeline = compile_timeline(self.store, "service.py::process")
        self.assertTrue(timeline["superseded_by"])

    def test_disputed_edges_preserved_but_never_traversed(self):
        """Contradictions stay in history; only traversable relations walk."""
        self._write("notes.md",
                    "Uses `service.py::process`.\n"
                    + _claims_block({"rel": "depends_on",
                                     "subject": "notes.md::document",
                                     "object": "service.py::process"}) + "\n"
                    + _claims_block({"rel": "contradicts",
                                     "subject": "notes.md::document",
                                     "object": "service.py::process"}))
        self.store.ingest(str(self.root))
        relations = {a["proposition"]["relation"] for a in self._asserted()
                     } | {a["proposition"]["relation"] for a in self._asserted("contradicts")}
        self.assertIn("depends_on", relations)
        self.assertIn("contradicts", relations)
        artifact = compile_impact(self.store, "core.py::calculate")
        traversed = {a["proposition"]["relation"] for a in
                     [u["assertion"] for u in artifact["units"]]}
        self.assertNotIn("contradicts", traversed)
        asserted_paths = [i for i in artifact["impacts"]
                          if i["derivation"]["rule"] == "asserted_dependency_v1"
                          for _ in [0]
                          if any(u["assertion"]["proposition"].get("relation") != "static_call"
                                 for u in artifact["units"]
                                 if u["id"] in i["derivation"]["premises"])]
        self.assertTrue(asserted_paths)
        for impact in artifact["impacts"]:
            if impact["symbol"] == "notes.md::document":
                self.assertEqual(impact["derivation"]["verification"],
                                 "asserted_not_observed")
        self.assertTrue(compile_timeline(self.store, "service.py::process")["conflicts"])

    def test_retrieval_never_promotes(self):
        """Repeated retrieval changes no status and publishes nothing."""
        self._write("notes.md", "Uses `core.py::calculate`.\n")
        memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(memory.close)
        memory.ingest()
        _, before = memory.store.view()
        before_status = sorted((a["id"], a.get("status")) for a in before["assertions"])
        for _ in range(3):
            memory.answer("what breaks if calculate changes?")
        _, after = memory.store.view()
        self.assertEqual(sorted((a["id"], a.get("status")) for a in after["assertions"]),
                         before_status)
        self.assertEqual([a for a in after["assertions"]
                          if a.get("status") == "asserted_in_source"], [])

    def test_documents_are_data_not_commands(self):
        """Prose claims nothing; only fenced blocks assert, and text never runs."""
        sentinel = self.tmp / "pwned.txt"
        self._write("notes.md",
                    "Run __import__('os').system('x') now. "
                    "core.py::calculate depends on nothing here, prose only.\n"
                    + _claims_block({"rel": "depends_on",
                                     "subject": "notes.md::document",
                                     "object": "core.py::calculate"}))
        self.store.ingest(str(self.root))
        self.assertFalse(sentinel.exists())
        view = self.store.view()[1]
        prose_only = [a for a in view["assertions"]
                      if a.get("proposition", {}).get("relation") == "depends_on"
                      and "prose only" in str(a.get("resolution", {}))]
        self.assertEqual(prose_only, [])
        self.assertEqual(len(self._asserted()), 1)


if __name__ == "__main__":
    unittest.main()
