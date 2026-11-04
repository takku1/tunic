"""Symbols for any language with a grammar, from no per-language data.

Tree-sitter grammars name their nodes by convention: a definition is a
node whose type says what it defines (`function_definition`,
`class_declaration`, `struct_item`, `method`) with a `name` field or a
leading identifier. One walk over that convention yields names and
byte-exact spans for every grammar, including the many that ship no
tags query.

Where a grammar ships a `queries/tags.scm` it is preferred: it is written
by the grammar's authors and sees definitions no convention can, such as
elixir's `def`, which is a macro call.

The result is the symbols rung: parser-backed names, never edges. A
call between two of these symbols stays unproven until a language has
a spec in unigraph.py, because an edge needs binding rules and a name
does not.

Grammars come from installed `tree_sitter_<language>` wheels, then from
languages already cached by tree-sitter-language-pack. Ingest never
downloads: fetching a grammar is an explicit act (`coverage --fetch`).
"""

import importlib
import importlib.util
from pathlib import Path
import re

DERIVATION = "structural_walk_v1"
# Name-and-location receipts: neither implies a call edge.
INVENTORY_DERIVATIONS = frozenset({"heuristic_inventory_v1", DERIVATION})

_FUNCTION = ("function", "func", "fn", "method", "constructor", "subroutine",
             "procedure", "macro", "def", "defn")
_CLASS = ("class", "struct", "interface", "trait", "object", "protocol",
          "record", "mixin", "actor", "impl", "module", "namespace",
          "extension")
_TYPE = ("enum", "union", "type")
_WORD = re.compile(r"(?:^|_)(%s)(?:_|$)" % "|".join(_FUNCTION + _CLASS + _TYPE))
# A kind word alone is not a definition: `function_call`, `type_argument`,
# and `class_body` all carry one. The suffix is what makes it one. Weak
# suffixes also appear on references (powershell `type_spec` names a type
# it does not define), so those must carry an explicit name field.
_STRONG_SUFFIX = re.compile(r"(definition|declaration|item|statement|def|decl)$")
_WEAK_SUFFIX = re.compile(r"(declarator|specifier|spec)$")
# Shapes that use a name rather than introduce one.
_REFERENCE = re.compile(r"(?:^|_)(use|using|import|include|require|call|end|"
                        r"expression|argument|parameter|member|forward)(?:_|$)")
_STOP_DESCENT = re.compile(r"field|member|body|block|list|parameter|_type$")
_NAME_TYPES = ("identifier", "type_identifier", "constant", "name", "word",
               "simple_identifier", "field_identifier", "property_identifier")
_VALID_NAME = re.compile(r"[^\W\d][\w$!?-]*\Z")
_NAME_SEARCH_DEPTH = 3

_PARSERS = {}
_TAGS = {}
# Grammar wheels whose module name differs from the language id.
_WHEEL_ALIASES = {"csharp": "c_sharp", "tsx": "typescript"}
_TAG_KINDS = {"function": "function", "method": "function", "macro": "function",
              "class": "class", "interface": "class", "module": "class",
              "type": "type", "enum": "type"}


def _wheel_module(language):
    return "tree_sitter_%s" % _WHEEL_ALIASES.get(language, language)


def _wheel_language(language):
    try:
        grammar = importlib.import_module(_wheel_module(language))
    except ImportError:
        return None
    entry = getattr(grammar, "language", None) \
        or getattr(grammar, "language_" + language, None)
    return entry() if entry is not None else None


def _cached_pack_parser(language):
    try:
        import tree_sitter_language_pack as pack
        if language in pack.downloaded_languages():
            return pack.get_parser(language)
    except (ImportError, AttributeError, ValueError, RuntimeError):
        pass
    return None


def parser_for(language):
    """A parser for the language, or None when no grammar is local."""
    if not language:
        return None
    if language not in _PARSERS:
        parser = None
        try:
            import tree_sitter
            capsule = _wheel_language(language)
            if capsule is not None:
                parser = tree_sitter.Parser(tree_sitter.Language(capsule))
        except (ImportError, ValueError, TypeError):
            parser = None
        _PARSERS[language] = parser or _cached_pack_parser(language)
    return _PARSERS[language]


def _tags_query(language):
    """The grammar's own tags query, or None; compiled once per language."""
    if language not in _TAGS:
        _TAGS[language] = None
        parser = parser_for(language)
        try:
            import tree_sitter
            spec = importlib.util.find_spec(_wheel_module(language))
            path = Path(spec.origin).parent / "queries" / "tags.scm"
            if parser is not None and path.exists():
                _TAGS[language] = tree_sitter.Query(
                    parser.language, path.read_text(encoding="utf-8"))
        except (ImportError, AttributeError, OSError, ValueError):
            _TAGS[language] = None
        except Exception as error:  # QueryError: grammar/query version skew
            if type(error).__name__ != "QueryError":
                raise
            _TAGS[language] = None
    return _TAGS[language]


def _kind_of(node):
    """function/class/type when the node is shaped like a definition."""
    node_type = node.type
    match = _WORD.search(node_type)
    if match is None or _REFERENCE.search(node_type):
        return None
    word = match.group(1)
    explicit = (node.child_by_field_name("name") is not None
                or node.child_by_field_name("declarator") is not None)
    strong = _STRONG_SUFFIX.search(node_type) is not None
    # ruby spells a definition `method`; haskell spells a type `function`.
    weak = word == node_type or _WEAK_SUFFIX.search(node_type) is not None
    if not (strong or (weak and explicit)):
        return None
    if word in _FUNCTION:
        return "function"
    return "class" if word in _CLASS else "type"


def _leading_name(node, depth):
    """Identifier reached through first children only.

    Grammars nest the name differently (julia: signature > call >
    identifier; powershell: function_name), but it is always the first
    thing a definition says. Bodies and field lists are never entered:
    an anonymous `struct { x: i32 }` has no name, not the name `x`.
    """
    if not node.named_child_count:
        return None
    first = node.named_children[0]
    if first.type in _NAME_TYPES or first.type.endswith("_name"):
        return first
    if depth > 0 and not _STOP_DESCENT.search(first.type):
        return _leading_name(first, depth - 1)
    return None


def _name_node(node):
    named = node.child_by_field_name("name")
    if named is not None:
        return named
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        inner = declarator.child_by_field_name("declarator")
        if inner is None:
            return declarator
        declarator = inner
    return _leading_name(node, _NAME_SEARCH_DEPTH)


def _clean_name(node):
    """Leaf identifier text; qualifiers and parameter lists fall away."""
    text = node.text.decode("utf-8", "replace").split("(")[0].strip()
    leaf = re.split(r"::|\.|->|\\", text)[-1].strip()
    return leaf if _VALID_NAME.match(leaf) else None


def definitions(tree):
    """Yield (qualified_name, kind, node) in source order.

    Descends through containers so `Class.method` qualifies, and stops
    at function bodies: a local helper is not part of a file's surface.
    """
    stack = [(tree.root_node, "")]
    while stack:
        node, prefix = stack.pop()
        kind = _kind_of(node)
        name_node = _name_node(node) if kind else None
        name = _clean_name(name_node) if name_node is not None else None
        # `module m` opens with a `module_statement` naming itself again.
        if name and prefix != name + ".":
            qualified = prefix + name
            yield qualified, kind, node
            if kind == "function":
                continue
            prefix = qualified + "."
        stack.extend((child, prefix) for child in reversed(node.named_children))


def tagged_definitions(query, tree):
    """(name, kind, node) from a tags query, in source order."""
    import tree_sitter
    found = []
    cursor = tree_sitter.QueryCursor(query)
    for _pattern, captures in cursor.matches(tree.root_node):
        names = captures.get("name") or ()
        for capture, nodes in captures.items():
            if not names or not capture.startswith("definition."):
                continue
            kind = _TAG_KINDS.get(capture.split(".", 1)[1])
            name = _clean_name(names[0])
            if kind and name:
                found.extend((name, kind, node) for node in nodes)
    found.sort(key=lambda row: (row[2].start_byte, row[0]))
    return found


def harvest(source, language, symbols, diagnostics):
    """Add grammar-backed symbols for a source; False if no grammar."""
    parser = parser_for(language)
    if parser is None:
        return False
    data, path = source["data"], source["path"]
    tree = parser.parse(data)
    query = _tags_query(language)
    rows = tagged_definitions(query, tree) if query else definitions(tree)
    test_file = Path(path).name.startswith("test_")
    found = 0
    for name, kind, node in rows:
        key = path + "::" + name
        if key in symbols:
            continue
        symbols[key] = {"key": key, "path": path, "name": name,
                        "source": source["id"],
                        "line": int(node.start_point[0]) + 1,
                        "start": int(node.start_byte),
                        "end": int(node.end_byte), "kind": kind,
                        "is_test": test_file or name.startswith("test_"),
                        "language": language, "derivation": DERIVATION}
        found += 1
    if tree.root_node.has_error:
        diagnostics.append({"path": path, "kind": "partial_parse",
                            "language": language})
    if found:
        diagnostics.append({"path": path, "kind": "language_inventory",
                            "language": language, "symbols": found})
    return True
