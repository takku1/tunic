"""Block splitting for component files (Tier-2).

Split the container BEFORE parsing, never regex-harvest across block
boundaries: .vue/.svelte <script> blocks and .astro frontmatter fences
feed the TS grammar as one joined module per file; template, style,
and markup stay inventory bytes. Notebook cells live in
tunic/notebook.py (same split-first rule, Python engine).

No virtual sources: the block module resolves under an alias path,
then every new span shifts to parent coordinates and alias keys are
rewritten to the parent path. Alias paths never persist, so refresh
(which is path-keyed) re-derives blocks deterministically.
"""

import bisect
import re
from pathlib import Path

from . import filetypes

BLOCK_EXTENSIONS = filetypes.BLOCK_EXTENSIONS

_TS_LANGS = frozenset({"ts", "tsx", "js", "jsx", "mts", "cts", "mjs",
                        "cjs", None})

_SCRIPT_OPEN = re.compile(rb"<script\b([^>]*)>", re.IGNORECASE)
_SCRIPT_CLOSE = re.compile(rb"</script\s*>", re.IGNORECASE)
_ATTR = re.compile(rb"([A-Za-z_:][\w:.-]*)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?")
_ASTRO_FENCE = re.compile(rb"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|$)",
                           re.DOTALL)


def is_block_path(path):
    return Path(path).suffix.lower() in BLOCK_EXTENSIONS


def _attr_value(tag_attrs, wanted):
    for match in _ATTR.finditer(tag_attrs):
        if match.group(1).lower() == wanted:
            return (match.group(2) or match.group(3)
                    or match.group(4) or b"").decode("ascii",
                                                     "replace").lower()
    return None


def split_script_blocks(path, data):
    """Split a component file into script blocks with parent spans.

    Returns (blocks, diagnostics). Each block has kind ("script" or
    "frontmatter"), lang, start/end byte offsets into data, and text.
    Unparseable containers yield diagnostics, never invented blocks.
    """
    suffix = Path(path).suffix.lower()
    diagnostics = []
    if suffix == ".astro":
        match = _ASTRO_FENCE.match(data)
        if match is None:
            return [], []
        start, end = match.start(1), match.end(1)
        return [{"kind": "frontmatter", "lang": "ts", "start": start,
                 "end": end, "text": data[start:end]}], []
    blocks = []
    pos = 0
    while True:
        opener = _SCRIPT_OPEN.search(data, pos)
        if opener is None:
            break
        attrs = opener.group(1) or b""
        content_start = opener.end()
        closer = _SCRIPT_CLOSE.search(data, content_start)
        if closer is None:
            diagnostics.append({"path": path, "caller": None,
                                "line": data.count(b"\n", 0, opener.start()) + 1,
                                "start": opener.start(), "end": opener.end(),
                                "kind": "unsupported_syntax",
                                "expression": "unclosed_script_block"})
            break
        if _attr_value(attrs, b"src") is not None:
            diagnostics.append({"path": path, "caller": None,
                                "line": data.count(b"\n", 0, opener.start()) + 1,
                                "start": opener.start(),
                                "end": closer.end(),
                                "kind": "unsupported_block",
                                "expression": "external_src"})
        else:
            lang = _attr_value(attrs, b"lang")
            if lang not in _TS_LANGS:
                diagnostics.append({"path": path, "caller": None,
                                    "line": data.count(b"\n", 0, opener.start()) + 1,
                                    "start": opener.start(),
                                    "end": closer.end(),
                                    "kind": "unsupported_block",
                                    "expression": "lang_%s" % lang})
            else:
                blocks.append({"kind": "script", "lang": lang or "js",
                               "start": content_start,
                               "end": closer.start(),
                               "text": data[content_start:closer.start()]})
        pos = closer.end()
    return blocks, diagnostics


def _join_suffix(blocks):
    for block in blocks:
        if (block.get("lang") or "") in ("tsx", "jsx"):
            return "tsx"
    return "ts"


def join_blocks(blocks, data):
    """Concatenate feedable blocks; map joined offsets to parents.

    Returns (joined, segments, suffix) where segments lists
    (joined_start, parent_offset, parent_line) per block.
    """
    joined = b""
    segments = []
    for block in blocks:
        segments.append((len(joined), block["start"],
                         data.count(b"\n", 0, block["start"]) + 1))
        joined += block["text"] + b"\n"
    return joined, segments, _join_suffix(blocks)


def block_import_targets(path, data, known_paths):
    """Resolved import targets of a component file's script blocks."""
    from . import tsgraph
    blocks, _diags = split_script_blocks(path, data)
    feedable = [b for b in blocks
                if b["kind"] in ("script", "frontmatter")]
    if not feedable:
        return set()
    joined, _segments, suffix = join_blocks(feedable, data)
    module, _errors = tsgraph.scan_module(path + ".block", joined, suffix)
    targets = set()
    for entry in module["imports"].values():
        target = tsgraph.resolve_specifier(entry.get("specifier"), path,
                                           known_paths)
        if target is not None:
            targets.add(target)
    return targets


def _seed_block_symbols(module, alias, symbols, source_id, is_test,
                        language):
    """Pre-seed precise symbols for block decls the harvest missed.

    Skip-if-exists: whole-file harvest spans (parent-absolute) win
    where present; created symbols carry joined-relative spans until
    the shift pass. Returns created keys.
    """
    created = []
    for name, decls in module["decls"].items():
        kinds = {d["kind"] for d in decls}
        if kinds <= {"type"}:
            continue
        key = "%s::%s" % (alias, name)
        if key in symbols:
            continue
        span = None
        line = 1
        for decl in decls:
            node = decl.get("node")
            if node is not None:
                span = (int(node.start_byte), int(node.end_byte))
                line = int(node.start_point[0]) + 1
                break
        if span is None:
            continue
        symbols[key] = {"key": key, "path": alias, "name": name,
                        "source": source_id, "line": line,
                        "start": span[0], "end": span[1],
                        "is_test": is_test, "language": language,
                        "derivation": "block_inventory_v1"}
        created.append(key)
    return created


def _shift_joined(joined, segments, alias, path, symbols, created,
                  assertions, diagnostics):
    """Shift block-relative spans to parent coordinates, rewrite alias.

    Only spans recorded from the joined text move: assertion/diag
    start/end/line, import-support spans, and pre-seeded block symbols.
    Support refs matching harvest (parent-absolute) symbol spans stay.
    """
    starts = [seg[0] for seg in segments]
    created_spans = {(symbols[key]["start"], symbols[key]["end"])
                     for key in created if key in symbols}

    def locate(offset):
        idx = bisect.bisect_right(starts, offset) - 1
        return segments[idx if idx >= 0 else 0]

    def shift(offset):
        jstart, parent_off, _pline = locate(offset)
        return offset - jstart + parent_off

    def shift_line(offset):
        jstart, _parent_off, pline = locate(offset)
        return pline + joined[jstart:offset].count(b"\n")

    def shift_support(ref):
        if not isinstance(ref, dict) or "start" not in ref:
            return
        if (ref["start"], ref.get("end")) in created_spans:
            end = shift(ref.get("end", ref["start"]))
            ref["start"] = shift(ref["start"])
            ref["end"] = end

    for key in created:
        if key in symbols:
            record = symbols[key]
            record["line"] = shift_line(record["start"])
            record["start"] = shift(record["start"])
            record["end"] = shift(record["end"])
    for assertion in assertions:
        proposition = assertion.get("proposition", {})
        for role in ("caller", "callee"):
            value = proposition.get(role)
            if isinstance(value, str) and value.startswith(alias):
                proposition[role] = path + value[len(alias):]
        assertion["start"] = shift(assertion["start"])
        assertion["end"] = shift(assertion["end"])
        for ref in assertion.get("resolution", {}).get("support", ()):
            shift_support(ref)
        from .identity import identity
        clean = {k: v for k, v in assertion.items() if k != "id"}
        assertion["id"] = identity("assertion", clean)
    for diag in diagnostics:
        if isinstance(diag.get("path"), str) \
                and diag["path"].startswith(alias):
            diag["path"] = path + diag["path"][len(alias):]
        caller = diag.get("caller")
        if isinstance(caller, str) and caller.startswith(alias):
            diag["caller"] = path + caller[len(alias):]
        if "line" in diag and isinstance(diag["line"], int):
            diag["line"] = shift_line(diag["start"])
        diag["start"] = shift(diag["start"])
        diag["end"] = shift(diag["end"])
    for key in list(created):
        if key in symbols and key.startswith(alias):
            record = symbols.pop(key)
            parent_key = path + key[len(alias):]
            if parent_key not in symbols:
                symbols[parent_key] = record
                record["key"] = parent_key
                record["path"] = path


def affected_paths(touched, fresh):
    """Touched component files plus their script-block importers."""
    touched, known = set(touched), set(fresh)
    out = {p for p in touched & known if is_block_path(p)}
    reverse = {}
    for path in fresh:
        if not is_block_path(path):
            continue
        targets = block_import_targets(path, fresh[path]["data"], known)
        if targets & touched:
            out.add(path)
        for target in targets:
            reverse.setdefault(target, set()).add(path)
    queue = list(out)
    while queue:
        current = queue.pop()
        for importer in reverse.get(current, ()):
            if importer not in out:
                out.add(importer)
                queue.append(importer)
    return out


def resolve_block_edges(sources, symbols, assertions, diagnostics,
                        fetch=None, known_paths=None):
    """Resolve verified call edges inside component script blocks."""
    from . import tsgraph
    records = [s for s in sources
               if is_block_path(s["path"]) and s.get("data")]
    if fetch is None:
        table = {s["path"]: (s["id"], s["data"]) for s in sources
                 if s.get("data")}
        fetch = table.get
    if known_paths is None:
        known_paths = {s["path"] for s in sources}
    for record in sorted(records, key=lambda s: s["path"]):
        path = record["path"]
        data = record["data"]
        blocks, split_diags = split_script_blocks(path, data)
        diagnostics.extend(split_diags)
        feedable = [b for b in blocks
                    if b["kind"] in ("script", "frontmatter")]
        if not feedable:
            continue
        joined, segments, suffix = join_blocks(feedable, data)
        alias = path + ".block.ts"
        if suffix == "tsx":
            alias = path + ".block.tsx"
        module, errors = tsgraph.scan_module(alias, joined, suffix)
        local_a, local_d = [], []
        created = []
        if module["has_error"]:
            node = errors[0] if errors else None
            local_d.append({"path": alias, "caller": None,
                            "line": int(node.start_point[0]) + 1
                            if node is not None else 1,
                            "start": int(node.start_byte)
                            if node is not None else 0,
                            "end": int(node.start_byte)
                            if node is not None else 0,
                            "kind": "unsupported_syntax",
                            "expression": ""})
        else:
            created = _seed_block_symbols(
                module, alias, symbols, record["id"],
                Path(path).name.startswith("test_"),
                "ts" if suffix == "ts" else "js")
            cache = {}
            tsgraph.resolve_file(alias, record["id"], joined, module,
                                 errors, symbols, fetch, known_paths,
                                 cache, local_a, local_d)
        _shift_joined(joined, segments, alias, path, symbols, created,
                      local_a, local_d)
        assertions.extend(local_a)
        diagnostics.extend(local_d)
