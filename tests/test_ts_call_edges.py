"""TypeScript/JavaScript verified call edges v1: failing-first contract.

Proof rules (see tunic/tsgraph.py):
- R1: same-module call `name(...)` where `name` is the single
  top-level `function` declaration (overload implementations count as
  one) or single module-scope `const` binding, unshadowed and
  unreassigned at the call site. Decorated declarations abstain.
- R2: call through a named import `import {orig [as local]} from
  "<relative>"` where the specifier resolves to one indexed file whose
  single matching top-level function/const is directly exported.
  Default imports resolve only to a uniquely default-exported
  function. Barrel re-exports, `export *`, namespaces, dynamic
  imports, and bare specifiers abstain.
- Everything else (member calls, `new`, shadowing, reassignment,
  nested scopes, module-scope calls, unsupported syntax) yields an
  actionable unresolved/nested diagnostic, never an invented edge.
A verified edge means a statically established call target, not proof
that execution reaches the call.
"""

import tempfile
import unittest
from pathlib import Path

from tunic import tsgraph
from tunic.memory import Memory

PARSER_OR_SKIP = unittest.skipUnless(
    tsgraph._HAVE_PARSER,
    "tree-sitter parser not installed (pip install tunic-context[typescript])")

FILES = {
    "src/a.ts": """export function alpha(x: number): number {
  return beta(x);
}
function beta(y: number): number {
  return y;
}
""",
    "src/b.ts": """import { alpha } from "./a";
export function run(): number {
  return alpha(1);
}
""",
    "src/c.ts": """import { alpha } from "./a";
export function use(alpha: (x: number) => number): number {
  return alpha(2);
}
""",
    "src/d.ts": """import def from "./e";
export function go(): number {
  return def();
}
""",
    "src/e.ts": """export default function make(): number {
  return 1;
}
""",
    "src/f.ts": """import * as ns from "./a";
export function whee(): number {
  const v = ns.alpha(1);
  return Math.max(v, 0);
}
""",
    "src/g.ts": """import { alpha } from "./a";
let h = alpha;
h = Math.max;
export function k(): number {
  return h(1);
}
""",
    "src/h.ts": """export { alpha } from "./a";
""",
    "src/i.ts": """import { run } from "./h";
export function tryrun(): number {
  return run();
}
""",
    "src/j.ts": """import type { alpha } from "./a";
export function typed(): void {
}
""",
    "src/k.ts": """function sealed(target: unknown): void {
}
@sealed
export function dec(): number {
  return 1;
}
export function calldec(): number {
  return dec();
}
""",
    "src/l.ts": """export function one(x: string): void {
}
export function two(x: string): void {
}
export function one(x: number): void;
export function one(x: unknown): void {
}
export function useone(): void {
  one(1);
}
""",
    "src/m.ts": """import { alpha } from "./a";
const top = alpha(0);
export function outer(): number {
  function inner(): number {
    return alpha(1);
  }
  return inner();
}
""",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    (work / "src").mkdir(parents=True)
    for name, text in FILES.items():
        (work / name).write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "ts.sqlite"), str(work))
    report = memory.ingest()
    return memory, report


def _edges(view):
    return {(a["proposition"]["caller"], a["proposition"]["callee"])
            for a in view["assertions"]
            if a["proposition"].get("relation") == "static_call"
            and a.get("status") == "syntactic_observation"}


def _diags(view, kind=None):
    return [d for d in view["diagnostics"]
            if kind is None or d.get("kind") == kind]


@PARSER_OR_SKIP
class TsProofTest(unittest.TestCase):
    def test_same_module_direct_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("src/a.ts::alpha", "src/a.ts::beta"),
                              _edges(view))
            finally:
                memory.close()

    def test_named_import_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("src/b.ts::run", "src/a.ts::alpha"),
                              _edges(view))
            finally:
                memory.close()

    def test_default_import_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("src/d.ts::go", "src/e.ts::make"),
                              _edges(view))
            finally:
                memory.close()

    def test_overload_implementation_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("src/l.ts::useone", "src/l.ts::one"),
                              _edges(view))
            finally:
                memory.close()


@PARSER_OR_SKIP
class TsNegativeTest(unittest.TestCase):
    def test_shadowed_param_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("src/c.ts::use", "src/a.ts::alpha"),
                                 _edges(view))
                self.assertTrue(
                    [d for d in _diags(view, "unresolved_call")
                     if d.get("caller") == "src/c.ts::use"])
            finally:
                memory.close()

    def test_reassigned_binding_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("src/g.ts::k", "src/a.ts::alpha"),
                                 _edges(view))
            finally:
                memory.close()

    def test_member_and_namespace_calls_abstain(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertFalse([e for e in edges
                                  if e[0] == "src/f.ts::whee"])
                reasons = {d.get("reason") for d in _diags(view)}
                self.assertIn("unsupported_receiver", reasons)
            finally:
                memory.close()

    def test_barrel_reexport_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("src/i.ts::tryrun", "src/a.ts::alpha"),
                                 _edges(view))
            finally:
                memory.close()

    def test_type_only_import_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertFalse([e for e in _edges(view)
                                  if e[0].startswith("src/j.ts::")])
            finally:
                memory.close()

    def test_decorated_target_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("src/k.ts::calldec", "src/k.ts::dec"),
                                 _edges(view))
            finally:
                memory.close()

    def test_module_scope_and_nested_calls_diagnosed_not_edged(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertFalse([e for e in edges
                                  if e[0] == "src/m.ts::outer"])
                diags = _diags(view)
                by_kind = {}
                for d in diags:
                    by_kind.setdefault(d.get("kind"), []).append(d)
                self.assertIn("unresolved_call", by_kind)
                self.assertIn("nested_call", by_kind)
            finally:
                memory.close()

    def test_no_edges_without_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                expected = {("src/a.ts::alpha", "src/a.ts::beta"),
                            ("src/b.ts::run", "src/a.ts::alpha"),
                            ("src/d.ts::go", "src/e.ts::make"),
                            ("src/l.ts::useone", "src/l.ts::one")}
                self.assertEqual(edges, expected)
            finally:
                memory.close()


WALK_FILES = {
    "src/s.ts": """export const double = (x: number): number => twice(x);
function twice(y: number): number {
  return y * 2;
}
""",
    "src/p.ts": """function tag(arg: unknown): unknown {
  return arg;
}
function make(x: number): number {
  return x;
}
@tag(make(1))
export class Box {
}
""",
    "src/q.ts": """export class Kid extends mixin(Base) {
}
export function mk(): number {
  return 1;
}
""",
    "src/t.ts": """export async function fetchIt(): Promise<number> {
  return parse(1);
}
export function* gen(): Generator<number> {
  yield build(2);
}
function parse(x: number): number {
  return x;
}
function build(x: number): number {
  return x;
}
""",
    "src/u.ts": """import { sql } from "./v";
export function load(id: number): unknown {
  const t = new Thing(id);
  return sql`select ${one(id)}`;
}
function one(x: number): number {
  return x;
}
function Thing(n: number): void {
}
""",
    "src/v.ts": """export function sql(x: unknown): unknown {
  return x;
}
""",
    "src/w.ts": """export default function (): number {
  return lone(1);
}
function lone(x: number): number {
  return x;
}
""",
    "src/cyc1.ts": """import { b } from "./cyc2";
export function a(): number {
  return b();
}
""",
    "src/cyc2.ts": """import { a } from "./cyc1";
export function b(): number {
  return a();
}
""",
    "src/bad.ts": "export function oops( {\n  return broken(;\n}\n",
    "src/view.tsx": """export function Comp(): unknown {
  return null;
}
""",
}


def _ingest_files(tmp, files):
    work = Path(tmp) / "ws"
    for name, text in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "ts2.sqlite"), str(work))
    memory.ingest()
    return memory


@PARSER_OR_SKIP
class TsWalkerTest(unittest.TestCase):
    def test_const_arrow_caller_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("src/s.ts::double", "src/s.ts::twice"),
                              _edges(view))
            finally:
                memory.close()

    def test_decorator_invocation_diagnosed_not_edged(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertFalse([e for e in edges
                                  if e[0].startswith("src/p.ts::")])
                reasons = {d.get("reason") for d in _diags(view)}
                self.assertIn("decorator_invocation", reasons)
            finally:
                memory.close()

    def test_class_heritage_call_diagnosed(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertFalse([e for e in edges
                                  if e[0] in ("src/q.ts::Kid",
                                              "src/q.ts::mk")])
                heritage = [d for d in _diags(view, "unresolved_call")
                            if d.get("reason") == "class_heritage_call"]
                self.assertTrue(heritage)
            finally:
                memory.close()

    def test_async_and_generator_bodies_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertIn(("src/t.ts::fetchIt", "src/t.ts::parse"),
                              edges)
                self.assertIn(("src/t.ts::gen", "src/t.ts::build"), edges)
            finally:
                memory.close()

    def test_new_and_tagged_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertIn(("src/u.ts::load", "src/u.ts::one"), edges)
                self.assertFalse([e for e in edges
                                  if e[1] in ("src/u.ts::Thing",
                                              "src/v.ts::sql")])
                reasons = {d.get("reason") for d in _diags(view)
                           if d.get("path") == "src/u.ts"}
                self.assertIn("unsupported_construct", reasons)
            finally:
                memory.close()

    def test_anonymous_default_body_diagnosed_not_edged(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertFalse([e for e in edges
                                  if e[0].startswith("src/w.ts::")])
                scoped = [d for d in _diags(view, "unresolved_call")
                          if d.get("reason") == "module_scope"
                          and d.get("path") == "src/w.ts"]
                self.assertTrue(scoped)
            finally:
                memory.close()

    def test_import_cycle_resolves_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertIn(("src/cyc1.ts::a", "src/cyc2.ts::b"), edges)
                self.assertIn(("src/cyc2.ts::b", "src/cyc1.ts::a"), edges)
            finally:
                memory.close()


@PARSER_OR_SKIP
class TsRobustnessTest(unittest.TestCase):
    def test_syntax_error_file_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertFalse([e for e in edges
                                  if e[0].startswith("src/bad.ts::")])
                kinds = {d.get("kind") for d in _diags(view)
                         if d.get("path") == "src/bad.ts"}
                self.assertIn("unsupported_syntax", kinds)
            finally:
                memory.close()

    def test_tsx_file_abstains_without_parser_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, WALK_FILES)
            try:
                _snapshot, view = memory.store.view()
                edges = _edges(view)
                self.assertFalse([e for e in edges
                                  if e[0].startswith("src/view.tsx::")])
            finally:
                memory.close()


REFRESH_V1 = {
    "src/a.ts": """export function f(x: number): number {
  return x;
}
export function g(x: number): number {
  return f(x);
}
""",
    "src/b.ts": """import { g } from "./a";
export function h(x: number): number {
  return g(x);
}
""",
}


@PARSER_OR_SKIP
class TsRefreshTest(unittest.TestCase):
    def _oracle_view(self, root, tag):
        from tunic.store import GraphStore
        oracle = GraphStore(str(Path(root) / ("oracle-%s.sqlite" % tag)))
        try:
            oracle.ingest(str(Path(root) / "ws"))
            return oracle.current(), oracle.view()[1]
        finally:
            oracle.close()

    def test_refresh_matches_rebuild_after_target_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, REFRESH_V1)
            try:
                work = Path(tmp) / "ws"
                (work / "src" / "a.ts").write_text(
                    REFRESH_V1["src/a.ts"].replace("return f(x);",
                                                   "return f(x) + 1;"),
                    encoding="utf-8")
                report = memory.refresh()
                snap, view2 = self._oracle_view(tmp, "target")
                self.assertEqual(report["snapshot"], snap)
                self.assertEqual(memory.store.view()[1], view2)
                _snapshot, view = memory.store.view()
                self.assertIn(("src/b.ts::h", "src/a.ts::g"),
                              _edges(view))
            finally:
                memory.close()

    def test_refresh_matches_rebuild_after_importer_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest_files(tmp, REFRESH_V1)
            try:
                work = Path(tmp) / "ws"
                (work / "src" / "b.ts").write_text(
                    """import { f } from "./a";
export function h(x: number): number {
  return f(x);
}
""",
                    encoding="utf-8")
                report = memory.refresh()
                snap, view2 = self._oracle_view(tmp, "importer")
                self.assertEqual(report["snapshot"], snap)
                self.assertEqual(memory.store.view()[1], view2)
                _snapshot, view = memory.store.view()
                self.assertIn(("src/b.ts::h", "src/a.ts::f"),
                              _edges(view))
                self.assertNotIn(("src/b.ts::h", "src/a.ts::g"),
                                 _edges(view))
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
