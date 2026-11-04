"""Schema-first reference edges (Tier-4): SQL, GraphQL, Protobuf.

Schema definitions are entities (`db.sql::users`,
`db.sql::users.id`); references between them (foreign keys, field
types, interface implementations, unions, rpc signatures) are a
`references` relation -- never a static_call, so impact queries can
never confuse a foreign key with a call. Unique-or-diagnostic
everywhere: missing or ambiguous targets abstain with a receipt,
never an invented edge. Views, indexes, triggers, imports, options,
and directives are skipped silently: file-level or out-of-scope
constructs, documented here, not gaps.
"""

import re
from pathlib import PurePosixPath

from . import filetypes
from .identity import identity

SCHEMA_EXTENSIONS = filetypes.SCHEMA_EXTENSIONS
PRODUCER = "schema_reference_v1"


def is_schema_path(path):
    return filetypes.suffix_of(path) in SCHEMA_EXTENSIONS


def _diag(path, kind, line, expression):
    return {"path": path, "line": line, "start": 0, "end": 0,
            "kind": kind, "expression": expression}


def _strip_comments(data, line_comment):
    """Blank out comments, preserving every byte offset and newline."""
    out = bytearray(data)
    n = len(out)
    i, quote = 0, None
    while i < n:
        ch = out[i]
        if quote is not None:
            if ch == quote:
                if i + 1 < n and out[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in (0x22, 0x27, 0x60):
            quote = ch
            i += 1
            continue
        if line_comment == b"--" and ch == 0x5B:
            end = bytes(out).find(b"]", i)
            i = end + 1 if end != -1 else i + 1
            continue
        if ch == line_comment[0] and i + 1 < n \
                and out[i + 1] == line_comment[1]:
            while i < n and out[i] != 0x0A:
                out[i] = 0x20
                i += 1
            continue
        if ch == 0x2F and i + 1 < n and out[i + 1] == 0x2A:
            out[i] = out[i + 1] = 0x20
            i += 2
            while i + 1 < n and not (out[i] == 0x2A
                                     and out[i + 1] == 0x2F):
                if out[i] != 0x0A:
                    out[i] = 0x20
                i += 1
            if i + 1 < n:
                out[i] = out[i + 1] = 0x20
                i += 2
            continue
        i += 1
    return bytes(out)


def _line_of(starts, offset):
    import bisect
    return bisect.bisect_right(starts, offset)


def _line_starts(data):
    starts = [0]
    for match in re.finditer(b"\n", data):
        starts.append(match.end())
    return starts


def _match_block(text, open_pos):
    """Match a paren/brace from open_pos; quote-aware, returns end+1."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    closer = pairs[text[open_pos]]
    depth, quote, pos = 0, None, open_pos
    while pos < len(text):
        ch = text[pos]
        if quote is not None:
            if ch == "\\":
                pos += 2
                continue
            if ch == quote:
                if pos + 1 < len(text) and text[pos + 1] == quote:
                    pos += 2
                    continue
                quote = None
            pos += 1
            continue
        if ch in "\"'`":
            quote = ch
            pos += 1
            continue
        if ch == text[open_pos]:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return -1


def _split_top(text):
    """Split on top-level commas; quote- and depth-aware.

    Returns [(chunk, start_offset)] with offsets relative to text.
    """
    parts, depth, quote, cur = [], 0, None, 0
    pos = 0
    while pos < len(text):
        ch = text[pos]
        if quote is not None:
            if ch == "\\":
                pos += 2
                continue
            if ch == quote:
                quote = None
            pos += 1
            continue
        if ch in "\"'`":
            quote = ch
            pos += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append((text[cur:pos], cur))
            cur = pos + 1
        pos += 1
    parts.append((text[cur:], cur))
    return parts


_IDENT = r'''(?:"(?:[^"]|"")+"|`(?:[^`]|``)+`|\[(?:[^\]]|\]\])+|[\w$]+)'''
_QUALIFIED = r"""(?P<name>%(i)s(?:\s*\.\s*%(i)s)*)""" % {"i": _IDENT}


def _unquote(token):
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] \
            and token[0] in "\"'`":
        return token[1:-1].replace(token[0] * 2, token[0])
    if token.startswith("[") and token.endswith("]"):
        return token[1:-1].replace("]]", "]")
    return token


def _last_part(qualified):
    parts = re.findall(_IDENT, qualified)
    return _unquote(parts[-1]) if parts else qualified


_TABLE = re.compile(
    r"""\bCREATE\s+(?:TEMP(?:ORARY)?\s+|GLOBAL\s+|LOCAL\s+|UNLOGGED\s+|OR\s+REPLACE\s+)*TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"""
    + _QUALIFIED, re.IGNORECASE)
_ALTER_FK = re.compile(
    r"""\bALTER\s+TABLE\s+""" + _QUALIFIED.replace("?P<name>", "?P<table>")
    + r"""\s+ADD\s+(?:CONSTRAINT\s+[\w$]+\s+)?FOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*REFERENCES\s+"""
    + _QUALIFIED.replace("?P<name>", "?P<parent>")
    + r"""(?:\s*\((?P<pcols>[^)]*)\))?""", re.IGNORECASE)
_FK_COLS = re.compile(
    r"""\bFOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*REFERENCES\s+"""
    + _QUALIFIED.replace("?P<name>", "?P<parent>")
    + r"""(?:\s*\((?P<pcols>[^)]*)\))?""", re.IGNORECASE)
_INLINE_REF = re.compile(
    r"""\bREFERENCES\s+""" + _QUALIFIED.replace("?P<name>", "?P<parent>")
    + r"""(?:\s*\((?P<pcols>[^)]*)\))?""", re.IGNORECASE)
_CONSTRAINT_LEAD = re.compile(
    r"^\s*(CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK|LIKE|EXCLUDE)\b",
    re.IGNORECASE)


def _last_span(match, group="name"):
    """Absolute span of the last identifier inside a qualified name."""
    base = match.start(group)
    parts = list(re.finditer(_IDENT, match.group(group)))
    if not parts:
        return match.start(group), match.end(group)
    last = parts[-1]
    return base + last.start(), base + last.end()


def _parse_sql(data, path, source_id):
    """CREATE TABLE columns plus FOREIGN KEY references, quoted-aware."""
    try:
        text = _strip_comments(data, b"--").decode("utf-8")
    except UnicodeError as error:
        return [], [_diag(path, "decode_error", 1, "config_decode")], []
    starts = _line_starts(data)
    entities, diags, refs = [], [], []

    def entity(name, start, end):
        entities.append({
            "key": "%s::%s" % (path, name), "path": path,
            "name": name, "source": source_id,
            "line": _line_of(starts, start), "start": start,
            "end": end, "is_test": False, "producer": PRODUCER,
            "value": None})

    def reference(caller, callee, start, end, rule):
        refs.append((caller, callee, _line_of(starts, start), start,
                     end, rule))

    seen_tables = set()
    for match in _TABLE.finditer(text):
        table = _last_part(match.group("name"))
        name_start, name_end = _last_span(match)
        if table in seen_tables:
            diags.append(_diag(path, "duplicate_table",
                                _line_of(starts, name_start), table))
            continue
        entity(table, name_start, name_end)
        seen_tables.add(table)
        seen_columns = set()
        rest = text[match.end():]
        gap = len(rest) - len(rest.lstrip())
        if match.end() + gap >= len(text) \
                or text[match.end() + gap] != "(":
            continue
        open_pos = match.end() + gap
        close_pos = _match_block(text, open_pos)
        if close_pos == -1:
            diags.append(_diag(path, "unsupported_syntax",
                                _line_of(starts, open_pos), table))
            continue
        body = text[open_pos + 1:close_pos - 1]
        body_base = open_pos + 1
        for entry, entry_off in _split_top(body):
            stripped = entry.strip()
            if not stripped:
                continue
            entry_start = body_base + entry_off + (
                len(entry) - len(entry.lstrip()))
            table_fk = _FK_COLS.search(stripped)
            if table_fk:
                local = [c.strip() for c in
                         table_fk.group("cols").split(",")]
                parent = _last_part(table_fk.group("parent"))
                pcols = [c.strip() for c in
                         (table_fk.group("pcols") or "").split(",")
                         if c.strip()]
                if pcols and len(pcols) != len(local):
                    diags.append(_diag(
                        path, "unsupported_fk",
                        _line_of(starts, entry_start),
                        stripped[:40]))
                    continue
                for pos, local_col in enumerate(local):
                    callee = "%s.%s" % (parent, _unquote(
                        pcols[pos])) if pcols else parent
                    fk_start = entry.find(local_col)
                    if fk_start < 0:
                        continue
                    fk_start += entry_start
                    reference("%s::%s.%s" % (
                        path, table, _unquote(local_col)), callee,
                        fk_start, fk_start + len(local_col),
                        "sql_fk_v1")
                continue
            if _CONSTRAINT_LEAD.match(stripped):
                continue
            col_match = re.match(_IDENT, stripped)
            if not col_match:
                continue
            column = _unquote(col_match.group(0))
            if column in seen_columns:
                diags.append(_diag(
                    path, "duplicate_column",
                    _line_of(starts, entry_start),
                    "%s.%s" % (table, column)))
                continue
            seen_columns.add(column)
            col_start = entry_start + col_match.start()
            col_end = entry_start + col_match.end()
            entity("%s.%s" % (table, column), col_start, col_end)
            inline = _INLINE_REF.search(stripped)
            if not inline:
                continue
            parent = _last_part(inline.group("parent"))
            pcols = inline.group("pcols")
            if pcols and "," in pcols:
                diags.append(_diag(
                    path, "unsupported_fk",
                    _line_of(starts, col_start), stripped[:40]))
                continue
            callee = "%s.%s" % (parent, _unquote(pcols.strip())) \
                if pcols and pcols.strip() else parent
            reference("%s::%s.%s" % (path, table, column),
                      callee, col_start, col_end, "sql_fk_v1")
    for match in _ALTER_FK.finditer(text):
        table = _last_part(match.group("table"))
        parent = _last_part(match.group("parent"))
        local = [c.strip() for c in match.group("cols").split(",")]
        pcols = [c.strip() for c in
                 (match.group("pcols") or "").split(",") if c.strip()]
        if pcols and len(pcols) != len(local):
            diags.append(_diag(path, "unsupported_fk",
                                _line_of(starts, match.start()),
                                "alter_fk"))
            continue
        for pos, local_col in enumerate(local):
            callee = "%s.%s" % (parent, _unquote(
                pcols[pos])) if pcols else parent
            col_pos = match.start("cols") + match.group(
                "cols").find(local_col)
            reference("%s::%s.%s" % (path, table,
                                     _unquote(local_col)), callee,
                      col_pos, col_pos + len(local_col), "sql_fk_v1")
    return entities, diags, refs


_GQL_SCALARS = {"String", "Int", "Float", "Boolean", "ID"}
_GQL_DEF = re.compile(
    r"""(?P<kind>type|interface|input|enum|scalar|union)\s+(?P<name>[A-Za-z_]\w*)(?:\s+implements\s+(?P<impl>[A-Za-z_][\w\s&]*))?""")
_GQL_FIELD = re.compile(
    r"""^\s*(?P<name>[A-Za-z_]\w*)\s*(?:\((?P<args>[^)]*)\))?\s*:\s*(?P<type>[^@\s]+)""")


def _gql_base(annotated):
    """Strip list/non-null wrappers and directives from a type."""
    token = annotated.strip().strip("!")
    while token.startswith("[") and token.endswith("]"):
        token = token[1:-1].strip().strip("!")
    return token.split()[0].strip("![]")


def _parse_graphql(data, path, source_id):
    """Types/fields plus field-type, implements, and union references."""
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        return [], [_diag(path, "decode_error", 1, "config_decode")], []
    starts = _line_starts(data)
    entities, diags, refs = [], [], []

    def entity(name, start, end):
        entities.append({
            "key": "%s::%s" % (path, name), "path": path,
            "name": name, "source": source_id,
            "line": _line_of(starts, start), "start": start,
            "end": end, "is_test": False, "producer": PRODUCER,
            "value": None})

    def reference(caller, callee, start, end, rule):
        refs.append((caller, callee, _line_of(starts, start), start,
                     end, rule))

    seen = set()
    for match in _GQL_DEF.finditer(text):
        kind, name = match.group("kind"), match.group("name")
        name_start = match.start("name")
        if name in seen:
            if kind != "extend":
                diags.append(_diag(path, "duplicate_type",
                                    _line_of(starts, name_start),
                                    name))
            continue
        seen.add(name)
        entity(name, name_start, match.end("name"))
        impl = match.group("impl")
        if impl:
            for iface in re.split(r"[&\s]+", impl.strip()):
                if iface:
                    reference("%s::%s" % (path, name), iface,
                              name_start, match.end("name"),
                              "gql_implements_v1")
    for match in re.finditer(
            r"""\bextend\s+type\s+(?P<name>[A-Za-z_]\w*)""", text):
        name = match.group("name")
        if name in seen:
            continue
        diags.append(_diag(path, "unresolved_extend",
                            _line_of(starts, match.start("name")),
                            name))
    for match in re.finditer(
            r"""\bunion\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<body>[A-Za-z_][\w |]*)""",
            text):
        name = match.group("name")
        for member in re.split(r"[\s|]+", match.group("body").strip()):
            if member:
                reference("%s::%s" % (path, name), member,
                          match.start("name"), match.end("name"),
                          "gql_union_v1")
    for match in _GQL_DEF.finditer(text):
        kind, name = match.group("kind"), match.group("name")
        rest = text[match.end():]
        gap = len(rest) - len(rest.lstrip())
        if match.end() + gap >= len(text) \
                or text[match.end() + gap] != "{":
            continue
        open_pos = match.end() + gap
        close_pos = _match_block(text, open_pos)
        if close_pos == -1:
            diags.append(_diag(path, "unsupported_syntax",
                                _line_of(starts, open_pos), name))
            continue
        body = text[open_pos + 1:close_pos - 1]
        seen_fields = set()
        if kind == "enum":
            scrubbed = re.sub(r'"(?:[^"\\]|\\.)*"', '""', body)
            for value in re.finditer(r"([A-Za-z_]\w*)", scrubbed):
                if value.start() > 0 and scrubbed[value.start() - 1] \
                        == "@":
                    continue
                if scrubbed[value.end():].lstrip().startswith(":"):
                    continue
                vstart = open_pos + 1 + value.start(1)
                if value.group(1) in seen_fields:
                    diags.append(_diag(
                        path, "duplicate_field",
                        _line_of(starts, vstart), value.group(1)))
                    continue
                seen_fields.add(value.group(1))
                entity("%s.%s" % (name, value.group(1)), vstart,
                       open_pos + 1 + value.end(1))
            continue
        for offset, raw_line in enumerate(body.split("\n")):
            anchor = open_pos + 1 + sum(
                len(part) + 1
                for part in body.split("\n")[:offset])
            if kind in ("scalar", "union"):
                continue
            field = _GQL_FIELD.match(raw_line)
            if not field:
                continue
            fname = field.group("name")
            if fname in seen_fields:
                diags.append(_diag(path, "duplicate_field",
                                    _line_of(starts, anchor), fname))
                continue
            seen_fields.add(fname)
            fstart = anchor + field.start("name")
            fend = anchor + field.end("name")
            entity("%s.%s" % (name, fname), fstart, fend)
            bases = [_gql_base(field.group("type"))]
            for arg in re.split(r",", field.group("args") or ""):
                if ":" in arg:
                    bases.append(_gql_base(arg.split(":")[-1]))
            for base in bases:
                if not base or base in _GQL_SCALARS:
                    continue
                reference("%s::%s.%s" % (path, name, fname), base,
                          fstart, fend, "gql_field_v1")
    return entities, diags, refs


_PROTO_SCALARS = {"double", "float", "int32", "int64", "uint32",
                  "uint64", "sint32", "sint64", "fixed32", "fixed64",
                  "sfixed32", "sfixed64", "bool", "string", "bytes"}
_PROTO_MSG = re.compile(r"""\bmessage\s+(?P<name>[A-Za-z_]\w*)""")
_PROTO_ENUM = re.compile(r"""\benum\s+(?P<name>[A-Za-z_]\w*)""")
_PROTO_FIELD = re.compile(
    r"""^\s*(?:(?P<label>repeated|optional|required)\s+)?(?P<type>\.?[A-Za-z_][\w.]*|map\s*<[^;{}]*>)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<number>\d+)""")
_PROTO_RPC = re.compile(
    r"""\brpc\s+(?P<name>[A-Za-z_]\w*)\s*\(\s*(?:stream\s+)?(?P<inp>\.?[A-Za-z_][\w.]*)\s*\)\s*returns\s*\(\s*(?:stream\s+)?(?P<out>\.?[A-Za-z_][\w.]*)\s*\)""")
_PROTO_EXTEND = re.compile(
    r"""\bextend\s+(?P<target>\.?[A-Za-z_][\w.]*)""")
_PROTO_SERVICE = re.compile(r"""\bservice\s+(?P<name>[A-Za-z_]\w*)""")


def _proto_last(dotted):
    return dotted.lstrip(".").split(".")[-1]


def _parse_proto(data, path, source_id):
    """Messages/enums/services plus field-type and rpc references."""
    try:
        text = _strip_comments(data, b"//").decode("utf-8")
    except UnicodeError as error:
        return [], [_diag(path, "decode_error", 1, "config_decode")], []
    starts = _line_starts(data)
    entities, diags, refs = [], [], []

    def entity(name, start, end):
        entities.append({
            "key": "%s::%s" % (path, name), "path": path,
            "name": name, "source": source_id,
            "line": _line_of(starts, start), "start": start,
            "end": end, "is_test": False, "producer": PRODUCER,
            "value": None})

    def reference(caller, callee, start, end, rule):
        refs.append((caller, callee, _line_of(starts, start), start,
                     end, rule))

    seen = set()
    deferred = []

    def emit_type(prefix, match):
        name = match.group("name")
        dotted = "%s.%s" % (prefix, name) if prefix else name
        if dotted in seen:
            diags.append(_diag(path, "duplicate_type",
                                _line_of(starts, match.start("name")),
                                dotted))
            return None
        seen.add(dotted)
        entity(dotted, match.start("name"), match.end("name"))
        return dotted

    def block_end(from_pos):
        """Brace extent from the first { at or after from_pos."""
        rest = text[from_pos:]
        gap = len(rest) - len(rest.lstrip())
        if from_pos + gap >= len(text) \
                or text[from_pos + gap] != "{":
            return None
        open_pos = from_pos + gap
        close_pos = _match_block(text, open_pos)
        return (open_pos, close_pos) if close_pos != -1 else None

    def walk_fields(prefix, open_pos, close_pos, skip=()):
        """Field lines at one level; nested block ranges are skipped."""
        body = text[open_pos + 1:close_pos - 1]
        seen_fields = set()
        for offset, raw_line in enumerate(body.split("\n")):
            anchor = open_pos + 1 + sum(
                len(part) + 1 for part in body.split("\n")[:offset])
            if any(start < anchor < end for start, end in skip):
                continue
            field = _PROTO_FIELD.match(raw_line)
            if not field:
                continue
            fname = field.group("name")
            if fname in seen_fields:
                diags.append(_diag(path, "duplicate_field",
                                    _line_of(starts, anchor), fname))
                continue
            seen_fields.add(fname)
            dotted = "%s.%s" % (prefix, fname) if prefix else fname
            fstart = anchor + field.start("name")
            fend = anchor + field.end("name")
            entity(dotted, fstart, fend)
            raw_type = field.group("type")
            if raw_type.startswith("map"):
                inner = raw_type[raw_type.find("<") + 1:
                                 raw_type.rfind(">")]
                targets = [inner.split(",")[-1].strip()]
            else:
                targets = [raw_type]
            for target in targets:
                base = _proto_last(target)
                if base in _PROTO_SCALARS:
                    continue
                tstart = anchor + field.start("type") + (
                    len(field.group("type"))
                    - len(field.group("type").lstrip()))
                reference("%s::%s" % (path, dotted), base, tstart,
                          tstart + len(target.strip()), "proto_field_v1")

    def walk_message(prefix, match):
        dotted = emit_type(prefix, match)
        if dotted is None:
            return
        span = block_end(match.end())
        if span is None:
            return
        open_pos, close_pos = span
        events = []
        for nested in _PROTO_MSG.finditer(text, open_pos + 1,
                                          close_pos - 1):
            events.append((nested.start(), "message", nested, 0))
        for nested in _PROTO_ENUM.finditer(text, open_pos + 1,
                                           close_pos - 1):
            events.append((nested.start(), "enum", nested, 0))
        inner = text[open_pos + 1:close_pos - 1]
        for nested in re.finditer(r"\boneof\s+(?P<name>[A-Za-z_]\w*)",
                                  inner):
            events.append((nested.start(), "oneof", nested,
                           open_pos + 1))
        for nested in _PROTO_EXTEND.finditer(text, open_pos + 1,
                                             close_pos - 1):
            events.append((nested.start(), "extend", nested, 0))
        skip, claimed = [], []
        for _, kind, nested, base in sorted(events):
            start = base + nested.start() if base else nested.start()
            if any(low < start < high for low, high in claimed):
                continue
            if kind == "message":
                walk_message(dotted, nested)
            elif kind == "enum":
                walk_enum(dotted, nested)
            elif kind == "oneof":
                name = nested.group("name")
                child = block_end(base + nested.end())
                if child is None or child[1] > close_pos:
                    continue
                entity("%s.%s" % (dotted, name),
                       base + nested.start("name"),
                       base + nested.end("name"))
                walk_fields(dotted, child[0], child[1])
            elif kind == "extend":
                target = _proto_last(nested.group("target"))
                child = block_end(nested.end())
                if child is None or child[1] > close_pos:
                    continue
                deferred.append((target, child[0], child[1],
                                 nested.start()))
            else:
                continue
            child = block_end((base + nested.end()) if base
                              else nested.end())
            if child is not None and child[1] <= close_pos:
                claimed.append(child)
                skip.append(child)
        walk_fields(dotted, open_pos, close_pos, skip)

    def walk_enum(prefix, match):
        dotted = emit_type(prefix, match)
        if dotted is None:
            return
        rest = text[match.end():]
        gap = len(rest) - len(rest.lstrip())
        if match.end() + gap >= len(text) \
                or text[match.end() + gap] != "{":
            return
        open_pos = match.end() + gap
        close_pos = _match_block(text, open_pos)
        if close_pos == -1:
            return
        body = text[open_pos + 1:close_pos - 1]
        seen_values = set()
        for value in re.finditer(r"([A-Za-z_]\w*)\s*=", body):
            before = body[:value.start()]
            if re.search(r"\boption\b[^\n;]*$", before.split("\n")[-1]):
                continue
            if value.group(1) in seen_values:
                diags.append(_diag(
                    path, "duplicate_field",
                    _line_of(starts, open_pos + 1 + value.start(1)),
                    value.group(1)))
                continue
            seen_values.add(value.group(1))
            vstart = open_pos + 1 + value.start(1)
            entity("%s.%s" % (dotted, value.group(1)), vstart,
                   open_pos + 1 + value.end(1))

    top = []
    for match in _PROTO_MSG.finditer(text):
        top.append((match.start(), "message", match))
    for match in _PROTO_ENUM.finditer(text):
        top.append((match.start(), "enum", match))
    for match in _PROTO_EXTEND.finditer(text):
        top.append((match.start(), "extend", match))
    claimed = []
    for _, kind, match in sorted(top):
        if any(low < match.start() < high for low, high in claimed):
            continue
        if kind == "message":
            walk_message("", match)
        elif kind == "enum":
            walk_enum("", match)
        else:
            target = _proto_last(match.group("target"))
            child = block_end(match.end())
            if child is None:
                continue
            deferred.append((target, child[0], child[1],
                             match.start()))
        child = block_end(match.end())
        if child is not None:
            claimed.append(child)
    for target, open_pos, close_pos, at in deferred:
        if not any(e["key"] == "%s::%s" % (path, target)
                   for e in entities):
            diags.append(_diag(path, "unresolved_extend",
                                _line_of(starts, at), target))
            continue
        walk_fields(target, open_pos, close_pos)
    for match in _PROTO_SERVICE.finditer(text):
        service = emit_type("", match)
        if service is None:
            continue
        rest = text[match.end():]
        gap = len(rest) - len(rest.lstrip())
        if match.end() + gap >= len(text) \
                or text[match.end() + gap] != "{":
            continue
        open_pos = match.end() + gap
        close_pos = _match_block(text, open_pos)
        if close_pos == -1:
            continue
        for rpc in _PROTO_RPC.finditer(text, open_pos + 1,
                                       close_pos - 1):
            method = "%s.%s" % (service, rpc.group("name"))
            entity(method, rpc.start("name"), rpc.end("name"))
            for group in ("inp", "out"):
                base = _proto_last(rpc.group(group))
                if base in _PROTO_SCALARS:
                    continue
                reference("%s::%s" % (path, method), base,
                          rpc.start(group), rpc.end(group),
                          "proto_rpc_v1")
    return entities, diags, refs


def _schema_index(symbols):
    """Schema entity keys by (lowered, exact) leaf for ref lookup."""
    lowered, exact = {}, {}
    for key in symbols:
        path = key.split("::")[0]
        if not is_schema_path(path):
            continue
        leaf = key.split("::", 1)[-1]
        lowered.setdefault(leaf.lower(), []).append(key)
        exact.setdefault(leaf, []).append(key)
    return lowered, exact


def _schema_target_key(index, kind, table, column=None):
    """Unique schema entity key for a table[/column], else None.

    Unique-or-diagnostic at the call site: zero or multiple matches
    abstain. Matching is case-insensitive for SQL (unquoted folding),
    exact for GraphQL/Protobuf.
    """
    wanted = "%s.%s" % (table, column) if column else table
    lowered, exact = index
    if kind == "sql":
        matches = lowered.get(wanted.lower(), [])
    else:
        matches = exact.get(wanted, [])
    if len(matches) == 1:
        return matches[0]
    return None


def affected_paths(touched, fresh):
    """Touched schemas plus schemas referencing touched definitions."""
    out = {p for p in touched if is_schema_path(p)}
    touched_defs = set()
    for path in touched:
        info = fresh.get(path)
        if info is None or not is_schema_path(path):
            continue
        touched_defs |= {name.lower() for name in defined_names(
            info["data"], filetypes.suffix_of(path))}
    if not touched_defs:
        return out
    for path, info in fresh.items():
        if not is_schema_path(path) or path in out:
            continue
        refs = referenced_names(info["data"], filetypes.suffix_of(path))
        if {name.lower() for name in refs} & touched_defs:
            out.add(path)
    return out


def resolve_schema_refs(sources, symbols, assertions, diagnostics,
                        fetch=None, known_paths=None):
    """Parse schema sources into entities plus references claims."""
    from .extract import check_relation_signature
    records = [s for s in sources
               if is_schema_path(s["path"]) and s.get("data")]
    if not records:
        return
    pending = []
    for record in sorted(records, key=lambda s: s["path"]):
        path = record["path"]
        suffix = filetypes.suffix_of(path)
        parser = {"sql": _parse_sql, "graphql": _parse_graphql,
                  "gql": _parse_graphql,
                  "proto": _parse_proto}.get(suffix.lstrip("."))
        if parser is None:
            continue
        try:
            entities, file_diags, refs = parser(record["data"], path,
                                                record["id"])
        except (UnicodeError, ValueError) as error:
            file_diags, entities, refs = (
                [_diag(path, "parse_error", 1, str(error))], [], [])
        diagnostics.extend(file_diags)
        for entry in entities:
            if entry["key"] not in symbols:
                symbols[entry["key"]] = entry
        for caller, callee, line, start, end, rule in refs:
            pending.append((path, record["id"], caller, callee, line,
                            start, end, rule))
    index = _schema_index(symbols)
    for (path, source_id, caller, callee, line, start, end,
            rule) in pending:
        if caller not in symbols:
            diagnostics.append({"path": path, "line": line,
                                "start": start, "end": end,
                                "kind": "dangling_caller",
                                "expression": caller})
            continue
        kind = "sql" if filetypes.suffix_of(path) == ".sql" \
            else "other"
        if "." in callee:
            table, _, column = callee.partition(".")
            target = _schema_target_key(index, kind, table, column)
        else:
            target = _schema_target_key(index, kind, callee)
        if target is None:
            diagnostics.append({"path": path, "line": line,
                                "start": start, "end": end,
                                "kind": "unresolved_ref",
                                "expression": callee})
            continue
        if not check_relation_signature("references", caller, target):
            diagnostics.append({"path": path, "line": line,
                                "start": start, "end": end,
                                "kind": "relation_signature",
                                "expression": "%s references %s"
                                % (caller, target)})
            continue
        proposition = {"relation": "references", "caller": caller,
                       "callee": target}
        assertion = {"proposition": proposition, "source": source_id,
                     "line": line, "start": start, "end": end,
                     "resolution": {
                         "expression": "%s -> %s" % (caller, target),
                         "rule": rule,
                         "support": [{"source": source_id,
                                      "start": start, "end": end}]},
                     "producer": PRODUCER,
                     "status": "syntactic_observation"}
        assertion["id"] = identity("assertion", assertion)
        assertions.append(assertion)


_DEF_SQL = re.compile(r"\bCREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+"
                      r"(?:IF\s+NOT\s+EXISTS\s+)?"
                      r"(?P<name>(?:\"(?:[^\"]|\"\")+\"|[\w$]+)"
                      r"(?:\s*\.\s*(?:\"(?:[^\"]|\"\")+\"|[\w$]+))*)",
                      re.IGNORECASE)
_REF_SQL = re.compile(r"\bREFERENCES\s+(?P<name>(?:\"(?:[^\"]|\"\")+\""
                      r"|[\w$]+)(?:\s*\.\s*(?:\"(?:[^\"]|\"\")+\""
                      r"|[\w$]+))*)", re.IGNORECASE)
_DEF_GQL = re.compile(r"\b(?:type|interface|input|enum|scalar)\s+"
                      r"(?P<name>[A-Za-z_]\w*)")
_REF_GQL = re.compile(r":\s*\[?(?P<name>[A-Za-z_]\w*)|"
                      r"\bimplements\s+(?P<impl>[A-Za-z_][\w\s&]*)|"
                      r"\bunion\s+\w+\s*=\s*(?P<body>[A-Za-z_][\w\s|]*)")
_DEF_PROTO = re.compile(r"\b(?:message|enum)\s+(?P<name>[A-Za-z_]\w*)")
_REF_PROTO = re.compile(r"(?P<type>\.?[A-Za-z_][\w.]*)\s+[A-Za-z_]\w*\s*=\s*\d+|"
                         r"\brpc\s+\w+\s*\(\s*(?:stream\s+)?(?P<inp>\.?[A-Za-z_][\w.]*)|"
                         r"returns\s*\(\s*(?:stream\s+)?(?P<out>\.?[A-Za-z_][\w.]*)")


def defined_names(data, suffix):
    """Type/table names defined in a schema file (affected-path scan)."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return set()
    if suffix == ".sql":
        return {_last_part(m.group("name")).lower()
                for m in _DEF_SQL.finditer(text)}
    if suffix in (".graphql", ".gql"):
        return {m.group("name") for m in _DEF_GQL.finditer(text)}
    if suffix == ".proto":
        return {m.group("name") for m in _DEF_PROTO.finditer(text)}
    return set()


def referenced_names(data, suffix):
    """Type/table names referenced in a schema file (affected-path scan)."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return set()
    if suffix == ".sql":
        return {_last_part(m.group("name")).lower()
                for m in _REF_SQL.finditer(text)}
    if suffix in (".graphql", ".gql"):
        out = set()
        for m in _REF_GQL.finditer(text):
            if m.group("name"):
                out.add(m.group("name"))
            if m.group("impl"):
                out.update(re.split(r"[&\s]+", m.group("impl").strip()))
            if m.group("body"):
                out.update(re.split(r"[\s|]+", m.group("body").strip()))
        return {name for name in out if name}
    if suffix == ".proto":
        out = set()
        for m in _REF_PROTO.finditer(text):
            for group in ("type", "inp", "out"):
                if m.group(group):
                    out.add(m.group(group).lstrip(".").split(".")[-1])
        return out - _PROTO_SCALARS
    return set()
