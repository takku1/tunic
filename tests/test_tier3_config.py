"""Tier-3: structured config as key-path entities (JSON/YAML/TOML/INI).

Covers entity keys/values/byte-exact spans, cross-file $ref claims,
YAML anchors/merges, lockfile deferral, duplicate/missing/broken
abstention, doc mentions of config keys, and refresh re-resolution.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tunic.memory import Memory

JSON_DOC = {
    "server": {"host": "db.local", "port": 8080},
    "routes": ["/a", "/b"],
    "$ref": "db.json#/server/host",
    "dup": 1,
}

FILES = {
    "app.json": json.dumps(JSON_DOC, indent=2)[:-2] + ',\n  "dup": 2\n}',
    "db.json": json.dumps({"server": {"host": "primary"}}),
    "svc.yaml": "\n".join([
        "server:",
        "  host: yaml.local",
        "base: &b",
        "  port: 1",
        "copy:",
        "  <<: *b",
        "  extra: 2",
        "alias_of_base: *b",
        "",
    ]),
    "conf.toml": "\n".join([
        'title = "svc"',
        "[server]",
        'host = "toml.local"',
        "port = 9090",
        "[db]",
        '"$ref" = "db.json#/server/host"',
        "point = {x = 1, y = 2}",
        "ports = [80, 443]",
    ]),
    "app.ini": "\n".join([
        "[server]",
        "host = ini.local",
        "multi = one",
        "  two",
        "[server]",
        "host = again",
    ]),
    "pkg/package-lock.json": json.dumps({"name": "x", "lockfileVersion": 3}),
    "broken.json": '{"server": ',
    "missing.json": json.dumps({"$ref": "nope.json#/a"}),
    "notes.md": "Endpoint configured at app.json::server.host for rollout.\n",
}


def _ingest(files):
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name) / "ws"
    for name, text in files.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    memory = Memory(str(Path(tmp.name) / "t.sqlite"), str(work))
    memory.ingest()
    _snapshot, view = memory.store.view()
    memory.close()
    tmp.cleanup()
    return view


def _edges(view, relation):
    return {(a["proposition"]["caller"], a["proposition"]["callee"])
            for a in view["assertions"]
            if a["proposition"].get("relation") == relation}


class ConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = _ingest(FILES)
        cls.symbols = {s["key"]: s for s in cls.view["symbols"]}
        cls.raw = {name: text.replace("\n", "\r\n").encode("utf-8")
                   for name, text in FILES.items()}

    def test_json_entities(self):
        self.assertEqual(
            self.symbols["app.json::server.host"]["value"], "db.local")
        self.assertEqual(
            self.symbols["app.json::server.port"]["value"], 8080)
        self.assertEqual(
            self.symbols["app.json::routes[1]"]["value"], "/b")
        self.assertIsNone(self.symbols["app.json::server"]["value"])

    def test_json_spans_are_key_exact(self):
        raw = self.raw["app.json"]
        for key, token in (("app.json::server.host", b'"host"'),
                           ("app.json::routes", b'"routes"')):
            symbol = self.symbols[key]
            self.assertEqual(raw[symbol["start"]:symbol["end"]], token)

    def test_json_duplicate_first_wins(self):
        self.assertEqual(self.symbols["app.json::dup"]["value"], 1)
        dups = [d for d in self.view["diagnostics"]
                if d.get("kind") == "duplicate_key"
                and d.get("path") == "app.json"]
        self.assertTrue(dups)

    def test_json_ref_edge(self):
        edges = _edges(self.view, "depends_on")
        self.assertIn(("app.json::$ref", "db.json::server.host"),
                      edges)
        edge = [a for a in self.view["assertions"]
                if a["proposition"].get("caller") == "app.json::$ref"
                and a["proposition"].get("relation") == "depends_on"][0]
        self.assertEqual(edge["producer"], "key_path_v1")
        self.assertEqual(edge["status"], "syntactic_observation")

    def test_yaml_entities_and_merge(self):
        self.assertEqual(
            self.symbols["svc.yaml::server.host"]["value"], "yaml.local")
        edges = _edges(self.view, "depends_on")
        self.assertIn(("svc.yaml::copy", "svc.yaml::base"), edges)
        self.assertIn(("svc.yaml::alias_of_base", "svc.yaml::base"),
                      edges)

    def test_toml_entities(self):
        self.assertEqual(
            self.symbols["conf.toml::server.port"]["value"], 9090)
        self.assertEqual(
            self.symbols["conf.toml::db.point.x"]["value"], 1)
        self.assertEqual(
            self.symbols["conf.toml::db.ports[0]"]["value"], 80)
        edges = _edges(self.view, "depends_on")
        self.assertIn(("conf.toml::db.$ref", "db.json::server.host"),
                      edges)

    def test_ini_entities(self):
        self.assertEqual(
            self.symbols["app.ini::server.host"]["value"], "ini.local")
        self.assertEqual(
            self.symbols["app.ini::server.multi"]["value"], "one\ntwo")
        dups = [d for d in self.view["diagnostics"]
                if d.get("kind") == "duplicate_key"
                and d.get("path") == "app.ini"]
        self.assertTrue(dups)

    def test_lockfile_deferred(self):
        self.assertFalse([k for k in self.symbols
                          if k.startswith("pkg/package-lock.json::")])
        deferred = [d for d in self.view["diagnostics"]
                    if d.get("kind") == "inventory_deferred"]
        self.assertTrue(deferred)

    def test_broken_abstains(self):
        self.assertFalse([k for k in self.symbols
                          if k.startswith("broken.json::")])
        errors = [d for d in self.view["diagnostics"]
                  if d.get("path") == "broken.json"
                  and d.get("kind") == "parse_error"]
        self.assertTrue(errors)

    def test_missing_target_abstains(self):
        edges = _edges(self.view, "depends_on")
        self.assertFalse([e for e in edges
                          if e[0] == "missing.json::$ref"])
        diags = [d for d in self.view["diagnostics"]
                 if d.get("path") == "missing.json"
                 and d.get("kind") == "unresolved_ref"]
        self.assertTrue(diags)

    def test_symbol_receipt(self):
        for key in ("app.json::server.host", "svc.yaml::server.host",
                    "conf.toml::server.port", "app.ini::server.host"):
            self.assertEqual(self.symbols[key]["producer"],
                             "key_path_v1")

    def test_doc_mentions_config_key(self):
        mentions = [(a["proposition"]["caller"],
                     a["proposition"]["callee"])
                    for a in self.view["assertions"]
                    if a["proposition"].get("relation") == "text_mention"
                    and a["proposition"].get("callee")
                    == "app.json::server.host"]
        self.assertIn(("notes.md::document", "app.json::server.host"),
                      mentions)

    def test_refresh_reresolves(self):
        tmp = tempfile.TemporaryDirectory()
        work = Path(tmp.name) / "ws"
        (work / "one.json").parent.mkdir(parents=True, exist_ok=True)
        (work / "one.json").write_text(
            json.dumps({"$ref": "two.json#/v"}), encoding="utf-8")
        (work / "two.json").write_text(json.dumps({"v": 1}),
                                       encoding="utf-8")
        memory = Memory(str(Path(tmp.name) / "t.sqlite"), str(work))
        memory.ingest()
        _snapshot, view = memory.store.view()
        before = {(a["proposition"]["caller"],
                   a["proposition"]["callee"])
                  for a in view["assertions"]
                  if a["proposition"].get("relation") == "depends_on"}
        self.assertIn(("one.json::$ref", "two.json::v"), before)
        (work / "two.json").write_text(json.dumps({"w": 1}),
                                       encoding="utf-8")
        memory.refresh()
        _snapshot, view = memory.store.view()
        after = {(a["proposition"]["caller"],
                  a["proposition"]["callee"])
                 for a in view["assertions"]
                 if a["proposition"].get("relation") == "depends_on"}
        self.assertNotIn(("one.json::$ref", "two.json::v"), after)
        diags = [d for d in view["diagnostics"]
                 if d.get("kind") == "unresolved_ref"]
        self.assertTrue(diags)
        memory.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
