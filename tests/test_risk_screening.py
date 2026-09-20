from __future__ import annotations

import unittest
from datetime import date, timedelta

from slopekg.risk import build_risk_screening


def graph_with(stability: dict | None = None, *, body_type: str | None = None, props: dict | None = None) -> dict:
    nodes = [{
        "id": "s1",
        "type": "Slope",
        "label": "G1 K1+000-K1+100 边坡",
        "summary": "滑坡" if body_type == "滑坡" else "",
        "props": {"slope_id": "G1-001", "route_code_cache": "G1", **(props or {})},
    }]
    edges = []
    if body_type:
        nodes.append({"id": "h1", "type": "HazardBody", "label": body_type, "props": {"body_type": body_type}})
        edges.append({"source": "s1", "target": "h1", "relation": "CONTAINS_HAZARD_BODY", "props": {}})
    if stability:
        nodes.append({"id": "a1", "type": "StabilityAnalysis", "label": "稳定性分析", "props": stability})
        edges.append({"source": "s1", "target": "a1", "relation": "HAS_STABILITY_ANALYSIS", "props": {"evidence": "ev1"}})
    return {
        "nodes": nodes,
        "edges": edges,
        "evidence": [{"id": "ev1", "source_file": "report.pdf", "page": 3, "text": "暴雨工况稳定系数为1.04，属于欠稳定状态。"}],
    }


class RiskScreeningTests(unittest.TestCase):
    def test_adverse_scenario_is_prioritized_without_fake_formal_level(self) -> None:
        graph = graph_with(
            {
                "condition": "暴雨工况",
                "fs": 1.04,
                "status": "欠稳定",
                "analysis_scope": "现状边坡",
            },
            body_type="滑坡",
        )

        result = build_risk_screening(graph, {"publication_status": "candidate_not_approved", "execution_enabled": False})
        row = result["assessments"][0]

        self.assertEqual(row["screening_priority_code"], "P1")
        self.assertEqual(row["likelihood_level"], "较高")
        self.assertIsNone(row["formal_risk_level"])
        self.assertIn("欠稳定", row["stability_concern"])

    def test_design_check_is_not_treated_as_current_safety(self) -> None:
        graph = graph_with(
            {
                "condition": "治理设计工况",
                "fs": 1.35,
                "required_fs": 1.30,
                "status": "满足要求",
            },
            body_type="危岩体",
            props={"slope_height_max_m": 35, "slope_gradient_max_deg": 70},
        )

        row = build_risk_screening(graph, {"execution_enabled": False})["assessments"][0]

        self.assertEqual(row["screening_priority_code"], "P2")
        self.assertEqual(row["stability_concern"], "无法判断")
        self.assertIsNone(row["likelihood_level"])
        self.assertTrue(any("设计防护措施" in item for item in row["recommended_action"]))

    def test_missing_data_never_becomes_low_risk(self) -> None:
        row = build_risk_screening(graph_with(), {"execution_enabled": False})["assessments"][0]

        self.assertEqual(row["screening_priority_code"], "P4")
        self.assertIsNone(row["formal_risk_level"])
        self.assertIsNone(row["likelihood_level"])
        self.assertTrue(any("低风险" in item for item in row["limitations"]))

    def test_potentially_unstable_label_is_p2_not_confirmed_p1(self) -> None:
        row = build_risk_screening(
            graph_with(body_type="潜在不稳定边坡"),
            {"execution_enabled": False},
        )["assessments"][0]

        self.assertEqual(row["screening_priority_code"], "P2")

    def test_unverified_generic_hazard_label_without_context_is_p4(self) -> None:
        graph = graph_with()
        graph["nodes"].extend([
            {"id": "h1", "type": "HazardBody", "label": "灾害体", "props": {"body_type": None}},
            {
                "id": "ha1",
                "type": "HazardSusceptibilityAssessment",
                "label": "灾害类型自动识别",
                "props": {"candidate_hazard_types": ["崩塌"]},
            },
        ])
        graph["edges"].extend([
            {"source": "s1", "target": "h1", "relation": "CONTAINS_HAZARD_BODY", "props": {}},
            {"source": "s1", "target": "ha1", "relation": "HAS_SUSCEPTIBILITY_ASSESSMENT", "props": {}},
        ])

        row = build_risk_screening(graph, {"execution_enabled": False})["assessments"][0]

        self.assertEqual(row["screening_priority_code"], "P4")
        self.assertFalse(row["screening_basis_status"]["minimum_basis_met"])
        self.assertIn("未经证据定位", row["screening_reasons"][0])

    def test_generic_hazard_label_with_independent_context_remains_p3(self) -> None:
        graph = graph_with()
        graph["nodes"].extend([
            {"id": "h1", "type": "HazardBody", "label": "灾害体", "props": {"body_type": None}},
            {"id": "l1", "type": "Lithology", "label": "页岩", "props": {}},
        ])
        graph["edges"].extend([
            {"source": "s1", "target": "h1", "relation": "CONTAINS_HAZARD_BODY", "props": {}},
            {"source": "s1", "target": "l1", "relation": "HAS_LITHOLOGY", "props": {}},
        ])

        row = build_risk_screening(graph, {"execution_enabled": False})["assessments"][0]

        self.assertEqual(row["screening_priority_code"], "P3")
        self.assertTrue(row["screening_basis_status"]["supporting_context_available"])

    def test_duplicate_stability_records_keep_the_correctly_aligned_evidence(self) -> None:
        graph = graph_with(
            {"condition": "饱和状态", "fs": 1.04, "status": "欠稳定", "analysis_scope": "现状边坡"},
            body_type="滑坡",
        )
        graph["evidence"][0]["text"] = "饱和状态稳定性系数为1.04，状态为欠稳定。"
        graph["nodes"].append({
            "id": "a2",
            "type": "StabilityAnalysis",
            "label": "重复稳定性分析",
            "props": {"condition": "饱水状态", "fs": 1.04, "status": "欠稳定", "analysis_scope": "现状边坡"},
        })
        graph["edges"].append({
            "source": "s1", "target": "a2", "relation": "HAS_STABILITY_ANALYSIS", "props": {"evidence": "ev2"},
        })
        graph["evidence"].append({
            "id": "ev2", "source_file": "report.pdf", "page": 4,
            "text": "天然状态稳定性系数为1.20，状态为基本稳定。",
        })

        scenarios = build_risk_screening(graph, {"execution_enabled": False})["assessments"][0]["stability_scenarios"]

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["evidence_alignment"], "exact")
        self.assertIn("1.04", scenarios[0]["evidence"]["text"])

    def test_missing_data_has_actionable_accuracy_guidance(self) -> None:
        completeness = {
            "reports": [{
                "slope_id": "s1",
                "risk_data_completeness": 0.1,
                "fields": [
                    {"code": "maintenance", "label_zh": "养护历史", "status": "missing"},
                    {"code": "rainfall", "label_zh": "近期降雨", "status": "interface_reserved", "interface": "/api/environment/latest"},
                    {"code": "deformation_observation", "label_zh": "近期变形巡检", "status": "interface_reserved", "interface": "/api/inspections"},
                ],
            }],
        }

        row = build_risk_screening(graph_with(), {"execution_enabled": False}, completeness)["assessments"][0]
        gaps = row["critical_data_gaps"]

        self.assertEqual(gaps[0]["code"], "deformation_observation")
        self.assertEqual(gaps[1]["code"], "rainfall")
        self.assertEqual(gaps[0]["improvement_priority_label"], "第一优先")
        self.assertIn("建议", "建议补采：" + gaps[0]["collection_hint"])
        self.assertTrue(gaps[0]["why_it_matters"])
        self.assertEqual(gaps[0]["gap_scope"], "interface_pending")
        self.assertEqual(row["risk_data_inventory"]["total_categories"], 3)
        self.assertEqual(row["risk_data_inventory"]["available_categories"], 0)

    def test_gap_count_is_not_artificially_capped_and_excludes_system_fields(self) -> None:
        codes = [
            "deformation_observation", "overall_slope_deformation_severity", "rainfall",
            "groundwater", "monitoring", "stability", "protection_condition",
            "structural_plane", "slope_height_m", "slope_gradient_deg",
            "material_nature", "hazard_body", "exposure", "maintenance",
            "slope_type", "slope_aspect_deg", "river_relation", "vegetation_condition",
        ]
        completeness = {
            "reports": [{
                "slope_id": "s1",
                "risk_data_completeness": 0,
                "fields": [
                    *[{"code": code, "label_zh": code, "status": "missing"} for code in codes],
                    {"code": "risk_rules", "label_zh": "已发布风险规则", "status": "interface_reserved"},
                    {"code": "coordinate_crs", "label_zh": "坐标系", "status": "missing"},
                ],
            }],
        }

        gaps = build_risk_screening(graph_with(), {"execution_enabled": False}, completeness)["assessments"][0]["critical_data_gaps"]

        self.assertGreater(len(gaps), 12)
        self.assertNotIn("risk_rules", {row["code"] for row in gaps})
        self.assertNotIn("coordinate_crs", {row["code"] for row in gaps})

    def test_output_separates_static_dynamic_and_consequence_axes(self) -> None:
        row = build_risk_screening(
            graph_with(
                {"condition": "暴雨工况", "fs": 1.04, "status": "欠稳定", "analysis_scope": "现状边坡"},
                body_type="滑坡",
            ),
            {"execution_enabled": False},
        )["assessments"][0]

        self.assertEqual(row["static_susceptibility"]["level"], "较高关注")
        self.assertEqual(row["dynamic_alert"]["level"], "无法判断")
        self.assertEqual(row["consequence_assessment"]["level"], "无法判断")
        self.assertEqual(row["hazard_profiles"][0]["hazard_type"], "滑坡")
        self.assertFalse(row["formal_risk_gate"]["checks"]["approved_dynamic_rule_result"])
        self.assertIsNone(row["formal_risk_level"])

    def test_stale_manual_alarm_is_historical_not_current_trigger(self) -> None:
        old_date = (date.today() - timedelta(days=120)).isoformat()
        row = build_risk_screening(graph_with(props={
            "deformation_observation": "发现新裂缝",
            "manual_override": {"fields": ["deformation_observation"], "observed_at": old_date},
        }), {"execution_enabled": False})["assessments"][0]

        self.assertEqual(row["temporal_validity"]["status"], "stale")
        self.assertEqual(row["dynamic_alert"]["level"], "近期状态未知（旧资料已过期）")
        self.assertNotEqual(row["screening_priority_code"], "P1")
        self.assertIn("新裂缝", row["historical_deformation_terms"])

    def test_recent_manual_alarm_can_trigger_conservative_review(self) -> None:
        row = build_risk_screening(graph_with(props={
            "deformation_observation": "发现新裂缝",
            "manual_override": {"fields": ["deformation_observation"], "observed_at": date.today().isoformat()},
        }), {"execution_enabled": False})["assessments"][0]

        self.assertEqual(row["temporal_validity"]["status"], "current")
        self.assertEqual(row["dynamic_alert"]["level"], "发现异常")
        self.assertEqual(row["screening_priority_code"], "P1")

    def test_conflicting_same_scenario_blocks_formal_gate(self) -> None:
        graph = graph_with(
            {"condition": "天然工况", "fs": 1.04, "status": "欠稳定", "analysis_scope": "现状边坡"},
            body_type="滑坡",
        )
        graph["nodes"].append({
            "id": "a2", "type": "StabilityAnalysis", "label": "稳定性分析2",
            "props": {"condition": "天然工况", "fs": 1.20, "status": "稳定", "analysis_scope": "现状边坡"},
        })
        graph["edges"].append({"source": "s1", "target": "a2", "relation": "HAS_STABILITY_ANALYSIS", "props": {}})

        row = build_risk_screening(graph, {
            "publication_status": "approved", "execution_enabled": True,
        })["assessments"][0]

        self.assertEqual(len(row["data_conflicts"]), 1)
        self.assertFalse(row["formal_risk_gate"]["checks"]["no_unresolved_stability_conflict"])
        self.assertIsNone(row["formal_risk_level"])


if __name__ == "__main__":
    unittest.main()
