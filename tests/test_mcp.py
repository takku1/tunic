"""MCP stdio boundary: protocol, validation, confinement, end-to-end session."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from tunic.mcp import handle_request
from tunic.memory import Memory


def _seed(root):
    (root / "core.py").write_bytes(b"def calculate(x):\n    return x + 1\n")
    (root / "notes.md").write_bytes(b"Uses `core.py::calculate` for totals.\n")


def _rpc(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _text(response):
    return json.loads(response["result"]["content"][0]["text"])


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        _seed(self.root)
        self.memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def test_initialize_and_list(self):
        response = handle_request(self.memory, _rpc("initialize"))
        self.assertEqual(response["result"]["serverInfo"]["name"], "tunic")
        self.assertIn("tools", response["result"]["capabilities"])
        response = handle_request(self.memory, _rpc("tools/list"))
        names = sorted(tool["name"] for tool in response["result"]["tools"])
        self.assertEqual(names, ["acquire", "callees", "candidates",
                                 "correct", "frontier", "impact", "note",
                                 "promote", "references", "refresh",
                                 "related",
                                 "retrieve", "review",
                                 "timeline"])
        for tool in response["result"]["tools"]:
            self.assertIn("inputSchema", tool)

    def test_ping_and_notifications(self):
        self.assertEqual(handle_request(self.memory, _rpc("ping"))["result"], {})
        self.assertIsNone(handle_request(self.memory, _rpc("notifications/initialized")))
        self.assertIsNone(handle_request(self.memory, {"jsonrpc": "2.0", "method": "notifications/x"}))

    def test_unknown_method_and_malformed(self):
        response = handle_request(self.memory, _rpc("tools/delete"))
        self.assertEqual(response["error"]["code"], -32601)
        self.assertEqual(handle_request(self.memory, {"nope": 1})["error"]["code"], -32600)

    def test_retrieve_round_trip(self):
        response = handle_request(self.memory, _rpc("tools/call", {
            "name": "retrieve", "arguments": {"task": "what breaks if calculate changes?"}}))
        self.assertFalse(response["result"]["isError"])
        payload = _text(response)
        self.assertEqual(payload["outcome"], "complete")
        self.assertTrue(any(row["symbol"] == "core.py::calculate"
                            for row in payload["candidates"]))

    def test_retrieve_rejects_bad_arguments(self):
        for args in ({"task": ""}, {"task": "x", "limit": -1},
                     {"task": "x", "policy": "vibes"}, {"task": "x", "threshold": -0.5},
                     {"task": "x", "limit": True}, "not-an-object"):
            response = handle_request(self.memory, _rpc("tools/call", {
                "name": "retrieve", "arguments": args}))
            self.assertTrue(response["result"]["isError"], msg=repr(args))
            self.assertIn("error", _text(response))

    def test_unknown_tool_and_missing_symbol(self):
        response = handle_request(self.memory, _rpc("tools/call", {"name": "teleport"}))
        self.assertTrue(response["result"]["isError"])
        response = handle_request(self.memory, _rpc("tools/call", {
            "name": "related", "arguments": {}}))
        self.assertTrue(response["result"]["isError"])

    def test_note_confined_to_workspace(self):
        response = handle_request(self.memory, _rpc("tools/call", {
            "name": "note", "arguments": {"text": "hello", "document": "../evil.md"}}))
        self.assertTrue(response["result"]["isError"])
        response = handle_request(self.memory, _rpc("tools/call", {
            "name": "note", "arguments": {"text": "hello", "document": "/abs/evil.md"}}))
        self.assertTrue(response["result"]["isError"])
        response = handle_request(self.memory, _rpc("tools/call", {
            "name": "note", "arguments": {"text": "hello"}}))
        self.assertFalse(response["result"]["isError"])
        self.assertTrue((self.root / "notes" / "session.md").exists())

    def test_promote_records_and_blocks_duplicates(self):
        args = {"subject": "notes.md::document", "object": "core.py::calculate",
                "rel": "depends_on"}
        first = handle_request(self.memory, _rpc("tools/call", {"name": "promote", "arguments": args}))
        self.assertFalse(first["result"]["isError"], msg=_text(first) if first["result"]["isError"] else "")
        self.memory.ingest()
        second = handle_request(self.memory, _rpc("tools/call", {"name": "promote", "arguments": args}))
        self.assertTrue(second["result"]["isError"])
        bad = handle_request(self.memory, _rpc("tools/call", {
            "name": "promote", "arguments": {"subject": "calculate", "object": "core.py::calculate"}}))
        self.assertTrue(bad["result"]["isError"])


class McpAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        _seed(self.root)
        self.memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def _call(self, name, arguments):
        return handle_request(self.memory, _rpc("tools/call", {
            "name": name, "arguments": arguments}))

    def test_unknown_principal_denied_on_read_and_write(self):
        for name, arguments in (
                ("retrieve", {"task": "x", "principal": "mallory"}),
                ("note", {"text": "x", "principal": "mallory"}),
                ("refresh", {"principal": "mallory"}),
                ("promote", {"subject": "a.py::b", "object": "c.py::d",
                             "principal": "mallory"})):
            denied = self._call(name, arguments)
            self.assertTrue(denied["result"]["isError"], name)
            self.assertIn("denied", _text(denied)["error"], name)

    def test_local_and_default_principals_allowed(self):
        ok = self._call("retrieve", {"task": "what breaks if calculate changes?",
                                     "principal": "local"})
        self.assertFalse(ok["result"]["isError"])
        also_ok = self._call("retrieve", {"task": "what breaks if calculate changes?"})
        self.assertFalse(also_ok["result"]["isError"])

    def test_memory_gate_rejects_unknown_principal(self):
        from tunic.memory import authorize
        receipt = authorize(None, "answer")
        self.assertEqual(receipt, {"principal": "local", "action": "answer",
                                  "decision": "allow"})
        with self.assertRaises(PermissionError):
            authorize("mallory", "answer")
        foreign = Memory(str(self.tmp / "foreign.sqlite"), self.root,
                         principal="mallory")
        self.addCleanup(foreign.close)
        with self.assertRaises(PermissionError):
            foreign.answer("x")
        with self.assertRaises(PermissionError):
            foreign.note("x")


class McpServerTests(unittest.TestCase):
    def test_stdio_session_end_to_end(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        root = tmp / "repo"
        root.mkdir()
        _seed(root)
        db = str(tmp / "mcp.sqlite")
        Memory(db, root).ingest()
        Memory(db, root).close()
        requests = [
            _rpc("initialize", {}, 1),
            _rpc("tools/list", {}, 2),
            _rpc("tools/call", {"name": "retrieve",
                                "arguments": {"task": "what breaks if calculate changes?"}}, 3),
            _rpc("tools/call", {"name": "note",
                                "arguments": {"text": "agent session note"}}, 4),
            _rpc("tools/call", {"name": "refresh", "arguments": {}}, 5),
            _rpc("tools/call", {"name": "nope", "arguments": {}}, 6),
        ]
        proc = subprocess.run(
            [sys.executable, "-m", "tunic", "--db", db, "mcp", "--root", str(root)],
            input="\n".join(json.dumps(message) for message in requests) + "\n",
            capture_output=True, text=True, timeout=180, cwd=str(Path.cwd()))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        by_id = {line["id"]: line for line in lines}
        self.assertEqual(len(by_id), 6)
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "tunic")
        self.assertEqual(len(by_id[2]["result"]["tools"]), 14)
        self.assertIn("callees",
                      {t["name"] for t in by_id[2]["result"]["tools"]})
        payload = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(payload["outcome"], "complete")
        noted = json.loads(by_id[4]["result"]["content"][0]["text"])
        self.assertEqual(noted["outcome"], "appended")
        refreshed = json.loads(by_id[5]["result"]["content"][0]["text"])
        self.assertIn("snapshot", refreshed)
        self.assertTrue(by_id[6]["result"]["isError"])


class McpAgentToolsTests(unittest.TestCase):
    """Agent integration tools: candidates, acquire, correct, frontier.

    Thin delegation only: listing reuses memory.candidates/frontier_list;
    acquire commits through the governed commit_candidate path; correct
    records a correction/retraction and publishes it. Deterministic,
    no model, dev-scale fixture.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "shop.py").write_text(
            "class Gadget:\n"
            "    def start(self):\n"
            "        return 1\n"
            "\n\n"
            "def create():\n"
            "    return Gadget()\n", encoding="utf-8")
        self.memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def call(self, name, arguments):
        response = handle_request(
            self.memory, _rpc("tools/call", {"name": name,
                                             "arguments": arguments}))
        self.assertFalse(response["result"]["isError"], msg=name)
        return _text(response)

    def test_tool_list_includes_agent_tools(self):
        response = handle_request(self.memory, _rpc("tools/list"))
        names = sorted(tool["name"] for tool in response["result"]["tools"])
        self.assertEqual(names, ["acquire", "callees", "candidates",
                                 "correct", "frontier", "impact", "note",
                                 "promote", "references", "refresh",
                                 "related",
                                 "retrieve", "review",
                                 "timeline"])

    def test_candidates_lists_live_proposals(self):
        payload = self.call("candidates", {})
        live = [c for c in payload["candidates"]
                if c.get("via") == "constructor_call"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["caller"], "shop.py::create")
        self.assertEqual(live[0]["callee"], "shop.py::Gadget")

    def test_acquire_commits_through_governed_path(self):
        listed = self.call("candidates", {})
        candidate = next(c for c in listed["candidates"]
                         if c.get("via") == "constructor_call")
        receipt = self.call("acquire", {
            "candidate_id": candidate["id"],
            "justification": "create constructs Gadget; start rides along",
            "task": "what breaks if start changes"})
        self.assertTrue(receipt["committed"])
        self.assertEqual(receipt["action"], "add")
        self.assertEqual(receipt["proposition"]["caller"],
                         "shop.py::create")
        # Published by the tool: post-acquire retrieval sees the edge.
        answer = self.call("retrieve", {"task": "what breaks if start changes"})
        self.assertTrue(any(e["symbol"] == "shop.py::create"
                            for e in answer["impacts"]))

    def test_acquire_requires_justification_and_live_id(self):
        listed = self.call("candidates", {})
        candidate = next(c for c in listed["candidates"]
                         if c.get("via") == "constructor_call")
        for args in ({"candidate_id": candidate["id"], "justification": ""},
                     {"candidate_id": "assertion:deadbeef",
                      "justification": "guessing"}):
            response = handle_request(
                self.memory, _rpc("tools/call", {"name": "acquire",
                                                 "arguments": args}))
            self.assertTrue(response["result"]["isError"], msg=repr(args))

    def test_correct_retracts_and_publishes(self):
        listed = self.call("candidates", {})
        candidate = next(c for c in listed["candidates"]
                         if c.get("via") == "constructor_call")
        self.call("acquire", {"candidate_id": candidate["id"],
                              "justification": "test selects gold",
                              "task": "t"})
        receipt = self.call("correct", {
            "subject": "shop.py::create", "object": "shop.py::Gadget",
            "rel": "retracts", "retract": "depends_on",
            "document": "notes/fix.md", "authority": "reviewer"})
        self.assertIn("snapshot", receipt)
        # Transparent policy keeps retracted history visible; the governed
        # current policy must not return the withdrawn caller.
        answer = self.call("retrieve", {"task": "what breaks if start changes",
                                        "policy": "current"})
        self.assertFalse(any(e["symbol"] == "shop.py::create"
                             for e in answer["impacts"]))

    def test_frontier_lists_open_gaps(self):
        payload = self.call("frontier", {})
        self.assertIn("entries", payload)
        self.assertTrue(all(e["status"] == "open" for e in payload["entries"]))
        filtered = self.call("frontier", {"status": "open"})
        self.assertEqual(len(filtered["entries"]), len(payload["entries"]))


class McpReviewHardeningTests(unittest.TestCase):
    """Review fixes: parity params, bounded frontier, idempotency,
    provenance, publish-failure semantics, correction validation."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        (self.root / "shop.py").write_text(
            "class Gadget:\n"
            "    def start(self):\n"
            "        return 1\n"
            "\n\n"
            "def create():\n"
            "    return Gadget()\n", encoding="utf-8")
        self.memory = Memory(str(self.tmp / "memory.sqlite"), self.root)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def call(self, name, arguments):
        response = handle_request(
            self.memory, _rpc("tools/call", {"name": name,
                                             "arguments": arguments}))
        return response

    def committed_edge_count(self):
        _snap, view = self.memory.store.view()
        return sum(1 for a in view["assertions"]
                   if a.get("status") == "asserted_in_source"
                   and a["proposition"].get("caller") == "shop.py::create"
                   and a["proposition"].get("callee") == "shop.py::Gadget"
                   and a["proposition"].get("relation") == "depends_on")

    def test_retrieve_parity_select_and_container_budget(self):
        listed = json.loads(self.call(
            "candidates", {})["result"]["content"][0]["text"])
        candidate = next(c for c in listed["candidates"]
                         if c.get("via") == "constructor_call")
        self.call("acquire", {"candidate_id": candidate["id"],
                              "justification": "parity probe",
                              "task": "t"})
        selected = json.loads(self.call(
            "retrieve", {"task": "what breaks if start changes",
                         "policy": "current",
                         "select": True})["result"]["content"][0]["text"])
        self.assertIn("selection", selected)
        self.assertTrue(any(e["symbol"] == "shop.py::create"
                            for e in selected["impacts"]))
        disabled = json.loads(self.call(
            "retrieve", {"task": "what breaks if start changes",
                         "policy": "current", "select": True,
                         "container_limit": 0})["result"]["content"][0]["text"])
        self.assertFalse(any(e["symbol"] == "shop.py::create"
                             for e in disabled["impacts"]))

    def test_retrieve_snapshot_pinning_and_rejection(self):
        snapshot = self.memory.store.current()
        pinned = json.loads(self.call(
            "retrieve", {"task": "what breaks if start changes",
                         "snapshot": snapshot})["result"]["content"][0]["text"])
        self.assertEqual(pinned["snapshot"], snapshot)
        bad = self.call("retrieve", {"task": "x", "snapshot": "snapshot:nope"})
        self.assertTrue(bad["result"]["isError"])

    def test_retrieve_rejects_bad_parity_arguments(self):
        for args in ({"task": "x", "select": "yes"},
                     {"task": "x", "select": 1},
                     {"task": "x", "container_limit": -1},
                     {"task": "x", "snapshot": 42}):
            response = self.call("retrieve", args)
            self.assertTrue(response["result"]["isError"], msg=repr(args))

    def test_retrieve_accepts_graph_budget_and_mmr(self):
        result = json.loads(self.call(
            "retrieve", {"task": "what breaks if start changes",
                         "budget": {"graph": 3},
                         "mmr_lambda": 0.7})["result"]["content"][0]["text"])
        self.assertEqual(result["mmr"], {"lambda": 0.7, "applied": True})
        self.assertIn("graph", result["stages"])
        self.assertFalse(result["stages"]["graph"]["skipped"])

    def test_retrieve_rejects_bad_graph_and_mmr_arguments(self):
        for args in ({"task": "x", "budget": [1]},
                     {"task": "x", "budget": "graph"},
                     {"task": "x", "mmr_lambda": -0.1},
                     {"task": "x", "mmr_lambda": 1.5},
                     {"task": "x", "mmr_lambda": "high"}):
            response = self.call("retrieve", args)
            self.assertTrue(response["result"]["isError"], msg=repr(args))

    def test_frontier_limit_bounds_unfiltered_ledger(self):
        full = json.loads(self.call(
            "frontier", {})["result"]["content"][0]["text"])
        self.assertIn("total", full)
        self.assertEqual(full["total"], len(full["entries"]))
        bounded = json.loads(self.call(
            "frontier", {"limit": 1})["result"]["content"][0]["text"])
        self.assertEqual(len(bounded["entries"]), min(1, full["total"]))
        self.assertEqual(bounded["total"], full["total"])
        bad = self.call("frontier", {"limit": -1})
        self.assertTrue(bad["result"]["isError"])
        bad_status = self.call("frontier", {"status": "vibes"})
        self.assertTrue(bad_status["result"]["isError"])

    def test_acquire_is_idempotent_no_double_write(self):
        listed = json.loads(self.call(
            "candidates", {})["result"]["content"][0]["text"])
        candidate = next(c for c in listed["candidates"]
                         if c.get("via") == "constructor_call")
        first = json.loads(self.call(
            "acquire", {"candidate_id": candidate["id"],
                        "justification": "first",
                        "task": "t"})["result"]["content"][0]["text"])
        self.assertTrue(first["committed"])
        second = json.loads(self.call(
            "acquire", {"candidate_id": candidate["id"],
                        "justification": "second",
                        "task": "t"})["result"]["content"][0]["text"])
        self.assertFalse(second["committed"])
        self.assertEqual(second["action"], "duplicate")
        self.assertEqual(self.committed_edge_count(), 1)

    def test_acquire_records_provenance_visible_in_review(self):
        listed = json.loads(self.call(
            "candidates", {})["result"]["content"][0]["text"])
        candidate = next(c for c in listed["candidates"]
                         if c.get("via") == "constructor_call")
        receipt = json.loads(self.call(
            "acquire", {"candidate_id": candidate["id"],
                        "justification": "reviewed: constructs Gadget",
                        "task": "why"})["result"]["content"][0]["text"])
        self.assertIn("reviewed: constructs Gadget",
                      receipt.get("justification", ""))
        self.assertIn(candidate["id"], receipt.get("candidate_id", ""))
        review = json.loads(self.call(
            "review", {"document": "notes/proposed.md"}
            )["result"]["content"][0]["text"])
        self.assertIn("shop.py::create", json.dumps(review))
        self.assertIn("shop.py::Gadget", json.dumps(review))

    def test_publish_failure_is_loud_and_state_is_explicit(self):
        listed = json.loads(self.call(
            "candidates", {})["result"]["content"][0]["text"])
        candidate = next(c for c in listed["candidates"]
                         if c.get("via") == "constructor_call")
        with unittest.mock.patch.object(
                self.memory.store, "ingest",
                side_effect=OSError("disk unavailable")):
            response = self.call(
                "acquire", {"candidate_id": candidate["id"],
                            "justification": "doomed",
                            "task": "t"})
        self.assertTrue(response["result"]["isError"])
        # Two-phase semantics: the claim block is written, but nothing is
        # published -- a later refresh makes it visible, nothing is lost
        # silently and nothing is half-asserted.
        self.assertEqual(self.committed_edge_count(), 0)
        proposed = Path(self.root / "notes" / "proposed.md")
        self.assertTrue(proposed.is_file())
        self.assertIn("shop.py::create", proposed.read_text(encoding="utf-8"))

    def test_correct_rejects_unknown_relations(self):
        for args in ({"subject": "a", "object": "b", "rel": "vibes"},
                     {"subject": "a", "object": "b", "rel": "retracts"},
                     {"subject": "a", "object": "b"}):
            response = self.call("correct", args)
            self.assertTrue(response["result"]["isError"], msg=repr(args))


if __name__ == "__main__":
    unittest.main()
