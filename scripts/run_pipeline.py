from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from slopekg.pipeline import DemoPipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SlopeKG demo parsing/OCR/graph pipeline")
    parser.add_argument("--ocr-pages", type=int, default=1000, help="Maximum adaptive OCR queue pages (default: all queued pages)")
    parser.add_argument("--force-ocr", action="store_true", help="Re-render OCR page images")
    parser.add_argument("--mode", choices=["basic", "deep"], default="basic", help="basic is fully local; deep adds validated LLM semantics")
    parser.add_argument("--deep", action="store_true", help="Shortcut for --mode deep")
    parser.add_argument("--no-llm", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--llm-model", default="deepseek-v4-flash")
    parser.add_argument("--reuse-parsed", action="store_true", help="Reuse existing PDF sidecars; only rerun extraction and graph generation")
    args = parser.parse_args()
    state = DemoPipeline().run(
        ocr_pages=args.ocr_pages,
        force_ocr=args.force_ocr,
        parse_mode="deep" if args.deep and not args.no_llm else args.mode,
        llm_model=args.llm_model,
        reuse_parsed=args.reuse_parsed,
    )
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
