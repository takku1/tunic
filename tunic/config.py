"""Structured config as key-path entities (Tier-3).

JSON/YAML/TOML/INI parse into path-keyed entities
(`config.yaml::server.port`) that are retrievable, mentionable, and
citable with byte-exact key spans -- but never call edges. Cross-file
$ref/$include keys (pointer form) and YAML anchors become depends_on
claims between key entities (same relation, new config endpoint
kind). Whole-file includes (no # pointer) abstain with
unsupported_include: depending on a file is not depending on a key.
Lockfiles stay out (Tier-5): they are dependency manifests, not
configuration. Values ride on symbols as scalars only; tables and
arrays cite by span, never by dumped structure.
"""

import bisect
import json
import re
import tomllib
from pathlib import PurePosixPath

from . import filetypes
from .identity import identity

try:
    import yaml
except ImportError:
    yaml = None

CONFIG_EXTENSIONS = filetypes.CONFIG_EXTENSIONS
PRODUCER = "key_path_v1"
REF_KEYS = {"$ref", "$include", "include"}
LOCKFILE_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock",
                  "Cargo.lock", "poetry.lock", "Pipfile.lock",
                  "Gemfile.lock", "composer.lock", "packages.lock.json",
                  "flake.lock"}
MAX_KEYS_PER_FILE = 5000


def is_config_path(path):
    return filetypes.suffix_of(path) in CONFIG_EXTENSIONS


def _escape_segment(segment):
    return segment.replace("\\", "\\\\").replace(".", "\\.")


def _render_path(segments):
    """Dotted key path with [i] array indices, dots escaped in keys."""
    out = ""
    for segment in segments:
        if isinstance(segment, int):
            out += "[%d]" % segment
        else:
            if out:
                out += "."
            out += _escape_segment(segment)
    return out


class _Text:
    """Decoded text with char offsets, byte offsets, and line numbers."""

    def __init__(self, data):
        try:
            self.text = data.decode("utf-8")
        except UnicodeError as error:
            raise _ConfigAbort("decode_error", 1, "config_decode",
                               str(error))
        self.starts = [0]
        for match in re.finditer("\n", self.text):
            self.starts.append(match.end())
        self.byte_of = [0] * (len(self.text) + 1)
        for pos, char in enumerate(self.text):
            self.byte_of[pos + 1] = self.byte_of[pos] + len(
                char.encode("utf-8"))

    def span(self, cstart, cend):
        return (self.byte_of[cstart], self.byte_of[cend])

    def line_of(self, cpos):
        return bisect.bisect_right(self.starts, cpos)


class _ConfigAbort(Exception):
    def __init__(self, kind, line, expression, error=None):
        super().__init__(expression)
        self.kind = kind
        self.line = line
        self.expression = expression
        self.error = error


def _entity(path, source_id, dotted, line, start, end, value):
    return {"key": "%s::%s" % (path, dotted), "path": path,
            "name": dotted, "source": source_id, "line": line,
            "start": start, "end": end, "is_test": False,
            "producer": PRODUCER,
            "value": value if _is_small_scalar(value) else None}


def _is_small_scalar(value):
    if isinstance(value, str):
        return len(value) <= 500
    return isinstance(value, (int, float, bool)) or value is None


def _diag(path, kind, line, expression, error=None):
    diag = {"path": path, "line": line, "start": 0, "end": 0,
            "kind": kind, "expression": expression}
    if error is not None:
        diag["error"] = error
    return diag


_WS = " \t\n\r"
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_LITERALS = {"true": True, "false": False, "null": None}


class _JsonWalker:
    """Location-tracking JSON walk: byte-exact key spans, first-wins."""

    def __init__(self, text):
        self.t = text
        self.s = text.text
        self.n = len(self.s)
        self.pos = 0
        self.entities = []
        self.refs = []
        self.diags = []

    def _ws(self):
        while self.pos < self.n and self.s[self.pos] in _WS:
            self.pos += 1

    def _abort(self, expression):
        raise _ConfigAbort("config_internal", self.t.line_of(self.pos),
                           expression)

    def _parse_string(self):
        start = self.pos
        pos = self.pos + 1
        while True:
            if pos >= self.n:
                self._abort("unterminated_string")
            char = self.s[pos]
            if char == '"':
                raw = self.s[start:pos + 1]
                try:
                    value = json.loads(raw)
                except ValueError:
                    self._abort("bad_string_escape")
                self.pos = pos + 1
                return value, start, pos + 1
            if char == "\\":
                pos += 2
                continue
            pos += 1

    def _parse_value(self, path, source_id, segs):
        self._ws()
        if self.pos >= self.n:
            self._abort("truncated_value")
        char = self.s[self.pos]
        if char == '"':
            return self._parse_string() + (False,)
        if char == "{":
            return self._parse_object(path, source_id, segs)
        if char == "[":
            return self._parse_array(path, source_id, segs)
        match = _NUMBER.match(self.s, self.pos)
        if match:
            token = match.group(0)
            self.pos = match.end()
            try:
                value = int(token)
            except ValueError:
                value = float(token)
            return value, match.start(), match.end(), False
        for literal, value in _LITERALS.items():
            if self.s.startswith(literal, self.pos):
                self.pos += len(literal)
                return value, self.pos - len(literal), self.pos, False
        self._abort("bad_value")

    def _emit(self, path, source_id, segs, cstart, cend, value,
              is_container):
        dotted = _render_path(segs)
        start, end = self.t.span(cstart, cend)
        line = self.t.line_of(cstart)
        self.entities.append(_entity(path, source_id, dotted, line,
                                     start, end,
                                     None if is_container else value))
        if segs and isinstance(segs[-1], str) and segs[-1] in REF_KEYS \
                and isinstance(value, str):
            self.refs.append((dotted, line, start, end, value,
                              "key_ref_v1"))

    def _parse_object(self, path, source_id, segs):
        vstart = self.pos
        self.pos += 1
        seen = set()
        first = True
        while True:
            self._ws()
            if self.pos < self.n and self.s[self.pos] == "}":
                self.pos += 1
                return None, vstart, self.pos, True
            if not first:
                if self.pos >= self.n or self.s[self.pos] != ",":
                    self._abort("bad_object_separator")
                self.pos += 1
                self._ws()
            first = False
            if self.pos >= self.n or self.s[self.pos] != '"':
                self._abort("bad_object_key")
            key, ks, ke = self._parse_string()
            self._ws()
            if self.pos >= self.n or self.s[self.pos] != ":":
                self._abort("bad_object_colon")
            self.pos += 1
            value, _vs, _ve, container = self._parse_value(
                path, source_id, segs + (key,))
            if key in seen:
                self.diags.append(_diag(path, "duplicate_key",
                                        self.t.line_of(ks), key))
            else:
                seen.add(key)
                self._emit(path, source_id, segs + (key,), ks, ke,
                           value, container)

    def _parse_array(self, path, source_id, segs):
        vstart = self.pos
        self.pos += 1
        index, first = 0, True
        while True:
            self._ws()
            if self.pos < self.n and self.s[self.pos] == "]":
                self.pos += 1
                return None, vstart, self.pos, True
            if not first:
                if self.pos >= self.n or self.s[self.pos] != ",":
                    self._abort("bad_array_separator")
                self.pos += 1
            first = False
            value, vs, ve, container = self._parse_value(
                path, source_id, segs + (index,))
            self._emit(path, source_id, segs + (index,), vs, ve,
                       value, container)
            index += 1


def _parse_json(data, path, source_id):
    """Validate with stdlib, then walk for spans. Never invents keys."""
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        return [], [_diag(path, "decode_error", 1, "config_decode",
                           str(error))], []
    try:
        document = json.loads(text)
    except ValueError as error:
        lineno = getattr(error, "lineno", None) or 1
        return [], [_diag(path, "parse_error", lineno, "config_json",
                           str(error))], []
    t = _Text(data)
    walker = _JsonWalker(t)
    try:
        walker._ws()
        if walker.pos < walker.n and walker.s[walker.pos] == "{":
            walker._parse_object(path, source_id, ())
        elif walker.pos < walker.n and walker.s[walker.pos] == "[":
            walker._parse_array(path, source_id, ())
        else:
            return [], [_diag(path, "config_scalar_root", 1,
                               "config_scalar")], []
        walker._ws()
    except _ConfigAbort as abort:
        walker.diags.append(_diag(path, abort.kind, abort.line,
                                  abort.expression, abort.error))
    return walker.entities, walker.diags, walker.refs


def _parse_yaml(data, path, source_id):
    """Compose for spans, safe-load for scalar values, aligned by doc."""
    if yaml is None:
        return [], [_diag(path, "unsupported_syntax", 1,
                           "yaml_parser_missing")], []
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        return [], [_diag(path, "decode_error", 1, "config_decode",
                           str(error))], []
    t = _Text(data)
    try:
        docs = list(yaml.compose_all(text))
    except yaml.YAMLError as error:
        return [], [_diag(path, "parse_error", 1, "config_yaml",
                           str(error))], []
    try:
        values = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        values = [None] * len(docs)
    entities, diags, refs = [], [], []
    seen, anchors = {}, {}

    def mark_span(node):
        return t.span(node.start_mark.index, node.end_mark.index)

    def line_of(node):
        return node.start_mark.line + 1

    def emit(segs, node, scalar):
        dotted = _render_path(segs)
        if dotted in seen:
            diags.append(_diag(path, "duplicate_key", line_of(node),
                                dotted))
            return None
        seen[dotted] = True
        start, end = mark_span(node)
        entities.append(_entity(path, source_id, dotted,
                                line_of(node), start, end, scalar))
        if segs and isinstance(segs[-1], str) \
                and segs[-1] in REF_KEYS and isinstance(scalar, str):
            refs.append((dotted, line_of(node), start, end, scalar,
                         "key_ref_v1"))
        return dotted

    def child_value(value, key):
        if isinstance(value, dict):
            return value.get(key)
        if isinstance(value, list) and isinstance(key, int) \
                and 0 <= key < len(value):
            return value[key]
        return None

    def alias_edge(dotted, node, target):
        start, end = mark_span(node)
        refs.append((dotted, line_of(node), start, end,
                     "#" + target, "yaml_anchor_v1"))

    def walk(node, value, segs, parent_dotted):
        if isinstance(node, yaml.MappingNode):
            for key_node, val_node in node.value:
                if not isinstance(key_node, yaml.ScalarNode):
                    diags.append(_diag(
                        path, "unsupported_key", line_of(key_node),
                        "complex_mapping_key"))
                    continue
                key = key_node.value
                child = segs + (key,)
                subtree = child_value(value, key)
                scalar = subtree if _is_small_scalar(subtree) else None
                if key == "<<" and not isinstance(val_node,
                                                  yaml.ScalarNode):
                    target = anchors.get(id(val_node))
                    if parent_dotted is not None and target is not None:
                        start, end = mark_span(key_node)
                        refs.append((parent_dotted, line_of(key_node),
                                     start, end, "#" + target,
                                     "yaml_merge_v1"))
                    else:
                        diags.append(_diag(path, "unresolved_ref",
                                            line_of(key_node), "<<"))
                    continue
                if id(val_node) in anchors:
                    dotted = emit(child, key_node, scalar)
                    if dotted is not None:
                        alias_edge(dotted, key_node,
                                   anchors[id(val_node)])
                    continue
                anchors[id(val_node)] = _render_path(child)
                dotted = emit(child, key_node, scalar)
                if dotted is not None:
                    walk(val_node, subtree, child, dotted)
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                child = segs + (index,)
                subtree = child_value(value, index)
                scalar = subtree if _is_small_scalar(subtree) else None
                if id(item) in anchors:
                    dotted = emit(child, item, scalar)
                    if dotted is not None:
                        alias_edge(dotted, item, anchors[id(item)])
                    continue
                anchors[id(item)] = _render_path(child)
                dotted = emit(child, item, scalar)
                if dotted is not None:
                    walk(item, subtree, child, dotted)

    for pos, doc in enumerate(docs):
        if doc is None:
            continue
        root_value = values[pos] if pos < len(values) else None
        if isinstance(doc, yaml.MappingNode):
            walk(doc, root_value if isinstance(root_value, dict)
                 else None, (), None)
        elif isinstance(doc, yaml.SequenceNode):
            walk(doc, root_value if isinstance(root_value, list)
                 else None, (), None)
        else:
            diags.append(_diag(path, "config_scalar_root",
                               line_of(doc), "config_scalar"))
    return entities, diags, refs


_TOML_HEADER = re.compile(r"^\s*(\[{1,2})\s*(.+?)\s*(\]{1,2})\s*(?:#.*)?$")


def _split_toml_key(expression):
    """Split a dotted TOML key on dots outside quotes."""
    segments, buf, quote, escape = [], [], None, False
    for char in expression:
        if escape:
            buf.append(char)
            escape = False
        elif quote is not None:
            if char == "\\" and quote == '"':
                escape = True
                buf.append(char)
            elif char == quote:
                quote = None
                buf.append(char)
            else:
                buf.append(char)
        elif char in "\"'":
            quote = char
            buf.append(char)
        elif char == ".":
            segments.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    segments.append("".join(buf))
    out = []
    for segment in segments:
        segment = segment.strip()
        if len(segment) >= 2 and segment[0] == segment[-1] \
                and segment[0] in "\"'":
            segment = segment[1:-1]
        out.append(segment)
    return out


def _split_key_value(line):
    """Split a TOML/INI line on the first = or : outside quotes.

    Returns (key_text, split_pos): key_text is the stripped key
    expression, split_pos the index of the separator. (None, None)
    when no top-level separator exists.
    """
    quote, escape = None, False
    for pos, char in enumerate(line):
        if escape:
            escape = False
        elif quote is not None:
            if char == "\\" and quote == '"':
                escape = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "=: ":
            if char in "=:":
                return line[:pos].strip(), pos
    return None, None


def _toml_raw(parsed, segs):
    current = parsed
    try:
        for segment in segs:
            current = current[segment]
    except (KeyError, IndexError, TypeError):
        return None
    return current


def _toml_navigate(parsed, segs):
    current = _toml_raw(parsed, segs)
    return current if _is_small_scalar(current) else None


def _scalar_end(text, pos):
    """End of a scalar token: first ,}] outside quotes."""
    quote, escape, cur = None, False, pos
    while cur < len(text):
        char = text[cur]
        if escape:
            escape = False
        elif quote is not None:
            if char == "\\" and quote == '"':
                escape = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in ",}]":
            return cur
        cur += 1
    return cur


def _balanced_brackets(segment):
    """True when every opener on the segment closes on it."""
    depth, quote, escape = 0, None, False
    for char in segment:
        if escape:
            escape = False
        elif quote is not None:
            if char == "\\" and quote == '"':
                escape = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _read_inline_key(text, pos):
    """Key token at pos: (decoded_key, key_start, key_end)."""
    if text[pos] in "\"'":
        quote = text[pos]
        cur = pos + 1
        while cur < len(text):
            if text[cur] == "\\" and quote == '"':
                cur += 2
                continue
            if text[cur] == quote:
                raw = text[pos:cur + 1]
                try:
                    value = json.loads(raw) if quote == '"' \
                        else raw[1:-1]
                except ValueError:
                    value = raw[1:-1]
                return value, pos, cur + 1
            cur += 1
        return text[pos + 1:], pos, len(text)
    match = re.match(r"[^=,\}\]]+", text[pos:])
    if not match:
        return None, pos, pos
    raw = match.group(0)
    key = raw.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        inner = key[1:-1]
        key = inner
    ks = pos + (len(raw) - len(raw.lstrip()))
    ke = pos + len(raw.rstrip())
    return key, ks, ke


def _walk_inline(text, pos, base, full, emit, navigate):
    """Walk an inline TOML table: keys extend the path, values resolve
    through the parsed document. Returns the end position."""
    while pos < len(text):
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text) or text[pos] in "}]":
            return pos + 1 if pos < len(text) else pos
        if text[pos] == ",":
            pos += 1
            continue
        if text[pos] in "{[":
            return pos
        key, ks, ke = _read_inline_key(text, pos)
        if key is None:
            return pos
        pos = ke
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text) or text[pos] != "=":
            return pos
        pos += 1
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        child = full + (key,)
        if pos < len(text) and text[pos] == "{":
            emit(child, base + ks, base + ke, None)
            pos = _walk_inline(text, pos + 1, base, child, emit,
                               navigate)
            continue
        if pos < len(text) and text[pos] == "[":
            emit(child, base + ks, base + ke, None)
            pos = _walk_inline_array(text, pos + 1, base, child,
                                     emit, navigate)
            continue
        end = _scalar_end(text, pos)
        emit(child, base + ks, base + ke, navigate(child))
        pos = end
    return pos


def _walk_inline_array(text, pos, base, full, emit, navigate):
    """Walk an inline TOML array: indices extend the path."""
    index = 0
    while pos < len(text):
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text):
            return pos
        if text[pos] == "]":
            return pos + 1
        if text[pos] == ",":
            pos += 1
            continue
        child = full + (index,)
        if text[pos] == "{":
            estart = pos
            pos = _walk_inline(text, pos + 1, base, child, emit,
                               navigate)
            emit(child, base + estart, base + pos, None)
            index += 1
            continue
        if text[pos] == "[":
            estart = pos
            pos = _walk_inline_array(text, pos + 1, base, child,
                                     emit, navigate)
            emit(child, base + estart, base + pos, None)
            index += 1
            continue
        end = _scalar_end(text, pos)
        emit(child, base + pos, base + end, navigate(child))
        index += 1
        pos = end
    return pos


def _parse_toml(data, path, source_id):
    """tomllib for values, a line walk for byte-exact key spans."""
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        return [], [_diag(path, "decode_error", 1, "config_decode",
                           str(error))], []
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        return [], [_diag(path, "parse_error", 1, "config_toml",
                           str(error))], []
    t = _Text(data)
    entities, diags, refs = [], [], []
    seen = {}

    def navigate(full):
        return _toml_navigate(parsed, full)

    def emit(full, cstart, cend, value):
        dotted = _render_path(full)
        if dotted in seen:
            diags.append(_diag(path, "duplicate_key",
                                t.line_of(cstart), dotted))
            return
        seen[dotted] = True
        start, end = t.span(cstart, cend)
        line = t.line_of(cstart)
        entities.append(_entity(path, source_id, dotted, line, start,
                                end, value))
        leaf = full[-1] if full else None
        if isinstance(leaf, str) and leaf in REF_KEYS \
                and isinstance(value, str):
            refs.append((dotted, line, start, end, value,
                         "key_ref_v1"))

    char_offset = 0
    ctx, array_counts = (), {}
    for lineno, raw_line in enumerate(text.split("\n"), 1):
        line_start = char_offset
        char_offset += len(raw_line) + 1
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        header = _TOML_HEADER.match(raw_line)
        if header:
            is_array = header.group(1) == "[["
            try:
                segs = tuple(_split_toml_key(header.group(2)))
            except (ValueError, IndexError):
                diags.append(_diag(path, "unsupported_syntax", lineno,
                                    raw_line.strip()))
                continue
            resolved = []
            if is_array:
                for seg in segs[:-1]:
                    probe = tuple(resolved + [seg])
                    if probe in array_counts:
                        resolved += [seg, array_counts[probe] - 1]
                    else:
                        resolved.append(seg)
                count_key = tuple(resolved + [segs[-1]])
                count = array_counts.get(count_key, 0)
                array_counts[count_key] = count + 1
                ctx = tuple(resolved + [segs[-1], count])
            else:
                for seg in segs:
                    probe = tuple(resolved + [seg])
                    if probe in array_counts:
                        resolved += [seg, array_counts[probe] - 1]
                    else:
                        resolved.append(seg)
                ctx = tuple(resolved)
            continue
        key_text, split_pos = _split_key_value(raw_line)
        if key_text is None:
            diags.append(_diag(path, "unsupported_syntax", lineno,
                                stripped))
            continue
        lead = len(raw_line) - len(raw_line.lstrip())
        key_start = lead
        try:
            key_segs = _split_toml_key(key_text)
        except (ValueError, IndexError):
            diags.append(_diag(path, "unsupported_syntax", lineno,
                                stripped))
            continue
        full = ctx + tuple(key_segs)
        key_end = key_start + len(key_text)
        rest = raw_line[split_pos + 1:]
        opener = rest.strip()[:1]
        if opener in "{[" and not _balanced_brackets(rest):
            emit(full, line_start + key_start, line_start + key_end,
                 None)
            diags.append(_diag(path, "unsupported_multiline", lineno,
                                _render_path(full)))
            continue
        if opener == "{":
            emit(full, line_start + key_start, line_start + key_end,
                 None)
            brace = raw_line.find("{", key_start)
            _walk_inline(raw_line, brace + 1, line_start, full,
                         emit, navigate)
        elif opener == "[":
            raw_value = _toml_raw(parsed, full)
            if raw_value is not None and not _is_small_scalar(
                    raw_value):
                emit(full, line_start + key_start,
                     line_start + key_end, None)
                bracket = raw_line.find("[", key_start)
                _walk_inline_array(raw_line, bracket + 1, line_start,
                                   full, emit, navigate)
            else:
                emit(full, line_start + key_start,
                     line_start + key_end, raw_value)
        else:
            emit(full, line_start + key_start, line_start + key_end,
                 navigate(full))
    return entities, diags, refs


def _parse_ini(data, path, source_id):
    """Line-oriented INI walk: sections scope keys, continuation lines
    extend values. Keys before any header land in DEFAULT."""
    try:
        text = data.decode("utf-8")
    except UnicodeError as error:
        return [], [_diag(path, "decode_error", 1, "config_decode",
                           str(error))], []
    t = _Text(data)
    entities, diags, refs = [], [], []
    seen = {}

    def emit(segs, cstart, cend, value):
        dotted = _render_path(segs)
        if dotted in seen:
            diags.append(_diag(path, "duplicate_key",
                                t.line_of(cstart), dotted))
            return None
        seen[dotted] = True
        start, end = t.span(cstart, cend)
        line = t.line_of(cstart)
        entities.append(_entity(path, source_id, dotted, line, start,
                                end, value))
        leaf = segs[-1] if segs else None
        if leaf in REF_KEYS and isinstance(value, str):
            refs.append((dotted, line, start, end, value,
                         "key_ref_v1"))
        return len(entities) - 1

    section, pending = "DEFAULT", None
    char_offset = 0
    for lineno, raw_line in enumerate(text.split("\n"), 1):
        line_start = char_offset
        char_offset += len(raw_line) + 1
        stripped = raw_line.strip()
        if not stripped or stripped[0] in "#;":
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            if not name:
                diags.append(_diag(path, "unsupported_syntax", lineno,
                                    stripped))
                continue
            section = name
            pending = None
            continue
        if raw_line[0] in " \t" and pending is not None:
            key, value, index = pending
            grown = value + "\n" + stripped
            pending = (key, grown, index)
            entities[index]["value"] = grown \
                if _is_small_scalar(grown) else None
            continue
        pending = None
        key_text, split_pos = _split_key_value(raw_line)
        if key_text is None:
            diags.append(_diag(path, "unsupported_syntax", lineno,
                                stripped))
            continue
        lead = len(raw_line) - len(raw_line.lstrip())
        value = raw_line[split_pos + 1:].strip()
        index = emit((section, key_text), line_start + lead,
                     line_start + lead + len(key_text), value)
        pending = (key_text, value, index) if index is not None \
            else None
    return entities, diags, refs


def _collapse_dots(parts):
    out = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part)
    return out


def _sibling_path(current, target):
    """Resolve a ref file part against the current file's directory."""
    if not target:
        return current
    if target.startswith("/"):
        return target.lstrip("/")
    parent = current.rpartition("/")[0]
    joined = (parent + "/" + target) if parent else target
    return "/".join(_collapse_dots(joined.split("/")))


def _resolve_ref_target(ref, current, symbols):
    """Map a $ref/$include string to a symbol key or a failure reason."""
    if "://" in ref:
        return None, "external_ref"
    if "#" in ref:
        file_part, _, pointer = ref.partition("#")
    else:
        return None, "whole_file"
    target_path = _sibling_path(current, file_part)
    segments = [seg.replace("~1", "/").replace("~0", "~")
                for seg in pointer.strip("/").split("/")
                if seg != ""]
    if not segments:
        return None, "root_pointer"
    target_key = "%s::%s" % (target_path,
                             _render_path(tuple(segments)))
    if target_key not in symbols:
        return None, "missing_target"
    return target_key, None


def affected_paths(touched, fresh):
    """Touched configs plus configs referencing touched files.

    Ref file-parts resolve against the referencing file's directory;
    a basename match re-resolves the config (idempotent), never
    inventing edges.
    """
    touched = set(touched)
    out = {p for p in touched if is_config_path(p)}
    for path, info in fresh.items():
        if not is_config_path(path) or path in out:
            continue
        try:
            targets = referenced_files(path, info["data"])
        except (ValueError, UnicodeError, KeyError):
            continue
        if targets & touched:
            out.add(path)
    return out


def resolve_config_keys(sources, symbols, assertions, diagnostics,
                        fetch=None, known_paths=None):
    """Parse config sources into key-path entities plus ref claims."""
    records = [s for s in sources
               if is_config_path(s["path"]) and s.get("data")]
    if not records:
        return
    pending = []
    for record in sorted(records, key=lambda s: s["path"]):
        path = record["path"]
        if PurePosixPath(path).name in LOCKFILE_NAMES:
            diagnostics.append(_diag(path, "inventory_deferred", 1,
                                     path))
            continue
        suffix = filetypes.suffix_of(path)
        parser = {"json": _parse_json, "yaml": _parse_yaml,
                  "yml": _parse_yaml, "toml": _parse_toml,
                  "ini": _parse_ini}.get(suffix.lstrip("."))
        if parser is None:
            continue
        try:
            entities, file_diags, refs = parser(record["data"], path,
                                                record["id"])
        except _ConfigAbort as abort:
            file_diags, entities, refs = (
                [_diag(path, abort.kind, abort.line, abort.expression,
                        abort.error)], [], [])
        diagnostics.extend(file_diags)
        if len(entities) > MAX_KEYS_PER_FILE:
            diagnostics.append(_diag(path, "config_truncated", 1,
                                     "%d keys" % len(entities)))
            entities = entities[:MAX_KEYS_PER_FILE]
        for entry in entities:
            if entry["key"] not in symbols:
                symbols[entry["key"]] = entry
        for dotted, line, start, end, target, rule in refs:
            pending.append((path, record["id"], dotted, line, start,
                            end, target, rule))
    from .extract import check_relation_signature
    for (path, source_id, dotted, line, start, end, target,
            rule) in pending:
        caller = "%s::%s" % (path, dotted)
        if caller not in symbols:
            continue
        if rule == "key_ref_v1":
            callee, failure = _resolve_ref_target(target, path,
                                                  symbols)
        else:
            pointer = target[1:] if target.startswith("#") else target
            segments = [seg.replace("~1", "/").replace("~0", "~")
                        for seg in pointer.strip("/").split("/")
                        if seg != ""]
            if not segments:
                callee, failure = None, "root_pointer"
            else:
                anchor = "%s::%s" % (path, _render_path(
                    tuple(segments)))
                callee = anchor if anchor in symbols else None
                failure = None if callee else "missing_target"
        if callee is None:
            kind = "unsupported_include" if failure == "whole_file" \
                else "unresolved_ref"
            diagnostics.append({"path": path, "line": line,
                                "start": start, "end": end,
                                "kind": kind, "expression": target})
            continue
        if not check_relation_signature("depends_on", caller, callee):
            diagnostics.append({"path": path, "line": line,
                                "start": start, "end": end,
                                "kind": "relation_signature",
                                "expression": "%s depends_on %s"
                                % (caller, callee)})
            continue
        proposition = {"relation": "depends_on", "caller": caller,
                       "callee": callee}
        assertion = {"proposition": proposition, "source": source_id,
                     "line": line, "start": start, "end": end,
                     "resolution": {
                         "expression": "%s -> %s" % (caller, callee),
                         "rule": rule,
                         "support": [{"source": source_id,
                                      "start": start, "end": end}]},
                     "producer": PRODUCER,
                     "status": "syntactic_observation"}
        assertion["id"] = identity("assertion", assertion)
        assertions.append(assertion)


_REF_SCAN = re.compile(r"""(?:\$ref|\$?include)\s*['"]?\s*[:=]\s*['"]([^'"]+)['"]""")


def referenced_files(path, data):
    """Ref file-parts inside a config file, normalized repo-relative."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        return set()
    out = set()
    for match in _REF_SCAN.finditer(text):
        ref = match.group(1)
        if "://" in ref:
            continue
        file_part = ref.partition("#")[0]
        out.add(_sibling_path(path, file_part))
    return out
