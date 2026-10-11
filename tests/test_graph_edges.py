from __future__ import annotations

import unittest

from slopekg.graph_builder import add_edge


class GraphEdgeTests(unittest.TestCase):
    def test_duplicate_properties_ignore_key_order(self):
        edges = []
        add_edge(edges, "a", "b", "HAS", value=1, unit="m")
        add_edge(edges, "a", "b", "HAS", unit="m", value=1)
        self.assertEqual(len(edges), 1)

    def test_distinct_properties_endpoints_and_relations_keep_order_and_ids(self):
        edges = []
        add_edge(edges, "a", "b", "HAS", value=1)
        add_edge(edges, "a", "b", "HAS", value=2)
        add_edge(edges, "a", "c", "HAS", value=1)
        add_edge(edges, "a", "b", "LINKS", value=1)
        self.assertEqual([row["id"] for row in edges], ["e0001", "e0002", "e0003", "e0004"])
        self.assertEqual([row["props"]["value"] for row in edges], [1, 2, 1, 1])


if __name__ == "__main__":
    unittest.main()
