from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.config import PATHS  # noqa: E402
from slopekg.risk import build_risk_screening  # noqa: E402
from slopekg.server import empty_rule_library  # noqa: E402
from slopekg.storage import ensure_dir, read_json, write_json  # noqa: E402


def main() -> None:
    graph = read_json(PATHS.graph_json, {"nodes": [], "edges": [], "evidence": []})
    rules = read_json(PATHS.rules_json, empty_rule_library())
    completeness = read_json(PATHS.completeness_json, {"reports": [], "summary": {}})
    result = build_risk_screening(graph, rules, completeness)
    destination = PATHS.output_dir / "risk" / "screening.json"
    ensure_dir(destination.parent)
    write_json(destination, result)
    print(json.dumps({"output": str(destination.relative_to(PATHS.root)), **result["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
