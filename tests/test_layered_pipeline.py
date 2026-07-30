from __future__ import annotations

import unittest
import uuid

from slopekg.config import PATHS
from slopekg.extractors import extract_section_facts, extract_stability_scenarios
from slopekg.llm import DEEPSEEK_MAX_OUTPUT_TOKENS
from slopekg.pipeline import PipelineAlreadyRunningError, pipeline_run_guard
from slopekg.semantic import detect_section_gaps, source_fingerprint
from slopekg.server import SlopeKGHandler, automatic_basic_options


class LayeredPipelineTests(unittest.TestCase):
    def test_server_defaults_to_basic_parse(self) -> None:
        options = SlopeKGHandler.pipeline_options(None, {})  # type: ignore[arg-type]
        self.assertEqual(options["parse_mode"], "basic")
        self.assertNotIn("use_llm", options)

    def test_pipeline_lock_rejects_a_second_run(self) -> None:
        lock_path = PATHS.output_dir / f".test_pipeline_{uuid.uuid4().hex}.lock"
        try:
            with pipeline_run_guard(lock_path):
                with self.assertRaises(PipelineAlreadyRunningError):
                    with pipeline_run_guard(lock_path):
                        pass
        finally:
            lock_path.unlink(missing_ok=True)

    def test_automatic_update_runs_full_ocr_only_for_changed_documents(self) -> None:
        options = automatic_basic_options()
        self.assertEqual(options["ocr_pages"], 1000)
        self.assertEqual(options["ocr_scope"], "changed")

    def test_basic_and_deep_outputs_are_separate(self) -> None:
        self.assertTrue(PATHS.basic_graph_json.exists())
        self.assertTrue(PATHS.deep_graph_json.exists())
        self.assertNotEqual(PATHS.basic_graph_json, PATHS.deep_graph_json)

    def test_deepseek_uses_documented_maximum_output(self) -> None:
        self.assertEqual(DEEPSEEK_MAX_OUTPUT_TOKENS, 393216)

    def test_deterministic_facts_suppress_redundant_section_gaps(self) -> None:
        samples = [{"page": 1, "text": "边坡治理措施为挂网锚喷，天然工况稳定系数为1.14。"}]
        gaps = detect_section_gaps(
            {},
            samples,
            {"measures": ["挂网锚喷"], "stability_scenarios": [{"condition": "天然工况", "safety_factor": 1.14}]},
        )
        self.assertNotIn("protection_records", gaps)
        self.assertNotIn("stability_conclusions", gaps)

    def test_survey_stability_conditions_are_extracted_as_distinct_scenarios(self) -> None:
        text = "天然工况下的稳定性系数为1.14，属于基本稳定状态，暴雨工况下稳定性系数为1.04，属于欠稳定状态。"
        scenarios = extract_stability_scenarios(text)
        self.assertEqual([(row["condition"], row["safety_factor"]) for row in scenarios], [("天然工况", 1.14), ("暴雨工况", 1.04)])

    def test_dangerous_rock_natural_and_saturated_states_are_extracted(self) -> None:
        text = (
            "天然状态下稳定性系数为1.18，危岩体处于基本稳定；"
            "饱水状态下单个危岩体稳定系数为1.05，处于欠稳定状态。"
        )
        scenarios = extract_stability_scenarios(text)

        self.assertEqual(
            [(row["condition"], row["safety_factor"], row["status"]) for row in scenarios],
            [("天然状态", 1.18, "基本稳定"), ("饱和状态", 1.05, "欠稳定")],
        )

    def test_stability_before_next_heading_stays_with_previous_slope(self) -> None:
        by_page = {
            ("doc1", 1): [{
                "id": "b1",
                "document_id": "doc1",
                "page": 1,
                "bbox": [0, 0, 500, 100],
                "text": "5.8 K2412+602-K2412+664 右侧不稳定边坡 稳定性分析",
            }],
            ("doc1", 2): [{
                "id": "b2",
                "document_id": "doc1",
                "page": 2,
                "bbox": [0, 0, 500, 300],
                "text": (
                    "天然工况下的稳定性系数为1.14，属于基本稳定状态，"
                    "暴雨工况下稳定性系数为1.04，属于欠稳定状态。"
                    "5.9 K2391+966-K2392+030 右侧危岩体 基本情况"
                ),
            }],
        }
        rows = extract_section_facts(
            {"doc1"},
            by_page,
            {"K2412+602-K2412+664", "K2391+966-K2392+030"},
            {"doc1": {"K2412+602-K2412+664", "K2391+966-K2392+030"}},
        )
        scenarios = {row["station"]: row["stability_scenarios"] for row in rows}

        self.assertEqual(len(scenarios["K2412+602-K2412+664"]), 2)
        self.assertEqual(scenarios["K2391+966-K2392+030"], [])

    def test_station_typo_in_prose_does_not_override_active_section(self) -> None:
        by_page = {
            ("doc1", 1): [{
                "id": "b1",
                "document_id": "doc1",
                "page": 1,
                "bbox": [0, 0, 500, 100],
                "text": "5.9 K2391+966-K2392+030 右侧危岩体 基本情况",
            }],
            ("doc1", 2): [{
                "id": "b2",
                "document_id": "doc1",
                "page": 2,
                "bbox": [0, 0, 500, 300],
                "text": (
                    "稳定性分析：K2388+976-K2389+036 左侧危岩体整体基本稳定。"
                    "天然状态下稳定性系数为1.18，状态为基本稳定；"
                    "饱和状态下单个危岩体稳定系数为1.05，状态为欠稳定。"
                ),
            }],
        }
        rows = extract_section_facts(
            {"doc1"},
            by_page,
            {"K2391+966-K2392+030", "K2388+976-K2389+036"},
            {"doc1": {"K2391+966-K2392+030", "K2388+976-K2389+036"}},
        )
        scenarios = {row["station"]: row["stability_scenarios"] for row in rows}

        self.assertEqual(len(scenarios["K2391+966-K2392+030"]), 2)
        self.assertEqual(scenarios.get("K2388+976-K2389+036", []), [])

    def test_figure_caption_for_next_slope_starts_new_section_context(self) -> None:
        by_page = {
            ("doc1", 1): [{
                "id": "b1",
                "document_id": "doc1",
                "page": 1,
                "bbox": [0, 0, 500, 100],
                "text": "5.3 K2398+300-K2398+439 右侧危岩体 基本情况",
            }],
            ("doc1", 2): [{
                "id": "b2",
                "document_id": "doc1",
                "page": 2,
                "bbox": [0, 0, 500, 300],
                "text": (
                    "前一边坡坡面植被发育。"
                    "图5.4-1 K2401+227-K2401+307 右侧危岩体全景图。"
                    "该处边坡为岩质边坡，目前坡表大量基岩裸露。"
                ),
            }],
        }
        rows = extract_section_facts(
            {"doc1"},
            by_page,
            {"K2398+300-K2398+439", "K2401+227-K2401+307"},
            {"doc1": {"K2398+300-K2398+439", "K2401+227-K2401+307"}},
        )
        by_station = {row["station"]: row for row in rows}

        self.assertEqual(by_station["K2398+300-K2398+439"]["vegetation_condition"]["density"], "茂密")
        self.assertEqual(by_station["K2401+227-K2401+307"]["material_nature"], "岩质")

    def test_semantic_cache_changes_with_deterministic_input(self) -> None:
        samples = [{"page": 1, "text": "原文"}]
        first = source_fingerprint("K1+000-K1+100", samples, "deepseek-v4-flash", {"measures": ["放坡"]})
        second = source_fingerprint("K1+000-K1+100", samples, "deepseek-v4-flash", {"measures": ["锚杆"]})
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
