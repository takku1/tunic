"""Relation type signatures: ontology enforcement at claim parse."""

import tempfile
import unittest
from pathlib import Path

from tunic import extract
from tunic.memory import Memory

FILES = {
    "code.py": "def alpha():\n    return 1\ndef beta():\n    return 2\n",
    "guide.md": "# Guide\n\nSee the API.\n",
    "api.md": "# API\n\nReference.\n",
    "claims.md": ("# Claims\n\n"
                  "```tunic-claims\n"
                  "[{\"rel\": \"depends_on\", "
                  "\"subject\": \"code.py::alpha\", "
                  "\"object\": \"code.py::beta\"}, "
                  "{\"rel\": \"depends_on\", "
                  "\"subject\": \"code.py::alpha\", "
                  "\"object\": \"guide.md::document\"}, "
                  "{\"rel\": \"same_as\", "
                  "\"subject\": \"code.py::alpha\", "
                  "\"object\": \"guide.md::document\"}, "
                  "{\"rel\": \"contradicts\", "
                  "\"subject\": \"guide.md::document\", "
                  "\"object\": \"api.md::document\"}]\n"
                  "```\n"),
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    work.mkdir(parents=True, exist_ok=True)
    for name, text in FILES.items():
        (work / name).write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "sig.sqlite"), str(work))
    report = memory.ingest()
    return memory, report


def _edges(view):
    return {(a["proposition"]["relation"],
             a["proposition"].get("caller"),
             a["proposition"].get("callee"))
            for a in view["assertions"]
            if a.get("status") == "syntactic_observation"
            or a.get("status") == "asserted_in_source"}


class SignatureTest(unittest.TestCase):
    def test_table_unit(self):
        self.assertTrue(extract.check_relation_signature(
            "depends_on", "a.py::x", "b.py::y"))
        self.assertTrue(extract.check_relation_signature(
            "depends_on", "a.md::document", "b.py::y"))
        self.assertFalse(extract.check_relation_signature(
            "depends_on", "b.py::y", "a.md::document"))
        self.assertTrue(extract.check_relation_signature(
            "same_as", "a.py::x", "b.py::y"))
        self.assertTrue(extract.check_relation_signature(
            "same_as", "a.md::document", "b.md::document"))
        self.assertFalse(extract.check_relation_signature(
            "same_as", "a.py::x", "b.md::document"))
        self.assertTrue(extract.check_relation_signature(
            "contradicts", "a.md::document", "b.py::y"))
        self.assertTrue(extract.check_relation_signature(
            "supersedes", "a.py::x", "b.md::document"))

    def test_code_to_code_asserted(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("depends_on", "code.py::alpha",
                               "code.py::beta"), _edges(view))
            finally:
                memory.close()

    def test_backwards_code_on_prose_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("depends_on", "code.py::alpha",
                                  "guide.md::document"), _edges(view))
                kinds = {d.get("kind") for d in view["diagnostics"]}
                self.assertIn("relation_signature", kinds)
            finally:
                memory.close()

    def test_cross_kind_same_as_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("same_as", "code.py::alpha",
                                  "guide.md::document"), _edges(view))
            finally:
                memory.close()

    def test_meta_relation_cross_kind_asserted(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("contradicts", "guide.md::document",
                               "api.md::document"), _edges(view))
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
