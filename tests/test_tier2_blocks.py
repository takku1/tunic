"""Tier-2 block splitting: component script blocks feed the TS grammar.

Fixtures cover .vue (script + setup joined, R2 into .ts, R2 into .vue
abstains), .svelte, .astro frontmatter, and malformed containers
(unclosed, external src, non-TS lang). Every edge pins the
ts_static_binding_v1 receipt; spans must be parent-absolute.
"""

import tempfile
import unittest
from pathlib import Path

from tunic import blocks as block_mod
from tunic import tsgraph
from tunic.memory import Memory

TS_OR_SKIP = unittest.skipUnless(
    tsgraph._HAVE_PARSER,
    "tree-sitter parser not installed (pip install tunic-context[typescript])")


def _ingest(files):
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name) / "ws"
    for name, text in files.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    raws = {name: (work / name).read_bytes() for name in files}
    memory = Memory(str(Path(tmp.name) / "t.sqlite"), str(work))
    memory.ingest()
    _snapshot, view = memory.store.view()
    memory.close()
    tmp.cleanup()
    return view, raws


def _edges(view):
    return {(a["proposition"]["caller"], a["proposition"]["callee"])
            for a in view["assertions"]
            if a["proposition"].get("relation") == "static_call"
            and a.get("status") == "syntactic_observation"}


def _rules(view):
    return {a["resolution"].get("rule")
            for a in view["assertions"]
            if a["proposition"].get("relation") == "static_call"
            and a.get("status") == "syntactic_observation"}


def _reasons(view, caller):
    return [d.get("reason") for d in view["diagnostics"]
            if d.get("kind") == "unresolved_call"
            and d.get("caller") == caller]


VUE_FILES = {
    "app/App.vue": """<template>
  <div>{{ run() }}</div>
</template>
<script>
import { helper } from './util';
export function run() {
  return helper(1);
}
export function shadow(helper) {
  return helper(1);
}
function localOnly() {
  return run();
}
</script>
<style>
.run { color: red; }
</style>
""",
    "app/util.ts": """export function helper(x: number): number {
  return x;
}
""",
    "app/Two.vue": """<script>
export function base() {
  return 1;
}
</script>
<script setup lang="ts">
import { run } from './run';
export function boot() {
  return run(base());
}
boot();
</script>
""",
    "app/run.ts": """export function run(x: number): number {
  return x;
}
""",
    "app/Other.vue": """<script>
import Widget from './Widget.vue';
export function make() {
  return Widget.create();
}
</script>
""",
    "app/Widget.vue": """<script>
export default {
  name: 'Widget',
};
</script>
""",
    "app/Bad.vue": """<script>
export function broken() {
  return 1;
}
""",
    "app/Src.vue": """<script src="./external.ts">
</script>
""",
    "app/Coffee.vue": """<script lang="coffee">
square = (x) -> x * x
</script>
""",
}


class VueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view, cls.raws = _ingest(VUE_FILES)
        cls.edges = _edges(cls.view)

    @TS_OR_SKIP
    def test_r2_into_ts(self):
        self.assertIn(("app/App.vue::run", "app/util.ts::helper"),
                      self.edges)

    @TS_OR_SKIP
    def test_shadowed_param(self):
        self.assertIn("shadowed_name",
                      _reasons(self.view, "app/App.vue::shadow"))

    @TS_OR_SKIP
    def test_same_file_r1(self):
        self.assertIn(("app/App.vue::localOnly", "app/App.vue::run"),
                      self.edges)

    @TS_OR_SKIP
    def test_joined_blocks_share_scope(self):
        self.assertIn(("app/Two.vue::boot", "app/Two.vue::base"),
                      self.edges)
        self.assertIn(("app/Two.vue::boot", "app/run.ts::run"),
                      self.edges)

    @TS_OR_SKIP
    def test_setup_top_level_abstains(self):
        diags = [d for d in self.view["diagnostics"]
                 if d.get("path") == "app/Two.vue"
                 and d.get("caller") is None
                 and d.get("kind") == "unresolved_call"]
        self.assertTrue(diags)

    @TS_OR_SKIP
    def test_r2_into_vue_abstains(self):
        # tsgraph does not resolve member calls through default
        # imports (unsupported_receiver); the .vue target itself is
        # never parsed as a module, so no edge is invented either way.
        self.assertFalse([e for e in self.edges
                          if e[0] == "app/Other.vue::make"])
        self.assertIn("unsupported_receiver",
                      _reasons(self.view, "app/Other.vue::make"))

    def test_unclosed_block_abstains(self):
        diags = [d for d in self.view["diagnostics"]
                 if d.get("path") == "app/Bad.vue"
                 and d.get("kind") == "unsupported_syntax"]
        self.assertTrue(diags)
        self.assertFalse([e for e in self.edges
                          if e[0].startswith("app/Bad.vue")])

    def test_external_src_diag(self):
        diags = [d for d in self.view["diagnostics"]
                 if d.get("path") == "app/Src.vue"
                 and d.get("kind") == "unsupported_block"]
        self.assertTrue(diags)

    def test_non_ts_lang_diag(self):
        diags = [d for d in self.view["diagnostics"]
                 if d.get("path") == "app/Coffee.vue"
                 and d.get("kind") == "unsupported_block"]
        self.assertTrue(diags)

    @TS_OR_SKIP
    def test_spans_are_parent_absolute(self):
        # Engine spans are parent-absolute over the stored file bytes;
        # compare against the exact bytes written, never a
        # newline-normalized reconstruction.
        raw = self.raws["app/App.vue"]
        script_at = raw.index(b"<script>")
        expect = {("app/App.vue::run", "app/util.ts::helper"): b"helper(1)",
                  ("app/App.vue::localOnly", "app/App.vue::run"): b"run()"}
        for a in self.view["assertions"]:
            prop = a["proposition"]
            if prop.get("relation") != "static_call":
                continue
            key = (prop.get("caller"), prop.get("callee"))
            if key not in expect:
                continue
            self.assertGreaterEqual(a["start"], script_at)
            self.assertEqual(raw[a["start"]:a["end"]], expect[key])

    @TS_OR_SKIP
    def test_receipt(self):
        self.assertIn("ts_static_binding_v1", _rules(self.view))


SVELTE_ASTRO_FILES = {
    "site/Comp.svelte": """<script>
import { helper } from './util';
export function run() {
  return helper(2);
}
</script>
<main>{run()}</main>
""",
    "site/util.ts": """export function helper(x: number): number {
  return x;
}
""",
    "site/page.astro": """---
import { helper } from './util';
export function load() {
  return helper(3);
}
---
<html>{load()}</html>
""",
}


class SvelteAstroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view, cls.raws = _ingest(SVELTE_ASTRO_FILES)
        cls.edges = _edges(cls.view)

    @TS_OR_SKIP
    def test_svelte_r2(self):
        self.assertIn(("site/Comp.svelte::run", "site/util.ts::helper"),
                      self.edges)

    @TS_OR_SKIP
    def test_astro_frontmatter(self):
        self.assertIn(("site/page.astro::load", "site/util.ts::helper"),
                      self.edges)

    @TS_OR_SKIP
    def test_astro_span_in_fence(self):
        raw = self.raws["site/page.astro"]
        fence_end = raw.index(b"---", 3)
        for a in self.view["assertions"]:
            prop = a["proposition"]
            if prop.get("relation") != "static_call":
                continue
            if not prop.get("caller", "").startswith("site/page.astro::"):
                continue
            self.assertLess(a["start"], fence_end)

    @TS_OR_SKIP
    def test_receipt(self):
        self.assertIn("ts_static_binding_v1", _rules(self.view))


class SplitterUnitTest(unittest.TestCase):
    def test_no_script_is_empty(self):
        found, diags = block_mod.split_script_blocks(
            "a.vue", b"<template><div/></template>")
        self.assertEqual(found, [])
        self.assertEqual(diags, [])

    def test_astro_no_fence_is_empty(self):
        found, diags = block_mod.split_script_blocks(
            "a.astro", b"<html></html>")
        self.assertEqual(found, [])
        self.assertEqual(diags, [])


if __name__ == "__main__":
    unittest.main()
