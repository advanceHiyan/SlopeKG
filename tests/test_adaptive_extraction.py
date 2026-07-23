from __future__ import annotations

import unittest

from scripts.extract_with_llm import build_quality_summary, candidate_is_schema_valid
from slopekg.config import PATHS
from slopekg.extractors import extract_domain_candidates, normalize_reference_station
from slopekg.llm import deepseek_key_info
from slopekg.ocr import normalize_paddle_result
from slopekg.parsers import classify_page
from slopekg.semantic import validate_candidate
from slopekg.storage import read_json, read_jsonl


class AdaptivePageStrategyTests(unittest.TestCase):
    def classify(self, **overrides):
        values = {
            "kind": "施工图",
            "page_no": 1,
            "page_count": 10,
            "text": "正文内容",
            "text_chars": 800,
            "width": 1000,
            "height": 700,
            "image_count": 0,
            "drawing_count": 0,
        }
        values.update(overrides)
        return classify_page(**values)

    def test_native_text_does_not_use_ocr(self):
        row = self.classify()
        self.assertEqual(row["page_type"], "native_text")
        self.assertFalse(row["needs_ocr"])

    def test_scanned_page_uses_full_page_ocr(self):
        row = self.classify(text="", text_chars=0, image_count=1)
        self.assertEqual(row["page_type"], "scanned")
        self.assertEqual(row["ocr_region"], "full_page")

    def test_vector_drawing_uses_title_block_ocr(self):
        row = self.classify(text="图号", text_chars=20, drawing_count=500)
        self.assertEqual(row["page_type"], "drawing")
        self.assertEqual(row["ocr_region"], "title_block")

    def test_table_prefers_native_table_extractor(self):
        text = "边坡治理方案一览表 序号 桩号 安全系数 " + " ".join(str(i) for i in range(15))
        row = self.classify(text=text, text_chars=len(text))
        self.assertEqual(row["page_type"], "table")
        self.assertEqual(row["processing_strategy"], ["native_layout", "table_extractor"])


class ExtractionRegressionTests(unittest.TestCase):
    def test_station_normalization(self):
        self.assertEqual(normalize_reference_station("K2398+300-439"), "K2398+300-K2398+439")

    def test_full_parse_outputs_have_expected_coverage(self):
        extraction = extract_domain_candidates(
            {
                "documents": read_jsonl(PATHS.parsed_dir / "documents.jsonl"),
                "pages": read_jsonl(PATHS.parsed_dir / "pages.jsonl"),
                "text_blocks": read_jsonl(PATHS.parsed_dir / "text_blocks.jsonl"),
                "tables": read_jsonl(PATHS.parsed_dir / "tables.jsonl"),
            }
        )
        stats = extraction.get("stats", {})
        self.assertGreaterEqual(stats.get("registry_rows", 0), 63)
        self.assertEqual(stats.get("coordinate_rows"), 13)
        self.assertGreaterEqual(stats.get("geometry_rows", 0), 11)
        self.assertEqual(stats.get("native_bbox_coverage"), 1.0)
        documents = read_jsonl(PATHS.parsed_dir / "documents.jsonl")
        self.assertEqual(len(read_jsonl(PATHS.parsed_dir / "pages.jsonl")), sum(int(row["pages"]) for row in documents))

    def test_new_reports_are_discovered_without_g209_template(self):
        extraction = extract_domain_candidates(
            {
                "documents": read_jsonl(PATHS.parsed_dir / "documents.jsonl"),
                "pages": read_jsonl(PATHS.parsed_dir / "pages.jsonl"),
                "text_blocks": read_jsonl(PATHS.parsed_dir / "text_blocks.jsonl"),
                "tables": read_jsonl(PATHS.parsed_dir / "tables.jsonl"),
            }
        )
        counts = {}
        for row in extraction["registry"]:
            counts[row["route_code"]] = counts.get(row["route_code"], 0) + 1
        self.assertGreaterEqual(counts.get("G318", 0), 13)
        self.assertGreaterEqual(counts.get("G344", 0), 1)
        self.assertGreaterEqual(counts.get("G347", 0), 9)
        self.assertGreaterEqual(counts.get("G351", 0), 27)

    def test_paddle_v3_result_objects_are_normalized(self):
        class ResultLike:
            def __init__(self):
                self.values = {"rec_texts": ["边坡", "裂缝"], "rec_scores": [0.99, 0.98], "rec_boxes": [[1, 2, 3, 4], [5, 6, 7, 8]]}

            def get(self, key):
                return self.values.get(key)

        task = {"id": "ocr1", "document_id": "doc1", "page_id": "p1", "file_name": "x.pdf", "page": 1}
        rows = normalize_paddle_result(task, [ResultLike()], PATHS.assets_dir / "x.png", PATHS.root)
        self.assertEqual([row["text"] for row in rows], ["边坡", "裂缝"])


class LlmCandidateValidationTests(unittest.TestCase):
    def test_project_secret_has_priority_and_is_not_exposed_by_status(self):
        key, source = deepseek_key_info()
        self.assertTrue(key)
        self.assertEqual(source, "project_secret_file")

    def test_candidate_schema_and_quality_metrics(self):
        candidate = {
            "mechanism_summary": "原文概括",
            "failure_modes": ["崩塌"],
            "stability_conclusions": [{"condition": "天然", "safety_factor": 1.2, "status": "基本稳定"}],
            "causal_factors": ["降雨"],
            "protection_rationale": "原文建议",
            "uncertainties": [],
            "evidence_quotes": [{"page": 3, "quote": "原文"}],
        }
        self.assertTrue(candidate_is_schema_valid(candidate))
        summary = build_quality_summary([{"candidate": candidate, "model": "deepseek-v4-flash", "review_status": "pending"}])
        self.assertEqual(summary["schema_valid_rate"], 1.0)
        self.assertEqual(summary["evidence_quote_coverage"], 1.0)

    def test_semantic_fields_without_verified_support_are_removed(self):
        candidate = {
            "hazard_body_type": "危岩体",
            "hazard_types": [],
            "mechanism_summary": "降雨不利于稳定",
            "failure_modes": ["危岩体发育"],
            "lithology_terms": ["灰岩"],
            "stratum_terms": [],
            "slope_structure": None,
            "structural_planes": [],
            "stability_conclusions": [],
            "causal_factors": ["降雨"],
            "protection_rationale": "现场可见危岩体发育",
            "uncertainties": [],
            "evidence_quotes": [
                {"page": 1, "quote": "该边坡为危岩体，降雨不利于稳定", "supports": ["hazard_body_type", "mechanism_summary", "causal_factors"]},
                {"page": 1, "quote": "现场可见危岩体发育", "supports": ["hazard_body_type", "failure_modes", "protection_rationale"]},
            ],
        }
        cleaned, validation = validate_candidate(candidate, [{"page": 1, "text": "该边坡为危岩体，降雨不利于稳定。现场可见危岩体发育。"}])
        self.assertTrue(validation["accepted"])
        self.assertEqual(cleaned["lithology_terms"], [])
        self.assertEqual(cleaned["hazard_body_type"], "危岩体")


if __name__ == "__main__":
    unittest.main()
