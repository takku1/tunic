"""TypeScript/JavaScript verified static-call extraction (tree-sitter).

Parser choice (recorded 2026-11-02): tree-sitter with the
tree-sitter-typescript grammars (`typescript` for .ts/.mts/.cts/.js/
.mjs/.cjs, `tsx` for .tsx/.jsx). Evaluated and rejected:
pyjsparser/esprima (JavaScript-only, no type syntax to strip soundly);
stdlib tokenizer (correct shadowing is doable, but generics, JSX,
decorators, `as`/`satisfies` casts, and ambient syntax force broad
bail-outs, so abstention would cover syntax instead of semantics).
Only Parser/Language/Node/Tree APIs are used (stable across 0.x; the
0.26 Query API is avoided). ERROR nodes never crash: the enclosing
construct abstains with an unsupported_syntax diagnostic.

Proof rules (v1):
- R1: same-module call `name(...)` where `name` is the single
  top-level `function` declaration (overload signatures plus one
  implementation count as one) or a single module-scope `const`
  binding, unshadowed and unreassigned at the call site.
- R2: calls through `import { orig [as local] } from "<relative>"`
  where the specifier resolves to one indexed file whose single
  matching top-level function/const is directly exported. Default
  imports resolve only to a uniquely default-exported function.
  Barrel re-exports, `export *`, namespaces, dynamic imports, and
  bare specifiers abstain.
- Decorated targets abstain; `let`/`var`/class targets abstain.
A verified edge means a statically established call target, not proof
that execution reaches the call. Everything else yields an
actionable unresolved/nested diagnostic, never an invented edge.
"""

try:
    import tree_sitter
    import tree_sitter_typescript
    _HAVE_PARSER = True
except ImportError:
    tree_sitter = None
    tree_sitter_typescript = None
    _HAVE_PARSER = False

from .identity import identity

TS_EXTENSIONS = frozenset({
    ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})
_TSX_EXTENSIONS = frozenset({".tsx", ".jsx"})

_PARSERS = {}


def is_ts_path(path):
    """Whether v1 resolves call edges for this path (.astro excluded)."""
    from pathlib import Path
    return Path(path).suffix.lower() in TS_EXTENSIONS


def _parser(tsx):
    if not _HAVE_PARSER:
        return None
    key = "tsx" if tsx else "ts"
    parser = _PARSERS.get(key)
    if parser is None:
        language = tree_sitter.Language(
            tree_sitter_typescript.language_tsx() if tsx
            else tree_sitter_typescript.language_typescript())
        parser = tree_sitter.Parser(language)
        _PARSERS[key] = parser
    return parser


def _text(data, node):
    return data[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _named(node):
    return [c for c in node.children if c.is_named]


def _field(node, *names):
    for name in names:
        child = node.child_by_field_name(name)
        if child is not None:
            return child
    return None


def _decl_name(node):
    name = _field(node, "name")
    return name.text.decode("utf-8", "replace") if name is not None else None


def parse_ts(data, suffix):
    """Parse one module; None without the parser or on parse failure."""
    if not _HAVE_PARSER:
        return None
    try:
        return _parser(suffix in _TSX_EXTENSIONS).parse(data)
    except (ValueError, TypeError, AttributeError):
        return None


def _specifier_string(node):
    for child in _named(node):
        if child.type == "string":
            for part in _named(child):
                if part.type == "string_fragment":
                    return _text_of(part)
    return None


def _text_of(node):
    return node.text.decode("utf-8", "replace")


def _pattern_names(node):
    """All bound identifiers in a binding pattern, left to right."""
    names = []
    if node.type == "identifier":
        return [node.text.decode("utf-8", "replace")]
    for child in _named(node):
        if child.type in ("identifier",
                          "shorthand_property_identifier_pattern"):
            names.append(child.text.decode("utf-8", "replace"))
        elif child.type in ("object_pattern", "array_pattern",
                            "object_assignment_pattern",
                            "assignment_pattern", "rest_pattern",
                            "pair_pattern"):
            names.extend(_pattern_names(child))
    return names


def _scan_import(node, data):
    """Import bindings of one import_statement.

    Returns (bindings, namespace, type_only) where bindings maps the
    local name to (original, default). Namespace and side-effect-only
    imports bind nothing here.
    """
    bindings, namespace, type_only = {}, None, False
    for child in node.children:
        if child.type == "type" and not child.is_named:
            type_only = True
    clause = None
    for child in _named(node):
        if child.type == "import_clause":
            clause = child
            break
    if clause is None:
        return bindings, namespace, type_only
    for child in _named(clause):
        if child.type == "identifier":
            bindings[_text_of(child)] = (child.text.decode("utf-8",
                                                            "replace"),
                                          True)
        elif child.type == "namespace_import":
            for leaf in _named(child):
                if leaf.type == "identifier":
                    namespace = leaf.text.decode("utf-8", "replace")
        elif child.type == "named_imports":
            for spec in _named(child):
                if spec.type != "import_specifier":
                    continue
                leaves = [leaf for leaf in _named(spec)
                          if leaf.type == "identifier"]
                if not leaves:
                    continue
                original = leaves[0].text.decode("utf-8", "replace")
                local = (leaves[-1].text.decode("utf-8", "replace")
                         if len(leaves) > 1 else original)
                bindings[local] = (original, False)
    return bindings, namespace, type_only


def scan_module(path, data, suffix):
    """Collect module-level declarations, imports, and exports.

    Returns (module, error_nodes) where module has decls (name to list
    of declaration dicts), imports (local to import dict), exports,
    and reassigned names. ERROR nodes are skipped by callers with an
    unsupported_syntax diagnostic, never walked for bindings.
    """
    tree = parse_ts(data, suffix)
    module = {"path": path, "decls": {}, "imports": {},
              "type_imports": set(), "namespaces": set(),
              "exports": {"named": {}, "star": False,
                           "reexport": [], "default_name": None},
              "reassigned": set(), "has_jsx": False, "has_error": False}
    if tree is None:
        module["has_error"] = True
        return module, []
    errors = []

    def note_error(node):
        module["has_error"] = True
        errors.append(node)

    def add_decl(name, decl):
        if name:
            module["decls"].setdefault(name, []).append(decl)

    def scan_statement(node, export_info=None):
        """One top-level statement; export_info marks `export ...`."""
        if node.type == "ERROR" or node.type == "MISSING":
            note_error(node)
            return
        if node.type == "export_statement":
            scan_export(node, data)
            return
        if node.type == "import_statement":
            scan_named_import(node, data)
            return
        if node.type in ("function_declaration",
                         "generator_function_declaration",
                         "function_signature"):
            name = _decl_name(node)
            add_decl(name, _function_decl(node, data, export_info))
        elif node.type == "lexical_declaration":
            scan_lexical(node, data, export_info)
        elif node.type == "variable_declaration":
            scan_lexical(node, data, export_info)
        elif node.type == "class_declaration":
            name = _decl_name(node)
            add_decl(name, {"kind": "class", "node": node,
                            "exported": bool(export_info),
                            "decorated": _has_decorator(node),
                            "has_body": True,
                            "span": (node.start_byte, node.end_byte),
                            "line": node.start_point[0] + 1})
        elif node.type in ("interface_declaration", "type_alias_declaration"):
            name = _decl_name(node)
            add_decl(name, {"kind": "type", "node": node,
                            "exported": bool(export_info),
                            "decorated": False, "has_body": False,
                            "span": (node.start_byte, node.end_byte),
                            "line": node.start_point[0] + 1})
        elif node.type == "enum_declaration":
            name = _decl_name(node)
            add_decl(name, {"kind": "enum", "node": node,
                            "exported": bool(export_info),
                            "decorated": False, "has_body": True,
                            "span": (node.start_byte, node.end_byte),
                            "line": node.start_point[0] + 1})
        elif node.type in ("ambient_declaration", "namespace_declaration"):
            return

    def scan_export(node, data):
        exported = {"default": False, "names": {}, "from": None,
                     "star": False, "declaration": None}
        for child in node.children:
            if child.type == "default" and not child.is_named:
                exported["default"] = True
            elif child.type == "export_clause":
                for spec in _named(child):
                    if spec.type != "export_specifier":
                        continue
                    leaves = [leaf for leaf in _named(spec)
                              if leaf.type == "identifier"]
                    if not leaves:
                        continue
                    original = leaves[0].text.decode("utf-8", "replace")
                    alias = (leaves[-1].text.decode("utf-8", "replace")
                             if len(leaves) > 1 else original)
                    exported["names"][alias] = original
            elif child.type == "namespace_export":
                exported["star"] = True
            elif child.type == "string":
                for part in _named(child):
                    if part.type == "string_fragment":
                        exported["from"] = part.text.decode("utf-8",
                                                            "replace")
            elif child.type == "export_all":
                exported["star"] = True
            elif child.is_named and child.type not in (
                    "export", "default", ",", ";", "as", "from", "*"):
                exported["declaration"] = child
        if exported["from"] is not None:
            if exported["star"]:
                module["exports"]["star"] = True
            else:
                for alias, original in exported["names"].items():
                    module["exports"]["reexport"].append(
                        (original, alias, exported["from"]))
            return
        if exported["star"]:
            module["exports"]["star"] = True
            return
        if exported["declaration"] is not None:
            if exported["declaration"].type in ("export_statement",
                                                 "import_statement"):
                return
            scan_statement(exported["declaration"],
                           export_info={"default": exported["default"]})
            decl_name = _decl_name(exported["declaration"])
            if exported["default"] and decl_name:
                module["exports"]["default_name"] = decl_name
            return
        for alias, original in exported["names"].items():
            module["exports"]["named"][alias] = original
        if exported["default"] and "default" in exported["names"]:
            module["exports"]["default_name"] = exported["names"]["default"]

    def scan_named_import(node, data):
        bindings, namespace, type_only = _scan_import(node, data)
        spec = _specifier_string(node)
        for local, (original, default) in bindings.items():
            module["imports"][local] = {"original": original,
                                        "default": default,
                                        "specifier": spec,
                                        "type_only": type_only,
                                        "node": node,
                                        "span": (node.start_byte,
                                                 node.end_byte),
                                        "line": node.start_point[0] + 1}
        if type_only:
            module["type_imports"].update(bindings)
        if namespace:
            module["namespaces"].add(namespace)

    def scan_lexical(node, data, export_info):
        kind = "const"
        for child in node.children:
            if not child.is_named and child.type in ("const", "let", "var"):
                kind = child.type
        for child in _named(node):
            if child.type != "variable_declarator":
                continue
            names = _pattern_names(_field(child, "name"))
            value = _field(child, "value")
            initializer = (value.type if value is not None else None)
            for name in names:
                add_decl(name, {"kind": kind, "node": child,
                                "exported": bool(export_info),
                                "decorated": False,
                                "has_body": initializer in (
                                    "arrow_function", "function_expression",
                                    "generator_function"),
                                "value_node": value,
                                "span": (child.start_byte, child.end_byte),
                                "line": child.start_point[0] + 1})

    for statement in _named(tree.root_node):
        scan_statement(statement)
    scan_reassigned(tree.root_node, data, module["reassigned"])
    return module, errors


def scan_reassigned(node, data, names):
    """Collect bare-identifier assignment targets in a subtree."""
    if node.type in ("assignment_expression",
                     "augmented_assignment_expression"):
        left = _field(node, "left")
        if left is not None and left.type == "identifier":
            names.add(left.text.decode("utf-8", "replace"))
    elif node.type == "update_expression":
        argument = _field(node, "argument")
        if argument is not None and argument.type == "identifier":
            names.add(argument.text.decode("utf-8", "replace"))
    for child in _named(node):
        if child.type in ("function_declaration",
                           "generator_function_declaration",
                           "function_expression", "arrow_function",
                           "function_signature", "method_definition",
                           "class_declaration", "class_expression"):
            continue
        scan_reassigned(child, data, names)


def _has_decorator(node):
    for child in node.children:
        if child.type == "decorator":
            return True
    return False


class _Scope:
    """Lexical scope: name to binding dicts, chained to the parent."""

    __slots__ = ("bindings", "parent")

    def __init__(self, parent=None):
        self.bindings = {}
        self.parent = parent

    def bind(self, name, kind, node=None):
        if name:
            self.bindings.setdefault(name, {"kind": kind, "node": node})

    def lookup(self, name):
        """Innermost binding, skipping type-only bindings (which never
        shadow value names: `interface F` coexists with `function F`)."""
        scope = self
        while scope is not None:
            binding = scope.bindings.get(name)
            if binding is not None and binding["kind"] != "type":
                return scope, binding
            scope = scope.parent
        return None, None


_FUNCTION_NODES = frozenset({
    "function_declaration", "generator_function_declaration",
    "function_expression", "arrow_function",
    "function_signature",
})
_CLASS_NODES = frozenset({"class_declaration", "class_expression"})
_TYPE_NODES = frozenset({"interface_declaration", "type_alias_declaration"})
_SKIP_SUBTREES = frozenset({
    "ambient_declaration", "type_annotation", "type_arguments",
    "implements_clause",
})
_WRAPPER_NODES = frozenset({
    "parenthesized_expression", "as_expression", "satisfies_expression",
    "non_null_expression",
})
_JSX_NODES = frozenset({
    "jsx_element", "jsx_fragment", "jsx_self_closing_element",
})


def _bind_params(scope, params_node):
    if params_node is None:
        return
    for param in _named(params_node):
        if param.type in ("required_parameter", "optional_parameter"):
            for child in _named(param):
                if child.type in ("identifier", "object_pattern",
                                  "array_pattern", "assignment_pattern",
                                  "rest_pattern"):
                    for name in _pattern_names(child):
                        scope.bind(name, "param", param)
                    break
        elif param.type in ("rest_pattern", "identifier", "object_pattern",
                            "array_pattern", "assignment_pattern"):
            for name in _pattern_names(param):
                scope.bind(name, "param", param)


def _bind_declarators(scope, node, hoisted=False):
    """Bind lexical/variable declarators; var hoisting is handled by
    the function-level pre-scan, so block walks bind conservatively
    (over-shadowing is the safe direction)."""
    kind = "const"
    for child in node.children:
        if not child.is_named and child.type in ("const", "let", "var"):
            kind = child.type
    for child in _named(node):
        if child.type != "variable_declarator":
            continue
        name_node = _field(child, "name")
        if name_node is None:
            continue
        for name in _pattern_names(name_node):
            scope.bind(name, kind, child)


def _is_var_decl(node):
    for token in node.children:
        if not token.is_named and token.type in ("const", "let"):
            return False
    return True


def _bind_hoisted(scope, node):
    """Bind function declarations and `var`s at any block depth below
    a function body (not crossing nested function/class bodies),
    mirroring hoisting for shadow-soundness. Block-scoped `function`
    declarations in strict mode are conservatively treated as
    function-scoped (may abstain more, never less)."""
    for child in _named(node):
        kind = child.type
        if kind in ("function_declaration",
                     "generator_function_declaration"):
            scope.bind(_decl_name(child), "function", child)
        elif kind in ("arrow_function", "function_expression",
                      "method_definition", "method_signature"):
            continue
        elif kind in ("lexical_declaration", "variable_declaration"):
            if _is_var_decl(child):
                _bind_declarators(scope, child, hoisted=True)
        elif kind in _CLASS_NODES:
            scope.bind(_decl_name(child), "class", child)
        elif kind in ("ambient_declaration", "function_signature",
                      "import_statement", "export_statement",
                      "interface_declaration", "type_alias_declaration",
                      "namespace_declaration"):
            continue
        elif kind == "enum_declaration":
            scope.bind(_decl_name(child), "variable", child)
        else:
            _bind_hoisted(scope, child)


def _bind_block(scope, statements):
    for node in statements:
        if node.type in ("function_declaration",
                         "generator_function_declaration"):
            scope.bind(_decl_name(node), "function", node)
        elif node.type in ("lexical_declaration", "variable_declaration"):
            _bind_declarators(scope, node)
        elif node.type in ("class_declaration", "export_statement",
                           "import_statement"):
            pass
        elif node.type in ("interface_declaration", "type_alias_declaration"):
            scope.bind(_decl_name(node), "type", node)
        elif node.type == "enum_declaration":
            scope.bind(_decl_name(node), "variable", node)


TS_PRODUCER = "typescript-static-v1"

_MISSING = object()


def _first_error(node):
    if node.type == "ERROR" or node.type == "MISSING":
        return node
    for child in node.children:
        found = _first_error(child)
        if found is not None:
            return found
    return None


def _func_text(ctx, node):
    text = " ".join(_t(ctx, node).split())
    return text[:120]


def _emit_edge(ctx, caller_key, callee_key, call_node, extra_support=()):
    symbols = ctx["symbols"]
    if caller_key not in symbols or callee_key not in symbols:
        return False
    start = int(call_node.start_byte)
    end = int(call_node.end_byte)
    target = symbols[callee_key]
    target_ref = {k: target[k] for k in ("source", "start", "end")}
    assertion = {
        "kind": "assertion",
        "proposition": {"relation": "static_call",
                         "caller": caller_key, "callee": callee_key},
        "resolution": {"expression": ctx.get("expression", callee_key),
                       "rule": "ts_static_binding_v1",
                       "support": list(extra_support) + [target_ref]},
        "producer": TS_PRODUCER,
        "status": "syntactic_observation",
        "source": ctx["source_id"],
        "start": start,
        "end": end,
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


def _single_target(cands):
    """One provable function/const target from same-name declarations.

    Overload signatures plus implementations count as one: the last
    body wins, matching both JavaScript redeclaration semantics and
    the TypeScript convention that the implementation follows the
    signatures. Anything else (non-function values, signatures without
    any implementation, mixed const/function bindings) is unprovable.
    """
    value_decls = [d for d in cands if d["kind"] != "type"]
    if not value_decls:
        return None
    if any(d["kind"] not in ("function", "const") for d in value_decls):
        return None
    if any(_decl_decorated(d) for d in value_decls):
        return "decorated"
    consts = [d for d in value_decls if d["kind"] == "const"]
    if consts:
        if len(value_decls) == 1 and _is_fn_decl(consts[0]):
            return consts[0]
        return None
    bodies = [d for d in value_decls if d["has_body"]]
    if bodies and len(bodies) + sum(
            1 for d in value_decls if not d["has_body"]) == len(value_decls):
        return bodies[-1]
    return None


def _resolve_local(name, scope, module, ctx, namespaces):
    """Resolve a same-module call. Returns ("edge", callee_key,
    extra_support) or ("diag", reason)."""
    if name in namespaces:
        return ("diag", "namespace_import")
    found_scope, binding = scope.lookup(name)
    if found_scope is not None and found_scope is not ctx["module_scope"]:
        return ("diag", "shadowed_name")
    if binding is not None and binding["kind"] == "import":
        entry = module["imports"].get(name)
        if entry is None:
            return ("diag", "unsupported_import")
        if entry.get("type_only"):
            return ("diag", "unsupported_import")
        if entry.get("default"):
            return _resolve_default_import(name, entry, module, ctx)
        return _resolve_named_import(entry.get("original", name), entry,
                                     module, ctx)
    entry = module["imports"].get(name)
    if entry is not None and entry.get("type_only"):
        return ("diag", "unsupported_import")
    if name in ctx["reassigned"]:
        return ("diag", "reassigned_binding")
    target = _single_target(module["decls"].get(name, []))
    if target is None:
        if not module["decls"].get(name):
            return ("diag", "unresolved_name")
        return ("diag", "unproven_target")
    if target == "decorated":
        return ("diag", "decorated_target")
    callee = "%s::%s" % (ctx["path"], name)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    return ("edge", callee, ())


def _resolve_default_import(name, entry, module, ctx):
    target = _import_target(entry, module, ctx)
    if target is None:
        return ("diag", "unresolved_import")
    tpath, tmod = target
    dname = tmod["exports"]["default_name"]
    cands = tmod["decls"].get(dname) if dname else None
    if not cands:
        return ("diag", "unresolved_import")
    picked = _single_target(cands)
    if picked is None:
        return ("diag", "unproven_target")
    if picked == "decorated":
        return ("diag", "decorated_target")
    callee = "%s::%s" % (tpath, dname)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    start, end = entry["span"]
    import_ref = {"source": ctx["source_id"],
                  "start": start, "end": end}
    return ("edge", callee, (import_ref,))


def _resolve_named_import(imported, entry, module, ctx):
    target = _import_target(entry, module, ctx)
    if target is None:
        return ("diag", "unresolved_import")
    tpath, tmod = target
    cands = tmod["decls"].get(imported)
    if not cands and imported in tmod["exports"]["named"]:
        original = tmod["exports"]["named"][imported]
        cands = tmod["decls"].get(original)
        imported = original
    if not cands:
        return ("diag", "unresolved_import")
    picked = _single_target(cands)
    if picked is None:
        return ("diag", "unproven_target")
    if picked == "decorated":
        return ("diag", "decorated_target")
    if not picked.get("exported"):
        return ("diag", "unproven_target")
    callee = "%s::%s" % (tpath, imported)
    if callee not in ctx["symbols"]:
        return ("diag", "unproven_target")
    start, end = entry["span"]
    import_ref = {"source": ctx["source_id"],
                  "start": start, "end": end}
    return ("edge", callee, (import_ref,))


def _import_target(entry, module, ctx):
    """Resolve an import entry to (path, target_module)."""
    key = (id(module), entry.get("specifier"))
    path = ctx["spec_cache"].get(key, _MISSING)
    if path is _MISSING:
        path = resolve_specifier(entry.get("specifier"), ctx["path"],
                                 ctx["known_paths"])
        ctx["spec_cache"][key] = path
    if path is None or path not in ctx["known_paths"]:
        return None
    if path == ctx["path"]:
        return (path, module)
    if path in ctx["cache"]:
        target = ctx["cache"][path]
        return (path, target) if target is not None else None
    record = ctx["fetch"](path)
    if record is None:
        ctx["cache"][path] = None
        return None
    target, _errors = scan_module(path, record[1], _suffix_of(path))
    ctx["cache"][path] = target
    return (path, target)


def _collect_reassigned(node, out):
    if (node.type in _SKIP_SUBTREES or
            node.type in ("ambient_declaration",) or
            node.type in _JSX_NODES):
        return
    if node.type in ("assignment_expression",
                     "augmented_assignment_expression"):
        left = _field(node, "left")
        if left is not None:
            if left.type == "identifier":
                out.add(left.text.decode("utf-8", "replace"))
            else:
                out.update(_pattern_names(left))
    elif node.type == "update_expression":
        arg = _field(node, "argument")
        if arg is not None and arg.type == "identifier":
            out.add(arg.text.decode("utf-8", "replace"))
    for child in _named(node):
        _collect_reassigned(child, out)


def _child_ctx(ctx, caller=_MISSING, nested=None, decorator=None):
    child = dict(ctx)
    if caller is not _MISSING:
        child["caller"] = caller
    if nested is not None:
        child["nested"] = nested
    if decorator is not None:
        child["decorator"] = decorator
    return child


def _handle_call(node, scope, ctx):
    module = ctx["module"]
    if ctx.get("decorator"):
        _record_diag(ctx, "unsupported_call", node, _func_text(ctx, node),
                     {"reason": "decorator_invocation"})
        args = _field(node, "arguments")
        if args is not None:
            for child in _named(args):
                _walk(child, scope, ctx)
        return
    func = _field(node, "function")
    args = _field(node, "arguments")
    if func is None or (args is not None
                        and args.type == "template_string"):
        # Tagged template: the grammar exposes the template as the
        # arguments field. The tag itself is not a proven call; walk
        # substitutions for calls evaluated to build the template.
        for child in _named(node):
            if child.type == "template_string":
                _walk(child, scope, ctx)
        return
    _walk(func, scope, ctx)
    fnode = func
    while fnode.type in _WRAPPER_NODES:
        kids = _named(fnode)
        if not kids:
            break
        fnode = kids[0]
    if ctx.get("nested"):
        _record_diag(ctx, "nested_call", node, _func_text(ctx, node))
    elif ctx.get("caller") is None:
        _record_diag(ctx, "unresolved_call", node, _func_text(ctx, node),
                     {"reason": "module_scope"})
    elif fnode.type == "identifier":
        name = _t(ctx, fnode)
        status = _resolve_local(name, scope, module, ctx,
                                ctx["namespaces"])
        if status[0] == "edge":
            ctx["expression"] = name
            _emit_edge(ctx, ctx.get("caller"), status[1], node, status[2])
        else:
            _record_diag(ctx, "unresolved_call", node, name,
                         {"reason": status[1]})
    elif fnode.type == "member_expression":
        reason = "unsupported_receiver"
        obj = _field(fnode, "object")
        if (obj is not None and obj.type == "identifier" and
                _t(ctx, obj) in ctx["namespaces"]):
            reason = "namespace_import"
        _record_diag(ctx, "unresolved_call", node, _func_text(ctx, fnode),
                     {"reason": reason})
    elif fnode.type in ("super", "this"):
        return
    elif fnode.type == "import":
        _record_diag(ctx, "unresolved_call", node, "import",
                     {"reason": "dynamic_import"})
    else:
        _record_diag(ctx, "unsupported_call", node, _func_text(ctx, fnode),
                     {"reason": "unsupported_construct"})
    if args is not None:
        for child in _named(args):
            _walk(child, scope, ctx)


def _walk_decorator(node, scope, ctx):
    child = _child_ctx(ctx, decorator=True)
    for kid in _named(node):
        _walk(kid, scope, child)


def _walk_function_like(node, scope, ctx, caller, nested):
    for child in node.children:
        if child.type == "decorator":
            _walk_decorator(child, scope, ctx)
    params = _field(node, "parameters")
    inner = _Scope(scope)
    if node.type == "function_expression":
        kids = _named(node)
        if kids and kids[0].type == "identifier":
            inner.bind(kids[0].text.decode("utf-8", "replace"),
                       "function", node)
    _bind_params(inner, params)
    body = _field(node, "body")
    if body is None:
        return
    child = _child_ctx(ctx, caller=caller, nested=nested, decorator=False)
    if params is not None:
        for param in _named(params):
            if param.type in ("required_parameter", "optional_parameter"):
                for sub in _named(param):
                    if sub.type == "assignment_pattern":
                        val = _field(sub, "right")
                        if val is not None:
                            _walk(val, inner, child)
    if body.type == "statement_block":
        _bind_hoisted(inner, body)
        _bind_block(inner, [c for c in _named(body)])
        for stmt in _named(body):
            _walk(stmt, inner, child)
    else:
        _walk(body, inner, child)


def _walk_heritage(node, scope, ctx, key):
    for child in _named(node):
        if child.type == "call_expression":
            sub = _child_ctx(ctx, caller=key, nested=True, decorator=False)
            _record_diag(sub, "unresolved_call", child,
                         _func_text(sub, child),
                         {"reason": "class_heritage_call"})
            args = _field(child, "arguments")
            if args is not None:
                for grand in _named(args):
                    _walk(grand, scope, sub)
        elif child.type in ("arrow_function", "function_expression",
                            "function_declaration",
                            "generator_function_declaration"):
            _walk_function_like(child, scope, ctx, key, True)
        else:
            _walk_heritage(child, scope, ctx, key)


def _walk_class(node, scope, ctx):
    name = _decl_name(node)
    key = None
    if name:
        candidate = "%s::%s" % (ctx["path"], name)
        if candidate in ctx["symbols"]:
            key = candidate
    member_caller = key if key is not None else ctx.get("caller")
    for child in _named(node):
        if child.type == "class_heritage":
            _walk_heritage(child, scope, ctx, key)
            break
    body = _field(node, "body")
    if body is None:
        return
    for child in _named(body):
        ctype = child.type
        if ctype == "decorator":
            _walk_decorator(child, scope, ctx)
        elif ctype == "method_definition":
            _walk_function_like(child, scope, ctx, member_caller, True)
        elif ctype in ("method_signature", "abstract_method_signature",
                       "index_signature"):
            for grand in child.children:
                if grand.type == "decorator":
                    _walk_decorator(grand, scope, ctx)
        elif ctype == "class_static_block":
            inner = _Scope(scope)
            sub = _child_ctx(ctx, caller=member_caller, nested=True,
                             decorator=False)
            for grand in _named(child):
                if grand.type == "statement_block":
                    _bind_hoisted(inner, grand)
                    _bind_block(inner, [c for c in _named(grand)])
                    for stmt in _named(grand):
                        _walk(stmt, inner, sub)
                else:
                    _walk(grand, inner, sub)
        elif "field_definition" in ctype:
            kids = _named(child)
            sub = _child_ctx(ctx, caller=member_caller, nested=True,
                             decorator=False)
            if kids and kids[0].type == "computed_property_name":
                _walk(kids[0], scope, ctx)
                kids = kids[1:]
            for grand in kids:
                if grand.type == "decorator":
                    _walk_decorator(grand, scope, ctx)
                elif grand.type not in ("property_identifier",
                                        "private_property_identifier",
                                        "type_annotation"):
                    if grand.type in ("arrow_function",
                                      "function_expression"):
                        _walk_function_like(grand, scope, ctx, member_caller,
                                            True)
                    else:
                        _walk(grand, scope, sub)
        else:
            sub = _child_ctx(ctx, caller=member_caller, nested=True,
                             decorator=False)
            _walk(child, scope, sub)


def _walk(node, scope, ctx):
    kind = node.type
    if kind in ("ERROR", "MISSING"):
        return
    if kind in _SKIP_SUBTREES or kind == "ambient_declaration":
        return
    if kind in _JSX_NODES:
        ctx["saw_jsx"] = True
        return
    if kind in ("function_declaration",
                "generator_function_declaration"):
        _walk_function_like(node, scope, ctx, ctx.get("caller"), True)
        return
    if kind in ("arrow_function", "function_expression",
                "method_definition"):
        _walk_function_like(node, scope, ctx, ctx.get("caller"), True)
        return
    if kind in _CLASS_NODES:
        _walk_class(node, scope, ctx)
        return
    if kind == "decorator":
        _walk_decorator(node, scope, ctx)
        return
    if kind == "call_expression":
        _handle_call(node, scope, ctx)
        return
    if kind == "new_expression":
        kids = _named(node)
        if kids and not ctx.get("decorator"):
            _record_diag(ctx, "unresolved_call", kids[0],
                         _func_text(ctx, kids[0]),
                         {"reason": "unsupported_construct"})
        for child in _named(node):
            _walk(child, scope, ctx)
        return
    if kind in ("lexical_declaration", "variable_declaration"):
        for child in _named(node):
            if child.type != "variable_declarator":
                continue
            value = _field(child, "value")
            if value is None:
                continue
            if value.type in ("arrow_function", "function_expression"):
                if (value.start_byte, value.end_byte) in ctx["caller_bodies"]:
                    continue
                _walk_function_like(value, scope, ctx, ctx.get("caller"),
                                    True)
            else:
                _walk(value, scope, ctx)
        return
    if kind in ("for_statement", "for_in_statement", "for_of_statement"):
        inner = _Scope(scope)
        for child in _named(node):
            if child.type in ("lexical_declaration",
                              "variable_declaration"):
                _bind_declarators(inner, child)
        for child in _named(node):
            _walk(child, inner, ctx)
        return
    if kind == "catch_clause":
        inner = _Scope(scope)
        for child in _named(node):
            if child.type == "identifier":
                inner.bind(child.text.decode("utf-8", "replace"),
                           "param", node)
                break
        for child in _named(node):
            _walk(child, inner, ctx)
        return
    if kind == "statement_block":
        inner = _Scope(scope)
        _bind_block(inner, [c for c in _named(node)])
        for child in _named(node):
            _walk(child, inner, ctx)
        return
    for child in _named(node):
        _walk(child, scope, ctx)


def _walk_module_statement(node, scope, ctx):
    kind = node.type
    if kind in ("function_declaration",
                "generator_function_declaration"):
        for child in node.children:
            if child.type == "decorator":
                _walk_decorator(child, scope, ctx)
        return
    if kind == "export_statement":
        for child in _named(node):
            ctype = child.type
            if ctype in ("function_declaration",
                         "generator_function_declaration"):
                if _decl_name(child) is None:
                    _walk_function_like(child, scope, ctx, None, False)
                else:
                    for grand in child.children:
                        if grand.type == "decorator":
                            _walk_decorator(grand, scope, ctx)
            elif ctype in ("export_clause", "export_specifier",
                           "namespace_export", "import_statement"):
                continue
            elif ctype in ("arrow_function", "function_expression"):
                _walk_function_like(child, scope, ctx, None, False)
            else:
                _walk(child, scope, ctx)
        return
    _walk(node, scope, ctx)


def _caller_entries(module, path, symbols):
    entries = []
    bodies = {}
    skipped = []
    for name, decls in module["decls"].items():
        key = "%s::%s" % (path, name)
        if key not in symbols:
            continue
        if any(_decl_decorated(d) for d in decls
               if d["kind"] in ("function", "const")):
            skipped.append(key)
            continue
        fns = [d for d in decls if d["kind"] == "function"]
        consts = [d for d in decls if d["kind"] == "const"
                  and _is_fn_decl(d)]
        others = [d for d in decls
                  if d["kind"] not in ("function", "type", "const")
                  or (d["kind"] == "const" and not _is_fn_decl(d))]
        if others:
            continue
        if fns and not consts and len([d for d in fns
                                       if d["has_body"]]) == 1:
            target = [d for d in fns if d["has_body"]][0]
            node = target["node"]
            if node is not None:
                entries.append((key, node))
                bodies[(node.start_byte, node.end_byte)] = key
        elif consts and not fns and len(consts) == 1:
            value = consts[0].get("value_node")
            if value is not None:
                entries.append((key, value))
                bodies[(value.start_byte, value.end_byte)] = key
    return entries, bodies, skipped


def _suffix_of(path):
    from pathlib import Path
    return Path(path).suffix.lower()


def resolve_file(path, source_id, data, module, errors, symbols, fetch,
                 known_paths, cache, assertions, diagnostics):
    if module["has_error"]:
        node = errors[0] if errors else None
        line = int(node.start_point[0]) + 1 if node is not None else 1
        start = int(node.start_byte) if node is not None else 0
        diagnostics.append({"path": path, "caller": None, "line": line,
                            "start": start, "end": start,
                            "kind": "unsupported_syntax",
                            "expression": ""})
        return
    tree = parse_ts(data, _suffix_of(path))
    if tree is None or tree.root_node.has_error:
        node = None
        if tree is not None:
            node = _first_error(tree.root_node)
        line = int(node.start_point[0]) + 1 if node is not None else 1
        start = int(node.start_byte) if node is not None else 0
        diagnostics.append({"path": path, "caller": None, "line": line,
                            "start": start, "end": start,
                            "kind": "unsupported_syntax",
                            "expression": ""})
        return
    namespaces = set(module["namespaces"])
    module_scope = _Scope()
    for name, decls in module["decls"].items():
        kinds = {d["kind"] for d in decls}
        if "type" in kinds and len(kinds) == 1:
            module_scope.bind(name, "type")
        else:
            module_scope.bind(name, "module")
    for local, entry in module["imports"].items():
        if entry["type_only"]:
            module_scope.bind(local, "type")
        else:
            module_scope.bind(local, "import")
    reassigned = set()
    _collect_reassigned(tree.root_node, reassigned)
    local_assertions = []
    entries, bodies, skipped = _caller_entries(module, path, symbols)
    for key in skipped:
        symbol = symbols.get(key, {})
        diagnostics.append({"path": path, "caller": key,
                            "line": symbol.get("line", 1),
                            "start": symbol.get("start", 0),
                            "end": symbol.get("start", 0),
                            "kind": "decorated_function",
                            "expression": key.split("::")[-1]})
    ctx = {"path": path, "source_id": source_id, "data": data,
           "module": module, "symbols": symbols, "fetch": fetch,
           "known_paths": known_paths, "cache": cache,
           "spec_cache": {}, "assertions": local_assertions,
           "diagnostics": diagnostics, "module_scope": module_scope,
           "namespaces": namespaces, "reassigned": reassigned,
           "caller": None, "nested": False, "decorator": False,
           "saw_jsx": False, "expression": "", "caller_bodies": bodies}
    for key, func_node in entries:
        _walk_function_like(func_node, module_scope, ctx, key, False)
    for stmt in _named(tree.root_node):
        _walk_module_statement(stmt, module_scope, ctx)
    if ctx["saw_jsx"]:
        diagnostics.append({"path": path, "caller": None,
                            "line": 1, "start": 0, "end": 0,
                            "kind": "unsupported_syntax",
                            "expression": "jsx"})
        return
    assertions.extend(local_assertions)


def _t(ctx, node):
    return _text(ctx["data"], node)


def _is_fn_decl(decl):
    if decl["kind"] == "function":
        return True
    if decl["kind"] == "const":
        value = decl.get("value_node")
        return (value is not None and
                value.type in ("arrow_function", "function_expression"))
    return False


def _decl_decorated(decl):
    """Whether a declaration carries a decorator.

    Covers `@dec export function f` where tree-sitter attaches the
    decorator to the export statement rather than the declaration.
    A decorated callable may be replaced by its decorator, so its
    body is not an authoritative resolution target.
    """
    if decl.get("decorated"):
        return True
    node = decl.get("node")
    if node is None:
        return False
    parent = node.parent
    if parent is not None and parent.type == "export_statement":
        for child in parent.children:
            if child.type == "decorator":
                return True
    return False


def resolve_specifier(spec, from_path, known_paths):
    """Resolve a relative import specifier to a known path.

    Returns None for bare package imports or unresolvable paths.
    Resolution is lexical (no filesystem access); symlinks and
    package-exports maps are out of scope and yield None.
    """
    if not spec or not spec.startswith("."):
        return None
    from_dir = from_path.rsplit("/", 1)[0] if "/" in from_path else ""
    out = []
    for part in (from_dir + "/" + spec).split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part)
    norm = "/".join(out)
    if not norm or norm in ("..",):
        return None
    if norm.startswith("../") or norm == "..":
        return None
    cands = [norm]
    if "." not in norm.rsplit("/", 1)[-1]:
        for ext in ("ts", "tsx", "mts", "cts", "js", "jsx", "mjs", "cjs"):
            cands.append("%s.%s" % (norm, ext))
        for ext in ("ts", "tsx", "js"):
            cands.append("%s/index.%s" % (norm, ext))
    for cand in cands:
        if cand in known_paths:
            return cand
    return None


def ts_import_targets(path, data, known_paths):
    """Distinct resolved import targets of one file (for refresh)."""
    module, _errors = scan_module(path, data, _suffix_of(path))
    targets = set()
    for entry in module["imports"].values():
        target = resolve_specifier(entry.get("specifier"), path,
                                   known_paths)
        if target is not None:
            targets.add(target)
    return targets


def ts_affected_paths(touched, fresh):
    """Touched TS files plus their transitive importers within fresh."""
    known = {p for p in fresh if is_ts_path(p)}
    reverse = {}
    for path in known:
        for target in ts_import_targets(path, fresh[path]["data"], known):
            reverse.setdefault(target, set()).add(path)
    out = set(touched) & known
    queue = list(touched)
    while queue:
        current = queue.pop()
        for importer in reverse.get(current, ()):
            if importer not in out:
                out.add(importer)
                queue.append(importer)
    return out


def resolve_ts_sources(sources, symbols, assertions, diagnostics,
                       fetch=None, known_paths=None):
    """Resolve verified call edges for TS/JS caller records.

    fetch(path) -> (source_id, data) resolves cross-file import
    targets; defaults to the records themselves. known_paths defaults
    to the given records' paths.
    """
    records = [s for s in sources
               if is_ts_path(s["path"]) and s.get("data")]
    if fetch is None:
        table = {s["path"]: (s["id"], s["data"]) for s in records}
        fetch = table.get
    if known_paths is None:
        known_paths = {s["path"] for s in records}
    cache = {}
    for record in sorted(records, key=lambda s: s["path"]):
        module, errors = scan_module(record["path"], record["data"],
                                     _suffix_of(record["path"]))
        resolve_file(record["path"], record["id"], record["data"],
                     module, errors, symbols, fetch, known_paths, cache,
                     assertions, diagnostics)


def _function_decl(node, data, export_info):
    params = _field(node, "parameters")
    return {"kind": "function", "node": node,
            "exported": bool(export_info),
            "decorated": _has_decorator(node),
            "has_body": _field(node, "body") is not None,
            "span": (node.start_byte, node.end_byte),
            "line": node.start_point[0] + 1}
