"""Retrieval calibration: end-to-end answer() + render for TS evidence.

Covers the path the extraction tests do not: a bare-name question must
surface verified enclosed-callback callers as OBSERVED render lines, and
an unknown bare name must abstain without inventing edges.
"""

import tempfile
import unittest
from pathlib import Path

from tunic import tsgraph
from tunic.memory import Memory
from tunic.render import render_answer_text

PARSER_OR_SKIP = unittest.skipUnless(
    tsgraph._HAVE_PARSER,
    "tree-sitter parser not installed (pip install tunic-context[typescript])")

FILES = {
    "src/content.ts": """export const releaseHref = (id: string) => `/music/${id}`;
""",
    "src/feed.ts": """import { releaseHref } from "./content";
export function latestFeed(ids: string[]) {
  return ids.map((id) => releaseHref(id));
}
""",
    "src/entry.ts": """import { releaseHref } from "./content";
export const GET = async (ids: string[]) => {
  const rows = ids.map((id) => releaseHref(id));
  return rows;
};
""",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    for name, text in FILES.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "calib.sqlite"), str(work))
    memory.ingest()
    return memory


@PARSER_OR_SKIP
class RetrievalCalibrationTest(unittest.TestCase):
    def test_bare_name_surfaces_verified_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("releaseHref")
                self.assertEqual(result.get("outcome"), "complete")
                self.assertIsNone(result.get("abstention"))
                observed = {
                    entry["symbol"] for entry in result.get("impacts", [])
                    if (entry.get("derivation") or {}).get("verification")
                    == "syntactic_path_only"}
                self.assertEqual(observed, {
                    "src/feed.ts::latestFeed", "src/entry.ts::GET"})
                text = render_answer_text(result)
                self.assertIn("OBSERVED", text)
                self.assertIn("src/feed.ts::latestFeed", text)
                self.assertIn("src/entry.ts::GET", text)
            finally:
                memory.close()

    def test_unknown_bare_name_abstains_without_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("noSuchHelper")
                self.assertEqual(result.get("outcome"), "empty")
                self.assertIsNotNone(result.get("abstention"))
                self.assertEqual(result.get("impacts"), [])
                text = render_answer_text(result)
                observed_block = text.split(
                    "OBSERVED DEPENDENCIES")[1].split("ASSERTED")[0]
                self.assertNotIn("src/", observed_block)
            finally:
                memory.close()
