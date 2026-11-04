"""Capture and the Python engine: bounded, non-executing AST extraction.

Unresolved is not absent. `extract` is the full-rebuild entry point: scope
walks files, this module captures them and resolves Python, pipeline runs
every other family, and prose runs last against the merged symbol table.

Names from scope, relations, and prose are re-exported here because this
was their historical home; new code should import from the owner.
"""

import ast
import copy
import io
import re
from pathlib import Path
import tokenize

from .identity import digest, identity
from . import filetypes
from . import pipeline
from . import structure
from .prose import (EVIDENCE_DF_THRESHOLD, INVENTORY_HEADER, PATH_TOKEN,  # noqa: F401
                    _build_bare_mentions, _build_claims, _build_docs,
                    _build_exact_mentions, _build_mentions,
                    _claim_mention_refs, _code_leaf_names, _context_hits,
                    _exact_mention_refs, _leaf_document_frequency,
                    _mention_signals, _requalify, split_doc_sections)
from .relations import (CLAIM_RELATIONS, PROFILE, RELATION_SIGNATURES,  # noqa: F401
                        RETRACTABLE, TRAVERSABLE_CLAIMS, _endpoint_kind,
                        check_relation_signature, is_iso_date)
from .scope import (EXCLUDED, EXCLUDED_BUILD, _compile_ignore_rules,  # noqa: F401
                    _is_ignored, _match_rules, _scan, _scope_fingerprint,
                    iter_scope_files, load_scope_config, resolve_scope)
from .structure import INVENTORY_DERIVATIONS  # noqa: F401

TEXT_EXTENSIONS = filetypes.PROSE_EXTENSIONS
INVENTORY_EXTENSIONS = filetypes.HARVEST_EXTENSIONS
FILE_TEXT_DERIVATION = "file_text_v1"


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


def _source_record(path, data, text=False):
    file_digest = digest(data)
    return {"path": path, "digest": file_digest, "data": data,
            "id": identity("source", [path, file_digest]), "tree": None,
            "text": text}


def _parse_python(record):
    """Attach the AST to a Python record; a diagnostic if it has none."""
    path, data = record["path"], record["data"]
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        if encoding not in {"utf-8", "utf-8-sig"}:
            return {"path": path, "kind": "unsupported_encoding", "encoding": encoding}
        record["tree"] = ast.parse(data.decode("utf-8-sig"), filename=path)
    except (SyntaxError, UnicodeError, ValueError) as error:
        return {"path": path, "kind": "parse_error", "line": getattr(error, "lineno", None)}
    return None


def capture(root, exclude=()):
    sources, diagnostics, modules = [], [], {}
    entries, walk_diagnostics, excluded = _scan(root, exclude)
    diagnostics.extend(walk_diagnostics)
    for entry in entries:
        source = _source_record(entry["path"], entry["data"], entry["text"])
        sources.append(source)
        if entry["text"]:
            continue
        if entry.get("inventory"):
            source["inventory"] = True
            continue
        source["module"] = module_of(entry["path"])
        modules.setdefault(source["module"], []).append(source)
        diagnostic = _parse_python(source)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
    return sources, diagnostics, modules, excluded


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
    record = _source_record(path, data)
    if not filetypes.is_python(path):
        record["inventory"] = True
        return record, None
    record["module"] = module_of(path)
    diagnostic = _parse_python(record)
    return (None, diagnostic) if diagnostic is not None else (record, None)


def _builtin_method_kind(decorator_list):
    """'staticmethod'/'classmethod' if that is the sole decorator, else None.

    A bare @staticmethod/@classmethod is language semantics, not a
    replaceable callable: the underlying function is indexed and called
    with exactly the passed arguments (plus cls for classmethod). Any
    other decorator, alone or stacked, may replace the callable, so
    those methods stay skipped with a decorated_function diagnostic.
    """
    if len(decorator_list) != 1:
        return None
    node = decorator_list[0]
    if isinstance(node, ast.Name) and node.id in ("staticmethod",
                                                  "classmethod"):
        return node.id
    return None


# A bare identifier on an indented line is usually a local binding, not a
# module-scope declaration. Exports are always top-level in ESM, so a
# brace-language line is harvestable when it starts at column 0 or
# carries `export`. The fn/func/def family stays indent-tolerant on
# purpose: Go/Rust top-level items are column-0 by convention, while
# `impl` methods and stub-file methods are indented but real. Each
# entry is (pattern, kind, scope_disciplined).
_TOP = rb"^(?P<indent>[ \t]*)(?P<export>export[ \t]+)?"
INVENTORY_PATTERNS = (
    (re.compile(_TOP + rb"(?:async[ \t]+)?function[ \t]*(?:\*[ \t]*)?([A-Za-z_$][\w$]*)[ \t]*\(", re.MULTILINE), "function", True),
    (re.compile(_TOP + rb"(?:abstract[ \t]+)?class[ \t]+([A-Za-z_$][\w$]*)", re.MULTILINE), "class", True),
    (re.compile(_TOP + rb"(?:const|let|var)[ \t]+([A-Za-z_$][\w$]*)[ \t]*[=:]", re.MULTILINE), "function", True),
    (re.compile(_TOP + rb"(?:interface|type|enum)[ \t]+([A-Za-z_$][\w$]*)", re.MULTILINE), "type", True),
    (re.compile(rb"^(?P<indent>[ \t]*)(?P<export>export[ \t]+)default[ \t]+(?:async[ \t]+)?function[ \t]+([A-Za-z_$][\w$]*)", re.MULTILINE), "function", True),
    (re.compile(rb"^(?P<indent>[ \t]*)(?P<export>export[ \t]+)default[ \t]+(?:async[ \t]+)?class[ \t]+([A-Za-z_$][\w$]*)", re.MULTILINE), "class", True),
    (re.compile(rb"^[ \t]*(?:public|private|protected)?[ \t]*(?:static[ \t]+|async[ \t]+|abstract[ \t]+)*(?:fn[ \t]+(?:\([^)]*\)[ \t]+)?|func[ \t]+(?:\([^)]*\)[ \t]+)?|def[ \t]+)([A-Za-z_]\w*)", re.MULTILINE), "function", False),
)


def _harvest_inventory(source, symbols, diagnostics):
    """Heuristic symbol inventory for non-Python code files.

    Emits function/class/type symbols with byte-exact spans so lookup,
    with column-0-or-export scope discipline for brace languages,
    so mentions and review keep working. Emits NO assertions:
    a harvested name is inventory, never a call edge. Deterministic:
    first occurrence wins, later duplicates file a diagnostic.
    """
    data = source["data"]
    language = Path(source["path"]).suffix[1:]
    found = 0
    for pattern, kind, scoped in INVENTORY_PATTERNS:
        for match in pattern.finditer(data):
            if (scoped and match.group("indent") != b""
                    and match.group("export") is None):
                continue
            name = match.group(match.lastindex).decode("ascii")
            key = source["path"] + "::" + name
            if key in symbols:
                continue
            lineno = data.count(b"\n", 0, match.start()) + 1
            symbols[key] = {"key": key, "path": source["path"],
                            "name": name, "source": source["id"],
                            "line": lineno, "start": match.start(),
                            "end": match.end(), "kind": kind,
                            "is_test": name.startswith("test_")
                            or Path(source["path"]).name.startswith("test_"),
                            "language": language,
                            "derivation": "heuristic_inventory_v1"}
            found += 1
    if found:
        diagnostics.append({"path": source["path"], "kind": "language_inventory",
                            "language": language, "symbols": found})
    return found


def _collect_inventory(source, symbols, diagnostics):
    """Symbols for a non-Python, non-prose file, at the best rung it has.

    Families with a resolver stage own their bytes. Files an edge engine
    will re-key keep the frozen harvest it builds on. Everything else
    gets grammar-backed symbols when a grammar is local, the regex
    harvest where one is receipted, and in every case one whole-file
    text record, so the file stays retrievable whatever else failed.
    """
    path = source["path"]
    filetype = filetypes.classify(path, source["data"][:filetypes.SNIFF_BYTES])
    if filetype.family in (filetypes.NOTEBOOK, filetypes.CONFIG, filetypes.SCHEMA):
        return
    if pipeline.has_edge_engine(path):
        _harvest_inventory(source, symbols, diagnostics)
        return
    if not structure.harvest(source, filetype.language, symbols, diagnostics) \
            and filetypes.suffix_of(path) in INVENTORY_EXTENSIONS:
        _harvest_inventory(source, symbols, diagnostics)
    key = path + "::document"
    symbols[key] = {"key": key, "path": path, "name": "document",
                    "source": source["id"], "line": 1, "start": 0,
                    "end": len(source["data"]), "is_test": False,
                    "derivation": FILE_TEXT_DERIVATION}


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
            elif s.get("inventory") and s["data"]:
                _collect_inventory(s, symbols, diagnostics)
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
                if item.decorator_list and _builtin_method_kind(
                        item.decorator_list) is None:
                    diagnostics.append({"path": s["path"], "line": item.lineno, "kind": "decorated_function"})
                    continue
                if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
                                       ast.GeneratorExp))
                       for statement in item.body for n in ast.walk(statement)):
                    # Nested scope noted, but the method is still indexed:
                    # its direct calls resolve (nested regions pruned at
                    # resolution time) while nested calls are reported as
                    # distinct nested_call diagnostics, never as outer edges.
                    diagnostics.append({"path": s["path"], "line": item.lineno, "kind": "nested_scope"})
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
            # The class itself is an entity: constructor-call candidates
            # need a resolvable target, and classes were previously
            # invisible in the graph (methods only). NamedTuple-style
            # declarations (annotation-only bodies) become visible here.
            # kind "class" marks records the mention/claim passes must
            # skip: document-mention semantics for classes are a later
            # decision, and frozen precision behavior must not move.
            key = s["path"] + "::" + node.name
            start, end = _span(s["data"], node)
            symbols[key] = {"key": key, "path": s["path"], "name": node.name,
                            "source": s["id"], "line": node.lineno,
                            "start": start, "end": end, "kind": "class",
                            "is_test": Path(s["path"]).name.startswith("test_")}
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
                if node.level > len(package) + 1:  # L1 == current package, valid even when empty
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


def _emit_attribute_candidate(s, symbols, assertions, caller, call, callee,
                              class_name, receiver_label, binding, constructor):
    """Emit one proposed attribute-dispatch candidate with a derivation chain.

    Shared by local-variable receivers and self-attribute receivers. The
    receiver type is proven by a single constructor binding: a candidate
    is emitted, never an assertion — pending verification through the
    governed promotion path.
    """
    start, end = _span(s["data"], call)
    definition = symbols[callee]
    call_ref = {"source": s["id"], "start": start, "end": end}
    proposition = {"relation": "depends_on", "caller": caller,
                   "callee": callee, "via": "attribute_dispatch"}
    assertion = {"proposition": proposition, "source": s["id"],
                 "start": start, "end": end,
                 "resolution": {"expression": _dotted(call.func),
                                "rule": "known_receiver_method_candidate_v1",
                                "support": [binding,
                                            {k: definition[k] for k in ("source", "start", "end")}]},
                 "derivation": {"receiver": receiver_label,
                                "class": class_name,
                                "binding": binding,
                                "constructor": constructor,
                                "method": call.func.attr,
                                "call": call_ref},
                 "producer": PROFILE, "status": "proposed"}
    assertion["id"] = identity("assertion", assertion)
    assertions.append(assertion)


def _class_attribute_bindings(s):
    """self.attr = C() facts: {(class, attr): (assign, value, method_node)}.

    Raw single-assignment candidates are collected here; constructor
    resolution happens at the call site with the assigning method's own
    scope, and the (class, attr) pair must be assigned exactly once in
    the class with the assignment inside __init__ (so the attribute is
    bound before any method can run).
    """
    raw = {}
    counts = {}
    for _key, node, self_class in _code_targets(s):
        if self_class is None:
            continue
        body = ast.Module(body=node.body, type_ignores=[])
        for assign in (n for n in ast.walk(body) if isinstance(n, ast.Assign)):
            if len(assign.targets) != 1:
                continue
            target = assign.targets[0]
            if not (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                continue
            slot = (self_class, target.attr)
            # count EVERY Store binding of the attribute: the
            # single-binding proof fails on `self.x = C()` plus any
            # other assignment (e.g. lazy `self.x = None`), even one
            # whose value is not a call.
            counts[slot] = counts.get(slot, 0) + 1
            if not isinstance(assign.value, ast.Call):
                continue
            raw.setdefault(slot, []).append((node, assign, assign.value))
    bindings = {}
    for slot, assigns in raw.items():
        if counts[slot] != 1:
            continue
        method_node, assign, value = assigns[0]
        if method_node.name != "__init__":
            continue
        bindings[slot] = (assign, value, method_node)
    return bindings


def _resolve_class_symbol(s, name, imports, modules, local, star):
    """Resolve a bare class name to its symbol key, or None.

    Local classes resolve through the source's own table; imported ones
    (aliases included, via the shared import map) through single-candidate
    module lookups. Shadowed, bound, or star-imported names resolve to
    None — a class invocation is only a candidate when the class
    definition is verifiably in the indexed workspace.
    """
    if name in local or name in s["bindings"] or star:
        return None
    if name in s["classes"]:
        return s["path"] + "::" + name, "local"
    if name in imports:
        parts = imports[name].split(".")
        candidates = modules.get(".".join(parts[:-1]), [])
        if len(candidates) == 1:
            record = candidates[0]
            # Class membership must be positive: a name that merely
            # resolves to a module is not a class (functions and
            # aliases share the same import table).
            class_names = set(record.get("classes", {})) | set(record.get("class_names", ()))
            if parts[-1] not in class_names:
                return None
            methods = record.get("classes", {}).get(parts[-1]) or {}
            if methods:
                return next(iter(methods.values())).rsplit(".", 1)[0], "import"
            path = record.get("path")
            if path is not None:
                return path + "::" + parts[-1], "import"
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


class _NestedScopePruner(ast.NodeTransformer):
    """Separate definition-time, eager, and deferred execution.

    Three phases: (1) the target's OWN header (decorators, defaults,
    annotations, returns) evaluates when the ``def`` statement runs --
    definition time, never attributed to the target's execution;
    (2) the target body plus nested-definition headers (decorators,
    defaults, bases, keywords) evaluate when the target runs -- eager,
    attributed outward; (3) nested function bodies, lambda bodies, and
    generator elements/conditions/late-iterables evaluate later or
    never -- deferred, recorded as ``nested_call`` diagnostics.
    Nested class bodies are transparent: the class statement itself
    runs during the target, so direct class-body calls are eager;
    only the methods inside defer. Operates on a deep copy: the
    shared parse tree is never mutated.
    """

    def __init__(self):
        self.calls = []
        self.pruned = False
        self._depth = 0

    def _record(self, target):
        if isinstance(target, ast.AST):
            target = [target]
        for node in target:
            self.calls.extend(c for c in ast.walk(node)
                              if isinstance(c, ast.Call)
                              and isinstance(c.func, ast.Name))

    def _blank(self, old):
        return ast.copy_location(ast.Constant(value=None), old)

    def _strip_header(self, node):
        """Neutralize the target's own definition-time positions.

        Depth 0 is the extraction target: its decorators, defaults,
        annotations, and returns evaluate at definition time (import
        or class-body time), not when the target executes. Replace
        them with constants so only the body attributes outward.
        Nested definitions keep their headers: those evaluate while
        the target runs. Matches the pre-nested-scope contract, where
        headers were never walked.
        """
        node.decorator_list[:] = []
        node.args.defaults[:] = [self._blank(d) for d in node.args.defaults]
        node.args.kw_defaults[:] = [self._blank(d) if d is not None else None
                                    for d in node.args.kw_defaults]
        for arg in ast.walk(node.args):
            if isinstance(arg, ast.arg):
                arg.annotation = None
        node.returns = None

    def visit_FunctionDef(self, node):
        if self._depth == 0:
            self._strip_header(node)
        else:
            self._record(node.body)
            self.pruned = True
            node.body[:] = [ast.copy_location(ast.Pass(), node)]
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1
        return node

    def visit_AsyncFunctionDef(self, node):
        if self._depth == 0:
            self._strip_header(node)
        else:
            self._record(node.body)
            self.pruned = True
            node.body[:] = [ast.copy_location(ast.Pass(), node)]
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1
        return node

    def visit_ClassDef(self, node):
        # Transparent at every depth: the class statement executes in
        # the enclosing scope, so direct class-body calls are eager.
        # Nested functions/methods neutralize themselves on visit.
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1
        return node

    def visit_Lambda(self, node):
        self._record(node.body)
        self.pruned = True
        node.body = ast.copy_location(ast.Constant(value=None), node.body)
        self.generic_visit(node)
        return node

    def visit_GeneratorExp(self, node):
        # Only the outermost iterable evaluates at generator creation;
        # later iterables, conditions, and the element run per-iteration.
        first, late = node.generators[0], node.generators[1:]
        self._record(node.elt)
        for comprehension in node.generators:
            for condition in comprehension.ifs:
                self._record(condition)
        for comprehension in late:
            self._record(comprehension.iter)
        self.pruned = True
        node.elt = ast.copy_location(ast.Constant(value=None), node.elt)
        for comprehension in node.generators:
            comprehension.ifs = [ast.copy_location(ast.Constant(value=True), condition)
                                 for condition in comprehension.ifs]
        for comprehension in late:
            comprehension.iter = ast.copy_location(ast.Constant(value=None),
                                                   comprehension.iter)
        self.generic_visit(node)
        return node


def _without_nested_scopes(node):
    """Split a function/method into eager positions and deferred content.

    Returns (kept_node, nested_names, nested_calls, pruned). kept_node
    is a deep copy of the definition with the target's own header
    stripped (definition-time, never attributed), deferred positions
    neutralized (nested function bodies, lambda bodies, generator
    elements/conditions/late-iterables), and everything else intact:
    the target body, nested-definition headers, transparent nested
    class bodies, and the outermost generator iterable. nested_names
    holds every nested definition name (still shadowing, never
    resolvable to module scope); nested_calls holds the bare-name Call
    nodes found in deferred positions for distinct diagnostics. NOTE:
    return/argument annotations ride with the stripped header (PEP 563
    ``from __future__ import annotations`` files excepted, documented
    but not modeled).
    """
    nested_names = {n.name for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef))
                    and n is not node}
    if (not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef, ast.Lambda, ast.GeneratorExp))
                for statement in node.body for n in ast.walk(statement))
            and not _header_calls(node)):
        return node, nested_names, [], False
    pruner = _NestedScopePruner()
    kept_node = pruner.visit(copy.deepcopy(node))
    return kept_node, nested_names, pruner.calls, pruner.pruned


def _header_calls(node):
    """Whether the target's own header contains any call expression."""
    parts = list(node.decorator_list) + list(node.args.defaults)
    parts += [d for d in node.args.kw_defaults if d is not None]
    if node.returns is not None:
        parts.append(node.returns)
    for arg in ast.walk(node.args):
        if isinstance(arg, ast.arg) and arg.annotation is not None:
            parts.append(arg.annotation)
    return any(isinstance(n, ast.Call) for part in parts for n in ast.walk(part))


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
        attr_bindings = _class_attribute_bindings(s)
        for caller, node, self_class in _code_targets(s):
            local = set(_bound_names(ast.Module(body=node.body, type_ignores=[])))
            # A recursive function name can itself be shadowed by a parameter/local.
            argnames = {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)}
            local.update(argnames)
            rebound = local - argnames
            kept_node, nested_names, nested_calls, pruned = _without_nested_scopes(node)
            if pruned and self_class is None:
                # Methods already recorded this at symbol extraction;
                # module functions record it here, once per target.
                diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "nested_scope"})
            for nested_call in nested_calls:
                start, end = _span(s["data"], nested_call)
                diagnostics.append({"path": s["path"], "caller": caller, "line": nested_call.lineno,
                                    "start": start, "end": end, "kind": "nested_call",
                                    "expression": _dotted(nested_call.func)})
            ordered_params = [a.arg for a in node.args.posonlyargs] + [a.arg for a in node.args.args]
            body = ast.Module(body=[kept_node], type_ignores=[])
            ctor_bindings = _constructor_bindings(s, body, imports,
                                                  modules, local, star)
            for call in (n for n in ast.walk(body) if isinstance(n, ast.Call)):
                target, expression, head = _resolve_call_target(
                    s, call, imports, modules, local, star, self_class, rebound)
                start, end = _span(s["data"], call)
                emitted = False
                if target is None and isinstance(call.func, ast.Attribute):
                    if isinstance(call.func.value, ast.Name):
                        receiver = ctor_bindings.get(call.func.value.id)
                        if receiver is not None and call.lineno > receiver["line"]:
                            callee = receiver["methods"].get(call.func.attr)
                            if callee is not None:
                                _emit_attribute_candidate(
                                    s, symbols, assertions, caller, call, callee,
                                    receiver["class"], call.func.value.id,
                                    receiver["binding"], receiver["constructor"])
                                emitted = True
                    elif self_class is not None and attr_bindings \
                            and isinstance(call.func.value, ast.Attribute) \
                            and isinstance(call.func.value.value, ast.Name) \
                            and call.func.value.value.id == "self":
                        fact = attr_bindings.get((self_class, call.func.value.attr))
                        if fact is not None:
                            assign, value, method_node = fact
                            if node.name != "__init__" or call.lineno > assign.lineno:
                                mlocal = {a.arg for a in ast.walk(method_node.args)
                                          if isinstance(a, ast.arg)}
                                methods = _class_methods(
                                    s, value.func.id, imports, modules,
                                    mlocal, star) if isinstance(value.func, ast.Name) else None
                                if methods:
                                    callee = methods.get(call.func.attr)
                                    if callee is not None:
                                        b_start, b_end = _span(s["data"], assign)
                                        c_start, c_end = _span(s["data"], value)
                                        _emit_attribute_candidate(
                                            s, symbols, assertions, caller, call, callee,
                                            value.func.id, "self." + call.func.value.attr,
                                            {"source": s["id"], "start": b_start, "end": b_end},
                                            {"source": s["id"], "start": c_start, "end": c_end})
                                        emitted = True
                if emitted:
                    continue
                if target is None and isinstance(call.func, ast.Name):
                    resolved = _resolve_class_symbol(
                        s, call.func.id, imports, modules, local, star)
                    if resolved is not None and resolved[0] in symbols:
                        class_key, how = resolved
                        definition = symbols[class_key]
                        call_ref = {"source": s["id"], "start": start, "end": end}
                        proposition = {"relation": "depends_on", "caller": caller,
                                       "callee": class_key, "via": "constructor_call"}
                        assertion = {"proposition": proposition, "source": s["id"],
                                     "start": start, "end": end,
                                     "resolution": {"expression": expression,
                                                    "rule": "constructor_call_candidate_v1",
                                                    "support": [{k: definition[k] for k in ("source", "start", "end")}]},
                                     "derivation": {"constructor": call.func.id,
                                                    "class": class_key, "how": how,
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
                    kind = ("nested_call"
                            if isinstance(call.func, ast.Name)
                            and call.func.id in nested_names
                            else "unresolved_call")
                    diagnostic = {"path": s["path"], "caller": caller, "line": call.lineno,
                                  "start": start, "end": end, "kind": kind, "expression": expression}
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
            kept_node, _nested_names, _nested_calls, _pruned = _without_nested_scopes(node)
            body = ast.Module(body=[kept_node], type_ignores=[])
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
                    # The caller is the CALL-SITE function (the one whose
                    # behavior depends on the callee), matching the
                    # registry-dispatch candidate shape; the parameter-call
                    # binding in the dispatcher stays in the derivation as
                    # evidence.
                    proposition = {"relation": "depends_on", "caller": _caller,
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
        kept_node, _nested_names, _nested_calls, _pruned = _without_nested_scopes(node)
        caller = s["functions"][node.name]
        aliases = {}
        for statement in kept_node.body:
            if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and isinstance(statement.value, ast.Name)):
                aliases[statement.targets[0].id] = statement.value.id
        for call in (n for n in ast.walk(ast.Module(body=[kept_node], type_ignores=[]))
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


def extract(root, exclude=()):
    """Full rebuild: capture, code resolution, then document extraction."""
    sources, diagnostics, modules, excluded = capture(root, exclude)
    symbols, assertions = {}, []
    _collect_code(sources, symbols, assertions, diagnostics)
    _resolve_code(sources, modules, symbols, assertions, diagnostics)
    _build_dispatch(sources, modules, symbols, assertions, diagnostics)
    pipeline.resolve_all(sources, symbols, assertions, diagnostics,
                         modules=modules)
    _build_docs(sources, symbols, assertions, diagnostics)
    return sources, sorted(symbols.values(), key=lambda x:x["key"]), sorted(assertions, key=lambda x:x["id"]), sorted(diagnostics, key=lambda x: (x["path"], x.get("line") or 0, x["kind"], x.get("start", 0))), excluded
