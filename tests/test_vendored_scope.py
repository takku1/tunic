"""Vendored scope policy v1: failing-first contract.

Scope precedence: built-in defaults < .gitignore < repository config
(.tunic.toml [scope]: vendored extra excludes, first_party re-includes)
< per-operation --exclude. Effective scope (with policy source and
fingerprint) is visible in ingest/refresh reports and discovery
receipts. Publication stays single-writer; config is re-read per
operation, never persisted. No channel leaks excluded content.
"""

import os
import tempfile
import unittest
from pathlib import Path

from tunic.extract import _scope_fingerprint, resolve_scope
from tunic.memory import Memory

TUNIC_TOML = """[scope]
vendored = ["vendored/**", ".claude/skills/ext/**"]
first_party = [".claude/skills/own/"]
"""

GUIDE_MD = "# Guide\n\nThe handler does the work.\n"
NOISE_MD = "rate limiting configured everywhere in this vendored copy.\n"
OWN_SKL = "# Own skill\n\nFirst-party helper notes.\n"
EXT_SKL = "# Ext skill\n\nVendored skill notes.\n"
APP_TS = "export const onRequest = 1;\n"


def _build_policy(root, gitignore="special/\n", config=TUNIC_TOML):
    root = Path(root)
    (root / "vendored").mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "special").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "skills" / "own").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "skills" / "ext").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "guide.md").write_text(GUIDE_MD, encoding="utf-8")
    (root / "vendored" / "noise.md").write_text(NOISE_MD, encoding="utf-8")
    (root / "special" / "keep.md").write_text("Kept.\n", encoding="utf-8")
    (root / ".claude" / "skills" / "own" / "SKILL.md").write_text(
        OWN_SKL, encoding="utf-8")
    (root / ".claude" / "skills" / "ext" / "SKILL.md").write_text(
        EXT_SKL, encoding="utf-8")
    (root / "src" / "app.ts").write_text(APP_TS, encoding="utf-8")
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    if config is not None:
        (root / ".tunic.toml").write_text(config, encoding="utf-8")


def _ingest(tmp, **kwargs):
    work = Path(tmp) / "ws"
    _build_policy(work)
    memory = Memory(str(Path(tmp) / "p.sqlite"), str(work))
    report = memory.ingest(**kwargs)
    return memory, report, str(work)


class ConfigPolicyTest(unittest.TestCase):
    def test_vendored_excluded_first_party_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, report, _work = _ingest(tmp)
            try:
                _snapshot, view = memory.store.view()
                paths = {s["path"] for s in view["symbols"]}
                self.assertNotIn("vendored/noise.md", paths)
                self.assertNotIn(".claude/skills/ext/SKILL.md", paths)
                self.assertIn(".claude/skills/own/SKILL.md", paths)
                self.assertIn("docs/guide.md", paths)
                scope = report.get("scope", {})
                self.assertEqual(scope.get("config_file"), ".tunic.toml")
                self.assertIn("vendored/**", scope.get("vendored", []))
                self.assertTrue(scope.get("fingerprint"))
                _rules, policy = resolve_scope(_work, ())
                self.assertEqual(policy["fingerprint"],
                                 scope["fingerprint"])
            finally:
                memory.close()

    def test_first_party_beats_gitignore_with_parent_reinclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_policy(work, gitignore="special/\n",
                           config="[scope]\nfirst_party = [\"special/\","
                                  " \"special/keep.md\"]\n")
            memory = Memory(str(Path(tmp) / "p.sqlite"), str(work))
            memory.ingest()
            try:
                _snapshot, view = memory.store.view()
                paths = {s["path"] for s in view["symbols"]}
                self.assertIn("special/keep.md", paths)
            finally:
                memory.close()

    def test_cli_exclude_beats_config_first_party(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_policy(work, gitignore="",
                           config="[scope]\nfirst_party = [\"special/\"]\n")
            memory = Memory(str(Path(tmp) / "p.sqlite"), str(work))
            memory.ingest(exclude=["special"])
            try:
                _snapshot, view = memory.store.view()
                paths = {s["path"] for s in view["symbols"]}
                self.assertNotIn("special/keep.md", paths)
            finally:
                memory.close()

    def test_bad_config_fails_closed(self):
        cases = ("[scope]\nunknown_key = [\"x\"]\n",
                 "[scope]\nvendored = \"no-list\"\n",
                 "[scope]\nfirst_party = [\"!nope\"]\n",
                 "[scope]\nvendored = [\"/absolute\"]\n",
                 "[scope\nbroken toml\n")
        for index, text in enumerate(cases):
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "ws"
                _build_policy(work, config=text)
                memory = Memory(str(Path(tmp) / ("p%d.sqlite" % index)),
                                str(work))
                try:
                    with self.assertRaises(ValueError, msg=text):
                        memory.ingest()
                finally:
                    memory.close()

    def test_fingerprint_changes_with_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_policy(work, config=None)
            plain = _scope_fingerprint(str(work), ())
            (work / ".tunic.toml").write_text(TUNIC_TOML, encoding="utf-8")
            from tunic.extract import load_scope_config
            _config, config_bytes, _file = load_scope_config(str(work))
            keyed = _scope_fingerprint(str(work), (), config_bytes)
            self.assertNotEqual(plain, keyed)
            self.assertEqual(keyed, _scope_fingerprint(
                str(work), (), config_bytes))

    def test_no_config_means_old_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "ws"
            _build_policy(work, gitignore="", config=None)
            memory = Memory(str(Path(tmp) / "p.sqlite"), str(work))
            report = memory.ingest()
            try:
                scope = report.get("scope", {})
                self.assertIsNone(scope.get("config_file"))
                self.assertEqual(scope.get("vendored"), [])
                self.assertEqual(scope.get("first_party"), [])
            finally:
                memory.close()


class ScopeLeakTest(unittest.TestCase):
    def test_no_channel_leaks_vendored_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, _work = _ingest(tmp)
            try:
                result = memory.answer(
                    "rate limiting configured vendored skill notes",
                    select=True)
                leaked = [d for d in result.get("documents", [])
                          if d["doc"].startswith("vendored/")
                          or "/ext/" in d["doc"]]
                self.assertEqual(leaked, [])
                exact = memory.answer(
                    "vendored/noise.md::document", select=True)
                self.assertFalse([d for d in exact.get("documents", [])
                                  if d["doc"] == "vendored/noise.md"])
                with self.assertRaises(ValueError):
                    memory.related("vendored/noise.md::document")
                with self.assertRaises(ValueError):
                    memory.review("vendored/noise.md")
            finally:
                memory.close()


class ScopeLifecycleTest(unittest.TestCase):
    def test_refresh_applies_config_change_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, first, work = _ingest(tmp)
            try:
                before = memory.store.current()
                os.remove(Path(work) / ".tunic.toml")
                result = memory.refresh()
                after = memory.store.current()
                self.assertNotEqual(before, after)
                _snapshot, view = memory.store.view(before)
                old_paths = {s["path"] for s in view["symbols"]}
                self.assertNotIn("vendored/noise.md", old_paths,
                                 "old snapshot gained vendored content")
                _snapshot, view = memory.store.view()
                new_paths = {s["path"] for s in view["symbols"]}
                self.assertIn("vendored/noise.md", new_paths,
                              "config removal did not widen scope")
                self.assertIn("scope", result)
            finally:
                memory.close()

    def test_reopen_repeats_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory, _report, work = _ingest(tmp)
            first = memory.store.current()
            memory.close()
            reopened = Memory(str(Path(tmp) / "p.sqlite"), work)
            try:
                self.assertEqual(reopened.store.current(), first)
                again = reopened.ingest()
                self.assertEqual(again["snapshot"], first)
                self.assertEqual(again["scope"]["fingerprint"],
                                 _report["scope"]["fingerprint"])
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
