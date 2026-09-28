import copy
import unittest

from slopekg.fact_evaluation import equivalent, evaluate_graph_facts


class FactEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.graph = {"nodes": [{"id": "s", "type": "Slope", "props": {
            "route_code_cache": "G999", "start_station_raw": "K1+000", "end_station_raw": "K1+100", "side": "右侧"}}], "edges": []}
        self.gold = {"version": "test", "scope": "test", "review_status": "synthetic",
                     "facts": [{"id": "side", "route": "G999", "station": "K1+000-K1+100",
                                "node_type": "Slope", "expected": {"side": "右侧"}}]}

    def status(self):
        return evaluate_graph_facts(self.graph, self.gold)["facts"][0]["status"]

    def test_mutating_field_changes_verdict_and_node_id_does_not(self):
        self.assertEqual(self.status(), "pass")
        self.graph["nodes"][0]["props"]["side"] = "左侧"
        self.assertEqual(self.status(), "wrong")
        self.graph["nodes"][0]["props"]["side"] = "右侧"
        self.graph["nodes"][0]["id"] = "new-id"
        self.assertEqual(self.status(), "pass")

    def test_numeric_pairs_are_ordered_and_boolean_is_not_one(self):
        self.assertFalse(equivalent([80, 30], [30, 80]))
        self.assertFalse(equivalent(True, 1))
        self.assertFalse(equivalent("2.62", 2.62))

    def test_null_field_is_missing_rather_than_an_incorrect_value(self):
        self.graph['nodes'][0]['props']['side']=None
        self.assertEqual(self.status(),'missing')

    def test_pair_cannot_be_assembled_from_different_planes(self):
        self.gold["facts"][0].update(node_type="StructuralPlane", relation="DEVELOPS_STRUCTURAL_PLANE",
                                     expected={"dip_direction": 120, "dip_angle": 45}, collection_member=True)
        for i, (direction, angle) in enumerate([(120, 70), (300, 45)]):
            self.graph["nodes"].append({"id": str(i), "type": "StructuralPlane", "props": {"dip_direction": direction, "dip_angle": angle}})
            self.graph["edges"].append({"source": "s", "target": str(i), "relation": "DEVELOPS_STRUCTURAL_PLANE"})
        self.assertEqual(self.status(), "wrong")

    def test_conflicts_and_duplicates_are_distinct(self):
        self.gold["facts"][0].update(node_type="StabilityAnalysis", relation="HAS_STABILITY_ANALYSIS",
                                     where={"condition": "暴雨", "failure_mode": "滑移式"}, expected={"fs": 1.2})
        for i, value in enumerate([1.2, 1.2]):
            self.graph["nodes"].append({"id": str(i), "type": "StabilityAnalysis", "props": {"condition": "暴雨", "failure_mode": "滑移式", "fs": value}})
            self.graph["edges"].append({"source": "s", "target": str(i), "relation": "HAS_STABILITY_ANALYSIS"})
        self.assertEqual(self.status(), "pass")
        self.assertEqual(evaluate_graph_facts(self.graph, self.gold)["duplicate_matches"], 1)
        self.graph["nodes"][2]["props"]["fs"] = .8
        self.assertEqual(self.status(), "conflict")
        self.graph["edges"][0]["source"] = "another-slope"
        self.assertEqual(self.status(), "wrong")
        self.graph["nodes"][2]["props"]["condition"] = "天然"
        self.assertEqual(self.status(), "missing")

    def test_absence_does_not_pass_for_missing_or_ambiguous_owner(self):
        self.gold["facts"][0]["expect_absent"] = True
        self.assertEqual(self.status(), "violation")
        self.graph["nodes"].append({**copy.deepcopy(self.graph["nodes"][0]), "id": "duplicate"})
        self.assertEqual(self.status(), "ambiguous_owner")
        self.graph["nodes"] = []
        self.assertEqual(self.status(), "missing_owner")
