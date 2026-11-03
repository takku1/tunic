"""Candidate syntax-adapter generator (derivation experiment).

Reads a pinned tree-sitter node-types.json and emits the six
syntax-mappable spec fields (def/call/container/import/literal/bind
node sets) using narrow structural rules. Semantic policy -- scope
units, call shapes, receivers, aliases, macros, proof eligibility --
stays hand-reviewed by design and is NOT generated.

Rules (each documented with its failure mode):
- def: node has a `name` field AND (`body` or `parameters` field), or a
  `declarator` field whose hidden-supertype expansion reaches
  `function_declarator` (C-style out-of-line definitions) AND a `body`
  field (excludes bare declarator uses without implementations).
- container: `body` field + container-ish name (class/interface/enum/
  record/struct/trait/impl/module/namespace/object/union) + name not
  ending in method/function/constructor/constant/variant/field/statement.
- call: `arguments` field, minus ctor-matched nodes.
- ctor: name matches new_expression/object_creation/
  explicit_constructor/compact_constructor/constructor_declaration$.
- literal: name matches lambda/closure/arrow.
- import: (import|use)_declaration/preproc_include, or the documented
  per-language `add` overrides (e.g. Rust `mod_item`: `mod foo;` has no
  syntactic import marker; a generic optional-body rule over-fired, so
  it stays an explicit override -- see report).

Usage:
    python generate.py <node-types.json> <lang> <overrides.json> <out.json>

Outputs a JSON artifact with provenance (grammar tag, generator
version) and the six assembled field sets (generated - trim + add).
Deterministic: same inputs, same bytes.
"""

import json
import re
import sys

GENERATOR_VERSION = "derive-v1"

DEF_NAME = re.compile(r"(declaration|definition)$")
CONTAINER_NAME = re.compile(
    r"(class|interface|enum|record|struct|trait|impl|module|namespace|object|union)")
NOT_CONTAINER = re.compile(
    r"(method|function|constructor|constant|variant|field|statement)$")
CALL_ARGS = "arguments"
CTOR_NAME = re.compile(r"(new_expression|object_creation|explicit_constructor|"
                       r"compact_constructor|constructor_declaration$)")
LITERAL_NAME = re.compile(r"(lambda|closure|arrow)")
IMPORT_NAME = re.compile(r"((import|use)_declaration|preproc_include)$")

FIELD_KEYS = ("def_types", "class_types", "call_types", "ctor_types",
              "func_literal_types", "import_types")


def fields_of(node):
    return node.get("fields", {})


def field_type_names(node, field):
    try:
        return {t.get("type", "") for t in node["fields"][field]["types"]}
    except (KeyError, TypeError):
        return set()


def expand_subtypes(all_nodes, type_names):
    """Follow hidden supertype rules to concrete named subtypes."""
    seen, out = set(type_names), set()
    while seen:
        current = seen.pop()
        if current in out:
            continue
        out.add(current)
        for node in all_nodes:
            if node["type"] == current:
                for sub in node.get("subtypes", []) or []:
                    if sub.get("named"):
                        seen.add(sub["type"])
            for sub in node.get("subtypes", []) or []:
                if sub.get("type") == current and sub.get("named"):
                    seen.add(node["type"])
    return out


def generate(nodes_data):
    nodes = {n["type"]: n for n in nodes_data if n.get("named")}
    raw = nodes_data
    def_types, class_types = set(), set()
    for name, node in nodes.items():
        fields = fields_of(node)
        if "declarator" in fields and "body" in fields \
                and "function_declarator" in expand_subtypes(
                    raw, field_type_names(node, "declarator")):
            def_types.add(name)
        if "name" not in fields:
            continue
        if "body" in fields or "parameters" in fields:
            def_types.add(name)
        if "body" in fields and CONTAINER_NAME.search(name) \
                and not NOT_CONTAINER.search(name):
            class_types.add(name)
    ctor_types = {n for n in nodes if CTOR_NAME.search(n)}
    call_types = {n for n in nodes if CALL_ARGS in fields_of(nodes[n])
                  and n not in ctor_types}
    literal_types = {n for n in nodes if LITERAL_NAME.search(n)}
    import_types = {n for n in nodes if IMPORT_NAME.search(n)}
    return {"def_types": def_types, "class_types": class_types,
            "call_types": call_types, "ctor_types": ctor_types,
            "func_literal_types": literal_types,
            "import_types": import_types}


def assemble(generated, overrides):
    return {key: sorted((set(generated.get(key, []))
                         - set(overrides.get("trim", {}).get(key, [])))
                        | set(overrides.get("add", {}).get(key, [])))
            for key in FIELD_KEYS}


def main(argv):
    node_types_path, lang, overrides_path, out_path = argv[1:5]
    with open(node_types_path, encoding="utf-8") as handle:
        nodes_data = json.load(handle)
    with open(overrides_path, encoding="utf-8") as handle:
        overrides = json.load(handle)[lang]
    generated = {k: sorted(v) for k, v in generate(nodes_data).items()}
    mapping = assemble(generated, overrides)
    artifact = {"generator": GENERATOR_VERSION, "language": lang,
                "grammar": overrides.get("grammar"),
                "mapping": mapping, "reviewed_overrides": overrides}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print("wrote %s (%d node entries)" % (
        out_path, sum(len(v) for v in mapping.values())))


if __name__ == "__main__":
    main(sys.argv)
