"""Tier-4: schema-first reference edges (SQL/GraphQL/Protobuf).

Covers entity keys/byte-exact spans, FK/field/implements/union/rpc
references, missing/duplicate/extend abstention, doc mentions of
schema keys, claims visibility, and impact isolation of the
references relation.
"""

import tempfile
import unittest
from pathlib import Path

from tunic.extract import TRAVERSABLE_CLAIMS
from tunic.memory import Memory

FILES = {
    "schema.sql": "\n".join([
        "-- shop schema",
        "CREATE TABLE orgs (",
        "  id INTEGER PRIMARY KEY,",
        "  name TEXT",
        ");",
        "CREATE TABLE users (",
        "  id INTEGER PRIMARY KEY,",
        "  org_id INT REFERENCES orgs(id),",
        "  team_id INT,",
        "  CONSTRAINT fk_team FOREIGN KEY (team_id) REFERENCES teams(id)",
        ");",
        "CREATE TABLE teams (",
        "  id INTEGER PRIMARY KEY",
        ");",
        "CREATE TABLE dangling (",
        "  x INT REFERENCES ghosts(id)",
        ");",
        "CREATE VIEW names AS SELECT name FROM users;",
    ]),
    "api.graphql": "\n".join([
        "type User implements Node {",
        "  id: ID!",
        "  org: Org",
        "}",
        "type Org implements Node {",
        "  id: ID!",
        "}",
        "interface Node {",
        "  id: ID!",
        "}",
        "union Feed = User | Org",
        "enum Role { ADMIN }",
        "scalar Date",
        "type Broken {",
        "  again: Missing",
        "}",
    ]),
    "svc.proto": "\n".join([
        'syntax = "proto3";',
        "message AppUser {",
        "  string name = 1;",
        "  AppOrg org = 2;",
        "  map<string, Team> teams = 3;",
        "  message Inner { int32 x = 1; }",
        "}",
        "message AppOrg { string id = 1; }",
        "message Team { string id = 1; }",
        "service Svc {",
        "  rpc Get(AppUser) returns (AppOrg);",
        "}",
    ]),
    "notes.md": "See schema.sql::users.org_id for the tenancy join.\n",
}


def _ingest(files):
    tmp = tempfile.TemporaryDirectory()
    work = Path(tmp.name) / "ws"
    for name, text in files.items():
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    raws = {name: (work / name).read_bytes() for name in files}
    memory = Memory(str(Path(tmp.name) / "t.sqlite"), str(work))
    memory.ingest()
    _snapshot, view = memory.store.view()
    memory.close()
    tmp.cleanup()
    return view, raws


def _edges(view):
    return {(a["proposition"]["caller"], a["proposition"]["callee"],
             a["proposition"]["relation"])
            for a in view["assertions"]}


def _producers(view, relation):
    return {a.get("producer")
            for a in view["assertions"]
            if a["proposition"].get("relation") == relation}


class SchemaRefsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view, cls.raw = _ingest(FILES)
        cls.symbols = {s["key"]: s for s in cls.view["symbols"]}
        cls.edges = _edges(cls.view)

    def test_sql_entities(self):
        for key in ("schema.sql::users", "schema.sql::users.org_id",
                    "schema.sql::orgs", "schema.sql::orgs.id"):
            self.assertIn(key, self.symbols)

    def test_sql_fk_edges(self):
        self.assertIn(("schema.sql::users.org_id",
                       "schema.sql::orgs.id", "references"), self.edges)
        self.assertIn(("schema.sql::users.team_id",
                       "schema.sql::teams.id", "references"), self.edges)

    def test_sql_spans_are_name_exact(self):
        raw = self.raw["schema.sql"]
        symbol = self.symbols["schema.sql::users.org_id"]
        self.assertEqual(raw[symbol["start"]:symbol["end"]], b"org_id")

    def test_sql_missing_target_abstains(self):
        self.assertFalse([e for e in self.edges
                          if e[0] == "schema.sql::dangling.x"])
        diags = [d for d in self.view["diagnostics"]
                 if d.get("kind") == "unresolved_ref"
                 and d.get("path") == "schema.sql"]
        self.assertTrue(diags)

    def test_gql_entities_and_refs(self):
        self.assertIn("api.graphql::User.org", self.symbols)
        self.assertIn(("api.graphql::User.org", "api.graphql::Org",
                       "references"), self.edges)
        self.assertIn(("api.graphql::User", "api.graphql::Node",
                       "references"), self.edges)
        self.assertIn(("api.graphql::Feed", "api.graphql::User",
                       "references"), self.edges)
        self.assertIn("api.graphql::Role.ADMIN", self.symbols)

    def test_gql_missing_type_abstains(self):
        self.assertFalse([e for e in self.edges
                          if e[0] == "api.graphql::Broken.again"])
        diags = [d for d in self.view["diagnostics"]
                 if d.get("kind") == "unresolved_ref"
                 and d.get("path") == "api.graphql"]
        self.assertTrue(diags)

    def test_proto_entities_and_refs(self):
        self.assertIn("svc.proto::AppUser.Inner.x", self.symbols)
        self.assertIn(("svc.proto::AppUser.org", "svc.proto::AppOrg",
                       "references"), self.edges)
        self.assertIn(("svc.proto::AppUser.teams", "svc.proto::Team",
                       "references"), self.edges)
        self.assertIn(("svc.proto::Svc.Get", "svc.proto::AppUser",
                       "references"), self.edges)
        self.assertIn(("svc.proto::Svc.Get", "svc.proto::AppOrg",
                       "references"), self.edges)

    def test_receipt(self):
        self.assertEqual(_producers(self.view, "references"),
                         {"schema_reference_v1"})
        self.assertEqual(self.symbols["schema.sql::users"]["producer"],
                         "schema_reference_v1")

    def test_references_never_traverse(self):
        self.assertNotIn("references", TRAVERSABLE_CLAIMS)
        statuses = {a.get("status")
                    for a in self.view["assertions"]
                    if a["proposition"].get("relation") == "references"}
        self.assertEqual(statuses, {"syntactic_observation"})

    def test_doc_mentions_schema_key(self):
        mentions = [(a["proposition"]["caller"],
                     a["proposition"]["callee"])
                    for a in self.view["assertions"]
                    if a["proposition"].get("relation") == "text_mention"
                    and a["proposition"].get("callee")
                    == "schema.sql::users.org_id"]
        self.assertIn(("notes.md::document",
                       "schema.sql::users.org_id"), mentions)


if __name__ == "__main__":
    unittest.main()
