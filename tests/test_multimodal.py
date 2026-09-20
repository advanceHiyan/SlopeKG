from __future__ import annotations

import json
from pathlib import Path
import unittest

from slopekg.multimodal import build_multimodal_outputs, discover_assets, multimodal_contract, validate_asset


TEST_TEMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def case_root(name: str) -> Path:
    root = TEST_TEMP_ROOT / name
    root.mkdir(parents=True, exist_ok=True)
    return root


class MultimodalAssetTests(unittest.TestCase):
    def test_contract_separates_raw_assets_processing_and_quality(self) -> None:
        contract = multimodal_contract()
        self.assertIn("RasterAsset", contract["entity_types"])
        self.assertIn("ProcessingRun", contract["entity_types"])
        self.assertIn("QualityReport", contract["entity_types"])
        self.assertIn("DERIVED_FROM", contract["relations"])

    def test_pdf_visual_asset_keeps_page_provenance(self) -> None:
        root = case_root("pdf_provenance")
        image = root / "output" / "demo" / "assets" / "ocr_pages" / "survey_abcd1234_p0016-full_page-fitz.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"not-a-real-png-but-valid-for-file-inventory")

        assets = discover_assets([image.parent], root)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["asset_type"], "VisualAsset")
        self.assertEqual(assets[0]["subtype"], "pdf_full_page")
        self.assertEqual(assets[0]["source_page"], 16)
        self.assertEqual(assets[0]["origin"], "pdf_derived")

    def test_spatial_asset_reports_missing_metadata_without_guessing(self) -> None:
        root = case_root("missing_spatial_metadata")
        point_cloud = root / "sample.laz"
        point_cloud.write_bytes(b"sample")
        asset = discover_assets([point_cloud], root)[0]
        issues = validate_asset(asset)

        self.assertEqual(asset["asset_type"], "PointCloudAsset")
        self.assertIsNone(asset["crs"])
        self.assertEqual({issue["field"] for issue in issues}, {"crs", "spatial_footprint", "acquisition_time"})

    def test_sidecar_metadata_makes_asset_traceable(self) -> None:
        root = case_root("sidecar_metadata")
        point_cloud = root / "S01.laz"
        point_cloud.write_bytes(b"sample")
        point_cloud.with_suffix(".laz.asset.json").write_text(
            json.dumps({
                "slope_id": "S01",
                "crs": "EPSG:4547",
                "bbox": [100, 200, 300, 400],
                "acquisition_time": "2026-09-01T10:00:00+08:00",
                "acquisition_event_id": "flight-01",
            }),
            encoding="utf-8",
        )

        asset = discover_assets([root], root)[0]

        self.assertEqual(asset["slope_id"], "S01")
        self.assertEqual(asset["crs"], "EPSG:4547")
        self.assertEqual(validate_asset(asset), [])

    def test_builder_writes_catalog_contract_and_quality_report(self) -> None:
        root = case_root("builder_outputs")
        assets_dir = root / "assets"
        assets_dir.mkdir(exist_ok=True)
        (assets_dir / "inspection.jpg").write_bytes(b"photo")
        output_dir = root / "generated"

        result = build_multimodal_outputs([assets_dir], root, output_dir)

        self.assertEqual(result["catalog"]["count"], 1)
        self.assertTrue((output_dir / "asset_catalog.json").exists())
        self.assertTrue((output_dir / "data_contract.json").exists())
        self.assertTrue((output_dir / "quality_report.json").exists())

    def test_builder_links_pdf_asset_to_document_by_source_hash(self) -> None:
        root = case_root("document_link")
        assets_dir = root / "output" / "demo" / "assets" / "ocr_pages"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / "survey_1234567890_p0003-full_page-fitz.png").write_bytes(b"page")
        documents = [{"id": "doc-survey", "title": "Survey", "source_sha256": "1234567890abcdef"}]

        result = build_multimodal_outputs([assets_dir], root, root / "generated", documents=documents)

        asset = result["catalog"]["assets"][0]
        self.assertEqual(asset["source_document_id"], "doc-survey")
        self.assertEqual(asset["derived_from"], ["doc-survey"])
        self.assertEqual(result["quality"]["summary"]["assets_linked_to_document"], 1)

    def test_builder_links_legacy_pdf_asset_by_exact_unique_file_stem(self) -> None:
        root = case_root("legacy_document_link")
        assets_dir = root / "output" / "demo" / "assets" / "ocr_pages"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / "边坡勘察报告_p0003-full_page-fitz.png").write_bytes(b"page")
        documents = [{"id": "doc-survey", "title": "Survey", "file_name": "边坡勘察报告.pdf"}]

        result = build_multimodal_outputs([assets_dir], root, root / "generated", documents=documents)

        asset = result["catalog"]["assets"][0]
        self.assertEqual(asset["source_document_id"], "doc-survey")
        self.assertEqual(result["quality"]["summary"]["assets_linked_to_document"], 1)

    def test_duplicate_sidecar_ids_are_blocking_catalog_errors(self) -> None:
        root = case_root("duplicate_asset_ids")
        assets_dir = root / "assets"
        assets_dir.mkdir(exist_ok=True)
        for name in ("a.jpg", "b.jpg"):
            asset = assets_dir / name
            asset.write_bytes(name.encode("utf-8"))
            asset.with_suffix(f"{asset.suffix}.asset.json").write_text(
                json.dumps({"id": "asset-duplicate"}),
                encoding="utf-8",
            )

        result = build_multimodal_outputs([assets_dir], root, root / "generated")

        duplicate_issues = [
            issue for issue in result["quality"]["issues"]
            if issue["field"] == "id" and issue["asset_id"] == "asset-duplicate"
        ]
        self.assertEqual(len(duplicate_issues), 1)
        self.assertEqual(duplicate_issues[0]["severity"], "error")
        self.assertEqual(result["quality"]["status"], "has_blocking_errors")


if __name__ == "__main__":
    unittest.main()
