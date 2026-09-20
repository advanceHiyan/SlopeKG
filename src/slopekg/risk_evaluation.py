from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any


PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def evaluate_risk_screening(screening: dict[str, Any], gold: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate review-priority output against an independent expert gold set.

    Absence of labels is reported explicitly.  Extraction regression samples and
    stability factors are not silently reused as risk labels.
    """
    labels = (gold or {}).get("labels", [])
    predictions = {str(row.get("slope_id")): row for row in screening.get("assessments", [])}
    if not labels:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "requires_independent_expert_gold_set",
            "evaluated": 0,
            "metrics": None,
            "required_label_fields": ["slope_id", "expert_priority"],
            "optional_label_fields": ["reviewed_at", "reviewer_role", "observed_event", "notes"],
            "boundary": "页面抽取准确率和稳定系数复核结果不能替代风险判断金标准。",
        }

    pairs = []
    rejected = []
    for row in labels:
        slope_id = str(row.get("slope_id") or "")
        actual = str(row.get("expert_priority") or "").upper()
        prediction = predictions.get(slope_id)
        if not slope_id or actual not in PRIORITY_ORDER:
            rejected.append({"slope_id": slope_id or None, "reason": "invalid_or_missing_expert_priority"})
            continue
        if not prediction:
            rejected.append({"slope_id": slope_id, "reason": "slope_not_found_in_screening"})
            continue
        predicted = str(prediction.get("screening_priority_code") or "").upper()
        if predicted not in PRIORITY_ORDER:
            rejected.append({"slope_id": slope_id, "reason": "invalid_prediction"})
            continue
        pairs.append((slope_id, actual, predicted))

    if not pairs:
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "no_valid_label_pairs", "evaluated": 0, "rejected": rejected, "metrics": None,
        }

    confusion = {actual: {pred: 0 for pred in PRIORITY_ORDER} for actual in PRIORITY_ORDER}
    for _, actual, predicted in pairs:
        confusion[actual][predicted] += 1
    exact = sum(actual == predicted for _, actual, predicted in pairs)
    within_one = sum(abs(PRIORITY_ORDER[actual] - PRIORITY_ORDER[predicted]) <= 1 for _, actual, predicted in pairs)
    actual_urgent = {(slope_id, actual, predicted) for slope_id, actual, predicted in pairs if actual in {"P1", "P2"}}
    predicted_urgent = {(slope_id, actual, predicted) for slope_id, actual, predicted in pairs if predicted in {"P1", "P2"}}
    urgent_hits = sum(actual in {"P1", "P2"} and predicted in {"P1", "P2"} for _, actual, predicted in pairs)
    critical_misses = [
        {"slope_id": slope_id, "expert_priority": actual, "predicted_priority": predicted}
        for slope_id, actual, predicted in pairs if actual == "P1" and predicted in {"P3", "P4"}
    ]
    n = len(pairs)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "evaluated_against_independent_labels",
        "evaluated": n,
        "rejected": rejected,
        "label_distribution": dict(Counter(actual for _, actual, _ in pairs)),
        "prediction_distribution": dict(Counter(predicted for _, _, predicted in pairs)),
        "metrics": {
            "exact_priority_accuracy": round(exact / n, 4),
            "within_one_priority_accuracy": round(within_one / n, 4),
            "urgent_recall_p1_p2": round(urgent_hits / len(actual_urgent), 4) if actual_urgent else None,
            "urgent_precision_p1_p2": round(urgent_hits / len(predicted_urgent), 4) if predicted_urgent else None,
            "critical_p1_miss_count": len(critical_misses),
        },
        "confusion_matrix": confusion,
        "critical_misses": critical_misses,
        "boundary": "该结果检验P1—P4复核排序一致性，不代表滑坡发生概率预测准确率。",
    }
