"""Cross-language textual reference discovery (read-only)."""
import tempfile
import unittest
from pathlib import Path

from tunic.identity import digest
from tunic.memory import Memory

SITE = {
    "src/lib/content.ts": """export const releaseHref = (id) => `/music/${id}`;
export function latestFeed(ids) {
  return ids.map((id) => releaseHref(id));
}
""",
    "src/pages/index.astro": """---
import { releaseHref } from '../../lib/content';
const items = [{ slug: 'a' }];
---
<ul>
{items.map((r) => <li><a href={releaseHref(r.slug)}>x</a></li>)}
</ul>
""",
    "src/pages/other.astro": """---
import { releaseHref, latestFeed } from '../../lib/content';
---
<a href={releaseHref('b')}>y</a>
<!-- releaseHref mentioned in a comment -->
<p>{releaseHrefExtra}</p>
<p>{x$releaseHref} {$releaseHref} {\u00e9releaseHref} {releaseHref\u00e9}</p>
""",
    "src/pages/plain.astro": """<p>nothing here</p>
""",
    "src/pages/bad.astro": b"---\n\xff\xfe not utf-8 releaseHref\n",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    for name, payload in SITE.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            dest.write_bytes(payload)
        else:
            dest.write_text(payload, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "ref.sqlite"), str(work))
    memory.ingest()
    return memory


class TextualReferenceTest(unittest.TestCase):
    def test_finds_astro_occurrences_with_spans(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref")
                self.assertEqual(result["kind"], "textual_reference")
                paths = sorted(r["path"] for r in result["references"])
                self.assertEqual(paths, ["src/pages/index.astro",
                                         "src/pages/index.astro",
                                         "src/pages/other.astro",
                                         "src/pages/other.astro",
                                         "src/pages/other.astro"])
                for ref in result["references"]:
                    raw = (Path(tmp) / "ws" / ref["path"]
                           ).read_bytes()[ref["start"]:ref["end"]]
                    self.assertEqual(raw, b"releaseHref")
                    self.assertEqual(ref["kind"], "textual_reference")
                    self.assertIn("releaseHref", ref["context"])
                self.assertFalse(result["truncated"])
                self.assertEqual(result["total"], 5)
            finally:
                memory.close()

    def test_exact_boundaries_exclude_longer_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref")
                contexts = [r["context"] for r in result["references"]]
                self.assertFalse(any("releaseHrefExtra" in c
                                     for c in contexts))
            finally:
                memory.close()

    def test_dollar_sign_is_an_identifier_character(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref")
                self.assertEqual(result["total"], 5)
                contexts = [r["context"]
                            for r in result["references"]]
                self.assertFalse(any("x$releaseHref" in c or
                                     "{$releaseHref}" in c
                                     for c in contexts))
            finally:
                memory.close()

    def test_unicode_adjacent_characters_are_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref")
                self.assertEqual(result["total"], 5)
                for ref in result["references"]:
                    raw = (Path(tmp) / "ws" / ref["path"]
                           ).read_bytes()[ref["start"]:ref["end"]]
                    self.assertEqual(raw, b"releaseHref")
            finally:
                memory.close()

    def test_undecodable_file_skipped_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref")
                self.assertEqual(result["skipped_undecodable"], 1)
                self.assertTrue(result["complete"])
                self.assertFalse(any(r["path"].endswith("bad.astro")
                                     for r in result["references"]))
            finally:
                memory.close()

    def test_limit_bounds_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref", limit=1)
                self.assertEqual(len(result["references"]), 1)
                self.assertTrue(result["truncated"])
                self.assertEqual(result["total"], 5)
            finally:
                memory.close()

    def test_limit_validation_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                for bad in (True, False, -1, 0, 201, 10**6, "20"):
                    with self.assertRaises(ValueError, msg=repr(bad)):
                        memory.references(
                            "src/lib/content.ts::releaseHref",
                            limit=bad)
            finally:
                memory.close()

    def test_extension_validation_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                for bad in (".md", ".txt", ".rst", ".zzz", "astro",
                            "", (), [], (".astro", 7), (".astro", "")):
                    with self.assertRaises(ValueError, msg=repr(bad)):
                        memory.references(
                            "src/lib/content.ts::releaseHref",
                            extensions=bad if isinstance(bad, (list, tuple))
                            else [bad] if bad != () and bad != [] else bad)
                with self.assertRaises(ValueError):
                    memory.references("src/lib/content.ts::releaseHref",
                                      extensions=".astro")
            finally:
                memory.close()

    def test_extension_case_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref",
                    extensions=(".ASTRO",))
                self.assertEqual(result["total"], 5)
                self.assertEqual(result["extensions"], [".astro"])
            finally:
                memory.close()

    def test_work_budget_validation_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                for kwargs in ({"max_bytes": True}, {"max_bytes": 0},
                               {"max_bytes": -1},
                               {"max_bytes": 2 ** 26 + 1},
                               {"max_files": False}, {"max_files": 0},
                               {"max_files": -3}):
                    with self.assertRaises(ValueError, msg=repr(kwargs)):
                        memory.references(
                            "src/lib/content.ts::releaseHref", **kwargs)
            finally:
                memory.close()

    def test_scan_budget_stops_early_with_unknown_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref", max_bytes=10)
                self.assertFalse(result["complete"])
                self.assertIsNone(result["total"])
                self.assertTrue(result["truncated"])
                self.assertEqual(result["references"], [])
                self.assertEqual(result["scanned_files"], 0)
            finally:
                memory.close()

    def test_max_files_stops_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref", max_files=1)
                self.assertFalse(result["complete"])
                self.assertIsNone(result["total"])
                self.assertTrue(result["truncated"])
                self.assertEqual(result["scanned_files"], 1)
            finally:
                memory.close()

    def test_budgets_reported_on_complete_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref")
                self.assertTrue(result["complete"])
                self.assertEqual(result["scanned_files"], 4)
                self.assertGreater(result["scanned_bytes"], 0)
                self.assertEqual(result["budgets"]["max_bytes"], 4_000_000)
                self.assertEqual(result["budgets"]["max_files"], 5_000)
            finally:
                memory.close()

    def test_rows_carry_source_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references(
                    "src/lib/content.ts::releaseHref")
                by_path = {}
                for ref in result["references"]:
                    raw = (Path(tmp) / "ws" / ref["path"]).read_bytes()
                    self.assertEqual(ref["digest"], digest(raw))
                    by_path.setdefault(ref["path"], set()).add(
                        ref["digest"])
                self.assertTrue(all(len(v) == 1 for v in by_path.values()))
            finally:
                memory.close()

    def test_bare_name_resolves_unique_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.references("releaseHref")
                self.assertEqual(result["symbol"],
                                 "src/lib/content.ts::releaseHref")
            finally:
                memory.close()

    def test_unknown_or_ambiguous_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                with self.assertRaises(ValueError):
                    memory.references("nope-missing")
            finally:
                memory.close()

    def test_no_graph_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                before = memory.store.view()[1]
                memory.references(
                    "src/lib/content.ts::releaseHref")
                after = memory.store.view()[1]
                self.assertEqual(before, after)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
