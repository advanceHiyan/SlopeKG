from __future__ import annotations

import unittest

from slopekg.config import PATHS
from slopekg.storage import read_json
from scripts.evaluate_page_extraction import evaluate


class PageExtractionEvaluationTests(unittest.TestCase):
    def test_visually_reviewed_page_types_and_key_facts(self) -> None:
        cases = read_json(PATHS.root / "tests/fixtures/page_extraction_gold.json")["cases"]
        report = evaluate(PATHS.parsed_dir, cases)
        self.assertEqual(report["summary"]["cases_total"], len(cases))
        self.assertGreaterEqual(len(cases), 11)
        # The gold set deliberately retains currently missed facts, so it can
        # measure progress instead of being weakened until every case passes.
        self.assertGreaterEqual(report["summary"]["key_fact_recall"], 0.90, report)
        missed = [check for row in report["cases"]
                  for check in row.get("checks", []) if not check["matched"]]
        self.assertEqual(len(missed), report["summary"]["expected_key_facts"] - report["summary"]["matched_key_facts"])
        self.assertEqual(
            sum(row["total"] for row in report["summary"]["by_page_type"].values()),
            len(cases),
        )

    def test_missing_source_does_not_disappear_from_recall_denominator(self):
        cases = read_json(PATHS.root / "tests/fixtures/page_extraction_gold.json")["cases"][:1]
        cases.append({"file_prefix": "nonexistent-document", "page": 1, "page_type": "native_text", "expected_phrases": ["必须保留的失败项"]})
        report = evaluate(PATHS.parsed_dir, cases)
        self.assertEqual(report["summary"]["expected_key_facts"], 6)
        self.assertEqual(report["summary"]["matched_key_facts"], 5)


if __name__ == "__main__":
    unittest.main()
