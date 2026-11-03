"""Uniform tree-sitter static-call engine (spec-driven).

One generic resolver for every tree-sitter-backed language. Per-language
differences live in small declarative specs (node types, import shape,
scope unit, exportedness, specifier resolution); the resolution core —
module/unit scope, same-unit R1, import-relative R2, shadowing and
reassignment guards, uniqueness gate, edge/diag shapes — is implemented
once and shared.

Why this instead of one module per language (e.g. tsgraph.py): the
per-language proof rules R1/R2 have the same shape in every language
(a same-scope call to a unique definition, or a call through an import
that resolves to one indexed file with a unique exported definition).
Stack graphs (GitHub) and tree-sitter tags queries (.scm) prove the
pattern: a language-agnostic core with declarative per-language rules.
Neither ships query files with the installed grammar packages and a
Rust engine is out of scope for this stdlib-first reference, so specs
here name concrete node types (probed empirically from the installed
grammars) instead of query captures.

Status: v1 covers Go (`go_static_binding_v1`). TypeScript/JavaScript
stays on tsgraph.py (`ts_static_binding_v1` receipts are frozen in
committed artifacts); migrating it onto this engine is open, gated on
the TS suite passing unchanged against engine output.

A verified edge means a statically established call target, not proof
that execution reaches the call. Everything else yields an actionable
unresolved diagnostic, never an invented edge.
"""

_GO_EXTENSIONS = frozenset({".go"})

_PARSERS = {}


def _load_grammar(module_name, attr):
    module = __import__(module_name, fromlist=[attr])
    import tree_sitter
    return tree_sitter.Language(getattr(module, attr)())


def _parser(spec):
    if spec.get("parser") is not None:
        return spec["parser"]
    try:
        language = _load_grammar(spec["grammar_module"],
                                 spec["grammar_attr"])
    except ImportError:
        return None
    import tree_sitter
    parser = tree_sitter.Parser(language)
    spec["parser"] = parser
    return parser


def _text_of(node):
    return node.text.decode("utf-8", "replace")


def _children(node):
    return node.children


def _named(node):
    return [c for c in node.children if c.is_named]


def _field(node, *names):
    for name in names:
        child = node.child_by_field_name(name)
        if child is not None:
            return child
    return None


class _Scope:
    """Lexical scope chain. Bindings map names to (scope, kind)."""

    def __init__(self, parent=None):
        self.parent = parent
        self.bindings = {}

    def bind(self, name, kind):
        if name:
            self.bindings[name] = (self, kind)

    def lookup(self, name):
        scope = self
        while scope is not None:
            if name in scope.bindings:
                return scope.bindings[name]
            scope = scope.parent
        return (None, None)

    def child(self):
        return _Scope(self)


# ---------------------------------------------------------------------------
# Go spec
# ---------------------------------------------------------------------------

def _go_import_bindings(tree, data):
    """Scan one Go file for import bindings.

    Returns (bindings, namespaces) where bindings maps the local package
    name to {"original", "specifier", "span", "default": False}. Blank
    ("_") and dot (".") imports bind nothing: the former is side-effect
    only, the latter merges an unknown name set (abstains).
    """
    bindings, namespaces = {}, set()
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "import_spec":
            path_node = _field(node, "path")
            if path_node is None:
                continue
            raw = _text_of(path_node)
            spec = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] \
                and raw[0] in "\"`" else raw
            name_node = _field(node, "name")
            local = _text_of(name_node) if name_node is not None else None
            if local is None:
                local = spec.rsplit("/", 1)[-1]
            if local in ("_", "."):
                continue
            bindings[local] = {
                "original": local,
                "specifier": spec,
                "span": (int(path_node.start_byte),
                         int(path_node.end_byte)),
                "default": False,
            }
        for child in _children(node):
            stack.append(child)
    return bindings, namespaces


def _go_package_name(tree):
    for child in _named(tree.root_node):
        if child.type == "package_clause":
            for leaf in _named(child):
                if leaf.type == "package_identifier":
                    return _text_of(leaf)
    return None


def _go_resolve_import(spec, from_path, known_paths):
    """Resolve a Go import path to one repo directory.

    Unique-suffix match only: the directory whose path equals the
    specifier or ends with "/" + specifier must be unique, otherwise
    (stdlib, third-party, ambiguous) this abstains with None. No
    filesystem access and no go.mod parsing in v1.
    """
    if not spec:
        return None
    dirs = set()
    for path in known_paths:
        if "/" in path:
            dirs.add(path.rsplit("/", 1)[0])
        elif path:
            dirs.add("")
    matches = [d for d in dirs
               if d and spec and (spec == d or spec.endswith("/" + d))]
    if len(matches) != 1:
        return None
    return matches[0]


def _go_exported(name):
    return bool(name) and name[0].isupper()


GO_SPEC = {
    "language": "go",
    "extensions": _GO_EXTENSIONS,
    "grammar_module": "tree_sitter_go",
    "grammar_attr": "language",
    "rule": "go_static_binding_v1",
    "producer": "go-static-v1",
    "parser": None,
    # Unit of shared scope: all .go files in one directory.
    "scope_unit": "dir",
    # Top-level definition nodes: type -> (kind, name_field, body_field).
    # Only "function" defs are R1/R2 targets in v1; methods are indexed
    # so bodies walk as callers, but a bare name never proves a method
    # call (receiver unknown) -- mirrors tsgraph class handling.
    "def_types": {
        "function_declaration": ("function", "name"),
        "method_declaration": ("method", "name"),
    },
    "call_type": "call_expression",
    "selector_type": "selector_expression",
    # Nodes that open a nested scope for bindings (checked structurally).
    "scope_block_types": frozenset({"block"}),
    "func_literal_types": frozenset({"func_literal"}),
    "param_decl_types": frozenset({"parameter_declaration"}),
    "short_var_types": frozenset({"short_var_declaration"}),
    "var_spec_types": frozenset({"var_spec"}),
    "assignment_types": frozenset({"assignment_statement"}),
    "skip_subtree_types": frozenset(),
    "import_bindings": _go_import_bindings,
    "package_name": _go_package_name,
    "resolve_import": _go_resolve_import,
    "exported": _go_exported,
}

JAVA_SPEC = {
    "language": "java",
    "engine": "generic",
    "extensions": frozenset({".java"}),
    "grammar_module": "tree_sitter_java",
    "grammar_attr": "language",
    "rule": "java_static_binding_v1",
    "producer": "java-static-v1",
    "scope_unit": "dir",
    "def_types": {
        "method_declaration": ("method",),
        "constructor_declaration": ("method",),
    },
    "class_types": {"class_declaration", "interface_declaration",
                    "enum_declaration", "record_declaration"},
    "def_containers": {"class_declaration", "class_body",
                       "interface_declaration", "interface_body",
                       "enum_declaration", "enum_body",
                       "record_declaration", "record_body"},
    "call_types": {"method_invocation"},
    "call_shapes": {
        "method_invocation": {"obj": ["object"], "name": ["name"],
                              "member": "auto", "first": True},
    },
    "member_types": {},
    "member_shapes": {},
    "ctor_types": {"object_creation_expression"},
    "func_literal_types": {"lambda_expression"},
    "scope_block_types": {"block"},
    "local_bind_types": {
        "local_variable_declaration": {"variable_declarator"},
    },
    "param_decl_types": {"formal_parameter": set(),
                         "catch_formal_parameter": set()},
    "param_container_types": {"formal_parameters"},
    "walk_skip_types": set(),
    "self_names": {"this"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": True,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"import_declaration": {"style": "dotted"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": {"kind": "excludes", "markers": ("private",),
                     "containers": ("modifiers",)},
}

KOTLIN_SPEC = {
    "language": "kotlin",
    "engine": "generic",
    "extensions": frozenset({".kt", ".kts"}),
    "grammar_module": "tree_sitter_kotlin",
    "grammar_attr": "language",
    "rule": "kotlin_static_binding_v1",
    "producer": "kotlin-static-v1",
    "scope_unit": "dir",
    "def_types": {
        "function_declaration": ("function",),
    },
    "class_types": {"class_declaration", "object_declaration"},
    "def_containers": {"class_body", "object_body", "interface_body",
                       "companion_object"},
    "call_types": {"call_expression"},
    "call_shapes": {
        "call_expression": {"func": [], "first": True},
    },
    "member_types": {"navigation_expression"},
    "member_shapes": {},
    "ctor_types": {},
    "ctor_by_stem": True,
    "body_types": {"function_body"},
    "expr_body": True,
    "func_literal_types": {"lambda_literal"},
    "scope_block_types": {"block"},
    "local_bind_types": {
        "property_declaration": {"variable_declaration"},
        "variable_declaration": set(),
    },
    "param_decl_types": {"parameter": set()},
    "param_container_types": {"function_value_parameters"},
    "walk_skip_types": set(),
    "self_names": {"this"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": False,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"import": {"style": "dotted"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": {"kind": "excludes", "markers": ("private",),
                     "containers": ("modifiers",)},
}

SCALA_SPEC = {
    "language": "scala",
    "engine": "generic",
    "extensions": frozenset({".scala"}),
    "grammar_module": "tree_sitter_scala",
    "grammar_attr": "language",
    "rule": "scala_static_binding_v1",
    "producer": "scala-static-v1",
    "scope_unit": "dir",
    "def_types": {
        "function_definition": ("function",),
    },
    "class_types": {"class_definition", "object_definition",
                    "trait_definition"},
    "def_containers": {"template_body", "class_definition",
                       "object_definition", "trait_definition"},
    "call_types": {"call_expression"},
    "call_shapes": {
        "call_expression": {"func": ["function"]},
    },
    "member_types": {},
    "member_shapes": {
        "field_expression": {"obj": ["value"], "name": ["field"]},
    },
    "ctor_types": {"instance_expression"},
    "func_literal_types": {"lambda_expression", "anonymous_function"},
    "scope_block_types": {"block"},
    "local_bind_types": {
        "val_definition": {"tuple_pattern"},
        "var_definition": {"tuple_pattern"},
    },
    "param_decl_types": {"parameter": set()},
    "param_container_types": {"parameters"},
    "walk_skip_types": set(),
    "self_names": {"this"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": False,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"import_declaration": {"style": "multi"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": {"kind": "excludes", "markers": ("private",),
                     "containers": ("modifiers",)},
}

CSHARP_SPEC = {
    "language": "csharp",
    "engine": "generic",
    "extensions": frozenset({".cs"}),
    "grammar_module": "tree_sitter_c_sharp",
    "grammar_attr": "language",
    "rule": "csharp_static_binding_v1",
    "producer": "csharp-static-v1",
    "scope_unit": "dir",
    "def_types": {
        "method_declaration": ("method",),
        "constructor_declaration": ("method",),
    },
    "class_types": {"class_declaration", "interface_declaration",
                    "struct_declaration", "enum_declaration"},
    "def_containers": {"declaration_list", "class_declaration",
                       "interface_declaration", "struct_declaration",
                       "namespace_declaration"},
    "call_types": {"invocation_expression"},
    "call_shapes": {
        "invocation_expression": {"func": ["function"]},
    },
    "member_types": {},
    "member_shapes": {
        "member_access_expression": {"obj": ["expression"],
                                    "name": ["name"]},
    },
    "ctor_types": {"object_creation_expression"},
    "func_literal_types": {"lambda_expression",
                           "anonymous_function_expression",
                           "simple_lambda_expression"},
    "scope_block_types": {"block"},
    "local_bind_types": {
        "local_declaration_statement": {"variable_declaration",
                                        "variable_declarator"},
    },
    "param_decl_types": {"parameter": set()},
    "param_container_types": {"parameter_list"},
    "walk_skip_types": set(),
    "self_names": {"this"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": True,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"using_directive": {"style": "dotted"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": {"kind": "excludes", "markers": ("private",),
                     "containers": ("modifier",)},
}

C_SPEC = {
    "language": "c",
    "engine": "generic",
    "extensions": frozenset({".c", ".h"}),
    "grammar_module": "tree_sitter_c",
    "grammar_attr": "language",
    "rule": "c_static_binding_v1",
    "producer": "c-static-v1",
    "scope_unit": "dir",
    "def_types": {
        "function_definition": ("function",),
    },
    "class_types": set(),
    "def_containers": {},
    "call_types": {"call_expression"},
    "call_shapes": {
        "call_expression": {"func": ["function"]},
    },
    "member_types": {},
    "member_shapes": {
        "field_expression": {"obj": ["argument"], "name": ["field"]},
    },
    "ctor_types": {},
    "func_literal_types": set(),
    "scope_block_types": {"compound_statement"},
    "local_bind_types": {
        "declaration": {"init_declarator"},
    },
    "param_decl_types": {"parameter_declaration": set()},
    "param_container_types": {"parameter_list"},
    "walk_skip_types": set(),
    "self_names": set(),
    "class_name_pattern": None,
    "implicit_this_bare": False,
    "r2_kinds": ("function",),
    "import_types": {"preproc_include": {"style": "string"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": {"kind": "excludes", "markers": ("static",),
                     "containers": ("storage_class_specifier",)},
    "builtin_names": {"sizeof"},
}

CPP_SPEC = {
    "language": "cpp",
    "engine": "generic",
    "extensions": frozenset({".cpp", ".hpp", ".cc", ".hh", ".cxx"}),
    "grammar_module": "tree_sitter_cpp",
    "grammar_attr": "language",
    "rule": "cpp_static_binding_v1",
    "producer": "cpp-static-v1",
    "scope_unit": "dir",
    "def_types": {
        "function_definition": ("function",),
    },
    "method_containers": {"class_specifier", "struct_specifier"},
    "qualified_def_marker": "qualified_identifier",
    "class_types": {"class_specifier", "struct_specifier"},
    "def_containers": {"class_specifier", "struct_specifier", "field_declaration_list",
                       "namespace_definition", "declaration_list"},
    "call_types": {"call_expression"},
    "call_shapes": {
        "call_expression": {"func": ["function"]},
    },
    "member_types": {"qualified_identifier"},
    "member_shapes": {
        "field_expression": {"obj": ["argument"], "name": ["field"]},
        "qualified_identifier": {"obj": ["scope"], "name": ["name"]},
    },
    "ctor_types": {"new_expression"},
    "func_literal_types": {"lambda_expression"},
    "scope_block_types": {"compound_statement"},
    "local_bind_types": {
        "declaration": {"init_declarator"},
    },
    "param_decl_types": {"parameter_declaration": set()},
    "param_container_types": {"parameter_list"},
    "walk_skip_types": set(),
    "self_names": {"this"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": True,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"preproc_include": {"style": "string"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": None,
}

RUST_SPEC = {
    "language": "rust",
    "engine": "generic",
    "extensions": frozenset({".rs"}),
    "grammar_module": "tree_sitter_rust",
    "grammar_attr": "language",
    "rule": "rust_static_binding_v1",
    "producer": "rust-static-v1",
    "scope_unit": "file",
    "def_types": {
        "function_item": ("function",),
    },
    "method_containers": {"impl_item", "trait_item"},
    "class_types": {"struct_item", "enum_item", "union_item",
                    "trait_item"},
    "def_containers": {"declaration_list", "impl_item", "trait_item",
                       "mod_item"},
    "call_types": {"call_expression"},
    "call_shapes": {
        "call_expression": {"func": ["function"]},
    },
    "member_types": {"scoped_identifier"},
    "member_shapes": {
        "field_expression": {"obj": ["value"], "name": ["field"]},
    },
    "ctor_types": {},
    "ctor_by_stem": True,
    "func_literal_types": {"closure_expression"},
    "scope_block_types": {"block"},
    "local_bind_types": {
        "let_declaration": {"tuple_pattern"},
    },
    "param_decl_types": {"parameter": {"tuple_pattern"}},
    "param_container_types": {"parameters"},
    "walk_skip_types": set(),
    "self_names": {"self"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": False,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"use_declaration": {"style": "paths"},
                     "mod_item": {"style": "mod"}},
    "path_prefixes": ("crate::", "super::", "self::"),
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": {"kind": "requires", "markers": ("pub",),
                     "containers": ("visibility_modifier",)},
}

SWIFT_SPEC = {
    "language": "swift",
    "engine": "generic",
    "extensions": frozenset({".swift"}),
    "grammar_module": "tree_sitter_swift",
    "grammar_attr": "language",
    "rule": "swift_static_binding_v1",
    "producer": "swift-static-v1",
    "scope_unit": "dir",
    "def_types": {
        "function_declaration": ("function",),
    },
    "class_types": {"class_declaration", "struct_declaration",
                    "enum_declaration"},
    "def_containers": {"class_body", "struct_body", "enum_body",
                       "extension_body"},
    "call_types": {"call_expression"},
    "call_shapes": {
        "call_expression": {"func": [], "first": True},
    },
    "member_types": {"navigation_expression"},
    "member_shapes": {
        "navigation_expression": {"obj": ["target"], "name": []},
    },
    "ctor_types": {},
    "ctor_by_stem": True,
    "func_literal_types": {"closure_expression"},
    "scope_block_types": {"function_body", "code_block"},
    "local_bind_types": {
        "property_declaration": {"pattern", "bound_identifier"},
    },
    "param_decl_types": {"parameter": set()},
    "param_container_types": {"parameters"},
    "walk_skip_types": set(),
    "self_names": {"self"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": False,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"import_declaration": {"style": "dotted"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": None,
}

PHP_SPEC = {
    "language": "php",
    "engine": "generic",
    "extensions": frozenset({".php"}),
    "grammar_module": "tree_sitter_php",
    "grammar_attr": "language_php",
    "rule": "php_static_binding_v1",
    "producer": "php-static-v1",
    "scope_unit": "file",
    "def_types": {
        "function_definition": ("function",),
        "method_declaration": ("method",),
    },
    "class_types": {"class_declaration", "interface_declaration",
                    "trait_declaration"},
    "def_containers": {"declaration_list", "class_declaration",
                       "interface_declaration", "namespace_definition"},
    "call_types": {"function_call_expression",
                   "member_call_expression", "scoped_call_expression"},
    "call_shapes": {
        "function_call_expression": {"func": ["function"]},
        "member_call_expression": {"obj": ["object"], "name": ["name"],
                                   "member": True},
        "scoped_call_expression": {"obj": ["scope"], "name": ["name"],
                                   "member": True},
    },
    "member_types": {},
    "member_shapes": {},
    "ctor_types": {"object_creation_expression"},
    "func_literal_types": {"anonymous_function_creation",
                           "arrow_function_creation"},
    "scope_block_types": {"compound_statement"},
    "local_bind_types": {
        "assignment_expression": set(),
    },
    "param_decl_types": {"simple_parameter": set(),
                         "variadic_parameter": set()},
    "param_container_types": {"formal_parameters"},
    "walk_skip_types": set(),
    "self_names": {"this"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": False,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {"require_expression": {"style": "string"},
                     "require_once_expression": {"style": "string"},
                     "include_expression": {"style": "string"},
                     "include_once_expression": {"style": "string"}},
    "directive_calls": {},
    "directive_commands": {},
    "private_rule": {"kind": "excludes", "markers": ("private",),
                     "containers": ("visibility_modifier",)},
}

RUBY_SPEC = {
    "language": "ruby",
    "engine": "generic",
    "extensions": frozenset({".rb"}),
    "grammar_module": "tree_sitter_ruby",
    "grammar_attr": "language",
    "rule": "ruby_static_binding_v1",
    "producer": "ruby-static-v1",
    "scope_unit": "file",
    "def_types": {
        "method": ("function",),
    },
    "class_types": {"class", "module"},
    "def_containers": {"class", "module", "body_statement"},
    "call_types": {"call"},
    "call_shapes": {
        "call": {"func": ["method"], "obj": ["receiver"],
                 "name": ["method"], "member": "auto"},
    },
    "member_types": {},
    "member_shapes": {},
    "new_method": {"new": "initialize"},
    "ctor_types": {},
    "func_literal_types": set(),
    "scope_block_types": {"body_statement", "block", "do_block"},
    "local_bind_types": {
        "assignment": set(),
    },
    "param_decl_types": {"method_parameters": {"optional_parameter",
                                               "splat_parameter",
                                               "keyword_parameter",
                                               "block_parameter"},
                         "block_parameters": set()},
    "param_container_types": {"method_parameters", "block_parameters"},
    "walk_skip_types": set(),
    "self_names": {"self"},
    "class_name_pattern": "^[A-Z]",
    "implicit_this_bare": False,
    "r2_kinds": ("function", "method", "type"),
    "import_types": {},
    "directive_calls": {"require": "require", "require_relative": "require",
                        "load": "require"},
    "directive_commands": {},
    "private_rule": None,
}

JULIA_SPEC = {
    "language": "julia",
    "engine": "generic",
    "extensions": frozenset({".jl"}),
    "grammar_module": "tree_sitter_julia",
    "grammar_attr": "language",
    "rule": "julia_static_binding_v1",
    "producer": "julia-static-v1",
    "scope_unit": "file",
    "def_types": {
        "function_definition": ("function",),
    },
    "class_types": {"struct_definition"},
    "def_containers": {"module_definition"},
    "call_types": {"call_expression"},
    "call_shapes": {
        "call_expression": {"func": [], "first": True},
    },
    "member_types": {"field_expression"},
    "member_shapes": {
        "field_expression": {"obj": ["value"], "name": []},
    },
    "ctor_types": {},
    "ctor_by_stem": True,
    "func_literal_types": {"function_expression"},
    "scope_block_types": {"block"},
    "local_bind_types": {
        "assignment": set(),
    },
    "param_decl_types": {},
    "param_container_types": set(),
    "signature_params": True,
    "signature_type": "signature",
    "body_is_self": True,
    "walk_skip_types": {"signature"},
    "self_names": set(),
    "class_name_pattern": None,
    "implicit_this_bare": False,
    "r2_kinds": ("function", "type"),
    "import_types": {},
    "directive_calls": {"include": "include"},
    "directive_commands": {},
    "private_rule": None,
}

LUA_SPEC = {
    "language": "lua",
    "engine": "generic",
    "extensions": frozenset({".lua"}),
    "grammar_module": "tree_sitter_lua",
    "grammar_attr": "language",
    "rule": "lua_static_binding_v1",
    "producer": "lua-static-v1",
    "scope_unit": "file",
    "def_types": {
        "function_declaration": ("function",),
    },
    "class_types": set(),
    "def_containers": {"block"},
    "call_types": {"function_call"},
    "call_shapes": {
        "function_call": {"func": ["name"]},
    },
    "member_types": {"method_index_expression"},
    "member_shapes": {
        "method_index_expression": {"obj": ["table"],
                                    "name": ["method"]},
    },
    "ctor_types": {},
    "func_literal_types": {"function_definition"},
    "scope_block_types": {"block"},
    "local_bind_types": {
        "variable_declaration": {"assignment_statement",
                                 "variable_list"},
        "assignment_statement": {"variable_list"},
    },
    "param_decl_types": {"parameters": set()},
    "param_container_types": {"parameters"},
    "walk_skip_types": set(),
    "self_names": {"self"},
    "class_name_pattern": None,
    "implicit_this_bare": False,
    "r2_kinds": ("function",),
    "import_types": {},
    "directive_calls": {"require": "require"},
    "directive_commands": {},
    "private_rule": None,
}

BASH_SPEC = {
    "language": "bash",
    "engine": "generic",
    "extensions": frozenset({".sh", ".bash"}),
    "grammar_module": "tree_sitter_bash",
    "grammar_attr": "language",
    "rule": "bash_static_binding_v1",
    "producer": "bash-static-v1",
    "scope_unit": "file",
    "def_types": {
        "function_definition": ("function",),
    },
    "class_types": set(),
    "def_containers": {},
    "call_types": {"command"},
    "command_types": {"command"},
    "command_name_fields": ("name", "command_name"),
    "call_shapes": {
        "command": {"func": ["name"]},
    },
    "member_types": {},
    "member_shapes": {},
    "ctor_types": {},
    "func_literal_types": set(),
    "scope_block_types": {"compound_statement"},
    "local_bind_types": {},
    "param_decl_types": {},
    "param_container_types": set(),
    "walk_skip_types": set(),
    "self_names": set(),
    "class_name_pattern": None,
    "implicit_this_bare": False,
    "r2_kinds": ("function",),
    "import_types": {},
    "directive_calls": {},
    "directive_commands": {"source": "include", ".": "include"},
    "private_rule": None,
}

POWERSHELL_SPEC = {
    "language": "powershell",
    "engine": "generic",
    "extensions": frozenset({".ps1", ".psm1"}),
    "grammar_module": "tree_sitter_powershell",
    "grammar_attr": "language",
    "rule": "powershell_static_binding_v1",
    "producer": "powershell-static-v1",
    "scope_unit": "file",
    "def_types": {
        "function_statement": ("function",),
    },
    "class_types": {"class_statement"},
    "def_containers": {"statement_list"},
    "call_types": {"command", "invokation_expression"},
    "command_types": {"command"},
    "command_name_fields": ("command_name", "name"),
    "call_shapes": {
        "command": {"func": ["command_name"]},
        "invokation_expression": {"obj_child": ["variable"],
                                  "name_child": ["member_name"],
                                  "member": True},
    },
    "member_types": {},
    "member_shapes": {},
    "ctor_types": {},
    "body_types": {"script_block"},
    "func_literal_types": {"script_block"},
    "scope_block_types": {"script_block", "script_block_body"},
    "local_bind_types": {},
    "param_decl_types": {},
    "param_container_types": set(),
    "walk_skip_types": set(),
    "self_names": {"this"},
    "class_name_pattern": None,
    "implicit_this_bare": False,
    "r2_kinds": ("function",),
    "import_types": {},
    "directive_calls": {},
    "directive_commands": {"Import-Module": "import"},
    "private_rule": None,
}

SPECS = {
    "go": GO_SPEC,
    "java": JAVA_SPEC,
    "kotlin": KOTLIN_SPEC,
    "scala": SCALA_SPEC,
    "csharp": CSHARP_SPEC,
    "c": C_SPEC,
    "cpp": CPP_SPEC,
    "rust": RUST_SPEC,
    "swift": SWIFT_SPEC,
    "php": PHP_SPEC,
    "ruby": RUBY_SPEC,
    "julia": JULIA_SPEC,
    "lua": LUA_SPEC,
    "bash": BASH_SPEC,
    "powershell": POWERSHELL_SPEC,
}


def spec_for_path(path):
    from pathlib import Path
    suffix = Path(path).suffix.lower()
    for spec in SPECS.values():
        if suffix in spec["extensions"]:
            return spec
    return None


def is_unigraph_path(path):
    return spec_for_path(path) is not None


# ---------------------------------------------------------------------------
# Generic engine
# ---------------------------------------------------------------------------

def _parse(spec, data):
    parser = _parser(spec)
    if parser is None:
        return None
    try:
        return parser.parse(data)
    except (ValueError, TypeError, AttributeError):
        return None


def _bind_param_names(node, scope):
    for child in _named(node):
        if child.type == "identifier":
            scope.bind(_text_of(child), "param")


def _bind_short_var(node, scope):
    for child in _named(node):
        if child.type == "expression_list":
            for leaf in _named(child):
                if leaf.type == "identifier":
                    scope.bind(_text_of(leaf), "local")
            return
    for child in _named(node):
        if child.type == "identifier":
            scope.bind(_text_of(child), "local")


def _bind_var_spec(node, scope):
    for child in _named(node):
        if child.type == "identifier":
            scope.bind(_text_of(child), "local")
        elif child.type not in ("=",):
            break


def _collect_reassigned(tree, spec, out):
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in spec["assignment_types"]:
            for child in _named(node):
                if child.type == "expression_list":
                    for leaf in _named(child):
                        if leaf.type == "identifier":
                            out.add(_text_of(leaf))
        for child in _children(node):
            stack.append(child)


def scan_unit(spec, files):
    """Scan one scope unit (a file, or a same-dir file set for Go).

    files maps path -> data. Returns a unit dict with decls (name to
    list of declaration dicts in source order), imports per path,
    package names per path, and reassigned names unioned over the unit.
    ERROR nodes mark the file unsupported_syntax; the file is skipped,
    never walked for bindings.
    """
    if _is_v2(spec):
        return _scan_unit_v2(spec, files)
    unit = {"decls": {}, "imports": {}, "packages": {},
            "reassigned": set(), "errors": {}, "trees": {}}
    for path in sorted(files):
        data = files[path]
        tree = _parse(spec, data)
        if tree is None or tree.root_node.has_error:
            unit["errors"][path] = True
            continue
        unit["trees"][path] = tree
        bindings, _namespaces = spec["import_bindings"](tree, data)
        unit["imports"][path] = bindings
        if spec.get("package_name") is not None:
            unit["packages"][path] = spec["package_name"](tree)
        reassigned = set()
        _collect_reassigned(tree, spec, reassigned)
        unit["reassigned"] |= {(path, name) for name in reassigned}
        for child in _named(tree.root_node):
            info = spec["def_types"].get(child.type)
            if info is None:
                continue
            kind, name_field = info
            name_node = child.child_by_field_name(name_field)
            if name_node is None:
                continue
            name = _text_of(name_node)
            body = child.child_by_field_name("body")
            unit["decls"].setdefault(name, []).append({
                "kind": kind,
                "name": name,
                "node": child,
                "path": path,
                "body": body,
                "span": (int(child.start_byte), int(child.end_byte)),
                "line": int(child.start_point[0]) + 1,
            })
    return unit


def _unit_paths(spec, path, known_paths):
    if spec.get("scope_unit") == "dir":
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        return sorted(p for p in known_paths
                      if (p.rsplit("/", 1)[0] if "/" in p else "") == directory
                      and p.rsplit(".", 1)[-1].lower() in
                      {e.lstrip(".") for e in spec["extensions"]})
    return [path] if path in known_paths else []


def _single_target(decls):
    """One provable function target from same-name declarations."""
    fns = [d for d in decls if d["kind"] == "function"]
    if len(fns) != 1 or len(decls) != 1:
        return None
    return fns[0]


def _emit_edge(ctx, spec, caller_key, callee_key, call_node,
               extra_support=()):
    symbols = ctx["symbols"]
    if caller_key not in symbols or callee_key not in symbols:
        return False
    from .identity import identity
    target = symbols[callee_key]
    target_ref = {k: target[k] for k in ("source", "start", "end")}
    assertion = {
        "kind": "assertion",
        "proposition": {"relation": "static_call",
                         "caller": caller_key, "callee": callee_key},
        "resolution": {"expression": ctx.get("expression", callee_key),
                       "rule": spec["rule"],
                       "support": list(extra_support) + [target_ref]},
        "producer": spec["producer"],
        "status": "syntactic_observation",
        "source": ctx["source_id"],
        "start": int(call_node.start_byte),
        "end": int(call_node.end_byte),
        "select": True,
    }
    assertion["id"] = identity("assertion", assertion)
    ctx["assertions"].append(assertion)
    return True


def _record_diag(ctx, kind, node, expression="", extra=None):
    entry = {"path": ctx["path"], "caller": ctx.get("caller"),
             "line": int(node.start_point[0]) + 1,
             "start": int(node.start_byte), "end": int(node.end_byte),
             "kind": kind, "expression": expression}
    if extra:
        entry.update(extra)
    ctx["diagnostics"].append(entry)


def _is_external_test_caller(spec, unit, caller_path):
    """A Go external test package (foo_test) shares its directory but
    not its package scope: bare names must not resolve into the
    primary package without an import. R2 still applies."""
    if spec.get("scope_unit") != "dir":
        return False
    caller_pkg = unit["packages"].get(caller_path)
    if not caller_pkg:
        return False
    return any(p != caller_pkg and caller_pkg == p + "_test"
               for p in unit["packages"].values())


def _resolve_bare(name, scope, unit, ctx, spec):
    """R1: same-unit call to a unique visible definition."""
    found_scope, kind = scope.lookup(name)
    if kind in ("local", "param") and found_scope is not ctx["unit_scope"]:
        return ("diag", "shadowed_name")
    if kind == "import":
        return _resolve_through_import(name, scope, unit, ctx, spec)
    if _is_external_test_caller(spec, unit, ctx["path"]):
        return ("diag", "unresolved_name")
    if (ctx["path"], name) in unit["reassigned"]:
        return ("diag", "reassigned_binding")
    decls = [d for d in unit["decls"].get(name, [])
             if d["path"] in unit.get("own_paths", {d["path"]})]
    if not decls:
        return ("diag", "unresolved_name")
    visible = [d for d in decls
               if _def_visible(spec, unit, ctx["path"], d)]
    target = _single_target(visible)
    if target is None:
        return ("diag", "unproven_target")
    callee = "%s::%s" % (target["path"], name)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    return ("edge", callee, ())


def _def_visible(spec, unit, caller_path, decl):
    """A same-unit def is visible unless package rules hide it.

    Go: R1 bare names resolve only within one package (files that
    share a directory but declare different packages never build
    together, so cross-package bare calls abstain). A _test-package
    file (package foo_test) sees only exported names of the
    directory's primary package; same-package files see everything.
    Undetermined packages (missing clause) stay lenient.
    Single-file units (TS-style) see everything.
    """
    if spec.get("scope_unit") != "dir":
        return True
    caller_pkg = unit["packages"].get(caller_path)
    decl_pkg = unit["packages"].get(decl["path"])
    if caller_pkg == decl_pkg:
        return True
    if caller_pkg is None or decl_pkg is None:
        return True
    if decl_pkg and caller_pkg == decl_pkg + "_test":
        return bool(spec["exported"](decl["name"]))
    return False


def _resolve_through_import(name, scope, unit, ctx, spec):
    entry = unit["imports"].get(ctx["path"], {}).get(name)
    if entry is None:
        return ("diag", "unsupported_import")
    return _resolve_imported(entry.get("original", name), entry, unit,
                             ctx, spec)


def _ensure_dir_scanned(unit, spec, target_dir, ctx):
    """Scan one imported directory on demand (cached per unit).

    Mirrors tsgraph's per-target module cache: cross-directory R2
    needs the target's declarations, but units only pre-scan their
    own members. fetch supplies records; failures leave decls empty
    and the caller abstains.

    Generic specs track per-file (unit["trees"]) instead of per-dir:
    a file-unit's own directory is marked scanned at setup, but only
    its own member was parsed, so required files in the same
    directory must still merge on demand.
    """
    if _is_v2(spec):
        for path in sorted(ctx["known_paths"]):
            if path in unit.get("trees", ()):
                continue
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            if directory != target_dir:
                continue
            if path.rsplit(".", 1)[-1].lower() not in \
                    {e.lstrip(".") for e in spec["extensions"]}:
                continue
            hit = ctx["fetch"](path)
            if hit is not None:
                extra = _scan_unit_v2(spec, {path: hit[1]})
                for name, decls in extra["decls"].items():
                    unit["decls"].setdefault(name, []).extend(decls)
                unit["imports"].update(extra["imports"])
                unit["trees"].update(extra["trees"])
                unit["errors"].update(extra["errors"])
                _ensure_v2_symbols(unit, spec, path, ctx["symbols"],
                                   hit[0])
        return
    scanned = unit.setdefault("scanned_dirs", set())
    if target_dir in scanned:
        return
    scanned.add(target_dir)
    files = {}
    for path in sorted(ctx["known_paths"]):
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        if directory != target_dir:
            continue
        if path.rsplit(".", 1)[-1].lower() not in \
                {e.lstrip(".") for e in spec["extensions"]}:
            continue
        hit = ctx["fetch"](path)
        if hit is not None:
            files[path] = hit[1]
    if not files:
        return
    extra = scan_unit(spec, files)
    for name, decls in extra["decls"].items():
        unit["decls"].setdefault(name, []).extend(decls)
    unit["imports"].update(extra["imports"])
    unit["packages"].update(extra["packages"])
    unit["reassigned"] |= extra["reassigned"]
    unit["trees"].update(extra["trees"])
    unit["errors"].update(extra["errors"])


def _resolve_imported(imported, entry, unit, ctx, spec):
    target_dir = spec["resolve_import"](entry.get("specifier"),
                                        ctx["path"], ctx["known_paths"])
    if target_dir is None:
        return ("diag", "unresolved_import")
    _ensure_dir_scanned(unit, spec, target_dir, ctx)
    cands = [d for d in unit["decls"].get(imported, [])
             if (d["path"].rsplit("/", 1)[0] if "/" in d["path"] else "")
             == target_dir and d["kind"] == "function"
             and spec["exported"](imported)]
    if not cands:
        return ("diag", "unresolved_import")
    if len(cands) != 1:
        return ("diag", "unproven_target")
    target = cands[0]
    callee = "%s::%s" % (target["path"], imported)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    start, end = entry["span"]
    import_ref = {"source": ctx["source_id"],
                  "start": start, "end": end}
    return ("edge", callee, (import_ref,))


def _walk_call(node, scope, ctx, spec, unit):
    func = _field(node, "function")
    if func is None:
        return
    args = None
    for child in _named(node):
        if child is not func and child.type.endswith("_list"):
            args = child
            break
    _walk(func, scope, ctx, spec, unit)
    callee = func
    if callee.type == "identifier":
        name = _text_of(callee)
        if ctx.get("nested"):
            _record_diag(ctx, "nested_call", node, name)
        elif ctx.get("caller") is None:
            _record_diag(ctx, "unresolved_call", node, name,
                         {"reason": "module_scope"})
        else:
            status = _resolve_bare(name, scope, unit, ctx, spec)
            if status[0] == "edge":
                ctx["expression"] = name
                _emit_edge(ctx, spec, ctx.get("caller"), status[1], node,
                           status[2])
            else:
                _record_diag(ctx, "unresolved_call", node, name,
                             {"reason": status[1]})
    elif callee.type == spec.get("selector_type"):
        operand = _field(callee, "operand")
        field = _field(callee, "field")
        text = _text_of(callee)
        if ctx.get("nested"):
            _record_diag(ctx, "nested_call", node, text)
        elif ctx.get("caller") is None:
            _record_diag(ctx, "unresolved_call", node, text,
                         {"reason": "module_scope"})
        elif (operand is not None and operand.type == "identifier"
                and field is not None):
            alias = _text_of(operand)
            _found_scope, kind = scope.lookup(alias)
            if kind == "import":
                entry = unit["imports"].get(ctx["path"], {}).get(alias)
                if entry is not None:
                    status = _resolve_imported(_text_of(field), entry,
                                               unit, ctx, spec)
                    if status[0] == "edge":
                        ctx["expression"] = text
                        _emit_edge(ctx, spec, ctx.get("caller"), status[1],
                                   node, status[2])
                    else:
                        _record_diag(ctx, "unresolved_call", node, text,
                                     {"reason": status[1]})
                else:
                    _record_diag(ctx, "unresolved_call", node, text,
                                 {"reason": "unsupported_import"})
            else:
                _record_diag(ctx, "unresolved_call", node, text,
                             {"reason": "unsupported_receiver"})
        else:
            _record_diag(ctx, "unresolved_call", node, text,
                         {"reason": "unsupported_receiver"})
    else:
        _record_diag(ctx, "unsupported_call", node, _text_of(callee),
                     {"reason": "unsupported_construct"})
    if args is not None:
        _walk(args, scope, ctx, spec, unit)


def _walk(node, scope, ctx, spec, unit):
    if node.type == spec["call_type"]:
        _walk_call(node, scope, ctx, spec, unit)
        return
    if node.type in spec["func_literal_types"]:
        nested = dict(ctx)
        nested["nested"] = True
        params = _field(node, "parameters", "parameter_list")
        child_scope = scope.child()
        if params is not None:
            for decl in _named(params):
                if decl.type in spec["param_decl_types"]:
                    _bind_param_names(decl, child_scope)
        body = _field(node, "body", "block")
        if body is not None:
            _walk(body, child_scope, nested, spec, unit)
        return
    if node.type in spec["scope_block_types"] and ctx.get("caller") is not None:
        child_scope = scope.child()
        for child in _named(node):
            _walk_statement(child, child_scope, ctx, spec, unit)
        return
    for child in _named(node):
        _walk(child, scope, ctx, spec, unit)


def _walk_statement(node, scope, ctx, spec, unit):
    if node.type in spec["param_decl_types"]:
        _bind_param_names(node, scope)
    elif node.type in spec["short_var_types"]:
        _bind_short_var(node, scope)
    elif node.type in spec["var_spec_types"]:
        _bind_var_spec(node, scope)
    _walk(node, scope, ctx, spec, unit)


def resolve_unit(spec, unit, path, source_id, symbols, fetch, known_paths,
                 cache, assertions, diagnostics):
    """Resolve verified edges for one file inside its scanned unit."""
    if _is_v2(spec):
        _resolve_unit_v2(spec, unit, path, source_id, symbols, fetch,
                         known_paths, cache, assertions, diagnostics)
        return
    if path in unit["errors"]:
        diagnostics.append({"path": path, "caller": None, "line": 1,
                            "start": 0, "end": 0,
                            "kind": "unsupported_syntax",
                            "expression": ""})
        return
    tree = unit["trees"].get(path)
    if tree is None:
        return
    unit_scope = _Scope()
    for name in unit["decls"]:
        unit_scope.bind(name, "module")
    for bpath, bindings in unit["imports"].items():
        if bpath != path:
            continue
        for local in bindings:
            unit_scope.bind(local, "import")
    local_assertions = []
    ctx = {"path": path, "source_id": source_id,
           "symbols": symbols, "fetch": fetch,
           "known_paths": known_paths, "cache": cache,
           "assertions": local_assertions, "diagnostics": diagnostics,
           "unit_scope": unit_scope, "caller": None, "nested": False,
           "expression": ""}
    entries = _caller_entries(spec, unit, path, symbols)
    for key, body in entries:
        ctx["caller"] = key
        ctx["nested"] = False
        ctx["expression"] = ""
        scope = unit_scope.child()
        _bind_body_params(spec, body, scope)
        _walk(body, scope, ctx, spec, unit)
    ctx["caller"] = None
    for child in _named(tree.root_node):
        if child.type in spec["def_types"]:
            continue
        _walk(child, unit_scope.child(), ctx, spec, unit)
    assertions.extend(local_assertions)


def _bind_body_params(spec, body, scope):
    parent = body.parent
    if parent is None:
        return
    params = _field(parent, "parameters", "parameter_list")
    if params is None:
        for sibling in _named(parent):
            if sibling.type in spec["param_decl_types"]:
                _bind_param_names(sibling, scope)
        return
    for decl in _named(params):
        if decl.type in spec["param_decl_types"]:
            _bind_param_names(decl, scope)


def _caller_entries(spec, unit, path, symbols):
    """Map inventoried symbol keys to callable bodies (source order)."""
    entries = []
    for name in unit["decls"]:
        key = "%s::%s" % (path, name)
        if key not in symbols:
            continue
        decls = [d for d in unit["decls"][name] if d["path"] == path]
        if not decls:
            continue
        first = decls[0]
        if first["kind"] not in ("function", "method"):
            continue
        body = first.get("body")
        if body is not None:
            entries.append((key, body))
    return entries


def unit_import_targets(spec, path, data, known_paths):
    """Distinct resolved import dirs of one file (for refresh)."""
    if _is_v2(spec):
        return _unit_import_targets_v2(spec, path, data, known_paths)
    tree = _parse(spec, data)
    targets = set()
    if tree is None:
        return targets
    bindings, _namespaces = spec["import_bindings"](tree, data)
    for entry in bindings.values():
        target = spec["resolve_import"](entry.get("specifier"), path,
                                        known_paths)
        if target is not None:
            targets.add(target)
    return targets


def unit_affected_paths(spec, touched, fresh):
    """Touched files plus same-unit files plus transitive importers.

    A touched path contributes its whole unit even when the path
    itself left fresh (deleted): surviving members re-resolve instead
    of keeping rows derived from the old unit. Importers of touched
    directories join for the same reason.
    """
    known = {p for p in fresh if spec_for_path(p) is spec}
    touched_set = set(touched)
    touched_dirs = {(p.rsplit("/", 1)[0] if "/" in p else "")
                    for p in touched_set}
    out = set()
    queue = []

    def admit(path):
        # A path joins with its whole unit: resolve always covers
        # every unit member, so kept rows for a partially covered
        # unit would collide with freshly resolved ones.
        if path in out:
            return
        out.add(path)
        queue.append(path)
        for member in _unit_paths(spec, path, known):
            if member not in out:
                out.add(member)
                queue.append(member)

    for path in set(touched) & known:
        admit(path)
    for path in known:
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        if directory in touched_dirs:
            admit(path)
    reverse = {}
    cache = {}
    for path in known:
        data = fresh[path]["data"]
        key = (path, len(data))
        if key not in cache:
            cache[key] = unit_import_targets(spec, path, data, known)
        dirs = cache[key]
        unit_dirs = set()
        for member in _unit_paths(spec, path, known):
            unit_dirs.add(member.rsplit("/", 1)[0] if "/" in member else "")
        if touched_dirs & (dirs | unit_dirs):
            admit(path)
        for target_dir in dirs:
            for member in known:
                member_dir = (member.rsplit("/", 1)[0] if "/" in member
                              else "")
                if member_dir == target_dir:
                    reverse.setdefault(member, set()).add(path)
    while queue:
        current = queue.pop()
        for importer in reverse.get(current, ()):
            admit(importer)
    return out


def resolve_unigraph_sources(sources, symbols, assertions, diagnostics,
                             fetch=None, known_paths=None):
    """Resolve verified static-call edges for spec-covered files."""
    records = [s for s in sources
               if spec_for_path(s["path"]) is not None and s.get("data")]
    if fetch is None:
        table = {s["path"]: (s["id"], s["data"]) for s in records}
        fetch = table.get
    if known_paths is None:
        known_paths = {s["path"] for s in records}
    by_spec = {}
    for record in records:
        spec = spec_for_path(record["path"])
        by_spec.setdefault(spec["language"], []).append(record)
    for _language, spec_records in by_spec.items():
        spec = spec_for_path(spec_records[0]["path"])
        if _parser(spec) is None:
            for record in sorted(spec_records,
                                 key=lambda s: s["path"]):
                diagnostics.append({
                    "path": record["path"], "caller": None,
                    "line": 1, "start": 0, "end": 0,
                    "kind": "unsupported_syntax", "expression": ""})
            continue
        units = {}
        for record in spec_records:
            for member in _unit_paths(spec, record["path"], known_paths):
                units.setdefault(
                    _unit_key(spec, member),
                    set()).add(member)
        cache = {}
        for key in sorted(units):
            members = sorted(units[key])
            files = {}
            for member in members:
                hit = fetch(member)
                if hit is not None:
                    files[member] = hit[1]
            if not files:
                continue
            unit = scan_unit(spec, files)
            unit["own_paths"] = set(files)
            unit["scanned_dirs"] = {
                m.rsplit("/", 1)[0] if "/" in m else "" for m in files}
            if _is_v2(spec):
                for member in members:
                    hit = fetch(member)
                    if hit is not None:
                        _ensure_v2_symbols(unit, spec, member, symbols,
                                           hit[0])
            for member in members:
                hit = fetch(member)
                if hit is None:
                    continue
                resolve_unit(spec, unit, member, hit[0], symbols, fetch,
                             known_paths, cache, assertions, diagnostics)


def _unit_key(spec, path):
    if spec.get("scope_unit") == "dir":
        return "dir:" + (path.rsplit("/", 1)[0] if "/" in path else "")
    return "file:" + path


def affected_paths(touched, fresh):
    """Touched files plus same-unit files plus transitive importers.

    Deleted paths stay in the touched set: unit logic resolves their
    directories from path strings, so surviving members and
    importers still re-resolve.
    """
    out = set()
    for spec in SPECS.values():
        if _parser(spec) is None:
            continue
        out |= unit_affected_paths(spec, set(touched), fresh)
    return out


# ---------------------------------------------------------------------------
# Generic v2 engine (Tier-1 languages)
#
# Same R1/R2 core as the Go path, but every language-shaped decision is
# data in SPECS, not code: def discovery (def_types + def_containers),
# name/body location (field cascades with a pruned-DFS fallback), call
# shapes (func/obj/name fields), member targets (member_shapes), imports
# (import_types styles + directive names), and binding forms
# (local_bind_types + param_decl_types). The resolver below never names a
# language. Specs name only node types probed from the installed
# grammars; resolution semantics (unique-target-or-diagnostic,
# abstain-beats-invent, receipted edges) are shared.
# ---------------------------------------------------------------------------

def _is_v2(spec):
    return spec.get("engine") == "generic"


_V2_NAME_TYPES = frozenset({
    "identifier", "simple_identifier", "name", "word", "function_name",
    "command_name", "member_name", "simple_name", "field_identifier",
    "variable_name", "variable", "constant", "type_identifier",
    "namespace_identifier",
})

# Subtrees never entered when searching a bare name or member endpoint:
# type arguments/parameters, attributes/annotations, comments, decorators.
_V2_PRUNE_SUBS = ("type_argument", "type_parameter", "attribute",
                 "annotat", "comment", "decorator")


def _is_nameish(node, spec):
    return (node.type in _V2_NAME_TYPES
            or node.type in spec.get("extra_name_types", ()))


def _search_subtree(node, skip_types=()):
    """Pre-order named descendants, pruning configured subtrees."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in reversed(_named(current)):
            if child.type in skip_types:
                continue
            if any(sub in child.type for sub in _V2_PRUNE_SUBS):
                continue
            stack.append(child)


def _sig_name(node, spec):
    """Name from a signature subtree (julia): first bare name outside args."""
    skip = (set(spec.get("param_container_types", ()))
            | {"argument_list"})
    for cand in _search_subtree(node, skip):
        if cand is node:
            continue
        if _is_nameish(cand, spec):
            return _text_of(cand)
    return None


def _def_name(node, spec):
    """Decl name via field cascade, else first pruned-DFS name."""
    sigtype = spec.get("signature_type")
    if sigtype is not None:
        for child in _named(node):
            if child.type == sigtype:
                found = _sig_name(child, spec)
                if found:
                    return found
    for field in spec.get("name_fields", ("name", "declarator")):
        hit = node.child_by_field_name(field)
        if hit is None:
            continue
        if hit.type in spec.get("member_types", ()):
            return _member_name(hit, spec)
        if _is_nameish(hit, spec):
            return _text_of(hit)
        found = _def_name(hit, spec)
        if found:
            return found
    skip = (set(spec.get("body_types", ()))
            | set(spec.get("param_container_types", ()))
            | set(spec.get("walk_skip_types", ()))
            | set(spec.get("qualifier_types", ())))
    for cand in _search_subtree(node, skip):
        if cand is node:
            continue
        if _is_nameish(cand, spec):
            return _text_of(cand)
    return None


def _def_body(node, spec):
    for field in spec.get("body_fields", ("body",)):
        hit = node.child_by_field_name(field)
        if hit is not None:
            return hit
    for child in _named(node):
        if child.type in spec.get("body_types", ()):
            return child
    return None


def _is_nameish_for_bind(node, spec):
    return _is_nameish(node, spec) or node.type == "variable_name"


def _bind_from_spec(node, descend, scope, kind, spec):
    for child in _named(node):
        if child.type in descend:
            _bind_from_spec(child, descend, scope, kind, spec)
        elif _is_nameish_for_bind(child, spec):
            scope.bind(_text_of(child), kind)


def _iter_def_nodes(tree, spec):
    """Yield (node, kind) for defs, descending through containers."""
    containers = spec.get("def_containers", ())
    methods = spec.get("method_containers", ())
    qualified = spec.get("qualified_def_marker")
    classes = spec.get("class_types", ())
    stack = [(tree.root_node, (), None)]
    while stack:
        node, ancestors, parent = stack.pop()
        for child in _named(node):
            info = spec["def_types"].get(child.type)
            if info is not None:
                kind = info[0]
                if kind == "function" and (
                        any(a in methods for a in ancestors)
                        or (qualified is not None and any(
                            n.type == qualified
                            for n in _search_subtree(child)))):
                    kind = "method"
                yield child, kind, parent
                stack.append((child, ancestors + (child.type,), child))
            elif child.type in classes:
                yield child, "type", parent
                stack.append((child, ancestors + (child.type,), parent))
            elif child.type in containers:
                stack.append((child, ancestors + (child.type,), parent))


def _spec_private(spec, node):
    """A decl is private only when its own modifier child says so."""
    rule = spec.get("private_rule")
    if not rule:
        return False
    markers = rule.get("markers", ())
    containers = rule.get("containers", ())
    for child in _named(node):
        if containers and child.type not in containers:
            continue
        text = _text_of(child)
        if rule.get("kind") == "requires":
            return not any(m in text for m in markers)
        if any(m in text for m in markers):
            return True
    if rule.get("kind") == "requires":
        return True
    return False


def _spec_visible(spec, decl, caller_path):
    if decl["path"] == caller_path:
        return True
    return not decl.get("private", False)


_SIGILS = "$@"

_IMPORT_STRING_TYPES = ("string", "string_literal", "string_content",
                        "generic_token", "word")


def _norm_recv(text, spec):
    sigils = spec.get("sigils", _SIGILS)
    stripped = text.lstrip(sigils) if sigils else text
    out = []
    for char in stripped:
        if char.isalnum() or char in "_-":
            out.append(char)
        else:
            break
    return "".join(out).rstrip("-")


def _leftmost_name(node, spec):
    for cand in _search_subtree(node):
        if _is_nameish(cand, spec) or cand.type == "variable":
            return _norm_recv(_text_of(cand), spec)
    return ""


def _rightmost_name(node, spec):
    best, best_start = None, -1
    for cand in _search_subtree(node):
        if _is_nameish(cand, spec):
            if int(cand.start_byte) >= best_start:
                best, best_start = _text_of(cand), int(cand.start_byte)
    return best or ""


def _member_name(node, spec):
    if _is_nameish(node, spec):
        return _text_of(node)
    return _rightmost_name(node, spec)


def _member_endpoint(shape, callee, spec):
    """Resolve (receiver_root, name) for a member-access callee."""
    obj = None
    for field in shape.get("obj", ()):
        hit = callee.child_by_field_name(field)
        if hit is not None:
            obj = hit
            break
    if obj is None:
        for want in shape.get("obj_child", ()):
            for cand in _search_subtree(callee):
                if cand.type == want:
                    obj = cand
                    break
            if obj is not None:
                break
    name = None
    for field in shape.get("name", ()):
        hit = callee.child_by_field_name(field)
        if hit is not None:
            name = _member_name(hit, spec)
            break
    if name is None:
        for want in shape.get("name_child", ()):
            for cand in _search_subtree(callee):
                if cand.type == want:
                    name = _text_of(cand)
                    break
            if name is not None:
                break
    if name is None:
        name = _rightmost_name(callee, spec)
    recv = ""
    if obj is not None:
        raw = _text_of(obj).strip()
        if raw.lstrip(spec.get("sigils", _SIGILS)) in spec.get(
                "self_names", ()):
            recv = raw.lstrip(spec.get("sigils", _SIGILS))
        else:
            recv = _leftmost_name(obj, spec)
    if not recv:
        recv = _norm_recv(_text_of(callee).split(".")[0].split(":")[0],
                          spec)
    return recv, name


def _unquote(text):
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _import_string(node, skip=()):
    """First string-ish text under node, excluding skip subtrees."""
    marks = {(s.start_byte, s.end_byte) for s in skip}
    stack = [node]
    while stack:
        current = stack.pop()
        if (current.start_byte, current.end_byte) in marks:
            continue
        if current is not node and current.type in _IMPORT_STRING_TYPES:
            return _unquote(_text_of(current))
        for child in reversed(_named(current)):
            stack.append(child)
    return ""


def _dotted_text(node, spec):
    parts = [_text_of(c) for c in _search_subtree(node)
             if _is_nameish(c, spec) and c.type != "variable"]
    return ".".join(parts)


def _collect_imports_v2(tree, spec, data):
    """Scan one file for import entries: local -> entry dicts.

    Entries carry the raw specifier, candidate file matches, span, and
    flags (static/wildcard/class_hint). Directives (require/include/
    source/Import-Module style calls and commands) feed the same
    table; module-alias forms (using X = ...) are skipped.
    """
    out = {}
    directives = spec.get("directive_calls", {})
    commands = spec.get("directive_commands", {})
    for node in _search_subtree(tree.root_node):
        info = spec["import_types"].get(node.type)
        if info is not None:
            style = info.get("style", "dotted")
            if style == "string":
                raw = _import_string(node)
                if not raw:
                    continue
                entry = {"original": raw.rsplit("/", 1)[-1],
                         "candidates": [raw],
                         "span": (int(node.start_byte), int(node.end_byte)),
                         "kind": info.get("kind", "import")}
                out.setdefault(entry["original"], entry)
            elif style == "dotted":
                if "=" in [_text_of(c) for c in node.children
                           if not c.is_named]:
                    continue
                dotted = _dotted_text(node, spec)
                if not dotted:
                    continue
                segs = dotted.split(".")
                tokens = _text_of(node).split()
                original = segs[-1]
                if "as" in tokens and len(segs) > 1:
                    alias = tokens[tokens.index("as") + 1].strip(" ;")
                    if segs[-1] == alias:
                        segs = segs[:-1]
                    original = alias
                static = "static" in tokens
                wildcard = any(_text_of(c) in ("*", "_")
                               for c in node.children)
                entry = {"original": original,
                         "candidates": ["/".join(segs), original],
                         "span": (int(node.start_byte), int(node.end_byte)),
                         "kind": info.get("kind", "import"),
                         "static": static, "wildcard": wildcard,
                         "class_hint": segs[-2] if static and len(segs) > 1
                         else None}
                out.setdefault(original, entry)
            elif style == "multi":
                ids = [_text_of(c) for c in _search_subtree(node)
                       if c.type == "identifier"]
                if any(_text_of(c) in ("*", "_") for c in node.children):
                    dotted = ".".join(ids)
                    entry = {"original": ids[0] if ids else "",
                             "candidates": [dotted.replace(".", "/")],
                             "span": (int(node.start_byte),
                                      int(node.end_byte)),
                             "kind": info.get("kind", "import"),
                             "wildcard": True}
                    if entry["original"]:
                        out.setdefault(entry["original"], entry)
                else:
                    for original in ids[1:]:
                        entry = {"original": original,
                                 "candidates": [original],
                                 "span": (int(node.start_byte),
                                          int(node.end_byte)),
                                 "kind": info.get("kind", "import")}
                        out.setdefault(original, entry)
            elif style == "paths":
                best = None
                for cand in _search_subtree(node):
                    if cand is node:
                        continue
                    text = _text_of(cand)
                    if "::" in text and (best is None
                                         or len(text) < len(best)):
                        best = text
                raw = best.replace(" ", "") if best is not None else ""
                for prefix in spec.get("path_prefixes", ("crate::",
                                                         "super::",
                                                         "self::")):
                    if raw.startswith(prefix):
                        raw = raw[len(prefix):]
                        break
                segs = raw.split("::")
                entry = {"original": segs[-1],
                         "candidates": ["/".join(segs), segs[-1]],
                         "span": (int(node.start_byte), int(node.end_byte)),
                         "kind": info.get("kind", "import")}
                out.setdefault(entry["original"], entry)
            elif style == "mod":
                hit = node.child_by_field_name("name")
                name = _text_of(hit) if hit is not None else ""
                if not name:
                    continue
                entry = {"original": name,
                         "candidates": [name + ".rs",
                                        name + "/mod.rs"],
                         "span": (int(node.start_byte), int(node.end_byte)),
                         "kind": "mod"}
                out.setdefault(name, entry)
            continue
        name = None
        if node.type in spec.get("command_types", ()):
            hit = None
            for field in spec.get("command_name_fields",
                                  ("name", "command_name")):
                hit = node.child_by_field_name(field)
                if hit is not None:
                    break
            if hit is not None:
                name = _text_of(hit)
            directive = commands.get(name) if name else None
            arg = (_import_string(node, skip=(hit,)) if directive else "")
            if directive and arg:
                entry = {"original": arg.rsplit("/", 1)[-1],
                         "candidates": [arg],
                         "span": (int(node.start_byte), int(node.end_byte)),
                         "kind": directive}
                out.setdefault(entry["original"], entry)
        elif node.type in spec.get("call_types", ()):
            hit = None
            for field in ("name", "method", "function"):
                hit = node.child_by_field_name(field)
                if hit is not None:
                    break
            if hit is None:
                kids = _named(node)
                hit = kids[0] if kids else None
            if hit is not None:
                name = _text_of(hit)
            directive = directives.get(name) if name else None
            arg = _import_string(node) if directive else ""
            if directive and arg:
                entry = {"original": arg.rsplit("/", 1)[-1],
                         "candidates": [arg],
                         "span": (int(node.start_byte), int(node.end_byte)),
                         "kind": directive}
                out.setdefault(entry["original"], entry)
    return out


def _match_import_file(spec, entry, from_path, known_paths):
    """Resolve an import entry to target files (usually one)."""
    exts = {e.lstrip(".") for e in spec["extensions"]}
    if entry.get("wildcard"):
        want = entry["candidates"][0] if entry["candidates"] else ""
        dirs = {p.rsplit("/", 1)[0] if "/" in p else "" for p in known_paths
                if p.rsplit(".", 1)[-1].lower() in exts}
        hits = [d for d in dirs
                if d == want or d.endswith("/" + want)] if want else []
        if len(hits) == 1:
            return ("dir", hits[0])
        return None
    if entry.get("class_hint"):
        stem = entry["class_hint"]
        hits = [p for p in known_paths
                if p.rsplit("/", 1)[-1].rsplit(".", 1)[0] == stem
                and p.rsplit(".", 1)[-1].lower() in exts]
        if len(hits) == 1:
            return ("file", hits[0])
        return None
    directory = from_path.rsplit("/", 1)[0] if "/" in from_path else ""
    matches = set()
    for cand in entry.get("candidates", ()):
        if cand.startswith("./"):
            cand = cand[2:]
        base = cand.rsplit("/", 1)[-1]
        for path in known_paths:
            if path.rsplit(".", 1)[-1].lower() not in exts:
                continue
            if "/" in cand:
                if path == cand or path.endswith("/" + cand):
                    matches.add(path)
                stem = cand
                if path.rsplit(".", 1)[0] == stem or path.endswith(
                        "/" + stem + "." + path.rsplit(".", 1)[-1].lower()):
                    matches.add(path)
            else:
                name = base.rsplit(".", 1)[0]
                if (path.rsplit("/", 1)[0] if "/" in path else "") \
                        == directory and \
                        path.rsplit("/", 1)[-1].rsplit(".", 1)[0] == name:
                    matches.add(path)
    if len(matches) == 1:
        return ("file", next(iter(matches)))
    stems = {}
    for path in matches:
        stems.setdefault(
            path.rsplit("/", 1)[-1].rsplit(".", 1)[0], set()).add(path)
    base = (entry.get("candidates", [""])[-1]).rsplit("/", 1)[-1]
    base = base.rsplit(".", 1)[0]
    if base in stems:
        return ("files", sorted(stems[base]))
    return None


def _v2_single_target(decls):
    fns = [d for d in decls if d["kind"] == "function"]
    if len(fns) != 1 or len(decls) != 1:
        return None
    return fns[0]


def _v2_edge(ctx, spec, callee_key, call_node, extra_support=()):
    ctx["expression"] = ctx.get("expression") or callee_key
    return _emit_edge(ctx, spec, ctx.get("caller"), callee_key,
                      call_node, extra_support)


def _v2_resolve_bare(name, scope, unit, ctx, spec):
    """R1 for generic specs: same-file preference, implicit receivers."""
    found_scope, kind = scope.lookup(name)
    if kind in ("local", "param") and found_scope is not ctx["unit_scope"]:
        return ("diag", "shadowed_name")
    if kind == "nested":
        return ("diag", "nested_function")
    if kind == "import":
        return _v2_resolve_through_import(name, scope, unit, ctx, spec)
    if _is_external_test_caller(spec, unit, ctx["path"]):
        return ("diag", "unresolved_name")
    if (ctx["path"], name) in unit["reassigned"]:
        return ("diag", "reassigned_binding")
    decls = [d for d in unit["decls"].get(name, [])
             if d["path"] in unit.get("own_paths", {d["path"]})]
    if decls and all(d["kind"] == "type" for d in decls):
        visible = [d for d in decls if _spec_visible(spec, d, ctx["path"])]
        if len(visible) == 1:
            callee = "%s::%s" % (visible[0]["path"], name)
            if callee not in ctx["symbols"]:
                return ("diag", "unproven_target")
            return ("edge", callee, ())
        return ("diag", "unproven_target")
    if not decls:
        if spec.get("ctor_by_stem"):
            return _v2_resolve_ctor(name, unit, ctx, spec)
        return ("diag", "unresolved_name")
    same = [d for d in decls if d["path"] == ctx["path"]]
    if len(same) == 1 and _v2_single_target(same) is not None:
        target = same[0]
        callee = "%s::%s" % (target["path"], name)
        if callee not in ctx["symbols"]:
            return ("diag", "unproven_target")
        return ("edge", callee, ())
    if (len(same) == 1 and same[0]["kind"] == "method"
            and spec.get("implicit_this_bare")):
        callee = "%s::%s" % (same[0]["path"], name)
        if callee not in ctx["symbols"]:
            return ("diag", "unproven_target")
        return ("edge", callee, ())
    if same:
        return ("diag", "unproven_target")
    visible = [d for d in decls if _spec_visible(spec, d, ctx["path"])]
    target = _v2_single_target(visible)
    if target is None:
        return ("diag", "unproven_target")
    callee = "%s::%s" % (target["path"], name)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    return ("edge", callee, ())


def _v2_method_targets(unit, ctx, name, kinds=("function", "method")):
    return [d for d in unit["decls"].get(name, [])
            if d["path"] == ctx["path"] and d["kind"] in kinds
            and d["path"] in unit.get("own_paths", {d["path"]})]


def _v2_resolve_self(name, unit, ctx, spec):
    """this/self receiver: same-file unique method (or function)."""
    cands = _v2_method_targets(unit, ctx, name)
    if len(cands) != 1:
        return ("diag", "unproven_target")
    callee = "%s::%s" % (cands[0]["path"], name)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    return ("edge", callee, ())


def _v2_class_files(unit, ctx, classname):
    stems = set()
    for path in unit.get("own_paths", ()):
        stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem.lower() == classname.lower():
            stems.add(path)
    return sorted(stems)


def _v2_resolve_class_member(classname, name, unit, ctx, spec):
    """ClassName.member: unique method in the stem-matching file."""
    files = _v2_class_files(unit, ctx, classname)
    cands = [d for d in unit["decls"].get(name, [])
             if d["path"] in files and d["kind"] in ("function", "method")
             and _spec_visible(spec, d, ctx["path"])]
    if len(cands) != 1:
        return ("diag", "unproven_target")
    callee = "%s::%s" % (cands[0]["path"], name)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    return ("edge", callee, ())


def _v2_resolve_ctor(name, unit, ctx, spec):
    """Constructor call: unique visible type-decl in the unit."""
    cands = [d for d in unit["decls"].get(name, [])
             if d["path"] in unit.get("own_paths", {d["path"]})
             and d["kind"] == "type"
             and _spec_visible(spec, d, ctx["path"])]
    if len(cands) != 1:
        return ("diag", "unproven_target")
    callee = "%s::%s" % (cands[0]["path"], name)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    return ("edge", callee, ())


def _v2_resolve_through_import(name, scope, unit, ctx, spec):
    entry = unit["imports"].get(ctx["path"], {}).get(name)
    if entry is None:
        return ("diag", "unsupported_import")
    return _v2_resolve_imported(name, entry, unit, ctx, spec)


def _v2_resolve_imported(original, entry, unit, ctx, spec):
    if entry is None:
        return ("diag", "unsupported_import")
    match = _match_import_file(spec, entry, ctx["path"], ctx["known_paths"])
    if match is None:
        return ("diag", "unresolved_import")
    kind, target = match
    if kind == "dir":
        _ensure_dir_scanned(unit, spec, target, ctx)
        cands = [d for d in unit["decls"].get(original, [])
                 if (d["path"].rsplit("/", 1)[0] if "/" in d["path"] else "")
                 == target and d["kind"] in spec.get(
                     "r2_kinds", ("function", "method"))
                 and _spec_visible(spec, d, ctx["path"])]
        start, end = entry["span"]
        support = ({"source": ctx["source_id"],
                    "start": start, "end": end},)
    else:
        files = [target] if kind == "file" else list(target)
        for path in files:
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            _ensure_dir_scanned(unit, spec, directory, ctx)
        cands = [d for d in unit["decls"].get(original, [])
                 if d["path"] in files and d["kind"] in spec.get(
                     "r2_kinds", ("function", "method", "type"))
                 and _spec_visible(spec, d, ctx["path"])]
        start, end = entry["span"]
        support = ({"source": ctx["source_id"],
                    "start": start, "end": end},)
    if not cands:
        return ("diag", "unresolved_import")
    if len(cands) != 1:
        return ("diag", "unproven_target")
    callee = "%s::%s" % (cands[0]["path"], original)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    return ("edge", callee, support)


def _v2_directive_name(node, spec):
    """A require/source/Import-Module style call or command, or None."""
    if node.type in spec.get("command_types", ()):
        hit = None
        for field in spec.get("command_name_fields",
                              ("name", "command_name")):
            hit = node.child_by_field_name(field)
            if hit is not None:
                break
        name = _text_of(hit) if hit is not None else ""
        if name in spec.get("directive_commands", {}):
            return name
        return None
    if node.type in spec.get("call_types", ()):
        hit = None
        for field in ("name", "method", "function"):
            hit = node.child_by_field_name(field)
            if hit is not None:
                break
        if hit is None:
            kids = _named(node)
            hit = kids[0] if kids else None
        name = _text_of(hit) if hit is not None else ""
        if name in spec.get("directive_calls", {}):
            return name
    return None


def _v2_has_obj(shape, node):
    for field in shape.get("obj", ()):
        if node.child_by_field_name(field) is not None:
            return True
    for want in shape.get("obj_child", ()):
        for cand in _search_subtree(node):
            if cand.type == want:
                return True
    return False


def _v2_callee(node, spec):
    """Split a call node into (kind, target) for resolution."""
    shape = spec["call_shapes"][node.type]
    member = shape.get("member")
    if member is True or (member == "auto"
                          and _v2_has_obj(shape, node)):
        recv, name = _member_endpoint(shape, node, spec)
        return ("member", (recv, name))
    func = None
    for field in shape.get("func", ()):
        func = node.child_by_field_name(field)
        if func is not None:
            break
    if func is None and shape.get("first"):
        kids = _named(node)
        func = kids[0] if kids else None
    if func is None:
        return ("unknown", node)
    if func.type in spec.get("member_types", ()) or func.type in spec.get(
            "member_shapes", ()):
        shape2 = spec.get("member_shapes", {}).get(func.type, {})
        recv, name = _member_endpoint(shape2, func, spec)
        return ("member", (recv, name))
    if _is_nameish(func, spec):
        return ("plain", _text_of(func))
    return ("unknown", func)


def _v2_resolve_member(recv, name, scope, unit, ctx, spec):
    if not name:
        return ("diag", "unresolved_callee")
    if recv in spec.get("self_names", ()):
        return _v2_resolve_self(name, unit, ctx, spec)
    new_method = spec.get("new_method", {})
    if recv and name in new_method:
        return _v2_resolve_class_member(recv, new_method[name], unit,
                                        ctx, spec)
    if recv:
        _found, kind = scope.lookup(recv)
        if kind == "import":
            return _v2_resolve_imported(name, unit["imports"].get(
                ctx["path"], {}).get(recv), unit, ctx, spec)
        pattern = spec.get("class_name_pattern")
        if pattern and recv[:1].isupper():
            status = _v2_resolve_class_member(recv, name, unit, ctx, spec)
            if status[0] == "edge":
                return status
        return ("diag", "unsupported_receiver")
    return ("diag", "unresolved_callee")


def _v2_walk_call(node, scope, ctx, spec, unit):
    if _v2_directive_name(node, spec) is not None:
        return
    kind, payload = _v2_callee(node, spec)
    text = _text_of(node)
    if ctx.get("nested"):
        _record_diag(ctx, "nested_call", node, text[:80]
                     if len(text) > 80 else text)
    elif ctx.get("caller") is None:
        _record_diag(ctx, "unresolved_call", node, text[:80]
                     if len(text) > 80 else text,
                     {"reason": "module_scope"})
    elif kind == "plain":
        if payload in spec.get("builtin_names", ()):
            return
        status = _v2_resolve_bare(payload, scope, unit, ctx, spec)
        if status[0] == "edge":
            ctx["expression"] = payload
            _v2_edge(ctx, spec, status[1], node, status[2])
        else:
            _record_diag(ctx, "unresolved_call", node, payload,
                         {"reason": status[1]})
    elif kind == "member":
        recv, name = payload
        status = _v2_resolve_member(recv, name, scope, unit, ctx, spec)
        if status[0] == "edge":
            ctx["expression"] = "%s.%s" % (recv, name)
            _v2_edge(ctx, spec, status[1], node, status[2])
        else:
            _record_diag(ctx, "unresolved_call", node,
                         "%s.%s" % (recv, name),
                         {"reason": status[1]})
    else:
        _record_diag(ctx, "unsupported_call", node, text[:80]
                     if len(text) > 80 else text,
                     {"reason": "unsupported_construct"})
    for child in _named(node):
        if child.type in spec.get("func_literal_types", ()):
            nested = dict(ctx)
            nested["nested"] = True
            child_scope = scope.child()
            _v2_bind_params(child, spec, child_scope, nested)
            for grand in _named(child):
                _walk_v2(grand, child_scope, nested, spec, unit)
            continue
        _walk_v2(child, scope, ctx, spec, unit)


def _v2_walk_ctor(node, scope, ctx, spec, unit):
    name = ""
    for head in _named(node):
        if head.type in spec.get("ctor_skip_types",
                                 ("arguments", "argument_list")):
            continue
        if _is_nameish(head, spec):
            name = _text_of(head)
        else:
            inner = _rightmost_name(head, spec)
            if inner:
                name = inner
        break
    if not name:
        name = _rightmost_name(node, spec) or _leftmost_name(node, spec)
    if ctx.get("nested"):
        _record_diag(ctx, "nested_call", node, "new " + name)
    elif ctx.get("caller") is None:
        _record_diag(ctx, "unresolved_call", node, "new " + name,
                     {"reason": "module_scope"})
    else:
        status = _v2_resolve_ctor(name, unit, ctx, spec)
        if status[0] == "edge":
            ctx["expression"] = "new " + name
            _v2_edge(ctx, spec, status[1], node, status[2])
        else:
            _record_diag(ctx, "unresolved_call", node, "new " + name,
                         {"reason": status[1]})
    for child in _named(node):
        _walk_v2(child, scope, ctx, spec, unit)


def _v2_bind_params(def_node, spec, scope, ctx):
    if spec.get("signature_params"):
        for child in _named(def_node):
            if child.type == "signature":
                for sub in _search_subtree(child):
                    if sub.type == "argument_list":
                        for leaf in _named(sub):
                            if _is_nameish(leaf, spec):
                                scope.bind(_text_of(leaf), "param")
                        break
                break
    containers = spec.get("param_container_types", ())
    found = None
    for cand in _search_subtree(def_node):
        if cand is def_node:
            continue
        if cand.type in spec.get("body_types", ()):
            return
        if cand.type in containers:
            found = cand
            break
    if found is not None:
        _bind_container_children(found, spec, scope)
        return
    for child in _named(def_node):
        if child.type in spec.get("param_decl_types", ()):
            _bind_from_spec(child, spec["param_decl_types"][child.type],
                            scope, "param", spec)
        elif child.type in spec.get("param_container_types", ()):
            _bind_container_children(child, spec, scope)


def _bind_container_children(container, spec, scope):
    for child in _named(container):
        if child.type in spec.get("param_decl_types", ()):
            _bind_from_spec(child, spec["param_decl_types"][child.type],
                            scope, "param", spec)
        elif _is_nameish_for_bind(child, spec):
            scope.bind(_text_of(child), "param")


def _walk_v2(node, scope, ctx, spec, unit):
    ctx["scope"] = scope
    if node.type in spec.get("call_types", ()):
        _v2_walk_call(node, scope, ctx, spec, unit)
        return
    if node.type in spec.get("ctor_types", ()):
        _v2_walk_ctor(node, scope, ctx, spec, unit)
        return
    if node.type in spec.get("def_types", ()):
        if ctx.get("caller") is None:
            return
        name = _def_name(node, spec)
        if name:
            scope.bind(name, "nested")
        return
    if node.type in spec.get("walk_skip_types", ()):
        return
    if node.type in spec.get("func_literal_types", ()):
        nested = dict(ctx)
        nested["nested"] = True
        child_scope = scope.child()
        _v2_bind_params(node, spec, child_scope, nested)
        for child in _named(node):
            _walk_v2(child, child_scope, nested, spec, unit)
        return
    if node.type in spec.get("local_bind_types", ()):
        _bind_from_spec(node, spec["local_bind_types"][node.type],
                        scope, "local", spec)
    if node.type in spec.get("param_decl_types", ()):
        _bind_from_spec(node, spec["param_decl_types"][node.type],
                        scope, "param", spec)
    if node.type in spec.get("scope_block_types", ()) \
            and ctx.get("caller") is not None:
        child_scope = scope.child()
        for child in _named(node):
            if child.type in spec.get("param_container_types", ()):
                for leaf in _named(child):
                    if leaf.type in spec.get("param_decl_types", ()):
                        _bind_from_spec(
                            leaf, spec["param_decl_types"][leaf.type],
                            child_scope, "param", spec)
                    elif _is_nameish_for_bind(leaf, spec):
                        child_scope.bind(_text_of(leaf), "param")
        for child in _named(node):
            _walk_v2(child, child_scope, ctx, spec, unit)
        return
    for child in _named(node):
        _walk_v2(child, scope, ctx, spec, unit)


def _v2_caller_entries(spec, unit, path, symbols):
    entries = []
    for name, decls in unit["decls"].items():
        for decl in decls:
            if decl["path"] != path:
                continue
            if decl["kind"] not in ("function", "method", "type"):
                continue
            if decl["kind"] == "type":
                continue
            key = "%s::%s" % (path, name)
            if key not in symbols:
                continue
            body = decl.get("body")
            if body is None and (spec.get("body_is_self")
                                 or spec.get("expr_body")):
                body = decl["node"]
            if body is None:
                continue
            entries.append((key, decl, body))
    return entries


def _ensure_v2_symbols(unit, spec, path, symbols, source_id):
    language = spec["language"]
    for name, decls in unit["decls"].items():
        for decl in decls:
            if decl["path"] != path:
                continue
            key = "%s::%s" % (path, name)
            if key in symbols:
                continue
            node = decl["node"]
            symbols[key] = {"key": key, "path": path, "name": name,
                            "source": source_id,
                            "line": decl.get("line", 1),
                            "start": int(node.start_byte),
                            "end": int(node.end_byte),
                            "kind": decl["kind"] if decl["kind"] in (
                                "function", "method", "type") else "function",
                            "is_test": name.startswith("test_"),
                            "language": language,
                            "derivation": "unigraph_inventory_v1"}


def _scan_unit_v2(spec, files):
    unit = {"decls": {}, "imports": {}, "packages": {},
            "reassigned": set(), "errors": {}, "trees": {}}
    for path in sorted(files):
        data = files[path]
        tree = _parse(spec, data)
        if tree is None or tree.root_node.has_error:
            unit["errors"][path] = True
            continue
        unit["trees"][path] = tree
        unit["imports"][path] = _collect_imports_v2(tree, spec, data)
        for node, kind, parent in _iter_def_nodes(tree, spec):
            name = _def_name(node, spec)
            if not name:
                continue
            body = _def_body(node, spec)
            if node.type in spec.get("class_types", ()):
                kind = "type"
            decl = {"kind": kind, "name": name, "node": node,
                    "path": path, "body": body,
                    "private": _spec_private(spec, node),
                    "parent": parent,
                    "span": (int(node.start_byte), int(node.end_byte)),
                    "line": int(node.start_point[0]) + 1}
            unit["decls"].setdefault(name, []).append(decl)
    return unit


_V2_SCOPE_PULL_KINDS = ("import", "require", "include", "source",
                          "mod")
_V2_BIND_KINDS = ("import", "require", "mod")


def _v2_pull_scope(unit, spec, path, ctx):
    """Matched import targets join the visible scope.

    A require/include/source directive evaluates the target file in the
    caller's scope at runtime; a use/import entry that resolves to a
    repo file names the same file the R2 edge would cite. Either way
    the target's declarations are visible to same-unit resolution, so
    any successfully matched entry pulls its files. Unmatched entries
    (stdlib, third-party, missing) pull nothing and R2 abstains.
    """
    for local, entry in unit["imports"].get(path, {}).items():
        if entry.get("kind") not in _V2_SCOPE_PULL_KINDS:
            continue
        match = _match_import_file(spec, entry, path, ctx["known_paths"])
        if match is None:
            continue
        kind, target = match
        if kind == "dir":
            continue
        files = [target] if kind == "file" else list(target)
        for file in files:
            directory = file.rsplit("/", 1)[0] if "/" in file else ""
            _ensure_dir_scanned(unit, spec, directory, ctx)
            unit.setdefault("own_paths", set()).add(file)


def _resolve_unit_v2(spec, unit, path, source_id, symbols, fetch,
                     known_paths, cache, assertions, diagnostics):
    if path in unit["errors"]:
        diagnostics.append({"path": path, "caller": None, "line": 1,
                            "start": 0, "end": 0,
                            "kind": "unsupported_syntax",
                            "expression": ""})
        return
    tree = unit["trees"].get(path)
    if tree is None:
        return
    local_assertions = []
    early = {"path": path, "source_id": source_id,
             "symbols": symbols, "fetch": fetch,
             "known_paths": known_paths, "cache": cache,
             "assertions": local_assertions, "diagnostics": diagnostics}
    _v2_pull_scope(unit, spec, path, early)
    _ensure_v2_symbols(unit, spec, path, symbols, source_id)
    unit_scope = _Scope()
    for name in unit["decls"]:
        unit_scope.bind(name, "module")
    for bpath, bindings in unit["imports"].items():
        if bpath != path:
            continue
        for local, entry in bindings.items():
            if entry.get("kind") in _V2_BIND_KINDS:
                unit_scope.bind(local, "import")
    ctx = {"path": path, "source_id": source_id,
           "symbols": symbols, "fetch": fetch,
           "known_paths": known_paths, "cache": cache,
           "assertions": local_assertions, "diagnostics": diagnostics,
           "unit_scope": unit_scope, "caller": None, "nested": False,
           "expression": "", "scope": unit_scope}
    scopes = {}
    entries = sorted(_v2_caller_entries(spec, unit, path, symbols),
                     key=lambda e: (e[1].get("span", (0, 0))[0],
                                    e[1].get("span", (0, 0))[1]))
    for key, decl, body in entries:
        ctx["caller"] = key
        ctx["nested"] = False
        ctx["expression"] = ""
        base = scopes.get(id(decl.get("parent")), unit_scope)
        scope = base.child()
        scopes[id(decl["node"])] = scope
        _v2_bind_params(decl["node"], spec, scope, ctx)
        if body is decl["node"]:
            for child in _named(body):
                if child.type in spec.get("walk_skip_types", ()):
                    continue
                _walk_v2(child, scope, ctx, spec, unit)
        elif body.type in spec.get("func_literal_types", ()):
            for child in _named(body):
                _walk_v2(child, scope, ctx, spec, unit)
        else:
            _walk_v2(body, scope, ctx, spec, unit)
    ctx["caller"] = None
    for child in _named(tree.root_node):
        _walk_v2(child, unit_scope.child(), ctx, spec, unit)
    assertions.extend(local_assertions)


def _unit_import_targets_v2(spec, path, data, known_paths):
    tree = _parse(spec, data)
    targets = set()
    if tree is None:
        return targets
    entries = _collect_imports_v2(tree, spec, data)
    for entry in entries.values():
        match = _match_import_file(spec, entry, path, known_paths)
        if match is None:
            continue
        kind, target = match
        if kind == "dir":
            targets.add(target)
        elif kind == "file":
            targets.add(target.rsplit("/", 1)[0]
                        if "/" in target else "")
        else:
            for path in target:
                targets.add(path.rsplit("/", 1)[0]
                            if "/" in path else "")
    return targets

