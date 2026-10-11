"""Opt-in checks against the locally prepared original-page corpus."""
from __future__ import annotations
import unittest
from slopekg.config import PATHS
from slopekg.storage import read_json, read_jsonl
from slopekg.extractors import extract_domain_candidates
from slopekg.graph_builder import build_automatic_graph
from slopekg.schema import build_completeness
from scripts.evaluate_engineering_extraction import evaluate
from scripts.evaluate_page_extraction import evaluate as evaluate_pages

class ExtractionCorpusTests(unittest.TestCase):
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
        self.assertGreaterEqual(stats.get("registry_rows", 0), 62)
        self.assertEqual(stats.get("registry_source_rows"), 63)
        self.assertEqual(stats.get("registry_records_needing_review"), 1)
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
        self.assertGreaterEqual(counts.get("G351", 0), 26)
        self.assertEqual(
            extraction["registry_issues"][0]["quality_issues"],
            ["non_increasing_station_range"],
        )


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


class GraphCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        parsed = {
            "documents": read_jsonl(PATHS.parsed_dir / "documents.jsonl"),
            "pages": read_jsonl(PATHS.parsed_dir / "pages.jsonl"),
            "text_blocks": read_jsonl(PATHS.parsed_dir / "text_blocks.jsonl"),
            "tables": read_jsonl(PATHS.parsed_dir / "tables.jsonl"),
            "ocr_results": read_jsonl(PATHS.parsed_dir / "ocr_results.jsonl"),
        }
        extracted = extract_domain_candidates(parsed)
        cls.graph = build_automatic_graph(parsed, extracted, [])

    def test_graph_is_slope_centered(self) -> None:
        counts = self.graph["meta"]["stats"]["node_type_counts"]
        self.assertGreaterEqual(counts.get("Slope", 0), 62)
        self.assertGreaterEqual(counts.get("RouteSegment", 0), 5)
        self.assertNotIn("HazardPoint", counts)
        self.assertFalse(self.graph["meta"]["automatic_extraction"]["manual_seed_used"])
        self.assertEqual(self.graph["meta"]["generation_basis"], "parsed_documents_only_no_manual_seed")

    def test_slope_relations_are_explicit(self) -> None:
        relations = {edge["relation"] for edge in self.graph["edges"]}
        self.assertIn("HAS_SLOPE", relations)
        self.assertIn("CONTAINS_HAZARD_BODY", relations)
        self.assertIn("HAS_PROTECTION_DESIGN", relations)
        self.assertNotIn("HAS_ATTRIBUTE", relations)

    def test_completeness_never_treats_missing_as_ready(self) -> None:
        report = build_completeness(self.graph)
        self.assertEqual(report["summary"]["slopes"], self.graph["meta"]["stats"]["node_type_counts"]["Slope"])
        self.assertEqual(report["summary"]["ready"], 0)
        self.assertTrue(all(row["blocking_count"] > 0 for row in report["reports"]))


class PageCorpusTests(unittest.TestCase):
    def test_visually_reviewed_page_types_and_key_facts(self) -> None:
        cases = read_json(PATHS.root / "tests/fixtures/page_extraction_gold.json")["cases"]
        report = evaluate_pages(PATHS.parsed_dir, cases)
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
