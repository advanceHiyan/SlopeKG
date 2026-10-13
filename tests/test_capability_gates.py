import unittest
from slopekg.risk import formal_risk_gate, stability_calculation_readiness, build_risk_screening


class CapabilityGateTests(unittest.TestCase):
    def test_formula_and_metadata_cannot_enable_a_nonexistent_solver(self):
        result = stability_calculation_readiness(
            props={"section_geometry": {"description": "present"}},
            related=[{"node": {"type": "MaterialParameterSet"}}, {"node": {"type": "StructuralPlane"}}],
            rule_library={"formulas": [{"id": "method", "approval_status": "approved", "execution_enabled": True}]},
            height=20, gradient=40)
        self.assertTrue(result["metadata_presence_checks_passed"])
        self.assertFalse(result["ready"])
        self.assertIn("solver_implemented", result["blocking_items"])
        self.assertIn("reference_case_verified", result["blocking_items"])
        self.assertEqual(set(result["checks"]), set(result["check_labels"]))

    def test_formal_release_stays_disabled_even_with_declared_prerequisites(self):
        result = formal_risk_gate(rule_library={"publication_status": "approved", "execution_enabled": True},
            likelihood_level="较高", consequence_level="高", current_dynamic_evidence=True,
            dynamic_rule_result_available=True, conflicts=[])
        self.assertTrue(result["metadata_presence_checks_passed"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["blocking_items"], ["formal_release_supported"])
        self.assertEqual(set(result["checks"]), set(result["check_labels"]))

    def test_overall_screening_never_disagrees_with_disabled_formal_release(self):
        graph = {"nodes": [
            {"id": "s", "type": "Slope", "label": "匿名边坡", "props": {"consequence_level": "高"}},
            {"id": "o", "type": "MonitoringObservation", "props": {"current_status_known": True,
                "threshold_status": "declared", "description": "新裂缝正在发展"}},
        ], "edges": [{"source": "s", "target": "o", "relation": "HAS_OBSERVATION", "props": {}}], "evidence": []}
        result = build_risk_screening(graph, {"publication_status": "approved", "execution_enabled": True})
        self.assertFalse(result["formal_risk_assessment_enabled"])
        self.assertTrue(result["assessments"][0]["formal_risk_gate"]["metadata_presence_checks_passed"])
        self.assertIsNone(result["assessments"][0]["formal_risk_level"])
