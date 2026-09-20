import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slopekg.config import DemoPaths
from slopekg.manual import (
    apply_slope_overrides,
    merged_rule_library,
    save_manual_rule,
    save_slope_override,
)
from slopekg.risk import build_risk_screening
from slopekg.schema import build_completeness


def minimal_graph() -> dict:
    return {
        "nodes": [{"id": "s1", "type": "Slope", "label": "测试边坡", "summary": "", "props": {"slope_id": "TEST-1"}}],
        "edges": [],
        "evidence": [],
    }


class ManualWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".tmp" / f"manual_test_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def paths(self, root: Path) -> DemoPaths:
        return DemoPaths(root=root, output_dir=root / "output")

    def test_manual_slope_values_overlay_generated_graph_without_mutating_it(self) -> None:
        paths = self.paths(self.root)
        save_slope_override("s1", {"fields": {"slope_height_m": 42, "material_nature": "岩质"}}, paths)
        base = minimal_graph()
        effective = apply_slope_overrides(base, paths)

        self.assertNotIn("slope_height_m", base["nodes"][0]["props"])
        self.assertEqual(effective["nodes"][0]["props"]["slope_height_m"], 42.0)
        self.assertEqual(effective["nodes"][0]["props"]["slope_height_max_m"], 42.0)
        self.assertIn("slope_height_m", effective["nodes"][0]["props"]["manual_override"]["fields"])

    def test_dynamic_manual_value_requires_observation_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "数据日期"):
            save_slope_override(
                "s1", {"fields": {"deformation_observation": "发现新裂缝"}}, self.paths(self.root)
            )

    def test_negative_manual_inspection_text_does_not_raise_alarm(self) -> None:
        graph = minimal_graph()
        graph["nodes"][0]["props"]["deformation_observation"] = "现场未发现坡面渗水，未见管涌。"
        assessment = build_risk_screening(graph, {"rules": [], "execution_enabled": False}, build_completeness(graph))["assessments"][0]

        self.assertNotEqual(assessment["screening_priority_code"], "P1")
        self.assertFalse(any(item.get("kind") == "deformation_observation" for item in assessment["basis"]))

    def test_approved_supported_manual_rule_changes_review_priority(self) -> None:
        paths = self.paths(self.root)
        rule = save_manual_rule(
                {
                    "title": "高边坡重点复核",
                    "conditions": [{"field": "slope_height_m", "operator": "gte", "value": 40}],
                    "match": "all",
                    "priority": "P2",
                    "reason": "人工规则：坡高达到40m",
                    "approval_status": "approved",
                },
            paths,
        )
        graph = minimal_graph()
        graph["nodes"][0]["props"]["slope_height_m"] = 45
        library = merged_rule_library({"rules": [], "execution_enabled": False, "stats": {}}, paths)
        screening = build_risk_screening(graph, library, build_completeness(graph))
        assessment = screening["assessments"][0]

        self.assertTrue(rule["execution_enabled"])
        self.assertEqual(assessment["screening_priority_code"], "P2")
        self.assertEqual(assessment["executed_manual_rules"][0]["id"], rule["id"])
        self.assertIsNone(assessment["formal_risk_level"])

    def test_unsupported_manual_rule_is_stored_but_never_executed(self) -> None:
        paths = self.paths(self.root)
        rule = save_manual_rule(
                {
                    "title": "复杂公式占位",
                    "conditions": [{"field": "custom_formula_result", "operator": "approximately", "value": "高"}],
                    "priority": "P1",
                    "approval_status": "approved",
                    "logic_note": "需要新的物理计算模块。",
                },
            paths,
        )
        graph = minimal_graph()
        library = merged_rule_library({"rules": [], "execution_enabled": False, "stats": {}}, paths)
        assessment = build_risk_screening(graph, library, build_completeness(graph))["assessments"][0]

        self.assertEqual(rule["execution_status"], "stored_not_executable")
        self.assertFalse(rule["execution_enabled"])
        self.assertEqual(assessment["executed_manual_rules"], [])


if __name__ == "__main__":
    unittest.main()
