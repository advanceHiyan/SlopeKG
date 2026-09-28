"""Suggest slope links for PDF-derived page evidence without approving them."""

from __future__ import annotations

from collections import defaultdict
import re
import unicodedata
from typing import Any, Iterable


STATION_PATTERN = re.compile(r"(?<![A-Z0-9])K\s*(\d{1,4})\s*\+\s*(\d{1,3})(?!\d)", re.IGNORECASE)
ROUTE_PATTERN = re.compile(r"(?<![A-Z0-9])G\s*\d{3,4}(?!\d)", re.IGNORECASE)
DOCUMENT_RANGE_PATTERN = re.compile(r"G\d{3,4}-(\d{1,4})\.(\d{1,3})-(\d{1,4})\.(\d{1,3})", re.IGNORECASE)


def _normalise_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).upper()


def _href(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _stations(value: Any) -> set[int]:
    text = _normalise_text(value)
    return {int(km) * 1000 + int(metres) for km, metres in STATION_PATTERN.findall(text)}


def _routes(value: Any) -> set[str]:
    return {route.replace(" ", "") for route in ROUTE_PATTERN.findall(_normalise_text(value))}


def _document_bounds(value: Any) -> set[int]:
    match = DOCUMENT_RANGE_PATTERN.search(_normalise_text(value))
    if not match:
        return set()
    start_km, start_m, end_km, end_m = map(int, match.groups())
    return {start_km * 1000 + start_m, end_km * 1000 + end_m}


def _source_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row.get("id"),
        "text": str(row.get("text") or "")[:240],
        "bbox": row.get("bbox"),
    }


def suggest_slope_attributions(
    assets: list[dict[str, Any]],
    *,
    documents: Iterable[dict[str, Any]],
    slopes: Iterable[dict[str, Any]],
    text_blocks: Iterable[dict[str, Any]] = (),
    ocr_results: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Make page-level candidates from explicit stations; never set ``slope_id``.

    A source document may cover several slopes. Its name and route only limit
    the search; only station mentions on the asset's own page create matches.
    """
    documents_by_id = {str(row.get("id")): row for row in documents}
    slopes_by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slope in slopes:
        if slope.get("type") != "Slope":
            continue
        props = slope.get("props") or {}
        route = str(props.get("route_code_cache") or "").upper()
        start, end = props.get("start_station_m"), props.get("end_station_m")
        if route and type(start) is int and type(end) is int and start <= end:
            slopes_by_route[route].append({
                "id": slope.get("id"), "label": slope.get("label"),
                "start": start, "end": end,
            })

    blocks_by_page: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    ocr_by_page: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    ocr_by_image: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in text_blocks:
        if type(row.get("page")) is int:
            blocks_by_page[(str(row.get("document_id")), row["page"])].append(row)
    for row in ocr_results:
        if type(row.get("page")) is int:
            ocr_by_page[(str(row.get("document_id")), row["page"])].append(row)
        if row.get("image_path"):
            ocr_by_image[_href(row["image_path"])].append(row)

    rows: list[dict[str, Any]] = []
    for asset in assets:
        if asset.get("origin") != "pdf_derived":
            continue
        document_id = str(asset.get("source_document_id") or "")
        page = asset.get("source_page")
        record = {
            "asset_id": asset.get("id"), "source_document_id": document_id or None,
            "source_page": page, "status": "unmatched", "candidates": [],
        }
        if not document_id or type(page) is not int:
            record["reason"] = "缺少可靠的来源文档或页码"
            rows.append(record)
            continue

        key = (document_id, page)
        matching_ocr = ocr_by_image.get(_href(asset.get("href")), [])
        if asset.get("subtype") == "drawing_title_block":
            evidence_rows = matching_ocr
        else:
            evidence_rows = matching_ocr or ocr_by_page.get(key, []) or blocks_by_page.get(key, [])
        if not evidence_rows:
            record["reason"] = "该图片所在页没有可核对的文字识别结果"
            rows.append(record)
            continue
        document = documents_by_id.get(document_id, {})
        station_values = set().union(*(_stations(row.get("text")) for row in evidence_rows))
        document_bounds = _document_bounds(document.get("file_name") or document.get("title"))
        # Cover/title pages often repeat the full road-section range. Those
        # endpoints are document scope, not evidence for the first/last slope.
        if document_bounds and document_bounds <= station_values:
            station_values -= document_bounds
        if not station_values:
            record["reason"] = "页面文字未识别出桩号"
            rows.append(record)
            continue

        routes = _routes(document.get("file_name") or document.get("title"))
        if not routes:
            routes = set().union(*(_routes(row.get("text")) for row in evidence_rows))
        pool = [slope for route, group in slopes_by_route.items() for slope in group if not routes or route in routes]
        for slope in pool:
            matched = sorted(value for value in station_values if slope["start"] <= value <= slope["end"])
            if not matched:
                continue
            supporting = [row for row in evidence_rows if _stations(row.get("text")) & set(matched)]
            exact_range = slope["start"] in matched and slope["end"] in matched
            record["candidates"].append({
                "slope_id": slope["id"], "slope_label": slope["label"],
                "basis": "both_bounds_on_page" if exact_range else "station_within_slope_range",
                "matched_station_m": matched,
                "evidence": [_source_row(row) for row in supporting[:4]],
            })
        record["candidates"].sort(key=lambda item: (item["basis"] != "both_bounds_on_page", str(item["slope_label"])))
        if len(record["candidates"]) == 1:
            record["status"] = "single_candidate_needs_review"
        elif record["candidates"]:
            record["status"] = "multiple_candidates_needs_review"
        else:
            record["reason"] = "页面桩号与当前边坡范围不匹配"
        rows.append(record)

    counts = {status: sum(row["status"] == status for row in rows) for status in (
        "single_candidate_needs_review", "multiple_candidates_needs_review", "unmatched",
    )}
    return {
        "scope": "仅依据PDF派生图片对应页面的桩号生成归属候选；候选不等于人工审核通过的边坡关系",
        "summary": {"pdf_assets_examined": len(rows), **counts},
        "assets": rows,
    }
