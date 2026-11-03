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

SPECS = {
    "go": GO_SPEC,
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
    """
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

