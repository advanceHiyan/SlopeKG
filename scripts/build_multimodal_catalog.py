from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.config import PATHS  # noqa: E402
from slopekg.multimodal import build_multimodal_outputs  # noqa: E402
from slopekg.schema import interface_payload, schema_payload  # noqa: E402
from slopekg.storage import read_jsonl, write_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SlopeKG multimodal asset catalog and quality report")
    parser.add_argument("paths", nargs="*", type=Path, help="Files or directories to scan")
    parser.add_argument("--output-dir", type=Path, default=PATHS.multimodal_dir)
    args = parser.parse_args()

    roots = args.paths or [PATHS.assets_dir, PATHS.root / "data" / "multimodal"]
    roots = [path if path.is_absolute() else PATHS.root / path for path in roots]
    output_dir = args.output_dir if args.output_dir.is_absolute() else PATHS.root / args.output_dir
    documents = read_jsonl(PATHS.parsed_dir / "documents.jsonl")
    slopes = {row["id"] for row in read_jsonl(PATHS.graph_dir / "nodes.jsonl") if row.get("type") == "Slope"}
    result = build_multimodal_outputs(roots, PATHS.root, output_dir, documents=documents, slope_ids=slopes)
    write_json(PATHS.schema_json, schema_payload())
    write_json(PATHS.interfaces_json, interface_payload())
    summary = {
        "output_dir": str(output_dir),
        "scanned_roots": [str(path) for path in roots],
        **result["quality"]["summary"],
        "status": result["quality"]["status"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
