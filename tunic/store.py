"""Immutable evidence versions and atomic full-rebuild snapshots in SQLite."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .extract import extract, PROFILE
from .identity import canonical, identity

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

    def current(self):
        row = self.connection.execute("SELECT value FROM metadata WHERE key='current'").fetchone()
        if row is None:
            raise ValueError("database has no published snapshot; ingest a repository first")
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
