"""Signal-aware mention ranking: failing-first contract.

Default-budget retrieval must surface the strongest evidence first:
stage-1 document recall wide enough that the best section can compete,
sections ordered by score then query-term coverage with explicit
signals, and mentions tiered by evidence strength (exact identity >
signaled observation > query-matched lexical > unsignaled ambiguity).
Nothing is promoted to asserted edges; the zero-signal filing guard
holds; code retrieval is unchanged.
"""

import tempfile
import unittest
from pathlib import Path

from tunic.extract import split_doc_sections
from tunic.memory import Memory

NOISE_MD = """# Reference

""" + "\n".join(
    "The rate of incoming requests keeps limiting throughput; "
    "configured retries apply to the rate limiter."
    for _ in range(12)
)

AAA_MD = """Call onRequest() to begin.

Filler paragraph here.

See src/app.ts::onRequest for details.
"""

GOLD_MD = """# Security baseline

## Rate limiting

API rate limiting is configured in `src/middleware.ts`.
"""

GUIDE_MD = """# Guide

See `src/app.ts::onRequest` for the entry point. The onRequest
handler wraps every route; onRequest is idempotent. Use pick() to choose.
"""

APP_TS = """export const onRequest = defineMiddleware(async ({ locals }, next) => {
  return next();
});
"""

IDS_TS = """export const id = "x";
"""

PICK_A_TS = """export function pick(items) {
  return items[0];
}
"""

PICK_B_TS = """export function pick(names) {
  return names[0];
}
"""


def _build_ranking(root):
    root = Path(root)
    (root / "vendored").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    for index in range(4):
        (root / "vendored" / ("noise%d.md" % index)).write_text(
            NOISE_MD, encoding="utf-8")
    for index in range(20):
        (root / "fillers" / ("filler%d.md" % index)).parent.mkdir(
            parents=True, exist_ok=True)
        (root / "fillers" / ("filler%d.md" % index)).write_text(
            "Filler text number %d, unrelated.\n" % index, encoding="utf-8")
    (root / "docs" / "security.md").write_text(GOLD_MD, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(GUIDE_MD, encoding="utf-8")
    (root / "aaa.md").write_text(AAA_MD, encoding="utf-8")
    (root / "src" / "app.ts").write_text(APP_TS, encoding="utf-8")
    (root / "src" / "ids.ts").write_text(IDS_TS, encoding="utf-8")
    (root / "src" / "a.ts").write_text(PICK_A_TS, encoding="utf-8")
    (root / "src" / "b.ts").write_text(PICK_B_TS, encoding="utf-8")


def _ingest(tmp, **kwargs):
    work = Path(tmp) / "ws"
    _build_ranking(work)
    memory = Memory(str(Path(tmp) / "rank.sqlite"), str(work))
    memory.ingest(**kwargs)
    return memory


class DocumentRankingTest(unittest.TestCase):
    def test_gold_doc_reaches_default_top3(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("where is rate limiting configured",
                                       select=True)
                docs = [d["doc"] for d in result.get("documents", [])]
                self.assertIn("docs/security.md", docs[:3],
                              "gold doc crowded out of default top-3: %r"
                              % (docs,))
            finally:
                memory.close()

    def test_passages_carry_signals_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("where is rate limiting configured",
                                       select=True)
                docs = result.get("documents", [])
                self.assertTrue(docs)
                for passage in docs:
                    self.assertIn("signals", passage)
                    self.assertIn("coverage", passage)
                    self.assertGreaterEqual(passage["coverage"], 0.0)
                    self.assertLessEqual(passage["coverage"], 1.0)
            finally:
                memory.close()

    def test_code_candidates_unchanged_by_doc_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("where is onRequest defined",
                                       select=True,
                                       budget={"documents": 0})
                capped = memory.answer("where is onRequest defined",
                                       select=True)
                self.assertEqual(
                    [c["symbol"] for c in result["candidates"]],
                    [c["symbol"] for c in capped["candidates"]])
            finally:
                memory.close()

    def test_repeated_answer_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                first = memory.answer("where is rate limiting configured",
                                      select=True)
                second = memory.answer("where is rate limiting configured",
                                       select=True)
                self.assertEqual(
                    [(d["doc"], d.get("section")) for d in first["documents"]],
                    [(d["doc"], d.get("section"))
                     for d in second["documents"]])
            finally:
                memory.close()


class MentionTierTest(unittest.TestCase):
    def test_exact_identity_outranks_bare_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("onRequest entry point handler",
                                       select=True)
                mentions = result.get("mentions", [])
                kinds = [(m.get("mentioner"), m.get("candidate"),
                          m.get("status")) for m in mentions]
                exact = [i for i, (_m, _c, s) in enumerate(kinds)
                         if s == "syntactic_observation"]
                bare = [i for i, (_m, _c, s) in enumerate(kinds)
                        if s in ("proposed", "proposed_ambiguous")]
                self.assertTrue(exact, "no exact mention found: %r" % (kinds,))
                if bare:
                    self.assertLess(min(exact), min(bare),
                                    "bare mention outranks exact: %r" % (kinds,))
            finally:
                memory.close()

    def test_fenced_headings_do_not_split(self):
        body = ("# Real\n\ntext\n\n```\n# not a heading\ncode\n```\n\n"
                "more text\n").encode("utf-8")
        sections = split_doc_sections("docs/a.md", body)
        self.assertEqual([s for s, _s, _e in sections], ["real"])

    def test_stale_passage_digest_changes_on_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_ranking(work)
            memory = Memory(str(Path(tmp) / "r.sqlite"), str(work))
            memory.ingest()
            try:
                first = memory.answer("where is rate limiting configured",
                                      select=True)
                gold = [d for d in first.get("documents", [])
                        if d["doc"] == "docs/security.md"]
                self.assertTrue(gold)
                before = gold[0]["digest"]
                path = work / "docs" / "security.md"
                path.write_text(path.read_text(encoding="utf-8")
                                + "\nExtra quota notes.\n", encoding="utf-8")
                memory.refresh()
                second = memory.answer("where is rate limiting configured",
                                       select=True)
                gold2 = [d for d in second.get("documents", [])
                         if d["doc"] == "docs/security.md"]
                self.assertTrue(gold2)
                self.assertNotEqual(before, gold2[0]["digest"])
            finally:
                memory.close()


class AdversarialTest(unittest.TestCase):
    def test_short_identifier_exact_still_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("where is src/ids.ts::id defined",
                                       select=True)
                self.assertIn("src/ids.ts::id",
                              [c["symbol"] for c in result["candidates"]])
                vague = memory.answer("what is the id", select=True)
                self.assertIn(vague.get("outcome"), ("complete", "empty"))
            finally:
                memory.close()

    def test_conflicting_alias_stays_ambiguous_and_ranked(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer("which pick helper to use",
                                       select=True)
                mentions = result.get("mentions", [])
                ambiguous = [m for m in mentions
                             if m.get("status") == "proposed_ambiguous"]
                self.assertTrue(ambiguous,
                                "conflicting alias collapsed: %r" % (mentions,))
            finally:
                memory.close()

    def test_named_doc_does_not_outrank_better_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = memory.answer(
                    "docs/security.md entry point handler", select=True)
                docs = result.get("documents", [])
                self.assertTrue(any(d["doc"] == "docs/security.md"
                                    for d in docs),
                                "named doc missing entirely")
                self.assertEqual(docs[0]["doc"], "docs/guide.md",
                                 "exact name outranked stronger evidence")
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
