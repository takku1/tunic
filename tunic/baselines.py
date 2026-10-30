"""Explicit small reference baselines; no semantic/vector retrieval is simulated."""

import ast
from collections import Counter
import math
from pathlib import Path
import re


def _tokens(text):
    return re.findall(r"[a-z_][a-z_0-9]*", text.lower())


class BM25:
    """Function chunks plus module-level imports, indexed without graph edges."""
    def __init__(self, root):
        self.documents = []
        self.diagnostics = []
        for path in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in path.parts or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
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
        count = len(self.documents)
        ranking = []
        for doc,length in zip(self.documents, self.lengths):
            if doc["symbol"] == symbol:
                continue
            score = 0.0
            for token in query:
                frequency = doc["tokens"][token]
                if not frequency:
                    continue
                idf = math.log(1+(count-self.frequency[token]+0.5)/(self.frequency[token]+0.5))
                score += idf * frequency * 2.2/(frequency + 1.2*(0.25+0.75*length/self.average_length))
            if score > 0:
                ranking.append({"symbol": doc["symbol"], "score": score, "is_test": doc["is_test"]})
        return sorted(ranking, key=lambda d:(-d["score"], d["symbol"]))[:top_k]


def direct_callers(store, symbol, snapshot):
    """One-hop control shares extraction with Tunic; not an independent analyzer."""
    _, view = store.view(snapshot)
    return sorted({a["proposition"]["caller"] for a in view["assertions"]
                   if a["proposition"]["callee"] == symbol and a["proposition"]["caller"] != symbol})
