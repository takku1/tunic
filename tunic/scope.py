"""Which files are in scope, and reading them.

Defaults, the root .gitignore, tunic.toml policy, and explicit excludes
decide what is walked; filetypes decides what enters the snapshot. Full
capture and incremental refresh share this walk so they observe the same
file set.
"""

import os
from pathlib import Path, PurePosixPath
import re
import tomllib

from . import filetypes
from .identity import identity

EXCLUDED = {".git", ".venv", "venv", "__pycache__", ".tunic", ".research", "node_modules"}


EXCLUDED_BUILD = {"dist", "build", ".astro", ".next", ".nuxt", "coverage", "target",
                  "out", ".output", "vendor", "third_party",
                  ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
                  ".tox", ".nox", ".gradle", ".terraform", ".ipynb_checkpoints",
                  ".benchmarks", ".deepeval", ".cache", ".idea", ".vs"}


def _pattern_regexes(pattern):
    """Compile one ignore pattern to [(regex, scope)] matchers.

    Documented subset of gitignore: `dir/` (that dir and everything
    under it), `*.ext`, bare names (any level), `/`-anchored paths,
    `**` spans, `*`/`?` within a segment, `!` negation (handled by
    the caller). Only the root `.gitignore` is read. Scopes: "any"
    matches files and dirs; "dir" matches only the directory itself;
    "under" matches paths beneath it.
    """
    body = pattern.strip("/")
    if not body:
        return None
    parts = []
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            parts.append("(.*/)?")
            i += 3
        elif body.startswith("**", i):
            parts.append(".*")
            i += 2
        elif body[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif body[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(body[i]))
            i += 1
    core = "".join(parts)
    dir_only = pattern.endswith("/")
    if "/" not in body:
        matchers = [(re.compile("^(?:.*/)?" + core + "$"), "any")]
        if dir_only:
            matchers = [(re.compile("^(?:.*/)?" + core + "$"), "dir"),
                        (re.compile("^(?:.*/)?" + core + "/.*$"), "under")]
    else:
        matchers = [(re.compile("^" + core + "$"), "any")]
        if dir_only:
            matchers = [(re.compile("^" + core + "$"), "dir"),
                        (re.compile("^" + core + "/.*$"), "under")]
    return matchers


def _check_scope_pattern(value, label):
    """Fail closed on non-relative explicit scope patterns.

    Leading `!` would invert the rule, and absolute paths (POSIX or
    Windows, with or without drive) never match workspace-relative
    paths, so both are rejected loudly instead of silently doing the
    wrong thing. Returns the stripped pattern.
    """
    stripped = value.strip() if isinstance(value, str) else ""
    if (not stripped or stripped.startswith("!")
            or PurePosixPath(stripped).is_absolute()
            or Path(stripped).is_absolute()
            or (len(stripped) > 1 and stripped[1] == ":")):
        raise ValueError("invalid %s scope pattern: %r" % (label, value))
    return stripped


def load_scope_config(root):
    """Read repository scope policy: (config, config_text, config_file).

    Only <root>/.tunic.toml [scope] is honored: `vendored` (extra
    exclude globs) and `first_party` (re-include globs). Unknown
    [scope] keys, wrong types, unreadable TOML, and absolute or
    leading-`!` patterns fail closed with ValueError. A missing file
    (or a file without [scope]) means ({}, "", None): old behavior.
    The config is re-read on every operation and never persisted, so
    crash/restart needs no recovery and snapshots stay atomic.
    """
    path = Path(root) / ".tunic.toml"
    if not path.is_file():
        return {"vendored": [], "first_party": []}, "", None
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ValueError("unreadable .tunic.toml: %s" % (error,)) from error
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except ValueError as error:
        raise ValueError("unreadable .tunic.toml: %s" % (error,)) from error
    scope = data.get("scope", {})
    if not isinstance(scope, dict):
        raise ValueError("[scope] must be a table")
    unknown = set(scope) - {"vendored", "first_party"}
    if unknown:
        raise ValueError("unknown [scope] keys: %s" % sorted(unknown))
    config = {}
    for key in ("vendored", "first_party"):
        values = scope.get(key, [])
        if not isinstance(values, list):
            raise ValueError("[scope].%s must be a list of relative "
                             "patterns" % key)
        config[key] = [_check_scope_pattern(value, key)
                       for value in values]
    return config, raw.decode("utf-8"), ".tunic.toml"


def _compile_ignore_rules(root, extra, *, vendored=(), first_party=()):
    """Ordered (matchers, negated) rules: defaults, root .gitignore,
    config vendored excludes, config first_party re-includes, then
    per-operation excludes. Last matching rule wins, so explicit
    per-operation excludes beat config, config beats .gitignore, and
    .gitignore beats the built-ins. First-party entries compile as
    negations, so git ancestor semantics apply: resurrecting paths
    beneath an excluded parent requires re-including the parent too."""
    for values, label in ((vendored, "vendored"),
                           (first_party, "first_party")):
        for value in values or ():
            _check_scope_pattern(value, label)
    raws = [name + "/" for name in sorted(EXCLUDED | EXCLUDED_BUILD)]
    ignore_file = Path(root) / ".gitignore"
    if ignore_file.is_file():
        try:
            lines = ignore_file.read_text(
                encoding="utf-8-sig").splitlines()
        except OSError:
            lines = []
        raws.extend(lines)
    raws.extend(value.strip() for value in vendored or ())
    raws.extend("!" + value.strip() for value in first_party or ())
    raws.extend(extra or ())
    rules = []
    for raw in raws:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        compiled = _pattern_regexes(line)
        if compiled is None:
            continue
        rules.append((compiled, negated))
    return rules


def _match_rules(relpath, is_dir, rules):
    """Last matching rule wins over one path."""
    ignored = False
    for matchers, negated in rules:
        fired = False
        for rx, scope in matchers:
            if scope == "dir" and not is_dir:
                continue
            if rx.match(relpath):
                fired = True
                break
        if fired:
            ignored = not negated
    return ignored


def _is_ignored(relpath, is_dir, rules):
    """Ignore state honoring excluded ancestors (git semantics).

    A file beneath an excluded directory stays excluded unless the
    directory itself is re-included by a later rule: negations match
    files, never their excluded parents.
    """
    parts = relpath.split("/")
    for depth in range(1, len(parts)):
        if _match_rules("/".join(parts[:depth]), True, rules):
            return True
    return _match_rules(relpath, is_dir, rules)


def iter_scope_files(root, rules, suffixes=None):
    """Yield (path, relpath) under root honoring ignore rules.

    Prunes excluded directories before descent: excluded trees are
    never walked. Returns (paths, stats) with files visited, pruned
    dirs, and excluded files for observability.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("scope root must be a directory")
    if suffixes is not None:
        suffixes = {s.lower() for s in suffixes}
    stats = {"files": 0, "dirs_pruned": 0, "excluded_files": 0}
    found = []
    for base, dirs, files in os.walk(root, followlinks=False):
        kept = []
        for name in sorted(dirs):
            path = Path(base) / name
            if path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            if _is_ignored(rel, True, rules):
                stats["dirs_pruned"] += 1
                continue
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = Path(base) / name
            if path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            if _is_ignored(rel, False, rules):
                stats["excluded_files"] += 1
                continue
            if suffixes is not None and path.suffix.lower() not in suffixes:
                continue
            stats["files"] += 1
            found.append((path, rel))
    return found, stats


def _scope_fingerprint(root, exclude, config_text=""):
    """Cheap scope identity: excludes + .gitignore bytes + config text."""
    ignore_file = Path(root) / ".gitignore"
    try:
        ignored = ignore_file.read_bytes() if ignore_file.is_file() else b""
    except OSError:
        ignored = b""
    return identity("scope", [sorted(set(exclude or ())),
                              ignored.decode("utf-8", "replace"),
                              config_text or ""])


def resolve_scope(root, exclude=()):
    """One scope evaluator for every stage: ingest, discovery, indexes.

    Reads .tunic.toml once per call (never persisted) and compiles the
    ordered rule set: built-in defaults, root .gitignore, config
    vendored excludes, config first_party re-includes, per-operation
    excludes. Returns (rules, policy) where policy records each source
    plus a fingerprint, so receipts show exactly which scope a result
    was computed under.
    """
    config, config_text, config_file = load_scope_config(root)
    rules = _compile_ignore_rules(root, exclude,
                                   vendored=config["vendored"],
                                   first_party=config["first_party"])
    fingerprint = _scope_fingerprint(root, exclude, config_text)
    policy = {"defaults": sorted(EXCLUDED | EXCLUDED_BUILD),
              "gitignore": (Path(root) / ".gitignore").is_file(),
              "config_file": config_file,
              "vendored": config["vendored"],
              "first_party": config["first_party"],
              "exclude": sorted(set(exclude or ())),
              "fingerprint": fingerprint}
    return rules, policy


def _scan(root, exclude=()):
    """Walk the source root and read candidate files without parsing.

    Returns (entries, diagnostics, excluded) where each entry carries
    path, data, text, and inventory flags. Build output, vendored code,
    root-.gitignore matches, and explicit excludes are pruned (counts
    reported, not per-path diagnostics). Shared by full capture and
    incremental change detection so both observe the same file set.

    Every other file is admitted unless filetypes.admit turns it away,
    and a turned-away file is counted under its reason in
    excluded["ledger"]. Nothing in scope disappears unaccounted for.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("source root must be a directory")
    rules, _scope_policy = resolve_scope(root, exclude)
    entries, diagnostics = [], []
    excluded = {"dirs": 0, "files": 0, "ledger": {}}
    def scan_error(error):
        raise error
    for base, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
        for name in sorted(dirs):
            p = Path(base) / name
            if p.is_symlink():
                diagnostics.append({"path": p.relative_to(root).as_posix(), "kind": "excluded_symlink"})
        kept = []
        for name in sorted(dirs):
            p = Path(base) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink() or name in EXCLUDED:
                continue
            if _is_ignored(rel, True, rules):
                excluded["dirs"] += 1
                continue
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            p = Path(base)/name
            path = p.relative_to(root).as_posix()
            if p.is_symlink():
                diagnostics.append({"path": path, "kind": "excluded_symlink"})
                continue
            if _is_ignored(path, False, rules):
                excluded["files"] += 1
                continue
            data, reason = None, None
            if filetypes.is_sensitive(path):
                reason = "sensitive"
            elif filetypes.is_known(path):
                data = p.read_bytes()
            else:
                data, reason = _read_unknown(p, path)
            if reason is not None:
                ledger = excluded["ledger"]
                ledger[reason] = ledger.get(reason, 0) + 1
                continue
            text = filetypes.is_prose(path)
            entries.append({"path": path, "data": data, "text": text,
                            "inventory": not (text or filetypes.is_python(path))})
    return entries, diagnostics, excluded


def _read_unknown(file_path, path):
    """(data, None) for an admitted file, else (None, ledger reason).

    Sniffs before reading: a repository of media must cost one small
    read per file, not its full size.
    """
    try:
        size = file_path.stat().st_size
        with open(file_path, "rb") as handle:
            head = handle.read(filetypes.SNIFF_BYTES)
            reason = filetypes.admit(path, size, head)
            if reason is not None:
                return None, reason
            data = head + handle.read()
    except OSError:
        return None, "unreadable"
    if not filetypes.decodes(data):
        return None, "undecodable"
    return data, None
