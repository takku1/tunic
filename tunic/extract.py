"""Bounded, non-executing Python AST extraction. Unresolved is not absent."""

import ast
import io
import os
from pathlib import Path
import tokenize

from .identity import digest, identity

PROFILE = "python-static-v3"
EXCLUDED = {".git", ".venv", "venv", "__pycache__", ".tunic", ".research", "node_modules"}
TEXT_EXTENSIONS = {".md", ".txt", ".rst"}


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


def capture(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source root must be a directory")
    sources, diagnostics, modules = [], [], {}
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
            data = p.read_bytes()
            source = {"path": path, "digest": digest(data), "data": data,
                      "id": identity("source", [path, digest(data)]), "tree": None, "text": text}
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


def extract(root):
    sources, diagnostics, modules = capture(root)
    symbols, assertions = {}, []
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

    for s in sources:
        tree = s["tree"]
        if tree is None:
            continue
        imports = {}
        import_evidence = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    binding = a.asname or a.name.split(".")[0]
                    imports[binding] = a.name if a.asname else a.name.split(".")[0]
                    start, end = _span(s["data"], node)
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
                        diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "star_import"})
                    else:
                        binding = a.asname or a.name
                        imports[binding] = prefix + "." + a.name if prefix else a.name
                        start, end = _span(s["data"], node)
                        import_evidence[binding] = {"source": s["id"], "start": start, "end": end}
        star = any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in tree.body)
        targets = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in s["functions"]:
                targets.append((s["functions"][node.name], node, None))
            elif isinstance(node, ast.ClassDef) and node.name in s["classes"]:
                for item in node.body:
                    if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and item.name in s["classes"][node.name]):
                        targets.append((s["classes"][node.name][item.name], item, node.name))
        for caller, node, self_class in targets:
            local = set(_bound_names(ast.Module(body=node.body, type_ignores=[])))
            # A recursive function name can itself be shadowed by a parameter/local.
            argnames = {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)}
            local.update(argnames)
            rebound = local - argnames
            if self_class is None and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                   for statement in node.body for n in ast.walk(statement)):
                diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "nested_scope"})
                continue
            body = ast.Module(body=node.body, type_ignores=[])
            for call in (n for n in ast.walk(body) if isinstance(n, ast.Call)):
                name = _dotted(call.func)
                target = None
                expression = name
                head = name.split(".")[0] if name else None
                constructor = None
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call):
                    base = call.func.value.func
                    if isinstance(base, ast.Name):
                        constructor = base.id
                        expression = constructor + "()." + call.func.attr
                if name and head not in local and head not in s["bindings"] and not star:
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
                start, end = _span(s["data"], call)
                if target:
                    proposition = {"relation": "static_call", "caller": caller, "callee": target}
                    definition = symbols[target]
                    support = [{k:definition[k] for k in ("source", "start", "end")}]
                    owner = head if name else constructor
                    if owner in import_evidence:
                        support.insert(0, import_evidence[owner])
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": expression, "rule": "unshadowed_static_binding_v1", "support": support},
                                 "producer": PROFILE, "status": "syntactic_observation"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
                else:
                    diagnostics.append({"path": s["path"], "caller": caller, "line": call.lineno,
                                        "start": start, "end": end, "kind": "unresolved_call", "expression": expression})
    text_ids = {s["id"] for s in sources if s.get("text")}
    code_keys = sorted(key for key, record in symbols.items() if record["source"] not in text_ids)
    seen_mentions = set()
    for s in sources:
        if not s.get("text"):
            continue
        mentioner = s["path"] + "::document"
        offset = 0
        for raw_line in s["data"].splitlines(keepends=True):
            start, end = offset, offset + len(raw_line)
            offset = end
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
    return sources, sorted(symbols.values(), key=lambda x:x["key"]), sorted(assertions, key=lambda x:x["id"]), sorted(diagnostics, key=lambda x: (x["path"], x.get("line") or 0, x["kind"], x.get("start", 0)))
