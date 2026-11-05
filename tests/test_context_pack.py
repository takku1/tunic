"""Context packs: one call an LLM can act on, with proof and hints kept apart."""

import json
from pathlib import Path
import tempfile
import unittest

from tunic import pack
from tunic.baselines import _subword_tokens
from tunic.mcp import handle_call
from tunic.memory import Memory
from tunic.compiler import compile_impact


def write_tree(root, files):
    for path, content in files.items():
        target = Path(root) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")


PYTHON = {
    "core.py": "def settle_invoice(amount):\n    return amount + 1\n",
    "service.py": "from core import settle_invoice\n\n"
                  "def checkout(cart):\n    return settle_invoice(cart)\n",
    "notes.md": "`core.py::settle_invoice` rounds amounts for checkout.\n",
}
ZIG = {
    "a.zig": "fn reconcile_ledger() void {}\n",
    "b.zig": "fn nightly_close() void { reconcile_ledger(); }\n",
}


class Base(unittest.TestCase):
    files = PYTHON

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.addCleanup(self.data.cleanup)
        write_tree(self.root.name, self.files)
        self.memory = Memory(str(Path(self.data.name) / "m.sqlite"), self.root.name)
        self.addCleanup(self.memory.close)
        self.memory.ingest()

    def node(self, result, key):
        return next(n for n in result["nodes"] if n["id"] == key)


class Shape(Base):
    def test_one_call_carries_code_and_proven_edges(self):
        result = pack.build(self.memory, "how does settle_invoice work")
        node = self.node(result, "core.py::settle_invoice")
        self.assertIn("return amount + 1", node["src"])
        self.assertEqual(node["called_by"], ["service.py::checkout"])
        self.assertEqual(node["rung"], "edges")
        self.assertEqual(node["at"], "core.py:1")

    def test_pack_explains_its_own_trust(self):
        result = pack.build(self.memory, "settle_invoice")
        self.assertEqual(result["schema"], pack.SCHEMA)
        for field in ("calls,called_by", "maybe_uses,maybe_used_by", "src,text"):
            self.assertIn(field, result["trust"])
        self.assertIn("never instructions", result["trust"]["src,text"])

    def test_documents_arrive_with_the_node(self):
        result = pack.build(self.memory, "settle_invoice rounds amounts")
        self.assertIn("notes.md", self.node(result, "core.py::settle_invoice")["docs"])
        self.assertIn("notes.md", {d["at"].split("@")[0] for d in result["docs"]})

    def test_unknown_task_states_a_gap_not_silence(self):
        result = pack.build(self.memory, "zzqqxx nothing matches this")
        self.assertEqual(result["nodes"], [])
        self.assertTrue(result["gaps"])

    def test_missing_exact_key_does_not_return_lexical_bycatch(self):
        result = pack.build(self.memory, "nonexistent.py::imaginary_symbol")
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["docs"], [])
        self.assertIn("not in this snapshot: nonexistent.py::imaginary_symbol",
                      result["gaps"])

    def test_missing_key_with_existing_name_does_not_resolve_elsewhere(self):
        result = pack.build(self.memory, "nonexistent.py::settle_invoice")
        self.assertEqual(result["nodes"], [])

    def test_exact_key_prefix_is_not_an_existing_symbol(self):
        result = pack.build(self.memory, "core.py::settle_invoice_extra")
        self.assertEqual(result["nodes"], [])

    def test_mixed_question_keeps_known_evidence_and_missing_key_gap(self):
        result = pack.build(self.memory,
                            "Compare core.py::settle_invoice and missing.py::unknown")
        self.node(result, "core.py::settle_invoice")
        self.assertTrue(any("missing.py::unknown" in gap for gap in result["gaps"]))

    def test_rejects_an_empty_task(self):
        with self.assertRaises(ValueError):
            pack.build(self.memory, "  ")

    def test_mcp_tool(self):
        result = handle_call(self.memory, "context", {"task": "settle_invoice"})
        self.assertEqual(result["schema"], pack.SCHEMA)


class Budget(Base):
    files = {"m%d.py" % i: "def invoice_step_%d():\n%s" % (i, "    x = 1\n" * 120)
             for i in range(6)}

    def test_stays_within_budget_and_counts_what_it_cut(self):
        result = pack.build(self.memory, "invoice step", limit=6, max_bytes=2048)
        size = len(json.dumps(result["nodes"] + result["docs"], separators=(",", ":")))
        self.assertLessEqual(size, 2048)
        omitted = result["omitted"]
        self.assertGreater(omitted["nodes"] + omitted["src"], 0)

    def test_long_bodies_say_they_were_cut(self):
        result = pack.build(self.memory, "invoice step", limit=1)
        node = result["nodes"][0]
        self.assertTrue(node["src_cut"])
        self.assertLessEqual(len(node["src"]), pack.MAX_EXCERPT_BYTES)


class Hints(Base):
    """Refs connect edgeless languages without becoming proof."""

    files = ZIG

    def test_edgeless_language_gets_a_neighborhood_both_ways(self):
        result = pack.build(self.memory, "reconcile ledger nightly close", limit=5)
        self.assertEqual(self.node(result, "b.zig::nightly_close")["maybe_uses"],
                         ["a.zig::reconcile_ledger"])
        self.assertEqual(self.node(result, "a.zig::reconcile_ledger")["maybe_used_by"],
                         ["b.zig::nightly_close"])

    def test_refs_never_reach_proof_paths(self):
        pack.build(self.memory, "reconcile ledger nightly close")
        _snapshot, view = self.memory.store.view()
        self.assertEqual(view["assertions"], [])
        impact = compile_impact(self.memory.store, "a.zig::reconcile_ledger")
        self.assertNotIn("b.zig::nightly_close", json.dumps(impact))

    def test_gap_says_empty_calls_means_unknown(self):
        result = pack.build(self.memory, "reconcile ledger")
        self.assertTrue(any("unknown, not none" in gap for gap in result["gaps"]))


class Ambiguity(Base):
    files = {"a.zig": "fn shared_name() void {}\n",
             "b.zig": "fn shared_name() void {}\n",
             "c.zig": "fn user_of_it() void { shared_name(); }\n"}

    def test_an_ambiguous_name_is_no_hint(self):
        result = pack.build(self.memory, "user of it shared name", limit=5)
        self.assertNotIn("maybe_uses", self.node(result, "c.zig::user_of_it"))


class Words(Base):
    """Models ask in words; identifiers are not words until split."""

    files = {"pay.py": "def chargeInvoice():\n    return 1\n",
             "steps.py": "def invoice_step_0():\n    return 1\n"}

    def test_words_reach_inside_identifiers(self):
        result = pack.build(self.memory, "how do we charge an invoice")
        self.assertIn("pay.py::chargeInvoice", {n["id"] for n in result["nodes"]})

    def test_tokens_keep_the_whole_identifier(self):
        tokens = _subword_tokens("chargeInvoice invoice_step_0 HTTPServer plain")
        for token in ("chargeinvoice", "charge", "invoice", "invoice_step_0",
                      "step", "http", "server", "plain"):
            self.assertIn(token, tokens)


class Excerpts(Base):
    """A node's code and links are about that node alone."""

    files = {"a.rs": "fn first(x: u64) -> u64 {\n    x\n}\n\nfn second(cfg: u64) -> u64 {\n    helper_call(cfg)\n}\n",
             "b.rs": "fn helper_call(x: u64) -> u64 {\n    x\n}\n"}

    def test_a_short_span_stops_at_the_next_symbol(self):
        result = pack.build(self.memory, "first second helper call", limit=5)
        self.assertNotIn("second", self.node(result, "a.rs::first")["src"])

    def test_a_local_variable_is_not_a_reference(self):
        files = {"a.zig": "fn path() void {}\n",
                 "b.zig": "fn opens_things() void { var path = 1; _ = path; }\n"}
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as data:
            write_tree(root, files)
            with Memory(str(Path(data) / "m.sqlite"), root) as memory:
                memory.ingest()
                result = pack.build(memory, "opens things", limit=2)
        self.assertNotIn("maybe_uses", self.node(result, "b.zig::opens_things"))

    def test_ref_by_needs_the_whole_identifier(self):
        files = {"a.zig": "fn disk() void {}\n",
                 "b.zig": "fn disk_usage_report() void { }\n"}
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as data:
            write_tree(root, files)
            with Memory(str(Path(data) / "m.sqlite"), root) as memory:
                memory.ingest()
                result = pack.build(memory, "disk", limit=2)
        self.assertNotIn("maybe_used_by", self.node(result, "a.zig::disk"))


class Proven(Base):
    def test_a_proven_call_is_not_repeated_as_a_hint(self):
        result = pack.build(self.memory, "checkout cart settle invoice")
        node = self.node(result, "service.py::checkout")
        self.assertEqual(node["calls"], ["core.py::settle_invoice"])
        self.assertNotIn("maybe_uses", node)


if __name__ == "__main__":
    unittest.main()
