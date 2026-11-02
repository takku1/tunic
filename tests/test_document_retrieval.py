"""R2 first-class document retrieval: failing-first contract.

Documents participate in retrieval as OBSERVED prose with byte-exact
citations, never as synthetic code symbols or asserted edges. Sections
are deterministic (path + slug + span + source digest + snapshot).
"""

import os
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory
from tunic.render import render_answer_text

README_MD = """# Deploy

## Rate limiting

API rate limiting is configured in `src/middleware.ts` (see wrangler.jsonc).

## Database

Postgres via DATABASE_URL.
"""

ARCH_MD = """# Architecture

## Request lifecycle

Requests flow through middleware then routes.

## Request lifecycle

Duplicate heading: retries and timeouts.
"""

STALE_MD = """# Old process

Deploy used to run on Fridays. This is stale but citable.
"""

NOTES_TXT = """Grocery list: eggs, milk, bread. Nothing to do with code.
"""

APP_TS = """export const onRequest = defineMiddleware(async ({ locals }, next) => {
  return next();
});
"""

SECRET_MD = """s3cr3t token configuration lives here and nowhere else.
"""


def _build_docs(root, secret=True):
    root = Path(root)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "README.md").write_text(README_MD, encoding="utf-8")
    (root / "docs" / "ARCH.md").write_text(ARCH_MD, encoding="utf-8")
    (root / "docs" / "STALE.md").write_text(STALE_MD, encoding="utf-8")
    (root / "docs" / "notes.txt").write_text(NOTES_TXT, encoding="utf-8")
    (root / "src" / "app.ts").write_text(APP_TS, encoding="utf-8")
    if secret:
        (root / "excluded").mkdir(parents=True, exist_ok=True)
        (root / "excluded" / "secret.md").write_text(SECRET_MD,
                                                     encoding="utf-8")


def _ingest(tmp, **kwargs):
    work = Path(tmp) / "ws"
    _build_docs(work)
    memory = Memory(str(Path(tmp) / "docs.sqlite"), str(work))
    memory.ingest(**kwargs)
    return memory


class DocumentExactTest(unittest.TestCase):
    def test_exact_document_key_retrieves(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer(
                    "Summarize docs/README.md::document", select=True)
                paths = [d["doc"] for d in result.get("documents", [])]
                self.assertIn("docs/README.md", paths)
            finally:
                memory.close()

    def test_heading_anchor_scopes_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer(
                    "What does docs/README.md::rate-limiting say?",
                    select=True)
                docs = result.get("documents", [])
                self.assertTrue(docs)
                self.assertEqual(docs[0]["doc"], "docs/README.md")
                self.assertEqual(docs[0]["section"], "rate-limiting")
            finally:
                memory.close()


class DocumentProseTest(unittest.TestCase):
    def test_prose_query_returns_bounded_cited_passage(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                before = len(memory.store.view()[1]["assertions"])
                result = memory.answer("where is rate limiting configured",
                                       select=True)
                docs = result.get("documents", [])
                self.assertTrue(docs, "prose answer missing")
                top = docs[0]
                self.assertEqual(top["doc"], "docs/README.md")
                self.assertEqual(top["section"], "rate-limiting")
                for field in ("span", "digest", "snapshot", "score",
                              "excerpt"):
                    self.assertIn(field, top)
                self.assertLess(len(top["excerpt"]),
                                len(README_MD),
                                "passage must be bounded, not the whole doc")
                self.assertIn("middleware", top["excerpt"])
                after = len(memory.store.view()[1]["assertions"])
                self.assertEqual(before, after,
                                 "retrieval invented graph edges")
            finally:
                memory.close()

    def test_duplicate_headings_disambiguated(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("request lifecycle retries timeouts",
                                       select=True)
                slugs = [(d["doc"], d["section"], tuple(d["span"]))
                         for d in result.get("documents", [])
                         if d["doc"] == "docs/ARCH.md"]
                self.assertGreaterEqual(len(set(slugs)), 2,
                                        "duplicate headings collapsed: %r"
                                        % (slugs,))
            finally:
                memory.close()

    def test_docs_only_outcome_complete_without_abstention(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            (work / "docs").mkdir(parents=True)
            (work / "docs" / "README.md").write_text(README_MD,
                                                     encoding="utf-8")
            memory = Memory(str(Path(tmp) / "d.sqlite"), str(work))
            memory.ingest()
            try:
                result = memory.answer("where is rate limiting configured",
                                       select=True)
                self.assertTrue(result.get("documents"))
                self.assertEqual(result.get("outcome"), "complete")
                self.assertIsNone(result.get("abstention"))
            finally:
                memory.close()


class DocumentLifecycleTest(unittest.TestCase):
    def test_rename_refresh_moves_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                os.rename(Path(tmp) / "ws" / "docs" / "ARCH.md",
                          Path(tmp) / "ws" / "docs" / "ARCH2.md")
                memory.refresh()
                gone = memory.answer("docs/ARCH.md::document", select=True)
                self.assertFalse([d for d in gone.get("documents", [])
                                  if d["doc"] == "docs/ARCH.md"])
                found = memory.answer("request lifecycle retries timeouts",
                                      select=True)
                self.assertTrue([d for d in found.get("documents", [])
                                 if d["doc"] == "docs/ARCH2.md"])
            finally:
                memory.close()

    def test_excluded_docs_never_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp, exclude=["excluded"])
            try:
                result = memory.answer("s3cr3t token configuration",
                                       select=True)
                leaked = [d for d in result.get("documents", [])
                          if d["doc"].startswith("excluded/")]
                self.assertEqual(leaked, [])
            finally:
                memory.close()


class DocumentRegressionTest(unittest.TestCase):
    def test_code_retrieval_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("where is onRequest defined",
                                       select=True)
                self.assertIn("src/app.ts::onRequest",
                              [c["symbol"] for c in result["candidates"]])
            finally:
                memory.close()

    def test_documents_budget_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("the code and docs",
                                       budget={"documents": 1})
                self.assertLessEqual(len(result.get("documents", [])), 1)
                with self.assertRaises(ValueError):
                    memory.answer("the code and docs",
                                  budget={"nope": 1})
            finally:
                memory.close()

    def test_render_labels_documents_as_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("where is rate limiting configured",
                                       select=True)
                text = render_answer_text(result)
                self.assertIn("DOCUMENTS", text)
                self.assertIn("docs/README.md", text)
                self.assertIn("TRUST RULE", text)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
