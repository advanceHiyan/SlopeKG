from __future__ import annotations

import unittest

from slopekg.graph_builder import matching_stability_evidence, stability_scenario_key
from slopekg.schema import attribute_dictionary_payload, interface_payload, schema_payload


class GraphSchemaTests(unittest.TestCase):
    def test_reserved_interfaces_are_visible(self) -> None:
        payload = interface_payload()
        reserved = [row for row in payload["interfaces"] if row["status"] != "implemented"]
        self.assertTrue(any(row["path"] == "/api/monitoring/observations" for row in reserved))
        implemented = [row for row in payload["interfaces"] if row["status"] == "implemented"]
        self.assertTrue(any(row["path"] == "/api/risk/assess" for row in implemented))
        self.assertTrue(any(row["path"] == "/api/risk/screening" for row in implemented))
        self.assertTrue(any(row["path"] == "/api/multimodal/assets" for row in implemented))

    def test_schema_exposes_missing_semantics(self) -> None:
        payload = schema_payload()
        self.assertIn("unknown", payload["missing_value_semantics"])
        self.assertIn("not_observed", payload["missing_value_semantics"])
        self.assertIn("VisualAsset", payload["node_types"])
        self.assertIn("PointCloudAsset", payload["node_types"])
        self.assertIn("DERIVED_FROM", payload["relation_types"])
        self.assertEqual(payload["multimodal"]["contract_path"], "/api/multimodal/schema")

    def test_feedback_dictionary_is_exposed_without_flattening_deferred_fields(self) -> None:
        payload = attribute_dictionary_payload()
        fields = {str(field["source_no"]): field for section in payload["sections"] for field in section["fields"]}
        self.assertEqual(fields["20"]["code"], "river_relation")
        self.assertEqual(fields["23c"]["item_fields"][0]["enum"], ["坡面防护", "沿河防护", "支挡设施"])
        self.assertEqual(fields["34"]["fields"][1]["item_fields"][1]["code"], "damaged_protection_facility_type")
        self.assertEqual(fields["35"]["code"], "overall_slope_deformation_severity")
        self.assertEqual(fields["38"]["status"], "DEFERRED_INTEGRATION")

    def test_stability_scenario_keys_merge_equivalent_condition_names(self) -> None:
        deterministic = {
            "condition": "饱和状态", "safety_factor": 1.04, "required_factor": None,
            "status": "欠稳定", "analysis_scope": "现状边坡",
        }
        semantic = {
            "condition": "饱水状态", "safety_factor": 1.04, "required_factor": None,
            "status": "欠稳定", "analysis_scope": "现状边坡",
        }

        self.assertEqual(stability_scenario_key(deterministic), stability_scenario_key(semantic))

    def test_semantic_stability_evidence_must_match_its_own_scenario(self) -> None:
        evidence = [
            {"id": "natural", "text": "天然状态稳定性系数为1.20，状态为基本稳定"},
            {"id": "rain", "text": "暴雨工况稳定性系数为1.04，状态为欠稳定"},
        ]
        scenario = {"condition": "暴雨工况", "safety_factor": 1.04, "status": "欠稳定"}

        self.assertEqual(matching_stability_evidence(scenario, ["natural", "rain"], evidence), "rain")


if __name__ == "__main__":
    unittest.main()
