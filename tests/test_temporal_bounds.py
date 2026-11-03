"""Valid-time reasoning: bound normalization and date-ordered conflicts."""

import unittest

from tunic import temporal


class NormalizeBoundsTest(unittest.TestCase):
    def test_future_statement_pins_to_reference(self):
        valid, invalid, signals = temporal.normalize_bounds(
            "We will migrate to the new API by Friday.",
            "2026-11-01", valid_at="2026-11-07")
        self.assertEqual(valid, "2026-11-01")
        self.assertIsNone(invalid)
        self.assertIn("future_statement_pinned_to_reference", signals)

    def test_soft_deadline_is_not_invalidation(self):
        valid, invalid, signals = temporal.normalize_bounds(
            "Migration will complete before 2026-12-01.",
            "2026-11-01", valid_at="2026-11-01",
            invalid_at="2026-12-01")
        self.assertEqual(valid, "2026-11-01")
        self.assertIsNone(invalid)
        self.assertIn("soft_deadline_is_not_invalidation", signals)

    def test_explicit_end_marker_keeps_end_bound(self):
        valid, invalid, signals = temporal.normalize_bounds(
            "The flag is valid until 2026-12-01.",
            "2026-11-01", valid_at="2026-11-01",
            invalid_at="2026-12-01")
        self.assertEqual(invalid, "2026-12-01")
        self.assertNotIn("soft_deadline_is_not_invalidation", signals)

    def test_plain_statement_untouched(self):
        valid, invalid, signals = temporal.normalize_bounds(
            "The service runs on port 80.", "2026-11-01",
            valid_at="2026-10-01")
        self.assertEqual((valid, invalid, signals),
                         ("2026-10-01", None, []))

    def test_unusable_reference_untouched(self):
        valid, invalid, signals = temporal.normalize_bounds(
            "We will migrate soon.", "soon", valid_at="2026-11-07")
        self.assertEqual((valid, invalid, signals),
                         ("2026-11-07", None, []))

    def test_bad_day_rejected(self):
        self.assertIsNone(temporal.parse_iso_day("2026-13-01"))
        self.assertIsNone(temporal.parse_iso_day("not-a-date"))
        self.assertEqual(temporal.parse_iso_day("2026-11-03"),
                         (2026, 11, 3))


def _view(assertions):
    return {"symbols": [], "assertions": assertions}


def _claim(caller, callee, effective, relation="depends_on"):
    return {"status": "asserted_in_source",
            "proposition": {"relation": relation, "caller": caller,
                            "callee": callee, "effective": effective}}


class DateConflictTest(unittest.TestCase):
    def test_ordered_dates_propose(self):
        view = _view([_claim("a.py::x", "b.py::y", "2026-10-01"),
                      _claim("a.py::x", "b.py::y", "2026-11-01")])
        found = temporal.propose_date_conflicts(view)
        self.assertEqual(len(found), 1)
        candidate = found[0]
        self.assertEqual(candidate["relation"], "contradicts")
        self.assertEqual(candidate["derivation"],
                         "temporal_order_conflict_v1")
        self.assertIn("earlier_effective:2026-10-01",
                      candidate["signals"])
        self.assertIn("later_effective:2026-11-01",
                      candidate["signals"])

    def test_same_date_no_proposal(self):
        view = _view([_claim("a.py::x", "b.py::y", "2026-10-01"),
                      _claim("a.py::x", "b.py::y", "2026-10-01",
                             relation="depends_on")])
        self.assertEqual(temporal.propose_date_conflicts(view), [])

    def test_undated_never_participates(self):
        view = _view([{"status": "asserted_in_source",
                       "proposition": {"relation": "depends_on",
                                       "caller": "a.py::x",
                                       "callee": "b.py::y"}},
                      _claim("a.py::x", "b.py::y", "2026-11-01")])
        self.assertEqual(temporal.propose_date_conflicts(view), [])

    def test_decided_pairs_skipped(self):
        view = _view([_claim("a.py::x", "b.py::y", "2026-10-01"),
                      _claim("a.py::x", "b.py::y", "2026-11-01"),
                      _claim("a.py::x", "b.py::y", "2026-11-02",
                             relation="contradicts")])
        self.assertEqual(temporal.propose_date_conflicts(view), [])

    def test_suppressed_excluded(self):
        first = _claim("a.py::x", "b.py::y", "2026-10-01")
        first["id"] = "a1"
        second = _claim("a.py::x", "b.py::y", "2026-11-01")
        second["id"] = "a2"
        view = _view([first, second])
        self.assertEqual(len(temporal.propose_date_conflicts(view)), 1)
        self.assertEqual(
            temporal.propose_date_conflicts(view, suppressed=["a2"]), [])


if __name__ == "__main__":
    unittest.main()
