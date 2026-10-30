"""MCP stdio boundary: protocol, validation, confinement, end-to-end session."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
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
        self.assertEqual(names, ["impact", "note", "promote", "refresh",
                                 "related", "retrieve", "review", "timeline"])
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
        self.assertEqual(len(by_id[2]["result"]["tools"]), 8)
        payload = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(payload["outcome"], "complete")
        noted = json.loads(by_id[4]["result"]["content"][0]["text"])
        self.assertEqual(noted["outcome"], "appended")
        refreshed = json.loads(by_id[5]["result"]["content"][0]["text"])
        self.assertIn("snapshot", refreshed)
        self.assertTrue(by_id[6]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
