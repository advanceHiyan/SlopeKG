from __future__ import annotations

from dataclasses import replace
import copy
import json
from pathlib import Path
import uuid
import unittest

from PIL import Image

from slopekg.config import PATHS
from slopekg.graph_builder import attach_visual_assets
from slopekg.multimodal import build_multimodal_outputs, describe_asset, validate_asset
from slopekg.pipeline import DemoPipeline


class AssetIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Explicit mkdir preserves inherited Windows sandbox ACLs (mkdtemp's
        # private mode can prevent even the creating process reopening it).
        self.root = Path(__file__).resolve().parents[1] / ".tmp/tests" / f"assets-{uuid.uuid4().hex}"
        self.assets = self.root / "assets" / "ocr_pages"
        self.assets.mkdir(parents=True)
        self.image = self.assets / "survey_1234567890_p0002-full_page-fitz.png"
        Image.new("RGB", (20, 20), "white").save(self.image)
        self.documents = [{"id": "doc", "file_name": "survey.pdf", "source_sha256": "1234567890" + "a" * 54, "pages": 3}]

    def build(self):
        return build_multimodal_outputs([self.assets], self.root, self.root / "catalog", documents=self.documents, slope_ids={"slope"})

    def sidecar(self, data):
        self.image.with_suffix(".png.asset.json").write_text(json.dumps(data), encoding="utf-8")

    def graph(self):
        return {"nodes": [{"id": "doc", "type": "Document", "props": {}}, {"id": "slope", "type": "Slope", "props": {}}],
                "edges": [], "evidence": [], "meta": {}}

    def test_file_tampering_and_invalid_references_are_reported(self):
        record = describe_asset(self.image, self.root)
        record.update(source_page=-1, source_document_id="absent", slope_id="absent")
        self.image.write_bytes(b"broken")
        issues = validate_asset(record, project_root=self.root, documents={"doc": self.documents[0]}, slope_ids={"slope"})
        self.assertTrue({"sha256", "image", "source_page", "source_document_id", "slope_id"} <= {i["field"] for i in issues})
        self.image.unlink()
        self.assertIn("href", {i["field"] for i in validate_asset(record, project_root=self.root)})

    def test_bad_sidecar_does_not_abort_other_assets(self):
        self.image.with_suffix(".png.asset.json").write_text("{bad", encoding="utf-8")
        Image.new("RGB", (20, 20)).save(self.assets / "other.png")
        result = self.build()
        self.assertEqual(result["catalog"]["count"], 1)
        self.assertEqual(result["quality"]["status"], "has_blocking_errors")

    def test_zero_out_of_range_and_boolean_pages_are_invalid(self):
        for page in [0, -1, 4, True, "2"]:
            with self.subTest(page=page):
                self.sidecar({"source_page": page})
                result = self.build()
                self.assertEqual(result["catalog"]["assets"][0]["validation_status"], "invalid")

    def test_malformed_reference_lists_are_reported(self):
        for value in ["doc", [{"id": "doc"}]]:
            self.sidecar({"derived_from": value})
            self.assertEqual(self.build()["quality"]["status"], "has_blocking_errors")

    def test_stale_source_hash_is_not_guessed_from_filename(self):
        self.documents[0]["source_sha256"] = "b" * 64
        asset = self.build()["catalog"]["assets"][0]
        self.assertIsNone(asset["source_document_id"])
        graph = self.graph()
        attach_visual_assets(graph, {"assets": [asset], "count": 1})
        self.assertEqual(len(graph["nodes"]), 2)

    def test_explicit_stale_source_and_wrong_checksum_are_invalid(self):
        self.sidecar({"source_document_id": "doc", "sha256": "b" * 64})
        self.documents[0]["source_sha256"] = "c" * 64
        issues = self.build()["quality"]["issues"]
        self.assertTrue({"source_document_id", "sha256"} <= {i["field"] for i in issues})

    def test_duplicate_hash_is_ambiguous(self):
        self.documents.append({**self.documents[0], "id": "doc2"})
        self.assertIsNone(self.build()["catalog"]["assets"][0]["source_document_id"])

    def test_graph_is_idempotent_and_requires_slope_review(self):
        self.sidecar({"slope_id": "slope"})
        catalog = self.build()["catalog"]
        graph = self.graph()
        attach_visual_assets(graph, catalog)
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["edges"]), 2)
        original = copy.deepcopy(graph)
        attach_visual_assets(graph, catalog)
        self.assertEqual(graph, original)
        self.sidecar({"slope_id": "slope", "slope_review_status": "approved"})
        attach_visual_assets(graph, self.build()["catalog"])
        self.assertEqual(len(graph["edges"]), 3)
        self.image.unlink()
        attach_visual_assets(graph, self.build()["catalog"])
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(graph["edges"], [])

    def test_invalid_and_duplicate_assets_never_enter_graph(self):
        catalog = self.build()["catalog"]
        catalog["assets"][0]["validation_status"] = "invalid"
        graph = self.graph()
        attach_visual_assets(graph, catalog)
        self.assertEqual(len(graph["nodes"]), 2)
        catalog["assets"][0]["validation_status"] = "valid"
        catalog["assets"].append(dict(catalog["assets"][0]))
        attach_visual_assets(graph, catalog)
        self.assertEqual(len(graph["nodes"]), 2)

    def test_pipeline_refreshes_catalog_and_graph_from_current_files(self):
        out = self.root / "output"
        paths = replace(PATHS, root=self.root, output_dir=out, assets_dir=self.assets.parent,
                        graph_dir=out / "graph", web_data_dir=out / "web/data",
                        extracted_dir=out / "extracted", rules_dir=out / "rules", multimodal_dir=out / "multimodal")
        pipeline = DemoPipeline(paths)
        parsed = {"documents": self.documents, "pages": [], "text_blocks": [], "tables": [], "ocr_results": []}
        ocr_results = [{"image_path": self.image.relative_to(self.root).as_posix()}]
        graph, report = pipeline.build_graph(parsed, [], ocr_results, {"slopes": []}, [], {}, "basic", True)
        self.assertEqual(graph["meta"]["stats"]["node_type_counts"]["VisualAsset"], 1)
        self.assertEqual(report["multimodal"]["assets"], 1)
        self.assertTrue(paths.multimodal_catalog_json.exists())
        self.image.unlink()
        graph, report = pipeline.build_graph(parsed, [], ocr_results, {"slopes": []}, [], {}, "basic", True)
        self.assertNotIn("VisualAsset", graph["meta"]["stats"]["node_type_counts"])
        self.assertEqual(report["multimodal"]["assets"], 0)
