from __future__ import annotations

import json
import re
import unittest
from uuid import uuid4

from slopekg.config import PATHS
from slopekg.exporter import export_standalone_html


class StandaloneExportTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
