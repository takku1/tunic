"""Persistent embedding cache keyed by model, chunking, and preprocessing.

Embedding calls are the most expensive part of semantic retrieval: without
a cache, every query re-embeds the query plus every candidate. The cache
stores one row per (model digest, chunking version, preprocessing version,
text hash), so a changed model, chunker, or normalizer can never serve
stale vectors. Similarity still only proposes candidates; cached or not,
vectors never become graph evidence.
"""

import hashlib
import json
import sqlite3

from .retrieval import EmbeddingProvider

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors(
    key TEXT PRIMARY KEY, vector TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """SQLite-backed vector store with hit/miss accounting."""

    def __init__(self, path, *, model_digest, chunking_version="chunk-v1",
                 preprocessing_version="pre-v1"):
        if not isinstance(model_digest, str) or not model_digest:
            raise ValueError("model_digest must be a non-empty string")
        self.model_digest = model_digest
        self.chunking_version = chunking_version
        self.preprocessing_version = preprocessing_version
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(SCHEMA)
        self.hits = 0
        self.misses = 0

    def close(self):
        self.connection.close()

    def _key(self, digest):
        return "|".join((self.model_digest, self.chunking_version,
                         self.preprocessing_version, digest))

    def get_many(self, texts):
        """Split texts into cached vectors and cache misses, in order."""
        digests = [text_hash(text) for text in texts]
        rows = self.connection.execute(
            "SELECT key, vector FROM vectors WHERE key IN (%s)" % ",".join("?" * len(digests)),
            [self._key(digest) for digest in digests]).fetchall() if digests else []
        cached = {key: json.loads(vector) for key, vector in rows}
        found, missing = {}, []
        for text, digest in zip(texts, digests):
            vector = cached.get(self._key(digest))
            if vector is None:
                missing.append(text)
            else:
                found[text] = vector
        self.hits += len(found)
        self.misses += len(missing)
        return found, missing

    def put_many(self, pairs):
        """Store {text: vector} pairs under the current key versions."""
        with self.connection:
            self.connection.executemany(
                "INSERT OR REPLACE INTO vectors VALUES (?,?)",
                [(self._key(text_hash(text)), json.dumps(vector))
                 for text, vector in pairs.items()])

    def stats(self):
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": self.hits / total if total else None,
                "model_digest": self.model_digest,
                "chunking_version": self.chunking_version,
                "preprocessing_version": self.preprocessing_version}


class CachedEmbeddingProvider(EmbeddingProvider):
    """Wraps a real provider with a persistent vector cache.

    Identical rankings to the inner provider: the cache only avoids
    re-embedding text it has already seen under identical key versions.
    """

    def __init__(self, inner, cache):
        if not isinstance(inner, EmbeddingProvider):
            raise ValueError("inner must be an EmbeddingProvider")
        if not isinstance(cache, EmbeddingCache):
            raise ValueError("cache must be an EmbeddingCache")
        self.inner = inner
        self.cache = cache
        self.name = inner.name + "+cache"

    def available(self):
        return self.inner.available()

    def embed_texts(self, texts):
        found, missing = self.cache.get_many(list(texts))
        if missing:
            fresh = self.inner.embed_texts(missing)
            self.cache.put_many(dict(zip(missing, fresh)))
            found.update(zip(missing, fresh))
        return [found[text] for text in texts]
