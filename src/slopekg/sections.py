"""Source-first section discovery, independent of the inventory whitelist.

Headings establish boundaries even when their subject cannot be resolved.
Only a valid, single-subject heading corroborated on another page can add a
root. Source errors remain separate candidates; aliases are never guessed.
"""
from __future__ import annotations

import re
from typing import Any

from .identity import STATION_RE, normalize_station_match, stable_id, infer_route_code_from_values
from .geometry import rectangle

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


def source_position(item: dict, page_widths: dict[int, float] | None = None) -> tuple:
    box = rectangle(item.get("bbox"))
    width = (page_widths or {}).get(int(item["page"]), 1200)
    if box:
        return int(item["page"]), int((box[0] + box[2]) / 2 >= width / 2), box[1]
    return int(item["page"]), 0, item.get("reading_order", 0)


def _in_table(block: dict, tables: list[dict]) -> bool:
    box = rectangle(block.get("bbox"))
    if not box:
        return False
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return any(t.get("document_id") == block["document_id"] and t.get("page") == block["page"]
               and (bounds := rectangle(t.get("bbox")))
               and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3] for t in tables)


def _adjacent_title(number: dict, title: dict, widths: dict[int, float]) -> bool:
    a, b = rectangle(number.get("bbox")), rectangle(title.get("bbox"))
    if not a or not b or source_position(number, widths)[:2] != source_position(title, widths)[:2]:
        return False
    height = max(a[3] - a[1], b[3] - b[1])
    same_line = min(a[3], b[3]) - max(a[1], b[1]) >= min(a[3]-a[1], b[3]-b[1]) / 2
    return ((same_line and 0 <= b[0] - a[2] <= 3 * height)
            or (0 <= b[1] - a[3] <= 2 * height and abs(b[0] - a[0]) <= 2 * height))


def _source_headings(blocks: list[dict], widths: dict[int, float], tables: list[dict]) -> list[dict]:
    """Discover headings in source blocks; never join arbitrary table cells.

    Only an explicit station subject can complete a detached section number.
    Both constituent spans remain addressable in the original source text.
    """
    eligible = [b for b in blocks if b.get("bbox_coordinate_space") != "unmapped_image_pixels"
                and not _in_table(b, tables)]
    output = []
    for block in eligible:
        text = str(block.get("text", ""))
        for heading in numbered_headings(text):
            output.append({**heading, "block": block, "source_spans": [dict(
                block_id=block["id"], start=heading["start"], end=heading["end"])], "source_text": text})
        for match in re.finditer(r"(?m)^[ \t]*(\d+(?:\.\d+){1,3})[ \t]*\r?\n[ \t]*([Kk][^\n]+)", text):
            joined = match.group(1) + " " + match.group(2)
            headings = numbered_headings(joined)
            if headings and headings[0]["stations"]:
                output.append({**headings[0], "block": block, "source_spans": [dict(
                    block_id=block["id"], start=match.start(), end=match.end())], "source_text": text})
    ordered = sorted(eligible, key=lambda b: (*source_position(b, widths), (rectangle(b.get("bbox")) or [0])[0]))
    for index, block in enumerate(ordered):
        number = str(block.get("text", "")).strip()
        if not re.fullmatch(r"\d+(?:\.\d+){1,3}", number):
            continue
        for next_block in ordered[index + 1:index + 4]:
            title = str(next_block.get("text", "")).strip()
            if title == number:
                continue
            if not STATION_RE.match(title) or not _adjacent_title(block, next_block, widths):
                break  # Do not jump over unrelated prose/cells to find a title.
            headings = numbered_headings(number + " " + title)
            if headings and headings[0]["stations"]:
                output.append({**headings[0], "block": block, "source_spans": [
                    dict(block_id=b["id"], start=0, end=len(str(b.get("text", "")))) for b in [block, next_block]],
                    "source_text": number + "\n" + title})
                break
    return output


def build_section_catalog(document: dict[str, Any], by_page: dict, registry: list[dict],
                          page_widths: dict[int, float] | None = None,
                          tables: list[dict] | None = None) -> list[dict]:
    blocks = [block for (doc_id, _), rows in by_page.items() if doc_id == document["id"] for block in rows]
    registered = {row["station"] for row in registry if row.get("eligible_as_slope_root", True)}
    position = lambda item: source_position(item, page_widths)
    catalog, seen = [], {}
    for source in _source_headings(blocks, page_widths or {}, tables or []):
        block = source.pop("block")
        key = (block["page"], source["number"], tuple(source["stations"]), re.sub(r"\s+", "", source["title"]))
        if key in seen:
            # Duplicate OCR may supply spans, but not independent support.
            previous = seen[key]
            previous["source_spans"].extend(s for s in source["source_spans"] if s not in previous["source_spans"])
            previous["evidence_block_ids"] = list(dict.fromkeys(s["block_id"] for s in previous["source_spans"]))
            continue
        heading = {**source, "id": stable_id("section", f'{document["id"]}:{key}'),
                   "document_id": document["id"], "page": int(block["page"]),
                   "bbox": block.get("bbox"), "evidence_block_ids": list(dict.fromkeys(s["block_id"] for s in source["source_spans"])),
                   "method": "independent_numbered_heading"}
        seen[key] = heading
        catalog.append(heading)
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


def document_section_catalogs(parsed: dict, registry: list[dict]) -> list[dict]:
    """One source representation for root discovery, prose and engineering tables."""
    by_page: dict = {}
    for block in parsed.get("text_blocks", []):
        by_page.setdefault((block["document_id"], int(block["page"])), []).append(block)
    output = []
    for document in parsed.get("documents", []):
        route = infer_route_code_from_values(document.get("file_name"), document.get("title"))
        subjects = [r for r in registry if r.get("document_id") == document["id"] or r.get("route_code") == route]
        widths = {int(p["page"]): float(p.get("width") or 1200) for p in parsed.get("pages", []) if p["document_id"] == document["id"]}
        output.extend(build_section_catalog(document, by_page, subjects, widths, parsed.get("tables", [])))
    return output


def heading_subjects(heading: dict, registered: set[str]) -> list[str]:
    parent = body_parent(heading, registered)
    return [parent] if parent else heading["stations"]


def root_heading_depth(catalog: list[dict], registered: set[str]) -> int:
    return min([h["depth"] for h in catalog if h["stations"] and not body_parent(h, registered)] or [0])


def panel_heading_offsets(catalog: list[dict], blocks: list[dict]) -> list[dict]:
    """Translate source spans into a newline-preserving panel, without reparsing."""
    starts, cursor = {}, 0
    for block in blocks:
        starts[block["id"]] = cursor
        cursor += len(str(block.get("text", ""))) + 1
    output = []
    for heading in catalog:
        spans = [s for s in heading["source_spans"] if s["block_id"] in starts]
        if not spans:
            continue
        output.append({**heading, "start": min(starts[s["block_id"]]+s["start"] for s in spans),
                       "end": max(starts[s["block_id"]]+s["end"] for s in spans)})
    return output
