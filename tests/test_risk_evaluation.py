from __future__ import annotations

import unittest

from slopekg.risk_evaluation import evaluate_risk_screening


class RiskEvaluationTests(unittest.TestCase):
    def test_missing_gold_set_is_not_reported_as_accuracy(self) -> None:
        result = evaluate_risk_screening({"assessments": []})
        self.assertEqual(result["status"], "requires_independent_expert_gold_set")
        self.assertIsNone(result["metrics"])

    def test_priority_metrics_emphasize_urgent_recall_and_p1_misses(self) -> None:
        screening = {"assessments": [
            {"slope_id": "s1", "screening_priority_code": "P1"},
            {"slope_id": "s2", "screening_priority_code": "P3"},
            {"slope_id": "s3", "screening_priority_code": "P2"},
        ]}
        gold = {"labels": [
            {"slope_id": "s1", "expert_priority": "P1"},
            {"slope_id": "s2", "expert_priority": "P1"},
            {"slope_id": "s3", "expert_priority": "P3"},
        ]}
        result = evaluate_risk_screening(screening, gold)
        self.assertEqual(result["evaluated"], 3)
        self.assertEqual(result["metrics"]["critical_p1_miss_count"], 1)
        self.assertEqual(result["metrics"]["urgent_recall_p1_p2"], 0.5)


if __name__ == "__main__":
    unittest.main()
