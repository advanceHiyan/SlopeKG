from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.rule_extraction import RuleExtractionPipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract a candidate rule and formula library from standards and project PDFs")
    parser.add_argument("--no-llm", action="store_true", help="Skip DeepSeek semantic structuring")
    parser.add_argument("--force-ocr", action="store_true", help="Ignore cached rule-page OCR")
    parser.add_argument("--llm-model", default="deepseek-v4-flash")
    args = parser.parse_args()
    state = RuleExtractionPipeline().run(
        use_llm=not args.no_llm,
        llm_model=args.llm_model,
        force_ocr=args.force_ocr,
        progress_callback=lambda percent, stage, message: print(f"[{percent:3d}%] {stage}: {message}", flush=True),
    )
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
