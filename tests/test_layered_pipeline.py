from __future__ import annotations

import unittest

from slopekg.config import PATHS
from slopekg.extractors import extract_stability_scenarios
from slopekg.llm import DEEPSEEK_MAX_OUTPUT_TOKENS
from slopekg.semantic import detect_section_gaps, source_fingerprint
from slopekg.server import SlopeKGHandler, automatic_basic_options


class LayeredPipelineTests(unittest.TestCase):
    def test_server_defaults_to_basic_parse(self) -> None:
        options = SlopeKGHandler.pipeline_options(None, {})  # type: ignore[arg-type]
        self.assertEqual(options["parse_mode"], "basic")
        self.assertNotIn("use_llm", options)

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

    def test_semantic_cache_changes_with_deterministic_input(self) -> None:
        samples = [{"page": 1, "text": "原文"}]
        first = source_fingerprint("K1+000-K1+100", samples, "deepseek-v4-flash", {"measures": ["放坡"]})
        second = source_fingerprint("K1+000-K1+100", samples, "deepseek-v4-flash", {"measures": ["锚杆"]})
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
