"""Tool responses stay within a byte budget and account for every cut."""

import json
from pathlib import Path
import tempfile
import unittest

from tunic import bounded
from tunic.mcp import handle_request, tool_schemas
from tunic.memory import Memory


def rpc(name, arguments):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def body(response):
    return json.loads(response["result"]["content"][0]["text"])


class Bound(unittest.TestCase):
    BIG = {"snapshot": "abc", "outcome": "complete",
           "units": [{"symbol": "s%03d" % i, "support": ["x" * 400] * 20}
                     for i in range(300)],
           "diagnostics": [{"kind": "k", "path": "p%04d" % i} for i in range(5000)]}

    def test_a_small_result_passes_through_untouched(self):
        small = {"a": [1, 2], "b": "text"}
        self.assertIs(bounded.bound(small), small)

    def test_the_cap_is_on_serialized_bytes(self):
        for limit in (2048, 4096, 32768):
            result = bounded.bound(self.BIG, limit)
            self.assertLessEqual(bounded.serialized_size(result), limit)

    def test_non_ascii_cannot_escape_the_cap(self):
        result = bounded.bound({"text": "é" * 100000}, 4096)
        self.assertLessEqual(bounded.serialized_size(result), 4096)

    def test_small_fields_survive_beside_large_ones(self):
        result = bounded.bound(self.BIG, 8192)
        self.assertEqual(result["snapshot"], "abc")
        self.assertEqual(result["outcome"], "complete")

    def test_lists_keep_order_and_breadth(self):
        result = bounded.bound(self.BIG, 32768)
        names = [unit["symbol"] for unit in result["units"]]
        self.assertEqual(names, ["s%03d" % i for i in range(len(names))])
        self.assertGreaterEqual(len(names), bounded.LIST_BREADTH // 2)

    def test_it_is_deterministic(self):
        first = json.dumps(bounded.bound(self.BIG, 8192), sort_keys=True)
        self.assertEqual(first, json.dumps(bounded.bound(self.BIG, 8192), sort_keys=True))

    def test_the_receipt_states_what_was_cut(self):
        receipt = bounded.bound(self.BIG, 8192)["_truncated"]
        self.assertEqual(receipt["full_bytes"], bounded.serialized_size(self.BIG))
        self.assertGreater(receipt["cuts"], 0)
        self.assertLessEqual(len(receipt["shown"]), bounded.MAX_RECEIPTS)
        top = receipt["shown"][0]
        self.assertLess(top["returned"], top["total"])

    def test_paging_a_cut_reaches_everything(self):
        items = {"items": ["item-%05d" % i for i in range(5000)]}
        seen, offset = [], 0
        while offset < 5000:
            part = bounded.page(items, ["items"], offset, 4096)
            self.assertLessEqual(bounded.serialized_size(part), 4096)
            self.assertEqual(part["total"], 5000)
            seen.extend(part["value"])
            offset = len(seen)
        self.assertEqual(seen, items["items"])

    def test_a_bad_path_is_an_error_not_an_empty_page(self):
        with self.assertRaises(ValueError):
            bounded.page({"a": []}, ["missing"], 0)

    def test_a_top_level_list_is_bounded(self):
        result = bounded.bound(["x" * 100] * 1000, 4096)
        self.assertIn("result", result)
        self.assertIn("_truncated", result)


class Boundary(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.addCleanup(self.data.cleanup)
        root = Path(self.root.name)
        (root / "core.py").write_text("def target():\n    return 1\n", encoding="utf-8")
        for i in range(60):
            (root / ("m%02d.py" % i)).write_text(
                "from core import target\n\ndef caller_%d():\n    return target()\n" % i,
                encoding="utf-8")
        self.memory = Memory(str(Path(self.data.name) / "m.sqlite"), self.root.name)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def call(self, name, **arguments):
        return handle_request(self.memory, rpc(name, arguments))

    def test_every_response_obeys_the_limit(self):
        response = self.call("impact", symbol="core.py::target", max_response_bytes=4096)
        text = response["result"]["content"][0]["text"]
        self.assertLessEqual(len(text), 4096)
        self.assertIn("_truncated", json.loads(text))

    def test_a_cut_field_can_be_continued(self):
        first = body(self.call("impact", symbol="core.py::target", max_response_bytes=4096))
        cut = first["_truncated"]["shown"][0]
        more = body(self.call("impact", symbol="core.py::target",
                              continue_path=cut["continue_path"],
                              continue_offset=cut["continue_offset"]))
        self.assertEqual(more["continue_path"], cut["continue_path"])
        self.assertEqual(more["total"], cut["total"])

    def test_the_limit_is_range_checked(self):
        for value in (1, 10 ** 9, "big"):
            response = self.call("coverage", max_response_bytes=value)
            self.assertTrue(response["result"]["isError"])

    def test_tools_with_strict_arguments_still_accept_the_limit(self):
        response = self.call("candidates", max_response_bytes=4096)
        self.assertFalse(response["result"]["isError"])

    def test_every_tool_advertises_the_limit(self):
        for tool in tool_schemas():
            self.assertIn("max_response_bytes", tool["inputSchema"]["properties"], tool["name"])


if __name__ == "__main__":
    unittest.main()
