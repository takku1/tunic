"""same_as proposals: blocking, multi-signal scoring, governed promotion."""

import tempfile
import unittest
from pathlib import Path

from tunic import resolve
from tunic.memory import Memory

FILES = {
    "a.py": "def get_user(uid):\n    return uid\n",
    "b.py": "def GetUser(uid):\n    return uid\n",
    "c.py": "def get_order(oid):\n    return oid\n",
    "d.py": "def user(uid):\n    return fetch(uid)\n",
    "e.py": "from d import user, fetch\nfrom a import get_user\ndef run():\n    return (user(1), get_user(1))\ndef run2():\n    return (user(2), get_user(2))\ndef onlyfetch():\n    return fetch(1)\n",
}


def _ingest(tmp):
    work = Path(tmp) / "ws"
    for name, text in FILES.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp) / "sameas.sqlite"), str(work))
    memory.ingest()
    return memory


class SameAsProposalTest(unittest.TestCase):
    def test_normalized_exact_with_same_dir_proposes(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                _, view = memory.store.view()
                found = resolve.propose_same_as(view)
                pairs = {(c["subject"], c["object"]) for c in found}
                self.assertIn(("a.py::get_user", "b.py::GetUser"),
                              pairs)
            finally:
                memory.close()

    def test_unrelated_names_never_propose(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                _, view = memory.store.view()
                found = resolve.propose_same_as(view)
                pairs = {(c["subject"], c["object"]) for c in found}
                self.assertNotIn(("a.py::get_user",
                                  "c.py::get_order"),
                                 pairs)
            finally:
                memory.close()

    def test_subset_with_shared_caller_proposes(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                _, view = memory.store.view()
                found = resolve.propose_same_as(view)
                pairs = {(c["subject"], c["object"]) for c in found}
                self.assertIn(("a.py::get_user", "d.py::user"),
                              pairs)
            finally:
                memory.close()

    def test_vectors_boost_scoring(self):
        view = {"symbols": [{"key": "a.py::user"},
                            {"key": "b.py::get_account_user"}],
                "assertions": []}
        self.assertEqual(resolve.propose_same_as(view), [])
        boosted = resolve.propose_same_as(
            view, vectors={"a.py::user": [1.0, 0.0],
                           "b.py::get_account_user": [0.95, 0.31]})
        self.assertEqual(len(boosted), 1)
        self.assertTrue(any(signal.startswith("embedding_cosine")
                            for signal in boosted[0]["signals"]))

    def test_typo_alias_found_via_lsh(self):
        # handler/handlers share 5 of 8 MinHash bands under the fixed
        # seeds (verified by probe): the LSH block meets deterministically.
        view = {"symbols": [{"key": "a.py::handler"},
                            {"key": "b.py::handlers"},
                            {"key": "b.py::helper"}],
                "assertions": [
                    {"status": "syntactic_observation",
                     "proposition": {"relation": "static_call",
                                     "caller": "b.py::helper",
                                     "callee": "a.py::handler"}},
                    {"status": "syntactic_observation",
                     "proposition": {"relation": "static_call",
                                     "caller": "b.py::helper",
                                     "callee": "b.py::handlers"}}]}
        found = resolve.propose_same_as(view)
        pairs = {(c["subject"], c["object"]) for c in found}
        self.assertIn(("a.py::handler", "b.py::handlers"), pairs)
        signals = [c["signals"] for c in found
                   if c["subject"] == "a.py::handler"][0]
        self.assertTrue(any(signal.startswith("shingle_jaccard")
                            for signal in signals))

    def test_short_names_need_exact_match(self):
        view = {"symbols": [{"key": "a.py::get"},
                            {"key": "b.py::set"}],
                "assertions": []}
        self.assertEqual(resolve.propose_same_as(view), [])

    def test_low_entropy_names_defer_without_exact(self):
        view = {"symbols": [{"key": "a.py::ab"},
                            {"key": "b.py::ab_cd"},
                            {"key": "c.py::run1"},
                            {"key": "c.py::run2"}],
                "assertions": [
                    {"status": "syntactic_observation",
                     "proposition": {"relation": "static_call",
                                     "caller": "c.py::run1",
                                     "callee": "a.py::ab"}},
                    {"status": "syntactic_observation",
                     "proposition": {"relation": "static_call",
                                     "caller": "c.py::run1",
                                     "callee": "b.py::ab_cd"}},
                    {"status": "syntactic_observation",
                     "proposition": {"relation": "static_call",
                                     "caller": "c.py::run2",
                                     "callee": "a.py::ab"}},
                    {"status": "syntactic_observation",
                     "proposition": {"relation": "static_call",
                                     "caller": "c.py::run2",
                                     "callee": "b.py::ab_cd"}}]}
        self.assertEqual(resolve.propose_same_as(view), [])

    def test_minhash_deterministic(self):
        left = resolve._minhash(resolve._shingles("calculate"))
        right = resolve._minhash(resolve._shingles("calculate"))
        self.assertEqual(left, right)
        self.assertEqual(len(left), resolve.MINHASH_PERMS)

    def test_decided_pairs_excluded(self):
        view = {"symbols": [{"key": "a.py::get_user"},
                            {"key": "b.py::GetUser"}],
                "assertions": [
                    {"status": "asserted_in_source",
                     "proposition": {"relation": "same_as",
                                     "caller": "a.py::get_user",
                                     "callee": "b.py::GetUser"}}]}
        self.assertEqual(resolve.propose_same_as(view), [])

    def test_proposals_are_sorted_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                _, view = memory.store.view()
                found = resolve.propose_same_as(view, limit=1)
                self.assertEqual(len(found), 1)
                scores = [c["score"] for c in
                          resolve.propose_same_as(view)]
                self.assertEqual(scores, sorted(scores, reverse=True))
            finally:
                memory.close()


class SameAsPromotionTest(unittest.TestCase):
    def test_promote_same_as_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                action, _reason = memory.reconcile_proposal(
                    "a.py::get_user", "b.py::GetUser",
                    rel="same_as")
                self.assertEqual(action, "add")
                memory.promote("a.py::get_user",
                               "b.py::GetUser", rel="same_as")
            finally:
                memory.close()

    def test_unknown_relation_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = _ingest(tmp)
            try:
                action, _reason = memory.reconcile_proposal(
                    "a.py::get_user", "b.py::GetUser",
                    rel="likes")
                self.assertEqual(action, "invalid")
                with self.assertRaises(ValueError):
                    memory.promote("a.py::get_user",
                                   "b.py::GetUser", rel="likes")
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
