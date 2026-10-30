"""Immutable evidence versions and atomic full-rebuild snapshots in SQLite."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .extract import DOC_PHASE_KINDS, STRUCTURE_KINDS, TEXT_EXTENSIONS, _build_docs, _scan, extract, PROFILE
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
CREATE TABLE IF NOT EXISTS assertions(
    id TEXT PRIMARY KEY, proposition TEXT REFERENCES propositions(id),
    source TEXT REFERENCES sources(id), caller TEXT NOT NULL, callee TEXT NOT NULL,
    record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS incoming_calls ON assertions(callee,caller);
CREATE TABLE IF NOT EXISTS snapshot_assertions(
    snapshot TEXT REFERENCES snapshots(id), assertion TEXT REFERENCES assertions(id),
    PRIMARY KEY(snapshot,assertion));
"""


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

    def ingest(self, root):
        """Capture/extract first; publish all rows or roll back all rows."""
        root = str(Path(root).resolve(strict=True))
        binding = self.connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if binding and binding[0] != root:
            raise ValueError("database belongs to a different source root")
        sources, symbols, assertions, diagnostics = extract(root)
        return self._publish(root, sources, symbols, assertions, diagnostics)

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

    def refresh(self, root):
        """Incrementally re-ingest, differentially equivalent to ingest().

        Change detection walks and digests files without parsing. An
        unchanged tree returns the current snapshot with no parsing. When
        only text documents changed, stored code rows are reused and only the
        document phase reruns: code assertions provably depend on code bytes
        alone, while mentions and claims resolve against the reused symbol
        table. Any code change falls back to a full extract, since global
        name resolution can shift everywhere. Reports the path taken and
        per-phase work counters.
        """
        root = str(Path(root).resolve(strict=True))
        binding = self.connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if binding and binding[0] != root:
            raise ValueError("database belongs to a different source root")
        entries, walk_diagnostics = _scan(root)
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
                                    "id": identity("source", [entry["path"], file_digest])}
        if current is None:
            return self._refresh_full(root, work, dict(fresh))
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
                    "previous": current, "published_new": False, "work": work}
        touched = added + deleted + changed
        code_touched = [p for p in touched
                        if Path(p).suffix not in TEXT_EXTENSIONS]
        if code_touched or structure_now != structure_then:
            return self._refresh_full(root, work, dict(fresh))
        return self._refresh_documents(root, work, manifest, stored, dict(fresh),
                                       current, touched)

    def _refresh_full(self, root, work, fresh):
        """Full extract fallback; also serves databases without a snapshot."""
        code_files = sum(1 for entry in fresh.values() if not entry["text"])
        work["files_parsed"] = code_files
        work["code_files_parsed"] = code_files
        work["doc_files_processed"] = sum(1 for entry in fresh.values() if entry["text"])
        try:
            previous = self.current()
        except ValueError:
            previous = None
        sources, symbols, assertions, diagnostics = extract(root)
        work["assertions_built"] = len(assertions)
        work["diagnostics_built"] = len(diagnostics)
        result = self._publish(root, sources, symbols, assertions, diagnostics)
        result = dict(result, path="full", reused=False, previous=previous,
                      published_new=result["snapshot"] != previous, work=work)
        return result

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
        assertions = list(code_assertions)
        doc_diagnostics = []
        _build_docs(text_sources, symbols, assertions, doc_diagnostics)
        fresh_text_paths = {s["path"] for s in text_sources}
        deleted_paths = {p for p in touched if p not in fresh}
        kept = [d for d in manifest["diagnostics"]
                if not (d["kind"] in DOC_PHASE_KINDS
                        and (d["path"] in fresh_text_paths or d["path"] in deleted_paths))]
        merged = kept + doc_diagnostics
        merged.sort(key=lambda x: (x["path"], x.get("line") or 0, x["kind"], x.get("start", 0)))
        symbols_out = sorted(symbols.values(), key=lambda x: x["key"])
        assertions_out = sorted(assertions, key=lambda x: x["id"])
        work["doc_files_processed"] = len(text_sources)
        work["assertions_reused"] = len(code_assertions)
        work["assertions_built"] = len(assertions_out) - len(code_assertions)
        work["diagnostics_reused"] = len(kept)
        work["diagnostics_built"] = len(doc_diagnostics)
        sources_out = code_sources + text_sources
        result = self._publish(root, sources_out, symbols_out, assertions_out, merged)
        return dict(result, path="documents", reused=True, previous=current,
                    published_new=result["snapshot"] != current, work=work,
                    diagnostics=result["diagnostics"])

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
