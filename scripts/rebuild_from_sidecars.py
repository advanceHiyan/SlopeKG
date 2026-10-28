"""Rebuild basic products locally without OCR or model/API calls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.config import PATHS
from slopekg.extractors import extract_domain_candidates
from slopekg.pipeline import DemoPipeline, pipeline_run_guard, source_file_matches_record, ocr_result_is_current
from slopekg.ocr import with_layout_groups
from slopekg.storage import read_jsonl


def main():
    pipeline = DemoPipeline()
    with pipeline_run_guard(PATHS.output_dir / ".pipeline.lock"):
        parsed = pipeline.load_parsed_documents()
        known = {d["file_name"] for d in parsed["documents"]}
        if known != {p.name for p in PATHS.raw_dir.glob("*.pdf")} or any(
            not source_file_matches_record(PATHS.raw_dir / d["file_name"], d) for d in parsed["documents"]
        ):
            raise RuntimeError("原始PDF有变化，请先执行正常解析流水线，不能用旧逐页结果覆盖新文件。")
        task_map = {t["id"]: t for t in parsed["ocr_tasks"]}
        ocr = [r for r in read_jsonl(PATHS.parsed_dir / "ocr_results.jsonl")
               if ocr_result_is_current(r, task_map.get(r.get("task_id")))]
        ocr = with_layout_groups(ocr)
        parsed["ocr_results"] = ocr
        parsed["text_blocks"] = [*parsed["text_blocks"], *ocr]
        extracted = extract_domain_candidates(parsed)
        graph, evaluation = pipeline.build_graph(parsed, parsed["ocr_tasks"], ocr, extracted,
                                                 parse_mode="basic", activate=True)
        published = pipeline.publish_state(parsed, parsed["ocr_tasks"], ocr, graph, extracted, evaluation,
                                            parse_mode="basic", publication_stage="completed")
        print(json.dumps({"parse_mode": "basic", "api_calls": 0,
                          "extraction": extracted["stats"], "outputs": published["outputs"]}, ensure_ascii=False, indent=2))



if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
