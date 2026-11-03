"""Community detection: deterministic label propagation plus summaries."""

import unittest

from tunic import community


def _view(edges):
    assertions = []
    for caller, callee in edges:
        assertions.append({
            "status": "syntactic_observation",
            "proposition": {"relation": "static_call",
                            "caller": caller, "callee": callee}})
    symbols = [{"key": key} for key in
               sorted({endpoint for edge in edges for endpoint in edge})]
    return {"symbols": symbols, "assertions": assertions}


class DetectTest(unittest.TestCase):
    def test_chain_and_triangle_separate(self):
        view = _view([("a", "b"), ("b", "c"),
                      ("d", "e"), ("e", "f"), ("f", "d")])
        clusters = community.detect(view)
        self.assertEqual(len(clusters), 2)
        members = [sorted(members) for members in clusters.values()]
        self.assertIn(["a", "b", "c"], members)
        self.assertIn(["d", "e", "f"], members)

    def test_singletons_excluded(self):
        view = _view([("a", "b")])
        clusters = community.detect(view)
        self.assertEqual(len(clusters), 1)
        view = _view([])
        self.assertEqual(community.detect(view), {})

    def test_deterministic(self):
        view = _view([("a", "b"), ("b", "c"), ("c", "a"),
                      ("x", "y")])
        first = community.detect(view)
        second = community.detect(
            {"symbols": list(reversed(view["symbols"])),
             "assertions": list(reversed(view["assertions"]))})
        self.assertEqual(first, second)

    def test_zero_iterations_yields_nothing(self):
        view = _view([("a", "b"), ("b", "c")])
        self.assertEqual(community.detect(view, max_iter=0), {})

    def test_isolated_pair_is_community(self):
        view = _view([("m::x", "m::y")])
        clusters = community.detect(view)
        self.assertEqual(list(clusters.values()), [["m::x", "m::y"]])


class WeightedPropagationTest(unittest.TestCase):
    def test_parallel_edges_outweigh_single(self):
        # X-P once, X-Q twice: weighted propagation puts X in Q's
        # cluster; unweighted, the pass-1 tie would break to P.
        view = {"symbols": [], "assertions": []}
        pairs = [("x", "p"), ("x", "q"), ("x", "q"),
                 ("p", "p1"), ("q", "q1"), ("q", "q2")]
        for caller, callee in pairs:
            view["assertions"].append({
                "status": "syntactic_observation",
                "proposition": {"relation": "static_call",
                                "caller": caller, "callee": callee}})
            for key in (caller, callee):
                if key not in [s["key"] for s in view["symbols"]]:
                    view["symbols"].append({"key": key})
        clusters = community.detect(view)
        by_member = {}
        for members in clusters.values():
            for member in members:
                by_member[member] = sorted(members)
        self.assertEqual(by_member["x"], ["q", "q1", "q2", "x"])

    def test_weights_count_parallel_edges(self):
        view = {"symbols": [],
                "assertions": [{
                    "status": "syntactic_observation",
                    "proposition": {"relation": "static_call",
                                    "caller": "a", "callee": "b"}}] * 2}
        weights = community._weights(view)
        self.assertEqual(weights["a"]["b"], 2)
        self.assertEqual(weights["b"]["a"], 2)


class SummarizeTest(unittest.TestCase):
    def test_record_shape_and_entry_points(self):
        view = _view([("a", "b"), ("b", "c")])
        clusters = community.detect(view)
        records = community.summarize(view, clusters)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["members"], ["a", "b", "c"])
        self.assertEqual(record["member_count"], 3)
        self.assertEqual(record["internal_edges"], 2)
        self.assertEqual(record["entry_points"], ["a"])
        self.assertEqual(record["paths"], ["a", "b", "c"])
        self.assertEqual(record["rule"], "community_summary_v1")
        self.assertTrue(record["digest"])

    def test_paths_from_keys(self):
        view = _view([("p/x.py::f", "p/y.py::g")])
        records = community.summarize(view)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["paths"], ["p/x.py", "p/y.py"])

    def test_same_view_same_digest(self):
        view = _view([("a", "b"), ("b", "a")])
        first = community.summarize(view)
        second = community.summarize(view)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
