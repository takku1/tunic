"""Inventory precision (§8 #1) + per-symbol coverage (§8 #4).

Failing-first: the harvester must capture TS/JS entry points
(middleware handlers, APIRoute exports, module constants, interface /
type / enum declarations, named default exports) while refusing to
promote function-local bindings to top-level symbols. Replies about
inventory-only symbols must say so instead of wearing the Python
profile; forward-call traversal over them is unsupported, not empty.
"""

import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory

SITE_TS = """import { defineMiddleware } from "astro:middleware";

export const onRequest = defineMiddleware(async ({ locals }, next) => {
  const url = new URL(request.url);
  const type = url.searchParams.get("type");
  const mod = await import("./mod.ts");
  const b = 1;
  return next();
});

export const POST: APIRoute = async ({ request }) => {
  const el = await request.json();
  return Response.json({ ok: true });
};

const RULES = ["a", "b"];
const DEFAULT_MAX_BODY = 1024;

export interface RouteRule {
  pattern: string;
}

export type Handler = (req: Request) => Response;

export enum Mode {
  Strict = "strict",
}

export default function namedHandler() {
  return true;
}

function helper(x) {
  const q = x + 1;
  return q;
}
"""

WORDS_TS = """export const data = ("seed");
export const catalog = ("seed");
"""

DOC_MD = """# Notes

The data shows the catalog grew. Plain words, no code spans.

The `data` export feeds the `catalog` export.
"""


def _ingest_site(tmp):
    work = Path(tmp) / "ws"
    (work / "src").mkdir(parents=True)
    (work / "docs").mkdir(parents=True)
    (work / "src" / "site.ts").write_text(SITE_TS, encoding="utf-8")
    (work / "src" / "words.ts").write_text(WORDS_TS, encoding="utf-8")
    (work / "docs" / "notes.md").write_text(DOC_MD, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "site.sqlite"), str(work))
    memory.ingest()
    return memory


class HarvestScopeTest(unittest.TestCase):
    def test_entry_points_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_site(tmp)
            try:
                _snapshot, view = memory.store.view()
                keys = {s["key"] for s in view["symbols"]}
                for expected in ("src/site.ts::onRequest",
                                 "src/site.ts::POST",
                                 "src/site.ts::RULES",
                                 "src/site.ts::DEFAULT_MAX_BODY",
                                 "src/site.ts::RouteRule",
                                 "src/site.ts::Handler",
                                 "src/site.ts::Mode",
                                 "src/site.ts::namedHandler",
                                 "src/site.ts::helper"):
                    self.assertIn(expected, keys)
            finally:
                memory.close()

    def test_locals_not_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_site(tmp)
            try:
                _snapshot, view = memory.store.view()
                keys = {s["key"] for s in view["symbols"]}
                for bogus in ("src/site.ts::url", "src/site.ts::type",
                              "src/site.ts::mod", "src/site.ts::b",
                              "src/site.ts::el", "src/site.ts::q"):
                    self.assertNotIn(bogus, keys)
            finally:
                memory.close()


class CandidateNoiseTest(unittest.TestCase):
    def test_zero_signal_inventory_words_not_filed(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_site(tmp)
            try:
                _snapshot, view = memory.store.view()
                cands = [d for d in view["diagnostics"]
                         if d.get("kind") == "mention_candidate"
                         and d.get("callee") in ("src/words.ts::data",
                                                 "src/words.ts::catalog")]
                self.assertEqual(
                    [c for c in cands if c.get("signals") == []], [],
                    "zero-signal inventory words filed as candidates: %r"
                    % (cands,))
                self.assertTrue(
                    any("code_span" in (c.get("signals") or []) for c in cands),
                    "backticked inventory mentions lost entirely: %r"
                    % (cands,))
            finally:
                memory.close()

    def test_requalify_drops_zero_signal_inventory_candidates(self):
        from tunic.extract import _requalify
        key = "src/words.ts::data"
        kwargs = dict(leaf="data", line="plain data here",
                      source_id="src", callee=key,
                      doc_freq={"data": 0.9}, exact_refs=set())
        self.assertFalse(_requalify(**kwargs),
                         "non-inventory verdict changed")
        self.assertTrue(_requalify(inventory_keys=frozenset({key}),
                                   **kwargs),
                        "zero-signal inventory candidate kept on refresh")


class CoverageLabelTest(unittest.TestCase):
    def test_related_reports_inventory_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_site(tmp)
            try:
                result = memory.related("src/site.ts::onRequest")
                self.assertEqual(result.get("coverage"), "inventory_only")
                receipt = result["impact"]["selection_receipt"]
                self.assertEqual(receipt.get("coverage"), "inventory_only")
                self.assertFalse(
                    any("Python profile" in line
                        for line in result["impact"]["limitations"]),
                    result["impact"]["limitations"])
            finally:
                memory.close()

    def test_callees_reports_unsupported_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_site(tmp)
            try:
                result = memory.callees("src/site.ts::onRequest")
                self.assertEqual(result.get("outcome"),
                                 "unsupported_capability")
                self.assertEqual(result.get("coverage"), "inventory_only")
                self.assertEqual(result.get("direct"), [])
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
