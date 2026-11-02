"""Arena v2 task validation: defective specs cannot become answer keys.

Each v1 defect class is a rejected shape here:

- phantom symbol:  every ``path::symbol`` named in the text must exist
  in the source index (v1 Q2 named a nonexistent ``Memory.publish``).
- undisclosed rule: a uniqueness verdict must quote the exact
  decision rule in the task text (v1 Q3 applied an unstated
  self/cls-only rule while the receiver was constructor-resolved).
- branchless chain: a chain task must name its branch and pin every
  hop to a source line (v1 Q5 left the branch underdetermined).
- unstated scope:  a caller task must state cross-file vs same-file
  scope in the text (v1 Q1 enforced an undeclared cross-file rule).

Deterministic, no model. Builders construct specs; ``validate_task``
rejects defective ones; runners (transcripts, wall time, tokens) are
a separate, model-blocked milestone and are NOT implemented here.
"""
import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskSpec:
    id: str
    kind: str  # callers | uniqueness | chain | decoy | abstention
    text: str
    key: dict = field(default_factory=dict)
    budget_actions: int = 12


def _named_symbols(text):
    """Crude path::symbol mentions inside task text."""
    import re
    return set(re.findall(
        r"[A-Za-z0-9_./-]+\.py::[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", text))


def validate_task(spec, index):
    """Return a list of defect strings; empty means acceptable.

    index: {"symbols": set(keys), "trees": {path: ast.Module},
            "edges": {(caller, callee)}, "scopes"...} -- only the
    entries a kind needs are consulted.
    """
    errors = []
    symbols = index.get("symbols", set())
    for named in _named_symbols(spec.text):
        if named not in symbols:
            errors.append("phantom symbol: %s" % named)
    if spec.kind == "callers":
        scope = spec.key.get("scope")
        if scope not in ("cross-file", "same-file-included"):
            errors.append("callers task needs explicit scope")
        elif (scope not in spec.text
              and scope.replace("-", " ") not in spec.text):
            errors.append("scope '%s' not stated in text" % scope)
        else:
            sites = {(c, cpath, ln) for c, cpath, ln in
                     spec.key.get("sites", [])}
            for caller, cpath, ln in sites:
                if scope == "cross-file" and cpath == spec.key.get("target_path"):
                    errors.append("cross-file scope with same-file site %s:%d"
                                  % (cpath, ln))
    elif spec.kind == "uniqueness":
        rule = spec.key.get("rule")
        if not rule:
            errors.append("uniqueness verdict needs a decision rule")
        elif rule not in spec.text:
            errors.append("decision rule not quoted in text")
        if "resolved" not in spec.key or "unique" not in spec.key:
            errors.append("uniqueness key needs resolved/unique fields")
    elif spec.kind == "chain":
        if not spec.key.get("branch"):
            errors.append("chain task needs an explicit branch")
        trees = index.get("trees", {})
        for caller, callee, path, ln in spec.key.get("hops", []):
            tree = trees.get(path)
            if tree is None:
                errors.append("hop path not indexed: %s" % path)
                continue
            heads = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and node.lineno == ln):
                    func = node.func
                    if isinstance(func, ast.Name):
                        heads.add(func.id)
                    elif isinstance(func, ast.Attribute):
                        heads.add(func.attr)
            short = callee.split("::")[-1].split(".")[-1]
            if short not in heads and callee.split("::")[-1] not in heads:
                errors.append("hop %s:%d calls no %s head"
                              % (path, ln, short))
        if "cold" not in spec.key or "cold_reason" not in spec.key:
            errors.append("chain key needs cold head + reason")
    elif spec.kind == "decoy":
        if spec.key.get("verdict") not in ("no-call", "calls"):
            errors.append("decoy key needs an explicit verdict")
    elif spec.kind == "abstention":
        if spec.key.get("expected") not in ("abstain", "verified-absent"):
            errors.append("abstention key needs an expectation")
    else:
        errors.append("unknown kind: %s" % spec.kind)
    return errors


def build_callers_task(symbol, sites, scope, target_path):
    scope_phrase = ("callers in other files (cross-file only)"
                    if scope == "cross-file"
                    else "callers including the defining file (same-file-included)")
    text = ("Which functions call %s? List exact path::symbol keys, "
            "or ABSTAIN with the reason. Scope: %s."
            % (symbol, scope_phrase))
    return TaskSpec(id="callers:" + symbol, kind="callers", text=text,
                    key={"target": symbol, "sites": list(sites),
                         "scope": scope, "target_path": target_path})


def build_uniqueness_task(site_path, site_line, receiver, name, caller,
                          definitions, unique, resolved):
    rule = ("unique only when the receiver is self/cls and the "
            "enclosing class defines the name")
    text = ("At %s:%d, `%s.%s(...)` is called inside %s. List every "
            "qualified `%s` definition, and state whether the receiver "
            "determines a unique target under this rule: %s."
            % (site_path, site_line, receiver, name, caller, name, rule))
    return TaskSpec(id="uniqueness:%s:%d" % (site_path, site_line),
                    kind="uniqueness", text=text,
                    key={"definitions": list(definitions), "unique": unique,
                         "resolved": resolved, "rule": rule})


def build_chain_task(start, branch, hops, cold, cold_reason):
    text = ("Trace the static call chain starting at %s, following this "
            "branch: %s. List each hop (caller -> callee) with the "
            "import or same-file fact supporting it. State where the "
            "trail goes cold and why." % (start, branch))
    return TaskSpec(id="chain:" + start, kind="chain", text=text,
                    key={"start": start, "branch": branch,
                         "hops": [list(h) for h in hops], "cold": cold,
                         "cold_reason": cold_reason})


def build_decoy_task(a_symbol, b_symbol, verdict):
    text = ("Does %s call %s? Answer YES with the call evidence, or NO "
            "with the reason. Do not assert what the evidence does not "
            "support." % (a_symbol, b_symbol))
    return TaskSpec(id="decoy:%s:%s" % (a_symbol, b_symbol), kind="decoy",
                    text=text,
                    key={"a": a_symbol, "b": b_symbol, "verdict": verdict})


def build_abstention_task(symbol, expectation="abstain"):
    text = ("Which functions call %s? List exact path::symbol keys, "
            "or ABSTAIN with the reason." % symbol)
    return TaskSpec(id="abstention:" + symbol, kind="abstention",
                    text=text, key={"target": symbol,
                                    "expected": expectation})


def index_trees(root):
    """Parse every top-level ``*.py`` file: {path: tree} + symbol set."""
    root = Path(root)
    trees, symbols = {}, set()
    for path in sorted(root.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, ValueError, UnicodeError):
            continue
        trees[path.name] = tree
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.add("%s::%s" % (path.name, node.name))
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef,
                                        ast.AsyncFunctionDef)):
                        symbols.add("%s::%s.%s"
                                    % (path.name, node.name, item.name))
    return {"symbols": symbols, "trees": trees}
