"""Spec-derivation parity: checked-in generated mappings match hand specs.

The generator (benchmarks/spec-derivation/generate.py) proposes syntax
mappings from node-types.json; reviewed overrides trim/add. These tests
pin that the checked-in artifacts equal the hand-reviewed unigraph
specs on all six syntax-mappable fields, so neither side can drift
silently. Semantic policy (call shapes, receivers, scope, imports
styles) stays hand-reviewed and is intentionally not generated.
"""

import json
import unittest
from pathlib import Path

from tunic import unigraph

BASE = Path(__file__).resolve().parent.parent / "benchmarks" / "spec-derivation"
LANGS = ("java", "rust", "cpp")
FIELDS = ("def_types", "class_types", "call_types", "ctor_types",
          "func_literal_types", "import_types")


def _keys(value):
    if isinstance(value, dict):
        return set(value)
    return set(value or ())


class SpecDerivationParityTest(unittest.TestCase):
    def test_generated_artifacts_match_hand_specs(self):
        for lang in LANGS:
            artifact = json.loads(
                (BASE / "generated" / (lang + ".json")).read_text(
                    encoding="utf-8"))
            self.assertEqual(artifact["generator"], "derive-v1")
            self.assertIn("grammar", artifact)
            hand = unigraph.SPECS[lang]
            for field in FIELDS:
                with self.subTest(language=lang, field=field):
                    self.assertEqual(set(artifact["mapping"][field]),
                                     _keys(hand.get(field)),
                                     "generated mapping differs from hand "
                                     "spec for %s.%s" % (lang, field))
