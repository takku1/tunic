"""Notebook cells (Tier-2): code cells feed the Python AST.

Split the container BEFORE parsing, never regex-harvest across cell
boundaries: code cells parse with the stdlib AST after magic stripping
(magics yield unsupported_syntax diagnostics on their cell), markdown
cells join the document channel as text_mention sources, and outputs
are never ingested. Cells share one notebook namespace, so cross-cell
calls resolve like same-module calls. Spans are line-granular raw
line bounds (columns do not survive JSON string encoding), and every
symbol key carries its cell index for provenance across re-execution.
"""

import ast
import bisect
import json
import re
from pathlib import Path

NOTEBOOK_EXTENSION = ".ipynb"

_MAGIC_LINE = re.compile(r"^\s*(%|!)")
_CELL_MAGIC = re.compile(r"^\s*%%")


def is_notebook_path(path):
    return Path(path).suffix.lower() == NOTEBOOK_EXTENSION


def split_notebook(data):
    """Decode a notebook into cells.

    Returns (cells, diagnostics). Each cell has idx, kind ("code" or
    "markdown"), source_lines, and source text. Malformed input yields
    a parse_error diagnostic and no cells.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return [], [{"path": "", "caller": None, "line": 1,
                     "start": 0, "end": 0, "kind": "parse_error",
                     "expression": "notebook_decode"}]
    try:
        notebook = json.loads(text)
    except ValueError:
        return [], [{"path": "", "caller": None, "line": 1,
                     "start": 0, "end": 0, "kind": "parse_error",
                     "expression": "notebook_json"}]
    raw_cells = notebook.get("cells")
    if not isinstance(raw_cells, list):
        return [], [{"path": "", "caller": None, "line": 1,
                     "start": 0, "end": 0, "kind": "parse_error",
                     "expression": "notebook_cells"}]
    cells = []
    for idx, raw in enumerate(raw_cells):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("cell_type")
        if kind not in ("code", "markdown"):
            continue
        source = raw.get("source", "")
        if isinstance(source, list):
            text = "".join(str(part) for part in source)
        else:
            text = str(source)
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        cells.append({"idx": idx, "kind": kind,
                      "source_lines": lines, "text": text})
    return cells, []


def strip_magics(lines):
    """Drop IPython magic lines; map cleaned index to original index."""
    cleaned = []
    index_map = []
    dropped = 0
    for orig, line in enumerate(lines):
        if _CELL_MAGIC.match(line) or _MAGIC_LINE.match(line):
            dropped += 1
            continue
        index_map.append(orig)
        cleaned.append(line)
    return cleaned, index_map, dropped


def _decode_items(region, base, offsets):
    """Incrementally decode JSON strings; each maps to its start line."""
    decoder = json.JSONDecoder()
    items = []
    ends = []
    text = region.decode("utf-8", "replace")
    pos = 0
    guard = 0
    while guard < 10000:
        guard += 1
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] == "]":
            break
        if text[pos] == "[":
            pos += 1
            continue
        if text[pos] != '"':
            return None, None
        try:
            decoded, end = decoder.raw_decode(text, pos)
        except ValueError:
            return None, None
        if not isinstance(decoded, str):
            return None, None
        line_no = base
        for cursor_off, lineno in offsets:
            if cursor_off <= pos:
                line_no = lineno
            else:
                break
        end_line = base
        for cursor_off, lineno in offsets:
            if cursor_off <= end:
                end_line = lineno
            else:
                break
        items.append((line_no, decoded))
        ends.append(end_line)
        pos = end
    return items, ends


def _array_items(raw_lines, start, inline_tail, want):
    """Collect (lineno, decoded) items of a source array, 1-based.

    Grows the region line by line until enough strings decode, then
    prefix-matches: the first len(want) strings of the array ARE the
    cell lines, whatever layout the encoder used.
    """
    fragments = []
    if inline_tail is not None:
        fragments.append((start + 1, inline_tail))
    lineno = start + 2
    while True:
        if lineno - 1 < len(raw_lines):
            fragments.append((lineno, raw_lines[lineno - 1]))
        region = b" ".join(fragment for _, fragment in fragments)
        base = fragments[0][0]
        offsets = []
        cursor = 0
        for frag_no, fragment in fragments:
            offsets.append((cursor, frag_no))
            cursor += len(fragment) + 1
        items, ends = _decode_items(region, base, offsets)
        if items is None:
            return None
        if len(items) >= len(want):
            got = [text for _, text in items[:len(want)]]
            if got == want or [g.rstrip("\n") for g in got] == \
                    [w.rstrip("\n") for w in want]:
                return items[:len(want)]
            return None
        if lineno - 1 >= len(raw_lines):
            break
        if raw_lines[lineno - 1].strip().endswith(b"]"):
            break
        lineno += 1
    return None


def map_cell_lines(raw_bytes, cells):
    """Map cell source lines to absolute raw line numbers.

    Each source line is a JSON string in the raw file; decoding then
    comparing is robust to encoder spacing/escape differences. Returns
    {cell_idx: [raw_lineno]} (1-based). Unmappable cells map to None:
    the cell abstains instead of inventing coordinates.
    """
    raw_lines = raw_bytes.split(b"\n")
    mapping = {}
    occurrences = [match.start()
                   for match in re.finditer(b'"source"', raw_bytes)]
    used = 0
    for cell in sorted(cells, key=lambda c: c["idx"]):
        expected = [line for line in cell["source_lines"]]
        if not expected:
            mapping[cell["idx"]] = []
            continue
        found = None
        for pos, occ in enumerate(occurrences[used:], start=used):
            line_no = raw_bytes.count(b"\n", 0, occ) + 1
            i = line_no - 1
            tail = raw_lines[i][occ - sum(
                len(line) + 1 for line in raw_lines[:i]):]
            try:
                rest = tail.split(b":", 1)[1].strip()
                if rest.endswith(b","):
                    rest = rest[:-1]
                if rest.startswith(b'"'):
                    whole = json.loads(rest.decode("utf-8"))
                    if whole == "\n".join(expected) or \
                            whole == "".join(expected):
                        found = [i + 1] * len(expected)
                        used = pos + 1
                        break
            except (ValueError, IndexError):
                pass
            inline = None
            if b"[" in tail:
                after = tail.split(b"[", 1)[1]
                inline = after if after.strip() else None
            items = _array_items(raw_lines, i, inline, expected)
            if items is None:
                continue
            found = [lineno for lineno, _ in items]
            used = pos + 1
            break
        mapping[cell["idx"]] = found
    return mapping


def _cell_key(path, idx, name):
    return "%s::cell[%d]::%s" % (path, idx, name)


def _line_starts(data):
    starts = [0]
    for match in re.finditer(b"\n", data):
        starts.append(match.end())
    return starts


def _merged_raw_map(parts):
    """Joined-line index to raw notebook line (None on separators)."""
    raw_of = []
    for _idx, chunk, index_map, raw_map, _names in parts:
        span = chunk.count(b"\n") + 1
        for local in range(1, span + 1):
            raw = None
            if local - 1 < len(index_map):
                orig = index_map[local - 1]
                if 0 <= orig < len(raw_map):
                    raw = raw_map[orig]
            raw_of.append(raw)
    return raw_of


def _nearest_raw(raw_of, line):
    if 1 <= line <= len(raw_of) and raw_of[line - 1] is not None:
        return raw_of[line - 1]
    for radius in range(1, len(raw_of) + 1):
        for cand in (line - radius, line + radius):
            if 1 <= cand <= len(raw_of) \
                    and raw_of[cand - 1] is not None:
                return raw_of[cand - 1]
    return None


def _merged_offset(offset, raw_of, joined_starts, raw_starts, raw_lines,
                   is_end):
    line_no = bisect.bisect_right(joined_starts, offset)
    raw_no = _nearest_raw(raw_of, line_no)
    if raw_no is None:
        return None
    if is_end:
        return raw_starts[raw_no - 1] + len(raw_lines[raw_no - 1])
    return raw_starts[raw_no - 1]


def _merged_spans(record, ctx, source_id, is_assertion):
    raw_of, joined_starts, raw_lines, raw_starts = ctx
    start = _merged_offset(record.get("start", 0), raw_of, joined_starts,
                           raw_starts, raw_lines, False)
    end = _merged_offset(record.get("end", record.get("start", 0)),
                         raw_of, joined_starts, raw_starts, raw_lines,
                         True)
    if start is None or end is None:
        return False
    record["start"] = start
    record["end"] = end
    if is_assertion:
        for ref in record.get("resolution", {}).get("support", ()):
            if not isinstance(ref, dict) or ref.get("source") != source_id:
                continue
            ref_start = _merged_offset(ref.get("start", 0), raw_of,
                                       joined_starts, raw_starts,
                                       raw_lines, False)
            ref_end = _merged_offset(ref.get("end", ref.get("start", 0)),
                                     raw_of, joined_starts, raw_starts,
                                     raw_lines, True)
            if ref_start is None or ref_end is None:
                continue
            ref["start"] = ref_start
            ref["end"] = ref_end
    if "line" in record and isinstance(record["line"], int):
        mapped = _nearest_raw(raw_of, record["line"])
        if mapped is not None:
            record["line"] = mapped
    return True

def resolve_notebook_cells(sources, symbols, assertions, diagnostics,
                           fetch=None, known_paths=None, modules=None):
    """Resolve notebook code cells through the shared Python engine."""
    records = [s for s in sources
               if is_notebook_path(s["path"]) and s.get("data")]
    if not records:
        return
    table = dict(modules) if modules else {}
    for record in sorted(records, key=lambda s: s["path"]):
        _resolve_notebook(record, symbols, assertions, diagnostics,
                          table)


def _cell_def_names(tree):
    """Top-level def names per cell: funcs plus {class: methods}."""
    funcs = set()
    methods = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            funcs.add(node.name)
            meths = set()
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef,
                                    ast.AsyncFunctionDef)):
                    meths.add(sub.name)
            methods[node.name] = meths
    return (funcs, methods)


def _owner_cell(name, cls, parts):
    """Cell index uniquely defining a name (or method), else None."""
    homes = []
    for idx, _chunk, _imap, _rmap, names in parts:
        funcs, methods = names
        if cls is None:
            if name in funcs:
                homes.append(idx)
        elif name in methods.get(cls, ()):
            homes.append(idx)
    if len(homes) == 1:
        return homes[0]
    return None


def _resolve_notebook(record, symbols, assertions, diagnostics, table):
    from . import extract
    from .identity import identity
    path = record["path"]
    data = record["data"]
    cells, split_diags = split_notebook(data)
    for diag in split_diags:
        diag["path"] = path
        diagnostics.append(diag)
    if not cells:
        return
    if path + "::document" not in symbols:
        symbols[path + "::document"] = {
            "key": path + "::document", "path": path, "name": "document",
            "source": record["id"], "line": 1, "start": 0,
            "end": len(data), "is_test": False}
    raw_map_all = map_cell_lines(data, cells)
    parts = []
    for cell in cells:
        if cell["kind"] != "code":
            continue
        raw_map = raw_map_all.get(cell["idx"])
        if raw_map is None:
            diagnostics.append({"path": path, "caller": None,
                                "line": 1, "start": 0, "end": 0,
                                "kind": "cell_span_unmapped",
                                "expression": "cell[%d]" % cell["idx"]})
            continue
        cleaned, index_map, dropped = strip_magics(cell["source_lines"])
        chunk = ("\n".join(cleaned) + "\n").encode("utf-8") if cleaned \
            else b""
        if dropped:
            first = index_map[0] + 1 if index_map else 1
            raw_no = raw_map[first - 1] \
                if 0 <= first - 1 < len(raw_map) else None
            diagnostics.append({"path": path, "caller": None,
                                "line": raw_no or 1, "start": 0,
                                "end": 0, "kind": "unsupported_syntax",
                                "expression": "ipython_magic"})
        if chunk.strip():
            try:
                ctree = ast.parse(chunk.decode("utf-8"))
            except (SyntaxError, ValueError):
                ctree = None
            if ctree is None:
                diagnostics.append({"path": path, "caller": None,
                                    "line": 1, "start": 0, "end": 0,
                                    "kind": "unsupported_syntax",
                                    "expression": "cell[%d]" % cell["idx"]})
                continue
            names = _cell_def_names(ctree)
        else:
            names = (set(), {})
        parts.append((cell["idx"], chunk, index_map, raw_map, names))
    if not any(chunk.strip() for _, chunk, _, _, _ in parts):
        _mention_cells(record, cells, raw_map_all, symbols, assertions,
                       diagnostics)
        return
    joined = b"".join(chunk + b"\n" for _, chunk, _, _, _ in parts)
    try:
        tree = ast.parse(joined.decode("utf-8")) if joined.strip() \
            else ast.Module(body=[], type_ignores=[])
    except (SyntaxError, ValueError):
        diagnostics.append({"path": path, "caller": None, "line": 1,
                            "start": 0, "end": 0,
                            "kind": "unsupported_syntax",
                            "expression": "notebook_join"})
        _mention_cells(record, cells, raw_map_all, symbols, assertions,
                       diagnostics)
        return
    facade = {"path": path, "id": record["id"], "data": joined,
              "tree": tree, "module": extract.module_of(path)}
    local_d = []
    extract._collect_code([facade], symbols, [], local_d)
    ctx = (_merged_raw_map(parts), _line_starts(joined),
           data.split(b"\n"), _line_starts(data))
    rename = {}
    for name in list(facade.get("functions", {})):
        home = _owner_cell(name, None, parts)
        if home is None:
            continue
        plain = "%s::%s" % (path, name)
        cell_key = _cell_key(path, home, name)
        if plain in symbols:
            entry = symbols.pop(plain)
            entry["key"] = cell_key
            symbols[cell_key] = entry
            rename[plain] = cell_key
            facade["functions"][name] = cell_key
    for cls in list(facade.get("classes", {})):
        home = _owner_cell(cls, None, parts)
        if home is not None:
            plain_cls = "%s::%s" % (path, cls)
            cell_cls = _cell_key(path, home, cls)
            if plain_cls in symbols:
                entry = symbols.pop(plain_cls)
                entry["key"] = cell_cls
                symbols[cell_cls] = entry
                rename[plain_cls] = cell_cls
        for method in list(facade["classes"][cls]):
            home_m = _owner_cell(method, cls, parts)
            if home_m is None:
                continue
            plain_m = "%s::%s.%s" % (path, cls, method)
            cell_m = _cell_key(path, home_m, "%s.%s" % (cls, method))
            if plain_m in symbols:
                entry = symbols.pop(plain_m)
                entry["key"] = cell_m
                symbols[cell_m] = entry
                rename[plain_m] = cell_m
                facade["classes"][cls][method] = cell_m
    for diag in local_d:
        _merged_spans(diag, ctx, None, False)
        diagnostics.append(diag)
    for cell_key in rename.values():
        if cell_key in symbols:
            _merged_spans(symbols[cell_key], ctx, None, False)
    modules = dict(table)
    modules[facade["module"]] = \
        list(modules.get(facade["module"], [])) + [facade]
    local_a, local_d = [], []
    extract._resolve_code([facade], modules, symbols, local_a, local_d)
    for assertion in local_a:
        proposition = assertion.get("proposition", {})
        for role in ("caller", "callee"):
            value = proposition.get(role)
            if isinstance(value, str) and value in rename:
                proposition[role] = rename[value]
        _merged_spans(assertion, ctx, record["id"], True)
        clean = {k: v for k, v in assertion.items() if k != "id"}
        assertion["id"] = identity("assertion", clean)
        assertions.append(assertion)
    for diag in local_d:
        caller = diag.get("caller")
        if isinstance(caller, str) and caller in rename:
            diag["caller"] = rename[caller]
        _merged_spans(diag, ctx, None, False)
        diagnostics.append(diag)
    _mention_cells(record, cells, raw_map_all, symbols, assertions,
                   diagnostics)

def _mention_cells(record, cells, raw_map_all, symbols, assertions,
                   diagnostics):
    """Markdown cells join the document channel as text_mention sources."""
    from . import extract
    from .identity import identity
    path = record["path"]
    mentioner = path + "::document"
    code_keys = sorted(key for key, item in symbols.items()
                       if item.get("source") != record["id"]
                       and item.get("kind") != "class"
                       and not key.endswith("::document"))
    seen = set()
    raw_lines = record["data"].split(b"\n")
    raw_starts = _line_starts(record["data"])
    for cell in cells:
        if cell["kind"] != "markdown":
            continue
        raw_map = raw_map_all.get(cell["idx"]) or []
        for number, text in enumerate(cell["source_lines"]):
            raw_no = raw_map[number] if number < len(raw_map) else None
            for key in code_keys:
                if key in text and (cell["idx"], number, key) not in seen:
                    seen.add((cell["idx"], number, key))
                    if raw_no is None:
                        start, end, line = 0, 0, 1
                    else:
                        start = raw_starts[raw_no - 1]
                        end = start + len(raw_lines[raw_no - 1])
                        line = raw_no
                    assertion = {
                        "proposition": {"relation": "text_mention",
                                       "caller": mentioner,
                                       "callee": key},
                        "source": record["id"], "start": start,
                        "end": end,
                        "resolution": {"expression": key,
                                       "rule": "exact_key_match_v1",
                                       "support": []},
                        "producer": extract.PROFILE,
                        "status": "syntactic_observation"}
                    assertion["id"] = identity("assertion", assertion)
                    assertions.append(assertion)
                    break
