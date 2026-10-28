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
    parser.add_argument("--mode", choices=["basic"], default="basic", help="Local parsing/OCR and deterministic extraction")
    parser.add_argument("--reuse-parsed", action="store_true", help="Reuse PDF sidecars only if they still match current source files")
    parser.add_argument("--recognizer", choices=["legacy", "multimodal", "knowledge"], default="legacy",
                        help="Select basic, independent multimodal, or document-wide knowledge recognition")
    parser.add_argument("--pdf", type=Path, action="append", help="Explicit PDF input; repeat for multiple files (knowledge/multimodal)")
    parser.add_argument("--model-profile", type=Path, help="JSON endpoint/model/credential profile (knowledge/multimodal)")
    parser.add_argument("--review-profile", type=Path, help="Optional separate model for source verification and coverage audit")
    parser.add_argument("--visual-settings", type=Path, help="JSON visual context/render/call budgets")
    parser.add_argument("--knowledge-settings", type=Path, help="JSON document-wide knowledge budgets (knowledge route only)")
    parser.add_argument("--prepare-only", action="store_true", help="Inventory PDFs locally without any model/API/OCR execution")
    args = parser.parse_args()
    try:
        state = DemoPipeline().run(
            ocr_pages=args.ocr_pages,
            force_ocr=args.force_ocr,
            ocr_device=args.ocr_device,
            ocr_workers=args.ocr_workers,
            ocr_batch_size=args.ocr_batch_size,
            parse_mode=args.mode,
            reuse_parsed=args.reuse_parsed,
            recognizer=args.recognizer,
            input_pdfs=args.pdf,
            model_profile=args.model_profile,
            review_profile=args.review_profile,
            visual_settings=json.loads(args.visual_settings.read_text(encoding="utf-8-sig")) if args.visual_settings else None,
            knowledge_settings=json.loads(args.knowledge_settings.read_text(encoding="utf-8-sig")) if args.knowledge_settings else None,
            prepare_only=args.prepare_only,
        )
    except (PipelineAlreadyRunningError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    display = state
    if args.recognizer in {"multimodal", "knowledge"}:
        from slopekg.config import PATHS
        display = {key: state[key] for key in ("recognizer", "processing_status", "summary", "precision", "recall", "metric_note")}
        folder = 'knowledge-recognition' if args.recognizer == 'knowledge' else 'recognition'
        display["result_file"] = str(PATHS.output_dir / folder / "run.json")
        display["planning_estimates"] = [d.get("planning_estimate") for d in state["documents"]]
    print(json.dumps(display, ensure_ascii=False, indent=2))
    if state.get("processing_status") in {"partial", "failed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
