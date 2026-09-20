from __future__ import annotations

import unittest
import re
from unittest.mock import patch

from slopekg.config import PATHS
from slopekg.engineering import extract_parameter_cells, extract_factor_cells, extract_engineering_tables
from slopekg.extractors import extract_domain_candidates
from slopekg.graph_builder import build_automatic_graph, unique_stability_scenarios
from slopekg.risk import worst_stability_record, stability_record
from slopekg.storage import read_json, read_jsonl
from scripts.evaluate_engineering_extraction import evaluate
from scripts.rebuild_from_sidecars import revalidate_cached


class EngineeringTableTests(unittest.TestCase):
    def test_cached_revalidation_preserves_failed_candidates(self):
        extracted = {"slopes": [{"route_code": "G555", "station": "K100+100-K100+200"}]}
        failed = {"route_code": "G555", "station": "K100+100-K100+200", "validation_status": "failed"}
        with patch("scripts.rebuild_from_sidecars.validate_candidate") as validator:
            self.assertEqual(revalidate_cached(extracted, [failed]), [failed])
            validator.assert_not_called()

    def test_data_page_has_unique_ids_and_review_panel(self):
        html = (PATHS.web_dir / "data-status.html").read_text(encoding="utf-8")
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count("engineeringReviewRows"), 1)

    def test_thresholds_and_monitoring_requirements_are_not_results(self):
        self.assertEqual(extract_factor_cells([["工况", "安全系数"], ["天然", "1.15"]]), [])
        self.assertEqual(extract_factor_cells([["工况", "稳定系数"], ["天然", "≥1.15"]]), [])
        self.assertEqual(extract_parameter_cells([["应提供监测数据一览表", "地下水位"]]), [])

    def test_unknown_units_and_shifted_rows_are_not_guessed(self):
        self.assertEqual(extract_parameter_cells([["岩性", "项目", "天然状态"], ["黏土", "内聚力", "22"]]), [])
        self.assertEqual(extract_parameter_cells([["岩性", "项目", "天然状态"], ["黏土", "重度(kN/m3)", "19", "extra"]]), [])

    def test_unknown_material_is_not_filled_from_another_table(self):
        rows = [["岩性", "项目", "天然状态"], ["", "内聚力(kPa)", "20"]]
        self.assertEqual(extract_parameter_cells(rows), [])

    def test_distinct_modes_are_not_called_sections_or_deduplicated(self):
        rows = [["破坏模式", "工况", "危岩稳定性系数F", "稳定性评价"],
                ["坠落式", "暴雨", "1.1", "欠稳定"], ["滑移式", "暴雨", "1.1", "欠稳定"]]
        results = extract_factor_cells(rows)
        self.assertEqual(len(unique_stability_scenarios(results)), 2)
        self.assertNotIn("section", results[0])
        self.assertEqual(results[0]["failure_mode"], "坠落式")

    def test_ambiguous_or_unscoped_records_do_not_drive_priority(self):
        self.assertIsNone(worst_stability_record([dict(fs=0.8, status="不稳定", eligible_for_screening=False)]))
        self.assertIsNone(worst_stability_record([dict(fs=0.8, status="不稳定", quality_issues=["conflict"])]))

    def test_historical_existing_slope_is_not_a_current_observation(self):
        row = stability_record({"node": {"props": {"analysis_scope": "现状边坡", "fs": 1.14}}})
        self.assertFalse(row["current_status_known"])
        self.assertTrue(row["document_scope_is_existing_slope"])

    def test_cross_route_and_future_heading_do_not_assign_table(self):
        parsed = {"documents": [{"id": "d", "file_name": "G555.pdf", "kind": "勘察报告"}],
                  "pages": [{"document_id": "d", "page": 1, "width": 1200}],
                  "text_blocks": [{"id": "b", "document_id": "d", "page": 1, "bbox": [650, 600, 1000, 620], "text": "5.1 K100+100-K100+200 滑坡"}],
                  "tables": [{"id": "t", "document_id": "d", "page": 1, "bbox": [50, 100, 500, 200],
                              "rows": [["岩性", "项目", "天然状态"], ["黏土", "内聚力(kPa)", "20"]]}]}
        registry = [{"route_code": "G555", "station": "K100+100-K100+200"}]
        self.assertIsNone(extract_engineering_tables(parsed, registry)[0]["station"])
        parsed["text_blocks"][0]["bbox"] = [50, 50, 500, 70]
        self.assertEqual(extract_engineering_tables(parsed, registry)[0]["station"], "K100+100-K100+200")
        registry[0]["route_code"] = "G666"
        self.assertIsNone(extract_engineering_tables(parsed, registry)[0]["station"])

    def test_document_scope_recommended_parameters_are_not_an_unresolved_slope(self):
        parsed = {
            "documents": [{"id": "d", "file_name": "G555.pdf", "kind": "勘察报告"}],
            "pages": [{"document_id": "d", "page": 1, "width": 1200}],
            "text_blocks": [
                {"id": "b1", "document_id": "d", "page": 1, "bbox": [50, 20, 500, 60],
                 "text": "根据现场调查及区域经验，项目区推荐的计算参数见表1。"},
                {"id": "b2", "document_id": "d", "page": 1, "bbox": [50, 65, 500, 85],
                 "text": "表1 稳定性计算参数推荐表"},
            ],
            "tables": [{"id": "t", "document_id": "d", "page": 1, "bbox": [50, 90, 500, 200],
                        "rows": [["岩性", "项目", "天然状态"], ["黏土", "内聚力(kPa)", "20"]]}],
        }
        record = extract_engineering_tables(parsed, [])[0]
        self.assertIsNone(record["station"])
        self.assertEqual(record["association_scope"], "document")
        self.assertEqual(record["association_basis"], "document_level_recommended_parameters")
        self.assertEqual(record["review_status"], "accepted_document_scope")
        self.assertEqual(record["quality_issues"], [])


class EngineeringCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = {k: read_jsonl(PATHS.parsed_dir / f"{k}.jsonl") for k in ("documents", "pages", "text_blocks", "tables")}
        cls.extracted = extract_domain_candidates(cls.parsed)

    def test_visually_reviewed_gold_tables(self):
        gold = read_json(PATHS.root / "tests/fixtures/engineering_gold.json")
        report = evaluate(self.extracted["engineering_records"], gold["cases"])
        self.assertEqual(report["cases_passed"], report["cases_total"], report)

    def test_provenance_and_conservative_graph_integration(self):
        graph = build_automatic_graph(self.parsed, self.extracted, [])
        self.assertEqual(sum(n["type"] == "Slope" for n in graph["nodes"]), 62)
        self.assertTrue(any(n["type"] == "MaterialParameterSet" for n in graph["nodes"]))
        records = [n for n in graph["nodes"] if n["type"] == "StabilityAnalysis" and n["props"].get("quality_issues")]
        self.assertTrue(records)
        self.assertTrue(all(n["props"]["eligible_for_screening"] is False for n in records))
        self.assertTrue(all(r.get("source_file") and r.get("page") and r.get("bbox") for r in self.extracted["engineering_records"]))
        document_parameters = [n for n in graph["nodes"] if n["type"] == "MaterialParameterSet"
                               and n["props"].get("association_scope") == "document"]
        self.assertEqual(len(document_parameters), 1)
        self.assertFalse(document_parameters[0]["props"]["eligible_for_screening"])

    def test_control_points_are_not_slope_endpoints(self):
        self.assertEqual(len(self.extracted["coordinates"]), 13)
        for row in self.extracted["coordinates"]:
            self.assertTrue(row["control_points"])
            self.assertIsNone(row["start_coordinate"])
            self.assertIsNone(row["end_coordinate"])


if __name__ == "__main__":
    unittest.main()
