"""Explicit small reference baselines; no semantic/vector retrieval is simulated."""

import ast
from collections import Counter
import math
from pathlib import Path
import re


def _tokens(text):
    return re.findall(r"[a-z_][a-z_0-9]*", text.lower())


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


# A use, not a mention of a local: called, reached through a path or a
# receiver, or capitalized like a type. Measured against verified edges,
# this keeps recall of real calls and lifts confirmed hints substantially.
_USE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})(?=\s*\()"
                  r"|(?:::|\.|->)([A-Za-z_][A-Za-z0-9_]{2,})"
                  r"|\b([A-Z][A-Za-z0-9]{2,})\b")


def used_names(text):
    """Identifiers this code uses rather than merely binds."""
    return {name for match in _USE.findall(text) for name in match if name}


def _subword_tokens(text):
    """Whole identifiers plus their words, lowercased.

    Models ask in words. `chargeInvoice` and `invoice_step_0` must answer
    to "invoice", so each identifier also yields its camelCase and
    snake_case parts. The whole token stays, so exact names still match.
    """
    tokens = []
    for identifier in _IDENTIFIER.findall(text):
        tokens.append(identifier.lower())
        for piece in identifier.split("_"):
            parts = _CAMEL_BOUNDARY.split(piece)
            if len(parts) > 1 or piece != identifier:
                tokens.extend(part.lower() for part in parts if len(part) > 1)
    return tokens


def _rank(documents, lengths, average_length, frequency, terms, skip, top_k):
    """Shared BM25 scorer; skip excludes one symbol (the query itself)."""
    count = len(documents)
    ranking = []
    for doc, length in zip(documents, lengths):
        if doc["symbol"] == skip:
            continue
        score = 0.0
        for token in terms:
            occurrences = doc["tokens"][token]
            if not occurrences:
                continue
            idf = math.log(1 + (count - frequency[token] + 0.5) / (frequency[token] + 0.5))
            score += idf * occurrences * 2.2 / (occurrences + 1.2 * (0.25 + 0.75 * length / average_length))
        if score > 0:
            ranking.append({"symbol": doc["symbol"], "score": score, "is_test": doc["is_test"]})
    return sorted(ranking, key=lambda d: (-d["score"], d["symbol"]))[:top_k]


class BM25:
    """Function chunks plus module-level imports, indexed without graph edges.

    Indexes only in-scope ``.py`` files: defaults, the root
    ``.gitignore``, and explicit excludes prune before descent, so
    excluded trees are never walked.
    """
    def __init__(self, root, exclude=()):
        from .scope import iter_scope_files, resolve_scope
        root = Path(root)
        rules, policy = resolve_scope(root, exclude)
        found, self.scope_stats = iter_scope_files(root, rules, {".py"})
        self.scope_stats["policy"] = policy
        self.documents = []
        self.diagnostics = []
        for path, relative in sorted(found, key=lambda item: item[1]):
            try:
                text = path.read_bytes().decode("utf-8-sig")
                tree = ast.parse(text)
            except (ValueError, SyntaxError, UnicodeError) as error:
                self.diagnostics.append({"path": relative, "kind": type(error).__name__})
                continue
            imports = "\n".join(ast.get_source_segment(text, n) or "" for n in tree.body
                                if isinstance(n, (ast.Import, ast.ImportFrom)))
            documents = self.documents
            class Chunks(ast.NodeVisitor):
                def __init__(self):
                    self.scope = []

                def visit_ClassDef(self, node):
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                def visit_FunctionDef(self, node):
                    name = ".".join([*self.scope, node.name])
                    key = relative + "::" + name
                    content = relative + "\n" + imports + "\n" + (ast.get_source_segment(text, node) or "")
                    documents.append({"symbol": key, "tokens": Counter(_tokens(content)),
                                      "is_test": node.name.startswith("test_") or path.name.startswith("test_")})
                    self.scope.append(node.name)
                    self.generic_visit(node)
                    self.scope.pop()

                visit_AsyncFunctionDef = visit_FunctionDef
            Chunks().visit(tree)
        self.lengths = [sum(d["tokens"].values()) for d in self.documents]
        self.average_length = sum(self.lengths)/len(self.lengths) if self.lengths else 1
        self.frequency = Counter(t for d in self.documents for t in d["tokens"])

    def search(self, symbol, *, top_k=5):
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be positive")
        path, separator, function = symbol.partition("::")
        if not separator:
            raise ValueError("query requires an exact path::function")
        query = set(_tokens(Path(path).stem + " " + function.split(".")[-1]))
        return _rank(self.documents, self.lengths, self.average_length,
                     self.frequency, query, symbol, top_k)

    def search_text(self, query, *, top_k=5):
        """Rank chunks against free task text; the lexical discovery stage."""
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be positive")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        return _rank(self.documents, self.lengths, self.average_length,
                     self.frequency, set(_tokens(query)), None, top_k)


class TextNotes:
    """Lexical memory over prose notes: markdown/text files outside code.

    Code-only BM25 cannot retrieve session records, decisions, or outcomes,
    so a notes baseline that cannot read notes is a fiction. Each document
    is one retrievable unit keyed by relative path with an excerpt for
    bundling. No graph edges, no identity resolution: pure lexical recall,
    including stale records it cannot distinguish from current ones.

    Indexes only in-scope notes: defaults, the root ``.gitignore``, and
    explicit excludes prune before descent.
    """
    def __init__(self, root, exclude=()):
        from .filetypes import PROSE_EXTENSIONS
        from .scope import iter_scope_files
        from .scope import resolve_scope as _resolve_scope
        self.root = Path(root)
        rules, policy = _resolve_scope(self.root, exclude)
        found, self.scope_stats = iter_scope_files(
            self.root, rules, set(PROSE_EXTENSIONS))
        self.scope_stats["policy"] = policy
        self.documents = []
        for path, relative in sorted(found, key=lambda item: item[1]):
            try:
                text = path.read_bytes().decode("utf-8-sig")
            except (ValueError, UnicodeError):
                continue
            self.documents.append({"symbol": relative,
                                   "tokens": Counter(_tokens(text)),
                                   "text": text, "is_test": False})
        self.lengths = [sum(d["tokens"].values()) for d in self.documents]
        self.average_length = sum(self.lengths)/len(self.lengths) if self.lengths else 1
        self.frequency = Counter(t for d in self.documents for t in d["tokens"])

    def search_text(self, query, *, top_k=5):
        """Rank notes against free text; returns symbol/score/text rows."""
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be positive")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        ranked = _rank(self.documents, self.lengths, self.average_length,
                       self.frequency, set(_tokens(query)), None, top_k)
        texts = {d["symbol"]: d["text"] for d in self.documents}
        return [{**hit, "text": texts[hit["symbol"]]} for hit in ranked]


class _Lexicon:
    """BM25 statistics over one set of documents."""

    def __init__(self, documents):
        self.documents = documents
        self.lengths = [sum(d["tokens"].values()) for d in documents]
        self.average_length = (sum(self.lengths) / len(self.lengths)
                               if self.lengths else 1)
        self.frequency = Counter(t for d in documents for t in d["tokens"])
        self.by_symbol = {d["symbol"]: d for d in documents}

    def search(self, query, top_k):
        if top_k < 1 or not self.documents:
            return []
        return _rank(self.documents, self.lengths, self.average_length,
                     self.frequency, set(_subword_tokens(query)), None, top_k)


class SnapshotLexical:
    """Lexical reach over what BM25 and TextNotes cannot see.

    Those two walk the filesystem for Python and prose, match whole
    identifiers only, and have frozen rankings. This index reads the
    snapshot and matches words inside identifiers, across every symbol
    and every text-rung file. Reading the snapshot also means excluded
    paths cannot leak in. Callers add these results after the frozen
    stages, which is what keeps existing rankings byte-stable.
    """

    # Harvested names span one line; a symbol's text runs to the next one.
    MAX_CHUNK_BYTES = 4096

    def __init__(self, store, snapshot, view):
        from .filetypes import is_prose
        sources = store.snapshot_sources(snapshot)
        by_source = {}
        for symbol in view["symbols"]:
            path = symbol.get("path", "")
            if not is_prose(path):
                by_source.setdefault(symbol["source"], []).append(symbol)
        chunks, files = [], []
        for source_id, symbols in sorted(by_source.items()):
            path, data = sources.get(source_id, ("", b""))
            symbols.sort(key=lambda s: (s.get("start", 0), s["key"]))
            for index, symbol in enumerate(symbols):
                if symbol["key"].endswith("::document"):
                    text = data.decode("utf-8", "replace")
                    files.append({"symbol": path, "is_test": False,
                                  "tokens": Counter(_subword_tokens(path + " " + text))})
                    continue
                start = symbol.get("start", 0)
                following = symbols[index + 1].get("start", 0) \
                    if index + 1 < len(symbols) else len(data)
                end = min(max(symbol.get("end", start), following),
                          start + self.MAX_CHUNK_BYTES)
                text = data[start:end].decode("utf-8", "replace")
                chunks.append({"symbol": symbol["key"],
                               "uses": frozenset(used_names(text)),
                               "is_test": bool(symbol.get("is_test")),
                               "tokens": Counter(_subword_tokens(
                                   path + " " + symbol.get("name", "") + " " + text))})
        self.symbols = _Lexicon(chunks)
        self.files = _Lexicon(files)


def direct_callers(store, symbol, snapshot):
    """One-hop control shares extraction with Tunic; not an independent analyzer."""
    _, view = store.view(snapshot)
    return sorted({a["proposition"]["caller"] for a in view["assertions"]
                   if a["proposition"].get("relation") == "static_call"
                   and a["proposition"]["callee"] == symbol and a["proposition"]["caller"] != symbol})


def _evidence_edges(view):
    """Directed caller->callee pairs over static and mention evidence."""
    edges = set()
    for assertion in view["assertions"]:
        proposition = assertion["proposition"]
        if proposition.get("relation") not in ("static_call", "text_mention"):
            continue
        caller, callee = proposition.get("caller"), proposition.get("callee")
        if caller and callee and caller != callee:
            edges.add((caller, callee))
    return edges


def popularity(store, snapshot, *, top_k=5):
    """Most-mentioned symbols first: the naive string-matcher proxy.

    Query-independent. Ranks by incoming text_mention count, so generic
    verbs that match everywhere top the list. That is the point: it
    quantifies the fan-out pathology cheaper methods suffer from.
    """
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be positive")
    _, view = store.view(snapshot)
    counts = Counter()
    for assertion in view["assertions"]:
        if assertion["proposition"].get("relation") == "text_mention":
            counts[assertion["proposition"]["callee"]] += 1
    ranked = sorted(counts, key=lambda key: (-counts[key], key))
    return [{"symbol": key, "score": float(counts[key])} for key in ranked[:top_k]]


def pagerank(store, snapshot, *, top_k=5, iterations=50, damping=0.85):
    """Global PageRank over the evidence graph; the classic structural baseline.

    Query-independent and deterministic: fixed iteration count, sorted node
    order, uniform dangling redistribution. Standard reference point from
    the graph-tool world, reimplemented here in stdlib so the suite needs
    no downloads.
    """
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be positive")
    if type(iterations) is not int or iterations < 1:
        raise ValueError("iterations must be positive")
    if not isinstance(damping, (int, float)) or isinstance(damping, bool) \
            or not 0.0 < damping < 1.0:
        raise ValueError("damping must be strictly between 0 and 1")
    _, view = store.view(snapshot)
    edges = _evidence_edges(view)
    nodes = sorted({node for edge in edges for node in edge})
    if not nodes:
        return []
    outgoing = {node: sorted({callee for caller, callee in edges if caller == node})
                for node in nodes}
    count = len(nodes)
    rank = {node: 1.0 / count for node in nodes}
    for _ in range(iterations):
        dangling = sum(rank[node] for node in nodes if not outgoing[node])
        updated = {}
        for node in nodes:
            total = dangling / count
            for caller in nodes:
                if node in outgoing[caller]:
                    total += rank[caller] / len(outgoing[caller])
            updated[node] = (1.0 - damping) / count + damping * total
        rank = updated
    ordered = sorted(nodes, key=lambda node: (-rank[node], node))
    return [{"symbol": node, "score": rank[node]} for node in ordered[:top_k]]
