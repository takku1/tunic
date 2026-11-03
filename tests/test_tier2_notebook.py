"""Tier-2 notebooks: code cells feed the Python AST, markdown joins docs.

Covers cross-cell calls (shared notebook namespace), R2 into .py
imports, magic stripping with diagnostics, syntax-error cells,
markdown text_mention of code keys, document symbols, and
line-granular parent-absolute spans.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory


def _notebook(cells):
    return json.dumps({"nbformat": 4, "nbformat_minor": 5,
                       "metadata": {},
                       "cells": cells},
                      indent=1)


def _code(source_lines):
    return {"cell_type": "code", "execution_count": None,
            "metadata": {}, "outputs": [],
            "source": [line + "\n" for line in source_lines]}


def _markdown(source_lines):
    return {"cell_type": "markdown", "metadata": {},
            "source": [line + "\n" for line in source_lines]}


NB_FILES = {
    "nb.ipynb": _notebook([
        _markdown(["# Analysis", "",
                   "See analysis.py::run for the entry point."]),
        _code(["def helper(x):",
               "    return x"]),
        _code(["from analysis import run",
               "",
               "",
               "def main():",
               "    return run(helper(1))"]),
        _code(["%matplotlib inline",
               "",
               "def plotted():",
               "    return helper(2)"]),
        _code(["def broken(:",
               "    pass"]),
        _markdown(["Done."]),
    ]),
    "analysis.py": """def run(x):
    return x


def top():
    return run(1)
""",
}


def _ingest(files):
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name) / "ws"
    for name, text in files.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    raws = {name: (work / name).read_bytes() for name in files}
    memory = Memory(str(Path(tmp.name) / "t.sqlite"), str(work))
    memory.ingest()
    _snapshot, view = memory.store.view()
    memory.close()
    tmp.cleanup()
    return view, raws


def _edges(view):
    return {(a["proposition"]["caller"], a["proposition"]["callee"])
            for a in view["assertions"]
            if a["proposition"].get("relation") == "static_call"
            and a.get("status") == "syntactic_observation"}


def _producers(view):
    return {a.get("producer")
            for a in view["assertions"]
            if a["proposition"].get("relation") == "static_call"
            and a.get("status") == "syntactic_observation"}


class NotebookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view, cls.raws = _ingest(NB_FILES)
        cls.edges = _edges(cls.view)

    def test_cross_cell_call(self):
        self.assertIn(("nb.ipynb::cell[2]::main",
                       "nb.ipynb::cell[1]::helper"),
                      self.edges)

    def test_r2_into_py(self):
        self.assertIn(("nb.ipynb::cell[2]::main", "analysis.py::run"),
                      self.edges)

    def test_magic_cell_stripped_and_resolved(self):
        self.assertIn(("nb.ipynb::cell[3]::plotted",
                       "nb.ipynb::cell[1]::helper"),
                      self.edges)
        diags = [d for d in self.view["diagnostics"]
                 if d.get("kind") == "unsupported_syntax"
                 and d.get("expression") == "ipython_magic"]
        self.assertTrue(diags)

    def test_broken_cell_abstains(self):
        self.assertFalse([e for e in self.edges
                          if e[0] == "nb.ipynb::cell[4]::broken"])
        diags = [d for d in self.view["diagnostics"]
                 if d.get("expression") == "cell[4]"]
        self.assertTrue(diags)

    def test_markdown_mentions_code(self):
        mentions = [(a["proposition"]["caller"],
                     a["proposition"]["callee"])
                    for a in self.view["assertions"]
                    if a["proposition"].get("relation") == "text_mention"
                    and a["proposition"].get("caller")
                    == "nb.ipynb::document"]
        self.assertIn(("nb.ipynb::document", "analysis.py::run"),
                      mentions)

    def test_document_symbol(self):
        keys = [s["key"] for s in self.view["symbols"]]
        self.assertIn("nb.ipynb::document", keys)
        self.assertIn("nb.ipynb::cell[1]::helper", keys)

    def test_python_receipt(self):
        self.assertIn(("analysis.py::top", "analysis.py::run"),
                      self.edges)
        self.assertEqual(_producers(self.view), {"python-static-v7"})

    def test_spans_are_line_granular(self):
        # Spans are over the stored file bytes; compare against the
        # exact bytes written, never a newline-normalized reconstruction.
        raw = self.raws["nb.ipynb"]
        for a in self.view["assertions"]:
            prop = a["proposition"]
            if prop.get("relation") != "static_call":
                continue
            if prop.get("caller") != "nb.ipynb::cell[2]::main":
                continue
            window = raw[a["start"]:a["end"]]
            self.assertIn(b"run(helper(1))", window)


if __name__ == "__main__":
    unittest.main()
