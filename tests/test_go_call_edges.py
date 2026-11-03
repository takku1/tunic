"""Go verified call edges v1 via the uniform tree-sitter engine.

Proof rules (see tunic/unigraph.py, GO_SPEC):
- R1: same-directory call to a unique visible definition (package-level
  function), unshadowed and unreassigned at the call site. External
  test packages (foo_test) do not get bare-name scope: only imports.
- R2: call through an import alias that resolves to one repo
  directory with a unique exported function of that name.
  Stdlib, third-party, ambiguous, dot, and blank imports abstain.
- Everything else (method receivers, shadowing, reassignment,
  nested closures, module-scope calls, unsupported syntax) yields an
  actionable unresolved/nested diagnostic, never an invented edge.
A verified edge means a statically established call target, not proof
that execution reaches the call.
"""

import tempfile
import unittest
from pathlib import Path

from tunic import unigraph
from tunic.memory import Memory

PARSER_OR_SKIP = unittest.skipUnless(
    unigraph._parser(unigraph.GO_SPEC) is not None,
    "tree-sitter Go grammar not installed")


FILES = {
    "pkg/a.go": """package foo
func Alpha(x int) int {
  return Beta(x)
}
func Beta(y int) int {
  return y
}
func beta(y int) int {
  return y
}
""",
    "pkg/b.go": """package foo
func Run() int {
  return Alpha(1)
}
func Shadow(Beta int) int {
  return Beta(1)
}
""",
    "pkg/c.go": """package foo
type Store struct{}
func (s *Store) Get(k string) string {
  return helper(k)
}
func helper(k string) string {
  return k
}
func UseStore() string {
  s := &Store{}
  return s.Get("k")
}
""",
    "pkg/dup1.go": """package foo
func Dup() int {
  return 1
}
""",
    "pkg/dup2.go": """package foo
func Dup() int {
  return 2
}
func UseDup() int {
  return Dup()
}
""",
    "pkg/e_test.go": """package foo_test
import fooalias "example.com/mod/pkg"
func TestAlpha() int {
  return fooalias.Alpha(1)
}
func TestBeta() int {
  return fooalias.beta(1)
}
""",
    "bar/c.go": """package bar
import alias "example.com/mod/pkg"
func Do() int {
  return alias.Alpha(1)
}
func DoMissing() int {
  return alias.Nope(1)
}
""",
    "bar/d.go": """package bar
import "fmt"
func Loud(x int) int {
  fmt.Println(x)
  return undefinedfn(x)
}
""",
    "bar/e.go": """package bar
import . "example.com/mod/pkg"
func DotUse() int {
  return Alpha(1)
}
""",
    "bar/f.go": """package bar
import _ "example.com/mod/pkg"
func BlankUse() int {
  return Alpha(1)
}
""",
    "bar/g.go": """package bar
var Maker = Alpha
func Reassign() int {
  Maker = Beta
  return Maker(1)
}
""",
    "bar/nested.go": """package bar
func Outer() int {
  fn := func(q int) int {
    return Alpha(q)
  }
  return fn(1)
}
""",
    "bar/broken.go": """package bar
func Broken( {
  return Alpha(
}
""",
    "plat/a.go": """package foo
func Dup() int {
  return 1
}
func Use() int {
  return Dup()
}
""",
    "plat/a_windows.go": """package foo
func Dup() int {
  return 2
}
""",
    "tint/b.go": """package bar
func Help() int {
  return 1
}
""",
    "tint/b_test.go": """package bar
func TestHelp() int {
  return Help()
}
""",
    "mixed/m1.go": """package one
func Solo() int {
  return 1
}
""",
    "mixed/m2.go": """package two
func Caller() int {
  return Solo()
}
""",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    for name, text in FILES.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "go.sqlite"), str(work))
    report = memory.ingest()
    return memory, report


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


def _diags(view, kind=None):
    return [d for d in view["diagnostics"]
            if kind is None or d.get("kind") == kind]


@PARSER_OR_SKIP
class GoProofTest(unittest.TestCase):
    def test_same_file_direct_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("pkg/a.go::Alpha", "pkg/a.go::Beta"),
                              _edges(view))
            finally:
                memory.close()

    def test_same_dir_cross_file_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("pkg/b.go::Run", "pkg/a.go::Alpha"),
                              _edges(view))
            finally:
                memory.close()

    def test_import_alias_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("bar/c.go::Do", "pkg/a.go::Alpha"),
                              _edges(view))
            finally:
                memory.close()

    def test_external_test_package_sees_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("pkg/e_test.go::TestAlpha",
                               "pkg/a.go::Alpha"),
                              _edges(view))
            finally:
                memory.close()

    def test_receipt_rule_is_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn("go_static_binding_v1", _rules(view))
            finally:
                memory.close()


@PARSER_OR_SKIP
class GoNegativeTest(unittest.TestCase):
    def test_shadowed_param_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("pkg/b.go::Shadow", "pkg/a.go::Beta"),
                                 _edges(view))
                reasons = [d.get("reason") for d in
                           _diags(view, "unresolved_call")
                           if d.get("caller") == "pkg/b.go::Shadow"]
                self.assertIn("shadowed_name", reasons)
            finally:
                memory.close()

    def test_method_receiver_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("pkg/c.go::UseStore", "pkg/c.go::Get"),
                                 _edges(view))
                reasons = [d.get("reason") for d in
                           _diags(view, "unresolved_call")
                           if d.get("caller") == "pkg/c.go::UseStore"]
                self.assertIn("unsupported_receiver", reasons)
            finally:
                memory.close()

    def test_method_body_calls_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("pkg/c.go::Get", "pkg/c.go::helper"),
                              _edges(view))
            finally:
                memory.close()

    def test_duplicate_defs_unproven(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("pkg/dup2.go::UseDup", "pkg/dup1.go::Dup"),
                                 _edges(view))
                self.assertNotIn(("pkg/dup2.go::UseDup", "pkg/dup2.go::Dup"),
                                 _edges(view))
            finally:
                memory.close()

    def test_external_test_package_unexported_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("pkg/e_test.go::TestBeta",
                                  "pkg/a.go::beta"),
                                 _edges(view))
            finally:
                memory.close()

    def test_missing_import_member_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("bar/c.go::DoMissing", "pkg/a.go::Alpha"),
                                 _edges(view))
                reasons = [d.get("reason") for d in
                           _diags(view, "unresolved_call")
                           if d.get("caller") == "bar/c.go::DoMissing"]
                self.assertIn("unresolved_import", reasons)
            finally:
                memory.close()

    def test_stdlib_and_undefined_abstain(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                reasons = [d.get("reason") for d in
                           _diags(view, "unresolved_call")
                           if d.get("caller") == "bar/d.go::Loud"]
                self.assertIn("unresolved_import", reasons)
                self.assertIn("unresolved_name", reasons)
            finally:
                memory.close()

    def test_dot_and_blank_imports_abstain(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("bar/e.go::DotUse", "pkg/a.go::Alpha"),
                                 _edges(view))
                self.assertNotIn(("bar/f.go::BlankUse", "pkg/a.go::Alpha"),
                                 _edges(view))
            finally:
                memory.close()

    def test_var_alias_call_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("bar/g.go::Reassign", "pkg/a.go::Alpha"),
                                 _edges(view))
            finally:
                memory.close()

    def test_nested_closure_call_is_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                nested = [d for d in _diags(view, "nested_call")
                          if d.get("path") == "bar/nested.go"]
                self.assertTrue(nested)
                self.assertNotIn(("bar/nested.go::Outer", "pkg/a.go::Alpha"),
                                 _edges(view))
            finally:
                memory.close()

    def test_broken_file_is_unsupported_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                kinds = [d.get("kind") for d in _diags(view)
                         if d.get("path") == "bar/broken.go"]
                self.assertIn("unsupported_syntax", kinds)
            finally:
                memory.close()

    def test_platform_duplicate_defs_unproven(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("plat/a.go::Use", "plat/a.go::Dup"),
                                 _edges(view))
                reasons = [d.get("reason") for d in
                           _diags(view, "unresolved_call")
                           if d.get("caller") == "plat/a.go::Use"]
                self.assertIn("unproven_target", reasons)
            finally:
                memory.close()

    def test_internal_test_file_resolves_same_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertIn(("tint/b_test.go::TestHelp",
                               "tint/b.go::Help"),
                              _edges(view))
            finally:
                memory.close()

    def test_mixed_package_dir_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                self.assertNotIn(("mixed/m2.go::Caller",
                                  "mixed/m1.go::Solo"),
                                 _edges(view))
                reasons = [d.get("reason") for d in
                           _diags(view, "unresolved_call")
                           if d.get("caller") == "mixed/m2.go::Caller"]
                self.assertIn("unproven_target", reasons)
            finally:
                memory.close()


@PARSER_OR_SKIP
class GoRefreshTest(unittest.TestCase):
    def _oracle_view(self, root, tag):
        from tunic.store import GraphStore
        oracle = GraphStore(str(Path(root) / ("oracle-%s.sqlite" % tag)))
        try:
            oracle.ingest(str(Path(root) / "ws"))
            return oracle.current(), oracle.view()[1]
        finally:
            oracle.close()

    def test_refresh_rebuild_equivalence(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            for name, text in FILES.items():
                dest = work / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            memory = Memory(str(Path(tmp) / "go.sqlite"), str(work))
            try:
                memory.ingest()
                _snapshot, before = memory.store.view()
                before_edges = _edges(before)
                (work / "pkg" / "b.go").write_text(
                    FILES["pkg/b.go"] + "\n", encoding="utf-8")
                report = memory.refresh()
                self.assertEqual(before_edges, _edges(
                    memory.store.view()[1]))
                snap, view2 = self._oracle_view(tmp, "touch")
                self.assertEqual(report["snapshot"], snap)
                self.assertEqual(memory.store.view()[1], view2)
            finally:
                memory.close()

    def test_refresh_matches_rebuild_after_target_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            for name, text in FILES.items():
                dest = work / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8")
            memory = Memory(str(Path(tmp) / "go.sqlite"), str(work))
            try:
                memory.ingest()
                (work / "pkg" / "dup1.go").unlink()
                report = memory.refresh()
                snap, view2 = self._oracle_view(tmp, "removed")
                self.assertEqual(report["snapshot"], snap)
                self.assertEqual(memory.store.view()[1], view2)
                _snapshot, view = memory.store.view()
                self.assertIn(("pkg/dup2.go::UseDup", "pkg/dup2.go::Dup"),
                              _edges(view))
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
