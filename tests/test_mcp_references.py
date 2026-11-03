"""MCP `references` tool: read-only textual reference discovery."""
import tempfile
import unittest
from pathlib import Path

from tunic.mcp import handle_call, tool_schemas
from tunic.memory import Memory

SITE = {
    "src/lib/content.ts": "export const releaseHref = (id) => id;\n",
    "src/pages/a.astro": ("---\nimport { releaseHref } from '../lib/content';\n---\n"
                          "<a href={releaseHref('x')}>y</a>\n"),
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


class McpReferencesTest(unittest.TestCase):
    def test_tool_listed_with_required_symbol(self):
        schemas = {s["name"]: s for s in tool_schemas()}
        self.assertIn("references", schemas)
        schema = schemas["references"]
        self.assertEqual(schema["inputSchema"]["required"], ["symbol"])
        self.assertIn("limit", schema["inputSchema"]["properties"])
        self.assertIn("extensions", schema["inputSchema"]["properties"])
        self.assertIn("max_bytes", schema["inputSchema"]["properties"])
        self.assertIn("not verified", schema["description"].lower())

    def test_call_returns_textual_kind_with_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                result = handle_call(
                    memory, "references",
                    {"symbol": "src/lib/content.ts::releaseHref"})
                self.assertEqual(result["kind"], "textual_reference")
                self.assertEqual(result["total"], 2)
                self.assertTrue(any("call edge" in lim
                                    for lim in result["limitations"]))
            finally:
                memory.close()

    def test_call_rejects_bad_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                with self.assertRaises(ValueError):
                    handle_call(memory, "references",
                                {"symbol": "src/lib/content.ts::releaseHref",
                                 "limit": "2"})
                with self.assertRaises(ValueError):
                    handle_call(memory, "references",
                                {"symbol": "src/lib/content.ts::releaseHref",
                                 "limit": -1})
                with self.assertRaises(ValueError):
                    handle_call(memory, "references",
                                {"symbol": "src/lib/content.ts::releaseHref",
                                 "extensions": ".astro"})
                with self.assertRaises(ValueError):
                    handle_call(memory, "references",
                                {"symbol": "src/lib/content.ts::releaseHref",
                                 "extensions": [".md"]})
                with self.assertRaises(ValueError):
                    handle_call(memory, "references",
                                {"symbol": "nope-missing"})
            finally:
                memory.close()

    def test_call_forbidden_principal_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                with self.assertRaises(PermissionError):
                    handle_call(memory, "references",
                                {"symbol": "src/lib/content.ts::releaseHref",
                                 "principal": "evil"})
            finally:
                memory.close()

    def test_call_no_graph_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                before = memory.store.view()[1]
                handle_call(memory, "references",
                            {"symbol": "src/lib/content.ts::releaseHref"})
                self.assertEqual(before, memory.store.view()[1])
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
