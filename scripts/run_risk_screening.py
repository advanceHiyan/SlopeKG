from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.config import PATHS  # noqa: E402
from slopekg.manual import apply_slope_overrides, merged_rule_library  # noqa: E402
from slopekg.risk import build_risk_screening  # noqa: E402
from slopekg.risk_evaluation import evaluate_risk_screening  # noqa: E402
from slopekg.schema import build_completeness  # noqa: E402
from slopekg.storage import ensure_dir, read_json, write_json  # noqa: E402


def main() -> None:
    graph = apply_slope_overrides(read_json(PATHS.graph_json, {"nodes": [], "edges": [], "evidence": []}))
    rules = merged_rule_library(read_json(PATHS.rules_json, {"execution_enabled": False, "publication_status": "not_generated", "rules": []}))
    completeness = build_completeness(graph)
    result = build_risk_screening(graph, rules, completeness)
    destination = PATHS.output_dir / "risk" / "screening.json"
    ensure_dir(destination.parent)
    write_json(destination, result)
    gold_path = PATHS.root / "data" / "validation" / "risk_gold.json"
    evaluation = evaluate_risk_screening(result, read_json(gold_path, {"labels": []}) if gold_path.exists() else {"labels": []})
    write_json(PATHS.output_dir / "risk" / "evaluation.json", evaluation)
    print(json.dumps({"output": str(destination.relative_to(PATHS.root)), **result["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
