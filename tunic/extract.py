"""Bounded, non-executing Python AST extraction. Unresolved is not absent."""

import ast
import io
import json
import os
import re
from pathlib import Path
import tokenize

from .identity import digest, identity

PROFILE = "python-static-v7"
EXCLUDED = {".git", ".venv", "venv", "__pycache__", ".tunic", ".research", "node_modules"}
TEXT_EXTENSIONS = {".md", ".txt", ".rst"}
CLAIM_RELATIONS = {"depends_on", "implements", "supersedes", "decided_by", "contradicts",
                   "corrects", "retracts"}
TRAVERSABLE_CLAIMS = {"depends_on", "implements"}
# Relations a retracts claim may target: asserted relationships only.
# Observations (static_call, text_mention) vanish when their source
# changes; retracting them is meaningless. Meta-operations
# (corrects, retracts) are superseded, never retracted.
RETRACTABLE = {"depends_on", "implements", "supersedes", "decided_by", "contradicts"}


def is_iso_date(value):
    """YYYY-MM-DD without importing date parsing; lexicographic order matches time order."""
    return (isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"
            and value.replace("-", "").isdigit())


def _bound_names(tree):
    """Conservative shadowing: any binding in the scope blocks resolution."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.extend(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.append(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.append(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.append(node.rest)
    return names


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return parent + "." + node.attr if parent else None
    return None


def _span(data, node):
    lines = data.splitlines(keepends=True)
    bom = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    start = sum(map(len, lines[:node.lineno-1])) + node.col_offset + (bom if node.lineno == 1 else 0)
    end = sum(map(len, lines[:node.end_lineno-1])) + node.end_col_offset + (bom if node.end_lineno == 1 else 0)
    return start, end


DOC_PHASE_KINDS = {"malformed_claims", "invalid_claim", "unknown_relation",
                   "invalid_effective", "ambiguous_reference", "unresolved_reference",
                   "unclosed_claims", "mention_candidate"}
STRUCTURE_KINDS = {"excluded_symlink"}


def _scan(root):
    """Walk the source root and read candidate files without parsing.

    Returns (entries, diagnostics) where each entry carries path, data and
    text flags. Shared by full capture and incremental change detection so
    both observe the same file set.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source root must be a directory")
    entries, diagnostics = [], []
    def scan_error(error):
        raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
        for name in sorted(dirs):
            p = Path(base) / name
            if p.is_symlink():
                diagnostics.append({"path": p.relative_to(root).as_posix(), "kind": "excluded_symlink"})
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (Path(base)/d).is_symlink())
        for name in sorted(files):
            text = Path(name).suffix in TEXT_EXTENSIONS
            if not (name.endswith(".py") or text):
                continue
            p = Path(base)/name
            path = p.relative_to(root).as_posix()
            if p.is_symlink():
                diagnostics.append({"path": path, "kind": "excluded_symlink"})
                continue
            entries.append({"path": path, "data": p.read_bytes(), "text": text})
    return entries, diagnostics


def capture(root):
    sources, diagnostics, modules = [], [], {}
    entries, walk_diagnostics = _scan(root)
    diagnostics.extend(walk_diagnostics)
    for entry in entries:
        path, data, text = entry["path"], entry["data"], entry["text"]
        file_digest = digest(data)
        source = {"path": path, "digest": file_digest, "data": data,
                  "id": identity("source", [path, file_digest]), "tree": None, "text": text}
        if text:
            sources.append(source)
            continue
        parts = list(Path(path).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        source["module"] = ".".join(parts)
        modules.setdefault(source["module"], []).append(source)
        sources.append(source)
        try:
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            if encoding not in {"utf-8", "utf-8-sig"}:
                diagnostics.append({"path": path, "kind": "unsupported_encoding", "encoding": encoding})
                continue
            source["tree"] = ast.parse(data.decode("utf-8-sig"), filename=path)
        except (SyntaxError, UnicodeError, ValueError) as error:
            diagnostics.append({"path": path, "kind": "parse_error", "line": getattr(error, "lineno", None)})
    return sources, diagnostics, modules


def module_of(path):
    """Map a repository-relative path to its module, mirroring capture."""
    parts = list(Path(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def parse_source(path, data):
    """Build a capture-style source record for one file, or a diagnostic.

    Returns (record, diagnostic): exactly one is None. Mirrors the per-file
    parse step of capture so incremental refresh reuses identical records.
    """
    file_digest = digest(data)
    record = {"path": path, "digest": file_digest, "data": data,
              "id": identity("source", [path, file_digest]), "tree": None,
              "text": False, "module": module_of(path)}
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        if encoding not in {"utf-8", "utf-8-sig"}:
            return None, {"path": path, "kind": "unsupported_encoding",
                          "encoding": encoding}
        record["tree"] = ast.parse(data.decode("utf-8-sig"), filename=path)
    except (SyntaxError, UnicodeError, ValueError) as error:
        return None, {"path": path, "kind": "parse_error",
                      "line": getattr(error, "lineno", None)}
    return record, None


def _collect_code(sources, symbols, assertions, diagnostics):
    """Populate symbols and per-file tables from parsed Python sources.

    Reads only code bytes; document sources yield document symbols. The
    module table is assembled by the caller from these records plus stored
    rows, so collection accepts any file subset. Mutates the passed-in
    containers; see extract for the full pipeline.
    """
    for s in sources:
        tree = s["tree"]
        if tree is None:
            if s.get("text") and s["data"]:
                key = s["path"] + "::document"
                symbols[key] = {"key": key, "path": s["path"], "name": "document",
                                "source": s["id"], "line": 1,
                                "start": 0, "end": len(s["data"]),
                                "is_test": False}
            continue
        # Collect module bindings without descending into function/class scopes.
        wrapper = ast.Module(body=[], type_ignores=[])
        module_names = []
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_names.append(statement.name)
            else:
                wrapper.body = [statement]
                module_names.extend(_bound_names(wrapper))
        s["bindings"] = {n for n in module_names if module_names.count(n) > 1}
        # A function may mutate a module binding through `global`; don't resolve
        # that binding elsewhere as though the initial import were immutable.
        for declaration in ast.walk(tree):
            if isinstance(declaration, ast.Global):
                s["bindings"].update(declaration.names)
        if s["bindings"]:
            diagnostics.append({"path": s["path"], "kind": "ambiguous_module_bindings", "names": sorted(s["bindings"])})
        s["functions"] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in s["bindings"]:
                key = s["path"] + "::" + node.name
                # Decorators may replace a callable; don't claim a resolved target.
                if node.decorator_list:
                    diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "decorated_function"})
                    continue
                s["functions"][node.name] = key
                start, end = _span(s["data"], node)
                symbols[key] = {"key": key, "path": s["path"], "name": node.name,
                                "source": s["id"], "line": node.lineno,
                                "start": start, "end": end,
                                "is_test": node.name.startswith("test_") or Path(s["path"]).name.startswith("test_")}
                if any(isinstance(n, ast.Call) for n in ast.walk(node.args)):
                    diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "signature_execution"})
        s["classes"] = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name in s["bindings"]:
                continue
            if node.decorator_list:
                diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "unsupported_class"})
                continue
            methods = {}
            duplicate = False
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if isinstance(item, ast.ClassDef):
                        diagnostics.append({"path": s["path"], "line": item.lineno, "kind": "unsupported_scope"})
                    continue
                if item.name in methods:
                    duplicate = True
                    continue
                if item.decorator_list:
                    diagnostics.append({"path": s["path"], "line": item.lineno, "kind": "decorated_function"})
                    continue
                if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                       for statement in item.body for n in ast.walk(statement)):
                    diagnostics.append({"path": s["path"], "line": item.lineno, "kind": "nested_scope"})
                    continue
                key = s["path"] + "::" + node.name + "." + item.name
                start, end = _span(s["data"], item)
                symbols[key] = {"key": key, "path": s["path"], "name": node.name + "." + item.name,
                                "source": s["id"], "line": item.lineno,
                                "start": start, "end": end,
                                "is_test": item.name.startswith("test_") or Path(s["path"]).name.startswith("test_")}
                methods[item.name] = key
            if duplicate:
                diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "duplicate_method"})
                for key in methods.values():
                    del symbols[key]
                continue
            s["classes"][node.name] = methods
        if (any(isinstance(n, ast.Lambda) for n in ast.walk(tree))
                or any(isinstance(n, ast.ClassDef) for statement in tree.body
                       if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                       for n in ast.walk(statement))):
            diagnostics.append({"path": s["path"], "kind": "unsupported_scope"})
        # Module-level execution/defaults are outside the call graph profile.
        if any(isinstance(n, ast.Call) for statement in tree.body
               if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               for n in ast.walk(statement)):
            diagnostics.append({"path": s["path"], "kind": "module_execution"})


def _import_map(s, tree):
    """Local import bindings for one code source.

    Shared by static-call resolution and registry-dispatch analysis so both
    see the same module bindings. Returns (imports, import_evidence, star).
    """
    imports = {}
    import_evidence = {}
    for node in tree.body:
        start, end = _span(s["data"], node)
        if isinstance(node, ast.Import):
            for a in node.names:
                binding = a.asname or a.name.split(".")[0]
                imports[binding] = a.name if a.asname else a.name.split(".")[0]
                import_evidence[binding] = {"source": s["id"], "start": start, "end": end}
        elif isinstance(node, ast.ImportFrom):
            package = s["module"].split(".") if Path(s["path"]).name == "__init__.py" else s["module"].split(".")[:-1]
            if node.level:
                if node.level > len(package):
                    continue
                base = package[:len(package)-node.level+1]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            for a in node.names:
                if a.name == "*":
                    continue
                binding = a.asname or a.name
                imports[binding] = prefix + "." + a.name if prefix else a.name
                import_evidence[binding] = {"source": s["id"], "start": start, "end": end}
    star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in tree.body)
    return imports, import_evidence, star


def _resolve_call_target(s, call, imports, modules, local, star, self_class, rebound):
    """Resolve a call's callee to a symbol key, or None.

    The unshadowed-static-binding rules, shared by pass-1 call resolution
    and pass-2 callback-candidate emission so both see identical bindings.
    Returns (target, expression, head).
    """
    name = _dotted(call.func)
    expression = name
    head = name.split(".")[0] if name else None
    constructor = None
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call):
        base = call.func.value.func
        if isinstance(base, ast.Name):
            constructor = base.id
            expression = constructor + "()." + call.func.attr
    target = None
    if name and (head not in local or head in ("self", "cls")) \
            and head not in s["bindings"] and not star:
        if "." not in name and name in s["functions"]:
            target = s["functions"][name]
        elif (self_class is not None and head in ("self", "cls") and head not in rebound
                and name.count(".") == 1):
            target = s["classes"][self_class].get(name.split(".")[1])
        elif head in s["classes"] and name.count(".") == 1:
            target = s["classes"][head].get(name.split(".")[1])
        elif head in imports:
            full = imports[head] + name[len(head):]
            module, _, function = full.rpartition(".")
            candidates = modules.get(module, [])
            if len(candidates) == 1:
                target = candidates[0].get("functions", {}).get(function)
            if target is None and len(full.split(".")) >= 3:
                parts = full.split(".")
                candidates = modules.get(".".join(parts[:-2]), [])
                if len(candidates) == 1:
                    target = candidates[0].get("classes", {}).get(parts[-2], {}).get(parts[-1])
    if target is None and constructor is not None and constructor not in local \
            and constructor not in s["bindings"] and not star:
        method = call.func.attr
        if constructor in s["classes"]:
            target = s["classes"][constructor].get(method)
        elif constructor in imports:
            parts = imports[constructor].split(".")
            if len(parts) >= 2:
                candidates = modules.get(".".join(parts[:-1]), [])
                if len(candidates) == 1:
                    target = candidates[0].get("classes", {}).get(parts[-1], {}).get(method)
    return target, expression, head


def _resolve_bare_function(s, name, imports, modules):
    """Resolve a bare function name (module-level or imported) to a key."""
    if name in s["functions"]:
        return s["functions"][name]
    if name in imports:
        module, _, function = imports[name].rpartition(".")
        candidates = modules.get(module, [])
        if len(candidates) == 1:
            return candidates[0].get("functions", {}).get(function)
    return None


def _class_methods(s, name, imports, modules, local, star):
    """Methods table {method: key} for a visible class name, or None.

    Local classes resolve through the source's own table; imported ones
    through the module table (single candidate only, same rule as static
    calls). Shadowed or star-imported names resolve to None.
    """
    if name in local or name in s["bindings"] or star:
        return None
    if name in s["classes"]:
        return s["classes"][name]
    if name in imports:
        parts = imports[name].split(".")
        candidates = modules.get(".".join(parts[:-1]), [])
        if len(candidates) == 1:
            return candidates[0].get("classes", {}).get(parts[-1])
    return None


def _constructor_bindings(s, body, imports, modules, local, star):
    """Variables provably bound once to a resolvable constructor call.

    Conservative proof obligations: exactly one Store binding of the name
    in the function (reassignment, loop targets, aug-assign all exclude),
    an Assign whose value is a direct constructor call naming a visible
    class, and — checked at the call site — the method call sits after
    the binding. Receiver names failing any obligation stay unresolved:
    unknown receiver types are never guessed.
    """
    counts = {}
    for name in _bound_names(body):
        counts[name] = counts.get(name, 0) + 1
    bindings = {}
    for assign in (n for n in ast.walk(body) if isinstance(n, ast.Assign)):
        if len(assign.targets) != 1 or not isinstance(assign.targets[0], ast.Name):
            continue
        var = assign.targets[0].id
        if counts.get(var, 0) != 1 or not isinstance(assign.value, ast.Call) \
                or not isinstance(assign.value.func, ast.Name):
            continue
        methods = _class_methods(s, assign.value.func.id, imports, modules,
                                 local, star)
        if not methods:
            continue
        start, end = _span(s["data"], assign)
        call_start, call_end = _span(s["data"], assign.value)
        bindings[var] = {"methods": methods, "class": assign.value.func.id,
                         "binding": {"source": s["id"], "start": start, "end": end},
                         "constructor": {"source": s["id"], "start": call_start,
                                           "end": call_end},
                         "line": assign.lineno}
    return bindings


def _code_targets(s):
    """Top-level functions and methods of one parsed code source."""
    targets = []
    for node in s["tree"].body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in s["functions"]:
            targets.append((s["functions"][node.name], node, None))
        elif isinstance(node, ast.ClassDef) and node.name in s["classes"]:
            for item in node.body:
                if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name in s["classes"][node.name]):
                    targets.append((s["classes"][node.name][item.name], item, node.name))
    return targets


def _resolve_code(sources, modules, symbols, assertions, diagnostics, known_sites=()):
    """Resolve imports and static calls for parsed code sources.

    Reads the module table and the accumulated symbol table; appends call
    assertions and resolution diagnostics. Accepts any source subset: files
    without trees are skipped, so callers pass only the files to resolve.

    Pass 1 resolves static calls and collects callback sites (parameter
    names invoked directly inside the function body). Pass 2 emits proposed
    depends_on candidates with derivation chains for call sites that pass
    a statically resolvable function into a known callback parameter.
    Candidates never traverse: every compiler path requires
    asserted_in_source. known_sites carries callback-site facts from kept
    diagnostics across refresh so an unchanged callback definition still
    matches newly re-parsed callers (rebuild equivalence).
    """
    callback_sites = list(known_sites)
    for s in sources:
        tree = s["tree"]
        if tree is None:
            continue
        imports, import_evidence, star = _import_map(s, tree)
        if star:
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                    diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "star_import"})
        for caller, node, self_class in _code_targets(s):
            local = set(_bound_names(ast.Module(body=node.body, type_ignores=[])))
            # A recursive function name can itself be shadowed by a parameter/local.
            argnames = {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)}
            local.update(argnames)
            rebound = local - argnames
            if self_class is None and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                   for statement in node.body for n in ast.walk(statement)):
                diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "nested_scope"})
                continue
            ordered_params = [a.arg for a in node.args.posonlyargs] + [a.arg for a in node.args.args]
            body = ast.Module(body=node.body, type_ignores=[])
            ctor_bindings = _constructor_bindings(s, body, imports,
                                                  modules, local, star)
            for call in (n for n in ast.walk(body) if isinstance(n, ast.Call)):
                target, expression, head = _resolve_call_target(
                    s, call, imports, modules, local, star, self_class, rebound)
                start, end = _span(s["data"], call)
                receiver = None
                if target is None and ctor_bindings \
                        and isinstance(call.func, ast.Attribute) \
                        and isinstance(call.func.value, ast.Name):
                    receiver = ctor_bindings.get(call.func.value.id)
                if target is None and receiver is not None \
                        and call.lineno > receiver["line"]:
                    callee = receiver["methods"].get(call.func.attr)
                    if callee is not None:
                        # The receiver type is proven by the single
                        # constructor binding: emit a candidate, not a
                        # diagnostic — the gap is bridged, pending
                        # verification. Never asserted automatically.
                        definition = symbols[callee]
                        call_ref = {"source": s["id"], "start": start, "end": end}
                        proposition = {"relation": "depends_on", "caller": caller,
                                       "callee": callee, "via": "attribute_dispatch"}
                        assertion = {"proposition": proposition, "source": s["id"],
                                     "start": start, "end": end,
                                     "resolution": {"expression": expression,
                                                    "rule": "known_receiver_method_candidate_v1",
                                                    "support": [receiver["binding"],
                                                                {k: definition[k] for k in ("source", "start", "end")}]},
                                     "derivation": {"receiver": call.func.value.id,
                                                    "class": receiver["class"],
                                                    "binding": receiver["binding"],
                                                    "constructor": receiver["constructor"],
                                                    "method": call.func.attr,
                                                    "call": call_ref},
                                     "producer": PROFILE, "status": "proposed"}
                        assertion["id"] = identity("assertion", assertion)
                        assertions.append(assertion)
                        continue
                if target:
                    proposition = {"relation": "static_call", "caller": caller, "callee": target}
                    definition = symbols[target]
                    support = [{k:definition[k] for k in ("source", "start", "end")}]
                    owner = head if head else None
                    if owner in import_evidence:
                        support.insert(0, import_evidence[owner])
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": expression, "rule": "unshadowed_static_binding_v1", "support": support},
                                 "producer": PROFILE, "status": "syntactic_observation"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
                else:
                    diagnostic = {"path": s["path"], "caller": caller, "line": call.lineno,
                                  "start": start, "end": end, "kind": "unresolved_call", "expression": expression}
                    if isinstance(call.func, ast.Name) and call.func.id in argnames \
                            and call.func.id in ordered_params:
                        diagnostic["parameter"] = call.func.id
                        diagnostic["index"] = ordered_params.index(call.func.id)
                        callback_sites.append({"callee": caller, "parameter": call.func.id,
                                               "index": diagnostic["index"], "path": s["path"],
                                               "binding": {"source": s["id"], "start": start, "end": end}})
                    diagnostics.append(diagnostic)
    _emit_callback_candidates(sources, modules, callback_sites, assertions)


def _emit_callback_candidates(sources, modules, callback_sites, assertions):
    """Propose depends_on edges for resolvable callback arguments.

    For every call to a function with a known callback site, resolve the
    positional argument at the callback parameter index; when it is an
    unshadowed bare name bound to a module-level or imported function,
    emit a proposed candidate (via callback_parameter) with the binding
    and call derivation chain. Unresolvable, shadowed, keyword-only,
    non-name, and self-recursive arguments propose nothing — negative
    controls keep the candidate set honest.
    """
    if not callback_sites:
        return
    by_callee = {}
    for site in callback_sites:
        by_callee.setdefault(site["callee"], []).append(site)
    for s in sources:
        tree = s["tree"]
        if tree is None:
            continue
        imports, _import_evidence, star = _import_map(s, tree)
        for _caller, node, self_class in _code_targets(s):
            local = set(_bound_names(ast.Module(body=node.body, type_ignores=[])))
            argnames = {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)}
            local.update(argnames)
            rebound = local - argnames
            if self_class is None and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                   for statement in node.body for n in ast.walk(statement)):
                continue
            body = ast.Module(body=node.body, type_ignores=[])
            for call in (n for n in ast.walk(body) if isinstance(n, ast.Call)):
                target, _expression, _head = _resolve_call_target(
                    s, call, imports, modules, local, star, self_class, rebound)
                if target is None or target not in by_callee:
                    continue
                start, end = _span(s["data"], call)
                for site in by_callee[target]:
                    if site["index"] >= len(call.args):
                        continue
                    arg = call.args[site["index"]]
                    if not isinstance(arg, ast.Name):
                        continue
                    if arg.id in local or arg.id in s["bindings"] or star:
                        continue
                    resolved = _resolve_bare_function(s, arg.id, imports, modules)
                    if resolved is None or resolved == site["callee"]:
                        continue
                    call_ref = {"source": s["id"], "start": start, "end": end}
                    proposition = {"relation": "depends_on", "caller": site["callee"],
                                   "callee": resolved, "via": "callback_parameter"}
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": arg.id,
                                                "rule": "callback_parameter_candidate_v1",
                                                "support": [site["binding"], call_ref]},
                                 "derivation": {"parameter": site["parameter"], "index": site["index"],
                                                "binding": site["binding"], "call": call_ref},
                                 "producer": PROFILE, "status": "proposed"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)


def _dispatch_target(value, record, imports, modules):
    """Resolve a registry value name to a code symbol key, or None."""
    if value in record["functions"]:
        return record["functions"][value]
    full = imports.get(value)
    if not full:
        return None
    module, _, function = full.rpartition(".")
    candidates = modules.get(module, [])
    if len(candidates) == 1:
        return candidates[0].get("functions", {}).get(function)
    return None


def _build_dispatch(sources, modules, symbols, assertions, diagnostics, invalid_tables=None):
    """Propose depends_on candidates through registry/dict indirection.

    Closed module-level ``NAME = {"key": func}`` tables with one static
    assignment, indexed by literals in the same function or by a parameter
    fed a literal at a static call site (one level), yield status=proposed
    candidates with a derivation chain (registry binding, index lookup,
    call site). Candidates never traverse: every compiler path requires
    asserted_in_source. Dynamic keys, reassigned tables, aliases, getattr
    dispatch, and unresolvable entries yield dispatch_unresolved
    diagnostics instead. Accepts any source subset like _resolve_code;
    refresh extends the affected set so caller/table pairs resolve
    together, and carries forward invalid tables from kept diagnostics.
    """
    records = {}
    for s in sources:
        if s["tree"] is not None:
            records[s["path"]] = s
    tables = {}
    invalid = {}

    def note_invalid(qname, reason, path, line, start=None):
        if qname not in invalid:
            invalid[qname] = True
            entry = {"path": path, "line": line, "kind": "dispatch_unresolved",
                     "reason": reason, "table": qname}
            if start is not None:
                entry["start"] = start
            diagnostics.append(entry)

    for path in sorted(records):
        s = records[path]
        tree = s["tree"]
        counts = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                name = node.targets[0].id
                counts[name] = counts.get(name, 0) + 1
                if counts[name] == 1 and isinstance(node.value, ast.Dict):
                    keys = node.value.keys
                    values = node.value.values
                    if any(not isinstance(k, ast.Constant) or not isinstance(k.value, str)
                           for k in keys):
                        note_invalid(s["path"] + "::" + name, "dynamic_table_key",
                                     s["path"], node.lineno)
                        continue
                    if any(not isinstance(v, ast.Name) for v in values):
                        note_invalid(s["path"] + "::" + name, "unresolvable_entry",
                                     s["path"], node.lineno)
                        continue
                    start, end = _span(s["data"], node)
                    tables[s["path"] + "::" + name] = {
                        "source": s["id"], "path": s["path"], "module": s["module"],
                        "name": name, "span": {"source": s["id"], "start": start, "end": end},
                        "entries": {k.value: v.id for k, v in zip(keys, values)}}
        for node in ast.walk(tree):
            if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                target = node.target
                names = [target.id] if isinstance(target, ast.Name) else []
                for name in names:
                    if name in counts:
                        note_invalid(s["path"] + "::" + name, "reassigned_table",
                                     s["path"], getattr(node, "lineno", None))
            elif isinstance(node, ast.Delete):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in counts:
                        note_invalid(s["path"] + "::" + target.id, "reassigned_table",
                                     s["path"], getattr(node, "lineno", None))
    for qname in list(tables):
        name = tables[qname]["name"]
        path = tables[qname]["path"]
        s = records[path]
        tree = s["tree"]
        stores = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        stores += 1
        if stores != 1:
            note_invalid(qname, "reassigned_table", path, None)
            del tables[qname]
    by_module = {}
    for qname, info in tables.items():
        by_module.setdefault(info["module"], {})[info["name"]] = info
    for path in sorted(records):
        s = records[path]
        tree = s["tree"]
        imports, _, _ = _import_map(s, tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)):
                continue
            target = node.value
            table = None
            if isinstance(target, ast.Name):
                info = by_module.get(s["module"], {}).get(target.id)
                if info is not None and info["path"] == s["path"]:
                    table = info
                else:
                    full = imports.get(target.id, "")
                    module, _, attr = full.rpartition(".")
                    if attr == target.id:
                        info = by_module.get(module, {}).get(attr)
                        if info is not None:
                            table = info
            elif (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)):
                full = imports.get(target.value.id, "")
                info = by_module.get(full or target.value.id, {}).get(target.attr)
                if info is not None:
                    table = info
            if table is not None:
                note_invalid(table["path"] + "::" + table["name"], "reassigned_table",
                             s["path"], getattr(node, "lineno", None))
    dead = {}
    for qname in list(invalid):
        if qname in tables:
            dead[qname] = tables.pop(qname)
    for qname in (invalid_tables or ()):
        if qname in tables:
            dead[qname] = tables.pop(qname)
    by_module = {}
    for qname, info in tables.items():
        by_module.setdefault(info["module"], {})[info["name"]] = info
    by_dead = {}
    for qname, info in dead.items():
        by_dead.setdefault(info["module"], {})[info["name"]] = info
    for info in tables.values():
        resolved = {}
        s = records[info["path"]]
        imports, _, _ = _import_map(s, s["tree"])
        for key, value in info["entries"].items():
            target = _dispatch_target(value, s, imports, modules)
            if target is None or target not in symbols:
                note_invalid(info["path"] + "::" + info["name"], "unknown_target",
                             info["path"], None)
                resolved = None
                break
            resolved[key] = target
        info["resolved"] = resolved

    def emit(caller_key, caller_source, call_span, lookup_span, lookup_expr, info, key):
        start, end = call_span
        call_ref = {"source": caller_source, "start": start, "end": end}
        proposition = {"relation": "depends_on", "caller": caller_key,
                       "callee": info["resolved"][key], "via": "registry_dispatch"}
        assertion = {"proposition": proposition, "source": caller_source,
                     "start": start, "end": end,
                     "resolution": {"expression": lookup_expr,
                                    "rule": "registry_dispatch_candidate_v1",
                                    "support": [info["span"], lookup_span, call_ref]},
                     "derivation": {"table": info["path"] + "::" + info["name"],
                                    "key": key, "binding": info["span"],
                                    "lookup": lookup_span, "call": call_ref},
                     "producer": PROFILE, "status": "proposed"}
        assertion["id"] = identity("assertion", assertion)
        assertions.append(assertion)

    func_index = {}
    for path in sorted(records):
        s = records[path]
        for node in s["tree"].body:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in s.get("functions", {})):
                func_index[(s["module"], node.name)] = (node, s)
    func_by_key = {}
    for (_, _), (node, s) in func_index.items():
        func_by_key[s["functions"][node.name]] = (node, s)
    import_cache = {}

    def find_table(name, record, imports, mapping):
        direct = mapping.get(record["module"], {}).get(name)
        if direct is not None and direct["path"] == record["path"]:
            return direct
        full = imports.get(name, "")
        module, _, attr = full.rpartition(".")
        if attr == name:
            found = mapping.get(module, {}).get(attr)
            if found is not None:
                return found
        return direct

    def lookup_table(name, record, imports):
        info = find_table(name, record, imports, by_module)
        if info is not None:
            return info, True
        info = find_table(name, record, imports, by_dead)
        return info, False

    def imports_of(record):
        if record["path"] not in import_cache:
            import_cache[record["path"]] = _import_map(record, record["tree"])[0]
        return import_cache[record["path"]]

    def rebound(name, node):
        for child in ast.walk(node):
            if (isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                    and child.id == name):
                return True
        return False

    for (module, _), (node, s) in sorted(func_index.items()):
        if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
               for statement in node.body for n in ast.walk(statement)):
            continue
        caller = s["functions"][node.name]
        aliases = {}
        for statement in node.body:
            if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and isinstance(statement.value, ast.Name)):
                aliases[statement.targets[0].id] = statement.value.id
        for call in (n for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                     if isinstance(n, ast.Call)):
            target = call.func
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)):
                table_name = target.value.id
                if table_name in aliases:
                    start, end = _span(s["data"], call)
                    diagnostics.append({"path": s["path"], "line": call.lineno,
                                        "kind": "dispatch_unresolved",
                                        "reason": "indirect_table", "start": start})
                    continue
                info, live = lookup_table(table_name, s, imports_of(s))
                if info is None or not live or info["resolved"] is None:
                    continue
                key = target.slice.value
                if key not in info["resolved"]:
                    start, end = _span(s["data"], call)
                    diagnostics.append({"path": s["path"], "line": call.lineno,
                                        "kind": "dispatch_unresolved", "reason": "unknown_key",
                                        "table": info["path"] + "::" + info["name"],
                                        "start": start})
                    continue
                call_start, call_end = _span(s["data"], call)
                lookup_start, lookup_end = _span(s["data"], target)
                lookup_span = {"source": s["id"], "start": lookup_start, "end": lookup_end}
                emit(caller, s["id"], (call_start, call_end), lookup_span,
                     table_name + "[\"" + key + "\"]", info, key)
            elif (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)):
                table_name = target.value.id
                if table_name in aliases:
                    start, _ = _span(s["data"], call)
                    diagnostics.append({"path": s["path"], "line": call.lineno,
                                        "kind": "dispatch_unresolved",
                                        "reason": "indirect_table", "start": start})
                    continue
                info, _live = lookup_table(table_name, s, imports_of(s))
                if info is not None:
                    start, _ = _span(s["data"], call)
                    diagnostics.append({"path": s["path"], "line": call.lineno,
                                        "kind": "dispatch_unresolved", "reason": "dynamic_key",
                                        "table": info["path"] + "::" + info["name"],
                                        "start": start})
            elif (isinstance(target, ast.Call) and isinstance(target.func, ast.Name)
                    and target.func.id == "getattr"):
                start, _ = _span(s["data"], call)
                diagnostics.append({"path": s["path"], "line": call.lineno,
                                    "kind": "dispatch_unresolved",
                                    "reason": "getattr_dispatch", "start": start})
            elif isinstance(target, ast.Name):
                callee_key = _dispatch_target(target.id, s, imports_of(s), modules)
                found = func_by_key.get(callee_key)
                if found is None:
                    continue
                def_node, def_s = found
                if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef, ast.Lambda))
                       for statement in def_node.body for n in ast.walk(statement)):
                    continue
                params = [a.arg for a in def_node.args.args]
                for position, argument in enumerate(call.args):
                    if not (isinstance(argument, ast.Constant)
                            and isinstance(argument.value, str)):
                        continue
                    if position >= len(params):
                        continue
                    key = argument.value
                    param = params[position]
                    if rebound(param, def_node):
                        continue
                    for site in ast.walk(def_node):
                        if not (isinstance(site, ast.Subscript)
                                and isinstance(site.value, ast.Name)
                                and isinstance(site.slice, ast.Name)
                                and site.slice.id == param):
                            continue
                        info = by_module.get(def_s["module"], {}).get(site.value.id)
                        if info is None or info["resolved"] is None:
                            continue
                        if key not in info["resolved"]:
                            continue
                        call_start, call_end = _span(s["data"], call)
                        lookup_start, lookup_end = _span(def_s["data"], site)
                        lookup_span = {"source": def_s["id"],
                                       "start": lookup_start, "end": lookup_end}
                        emit(caller, s["id"], (call_start, call_end), lookup_span,
                             info["name"] + "[\"" + key + "\"] via "
                             + params[position], info, key)
                        break
                    else:
                        continue
                    break


def _build_docs(sources, symbols, assertions, diagnostics):
    """Populate document mentions and typed-claim assertions.

    Reads only text sources against an already built symbol table, so this
    phase can rerun alone when code bytes are unchanged. The mention dedup
    set restarts here exactly as in a full rebuild, which refresh replicates
    by always processing every text source.
    Mutates the passed-in containers; see extract for the full pipeline.
    """
    return _build_mentions(sources, symbols, assertions, diagnostics,
                             _build_claims(sources, symbols, assertions, diagnostics))


def _build_claims(sources, symbols, assertions, diagnostics, text_ids=None):
    """Populate typed-claim assertions from document claim blocks.

    Returns the per-source claim spans that hide claimed text from mention
    extraction. Order-independent: claims never deduplicate across sources.
    Pass the full-build text ids when running over a subset of sources so
    other documents' symbols are not mistaken for code.
    """
    if text_ids is None:
        text_ids = {s["id"] for s in sources if s.get("text")}
    code_keys = sorted(key for key, record in symbols.items() if record["source"] not in text_ids)

    def _resolve_reference(ref):
        if ref in symbols:
            return ("exact", [ref])
        matches = sorted(key for key in code_keys
                         if symbols[key]["name"] == ref or key.split("::", 1)[-1].split(".")[-1] == ref)
        if matches:
            return ("named", matches)
        return ("missing", [])

    claim_spans = {}
    for s in sources:
        if not s.get("text"):
            continue
        mentioner = s["path"] + "::document"
        offset = 0
        lineno = 0
        in_fence = False
        fence_start = 0
        fence_line = 0
        body = []
        for raw_line in s["data"].splitlines(keepends=True):
            start, end = offset, offset + len(raw_line)
            offset = end
            lineno += 1
            try:
                stripped = raw_line.decode("utf-8").strip()
            except UnicodeError:
                stripped = ""
            if not in_fence and stripped == "```tunic-claims":
                in_fence = True
                fence_start = start
                fence_line = lineno
                body = []
                continue
            if in_fence:
                if stripped == "```":
                    in_fence = False
                    claim_spans.setdefault(s["id"], []).append((fence_start, end))
                    try:
                        block = b"".join(chunk for _, _, chunk in body).decode("utf-8")
                        claims = json.loads(block)
                        if not isinstance(claims, list):
                            raise ValueError("claim block must be a JSON list")
                    except (UnicodeError, ValueError) as error:
                        diagnostics.append({"path": s["path"], "line": fence_line,
                                            "kind": "malformed_claims", "error": str(error)})
                        continue
                    for claim in claims:
                        if (not isinstance(claim, dict) or not isinstance(claim.get("rel"), str)
                                or not isinstance(claim.get("subject"), str)
                                or not isinstance(claim.get("object"), str)):
                            diagnostics.append({"path": s["path"], "line": fence_line,
                                                "kind": "invalid_claim"})
                            continue
                        rel = claim["rel"]
                        if rel not in CLAIM_RELATIONS:
                            diagnostics.append({"path": s["path"], "line": fence_line,
                                                "kind": "unknown_relation", "relation": rel})
                            continue
                        retract_target = claim.get("retract")
                        if rel == "retracts":
                            if not isinstance(retract_target, str) or retract_target not in RETRACTABLE:
                                diagnostics.append({"path": s["path"], "line": fence_line,
                                                    "kind": "invalid_retract",
                                                    "expression": (claim.get("subject", "?") + " retracts "
                                                                   + claim["object"]),
                                                    "retract": retract_target})
                                continue
                        elif retract_target is not None:
                            diagnostics.append({"path": s["path"], "line": fence_line,
                                                "kind": "invalid_retract",
                                                "expression": (claim.get("subject", "?") + " " + rel + " "
                                                               + claim["object"]),
                                                "retract": retract_target})
                            continue
                        subject = claim["subject"]
                        s_kind, s_candidates = _resolve_reference(subject)
                        o_kind, o_candidates = _resolve_reference(claim["object"])
                        expression = subject + " " + rel + " " + claim["object"]
                        qualifiers = {}
                        if isinstance(claim.get("authority"), str):
                            qualifiers["authority"] = claim["authority"]
                        if "effective" in claim:
                            if is_iso_date(claim["effective"]):
                                qualifiers["effective"] = claim["effective"]
                            else:
                                diagnostics.append({"path": s["path"], "line": fence_line,
                                                    "kind": "invalid_effective", "expression": expression})
                        if s_kind == "exact" and o_kind == "exact":
                            proposition = {"relation": rel, "caller": s_candidates[0], "callee": o_candidates[0]}
                            if rel == "retracts":
                                proposition["retract"] = retract_target
                            proposition.update(qualifiers)
                            if isinstance(claim.get("note"), str):
                                proposition["note"] = claim["note"]
                            assertion = {"proposition": proposition, "source": s["id"],
                                         "start": fence_start, "end": end,
                                         "resolution": {"expression": expression, "rule": "exact_key_match_v1",
                                                        "support": []},
                                         "producer": PROFILE, "status": "asserted_in_source"}
                            assertion["id"] = identity("assertion", assertion)
                            assertions.append(assertion)
                            continue
                        if (s_kind, o_kind) in (("exact", "named"), ("named", "exact")):
                            if s_kind == "exact":
                                fixed, candidates = s_candidates[0], o_candidates
                                subject_named = False
                            else:
                                fixed, candidates = o_candidates[0], s_candidates
                                subject_named = True
                            ambiguous = len(candidates) > 1
                            for candidate in candidates:
                                caller = candidate if subject_named else fixed
                                callee = fixed if subject_named else candidate
                                record = symbols[candidate]
                                support = [{"source": record["source"], "start": record["start"],
                                            "end": record["end"]}]
                                proposition = {"relation": rel, "caller": caller, "callee": callee,
                                               "candidate": candidate}
                                if rel == "retracts":
                                    proposition["retract"] = retract_target
                                proposition.update(qualifiers)
                                if isinstance(claim.get("note"), str):
                                    proposition["note"] = claim["note"]
                                assertion = {"proposition": proposition, "source": s["id"],
                                             "start": fence_start, "end": end,
                                             "resolution": {"expression": expression,
                                                            "rule": "name_match_proposed_v1",
                                                            "support": support},
                                             "producer": PROFILE,
                                             "status": "proposed_ambiguous" if ambiguous else "proposed"}
                                assertion["id"] = identity("assertion", assertion)
                                assertions.append(assertion)
                            if ambiguous:
                                diagnostics.append({"path": s["path"], "line": fence_line,
                                                    "kind": "ambiguous_reference", "expression": expression,
                                                    "candidates": sorted(candidates)})
                            continue
                        diagnostics.append({"path": s["path"], "line": fence_line,
                                            "kind": ("ambiguous_reference"
                                                     if s_kind == "named" or o_kind == "named"
                                                     else "unresolved_reference"),
                                            "expression": expression,
                                            "candidates": sorted(s_candidates + o_candidates)})
                else:
                    body.append((start, end, raw_line))
                continue
        if in_fence:
            diagnostics.append({"path": s["path"], "line": fence_line, "kind": "unclosed_claims"})
    return claim_spans


def _code_leaf_names(symbols, text_ids):
    """Mappable bare leaves over code symbols: {leaf: [keys]}."""
    leaf_names = {}
    for key, record in symbols.items():
        if record["source"] in text_ids:
            continue
        leaf = record["name"].split(".")[-1]
        if len(leaf) >= 2:
            leaf_names.setdefault(leaf, []).append(key)
    return leaf_names


def _exact_mention_refs(assertion_list):
    """(source id, callee) pairs of exact text mentions for corroboration."""
    return {(record.get("source"), record["proposition"].get("callee"))
            for record in assertion_list
            if record.get("proposition", {}).get("relation") == "text_mention"
            and record.get("resolution", {}).get("rule") == "exact_key_match_v1"}


def _build_mentions(sources, symbols, assertions, diagnostics, claim_spans, seen=None):
    """Populate document mention assertions in source order.

    Exact key matches across every document take priority over bare leaf
    matches anywhere: the exact pass runs to completion before the bare
    pass starts. Takes an optional seen set of (start, key) pairs so
    incremental refresh can seed mentions from unchanged documents and
    preserve full-rebuild first-wins dedup order. Returns the seen set.
    """
    seen_mentions = set() if seen is None else seen
    _build_exact_mentions(sources, symbols, assertions, claim_spans, seen_mentions)
    text_ids = {s["id"] for s in sources if s.get("text")}
    doc_freq = _leaf_document_frequency(
        [s for s in sources if s.get("text")],
        _code_leaf_names(symbols, text_ids))
    _build_bare_mentions(sources, symbols, assertions, diagnostics, claim_spans,
                         seen_mentions, text_ids,
                         doc_freq, _exact_mention_refs(assertions),
                         _claim_mention_refs(assertions))
    return seen_mentions


def _build_exact_mentions(sources, symbols, assertions, claim_spans, seen, text_ids=None):
    """Populate exact `path::symbol` mention assertions in source order."""
    if text_ids is None:
        text_ids = {s["id"] for s in sources if s.get("text")}
    code_keys = sorted(key for key, record in symbols.items() if record["source"] not in text_ids)
    seen_mentions = seen
    for s in sources:
        if not s.get("text"):
            continue
        mentioner = s["path"] + "::document"
        hidden = claim_spans.get(s["id"], [])
        offset = 0
        for raw_line in s["data"].splitlines(keepends=True):
            start, end = offset, offset + len(raw_line)
            offset = end
            if any(first <= start < last for first, last in hidden):
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeError:
                continue
            for key in code_keys:
                if key in line and (start, key) not in seen_mentions:
                    seen_mentions.add((start, key))
                    proposition = {"relation": "text_mention", "caller": mentioner, "callee": key}
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": key, "rule": "exact_key_match_v1", "support": []},
                                 "producer": PROFILE, "status": "syntactic_observation"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
    return seen_mentions


EVIDENCE_DF_THRESHOLD = 0.05


def _leaf_document_frequency(text_sources, leaf_names):
    """Fraction of text sources containing each bare leaf, one scan per doc.

    Corpus-relative, never a fixed stopword list: a leaf that appears
    everywhere carries no identity evidence for any single occurrence.
    Must run over every text source, not a subset, or frequencies skew.
    """
    pattern = re.compile("|".join(
        r"\b" + re.escape(leaf) + r"\b"
        for leaf in sorted(leaf_names, key=len, reverse=True))) if leaf_names else None
    counts = {leaf: 0 for leaf in leaf_names}
    total = 0
    for source in text_sources:
        try:
            text = source["data"].decode("utf-8")
        except UnicodeError:
            continue
        total += 1
        if pattern is None:
            continue
        for leaf in {match.group(0) for match in pattern.finditer(text)}:
            counts[leaf] += 1
    return {leaf: (counts[leaf] / total if total else 0.0) for leaf in leaf_names}


def _claim_mention_refs(assertion_list):
    """(source id, entity) pairs touched by asserted doc claims."""
    refs = set()
    for record in assertion_list:
        proposition = record.get("proposition", {})
        if (record.get("status") != "asserted_in_source"
                or proposition.get("relation") in ("static_call", "text_mention")):
            continue
        refs.add((record.get("source"), proposition.get("caller")))
        refs.add((record.get("source"), proposition.get("callee")))
    return refs


def _requalify(leaf, line, source_id, callee, doc_freq, exact_refs, claim_refs=frozenset()):
    """Recompute admission for a kept mention under current corpus state.

    Lets incremental refresh reprocess documents whose bare mentions flip
    verdicts when frequencies or exact references change, so refresh output
    stays identical to a full rebuild without reprocessing everything.
    """
    frequency = doc_freq.get(leaf, 0.0)
    class_hit, module_hit, inventory = _context_hits(line, callee)
    signals = _mention_signals(line, leaf, (source_id, callee) in exact_refs,
                               (source_id, callee) in claim_refs,
                               class_hit, module_hit, inventory)
    return ("exact_same_document" in signals
            or "asserted_claim_same_document" in signals
            or "class_context" in signals
            or (frequency <= EVIDENCE_DF_THRESHOLD
                and ("dotted_or_call" in signals
                     or "module_path_context" in signals
                     or "inventory_list" in signals)))


INVENTORY_HEADER = re.compile(
    r"^\s*[-*]?\s*(defs?|classes?|functions?|methods?|members?|exports?|"
    r"symbols?|apis?|attributes?)\s*:", re.IGNORECASE)

PATH_TOKEN = re.compile(r"[\w$./-]+\.\w+")


def _mention_signals(line, leaf, exact_same_doc, claimed_same_doc,
                     class_hit=False, module_hit=False, inventory=False):
    """Independent corroborating signals for one bare occurrence.

    exact_same_document: the same document already references the entity
    exactly, so identity is established, not guessed. asserted_claim_same_
    document: the document asserts a typed claim touching the entity, which
    corroborates identity as strongly as an exact mention. class_context:
    the line names the candidate's own class next to the leaf, which is
    joint identity evidence as discriminating as an exact key. dotted_or_
    call: `recv.leaf` or `leaf(` syntax denotes the code entity, not a
    prose verb. module_path_context: the line names the candidate's file,
    corroborating identity by location. inventory_list: the leaf sits in
    an explicit member inventory (`defs:`, `classes:`, ...), which lists
    entities rather than using their names as prose. code_span: the leaf
    sits inside backticks. A backticked span alone is weak (prose backticks
    file names too) and never qualifies by itself.
    """
    signals = []
    if exact_same_doc:
        signals.append("exact_same_document")
    if claimed_same_doc:
        signals.append("asserted_claim_same_document")
    if class_hit:
        signals.append("class_context")
    if re.search(r"[\w$]+\." + re.escape(leaf) + r"\b|\b" + re.escape(leaf) + r"\s*\(",
                 line):
        signals.append("dotted_or_call")
    if module_hit:
        signals.append("module_path_context")
    if inventory:
        signals.append("inventory_list")
    if re.search(r"`[^`]*\b" + re.escape(leaf) + r"\b[^`]*`", line):
        signals.append("code_span")
    return signals


def _context_hits(line, callee_key):
    """Structural corroboration available to any bare occurrence.

    Returns (class_hit, module_hit, inventory): whether the line names the
    candidate's own class, names the candidate's file, or sits in an
    explicit member-inventory list. Derived from the key and the line
    alone, so full rebuilds and incremental refreshes agree exactly.
    """
    parts = callee_key.split("::")
    dotted = parts[1].split(".") if len(parts) > 1 else []
    class_hit = (len(dotted) > 1
                 and re.search(r"\b" + re.escape(dotted[-2]) + r"\b", line) is not None)
    cand_path = parts[0]
    module_hit = any(cand_path == token or cand_path.endswith("/" + token)
                     for token in PATH_TOKEN.findall(line))
    return class_hit, module_hit, INVENTORY_HEADER.match(line) is not None


def _build_bare_mentions(sources, symbols, assertions, diagnostics, claim_spans, seen,
                         text_ids=None, doc_freq=None, exact_refs=None,
                         claim_refs=None):
    """Populate bare leaf-name mention assertions in source order.

    Must run after the exact pass over every document: a bare match is
    suppressed when its (start, key) pair was already claimed by an exact
    match, even in a different document.

    Bare matches are qualified, not merely collected. A token match
    becomes a graph assertion with same-doc exact or claim evidence, with
    class context, or with dotted/call, file-path, or inventory-list
    syntax on a low-frequency leaf; anything weaker is recorded as a
    mention_candidate diagnostic. Backticked spans are recorded as a
    signal but never qualify alone: prose backticks tool names too.
    Candidates stay searchable through the lexical stage and visible for
    review, but never become evidence edges. doc_freq must cover every
    text source and exact_refs every exact mention, or subset refreshes
    diverge from full rebuilds.
    """
    if text_ids is None:
        text_ids = {s["id"] for s in sources if s.get("text")}
    code_keys = sorted(key for key, record in symbols.items() if record["source"] not in text_ids)
    seen_mentions = seen
    leaf_names = _code_leaf_names(symbols, text_ids)
    if doc_freq is None:
        doc_freq = _leaf_document_frequency(
            [s for s in sources if s.get("text")], leaf_names)
    if exact_refs is None:
        exact_refs = set()
    if claim_refs is None:
        claim_refs = set()
    leaf_pattern = None
    if leaf_names:
        leaf_pattern = re.compile("|".join(
            r"\b" + re.escape(leaf) + r"\b"
            for leaf in sorted(leaf_names, key=len, reverse=True)))
    for s in sources:
        if not s.get("text"):
            continue
        mentioner = s["path"] + "::document"
        hidden = claim_spans.get(s["id"], [])
        offset = 0
        for lineno, raw_line in enumerate(s["data"].splitlines(keepends=True), start=1):
            start, end = offset, offset + len(raw_line)
            offset = end
            if any(first <= start < last for first, last in hidden):
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeError:
                continue
            exact_here = {key for key in code_keys if key in line}
            if leaf_pattern is None:
                continue
            present = {match.group(0) for match in leaf_pattern.finditer(line)}
            for leaf in sorted(present):
                candidates = leaf_names[leaf]
                for key in candidates:
                    if key in exact_here or (start, key) in seen_mentions:
                        continue
                    seen_mentions.add((start, key))
                    frequency = doc_freq.get(leaf, 0.0)
                    class_hit, module_hit, inventory = _context_hits(line, key)
                    signals = _mention_signals(
                        line, leaf, (s["id"], key) in exact_refs,
                        (s["id"], key) in claim_refs,
                        class_hit, module_hit, inventory)
                    qualified = ("exact_same_document" in signals
                                 or "asserted_claim_same_document" in signals
                                 or "class_context" in signals
                                 or (frequency <= EVIDENCE_DF_THRESHOLD
                                     and ("dotted_or_call" in signals
                                          or "module_path_context" in signals
                                          or "inventory_list" in signals)))
                    if not qualified:
                        diagnostics.append({"path": s["path"], "line": lineno,
                                            "kind": "mention_candidate",
                                            "expression": leaf, "callee": key,
                                            "candidate": key,
                                            "document_frequency": round(frequency, 4),
                                            "signals": signals})
                        continue
                    record = symbols[key]
                    support = [{"source": record["source"], "start": record["start"],
                                "end": record["end"]}]
                    proposition = {"relation": "text_mention", "caller": mentioner, "callee": key,
                                   "candidate": key}
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": leaf, "rule": "name_match_proposed_v1",
                                                "support": support,
                                                "qualification": {"document_frequency": round(frequency, 4),
                                                                  "signals": signals}},
                                 "producer": PROFILE,
                                 "status": "proposed_ambiguous" if len(candidates) > 1 else "proposed"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
    return seen_mentions


def extract(root):
    """Full rebuild: capture, code resolution, then document extraction."""
    sources, diagnostics, modules = capture(root)
    symbols, assertions = {}, []
    _collect_code(sources, symbols, assertions, diagnostics)
    _resolve_code(sources, modules, symbols, assertions, diagnostics)
    _build_dispatch(sources, modules, symbols, assertions, diagnostics)
    _build_docs(sources, symbols, assertions, diagnostics)
    return sources, sorted(symbols.values(), key=lambda x:x["key"]), sorted(assertions, key=lambda x:x["id"]), sorted(diagnostics, key=lambda x: (x["path"], x.get("line") or 0, x["kind"], x.get("start", 0)))
