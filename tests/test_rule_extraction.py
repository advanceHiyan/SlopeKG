from __future__ import annotations

import unittest
from slopekg.rule_extraction import (
    RuleExtractionPipeline,
    derive_table_rules,
    extract_deterministic_rule_candidates,
    validate_llm_payload,
)
from slopekg.schema import interface_payload


class RuleExtractionTests(unittest.TestCase):
    def test_catalog_searches_nested_source_directories(self) -> None:
        rows = RuleExtractionPipeline().catalog_documents()
        nested = next(row for row in rows if row["code"] == "JTG C20-2011")
        self.assertIn("人工智能资料", nested["path"])

    def test_deterministic_threshold_rule_keeps_exact_evidence(self) -> None:
        pages = [{
            "id": "doc1_p0001",
            "document_id": "doc1",
            "document_code": "TEST-1",
            "source_type": "official_standard",
            "page": 1,
            "text": "表1 稳定状态\n稳定系数 Fs≤1.00 时为不稳定\n其余见规范。",
        }]

        rules = extract_deterministic_rule_candidates(pages)

        self.assertEqual(len(rules), 1)
        self.assertIn("Fs≤1.00", rules[0]["evidence_quote"])
        self.assertEqual(rules[0]["output_text"], "不稳定")
        self.assertFalse(rules[0]["execution_enabled"])

    def test_llm_candidate_requires_quote_from_claimed_page(self) -> None:
        document = {"id": "doc1", "code": "TEST-1"}
        pages = [{
            "id": "doc1_p0002",
            "document_id": "doc1",
            "document_code": "TEST-1",
            "source_type": "official_standard",
            "page": 2,
            "text": "稳定系数 Fs≤1.00 时判定为不稳定状态。",
        }]
        payload = {
            "rules": [
                {
                    "title": "稳定性判定",
                    "page": 2,
                    "evidence_quote": "稳定系数 Fs≤1.00 时判定为不稳定状态。",
                },
                {
                    "title": "被模型改写的错误候选",
                    "page": 2,
                    "evidence_quote": "稳定系数 Fs≤1.05 时判定为不稳定状态。",
                },
            ],
            "formulas": [],
            "threshold_tables": [],
        }

        result = validate_llm_payload(document, pages, payload)

        self.assertEqual(len(result["rules"]), 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["rules"][0]["evidence_validation"], "passed")

    def test_formula_requires_expression_in_page_text(self) -> None:
        document = {"id": "doc1", "code": "TEST-1"}
        pages = [{
            "id": "doc1_p0002",
            "document_id": "doc1",
            "document_code": "TEST-1",
            "page": 2,
            "text": "按式计算滑坡推力。\nTi = Ks Wi sinαi − Wi cosαi tanφi",
        }]
        payload = {
            "rules": [],
            "threshold_tables": [],
            "formulas": [
                {
                    "name": "正确公式",
                    "page": 2,
                    "expression_text": "Ti = Ks Wi sinαi − Wi cosαi tanφi",
                    "evidence_quote": "Ti = Ks Wi sinαi − Wi cosαi tanφi",
                },
                {
                    "name": "被模型改写的公式",
                    "page": 2,
                    "expression_text": "Ti = 2 Ks Wi sinαi",
                    "evidence_quote": "按式计算滑坡推力。",
                },
            ],
        }

        result = validate_llm_payload(document, pages, payload)

        self.assertEqual(len(result["formulas"]), 1)
        self.assertEqual(result["rejected"], 1)

    def test_rule_interfaces_are_marked_implemented(self) -> None:
        interfaces = {(row["method"], row["path"]): row["status"] for row in interface_payload()["interfaces"]}
        self.assertEqual(interfaces[("GET", "/api/risk/rules")], "implemented")
        self.assertEqual(interfaces[("GET", "/api/rules/library")], "implemented")

    def test_threshold_table_rows_become_atomic_rules(self) -> None:
        tables = [{
            "id": "table1",
            "title": "滑坡稳定状态划分",
            "document_id": "doc1",
            "document_code": "TEST-1",
            "page": 2,
            "evidence_page_id": "doc1_p0002",
            "evidence_quote": "不稳定 F<1.0 欠稳定 1.0≤F<1.05",
            "rows": [
                {"滑坡稳定状态": "不稳定", "滑坡稳定系数F": "F<1.0"},
                {"滑坡稳定状态": "欠稳定", "滑坡稳定系数F": "1.0≤F<1.05"},
            ],
        }]

        rules = derive_table_rules(tables)

        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["condition_text"], "滑坡稳定系数F=F<1.0")
        self.assertEqual(rules[0]["output_text"], "滑坡稳定状态=不稳定")
        self.assertFalse(rules[0]["execution_enabled"])


if __name__ == "__main__":
    unittest.main()
