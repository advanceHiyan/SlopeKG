"""Source-first section discovery, independent of the inventory whitelist.

Headings establish boundaries even when their subject cannot be resolved.
Only a valid, single-subject heading corroborated on another page can add a
root. Source errors remain separate candidates; aliases are never guessed.
"""
from __future__ import annotations

import re
from typing import Any

from .identity import STATION_RE, normalize_station_match, stable_id

UNRESOLVED_SECTION = "__unresolved_section__"
DOMAIN = re.compile(r"边坡|危岩|崩塌|滑坡|沉降|下沉|垮塌")
NUMBERED = re.compile(r"(?:^|(?<=[\s。；]))(?P<number>\d+(?:\.\d+){1,3})(?:[ \t]+|(?=[Kk]))(?P<title>[Kk\u4e00-\u9fff][^\n]*)", re.MULTILINE)


def numbered_headings(text: str) -> list[dict[str, Any]]:
    result = []
    for match in NUMBERED.finditer(text):
        title = match.group("title").strip()
        if re.search(r"\.{5,}|…{3,}", title):
            continue  # A contents entry cannot open or close a body section.
        first = STATION_RE.match(title)
        stations = []
        if first and DOMAIN.search(title[first.end():first.end() + 40]):
            stations.append(normalize_station_match(first))
            end = first.end()
            for other in STATION_RE.finditer(title, end):
                if not re.fullmatch(r"\s*[、及和,，]\s*", title[end:other.start()]):
                    break
                stations.append(normalize_station_match(other))
                end = other.end()
        elif first or len(title) > 80 or re.search(r"[。；;]", title):
            continue
        result.append(dict(start=match.start(), end=match.end(), number=match.group("number"),
                           depth=match.group("number").count(".") + 1,
                           title=title, stations=list(dict.fromkeys(stations))))
    return result


def valid_station(station: str) -> bool:
    match = STATION_RE.fullmatch(station)
    if not match or any(int(match.group(k)) >= 1000 for k in ("start_m", "end_m")):
        return False
    start = int(match.group("start_km")) * 1000 + int(match.group("start_m"))
    end = int(match.group("end_km") or match.group("start_km")) * 1000 + int(match.group("end_m"))
    return end > start


def station_bounds(station: str) -> tuple[int, int]:
    match = STATION_RE.fullmatch(station)
    assert match is not None
    return (int(match.group("start_km")) * 1000 + int(match.group("start_m")),
            int(match.group("end_km") or match.group("start_km")) * 1000 + int(match.group("end_m")))


def body_parent(heading: dict, registered: set[str]) -> str | None:
    """An explicitly named rock body inside one slope is a child, not a root.

    Containment alone cannot establish an alias or merge two slopes. Require
    the source's 危岩体 object label and exactly one enclosing registered root.
    """
    stations = heading["stations"]
    if len(stations) != 1 or stations[0] in registered or not valid_station(stations[0]):
        return None
    match = STATION_RE.match(heading["title"])
    if not match or not re.match(r"\s*(?:段)?危岩体", heading["title"][match.end():]):
        return None
    start, end = station_bounds(stations[0])
    parents = [s for s in registered if valid_station(s)
               and station_bounds(s)[0] <= start < end <= station_bounds(s)[1]]
    return parents[0] if len(parents) == 1 else None


def build_section_catalog(document: dict[str, Any], by_page: dict, registry: list[dict],
                          page_widths: dict[int, float] | None = None) -> list[dict]:
    blocks = [block for (doc_id, _), rows in by_page.items() if doc_id == document["id"] for block in rows]
    registered = {row["station"] for row in registry if row.get("eligible_as_slope_root", True)}
    def position(item):
        box = item.get("bbox")
        width = (page_widths or {}).get(int(item["page"]), 1200)
        if isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            return int(item["page"]), int((box[0] + box[2]) / 2 >= width / 2), box[1]
        return int(item["page"]), 0, item.get("reading_order", 0)
    catalog, seen = [], set()
    for block in blocks:
        for heading in numbered_headings(str(block.get("text", ""))):
            # Native/OCR duplicates are one source assertion, not corroboration.
            key = (block["page"], heading["number"], tuple(heading["stations"]), re.sub(r"\s+", "", heading["title"]))
            if key in seen:
                continue
            seen.add(key)
            catalog.append({**heading, "id": stable_id("section", f'{document["id"]}:{key}'),
                            "document_id": document["id"], "page": int(block["page"]),
                            "bbox": block.get("bbox"), "evidence_block_ids": [block["id"]],
                            "source_text": block["text"], "method": "independent_numbered_heading"})
    for heading in catalog:
        stations = heading["stations"]
        parent = body_parent(heading, registered)
        support = []
        if len(stations) == 1 and valid_station(stations[0]):
            # A later page is required so repeated OCR of the heading cannot
            # create a root. Do not corroborate with running headers or TOCs.
            stops = [position(h) for h in catalog if position(h) > position(heading)
                     and h["depth"] <= heading["depth"] and not body_parent(h, registered)]
            stop = min(stops) if stops else (float("inf"), 0, 0)
            for block in blocks:
                text = str(block.get("text", ""))
                if not heading["page"] < int(block["page"]) or position(block) >= stop or not DOMAIN.search(text):
                    continue
                if numbered_headings(text) or re.search(r"\.{5,}|…{3,}", text):
                    continue
                if re.match(r"\s*[GSXY]\d+", text) or "勘察报告" in text:
                    continue
                if stations[0] in {normalize_station_match(m) for m in STATION_RE.finditer(text)}:
                    support.append(dict(block_id=block["id"], page=block["page"], text=text, bbox=block.get("bbox")))
        status = ("structural_boundary" if not stations else "scoped_body_subject" if parent else "shared_subjects" if len(stations) > 1
                  else "invalid_station" if not valid_station(stations[0])
                  else "registered_subject" if stations[0] in registered
                  else "corroborated_heading" if support else "unconfirmed_heading")
        heading.update(resolution_status=status, supporting_evidence=support, parent_station=parent)
    return catalog
