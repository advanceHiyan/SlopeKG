from __future__ import annotations

import unittest

from slopekg.slope_attribution import suggest_slope_attributions


def slope(slope_id: str, start: int, end: int, route: str = "G209") -> dict:
    return {"id": slope_id, "type": "Slope", "label": f"{route} {slope_id}",
            "props": {"route_code_cache": route, "start_station_m": start, "end_station_m": end}}


def asset(subtype: str = "pdf_full_page", page: int = 3) -> dict:
    return {"id": "asset-1", "origin": "pdf_derived", "subtype": subtype,
            "source_document_id": "doc-1", "source_page": page,
            "href": "output/demo/assets/ocr_pages/page.png", "slope_id": None}


class SlopeAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.slopes = [slope("A", 2402780, 2402826), slope("B", 2402826, 2402890),
                       slope("other-route", 2402780, 2402826, "G318")]
        self.documents = [{"id": "doc-1", "file_name": "G209 route report.pdf"}]

    def suggest(self, evidence: list[dict], *, asset_row: dict | None = None) -> tuple[dict, dict]:
        target = asset_row or asset()
        report = suggest_slope_attributions([target], documents=self.documents,
                                            slopes=self.slopes, ocr_results=evidence)
        return report, target

    def test_exact_page_range_is_traceable_and_not_approved(self) -> None:
        report, target = self.suggest([{"id": "ocr-1", "document_id": "doc-1", "page": 3,
                                        "image_path": "output\\demo\\assets\\ocr_pages\\page.png",
                                        "text": "K2402+780-K2402+825 左侧", "bbox": [1, 2, 3, 4]}])
        row = report["assets"][0]
        self.assertEqual(row["status"], "single_candidate_needs_review")
        self.assertEqual(row["candidates"][0]["slope_id"], "A")
        self.assertEqual(row["candidates"][0]["evidence"][0]["source_id"], "ocr-1")
        self.assertEqual(row["candidates"][0]["evidence"][0]["bbox"], [1, 2, 3, 4])
        self.assertIsNone(target["slope_id"])

    def test_shared_endpoint_remains_ambiguous(self) -> None:
        report, _ = self.suggest([{"id": "ocr-2", "document_id": "doc-1", "page": 3,
                                   "image_path": "output/demo/assets/ocr_pages/page.png",
                                   "text": "K2402+826"}])
        self.assertEqual(report["assets"][0]["status"], "multiple_candidates_needs_review")
        self.assertEqual({c["slope_id"] for c in report["assets"][0]["candidates"]}, {"A", "B"})

    def test_document_range_alone_does_not_create_candidate(self) -> None:
        report, _ = self.suggest([{"id": "ocr-3", "document_id": "doc-1", "page": 3,
                                   "image_path": "output/demo/assets/ocr_pages/page.png", "text": "施工图设计"}])
        self.assertEqual(report["assets"][0]["status"], "unmatched")

    def test_cover_page_full_road_range_is_not_mistaken_for_edge_slopes(self) -> None:
        self.documents = [{"id": "doc-1", "file_name": "G209-2388.976-2435.900-report.pdf"}]
        self.slopes = [slope("first", 2388976, 2389036),
                       slope("last", 2435820, 2435900)]
        report, _ = self.suggest([{"id": "cover", "document_id": "doc-1", "page": 3,
                                   "image_path": "output/demo/assets/ocr_pages/page.png",
                                   "text": "第六合同段 K2388+976～K2435+900"}])
        self.assertEqual(report["assets"][0]["status"], "unmatched")

    def test_title_block_does_not_borrow_ocr_from_another_image(self) -> None:
        report, _ = self.suggest([{"id": "ocr-4", "document_id": "doc-1", "page": 3,
                                   "image_path": "output/demo/assets/ocr_pages/other.png",
                                   "text": "K2402+780"}], asset_row=asset("drawing_title_block"))
        self.assertEqual(report["assets"][0]["status"], "unmatched")

    def test_other_page_and_other_route_do_not_match(self) -> None:
        report, _ = self.suggest([{"id": "ocr-5", "document_id": "doc-1", "page": 4,
                                   "image_path": "output/demo/assets/ocr_pages/other.png",
                                   "text": "K2402+780"}])
        self.assertEqual(report["assets"][0]["status"], "unmatched")


if __name__ == "__main__":
    unittest.main()
