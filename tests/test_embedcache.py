"""Persistent embedding cache: identical rankings, no redundant model calls."""

import math
import shutil
import tempfile
import unittest
from pathlib import Path

from tunic.embedcache import CachedEmbeddingProvider, EmbeddingCache, text_hash
from tunic.retrieval import EmbeddingProvider


class CountingProvider(EmbeddingProvider):
    """Deterministic test double: one-hot vectors by text length."""

    name = "counting"

    def __init__(self):
        self.calls = 0

    def available(self):
        return True

    def embed_texts(self, texts):
        self.calls += 1
        return [[1.0 if i == len(text) % 8 else 0.0 for i in range(8)]
                for text in texts]


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _cached(self, **kwargs):
        inner = CountingProvider()
        params = {"model_digest": "sha256:test", "chunking_version": "chunk-v1",
                  "preprocessing_version": "pre-v1"}
        params.update(kwargs)
        cache = EmbeddingCache(str(self.tmp / "vectors.sqlite"), **params)
        self.addCleanup(cache.close)
        return inner, CachedEmbeddingProvider(inner, cache)

    def test_second_run_serves_from_cache(self):
        inner, provider = self._cached()
        first = provider.rank("query text", ["alpha", "beta gamma"], 2)
        calls_after_first = inner.calls
        self.assertGreater(calls_after_first, 0)
        second = provider.rank("query text", ["alpha", "beta gamma"], 2)
        self.assertEqual(first, second)
        self.assertEqual(inner.calls, calls_after_first)
        stats = provider.cache.stats()
        self.assertEqual(stats["misses"], 3)
        self.assertEqual(stats["hits"], 3)
        self.assertAlmostEqual(stats["hit_rate"], 0.5)

    def test_cache_survives_reopen(self):
        inner, provider = self._cached()
        provider.rank("query text", ["alpha"], 1)
        calls = inner.calls
        provider.cache.close()
        inner2, provider2 = self._cached()
        provider2.rank("query text", ["alpha"], 1)
        self.assertEqual(inner2.calls, 0)
        self.assertEqual(calls, 1)

    def test_model_digest_change_invalidates(self):
        _, provider = self._cached()
        provider.rank("query text", ["alpha"], 1)
        _, other = self._cached(model_digest="sha256:other")
        self.assertEqual(other.cache.stats()["hits"], 0)
        other.rank("query text", ["alpha"], 1)
        self.assertEqual(other.cache.stats()["misses"], 2)

    def test_cached_rank_matches_live_rank(self):
        inner, provider = self._cached()
        live = inner.rank("query text", ["alpha", "beta gamma", "delta"], 3)
        provider.cache.close()
        inner2, provider2 = self._cached()
        provider2.embed_texts(["query text", "alpha", "beta gamma", "delta"])
        self.assertEqual(provider2.rank("query text",
                                        ["alpha", "beta gamma", "delta"], 3),
                         live)
        self.assertEqual(inner2.calls, 1)

    def test_text_hash_is_stable_hex(self):
        digest = text_hash("abc")
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, text_hash("abc"))
        self.assertNotEqual(digest, text_hash("abd"))
        self.assertTrue(all(c in "0123456789abcdef" for c in digest))

    def test_unavailable_inner_stays_unavailable(self):
        _, provider = self._cached()
        provider.inner = EmbeddingProvider()
        self.assertFalse(provider.available())
        with self.assertRaises(NotImplementedError):
            provider.rank("q", ["a"], 1)


if __name__ == "__main__":
    unittest.main()
