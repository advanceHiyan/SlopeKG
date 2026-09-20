"""Rebuild local products without OCR or API calls; revalidate cached semantics."""
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
from slopekg.semantic import select_source_samples, validate_candidate, semantic_summary
from slopekg.storage import read_json, read_jsonl


def revalidate_cached(extracted, cached):
    slopes = {(s.get("route_code"), s["station"]): s for s in extracted["slopes"]}
    rows = []
    for row in cached:
        slope = slopes.get((row.get("route_code"), row.get("station")))
        if not slope:
            continue
        if row.get("validation_status") != "passed":
            rows.append(row)  # Keep failed candidates; do not inflate the pass rate.
            continue
        samples = select_source_samples(slope)
        candidate, validation = validate_candidate(row.get("candidate"), samples)
        rows.append({**row, "candidate": candidate, "validation": validation,
                     "validation_status": "passed" if validation["accepted"] else "failed",
                     "reuse_mode": "offline_source_revalidation_no_api"})
    return rows


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
        state = read_json(PATHS.state_json, {})
        mode = state.get("parse_mode", "basic")
        basic_summary = semantic_summary([], enabled=False, model="deepseek-v4-flash")
        graph, evaluation = pipeline.build_graph(parsed, parsed["ocr_tasks"], ocr, extracted, [], basic_summary,
                                                 parse_mode="basic", activate=mode != "deep")
        summary = basic_summary
        if mode == "deep":
            rows = revalidate_cached(extracted, read_jsonl(PATHS.extracted_dir / "llm_candidates.jsonl"))
            summary = semantic_summary(rows, enabled=True, model="deepseek-v4-flash")
            summary.update(api_calls=0, cache_revalidated=sum(r.get("reuse_mode") == "offline_source_revalidation_no_api" for r in rows), mode="offline_source_revalidation_no_api")
            graph, evaluation = pipeline.build_graph(parsed, parsed["ocr_tasks"], ocr, extracted, rows, summary,
                                                     parse_mode="deep", activate=True)
        published = pipeline.publish_state(parsed, parsed["ocr_tasks"], ocr, graph, extracted, evaluation,
                                            summary, parse_mode=mode, publication_stage="completed")
        print(json.dumps({"parse_mode": mode, "api_calls": 0, "semantic_revalidated": summary.get("cache_revalidated", 0),
                          "extraction": extracted["stats"], "outputs": published["outputs"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
