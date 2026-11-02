"""Tool-transcript replay adapter: deterministic replay + scoring.

Replays recorded agent-tool transcripts through the exact MCP dispatch
on fresh fixture sessions. No model, no network, dev fixtures only.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "benchmarks/agent"))

from replay_tool_ab_v1 import build_fixture, replay, run_transcript, score  # noqa: E402

sys.path.pop(0)

TRANSCRIPTS = REPO / "benchmarks" / "agent" / "transcripts"


def load(name):
    return json.loads((TRANSCRIPTS / name).read_text(encoding="utf-8"))


class ToolAdapterTests(unittest.TestCase):
    def replay(self, name):
        record = load(name)
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        memory, _spec = build_fixture(tmp, record["fixture"])
        self.addCleanup(memory.close)
        log = replay(memory, record)
        return record, score(record, log), log

    def test_constructor_acquire_passes(self):
        _record, result, _log = self.replay("constructor-acquire.json")
        self.assertTrue(result["correct"])
        self.assertTrue(result["grounded"])
        self.assertTrue(result["governed"])
        self.assertTrue(result["pass"])
        self.assertEqual(result["commits"], 1)

    def test_unknown_question_abstains_cleanly(self):
        _record, result, log = self.replay("unknown-abstain.json")
        self.assertTrue(result["pass"])
        self.assertEqual(result["commits"], 0)
        self.assertTrue(all(not e["refused"] for e in log))

    def test_governance_refusal_is_recorded_not_crashed(self):
        _record, result, log = self.replay("governance-refusal.json")
        refusals = [e for e in log if e["refused"]]
        self.assertEqual(len(refusals), 1)
        self.assertIn("not a live", refusals[0]["error"])
        self.assertTrue(result["refusals_clean"])
        self.assertTrue(result["pass"])

    def test_ungrounded_answer_fails(self):
        record = load("constructor-acquire.json")
        record["answer"] = {"symbols": ["shop.py::Nonexistent.fake"]}
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        memory, _spec = build_fixture(tmp, record["fixture"])
        self.addCleanup(memory.close)
        result = score(record, replay(memory, record))
        self.assertFalse(result["grounded"])
        self.assertFalse(result["pass"])

    def test_frozen_artifacts_untouched(self):
        before = sorted(p.name for p in
                        (REPO / "benchmarks" / "agent" / "runs").glob("*.json"))
        self.replay("constructor-acquire.json")
        after = sorted(p.name for p in
                       (REPO / "benchmarks" / "agent" / "runs").glob("*.json"))
        self.assertEqual(before, after)

    def test_run_transcript_cli_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = run_transcript(
                str(TRANSCRIPTS / "constructor-acquire.json"), tmp)
        self.assertTrue(record["score"]["pass"])
        self.assertEqual(record["transcript"], "constructor-acquire.json")
        self.assertTrue(record["log"])


class ReplayValidationTests(unittest.TestCase):
    """The adapter replays through the real MCP dispatch on isolated
    stores, records refusals and publish failures instead of crashing,
    and its scores reflect actual store state."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def load(self, name):
        return json.loads((TRANSCRIPTS / name).read_text(encoding="utf-8"))

    def committed_count(self, memory):
        _snap, view = memory.store.view()
        return sum(1 for a in view["assertions"]
                   if a.get("status") == "asserted_in_source"
                   and a["proposition"].get("caller") == "shop.py::create")

    def test_replay_uses_the_real_mcp_dispatch(self):
        import tunic.mcp
        import replay_tool_ab_v1
        self.assertIs(replay_tool_ab_v1.handle_call, tunic.mcp.handle_call)

    def test_stores_are_isolated_between_replays(self):
        record = self.load("constructor-acquire.json")
        first = build_fixture(self.tmp + "/one", record["fixture"])
        second = build_fixture(self.tmp + "/two", record["fixture"])
        try:
            log_one = replay(first[0], record)
            self.assertEqual(self.committed_count(first[0]), 1)
            self.assertEqual(self.committed_count(second[0]), 0)
            log_two = replay(second[0], record)
            self.assertTrue(score(record, log_two)["pass"])
        finally:
            first[0].close()
            second[0].close()

    def test_duplicate_replay_is_a_safe_reverification(self):
        record = self.load("constructor-acquire.json")
        memory, _spec = build_fixture(self.tmp, record["fixture"])
        self.addCleanup(memory.close)
        first = replay(memory, record)
        self.assertTrue(score(record, first)["pass"])
        second = replay(memory, record)
        # Idempotent by design: the repeat acquire returns a duplicate
        # receipt (not a refusal, not a second edge) and the committed
        # fact still answers, so re-verification passes.
        acquires = [e for e in second if e["tool"] == "acquire"]
        self.assertEqual(len(acquires), 1)
        self.assertFalse(acquires[0]["refused"])
        self.assertFalse(acquires[0]["output"]["committed"])
        self.assertEqual(acquires[0]["output"]["action"], "duplicate")
        self.assertTrue(score(record, second)["pass"])
        self.assertEqual(self.committed_count(memory), 1)

    def test_publish_failure_is_recorded_with_state(self):
        record = self.load("constructor-acquire.json")
        memory, _spec = build_fixture(self.tmp, record["fixture"])
        self.addCleanup(memory.close)
        with mock.patch.object(memory.store, "ingest",
                               side_effect=OSError("disk unavailable")):
            log = replay(memory, record)
        errors = [e for e in log if e["refused"]]
        self.assertTrue(errors)
        self.assertTrue(all("error" in e for e in errors))
        # Claim block written, nothing published: no asserted edge.
        self.assertEqual(self.committed_count(memory), 0)

    def test_scores_reflect_actual_store_state(self):
        record = self.load("constructor-acquire.json")
        memory, _spec = build_fixture(self.tmp, record["fixture"])
        self.addCleanup(memory.close)
        log = replay(memory, record)
        result = score(record, log)
        self.assertTrue(result["pass"])
        self.assertEqual(result["commits"], 1)
        self.assertEqual(self.committed_count(memory), result["commits"])
        # Refusal transcript commits nothing anywhere.
        refusal = self.load("governance-refusal.json")
        memory2, _s2 = build_fixture(self.tmp + "-two", refusal["fixture"])
        self.addCleanup(memory2.close)
        log2 = replay(memory2, refusal)
        self.assertTrue(score(refusal, log2)["pass"])
        self.assertEqual(self.committed_count(memory2), 0)


if __name__ == "__main__":
    unittest.main()
