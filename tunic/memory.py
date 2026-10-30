"""Agent-facing memory sessions sharing one database across processes.

A Memory is a thin, explicit wrapper over GraphStore and the compiler
queries. Sessions are separate Memory instances (possibly in separate
processes) over the same database path: whatever one session ingests and
records, another session retrieves. Nothing is inferred; every write is a
timestamped file append or an explicit claim block awaiting ingest.
"""

from datetime import datetime, timezone
import json
from pathlib import Path

from . import compiler
from .extract import CLAIM_RELATIONS, is_iso_date
from .store import GraphStore


class Memory:
    def __init__(self, db, root="."):
        self.root = Path(root)
        self.store = GraphStore(db)

    def close(self):
        self.store.close()

    def _workspace_path(self, document):
        """Resolve a document against the root; absolute paths, `..` escapes,
        and symlinks pointing outside are rejected before any write."""
        candidate = self.root / document
        try:
            root = self.root.resolve()
            resolved = candidate.resolve()
        except OSError as error:
            raise ValueError("unresolvable document path: " + str(document)) from error
        if resolved == root or root not in resolved.parents:
            raise ValueError("document must stay inside the workspace root: " + str(document))
        return resolved

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def note(self, text, document="notes/session.md"):
        """Append a timestamped session note; creates parent directories."""
        target = self._workspace_path(document)
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n\n## " + stamp + "\n\n" + text.rstrip("\n") + "\n")
        return str(target)

    def ingest(self):
        return self.store.ingest(str(self.root))

    def impact(self, symbol, **kwargs):
        return compiler.compile_impact(self.store, symbol, **kwargs)

    def mentions(self, symbol, **kwargs):
        return compiler.compile_mentions(self.store, symbol, **kwargs)

    def claims(self, symbol, **kwargs):
        return compiler.compile_claims(self.store, symbol, **kwargs)

    def related(self, symbol, **kwargs):
        return compiler.compile_related(self.store, symbol, **kwargs)

    def timeline(self, symbol, **kwargs):
        return compiler.compile_timeline(self.store, symbol, **kwargs)

    def draft(self, document, **kwargs):
        return compiler.compile_drafts(self.store, document, **kwargs)

    def answer(self, task, *, snapshot=None, limit=3):
        """Task-based retrieval without an exact symbol.

        Links bare names in the task to code entities the same way document
        mentions do, then merges the cross-source evidence per candidate.
        Exact keys outrank bare names; nothing is asserted or traversed
        beyond what the compiler already proves.
        """
        import re
        snapshot, view = self.store.view(snapshot)
        code = [(s["key"], s["name"].split(".")[-1])
                for s in view["symbols"] if not s["key"].endswith("::document")]
        scored = {}
        for key, leaf in code:
            if key in task:
                scored[key] = (2, "exact")
            elif len(leaf) >= 2 and re.search(r"\b" + re.escape(leaf) + r"\b", task,
                                              flags=re.IGNORECASE):
                scored.setdefault(key, (1, "name"))
        top = sorted(scored, key=lambda key: (-scored[key][0], key))[:max(limit, 0)]
        impacts = {}
        mentions = []
        claims = []
        for symbol in top:
            related = compiler.compile_related(self.store, symbol, snapshot=snapshot)
            for entry in related["impact"]["impacts"]:
                impacts.setdefault(entry["symbol"], entry)
            mentions.extend(related["mentions"]["mentions"])
            claims.extend(related["claims"]["claims"])
        mentions.sort(key=lambda m: (m["evidence"]["path"], m["evidence"]["start"], m["status"]))
        claims.sort(key=lambda c: (c["relation"], c["caller"], c["callee"] or ""))
        return {"task": task, "snapshot": snapshot,
                "candidates": [{"symbol": key, "score": scored[key][0],
                                "match": scored[key][1]} for key in top],
                "impacts": [impacts[key] for key in sorted(impacts)],
                "mentions": mentions, "claims": claims,
                "outcome": "complete" if top else "empty"}

    def record_outcome(self, subject, object, rel="depends_on", effective=None,
                       authority="agent-outcome", document="notes/outcomes.md", note=None):
        """Append an explicit outcome claim block; ingest separately to publish.

        The claim is asserted text, never an observation. Only exact symbol
        keys are accepted: bare names stay proposals, never merges.
        """
        if rel not in CLAIM_RELATIONS:
            raise ValueError("unknown relation: " + str(rel))
        for ref in (subject, object):
            if not isinstance(ref, str) or "::" not in ref:
                raise ValueError("outcome endpoints must be exact path::symbol keys: " + repr(ref))
        if effective is None:
            effective = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not is_iso_date(effective):
            raise ValueError("effective must be an ISO YYYY-MM-DD date")
        claim = {"rel": rel, "subject": subject, "object": object,
                 "effective": effective, "authority": authority}
        if note is not None:
            claim["note"] = note
        block = ("# Recorded outcome\n\n```tunic-claims\n"
                 + json.dumps([claim], ensure_ascii=True) + "\n```\n")
        target = self._workspace_path(document)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n\n" + block)
        return block
