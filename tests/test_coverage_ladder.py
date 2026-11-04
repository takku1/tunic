"""The coverage ladder: every in-scope file is admitted or accounted for.

The contract under test: an unrecognized file degrades to a lower rung
instead of disappearing, nothing admitted this way can disturb the
proof-bearing channels, and credentials never reach storage.
"""

import ast
import json
from pathlib import Path
import tempfile
import unittest

from tunic import coverage, extract, filetypes, structure, tsgraph, unigraph
from tunic.memory import Memory
from tunic.store import GraphStore


def write_tree(root, files):
    for path, content in files.items():
        target = Path(root) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8", newline="\n")


def scanned(root):
    entries, _diagnostics, excluded = extract._scan(root)
    return {entry["path"] for entry in entries}, excluded["ledger"]


class Classification(unittest.TestCase):
    def test_families(self):
        cases = {"a.py": filetypes.PYTHON, "a.md": filetypes.PROSE,
                 "a.ipynb": filetypes.NOTEBOOK, "a.yaml": filetypes.CONFIG,
                 "a.sql": filetypes.SCHEMA, "a.vue": filetypes.BLOCK,
                 "a.go": filetypes.CODE}
        for path, family in cases.items():
            self.assertEqual(filetypes.classify(path).family, family, path)

    def test_suffix_case_is_not_identity(self):
        self.assertEqual(filetypes.classify("analysis.R").language, "r")
        self.assertEqual(filetypes.classify("README.MD").family, filetypes.PROSE)
        self.assertEqual(filetypes.classify("a.YML").family, filetypes.CONFIG)

    def test_named_files(self):
        self.assertEqual(filetypes.classify("sub/Dockerfile").language, "dockerfile")
        self.assertEqual(filetypes.classify("Dockerfile.dev").language, "dockerfile")
        self.assertEqual(filetypes.classify("Makefile").language, "make")

    def test_a_suffix_family_beats_a_name_prefix(self):
        self.assertEqual(filetypes.classify("dockerfile.py").family, filetypes.PYTHON)

    def test_shebang_names_an_extensionless_script(self):
        for head, language in ((b"#!/bin/bash\n", "bash"),
                               (b"#!/usr/bin/env python3\n", "python"),
                               (b"#!/usr/bin/env -S node\n", "javascript")):
            self.assertEqual(filetypes.classify("tool", head).language, language)

    def test_unknown_is_text_not_rejected(self):
        filetype = filetypes.classify("notes.zzzunknown")
        self.assertEqual(filetype, filetypes.FileType(filetypes.TEXT, None))

    def test_every_edge_engine_extension_is_in_the_table(self):
        """The classifier cannot drift from what the engines handle."""
        engines = set(tsgraph.TS_EXTENSIONS)
        for spec in unigraph.SPECS.values():
            engines |= set(spec["extensions"])
        missing = {e for e in engines if e not in filetypes.CODE_LANGUAGES}
        self.assertEqual(missing, set())


class Admission(unittest.TestCase):
    def test_unknown_text_is_admitted(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"Dockerfile": "FROM python:3.12\n",
                              "site.css": ".a { color: red }\n",
                              "notes.zzzunknown": "hello\n"})
            paths, ledger = scanned(root)
            self.assertEqual(paths, {"Dockerfile", "site.css", "notes.zzzunknown"})
            self.assertEqual(ledger, {})

    def test_each_refusal_has_a_reason(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {
                "logo.png": b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR",
                "poetry.lock": "[[package]]\n",
                "bundle.js.map": "{}",
                "empty.dat": b"",
                "big.dat": b"x" * (filetypes.MAX_TEXT_BYTES + 1),
                "latin.dat": "caf\xe9\n".encode("latin-1"),
                "keep.dat": "kept\n"})
            paths, ledger = scanned(root)
            self.assertEqual(paths, {"keep.dat"})
            self.assertEqual(ledger, {"binary": 1, "generated": 2, "empty": 1,
                                      "oversize": 1, "undecodable": 1})

    def test_known_families_are_not_size_capped(self):
        """Admission gates must not change what was always read in full."""
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"big.py": "x = 1\n" * 300000})
            self.assertEqual(scanned(root)[0], {"big.py"})

    def test_tool_caches_are_pruned(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {".pytest_cache/v/cache/nodeids": "[]\n",
                              ".mypy_cache/x.txt": "y\n", "a.py": "x = 1\n"})
            self.assertEqual(scanned(root)[0], {"a.py"})


class Secrets(unittest.TestCase):
    SECRET = "API_KEY=sk-should-never-leave\n"

    def test_credentials_are_refused_by_name(self):
        names = [".env", ".env.local", ".env.production", "prod.env", "id_rsa",
                 ".npmrc", "server.pem", "tls.key", "credentials.json",
                 "secrets.yaml", "deep/er/.git-credentials"]
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {name: self.SECRET for name in names})
            paths, ledger = scanned(root)
            self.assertEqual(paths, set())
            self.assertEqual(ledger, {"sensitive": len(names)})

    def test_templates_carry_no_secret_and_stay(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {".env.example": "API_KEY=\n",
                              ".env.sample": "API_KEY=\n"})
            self.assertEqual(scanned(root)[0], {".env.example", ".env.sample"})

    def test_private_key_is_refused_by_content(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"deploy_material":
                              "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n"})
            paths, ledger = scanned(root)
            self.assertEqual(paths, set())
            self.assertEqual(ledger, {"sensitive": 1})

    def test_secret_bytes_never_reach_the_database(self):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as data:
            write_tree(root, {".env": self.SECRET, "a.py": "x = 1\n"})
            db = str(Path(data) / "t.sqlite")
            with GraphStore(db) as store:
                store.ingest(root)
            self.assertNotIn(b"sk-should-never-leave", Path(db).read_bytes())

    def test_a_python_module_named_secrets_is_code(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"secrets.py": "x = 1\n"})
            self.assertEqual(scanned(root)[0], {"secrets.py"})


class Isolation(unittest.TestCase):
    """New admissions must not reach the proof-bearing channels."""

    def test_text_rung_files_carry_no_claims_or_mentions(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {
                "core.py": "def compute():\n    return 1\n",
                "notes.zzzunknown": "compute\n<!-- tunic: depends_on a b -->\n"})
            _sources, symbols, assertions, _d, _e = extract.extract(root)
            record = next(s for s in symbols
                          if s["key"] == "notes.zzzunknown::document")
            self.assertEqual(record["derivation"], extract.FILE_TEXT_DERIVATION)
            callers = {a["proposition"]["caller"] for a in assertions}
            self.assertNotIn("notes.zzzunknown::document", callers)

    def test_document_is_not_a_mention_target(self):
        """`::document` records of non-prose files are not code names."""
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"a.zzzunknown": "text\n",
                              "guide.md": "This document explains things.\n"})
            _sources, _symbols, assertions, _d, _e = extract.extract(root)
            self.assertEqual([a for a in assertions if a["proposition"].get(
                "callee", "").endswith("::document")], [])

    def test_a_sibling_stem_does_not_join_the_python_module(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"foo.py": "def helper():\n    return 1\n",
                              "foo.html": "<p>hi</p>\n",
                              "main.py": "import foo\n\ndef run():\n    return foo.helper()\n"})
            _s, _y, assertions, _d, _e = extract.extract(root)
            self.assertIn(("main.py::run", "foo.py::helper"),
                          {(a["proposition"]["caller"], a["proposition"]["callee"])
                           for a in assertions})


class KeyPathMentions(unittest.TestCase):
    """A config key's leaf is usually an English word, not a citation."""

    TREE = {"package.json": '{"type": "module", "name": "site"}\n',
            "notes.md": "The type of name matters.\n"
                        "Set `package.json::type` before release.\n"}

    def test_bare_leaf_words_do_not_flood(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, self.TREE)
            _s, _y, assertions, diagnostics, _e = extract.extract(root)
            self.assertEqual([d for d in diagnostics
                              if d["kind"] == "mention_candidate"], [])
            self.assertEqual([a for a in assertions if a.get("resolution", {}).get(
                "rule") == "name_match_proposed_v1"], [])

    def test_identifier_shaped_leaves_stay_mentionable(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"compose.yml": "env:\n  APP_NAME: site\n",
                              "guide.md": "Set APP_NAME before you deploy.\n"})
            _s, _y, assertions, diagnostics, _e = extract.extract(root)
            found = {a["proposition"]["callee"] for a in assertions} | {
                d.get("callee") for d in diagnostics
                if d["kind"] == "mention_candidate"}
            self.assertIn("compose.yml::env.APP_NAME", found)

    def test_exact_keys_stay_citable(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, self.TREE)
            _s, _y, assertions, _d, _e = extract.extract(root)
            self.assertIn(("notes.md::document", "package.json::type"),
                          {(a["proposition"]["caller"], a["proposition"]["callee"])
                           for a in assertions})


class RefreshEquivalence(unittest.TestCase):
    """refresh() promises full-rebuild output; hold it on mixed trees."""

    TREE = {
        "foo.py": "def helper():\n    return 1\n",
        "foo.ts": "export function other() { return 2 }\n",
        "foo.html": "<p>page</p>\n",
        "main.py": "import foo\n\ndef run():\n    return foo.helper()\n",
        "app.go": "package app\n\nfunc Run() int { return add() }\nfunc add() int { return 1 }\n",
        "conf.yaml": "server:\n  port: 8080\n",
        "db.sql": "CREATE TABLE t (id INT PRIMARY KEY);\n",
        "Dockerfile": "FROM python:3.12\n",
        "script.zzzunknown": "plain text\n",
        "guide.md": "See `foo.py::helper` for details.\n",
        "nb.ipynb": json.dumps({"cells": [{"cell_type": "code", "source": ["def cell():\n", "    return 1\n"],
                                           "metadata": {}, "outputs": [], "execution_count": 1}],
                                "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
    }

    def assert_equivalent(self, edit):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as data:
            write_tree(root, self.TREE)
            with GraphStore(str(Path(data) / "inc.sqlite")) as incremental:
                incremental.ingest(root)
                edit(Path(root))
                incremental.refresh(root)
                with GraphStore(str(Path(data) / "full.sqlite")) as full:
                    full.ingest(root)
                    self.assertEqual(incremental.current(), full.current())

    def test_each_file_edited(self):
        for path in sorted(self.TREE):
            with self.subTest(path=path):
                def edit(root, path=path):
                    target = root / path
                    if path.endswith(".ipynb"):
                        body = json.loads(target.read_text(encoding="utf-8"))
                        body["cells"][0]["source"] = ["def cell2():\n", "    return 2\n"]
                        target.write_text(json.dumps(body), encoding="utf-8", newline="\n")
                    else:
                        with target.open("a", encoding="utf-8", newline="\n") as handle:
                            handle.write("\n" if path.endswith(".py") else " \n")
                self.assert_equivalent(edit)

    def test_each_file_deleted(self):
        for path in sorted(self.TREE):
            with self.subTest(path=path):
                self.assert_equivalent(lambda root, path=path: (root / path).unlink())

    def test_new_unknown_file_added(self):
        self.assert_equivalent(lambda root: (root / "new.zzz").write_text("x\n"))


class Structure(unittest.TestCase):
    def setUp(self):
        if structure.parser_for("python") is None:
            self.skipTest("tree-sitter-python not installed")

    def rows(self, language, source):
        tree = structure.parser_for(language).parse(source)
        return {name for name, _kind, _node in structure.definitions(tree)}

    def test_true_labels_on_this_package(self):
        """Python's ast is ground truth; the walker must match it exactly."""
        parser = structure.parser_for("python")
        checked = 0
        for path in sorted(Path(extract.__file__).parent.glob("*.py")):
            data = path.read_bytes()
            truth, scope = set(), []

            class Visitor(ast.NodeVisitor):
                def visit_ClassDef(self, node):
                    truth.add(".".join(scope + [node.name]))
                    scope.append(node.name)
                    self.generic_visit(node)
                    scope.pop()

                def visit_FunctionDef(self, node):
                    truth.add(".".join(scope + [node.name]))

                visit_AsyncFunctionDef = visit_FunctionDef

            Visitor().visit(ast.parse(data))
            got = {n for n, _k, _node in structure.definitions(parser.parse(data))}
            self.assertEqual(got, truth, path.name)
            checked += len(truth)
        self.assertGreater(checked, 500)

    def test_stops_at_function_bodies(self):
        self.assertEqual(self.rows("python", b"def a():\n    def b():\n        pass\n"), {"a"})

    def test_qualifies_through_classes(self):
        self.assertEqual(self.rows("python", b"class A:\n    def m(self):\n        pass\n"),
                         {"A", "A.m"})

    def test_imports_are_not_definitions(self):
        if structure.parser_for("php") is None:
            self.skipTest("tree-sitter-php not installed")
        grammar = structure.parser_for("php")
        tree = grammar.parse(b"<?php\nnamespace App;\nuse Foo\\Bar;\nclass Baz {}\n")
        names = {n for n, _k, _node in structure.definitions(tree)}
        self.assertNotIn("Bar", names)
        self.assertIn("Baz", names)

    def test_no_grammar_is_none_not_an_error(self):
        self.assertIsNone(structure.parser_for("zzz_no_such_language"))
        self.assertIsNone(structure.parser_for(None))

    def test_symbols_rung_makes_no_edges(self):
        if structure.parser_for("zig") is None:
            self.skipTest("tree-sitter-zig not installed")
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"a.zig": "fn callee() void {}\nfn caller() void { callee(); }\n"})
            _s, symbols, assertions, _d, _e = extract.extract(root)
            self.assertEqual(assertions, [])
            derived = {s["name"]: s["derivation"] for s in symbols if s["name"] != "document"}
            self.assertEqual(derived, {"callee": structure.DERIVATION,
                                       "caller": structure.DERIVATION})


class Retrieval(unittest.TestCase):
    def memory(self, root, data):
        memory = Memory(str(Path(data) / "m.sqlite"), root)
        memory.ingest()
        return memory

    def test_a_dockerfile_is_retrievable_by_content(self):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as data:
            write_tree(root, {"Dockerfile": "FROM gunicorn-base\nEXPOSE 8123\n",
                              "core.py": "def compute():\n    return 1\n"})
            with self.memory(root, data) as memory:
                answer = memory.answer("what does gunicorn expose", select=False)
            self.assertIn("Dockerfile", {p["doc"] for p in answer["documents"]})

    def test_file_passages_never_displace_prose(self):
        task = "rollback procedure for gunicorn"
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as data:
            prose = {"ops.md": "# Rollback\n\nThe rollback procedure for gunicorn.\n"}
            write_tree(root, prose)
            with self.memory(root, data) as memory:
                before = memory.answer(task, select=False)["documents"]
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as data:
            write_tree(root, dict(prose, **{"run.zzz": "rollback gunicorn rollback\n" * 9}))
            with self.memory(root, data) as memory:
                after = memory.answer(task, select=False)["documents"]
        self.assertEqual([p["unit"] for p in after][:len(before)],
                         [p["unit"] for p in before])
        self.assertEqual(after[-1]["doc"], "run.zzz")

    def test_files_budget_zero_disables_the_stage(self):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as data:
            write_tree(root, {"run.zzz": "gunicorn\n"})
            with self.memory(root, data) as memory:
                answer = memory.answer("gunicorn", select=False, budget={"files": 0})
            self.assertEqual(answer["documents"], [])

    def test_lexical_reaches_non_python_symbols(self):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as data:
            write_tree(root, {"svc.ts": "export function chargeInvoice() {\n"
                                        "  // settle the ledger balance\n  return 1\n}\n"})
            with self.memory(root, data) as memory:
                answer = memory.answer("settle ledger balance", select=False)
            self.assertIn("svc.ts::chargeInvoice",
                          {row["symbol"] for row in answer["candidates"]})


class Report(unittest.TestCase):
    def test_every_file_is_on_a_rung_or_the_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"a.py": "x = 1\n", "b.md": "# b\n", "c.yaml": "k: 1\n",
                              "d.zzz": "text\n", "e.png": b"\x89PNG\0\0", ".env": "A=1\n"})
            result = coverage.report(root)
            self.assertEqual(result["files"], 4)
            self.assertEqual(sum(result["rungs"].values()), 4)
            self.assertEqual(result["rungs"]["edges"], 1)
            self.assertEqual(result["rungs"]["entities"], 1)
            self.assertEqual(result["rungs"]["text"], 2)
            self.assertEqual(result["ledger"], {"binary": 1, "sensitive": 1})

    def test_missing_grammar_is_named_not_hidden(self):
        with tempfile.TemporaryDirectory() as root:
            write_tree(root, {"m.cobol_like": "x\n"})
            original = filetypes.CODE_LANGUAGES.get(".cobol_like")
            filetypes.CODE_LANGUAGES[".cobol_like"] = "zzz_no_such_language"
            try:
                result = coverage.report(root)
            finally:
                if original is None:
                    del filetypes.CODE_LANGUAGES[".cobol_like"]
            self.assertEqual(result["missing_grammars"], {"zzz_no_such_language": 1})
            self.assertEqual(result["rungs"], {"text": 1})


if __name__ == "__main__":
    unittest.main()
