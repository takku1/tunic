"""Immutable evidence versions and atomic full-rebuild snapshots in SQLite."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .extract import DOC_PHASE_KINDS, STRUCTURE_KINDS, TEXT_EXTENSIONS, _build_bare_mentions, _build_claims, _build_dispatch, _build_docs, _build_exact_mentions, _build_mentions, _claim_mention_refs, _code_leaf_names, _collect_code, _exact_mention_refs, _leaf_document_frequency, _requalify, _resolve_code, _scan, extract, module_of, parse_source, PROFILE
from .identity import canonical, digest, identity

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(
    id TEXT PRIMARY KEY, path TEXT NOT NULL, digest TEXT NOT NULL, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(
    id TEXT PRIMARY KEY, committed_at TEXT NOT NULL, manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(
    snapshot TEXT REFERENCES snapshots(id), source TEXT REFERENCES sources(id),
    PRIMARY KEY(snapshot,source));
CREATE TABLE IF NOT EXISTS entities(
    snapshot TEXT REFERENCES snapshots(id), key TEXT, record TEXT NOT NULL,
    PRIMARY KEY(snapshot,key));
CREATE TABLE IF NOT EXISTS propositions(id TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS frontier(id TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS frontier_dedupe ON frontier(json_extract(record, '$.dedupe'));
CREATE TABLE IF NOT EXISTS schedule_jobs(id TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS schedule_jobs_state ON schedule_jobs(json_extract(record, '$.state'));
CREATE TABLE IF NOT EXISTS assertions(
    id TEXT PRIMARY KEY, proposition TEXT REFERENCES propositions(id),
    source TEXT REFERENCES sources(id), caller TEXT NOT NULL, callee TEXT NOT NULL,
    record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS incoming_calls ON assertions(callee,caller);
CREATE TABLE IF NOT EXISTS snapshot_assertions(
    snapshot TEXT REFERENCES snapshots(id), assertion TEXT REFERENCES assertions(id),
    PRIMARY KEY(snapshot,assertion));
CREATE TABLE IF NOT EXISTS consolidated_supports(
    id TEXT PRIMARY KEY, snapshot TEXT REFERENCES snapshots(id),
    record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS consolidated_by_snapshot ON consolidated_supports(snapshot);
CREATE TABLE IF NOT EXISTS consolidation_receipts(
    snapshot TEXT PRIMARY KEY REFERENCES snapshots(id), record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suppressions(
    id TEXT PRIMARY KEY, assertion TEXT NOT NULL,
    record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS suppressions_by_assertion ON suppressions(assertion);
"""


def _referenced_sources(value, found):
    """Collect every source-row id referenced anywhere inside a record.

    Assertion resolution supports embed source ids of definition spans.
    A kept document assertion referencing a row that is no longer a member
    of the current snapshot cites superseded content, so its document must
    be reprocessed. Extra reprocessing only costs work; it cannot change
    results, since reprocessing reproduces full-rebuild output exactly.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source" and isinstance(item, str):
                found.add(item)
            else:
                _referenced_sources(item, found)
    elif isinstance(value, list):
        for item in value:
            _referenced_sources(item, found)
    return found


class GraphStore:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.connection.close()
            raise ValueError("unsupported database schema")
        self.connection.executescript(SCHEMA)
        self.connection.execute("PRAGMA user_version=1")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.connection.close()

    def ingest(self, root, exclude=()):
        """Capture/extract first; publish all rows or roll back all rows."""
        root = str(Path(root).resolve(strict=True))
        binding = self.connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if binding and binding[0] != root:
            raise ValueError("database belongs to a different source root")
        sources, symbols, assertions, diagnostics, excluded = extract(
            root, exclude)
        result = self._publish(root, sources, symbols, assertions, diagnostics)
        result["excluded"] = excluded
        return result

    def _publish(self, root, sources, symbols, assertions, diagnostics):
        """Assemble the manifest and publish every row atomically."""
        manifest = {"profile": PROFILE, "sources": sorted(s["id"] for s in sources),
                    "symbols": symbols, "assertions": assertions, "diagnostics": diagnostics}
        snapshot = identity("snapshot", manifest)
        with self.connection:
            # Recheck ownership under the write transaction, including first ingest.
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('root',?)", (root,))
            actual = self.connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()[0]
            if actual != root:
                raise ValueError("database belongs to a different source root")
            for source in sources:
                self.connection.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?)",
                    (source["id"], source["path"], source["digest"], source["data"]))
            self.connection.execute("INSERT OR IGNORE INTO snapshots VALUES (?,?,?)",
                (snapshot, datetime.now(timezone.utc).isoformat(), canonical(manifest)))
            self.connection.executemany("INSERT OR IGNORE INTO members VALUES (?,?)",
                                        ((snapshot, s["id"]) for s in sources))
            self.connection.executemany("INSERT OR IGNORE INTO entities VALUES (?,?,?)",
                                        ((snapshot, s["key"], canonical(s)) for s in symbols))
            for assertion in assertions:
                p = assertion["proposition"]
                pid = identity("proposition", p)
                self.connection.execute("INSERT OR IGNORE INTO propositions VALUES (?,?)", (pid, canonical(p)))
                self.connection.execute("INSERT OR IGNORE INTO assertions VALUES (?,?,?,?,?,?)",
                    (assertion["id"], pid, assertion["source"], p["caller"], p["callee"], canonical(assertion)))
                self.connection.execute("INSERT OR IGNORE INTO snapshot_assertions VALUES (?,?)", (snapshot, assertion["id"]))
            self.connection.execute("INSERT OR REPLACE INTO metadata VALUES ('current',?)", (snapshot,))
        return {"snapshot": snapshot, "sources": len(sources), "symbols": len(symbols),
                "assertions": len(assertions), "diagnostics": diagnostics,
                "coverage": "bounded_static_analysis"}

    def refresh(self, root, exclude=()):
        """Incrementally re-ingest, differentially equivalent to ingest().

        Change detection walks and digests files without parsing. An
        unchanged tree returns the current snapshot with no parsing. When
        only text documents changed, stored code rows are reused and only the
        document phase reruns. When Python files changed, only the affected
        subset is re-parsed and re-resolved: changed files plus importers,
        same-module members, star importers, and module referrers, with
        documents reprocessed against the merged symbol table. Reports the
        path taken and per-phase work counters.
        """
        root = str(Path(root).resolve(strict=True))
        binding = self.connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if binding and binding[0] != root:
            raise ValueError("database belongs to a different source root")
        entries, walk_diagnostics, excluded = _scan(root, exclude)
        work = {"files_walked": len(entries), "files_parsed": 0,
                "code_files_parsed": 0, "doc_files_processed": 0,
                "assertions_reused": 0, "assertions_built": 0,
                "diagnostics_reused": 0, "diagnostics_built": 0}
        try:
            current = self.current()
        except ValueError:
            current = None
        fresh = {}
        for entry in entries:
            file_digest = digest(entry["data"])
            fresh[entry["path"]] = {"digest": file_digest, "data": entry["data"],
                                    "text": entry["text"],
                                    "inventory": entry.get("inventory", False),
                                    "id": identity("source", [entry["path"], file_digest])}
        if current is None:
            return self._refresh_full(root, work, dict(fresh), exclude)
        _, manifest = self.view(current)
        stored = {row[0]: {"digest": row[1], "id": row[2]}
                  for row in self.connection.execute(
                      """SELECT s.path, s.digest, s.id FROM sources s
                         JOIN members m ON m.source=s.id WHERE m.snapshot=?""", (current,))}
        added = sorted(set(fresh) - set(stored))
        deleted = sorted(set(stored) - set(fresh))
        changed = sorted(path for path in fresh if path in stored
                         and fresh[path]["digest"] != stored[path]["digest"])
        structure_now = sorted((d["path"], d["kind"]) for d in walk_diagnostics
                               if d["kind"] in STRUCTURE_KINDS)
        structure_then = sorted((d["path"], d["kind"]) for d in manifest["diagnostics"]
                                if d["kind"] in STRUCTURE_KINDS)
        if not added and not deleted and not changed and structure_now == structure_then:
            return {"snapshot": current, "path": "unchanged", "reused": True,
                    "previous": current, "published_new": False, "work": work,
                    "excluded": excluded}
        touched = added + deleted + changed
        code_touched = [p for p in touched
                        if Path(p).suffix not in TEXT_EXTENSIONS]
        if structure_now != structure_then:
            return self._refresh_full(root, work, dict(fresh), exclude)
        if code_touched:
            return self._refresh_code(root, work, manifest, stored, dict(fresh),
                                      current, touched, code_touched)
        return self._refresh_documents(root, work, manifest, stored, dict(fresh),
                                       current, touched)

    def _refresh_full(self, root, work, fresh, exclude=()):
        """Full extract fallback; also serves databases without a snapshot."""
        code_files = sum(1 for entry in fresh.values() if not entry["text"])
        work["files_parsed"] = code_files
        work["code_files_parsed"] = code_files
        work["doc_files_processed"] = sum(1 for entry in fresh.values() if entry["text"])
        try:
            previous = self.current()
        except ValueError:
            previous = None
        sources, symbols, assertions, diagnostics, excluded = extract(
            root, exclude)
        work["assertions_built"] = len(assertions)
        work["diagnostics_built"] = len(diagnostics)
        result = self._publish(root, sources, symbols, assertions, diagnostics)
        result = dict(result, path="full", reused=False, previous=previous,
                      published_new=result["snapshot"] != previous, work=work,
                      excluded=excluded)
        return result

    @staticmethod
    def _imports_touched(text, path, touched_modules):
        """Whether file text imports any touched module.

        Over-approximates the extractor's own top-level import recognition:
        extra matches only cause extra re-parsing, never wrong results.
        Relative imports resolve against the importing file's package.
        """
        import re
        for module in touched_modules:
            if re.search(r"(?m)^\s*(?:import|from)\s+[\w.]*"
                         + re.escape(module) + r"\b", text):
                return True
        own = module_of(path).split(".")
        package = own if Path(path).name == "__init__.py" else own[:-1]
        for match in re.finditer(r"(?m)^\s*from\s+(\.+)([\w.]*)\s+import\b", text):
            level = len(match.group(1))
            if level > len(package):
                continue
            base = package[:len(package) - level + 1]
            remainder = match.group(2)
            referenced = ".".join(base + ([remainder] if remainder else []))
            if referenced in touched_modules:
                return True
        return False

    @staticmethod
    def _dispatch_scope(manifest, touched_set, stored):
        """Caller/table file pairs linked by registry-dispatch candidates.

        A candidate's validity spans its call-site file and its registry
        table file, so touching either side must re-resolve both. Returns
        (extra affected paths, invalid table names carried forward from
        kept diagnostics on untouched files). Table-validity reasons only;
        site-level reasons never invalidate a table.
        """
        callers = {}
        for assertion in manifest["assertions"]:
            proposition = assertion.get("proposition", {})
            if proposition.get("via") != "registry_dispatch":
                continue
            caller_path = proposition.get("caller", "").split("::")[0]
            table = (assertion.get("derivation") or {}).get("table", "")
            table_path = table.split("::")[0]
            if not caller_path or not table_path:
                continue
            callers.setdefault(table, set()).add(caller_path)
        extra = set()
        for table, paths in callers.items():
            table_path = table.split("::")[0]
            if table_path in touched_set:
                extra.update(p for p in paths if p in stored)
            if paths & touched_set and table_path in stored:
                extra.add(table_path)
        invalid = set()
        for diagnostic in manifest["diagnostics"]:
            if (diagnostic.get("kind") == "dispatch_unresolved"
                    and diagnostic.get("reason") in (
                        "reassigned_table", "dynamic_table_key",
                        "unresolvable_entry", "unknown_target")
                    and diagnostic.get("path") not in touched_set
                    and diagnostic.get("table")):
                invalid.add(diagnostic["table"])
        return extra, invalid

    def _doc_subset(self, text_sources, symbols, code_assertions, manifest,
                      reprocess):
        """Recompute claims and mentions for reprocessed docs only.

        Runs the exact pass over every document before the bare-leaf pass
        starts, seeding the shared dedup set from stored assertions of
        untouched documents at their scan positions and dropping stored
        mentions that an earlier document now shadows. This replicates full
        rebuild first-wins order exactly. Stored claims of untouched
        documents are reused; reprocessed documents regenerate both. Returns
        (assertions, claim_diagnostics, mention_diagnostics).
        """
        order = [s["path"] for s in text_sources]
        by_path = {s["path"]: s for s in text_sources}
        reprocess = set(reprocess)
        all_text_ids = {s["id"] for s in text_sources}
        stored_mentions = {}
        for record in manifest["assertions"]:
            proposition = record.get("proposition", {})
            if proposition.get("relation") == "text_mention":
                stored_mentions.setdefault(record.get("source"), []).append(record)
        src_id = {s["path"]: s["id"] for s in text_sources}
        id_of_source = {s["id"]: s["path"] for s in text_sources}

        def _line_at(path, start):
            offset = 0
            for raw in by_path[path]["data"].splitlines(keepends=True):
                offset += len(raw)
                if offset > start:
                    try:
                        return raw.decode("utf-8")
                    except UnicodeError:
                        return ""
            return ""

        seen = set()
        fresh_mentions, mention_diags = [], []
        kept_mentions = []
        kept_exact = {(src_id[path], record["proposition"].get("callee"))
                      for path in order if path not in reprocess
                      for record in stored_mentions.get(src_id[path], [])
                      if record.get("resolution", {}).get("rule") == "exact_key_match_v1"}
        doc_freq = _leaf_document_frequency(
            text_sources, _code_leaf_names(symbols, all_text_ids))
        kept_claim_refs = set()
        for entry in manifest["assertions"]:
            proposition = entry.get("proposition", {})
            if (entry.get("status") != "asserted_in_source"
                    or proposition.get("relation") in ("static_call", "text_mention")):
                continue
            doc = id_of_source.get(entry.get("source"))
            if doc is None or doc in reprocess:
                continue
            kept_claim_refs.add((entry.get("source"), proposition.get("caller")))
            kept_claim_refs.add((entry.get("source"), proposition.get("callee")))
        for record in manifest["assertions"]:
            if (record.get("proposition", {}).get("relation") != "text_mention"
                    or record.get("resolution", {}).get("rule") != "name_match_proposed_v1"):
                continue
            path = id_of_source.get(record.get("source"))
            if path is None or path in reprocess or path not in by_path:
                continue
            leaf = record.get("resolution", {}).get("expression", "")
            if not _requalify(leaf, _line_at(path, record.get("start", 0)),
                              record.get("source"),
                              record["proposition"].get("callee"),
                              doc_freq, kept_exact, kept_claim_refs):
                reprocess.add(path)
        for diagnostic in manifest["diagnostics"]:
            if diagnostic.get("kind") != "mention_candidate":
                continue
            path = diagnostic.get("path")
            if path is None or path in reprocess or path not in by_path:
                continue
            lines = by_path[path]["data"].splitlines()
            lineno = diagnostic.get("line") or 0
            try:
                line = lines[lineno - 1].decode("utf-8")
            except (UnicodeError, IndexError):
                line = ""
            if _requalify(diagnostic.get("expression", ""), line,
                          src_id.get(path),
                          diagnostic.get("callee"), doc_freq, kept_exact,
                          kept_claim_refs):
                reprocess.add(path)
        repro = [by_path[p] for p in order if p in reprocess]
        claim_assertions, claim_diags = [], []
        claim_spans = _build_claims(repro, symbols, claim_assertions,
                                    claim_diags, all_text_ids)
        for path in order:
            if path in reprocess:
                _build_exact_mentions([by_path[path]], symbols,
                                      fresh_mentions, claim_spans,
                                      seen, all_text_ids)
            else:
                for record in stored_mentions.get(src_id[path], []):
                    if record.get("resolution", {}).get("rule") != "exact_key_match_v1":
                        continue
                    key = (record.get("start"),
                           record["proposition"].get("callee"))
                    if key in seen:
                        continue
                    seen.add(key)
                    kept_mentions.append(record)
        exact_refs = kept_exact | _exact_mention_refs(fresh_mentions)
        for path in order:
            if path in reprocess:
                _build_bare_mentions([by_path[path]], symbols,
                                     fresh_mentions, mention_diags,
                                     claim_spans, seen, all_text_ids,
                                     doc_freq, exact_refs,
                                     kept_claim_refs | _claim_mention_refs(claim_assertions))
            else:
                for record in stored_mentions.get(src_id[path], []):
                    if record.get("resolution", {}).get("rule") == "exact_key_match_v1":
                        continue
                    key = (record.get("start"),
                           record["proposition"].get("callee"))
                    if key in seen:
                        continue
                    seen.add(key)
                    kept_mentions.append(record)
        valid_ids = {s["id"] for s in text_sources if s["path"] not in reprocess}
        kept_claims = [a for a in manifest["assertions"]
                       if a.get("proposition", {}).get("relation")
                       not in ("static_call", "text_mention")
                       and a.get("source") in valid_ids]
        assertions = (list(code_assertions) + kept_mentions + kept_claims
                      + claim_assertions + fresh_mentions)
        return assertions, claim_diags, mention_diags

    def _refresh_code(self, root, work, manifest, stored, fresh, current,
                      touched, code_touched):
        """Re-parse and re-resolve only the affected code subset.

        Affected files are the changed or added files, stored importers of
        touched code paths, members of touched modules, files with star
        imports, and referrers of added or removed modules. Everything else
        reuses stored rows verbatim. Documents rerun fully against the
        merged symbol table to preserve mention-dedup order.
        """
        touched_set = set(code_touched)
        touched_modules = {module_of(p) for p in code_touched}
        stored_code_paths = [p for p in stored
                             if Path(p).suffix not in TEXT_EXTENSIONS]
        affected = set(p for p in code_touched if p in fresh)
        for path in stored_code_paths:
            if path in touched_set:
                affected.add(path)
                continue
            if module_of(path) in {module_of(p) for p in touched_set}:
                affected.add(path)
                continue
        for assertion in manifest["assertions"]:
            proposition = assertion.get("proposition", {})
            callee = proposition.get("callee", "")
            callee_path = callee.split("::")[0]
            if callee_path in touched_set or module_of(callee_path) in touched_modules:
                caller = proposition.get("caller", "").split("::")[0]
                if (caller in stored and caller not in touched_set
                        and Path(caller).suffix not in TEXT_EXTENSIONS):
                    affected.add(caller)
        for diagnostic in manifest["diagnostics"]:
            if diagnostic.get("kind") == "star_import":
                path = diagnostic.get("path")
                if path in stored and path not in touched_set:
                    affected.add(path)
        unresolved_paths = {d.get("path") for d in manifest["diagnostics"]
                            if d.get("kind") == "unresolved_call"}
        for path in sorted(unresolved_paths):
            if (path not in stored or path in affected
                    or Path(path).suffix in TEXT_EXTENSIONS
                    or path not in fresh):
                continue
            try:
                text = fresh[path]["data"].decode("utf-8")
            except UnicodeError:
                continue
            if self._imports_touched(text, path, touched_modules):
                affected.add(path)
        dispatch_extra, dispatch_invalid = self._dispatch_scope(
            manifest, touched_set, stored)
        affected |= {p for p in dispatch_extra if p in fresh}
        parse_paths = sorted(p for p in affected if p in fresh)
        records, parse_diagnostics, unparsed = {}, [], []
        for path in parse_paths:
            record, diagnostic = parse_source(path, fresh[path]["data"])
            if diagnostic is not None:
                parse_diagnostics.append(diagnostic)
                unparsed.append(path)
            else:
                records[path] = record
        work["files_parsed"] = len(parse_paths)
        work["code_files_parsed"] = len(parse_paths)
        work["parse_skipped"] = (sum(1 for entry in fresh.values() if not entry["text"])
                                 - len(parse_paths))
        modules = {}
        for path in stored_code_paths:
            if path in affected:
                continue
            module = module_of(path)
            members = modules.setdefault(module, [])
            functions, classes, class_names = {}, {}, set()
            for symbol in manifest["symbols"]:
                if symbol.get("path") != path:
                    continue
                name = symbol.get("name", "")
                if symbol.get("kind") == "class":
                    # class entities are resolved by _resolve_class_symbol,
                    # not callable functions; their keys are derived from
                    # path + name, so they need no table entry here — but
                    # the name set must be present so import resolution
                    # can distinguish a method-less class from a function.
                    class_names.add(name)
                    continue
                if "." not in name:
                    functions[name] = symbol["key"]
                else:
                    head, _, leaf = name.partition(".")
                    classes.setdefault(head, {})[leaf] = symbol["key"]
            members.append({"module": module, "path": path,
                            "functions": functions, "classes": classes,
                            "class_names": sorted(class_names)})
        for record in records.values():
            if record.get("inventory"):
                # Harvested symbols need no import-resolution table;
                # resolve passes skip treeless sources.
                continue
            modules.setdefault(record["module"], []).append(record)
        for path in unparsed:
            module = module_of(path)
            members = modules.setdefault(module, [])
            members.append({"module": module, "functions": {}, "classes": {}})
        symbols = {s["key"]: s for s in manifest["symbols"]
                   if s.get("path") not in affected
                   and not s["key"].endswith("::document")}
        text_sources = [{"id": info["id"], "path": path, "digest": info["digest"],
                         "data": info["data"], "tree": None, "text": True}
                        for path, info in sorted(fresh.items()) if info["text"]]
        failed_sources = [{"id": fresh[path]["id"], "path": path,
                           "digest": fresh[path]["digest"],
                           "data": fresh[path]["data"], "tree": None,
                           "text": False, "module": module_of(path)}
                          for path in unparsed]
        collect_sources = list(records.values()) + failed_sources + text_sources
        collect_diags = list(parse_diagnostics)
        _collect_code(collect_sources, symbols,
                      [], collect_diags)
        code_sources = [r for r in collect_sources if not r.get("text")]
        # Callback-site facts from kept (unaffected-path) diagnostics:
        # an unchanged callback definition must still match re-parsed
        # callers. Affected paths re-discover their own sites from the
        # fresh parse, so stale facts are never carried for them.
        known_sites = [{"callee": d["caller"], "parameter": d["parameter"],
                        "index": d["index"], "path": d["path"],
                        "binding": {"source": fresh[d["path"]]["id"],
                                    "start": d["start"], "end": d["end"]}}
                       for d in manifest["diagnostics"]
                       if d.get("kind") == "unresolved_call" and "parameter" in d
                       and d.get("caller") and d.get("path") not in affected
                       and d.get("path") in fresh]
        resolve_assertions, resolve_diags = [], []
        _resolve_code(code_sources, modules, symbols,
                      resolve_assertions, resolve_diags,
                      known_sites=known_sites)
        _build_dispatch(code_sources, modules, symbols,
                        resolve_assertions, resolve_diags,
                        invalid_tables=dispatch_invalid)
        rows = {row[0]: row[1] for row in self.connection.execute(
            """SELECT s.id, s.path FROM sources s
               JOIN members m ON m.source=s.id WHERE m.snapshot=?""", (current,))}
        old_keys = {s["key"] for s in manifest["symbols"]
                      if s.get("path") in touched_set}
        new_keys = set()
        for path, record in list(records.items()):
            if path not in touched_set:
                continue
            for key in record.get("functions", {}).values():
                new_keys.add(key)
            for methods in record.get("classes", {}).values():
                new_keys.update(methods.values())
        changed_terms = set(old_keys) | set(new_keys)
        for key in old_keys | new_keys:
            changed_terms.add(key.split("::")[-1].split(".")[-1])
        reprocess = {p for p in touched if p in fresh
                     and Path(p).suffix in TEXT_EXTENSIONS}
        for info in text_sources:
            if info["path"] not in stored:
                reprocess.add(info["path"])
                continue
            try:
                text = info["data"].decode("utf-8")
            except UnicodeError:
                continue
            if any(term in text for term in changed_terms):
                reprocess.add(info["path"])
        member_ids = set(rows)
        doc_of_source = {info["id"]: info["path"] for info in text_sources}
        for record in manifest["assertions"]:
            path = doc_of_source.get(record.get("source"))
            if (path is None or path in reprocess or path not in stored
                    or Path(path).suffix not in TEXT_EXTENSIONS):
                continue
            if _referenced_sources(record, set()) - member_ids:
                reprocess.add(path)
        drop = set(affected) | {p for p in touched if p not in fresh}
        code_assertions = [a for a in manifest["assertions"]
                           if rows.get(a.get("source")) not in drop
                           and rows.get(a.get("source")) not in {s["path"] for s in text_sources}]
        code_assertions.extend(resolve_assertions)
        assertions, claim_diags, mention_diags = self._doc_subset(
            text_sources, symbols, code_assertions, manifest, reprocess)
        doc_diags = claim_diags + mention_diags
        deleted_paths = {p for p in touched if p not in fresh}
        kept = [d for d in manifest["diagnostics"]
                if not (d["path"] in drop
                        or (d["kind"] in DOC_PHASE_KINDS
                            and (d["path"] in reprocess or d["path"] in deleted_paths)))]
        merged = kept + collect_diags + resolve_diags + doc_diags
        merged.sort(key=lambda x: (x["path"], x.get("line") or 0, x["kind"], x.get("start", 0)))
        work["doc_files_processed"] = len(text_sources)
        work["docs_reprocessed"] = len(reprocess)
        code_reused = [a for a in manifest["assertions"]
                       if rows.get(a.get("source")) not in drop
                       and rows.get(a.get("source")) not in {s["path"] for s in text_sources}]
        work["assertions_reused"] = len(code_reused)
        work["assertions_built"] = len(assertions) - len(code_reused)
        work["diagnostics_reused"] = len(kept)
        work["diagnostics_built"] = len(collect_diags) + len(resolve_diags) + len(doc_diags)
        member_rows = self.connection.execute(
            """SELECT s.id, s.path, s.digest, s.data FROM sources s
               JOIN members m ON m.source=s.id WHERE m.snapshot=?""", (current,)).fetchall()
        unaffected_sources = [{"id": row[0], "path": row[1], "digest": row[2],
                               "data": bytes(row[3]), "tree": None, "text": False}
                              for row in member_rows
                              if row[1] not in affected and row[1] in fresh
                              and Path(row[1]).suffix not in TEXT_EXTENSIONS]
        sources_out = unaffected_sources + code_sources + text_sources
        result = self._publish(root, sources_out,
                               sorted(symbols.values(), key=lambda x: x["key"]),
                               sorted(assertions, key=lambda x: x["id"]), merged)
        return dict(result, path="code", reused=True, previous=current,
                    published_new=result["snapshot"] != current, work=work,
                    affected_files=sorted(affected))

    def _refresh_documents(self, root, work, manifest, stored, fresh, current, touched):
        """Reuse stored code rows; rerun the document phase over text files."""
        code_ids = {info["id"] for path, info in stored.items()
                    if Path(path).suffix not in TEXT_EXTENSIONS}
        rows = self.connection.execute(
            """SELECT s.id, s.path, s.digest, s.data FROM sources s
               JOIN members m ON m.source=s.id WHERE m.snapshot=?""", (current,)).fetchall()
        code_sources = [{"id": row[0], "path": row[1], "digest": row[2],
                         "data": bytes(row[3]), "tree": None, "text": False}
                        for row in rows if row[0] in code_ids]
        text_sources = [{"id": info["id"], "path": path, "digest": info["digest"],
                         "data": info["data"], "tree": None, "text": True}
                        for path, info in sorted(fresh.items())
                        if info["text"]]
        code_symbols = [s for s in manifest["symbols"] if s["source"] in code_ids]
        code_assertions = [a for a in manifest["assertions"] if a["source"] in code_ids]
        doc_symbols = {}
        for source in text_sources:
            if source["data"]:
                key = source["path"] + "::document"
                doc_symbols[key] = {"key": key, "path": source["path"], "name": "document",
                                    "source": source["id"], "line": 1,
                                    "start": 0, "end": len(source["data"]), "is_test": False}
        symbols = {s["key"]: s for s in code_symbols}
        symbols.update(doc_symbols)
        reprocess = {path for path in touched if path in fresh
                     and Path(path).suffix in TEXT_EXTENSIONS}
        assertions, claim_diags, mention_diags = self._doc_subset(
            text_sources, symbols, code_assertions, manifest, reprocess)
        doc_diagnostics = claim_diags + mention_diags
        deleted_paths = {p for p in touched if p not in fresh}
        kept = [d for d in manifest["diagnostics"]
                if not (d["kind"] in DOC_PHASE_KINDS
                        and (d["path"] in reprocess or d["path"] in deleted_paths))]
        merged = kept + doc_diagnostics
        merged.sort(key=lambda x: (x["path"], x.get("line") or 0, x["kind"], x.get("start", 0)))
        symbols_out = sorted(symbols.values(), key=lambda x: x["key"])
        assertions_out = sorted(assertions, key=lambda x: x["id"])
        work["doc_files_processed"] = len(text_sources)
        work["docs_reprocessed"] = len(reprocess)
        work["assertions_reused"] = len(code_assertions)
        work["assertions_built"] = len(assertions_out) - len(code_assertions)
        work["diagnostics_reused"] = len(kept)
        work["diagnostics_built"] = len(doc_diagnostics)
        sources_out = code_sources + text_sources
        result = self._publish(root, sources_out, symbols_out, assertions_out, merged)
        return dict(result, path="documents", reused=True, previous=current,
                    published_new=result["snapshot"] != current, work=work,
                    diagnostics=result["diagnostics"])

    def snapshots(self):
        rows = self.connection.execute(
            "SELECT id, substr(committed_at,1,10) FROM snapshots ORDER BY committed_at, id"
        ).fetchall()
        return [{"id": row[0], "committed_on": row[1]} for row in rows]

    def current(self):
        row = self.connection.execute("SELECT value FROM metadata WHERE key='current'").fetchone()
        if row is None:
            raise ValueError("database has no published snapshot; ingest a repository first")
        return row[0]

    def committed_on(self, snapshot):
        row = self.connection.execute("SELECT substr(committed_at,1,10) FROM snapshots WHERE id=?",
                                      (snapshot,)).fetchone()
        if row is None:
            raise ValueError("unknown snapshot")
        return row[0]

    def view(self, snapshot=None):
        snapshot = snapshot or self.current()
        row = self.connection.execute("SELECT manifest FROM snapshots WHERE id=?", (snapshot,)).fetchone()
        if row is None:
            raise ValueError("unknown snapshot")
        manifest = json.loads(row[0])
        if identity("snapshot", manifest) != snapshot:
            raise ValueError("snapshot integrity failure")
        # The manifest commits to the normalized graph rows, not just a JSON copy.
        symbols = [json.loads(r[0]) for r in self.connection.execute(
            "SELECT record FROM entities WHERE snapshot=? ORDER BY key", (snapshot,))]
        assertions = [json.loads(r[0]) for r in self.connection.execute("""SELECT a.record FROM assertions a
            JOIN snapshot_assertions m ON m.assertion=a.id WHERE m.snapshot=? ORDER BY a.id""", (snapshot,))]
        sources = sorted(r[0] for r in self.connection.execute("SELECT source FROM members WHERE snapshot=?", (snapshot,)))
        if symbols != manifest["symbols"] or assertions != manifest["assertions"] or sources != manifest["sources"]:
            raise ValueError("snapshot graph rows differ from committed manifest")
        return snapshot, manifest

    def source(self, source_id, snapshot):
        row = self.connection.execute("""SELECT s.path,s.digest,s.data FROM sources s
            JOIN members m ON m.source=s.id WHERE m.snapshot=? AND s.id=?""", (snapshot, source_id)).fetchone()
        if row is None:
            raise ValueError("source is not a member of the selected snapshot")
        return {"id": source_id, "path": row[0], "digest": row[1], "data": bytes(row[2])}
