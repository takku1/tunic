"""Memory lifecycle v1: consolidation + forgetting safety acceptance.

Consolidation (C1-C7) is additive: derived support summaries compact
redundant corroboration with exact source links but never enter the
answer path, supports arithmetic, traversal, or timeline evidence.
Forgetting (F1-F8) is reversible policy-governed retrieval
suppression: suppressed assertions stay stored but contribute to no
traversal, selection, rendering, or successor reasoning, with
metadata-only audit tombstones.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.lifecycle import (consolidate, summarize_supports, suppress,
                             unsuppress, review_suppressions)
from tunic.memory import Memory

FIXTURE = Path(__file__).resolve().parents[1] / "benchmarks" / "agent" / "lifecycle"

NOTE_1 = """# Lifecycle audit 01 (2026-11-02)

```tunic-claims
[{"rel": "depends_on", "subject": "svc.py::dispatch", "object": "svc.py::legacy_worker"},
 {"rel": "supersedes", "subject": "svc.py::new_handler", "object": "svc.py::old_handler"}]
```

Audit: `dispatch` routes legacy traffic to `legacy_worker` (dynamic
table, verified). `old_handler` retired; use `new_handler`.
"""

NOTE_2 = """# Lifecycle audit 02 (2026-11-02)

```tunic-claims
[{"rel": "depends_on", "subject": "svc.py::dispatch", "object": "svc.py::legacy_worker"},
 {"rel": "depends_on", "subject": "svc.py::route", "object": "svc.py::new_handler"}]
```

Re-audit: dispatch routing confirmed. `route` -> `new_handler`
confirmed by inspection.
"""

QUESTIONS = [
    "Which function does dispatch route legacy requests to?",
    "What should replace old_handler?",
    "Which functions break if new_handler changes?",
]


def _build_db(tmp, notes=(NOTE_1, NOTE_2)):
    work = Path(tmp) / "ws"
    shutil.copytree(str(FIXTURE), str(work))
    notes_dir = work / "notes"
    notes_dir.mkdir(exist_ok=True)
    for index, note in enumerate(notes):
        (notes_dir / ("audit-%02d.md" % index)).write_text(
            note, encoding="utf-8")
    db_path = str(Path(tmp) / "life.sqlite")
    memory = Memory(db_path, str(work))
    memory.ingest()
    snapshot = memory.store.current()
    return memory, db_path, str(work), snapshot


def _answers(memory):
    return [memory.answer(task, select=True) for task in QUESTIONS]


def _rendered(results):
    return [json.dumps(_scrub(r), sort_keys=True) for r in results]


def _scrub(value):
    """Drop wall-clock timing fields; equivalence is about evidence."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if k not in ("ms", "elapsed_s", "wall_s", "elapsed_ms",
                             "duration_s", "setup_s")}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


class ConsolidationTest(unittest.TestCase):
    def test_groups_redundant_supports_with_source_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, snapshot = _build_db(tmp)
            try:
                report = consolidate(memory)
                self.assertEqual(report["snapshot"], snapshot)
                summaries = summarize_supports(
                    memory, snapshot=snapshot)["summaries"]
                by_key = {(s["proposition"]["relation"],
                           s["proposition"]["caller"],
                           s["proposition"]["callee"]): s
                          for s in summaries}
                key = ("depends_on", "svc.py::dispatch",
                       "svc.py::legacy_worker")
                self.assertIn(key, by_key)
                summary = by_key[key]
                self.assertEqual(summary["support_count"], 2)
                self.assertEqual(len(summary["inputs"]), 2)
                self.assertEqual(len(summary["sources"]), 2)
                self.assertEqual(summary["status"], "derived_consolidated")
            finally:
                memory.close()

    def test_skips_singletons_and_governance(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, snapshot = _build_db(tmp)
            try:
                consolidate(memory)
                summaries = summarize_supports(
                    memory, snapshot=snapshot)["summaries"]
                keys = {(s["proposition"]["relation"],
                         s["proposition"]["caller"],
                         s["proposition"]["callee"]) for s in summaries}
                self.assertNotIn(("depends_on", "svc.py::route",
                                  "svc.py::new_handler"), keys)
                self.assertNotIn(("supersedes", "svc.py::new_handler",
                                  "svc.py::old_handler"), keys)
            finally:
                memory.close()

    def test_answer_equivalence_before_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, _snapshot = _build_db(tmp)
            try:
                before = _rendered(_answers(memory))
                consolidate(memory)
                after = _rendered(_answers(memory))
                self.assertEqual(before, after)
            finally:
                memory.close()

    def test_idempotent_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, db_path, work, snapshot = _build_db(tmp)
            try:
                first = consolidate(memory)
                second = consolidate(memory)
                self.assertEqual(first, second)
                memory.close()
                reopened = Memory(db_path, work)
                try:
                    summaries = summarize_supports(
                        reopened, snapshot=snapshot)["summaries"]
                    self.assertTrue(summaries)
                finally:
                    reopened.close()
            finally:
                try:
                    memory.close()
                except Exception:
                    pass

    def test_stale_after_new_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, work, snapshot = _build_db(tmp)
            try:
                consolidate(memory)
                Path(work, "svc.py").write_text(
                    Path(work, "svc.py").read_text(encoding="utf-8")
                    + "\n\n\ndef extra():\n    return 1\n",
                    encoding="utf-8")
                memory.refresh()
                fresh = memory.store.current()
                self.assertNotEqual(fresh, snapshot)
                old = summarize_supports(memory, snapshot=snapshot)
                self.assertEqual(old["status"], "current")
                self.assertTrue(old["summaries"])
                result = summarize_supports(memory)
                self.assertEqual(result["status"], "stale")
                self.assertEqual(result["summaries"], [])
                recon = consolidate(memory)
                self.assertEqual(recon["snapshot"], fresh)
                self.assertEqual(
                    summarize_supports(memory)["status"], "current")
            finally:
                memory.close()

    def test_derived_rows_never_count_as_support(self):
        from tunic.selection import candidate_evidence
        with tempfile.TemporaryDirectory() as tmp:
            memory, db_path, _ws, _snapshot = _build_db(tmp)
            try:
                _snapshot, view = memory.store.view()
                before = candidate_evidence(
                    view, "svc.py::legacy_worker")
                consolidate(memory)
                _snapshot, fresh = memory.store.view()
                after = candidate_evidence(
                    fresh, "svc.py::legacy_worker")
                self.assertEqual(
                    _scrub(before), _scrub(after))
                self.assertNotIn(
                    "derived_consolidated",
                    {a.get("status") for a in fresh["assertions"]})
            finally:
                memory.close()


def _proposition_ids(memory, relation, caller, callee):
    from tunic.lifecycle import suppressed_ids
    hidden = suppressed_ids(memory.store)
    _snapshot, view = memory.store.view()
    return sorted(a["id"] for a in view["assertions"]
                  if a["id"] not in hidden
                  and a.get("status") == "asserted_in_source"
                  and (a.get("proposition") or {}).get("relation") == relation
                  and (a.get("proposition") or {}).get("caller") == caller
                  and (a.get("proposition") or {}).get("callee") == callee)


class SuppressionTest(unittest.TestCase):
    def test_suppressed_edge_invisible_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, _snapshot = _build_db(tmp)
            try:
                ids = _proposition_ids(memory, "depends_on",
                                       "svc.py::dispatch",
                                       "svc.py::legacy_worker")
                self.assertEqual(len(ids), 2)
                report = suppress(memory, ids, policy="governance-review",
                                  reason="re-audit pending")
                self.assertEqual(sorted(report["suppressed"]), ids)
                related = memory.related("svc.py::legacy_worker")
                callers = [e["symbol"]
                           for e in related["impact"]["impacts"]]
                self.assertNotIn("svc.py::dispatch", callers)
                claims = memory.claims("svc.py::legacy_worker")["claims"]
                self.assertFalse(
                    [c for c in claims
                     if c.get("caller") == "svc.py::dispatch"
                     and c.get("relation") == "depends_on"])
                timeline = memory.timeline("svc.py::legacy_worker")
                for entry in timeline["entries"]:
                    self.assertFalse(
                        entry.get("relation") == "depends_on"
                        and entry.get("caller") == "svc.py::dispatch"
                        and entry.get("callee") == "svc.py::legacy_worker",
                        "suppressed triple visible in timeline: %r" % (entry,))
                    self.assertFalse(
                        entry.get("kind") == "mention"
                        and (entry.get("evidence") or {}).get("path", "")
                        .startswith("notes/"),
                        "suppressed note mention visible: %r" % (entry,))
                static_left = [
                    e for e in timeline["entries"]
                    if e.get("caller") == "svc.py::entry"
                    and e.get("callee") == "svc.py::legacy_worker"]
                self.assertTrue(
                    static_left,
                    "independent static call-site observation must survive "
                    "suppression of the reviewer triple")
                answer = memory.answer(
                    "Which function does dispatch route legacy requests to?",
                    select=True)
                seen = ({c["symbol"] for c in answer["candidates"]} |
                        {i["symbol"] for i in answer["impacts"]})
                self.assertNotIn("svc.py::legacy_worker", seen)
            finally:
                memory.close()

    def test_audit_exposes_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, _snapshot = _build_db(tmp)
            try:
                ids = _proposition_ids(memory, "depends_on",
                                       "svc.py::dispatch",
                                       "svc.py::legacy_worker")
                suppress(memory, ids, policy="governance-review",
                         reason="re-audit pending")
                audit = review_suppressions(memory)
                self.assertEqual(len(audit["tombstones"]), 2)
                blob = json.dumps(audit, sort_keys=True)
                self.assertNotIn("svc.py::dispatch", blob)
                self.assertNotIn("svc.py::legacy_worker", blob)
                self.assertNotIn("depends_on", blob)
                for stone in audit["tombstones"]:
                    self.assertEqual(stone["policy"], "governance-review")
                    self.assertEqual(stone["reason"], "re-audit pending")
                    self.assertEqual(stone["status"], "active")
            finally:
                memory.close()

    def test_fail_closed_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, _snapshot = _build_db(tmp)
            try:
                live = _proposition_ids(memory, "depends_on",
                                        "svc.py::dispatch",
                                        "svc.py::legacy_worker")
                gov = _proposition_ids(memory, "supersedes",
                                       "svc.py::new_handler",
                                       "svc.py::old_handler")
                self.assertTrue(gov)
                with self.assertRaises(ValueError):
                    suppress(memory, ["assertion:nope"],
                             policy="governance-review", reason="x")
                with self.assertRaises(ValueError):
                    suppress(memory, live, policy="governance-review",
                             reason="")
                with self.assertRaises(ValueError):
                    suppress(memory, live, policy="", reason="x")
                with self.assertRaises(ValueError):
                    suppress(memory, gov, policy="governance-review",
                             reason="retire the retirement")
                self.assertEqual(review_suppressions(memory)["tombstones"],
                                 [])
                self.assertEqual(
                    _proposition_ids(memory, "depends_on",
                                     "svc.py::dispatch",
                                     "svc.py::legacy_worker"), live)
            finally:
                memory.close()

    def test_survives_refresh_reconsolidation_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, db_path, work, _snapshot = _build_db(tmp)
            try:
                ids = _proposition_ids(memory, "depends_on",
                                       "svc.py::dispatch",
                                       "svc.py::legacy_worker")
                suppress(memory, ids, policy="retention-expiry",
                         reason="stale routing")
                memory.refresh()
                consolidate(memory)
                callers = [e["symbol"] for e in
                           memory.related("svc.py::legacy_worker")
                           ["impact"]["impacts"]]
                self.assertNotIn("svc.py::dispatch", callers)
                memory.close()
                reopened = Memory(db_path, work)
                try:
                    callers = [e["symbol"] for e in
                               reopened.related("svc.py::legacy_worker")
                               ["impact"]["impacts"]]
                    self.assertNotIn("svc.py::dispatch", callers)
                finally:
                    reopened.close()
            finally:
                try:
                    memory.close()
                except Exception:
                    pass

    def test_edited_source_reasserts_new_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, work, _snapshot = _build_db(tmp)
            try:
                ids = _proposition_ids(memory, "depends_on",
                                       "svc.py::dispatch",
                                       "svc.py::legacy_worker")
                suppress(memory, ids, policy="governance-review",
                         reason="re-audit pending")
                note = Path(work, "notes", "audit-00.md")
                note.write_text(note.read_text(encoding="utf-8")
                                + "\nReconfirmed.\n", encoding="utf-8")
                memory.refresh()
                fresh = _proposition_ids(memory, "depends_on",
                                         "svc.py::dispatch",
                                         "svc.py::legacy_worker")
                self.assertTrue(fresh)
                self.assertTrue(set(fresh).isdisjoint(ids))
                callers = [e["symbol"] for e in
                           memory.related("svc.py::legacy_worker")
                           ["impact"]["impacts"]]
                self.assertIn("svc.py::dispatch", callers)
                stones = review_suppressions(memory)["tombstones"]
                self.assertEqual(len(stones), 2)
                self.assertTrue(all(s["status"] == "active"
                                    for s in stones))
            finally:
                memory.close()

    def test_unsuppress_restores_and_double_suppress_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, _snapshot = _build_db(tmp)
            try:
                ids = _proposition_ids(memory, "depends_on",
                                       "svc.py::dispatch",
                                       "svc.py::legacy_worker")
                suppress(memory, ids, policy="user-request",
                         reason="oops")
                again = suppress(memory, ids, policy="user-request",
                                 reason="oops")
                self.assertEqual(sorted(again["suppressed"]), ids)
                self.assertEqual(len(review_suppressions(memory)
                                     ["tombstones"]), 2)
                unsuppress(memory, ids, reason="restored after review")
                callers = [e["symbol"] for e in
                           memory.related("svc.py::legacy_worker")
                           ["impact"]["impacts"]]
                self.assertIn("svc.py::dispatch", callers)
                stones = review_suppressions(memory)["tombstones"]
                self.assertTrue(all(s["status"] == "reversed"
                                    for s in stones))
            finally:
                memory.close()

    def test_unrelated_answers_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _db, _ws, _snapshot = _build_db(tmp)
            try:
                before = _rendered(_answers(memory))
                ids = _proposition_ids(memory, "depends_on",
                                       "svc.py::dispatch",
                                       "svc.py::legacy_worker")
                suppress(memory, ids, policy="governance-review",
                         reason="re-audit pending")
                after = _rendered(_answers(memory))
                self.assertEqual(before[1], after[1])
                self.assertEqual(before[2], after[2])
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
