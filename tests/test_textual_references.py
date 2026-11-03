"""Cross-language textual reference discovery (prototype, read-only)."""
import tempfile
import unittest
from pathlib import Path

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
""",
    "src/pages/plain.astro": """<p>nothing here</p>
""",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    for name, text in SITE.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
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
