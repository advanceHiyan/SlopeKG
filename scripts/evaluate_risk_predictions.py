from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.risk_evaluation import evaluate_risk_screening  # noqa: E402
from slopekg.storage import read_json, write_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="使用独立专家标签评测P1—P4风险复核排序")
    parser.add_argument("--screening", type=Path, default=ROOT / "output" / "demo" / "risk" / "screening.json")
    parser.add_argument("--gold", type=Path, default=ROOT / "data" / "validation" / "risk_gold.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "demo" / "risk" / "evaluation.json")
    args = parser.parse_args()
    screening = read_json(args.screening, {"assessments": []})
    gold = read_json(args.gold, {"labels": []}) if args.gold.exists() else {"labels": []}
    result = evaluate_risk_screening(screening, gold)
    write_json(args.output, result)
    print(json.dumps({"output": str(args.output), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
