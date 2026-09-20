from __future__ import annotations

import unittest

from slopekg.extractors import extract_slope_registry, mark_station_resolution


def block(doc_id: str, page: int, block_id: str, text: str, x0: float = 10.0) -> dict:
    return {
        "id": block_id,
        "document_id": doc_id,
        "page": page,
        "text": text,
        "bbox": [x0, 10.0, x0 + 400.0, 80.0],
        "reading_order": 1,
    }


class GeneralizedSlopeDiscoveryTests(unittest.TestCase):
    def test_non_increasing_source_station_is_retained_but_not_a_slope_root(self):
        candidate = {"station": "K1625+980-K1625+030"}
        mark_station_resolution(candidate)
        self.assertFalse(candidate["eligible_as_slope_root"])
        self.assertEqual(candidate["entity_resolution_status"], "non_increasing_station_range")
        self.assertEqual(candidate["quality_issues"], ["non_increasing_station_range"])

    def test_generic_inventory_does_not_require_exact_title_or_side(self):
        document = {"id": "doc1", "file_name": "G999-1.000-2.000-勘察报告.pdf", "title": "沿线灾害报告"}
        title = block("doc1", 2, "b1", "表1-1 沿线灾害一览表")
        table = {
            "id": "t1",
            "document_id": "doc1",
            "page": 2,
            "bbox": [0, 0, 500, 500],
            "rows": [
                ["序号", "起讫点桩号", "影响长度(m)", "灾害类型及规模"],
                ["1", "K10+100~K10+180", "80", "崩塌、规模中等"],
                ["2", "K11+200~K11+260", "60", "滑坡"],
            ],
        }
        rows = extract_slope_registry(document, {("doc1", 2): [title]}, [table])
        self.assertEqual([row["station"] for row in rows], ["K10+100-K10+180", "K11+200-K11+260"])
        self.assertTrue(all(row.get("side") is None for row in rows))
        self.assertTrue(all(row["route_code"] == "G999" for row in rows))

    def test_single_site_report_creates_root_without_inventory_table(self):
        document = {"id": "doc2", "file_name": "G888-100.000-101.000-勘察报告.pdf", "title": "边坡勘察"}
        blocks = [
            block("doc2", 1, "b1", "G888（K100+120-K100+210）某沟段"),
            block("doc2", 1, "b2", "边坡灾害防治工程地质勘察说明书。该处为山体滑坡，治理段长度约120m。", 20.0),
        ]
        rows = extract_slope_registry(document, {("doc2", 1): blocks}, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["station"], "K100+120-K100+210")
        self.assertEqual(rows[0]["slope_length_m"], 120.0)
        self.assertEqual(rows[0]["route_code"], "G888")


if __name__ == "__main__":
    unittest.main()
