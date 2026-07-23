from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.exporter import DEFAULT_OUTPUT, export_standalone_html  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SlopeKG as one self-contained offline HTML file")
    parser.add_argument("--mode", choices=["basic", "deep", "active"], default="active")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = export_standalone_html(args.output.resolve(), graph_mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
