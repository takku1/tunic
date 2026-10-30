"""Bounded, non-executing Python AST extraction. Unresolved is not absent."""

import ast
import io
import os
from pathlib import Path
import tokenize

from .identity import digest, identity

PROFILE = "python-static-v1"
EXCLUDED = {".git", ".venv", "venv", "__pycache__", ".tunic", ".research", "node_modules"}


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
            if not name.endswith(".py"):
                continue
            p = Path(base)/name
            path = p.relative_to(root).as_posix()
            if p.is_symlink():
                diagnostics.append({"path": path, "kind": "excluded_symlink"})
                continue
            data = p.read_bytes()
            source = {"path": path, "digest": digest(data), "data": data,
                      "id": identity("source", [path, digest(data)]), "tree": None}
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
        if any(isinstance(n, (ast.ClassDef, ast.Lambda)) for n in ast.walk(tree)):
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
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in s["functions"]:
                continue
            caller = s["functions"][node.name]
            local = set(_bound_names(ast.Module(body=node.body, type_ignores=[])))
            # A recursive function name can itself be shadowed by a parameter/local.
            local.update(a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg))
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                   for statement in node.body for n in ast.walk(statement)):
                diagnostics.append({"path": s["path"], "line": node.lineno, "kind": "nested_scope"})
                continue
            body = ast.Module(body=node.body, type_ignores=[])
            for call in (n for n in ast.walk(body) if isinstance(n, ast.Call)):
                name = _dotted(call.func)
                target = None
                head = name.split(".")[0] if name else None
                if name and head not in local and head not in s["bindings"] and not star:
                    if "." not in name and name in s["functions"]:
                        target = s["functions"][name]
                    elif head in imports:
                        full = imports[head] + name[len(head):]
                        module, _, function = full.rpartition(".")
                        candidates = modules.get(module, [])
                        if len(candidates) == 1:
                            target = candidates[0].get("functions", {}).get(function)
                start, end = _span(s["data"], call)
                if target:
                    proposition = {"relation": "static_call", "caller": caller, "callee": target}
                    definition = symbols[target]
                    support = [{k:definition[k] for k in ("source", "start", "end")}]
                    if head in import_evidence:
                        support.insert(0, import_evidence[head])
                    assertion = {"proposition": proposition, "source": s["id"], "start": start, "end": end,
                                 "resolution": {"expression": name, "rule": "unshadowed_static_binding_v1", "support": support},
                                 "producer": PROFILE, "status": "syntactic_observation"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
                else:
                    diagnostics.append({"path": s["path"], "caller": caller, "line": call.lineno,
                                        "start": start, "end": end, "kind": "unresolved_call", "expression": name})
    return sources, sorted(symbols.values(), key=lambda x:x["key"]), sorted(assertions, key=lambda x:x["id"]), sorted(diagnostics, key=lambda x: (x["path"], x.get("line") or 0, x["kind"], x.get("start", 0)))
