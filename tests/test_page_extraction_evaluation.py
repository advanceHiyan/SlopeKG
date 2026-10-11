from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from slopekg.storage import write_jsonl
from scripts.evaluate_page_extraction import evaluate


class PageExtractionEvaluationTests(unittest.TestCase):

    def test_missing_source_does_not_disappear_from_recall_denominator(self):
        cases = [{"file_prefix": "sample", "page": 1, "page_type": "native_text", "expected_phrases": ["原文事实"]}]
        cases.append({"file_prefix": "nonexistent-document", "page": 1, "page_type": "native_text", "expected_phrases": ["必须保留的失败项"]})
        with tempfile.TemporaryDirectory() as directory:
            parsed = Path(directory)
            write_jsonl(parsed / "documents.jsonl", [{"id": "d1", "file_name": "sample.pdf"}])
            write_jsonl(parsed / "pages.jsonl", [{"document_id": "d1", "page": 1, "page_type": "native_text"}])
            write_jsonl(parsed / "text_blocks.jsonl", [{"document_id": "d1", "page": 1, "text": "原文事实"}])
            report = evaluate(parsed, cases)
        self.assertEqual(report["summary"]["expected_key_facts"], 2)
        self.assertEqual(report["summary"]["matched_key_facts"], 1)


if __name__ == "__main__":
    unittest.main()
