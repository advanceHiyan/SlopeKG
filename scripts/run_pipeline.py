from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from slopekg.pipeline import DemoPipeline, PipelineAlreadyRunningError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SlopeKG demo parsing/OCR/graph pipeline")
    parser.add_argument("--ocr-pages", type=int, default=1000, help="Maximum adaptive OCR queue pages (default: all queued pages)")
    parser.add_argument("--force-ocr", action="store_true", help="Re-render OCR page images")
    parser.add_argument("--ocr-device", choices=["auto", "cpu", "gpu"], default="auto", help="PaddleOCR inference device")
    parser.add_argument("--ocr-workers", type=int, default=4, help="Concurrent PDF page rendering workers")
    parser.add_argument("--ocr-batch-size", type=int, default=0, help="OCR inference batch size; 0 selects 1 on CPU or 4 on GPU")
    parser.add_argument("--mode", choices=["basic", "deep"], default="basic", help="basic is fully local; deep adds validated LLM semantics")
    parser.add_argument("--deep", action="store_true", help="Shortcut for --mode deep")
    parser.add_argument("--no-llm", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--llm-model", default="deepseek-v4-flash")
    parser.add_argument("--reuse-parsed", action="store_true", help="Reuse existing PDF sidecars; only rerun extraction and graph generation")
    args = parser.parse_args()
    try:
        state = DemoPipeline().run(
            ocr_pages=args.ocr_pages,
            force_ocr=args.force_ocr,
            ocr_device=args.ocr_device,
            ocr_workers=args.ocr_workers,
            ocr_batch_size=args.ocr_batch_size,
            parse_mode="deep" if args.deep and not args.no_llm else args.mode,
            llm_model=args.llm_model,
            reuse_parsed=args.reuse_parsed,
        )
    except PipelineAlreadyRunningError as exc:
        parser.error(str(exc))
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
