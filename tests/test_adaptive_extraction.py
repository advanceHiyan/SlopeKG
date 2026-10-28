from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from slopekg.config import PATHS
from slopekg.extractors import extract_section_values, normalize_reference_station
from slopekg.llm import deepseek_key_info
from slopekg.ocr import normalize_paddle_result
from slopekg.parsers import classify_page


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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".secrets").mkdir()
            (root / ".secrets" / "deepseek_api_key.txt").write_text("unit-test-placeholder", encoding="utf-8")
            with patch("slopekg.llm.ROOT", root), patch.dict("os.environ", {"DEEPSEEK_API_KEY": "environment-placeholder"}):
                key, source = deepseek_key_info()
            self.assertEqual(key, "unit-test-placeholder")
            self.assertEqual(source, "project_secret_file")



    def test_section_values_extract_static_risk_fields_with_history_boundary(self):
        values = extract_section_values(
            "该段为开挖岩质边坡，边坡长度约75m，高约30m，开挖边坡坡度约68-80°。"
            "坡面产状为120º∠85º，L1产状160º∠70º，坡面绿植发育。"
            "大里程段20m范围2年前发生过垮塌，地下水较发育。"
        )

        self.assertEqual(values["slope_type"], "路堑")
        self.assertEqual(values["material_nature"], "岩质")
        self.assertEqual(values["slope_height_max_m"], 30)
        self.assertEqual(values["slope_gradient_max_deg"], 80)
        self.assertEqual(values["slope_aspect_deg"], 120)
        self.assertEqual(values["vegetation_condition"]["density"], "茂密")
        self.assertTrue(values["structural_planes"])
        self.assertFalse(values["deformation_observations"][0]["current_status_known"])



if __name__ == "__main__":
    unittest.main()
