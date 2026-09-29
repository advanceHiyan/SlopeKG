from __future__ import annotations

import json
import copy
import re
import unittest
from uuid import uuid4

from slopekg.config import PATHS
from slopekg.exporter import compact_graph_for_offline, export_risk_standalone_html, export_standalone_html


class StandaloneExportTests(unittest.TestCase):
    def test_visual_provenance_is_preserved_without_mutating_live_graph(self):
        graph = {"meta": {}, "processing_audit": {"candidates": [{"quote": "trace"}]}, "nodes": [{"id": "asset", "type": "VisualAsset", "props": {
            "asset_catalog_member": True, "source_document_id": "doc", "source_page": 83, "sha256": "digest"}}],
            "edges": [{"id": "edge", "source": "doc", "target": "asset", "relation": "HAS_VISUAL_ASSET",
                       "props": {"source_document_id": "doc", "source_page": 83, "evidence": "ev"}}]}
        original = copy.deepcopy(graph)
        compact = compact_graph_for_offline(graph)
        self.assertEqual(graph, original)
        self.assertNotIn('processing_audit', compact)
        self.assertEqual(compact['meta']['offline_compaction']['omitted_processing_candidates'], 1)
        self.assertEqual(compact["nodes"], graph["nodes"])
        self.assertEqual(compact["edges"][0]["props"], {"evidence": "ev"})
        self.assertEqual(compact["edges"][0]["source"], "doc")
        self.assertEqual(compact["edges"][0]["target"], "asset")

    def test_export_is_one_self_contained_html_with_embedded_graph(self) -> None:
        output = PATHS.output_dir / "share" / f"_export_test_{uuid4().hex}.html"
        try:
            result = export_standalone_html(output, graph_mode="active")
            html = output.read_text(encoding="utf-8")
            self.assertGreater(result["nodes"], 0)
            self.assertLess(result["bytes"], 2 * 1024 * 1024)
            self.assertNotIn("__SLOPEKG_EMBEDDED_DATA__", html)
            self.assertNotRegex(html, r"<script[^>]+src=")
            self.assertNotRegex(html, r"<link[^>]+stylesheet")
            match = re.search(r'<script id="slopekg-data" type="application/json">([\s\S]*?)</script>', html)
            self.assertIsNotNone(match)
            payload = json.loads(match.group(1))  # type: ignore[union-attr]
            self.assertEqual(len(payload["graph"]["nodes"]), result["nodes"])
            self.assertEqual(payload["export"]["format"], "slopekg_single_file_offline_html")
        finally:
            output.unlink(missing_ok=True)

    def test_risk_export_is_interactive_single_html_with_embedded_screening(self) -> None:
        output = PATHS.output_dir / "share" / f"_risk_export_test_{uuid4().hex}.html"
        try:
            result = export_risk_standalone_html(output)
            html = output.read_text(encoding="utf-8")
            self.assertGreater(result["slopes"], 0)
            self.assertLess(result["bytes"], 2 * 1024 * 1024)
            self.assertNotIn("__SLOPEKG_RISK_DATA__", html)
            self.assertNotRegex(html, r"<script[^>]+src=")
            self.assertNotRegex(html, r"<link[^>]+stylesheet")
            match = re.search(r'<script id="slopekg-risk-data" type="application/json">([\s\S]*?)</script>', html)
            self.assertIsNotNone(match)
            payload = json.loads(match.group(1))  # type: ignore[union-attr]
            self.assertEqual(len(payload["risk"]["assessments"]), result["slopes"])
            self.assertEqual(payload["export"]["format"], "slopekg_risk_single_file_offline_html")
            self.assertIn("P1", payload["risk"]["summary"]["priority_counts"])
        finally:
            output.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
