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

    def test_duplicates_count_every_occurrence(self):
        inner, provider = self._cached()
        provider.embed_texts(["alpha", "alpha", "beta"])
        self.assertEqual(inner.calls, 1)
        self.assertEqual(provider.cache.stats()["misses"], 3)
        provider.embed_texts(["alpha", "alpha", "beta"])
        self.assertEqual(inner.calls, 1)
        self.assertEqual(provider.cache.stats()["hits"], 3)

    def test_large_batches_stay_under_variable_limit(self):
        inner, provider = self._cached()
        texts = ["text-%d" % i for i in range(1200)]
        provider.embed_texts(texts)
        self.assertEqual(inner.calls, 1)
        again = provider.embed_texts(list(reversed(texts)))
        self.assertEqual(inner.calls, 1)
        self.assertEqual(len(again), 1200)

    def test_corrupt_rows_are_misses_not_crashes(self):
        _, provider = self._cached()
        provider.embed_texts(["alpha"])
        key = provider.cache._key(text_hash("alpha"))
        provider.cache.connection.execute(
            "UPDATE vectors SET vector='{\"broken\"' WHERE key=?", (key,))
        provider.cache.connection.commit()
        found, missing = provider.cache.get_many(["alpha"])
        self.assertEqual(found, {})
        self.assertEqual(missing, ["alpha"])
        provider.embed_texts(["alpha"])
        found, _ = provider.cache.get_many(["alpha"])
        self.assertEqual(len(found["alpha"]), 8)

    def test_bad_vectors_rejected_on_write(self):
        _, provider = self._cached()
        with self.assertRaises(ValueError):
            provider.cache.put_many({"x": ["a", "b"]})
        with self.assertRaises(ValueError):
            provider.cache.put_many({"x": []})
        with self.assertRaises(ValueError):
            provider.cache.put_many({"x": [1.0, True]})
        self.assertEqual(provider.cache.get_many(["x"]), ({}, ["x"]))

    def test_unavailable_inner_stays_unavailable(self):
        _, provider = self._cached()
        provider.inner = EmbeddingProvider()
        self.assertFalse(provider.available())
        with self.assertRaises(NotImplementedError):
            provider.rank("q", ["a"], 1)

    def test_poisonous_text_does_not_nuke_batch(self):
        class Flaky(CountingProvider):
            def embed_texts(self, texts):
                if len(texts) > 1 and any(t == "poison" for t in texts):
                    raise RuntimeError("batch exploded")
                if texts == ["poison"]:
                    raise RuntimeError("poison exploded")
                self.calls += 1
                return [[1.0 if i == len(t) % 8 else 0.0 for i in range(8)]
                        for t in texts]

        inner = Flaky()
        cache = EmbeddingCache(str(self.tmp / "v.sqlite"), model_digest="sha256:t",
                               chunking_version="chunk-v1",
                               preprocessing_version="pre-v1")
        self.addCleanup(cache.close)
        provider = CachedEmbeddingProvider(inner, cache)
        vectors = provider.embed_texts(["alpha", "beta"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(provider.cache.get_many(["alpha", "beta"])[1], [])
        with self.assertRaises(RuntimeError):
            provider.embed_texts(["alpha", "poison"])
        found, missing = provider.cache.get_many(["poison"])
        self.assertEqual((found, missing), ({}, ["poison"]))


if __name__ == "__main__":
    unittest.main()
