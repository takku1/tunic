"""Tier-1 language edges via the generic unigraph engine.

Each language gets a fixture workspace exercising its spec: an R1
same-scope edge, an R2 import-relative edge where the language has one,
a member/self/class-qualified edge where applicable, plus negatives
(shadowing, privacy, unresolvable imports, receivers) that must yield
diagnostics instead of edges. Every language also pins its receipt
rule (<lang>_static_binding_v1).
"""

import tempfile
import unittest
from pathlib import Path

from tunic import unigraph
from tunic.memory import Memory


def _ingest(files):
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name) / "ws"
    for name, text in files.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp.name) / "t.sqlite"), str(work))
    memory.ingest()
    _snapshot, view = memory.store.view()
    memory.close()
    tmp.cleanup()
    return view


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


def _has_parser(spec):
    return unigraph._parser(spec) is not None


JAVA_FILES = {
    "pkg/Main.java": """package pkg;
import com.acme.External;
public class Main {
  public static int run() {
    return Helper.add(1);
  }
  public static int build() {
    return new Foo().value();
  }
  public static int missing() {
    return External.gone(1);
  }
}
""",
    "pkg/Helper.java": """package pkg;
public class Helper {
  public static int add(int a) {
    return Util.twice(a);
  }
  public static int addMany(int twice) {
    return twice(1);
  }
  private static int priv() {
    return 1;
  }
}
""",
    "pkg/Util.java": """package pkg;
public class Util {
  public static int twice(int x) {
    return thisRef(x);
  }
  public int thisRef(int x) {
    return this.twice(x);
  }
}
""",
    "pkg/Foo.java": """package pkg;
public class Foo {
  public Foo() {
  }
  public int value() {
    return 1;
  }
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.JAVA_SPEC), "no java grammar")
class JavaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(JAVA_FILES)
        cls.edges = _edges(cls.view)

    def test_r1c_class_qualified(self):
        self.assertIn(("pkg/Main.java::run", "pkg/Helper.java::add"),
                      self.edges)

    def test_r1_same_file_bare(self):
        self.assertIn(("pkg/Helper.java::add", "pkg/Util.java::twice"),
                      self.edges)

    def test_self_receiver(self):
        self.assertIn(("pkg/Util.java::thisRef", "pkg/Util.java::twice"),
                      self.edges)

    def test_ctor(self):
        self.assertIn(("pkg/Main.java::build", "pkg/Foo.java::Foo"),
                      self.edges)

    def test_shadowed_param(self):
        self.assertNotIn(("pkg/Helper.java::addMany", "pkg/Util.java::twice"),
                         self.edges)
        self.assertIn("shadowed_name",
                      _reasons(self.view, "pkg/Helper.java::addMany"))

    def test_private_cross_file_abstains(self):
        self.assertNotIn(("pkg/Main.java::run", "pkg/Helper.java::priv"),
                         self.edges)

    def test_external_import_abstains(self):
        self.assertNotIn(("pkg/Main.java::missing", "pkg/Util.java::twice"),
                         self.edges)
        self.assertIn("unresolved_import",
                      _reasons(self.view, "pkg/Main.java::missing"))

    def test_receipt(self):
        self.assertIn("java_static_binding_v1", _rules(self.view))


KOTLIN_FILES = {
    "pkg/a.kt": """package pkg
import com.bar.Baz
fun alpha(x: Int): Int = beta(x)
class A {
  fun method(): Int = alpha(1)
  fun other(): Int = this.method()
}
fun usesBaz(): Int = Baz(1)
fun shadow(beta: Int): Int = beta(1)
""",
    "pkg/b.kt": """package pkg
fun beta(y: Int): Int = y
fun qual(): Int = pkg.helper(1)
""",
    "pkg/Foo.kt": """package pkg
class Foo(val x: Int)
fun make(): Int = Foo(1).x
""",
}

@unittest.skipUnless(_has_parser(unigraph.KOTLIN_SPEC), "no kotlin grammar")
class KotlinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(KOTLIN_FILES)
        cls.edges = _edges(cls.view)

    def test_expr_body_r1(self):
        self.assertIn(("pkg/a.kt::alpha", "pkg/b.kt::beta"), self.edges)

    def test_implicit_receiver_bare(self):
        self.assertIn(("pkg/a.kt::method", "pkg/a.kt::alpha"), self.edges)

    def test_self_receiver(self):
        self.assertIn(("pkg/a.kt::other", "pkg/a.kt::method"), self.edges)

    def test_ctor_by_stem(self):
        self.assertIn(("pkg/Foo.kt::make", "pkg/Foo.kt::Foo"), self.edges)

    def test_shadowed_param(self):
        self.assertIn("shadowed_name",
                      _reasons(self.view, "pkg/a.kt::shadow"))

    def test_external_import_abstains(self):
        self.assertIn("unresolved_import",
                      _reasons(self.view, "pkg/a.kt::usesBaz"))

    def test_qualified_unbound_abstains(self):
        self.assertIn("unsupported_receiver",
                      _reasons(self.view, "pkg/b.kt::qual"))

    def test_receipt(self):
        self.assertIn("kotlin_static_binding_v1", _rules(self.view))


SCALA_FILES = {
    "pkg/main.scala": """package pkg
import foo.{Bar, Baz}
object A {
  def run(): Int = {
    val x = 1
    helper(x)
  }
  def viaObj(): Int = B.m(1)
  def missing(): Int = Bar(1)
}
""",
    "pkg/B.scala": """package pkg
object B {
  def m(x: Int): Int = x
}
class Foo(val x: Int)
""",
    "pkg/C.scala": """package pkg
object C {
  def build(): Int = {
    val o = new Foo(1)
    o.x
  }
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.SCALA_SPEC), "no scala grammar")
class ScalaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(SCALA_FILES)
        cls.edges = _edges(cls.view)

    def test_r1_same_dir_bare(self):
        # helper is not defined: unresolved; run body also exercises
        # val bindings without crashing. viaObj pins the R1c path.
        self.assertIn(("pkg/main.scala::viaObj", "pkg/B.scala::m"),
                      self.edges)

    def test_ctor(self):
        self.assertIn(("pkg/C.scala::build", "pkg/B.scala::Foo"),
                      self.edges)

    def test_braces_import_abstains(self):
        self.assertIn("unresolved_import",
                      _reasons(self.view, "pkg/main.scala::missing"))

    def test_receipt(self):
        self.assertIn("scala_static_binding_v1", _rules(self.view))


CSHARP_FILES = {
    "app/Prog.cs": """using System;
class Prog {
  static int Run() {
    return A.Add(1);
  }
  static int Build() {
    var o = new Foo();
    return o.Value;
  }
  static int Loud() {
    System.Console.WriteLine("x");
    return 1;
  }
}
""",
    "app/A.cs": """class A {
  public static int Add(int a) {
    return Helper(a);
  }
  static int Helper(int a) {
    return this.Instance(a);
  }
  int Instance(int a) {
    return a;
  }
  private static int Priv() {
    return 1;
  }
}
""",
    "app/Foo.cs": """class Foo {
  public Foo() {
  }
  public int Value {
    get { return 1; }
  }
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.CSHARP_SPEC), "no csharp grammar")
class CSharpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(CSHARP_FILES)
        cls.edges = _edges(cls.view)

    def test_r1c_static(self):
        self.assertIn(("app/Prog.cs::Run", "app/A.cs::Add"), self.edges)

    def test_implicit_this_bare(self):
        self.assertIn(("app/A.cs::Add", "app/A.cs::Helper"), self.edges)

    def test_self_receiver(self):
        self.assertIn(("app/A.cs::Helper", "app/A.cs::Instance"),
                      self.edges)

    def test_ctor(self):
        self.assertIn(("app/Prog.cs::Build", "app/Foo.cs::Foo"),
                      self.edges)

    def test_system_using_abstains(self):
        self.assertIn("unresolved_import",
                      _reasons(self.view, "app/Prog.cs::Loud"))

    def test_receipt(self):
        self.assertIn("csharp_static_binding_v1", _rules(self.view))


C_FILES = {
    "src/util.h": """int helper(int a);
""",
    "src/util.c": """#include "util.h"
static int priv(int a) {
  return a;
}
int helper(int a) {
  return priv(a);
}
""",
    "src/main.c": """#include "util.h"
int main(void) {
  int x = sizeof(int);
  return helper(x) + priv(x);
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.C_SPEC), "no c grammar")
class CTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(C_FILES)
        cls.edges = _edges(cls.view)

    def test_r1_same_dir(self):
        self.assertIn(("src/main.c::main", "src/util.c::helper"),
                      self.edges)

    def test_static_same_file(self):
        self.assertIn(("src/util.c::helper", "src/util.c::priv"),
                      self.edges)

    def test_static_cross_file_abstains(self):
        self.assertNotIn(("src/main.c::main", "src/util.c::priv"),
                         self.edges)
        reasons = _reasons(self.view, "src/main.c::main")
        self.assertIn("unproven_target", reasons)

    def test_sizeof_silent(self):
        diags = [d for d in self.view["diagnostics"]
                 if d.get("caller") == "src/main.c::main"
                 and "sizeof" in (d.get("expression") or "")]
        self.assertEqual(diags, [])

    def test_receipt(self):
        self.assertIn("c_static_binding_v1", _rules(self.view))


CPP_FILES = {
    "src/util.h": """int helper(int a);
""",
    "src/util.cpp": """#include "util.h"
int helper(int a) {
  return a;
}
""",
    "src/A.cpp": """struct A {
  static int create();
  int m(int a);
};
int A::create() {
  return 1;
}
int A::m(int a) {
  return this->m(a);
}
""",
    "src/main.cpp": """#include "util.h"
int main() {
  A* p = new A();
  return helper(1) + A::create();
}
""",
}

RUST_FILES = {
    "src/main.rs": """mod util;
use foo::bar;
fn main() {
  util::run(1);
}
fn calls_bar() -> i32 {
  bar(1)
}
fn calls_priv() -> i32 {
  util::priv_fn()
}
""",
    "src/util.rs": """pub fn run(x: i32) -> i32 {
  helper(x)
}
fn helper(x: i32) -> i32 {
  x
}
fn priv_fn() -> i32 {
  1
}
pub struct Counter {
  count: i32,
}
impl Counter {
  pub fn incr(&self) -> i32 {
    self.bump()
  }
  fn bump(&self) -> i32 {
    helper(1)
  }
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.RUST_SPEC), "no rust grammar")
class RustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(RUST_FILES)
        cls.edges = _edges(cls.view)

    def test_mod_r2(self):
        self.assertIn(("src/main.rs::main", "src/util.rs::run"),
                      self.edges)

    def test_self_receiver(self):
        self.assertIn(("src/util.rs::incr", "src/util.rs::bump"),
                      self.edges)

    def test_r1_same_file(self):
        self.assertIn(("src/util.rs::bump", "src/util.rs::helper"),
                      self.edges)

    def test_external_use_abstains(self):
        self.assertIn("unresolved_import",
                      _reasons(self.view, "src/main.rs::calls_bar"))

    def test_private_cross_file_abstains(self):
        reasons = _reasons(self.view, "src/main.rs::calls_priv")
        self.assertIn("unresolved_import", reasons)
        self.assertNotIn(("src/main.rs::calls_priv",
                          "src/util.rs::priv_fn"),
                         self.edges)

    def test_receipt(self):
        self.assertIn("rust_static_binding_v1", _rules(self.view))


SWIFT_FILES = {
    "pkg/Main.swift": """import Foo
func run() -> Int {
  return helper(1)
}
func build() -> Foo {
  return Foo()
}
class Worker {
  func task() -> Int {
    return self.step()
  }
  func step() -> Int {
    return helper(1)
  }
}
""",
    "pkg/Util.swift": """import Foundation
func helper(_ a: Int) -> Int {
  return a
}
""",
    "pkg/Foo.swift": """import Foundation
struct Foo {
  let x: Int
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.SWIFT_SPEC), "no swift grammar")
class SwiftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(SWIFT_FILES)
        cls.edges = _edges(cls.view)

    def test_r1_same_dir(self):
        self.assertIn(("pkg/Main.swift::run", "pkg/Util.swift::helper"),
                      self.edges)

    def test_import_bound_r2(self):
        # import Foo binds Foo; Foo() resolves to the type in Foo.swift.
        self.assertIn(("pkg/Main.swift::build", "pkg/Foo.swift::Foo"),
                      self.edges)

    def test_self_receiver(self):
        self.assertIn(("pkg/Main.swift::task", "pkg/Main.swift::step"),
                      self.edges)

    def test_receipt(self):
        self.assertIn("swift_static_binding_v1", _rules(self.view))


PHP_FILES = {
    "a.php": """<?php
require 'B.php';
function run() {
  $o = new B();
  return helper(1) + B::pub();
}
function helper($x) {
  return $x;
}
function shadow(helper $helper) {
  return 1;
}
""",
    "B.php": """<?php
class B {
  public function m($x) {
    return $this->n(1);
  }
  private function n($x) {
    return $x;
  }
  public static function pub() {
    return 1;
  }
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.PHP_SPEC), "no php grammar")
class PhpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(PHP_FILES)
        cls.edges = _edges(cls.view)

    def test_ctor(self):
        self.assertIn(("a.php::run", "B.php::B"), self.edges)

    def test_scoped_r1c(self):
        self.assertIn(("a.php::run", "B.php::pub"), self.edges)

    def test_r1_same_file(self):
        self.assertIn(("a.php::run", "a.php::helper"), self.edges)

    def test_self_receiver(self):
        self.assertIn(("B.php::m", "B.php::n"), self.edges)

    def test_receipt(self):
        self.assertIn("php_static_binding_v1", _rules(self.view))


RUBY_FILES = {
    "main.rb": """require 'helper'
require 'foo'
def run(a)
  helper(a)
end
def build()
  Foo.new(1)
end
def shadow(helper)
  helper(1)
end
def eacher(list)
  list.each do |i|
    helper(i)
  end
end
""",
    "helper.rb": """def helper(x)
  x
end
""",
    "foo.rb": """class Foo
  def initialize(x)
    @x = x
  end
end
""",
}

@unittest.skipUnless(_has_parser(unigraph.RUBY_SPEC), "no ruby grammar")
class RubyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(RUBY_FILES)
        cls.edges = _edges(cls.view)

    def test_require_r2(self):
        self.assertIn(("main.rb::run", "helper.rb::helper"), self.edges)

    def test_new_resolves_initialize(self):
        self.assertIn(("main.rb::build", "foo.rb::initialize"),
                      self.edges)

    def test_shadowed_param(self):
        self.assertIn("shadowed_name",
                      _reasons(self.view, "main.rb::shadow"))

    def test_block_body_resolves(self):
        self.assertIn(("main.rb::eacher", "helper.rb::helper"),
                      self.edges)

    def test_receipt(self):
        self.assertIn("ruby_static_binding_v1", _rules(self.view))


JULIA_FILES = {
    "main.jl": """include("util.jl")
function run(a)
  x = 1
  return helper(a)
end
function shadow(helper)
  return helper(1)
end
""",
    "util.jl": """function helper(a)
  return a
end
""",
    "Foo.jl": """struct Foo
  x
end
function make()
  return Foo(1)
end
""",
}

@unittest.skipUnless(_has_parser(unigraph.JULIA_SPEC), "no julia grammar")
class JuliaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(JULIA_FILES)
        cls.edges = _edges(cls.view)

    def test_include_pulls_scope(self):
        # include() evaluates the file in the caller's scope, so its
        # declarations resolve like same-unit definitions.
        self.assertIn(("main.jl::run", "util.jl::helper"), self.edges)

    def test_signature_param_shadow(self):
        self.assertIn("shadowed_name",
                      _reasons(self.view, "main.jl::shadow"))

    def test_ctor_same_file(self):
        self.assertIn(("Foo.jl::make", "Foo.jl::Foo"), self.edges)

    def test_receipt(self):
        self.assertIn("julia_static_binding_v1", _rules(self.view))


LUA_FILES = {
    "main.lua": """local helper = require("helper")
local function run(a)
  return helper(a)
end
local function via_method(obj)
  return obj:m(1)
end
local function shadow(helper)
  return helper(1)
end
local wrap = function(x)
  return helper(x)
end
""",
    "helper.lua": """local function helper(a)
  return a
end
return helper
""",
}

@unittest.skipUnless(_has_parser(unigraph.LUA_SPEC), "no lua grammar")
class LuaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(LUA_FILES)
        cls.edges = _edges(cls.view)

    def test_require_r2(self):
        self.assertIn(("main.lua::run", "helper.lua::helper"),
                      self.edges)

    def test_member_abstains(self):
        self.assertIn("unsupported_receiver",
                      _reasons(self.view, "main.lua::via_method"))

    def test_shadowed_param(self):
        self.assertIn("shadowed_name",
                      _reasons(self.view, "main.lua::shadow"))

    def test_closure_nested(self):
        # Anonymous function assigned to a local is not a named decl;
        # calls inside it abstain at module scope.
        diags = [d for d in self.view["diagnostics"]
                 if d.get("kind") == "nested_call"
                 and d.get("path") == "main.lua"]
        self.assertTrue(diags)

    def test_receipt(self):
        self.assertIn("lua_static_binding_v1", _rules(self.view))


BASH_FILES = {
    "main.sh": """source ./lib.sh
run() {
  helper 1
  lib_helper 2
}
helper() {
  echo hi
}
""",
    "lib.sh": """lib_helper() {
  echo lib
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.BASH_SPEC), "no bash grammar")
class BashTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(BASH_FILES)
        cls.edges = _edges(cls.view)

    def test_r1_same_file(self):
        self.assertIn(("main.sh::run", "main.sh::helper"), self.edges)

    def test_source_pulls_scope(self):
        self.assertIn(("main.sh::run", "lib.sh::lib_helper"),
                      self.edges)

    def test_receipt(self):
        self.assertIn("bash_static_binding_v1", _rules(self.view))


PS_FILES = {
    "Main.ps1": """Import-Module ./Util.psm1
function Run {
  Local 1
  Helper
}
function Local {
  1
}
function Worker {
  $o.Method(1)
}
function Init {
  Helper
}
""",
    "Util.psm1": """function Helper {
  1
}
function Local {
  2
}
""",
}

@unittest.skipUnless(_has_parser(unigraph.POWERSHELL_SPEC),
                     "no powershell grammar")
class PowershellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(PS_FILES)
        cls.edges = _edges(cls.view)

    def test_r1_same_file(self):
        self.assertIn(("Main.ps1::Run", "Main.ps1::Local"), self.edges)

    def test_module_pull(self):
        # Import-Module brings the module's functions into scope.
        self.assertIn(("Main.ps1::Run", "Util.psm1::Helper"),
                      self.edges)

    def test_member_abstains(self):
        self.assertIn("unsupported_receiver",
                      _reasons(self.view, "Main.ps1::Worker"))

    def test_module_pull_bare(self):
        self.assertIn(("Main.ps1::Init", "Util.psm1::Helper"),
                      self.edges)

    def test_receipt(self):
        self.assertIn("powershell_static_binding_v1",
                      _rules(self.view))


@unittest.skipUnless(_has_parser(unigraph.CPP_SPEC), "no cpp grammar")
class CppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(CPP_FILES)
        cls.edges = _edges(cls.view)

    def test_r1_same_dir(self):
        self.assertIn(("src/main.cpp::main", "src/util.cpp::helper"),
                      self.edges)

    def test_qualified_def_is_method(self):
        self.assertIn(("src/A.cpp::m", "src/A.cpp::m"), self.edges)

    def test_r1c_qualified_call(self):
        self.assertIn(("src/main.cpp::main", "src/A.cpp::create"),
                      self.edges)

    def test_ctor(self):
        self.assertIn(("src/main.cpp::main", "src/A.cpp::A"),
                      self.edges)

    def test_receipt(self):
        self.assertIn("cpp_static_binding_v1", _rules(self.view))
