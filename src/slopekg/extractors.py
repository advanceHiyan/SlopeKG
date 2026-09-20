from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

from .engineering import attach_engineering_records, extract_engineering_tables


STATION_RE = re.compile(
    r"K\s*(?P<start_km>\d+)\s*\+\s*(?P<start_m>\d+)\s*[-～~]\s*"
    r"(?:K\s*)?(?:(?P<end_km>\d+)\s*\+)?\s*(?P<end_m>\d+)",
    re.IGNORECASE,
)
GPS_RE = re.compile(r"GPS\s*(?P<no>\d+)\s+(?P<x>\d{5,6}(?:\.\d+)?)\s+(?P<y>\d{6,8}(?:\.\d+)?)\s+(?P<z>\d{2,4}(?:\.\d+)?)", re.IGNORECASE)
FS_PAIR_RE = re.compile(r"(?P<actual>\d+\.\d+)\s*>\s*(?P<required>\d+\.\d+)")
ROUTE_CODE_RE = re.compile(r"\b([GSXY]\d{2,4})\b", re.IGNORECASE)
HAZARD_TERMS = ["潜在不稳定边坡", "不稳定边坡", "顺层坡滑移", "路基沉降", "路基下沉", "路基垮塌", "坡面滑塌", "危岩体", "崩塌", "滑坡", "落石"]
REGISTRY_TITLE_TERMS = ["灾害点一览表", "沿线灾害一览表", "灾害一览表", "灾害分布表"]


def extract_domain_candidates(parsed: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    documents = {row["id"]: row for row in parsed.get("documents", [])}
    pages = parsed.get("pages", [])
    blocks = parsed.get("text_blocks", [])
    tables = parsed.get("tables", [])
    by_page: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        by_page[(block["document_id"], int(block["page"]))].append(block)
    for page_blocks in by_page.values():
        page_blocks.sort(key=lambda row: int(row.get("reading_order", 0)))

    survey_ids = [doc_id for doc_id, doc in documents.items() if doc.get("kind") == "勘察报告"]
    design_ids = [doc_id for doc_id, doc in documents.items() if doc.get("kind") == "施工图"]
    registry_source_rows = dedupe_by_route_station(
        [row for doc_id in survey_ids for row in extract_slope_registry(documents[doc_id], by_page, tables)]
    )
    registry_issues = [row for row in registry_source_rows if not row.get("eligible_as_slope_root", True)]
    registry = [row for row in registry_source_rows if row.get("eligible_as_slope_root", True)]
    coordinates = dedupe_by_station([row for doc_id in design_ids for row in extract_coordinates(doc_id, by_page)])
    treatments = dedupe_by_station([row for doc_id in design_ids for row in extract_treatments(doc_id, by_page, tables)])
    domain_document_ids = set([*survey_ids, *design_ids])
    geometry = extract_geometry_candidates(domain_document_ids, by_page)
    stations_by_document: dict[str, set[str]] = defaultdict(set)
    for row in registry:
        if row.get("document_id"):
            stations_by_document[str(row["document_id"])].add(row["station"])
    section_facts = extract_section_facts(
        domain_document_ids,
        by_page,
        {row["station"] for row in registry},
        stations_by_document,
    )

    route_by_document = {
        doc_id: infer_route_code_from_values(document.get("file_name"), document.get("title"))
        for doc_id, document in documents.items()
    }
    for group in [registry, coordinates, treatments, geometry, section_facts]:
        for row in group:
            row.setdefault("route_code", route_by_document.get(row.get("document_id"), "ROUTE"))

    merged = merge_slope_candidates(registry, coordinates, treatments, geometry, section_facts)
    engineering_records = extract_engineering_tables(parsed, registry)
    attach_engineering_records(merged, engineering_records)
    assertions = build_assertions(merged)
    page_type_counts: dict[str, int] = defaultdict(int)
    registry_rows_by_route: dict[str, int] = defaultdict(int)
    for row in registry:
        registry_rows_by_route[str(row.get("route_code") or "ROUTE")] += 1
    for page in pages:
        page_type_counts[page.get("page_type", "unknown")] += 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "deterministic_native_layout",
        "slopes": merged,
        "registry": registry,
        "registry_issues": registry_issues,
        "coordinates": coordinates,
        "treatments": treatments,
        "geometry": geometry,
        "section_facts": section_facts,
        "engineering_records": engineering_records,
        "assertions": assertions,
        "stats": {
            "registry_source_rows": len(registry_source_rows),
            "registry_rows": len(registry),
            "registry_records_needing_review": len(registry_issues),
            "registry_rows_by_route": dict(registry_rows_by_route),
            "coordinate_rows": len(coordinates),
            "treatment_rows": len(treatments),
            "geometry_rows": len(geometry),
            "section_fact_rows": len(section_facts),
            "engineering_tables": len(engineering_records),
            "engineering_tables_associated": sum(bool(r["station"]) for r in engineering_records),
            "engineering_tables_document_scope": sum(r.get("association_scope") == "document" for r in engineering_records),
            "engineering_tables_needing_review": sum(r.get("review_status") == "needs_review" for r in engineering_records),
            "engineering_tables_with_source_issues": sum(bool(r["quality_issues"]) for r in engineering_records),
            "material_parameter_values": sum(len(r["material_parameters"]) for r in engineering_records),
            "table_stability_results": sum(len(r["stability_results"]) for r in engineering_records),
            "assertions": len(assertions),
            "page_type_counts": dict(page_type_counts),
            "native_bbox_coverage": round(sum(1 for row in blocks if row.get("bbox")) / len(blocks), 4) if blocks else 0,
        },
    }


def extract_slope_registry(
    document: dict[str, Any] | str | None,
    by_page: dict[tuple[str, int], list[dict[str, Any]]],
    tables: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Discover slope roots without relying on one report's exact wording.

    Inventory tables are preferred because their row boundaries are more
    reliable than flattened page text.  A document-level fallback handles
    single-site reports that legitimately have no inventory table.
    """
    if not document:
        return []
    if isinstance(document, str):
        document = {"id": document, "file_name": "", "title": ""}
    document_id = document["id"]
    route_code = infer_route_code_from_values(document.get("file_name"), document.get("title"))
    output: list[dict[str, Any]] = []

    document_tables = [table for table in (tables or []) if table.get("document_id") == document_id and table.get("review_status") != "failed"]
    inventory_pages = {
        int(table["page"])
        for table in document_tables
        if registry_table_header(table) or page_has_registry_title(by_page.get((document_id, int(table["page"])), []))
    }
    for table in document_tables:
        page = int(table["page"])
        if page not in inventory_pages:
            continue
        category: str | None = None
        for raw_row in table.get("rows", []):
            cells = [str(cell or "").strip() for cell in raw_row]
            flat = " ".join(cells)
            if not STATION_RE.search(flat):
                category = hazard_label(flat) or category
                continue
            station = normalize_station_match(STATION_RE.search(flat))
            label = hazard_label(flat) or category
            if not station or not label:
                continue
            no = first_integer_cell(cells)
            side = next((value for value in ["左侧", "右侧"] if value in flat), None)
            candidate = candidate_row(
                station,
                page,
                table,
                "generic_slope_inventory_table",
                0.995 if registry_table_header(table) else 0.98,
                no=no,
                route_code=route_code,
                side=side,
                slope_length_m=registry_length_from_cells(cells, station, no),
                source_alias=next((cell.replace(" ", "") for cell in cells if STATION_RE.search(cell)), station),
                source_disaster_label=label,
                raw_row=" | ".join(cells),
            )
            mark_station_resolution(candidate)
            output.append(candidate)

    # Native blocks supplement rows that table extraction split or omitted.
    for (doc_id, page), blocks in by_page.items():
        if doc_id != document_id or page not in inventory_pages:
            continue
        for block in blocks:
            text = block["text"]
            station = normalize_station_match(STATION_RE.search(text))
            if not station:
                continue
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            no = parse_leading_int(lines)
            side = next((value for value in ["左侧", "右侧"] if value in text), None)
            length = parse_registry_length(lines, station, no)
            disaster = hazard_label(text)
            if no is None or disaster is None:
                continue
            candidate = candidate_row(
                station,
                page,
                block,
                "generic_slope_inventory_block",
                0.985,
                no=no,
                route_code=route_code,
                side=side,
                slope_length_m=length or station_span_m(station),
                source_alias=next((line.replace(" ", "") for line in lines if "K" in line and ("边坡" in line or "危岩体" in line)), station),
                source_disaster_label=disaster,
            )
            mark_station_resolution(candidate)
            output.append(candidate)
    output = dedupe_by_route_station(output)
    if output:
        return output
    single_site = discover_single_site_document(document, by_page)
    return [single_site] if single_site else []


def extract_coordinates(document_id: str | None, by_page: dict[tuple[str, int], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if not document_id:
        return []
    output: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for (doc_id, page), blocks in sorted(by_page.items(), key=lambda item: (item[0][0], item[0][1])):
        if doc_id != document_id:
            continue
        page_has_gps = any("GPS" in block["text"] and GPS_RE.search(" ".join(block["text"].split())) for block in blocks)
        if not page_has_gps:
            continue
        for block in blocks:
            flat = " ".join(block["text"].split())
            station = normalize_station_match(STATION_RE.search(flat))
            if station:
                if current and current.get("points"):
                    output.append(current)
                current = candidate_row(station, page, block, "control_point_table", 0.99, points=[])
            if current:
                for match in GPS_RE.finditer(flat):
                    current["points"].append(
                        {
                            "point_no": f"GPS{int(match.group('no')):02d}",
                            "x": float(match.group("x")),
                            "y": float(match.group("y")),
                            "z": float(match.group("z")),
                            "evidence_block_id": block["id"],
                            "bbox": block.get("bbox"),
                        }
                    )
                    current["evidence_block_ids"] = list(dict.fromkeys([*current.get("evidence_block_ids", []), block["id"]]))
        # The table continues from page 11 to 12; do not flush at page boundary.
    if current and current.get("points"):
        output.append(current)
    for row in output:
        row["control_points"] = row["points"]
        row["coordinate_role"] = "survey_control_points_not_slope_endpoints"
        row["start_coordinate"] = None
        row["end_coordinate"] = None
        row["coordinate_crs"] = "CGCS2000（国家2000坐标系，3度带具体参数待核验）"
    return dedupe_by_station(output)


def extract_treatments(
    document_id: str | None,
    by_page: dict[tuple[str, int], list[dict[str, Any]]],
    tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not document_id:
        return []
    table_rows = extract_treatments_from_tables(document_id, tables)
    if table_rows:
        return dedupe_by_station(table_rows)
    output: list[dict[str, Any]] = []
    for (doc_id, page), blocks in by_page.items():
        if doc_id != document_id:
            continue
        if not any("治理方案一览表" in block["text"] for block in blocks) and not any(FS_PAIR_RE.search(block["text"]) and STATION_RE.search(block["text"]) for block in blocks):
            continue
        table_blocks = treatment_table_blocks(blocks)
        starts = [block for block in table_blocks if re.match(r"^\s*\d{1,2}\s*\n\s*K", block["text"])]
        starts.sort(key=lambda row: bbox_value(row, 1))
        for index, start in enumerate(starts):
            y0 = bbox_value(start, 1)
            y1 = bbox_value(starts[index + 1], 1) if index + 1 < len(starts) else min(760.0, bbox_value(start, 3) + 90.0)
            row_blocks = [block for block in table_blocks if bbox_value(block, 1) >= y0 - 1 and bbox_value(block, 1) < y1]
            row_text = "\n".join(block["text"] for block in sorted(row_blocks, key=lambda row: (bbox_value(row, 1), bbox_value(row, 0))))
            station = normalize_station_match(STATION_RE.search(row_text))
            if not station:
                continue
            fs_pairs = [{"actual": float(m.group("actual")), "required": float(m.group("required"))} for m in FS_PAIR_RE.finditer(row_text)]
            measures = [name for name in PROTECTION_TERMS if name in row_text]
            confidence = 0.98 if fs_pairs and measures else 0.88
            output.append(
                candidate_row(
                    station,
                    page,
                    start,
                    "treatment_plan_table",
                    confidence,
                    measures=measures,
                    safety_factor_pairs=fs_pairs,
                    raw_row=row_text,
                    evidence_block_ids=[block["id"] for block in row_blocks],
                )
            )
    return dedupe_by_station([*table_rows, *output])


def extract_treatments_from_tables(document_id: str, tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for table in tables:
        if table.get("document_id") != document_id or table.get("review_status") == "failed":
            continue
        rows = table.get("rows", [])
        if not rows or not any("治理措施" in str(cell) for cell in rows[0]):
            continue
        current: dict[str, Any] | None = None
        for row in rows[1:]:
            cells = [str(cell or "").strip() for cell in row]
            station = normalize_station_match(STATION_RE.search(" ".join(cells)))
            if station:
                if current:
                    output.append(current)
                measure_text = cells[3] if len(cells) > 3 else ""
                fs_text = cells[4] if len(cells) > 4 else ""
                current = {
                    "station": station,
                    "page": int(table["page"]),
                    "document_id": table.get("document_id"),
                    "evidence_block_id": table["id"],
                    "evidence_block_ids": [table["id"]],
                    "bbox": table.get("bbox"),
                    "method": "pdfplumber_treatment_table",
                    "confidence": 0.995,
                    "measures": [name for name in PROTECTION_TERMS if name in measure_text.replace(" ", "")],
                    "safety_factor_pairs": fs_pairs(fs_text),
                    "raw_row": " | ".join(cells),
                }
            elif current:
                continuation = " ".join(cells)
                current["safety_factor_pairs"].extend(fs_pairs(continuation))
                if len(cells) > 3:
                    for name in PROTECTION_TERMS:
                        if name in cells[3].replace(" ", "") and name not in current["measures"]:
                            current["measures"].append(name)
                current["raw_row"] += " || " + " | ".join(cells)
        if current:
            output.append(current)
    return output


def extract_geometry_candidates(
    document_ids: set[str],
    by_page: dict[tuple[str, int], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    allowed = document_ids
    output: list[dict[str, Any]] = []
    geometry_re = re.compile(
        r"边坡长度?约?\s*(?P<length>\d+(?:\.\d+)?)\s*m.{0,260}?"
        r"(?:边坡高|高|最大高差)约?\s*(?P<h1>\d+(?:\.\d+)?)"
        r"(?:\s*[-～~]\s*(?P<h2>\d+(?:\.\d+)?))?\s*m.{0,260}?(?:开挖坡度|坡度).*?(?P<a1>\d+(?:\.\d+)?)"
        r"(?:\s*[-～~]\s*(?P<a2>\d+(?:\.\d+)?))?\s*[°º]",
        re.DOTALL,
    )
    current_station_by_doc: dict[str, str] = {}
    for (doc_id, page), blocks in sorted(by_page.items(), key=lambda item: (item[0][0], item[0][1])):
        if doc_id not in allowed:
            continue
        for panel_name, panel_blocks in split_page_panels(blocks).items():
            panel_text = " ".join(block["text"].replace("\n", " ") for block in panel_blocks)
            qualified_stations = []
            for station_match in STATION_RE.finditer(panel_text):
                context = panel_text[station_match.start() : min(len(panel_text), station_match.end() + 60)]
                if any(word in context for word in ["左侧", "右侧", "危岩体", "边坡", "崩塌", "滑坡", "沉降", "垮塌"]):
                    qualified_stations.append((station_match.start(), normalize_station_match(station_match)))
            if qualified_stations:
                current_station_by_doc[f"{doc_id}:{panel_name}"] = qualified_stations[-1][1]
            match = geometry_re.search(panel_text)
            if not match:
                continue
            before = [station for offset, station in qualified_stations if offset <= match.start()]
            station = before[-1] if before else current_station_by_doc.get(f"{doc_id}:{panel_name}")
            if not station:
                continue
            evidence_blocks = [block for block in panel_blocks if any(token in block["text"] for token in ["边坡长度", "最大高差", "坡度约", "开挖坡度"])]
            evidence_block = evidence_blocks[0] if evidence_blocks else panel_blocks[0]
            h1 = float(match.group("h1"))
            h2 = float(match.group("h2") or h1)
            a1 = float(match.group("a1"))
            a2 = float(match.group("a2") or a1)
            output.append(
                candidate_row(
                    station,
                    page,
                    evidence_block,
                    "geometry_sentence_rule",
                    0.94,
                    slope_length_m=float(match.group("length")),
                    slope_height_min_m=min(h1, h2),
                    slope_height_max_m=max(h1, h2),
                    slope_gradient_min_deg=min(a1, a2),
                    slope_gradient_max_deg=max(a1, a2),
                    raw_text=panel_text,
                    evidence_block_ids=[block["id"] for block in panel_blocks],
                )
            )
    return dedupe_by_station(output)


LITHOLOGY_TERMS = ["砂质页岩", "页岩", "灰岩", "泥岩", "砂岩", "黏土", "粘土", "碎石土", "块石土", "堆积体"]
STRATUM_TERMS = ["第四系", "志留系", "三叠系", "侏罗系", "纱帽群", "嘉陵江组"]
STRUCTURE_TERMS = ["顺向坡", "顺层坡", "逆向坡", "反倾坡", "斜交坡", "横向坡"]
FACTOR_TERMS = ["降雨", "风化", "节理裂隙", "地表水", "地下水", "道路开挖", "坡脚开挖", "人工开挖", "褶皱构造"]
SECTION_DOMAIN_WORDS = [
    "基本情况", "变形", "稳定性", "防护措施", "岩性", "结构面", "节理",
    "坡度", "地层", "植被", "地下水", "地表水", "水文地质", "垮塌",
]
STRUCTURAL_PLANE_RE = re.compile(
    r"(?P<name>坡面|岩层面|岩层|[LJ]\s*\d+|结构面\s*[LJ]?\s*\d*)"
    r"(?:名称)?(?:的)?(?:产状)?(?:为|是|[:：])?\s*"
    r"(?P<dip_direction>\d{1,3})\s*[°º]?\s*[∠/]\s*(?P<dip_angle>\d{1,2})\s*[°º]?"
)
STRUCTURAL_PLANE_TABLE_RE = re.compile(
    r"(?:^|\s)(?P<code>P|YC|[LJ]\s*\d+)\s*"
    r"(?P<name>坡面|岩层面|结构面)?\s+"
    r"(?P<dip_direction>\d{1,3})\s+(?P<dip_angle>\d{1,2})(?=\s|$)"
)


def extract_section_facts(
    document_ids: set[str],
    by_page: dict[tuple[str, int], list[dict[str, Any]]],
    allowed_stations: set[str],
    stations_by_document: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    allowed = document_ids
    grouped: dict[str, dict[str, Any]] = {}
    active_station_by_panel: dict[str, str] = {}
    for (doc_id, page), blocks in sorted(by_page.items(), key=lambda item: (item[0][0], item[0][1])):
        if doc_id not in allowed:
            continue
        for panel_name, panel_blocks in split_page_panels(blocks).items():
            panel_text = " ".join(block["text"].replace("\n", " ") for block in panel_blocks)
            if not any(word in panel_text for word in SECTION_DOMAIN_WORDS):
                continue
            panel_key = f"{doc_id}:{panel_name}"
            document_stations = (stations_by_document or {}).get(doc_id, set())
            segments, last_heading = station_section_segments(
                panel_text,
                allowed_stations,
                active_station_by_panel.get(panel_key),
                document_stations,
            )
            for station, segment_text in segments:
                values = extract_section_values(segment_text)
                station_scenarios = extract_stability_scenarios(segment_text)
                for scenario in station_scenarios:
                    scenario.pop("_evidence_start", None)
                    scenario["evidence_document_id"] = doc_id
                    scenario["evidence_page"] = page
                    scenario["evidence_block_ids"] = [block["id"] for block in panel_blocks]
                row = grouped.setdefault(
                    station,
                    candidate_row(
                        station,
                        page,
                        panel_blocks[0],
                        "section_panel_dictionary",
                        0.86,
                        lithology_terms=[],
                        stratum_terms=[],
                        slope_structure_terms=[],
                        causal_factor_terms=[],
                        structural_planes=[],
                        deformation_observations=[],
                        hydrology_observations=[],
                        stability_scenarios=[],
                        section_evidence=[],
                        section_text_samples=[],
                    ),
                )
                row["lithology_terms"] = union(row["lithology_terms"], terms_in_text(segment_text, LITHOLOGY_TERMS, longest_first=True))
                row["stratum_terms"] = union(row["stratum_terms"], terms_in_text(segment_text, STRATUM_TERMS, longest_first=True))
                row["slope_structure_terms"] = union(row["slope_structure_terms"], terms_in_text(segment_text, STRUCTURE_TERMS))
                row["causal_factor_terms"] = union(row["causal_factor_terms"], terms_in_text(segment_text, FACTOR_TERMS))
                row["structural_planes"] = union(row["structural_planes"], values.pop("structural_planes", []))
                row["deformation_observations"] = union(row["deformation_observations"], values.pop("deformation_observations", []))
                row["hydrology_observations"] = union(row["hydrology_observations"], values.pop("hydrology_observations", []))
                for key, value in values.items():
                    if value not in (None, "", [], {}) and row.get(key) in (None, "", [], {}):
                        row[key] = value
                row["stability_scenarios"] = union(row["stability_scenarios"], station_scenarios)
                row["section_evidence"].append(
                    {
                        "document_id": doc_id,
                        "page": page,
                        "panel": panel_name,
                        "block_ids": [block["id"] for block in panel_blocks],
                        "bbox": panel_bbox(panel_blocks),
                    }
                )
                if len(row["section_text_samples"]) < 20:
                    row["section_text_samples"].append(
                        {
                            "document_id": doc_id,
                            "page": page,
                            "panel": panel_name,
                            "station_context": station,
                            "text": segment_text[:8000],
                        }
                    )
                row["evidence_block_ids"] = union(row.get("evidence_block_ids", []), [block["id"] for block in panel_blocks])
            if last_heading:
                active_station_by_panel[panel_key] = last_heading
            elif len(document_stations) == 1:
                active_station_by_panel.setdefault(panel_key, next(iter(document_stations)))
    return sorted(grouped.values(), key=lambda row: station_start_m(row["station"]))


def station_section_segments(
    text: str,
    allowed_stations: set[str],
    active_station: str | None,
    document_stations: set[str],
) -> tuple[list[tuple[str, str]], str | None]:
    """Split a page panel by numbered slope headings and retain continuation text.

    A station appearing in prose is not automatically a new section. This keeps
    cross-references and typoed station ranges from stealing facts from the
    active slope while allowing the text before the next heading to remain with
    the previous slope.
    """
    occurrences: list[tuple[int, int, str]] = []
    headings: list[tuple[int, int, str]] = []
    for match in STATION_RE.finditer(text):
        station = normalize_station_match(match)
        if station not in allowed_stations:
            continue
        context = text[max(0, match.start() - 50) : min(len(text), match.end() + 90)]
        if not any(word in context for word in ["左侧", "右侧", "危岩体", "边坡", "崩塌", "滑坡", "沉降", "下沉", "垮塌"]):
            continue
        occurrence = (match.start(), match.end(), station)
        occurrences.append(occurrence)
        prefix = re.sub(r"\s+", "", text[max(0, match.start() - 18) : match.start()])
        if re.search(r"(?:^|\D)(?:图)?\d+\.\d+(?:\.\d+)?(?:-\d+)?$", prefix):
            headings.append(occurrence)

    segments: list[tuple[str, str]] = []
    if headings:
        if active_station in allowed_stations and headings[0][0] > 0:
            preamble = text[: headings[0][0]].strip()
            if preamble:
                segments.append((str(active_station), preamble))
        for index, (start, _end, station) in enumerate(headings):
            stop = headings[index + 1][0] if index + 1 < len(headings) else len(text)
            segment = text[start:stop].strip()
            if segment:
                segments.append((station, segment))
        return segments, headings[-1][2]

    if active_station in allowed_stations:
        return [(str(active_station), text.strip())], None
    unique_mentions = list(dict.fromkeys(station for _start, _end, station in occurrences))
    if len(unique_mentions) == 1:
        return [(unique_mentions[0], text.strip())], None
    if not occurrences and len(document_stations) == 1:
        return [(next(iter(document_stations)), text.strip())], None
    return [], None


def extract_section_values(text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", text)
    values: dict[str, Any] = {}

    length = first_range_match(compact, [r"边坡(?:长度|长)(?:约|为)?(?P<a>\d+(?:\.\d+)?)(?:[-～~](?P<b>\d+(?:\.\d+)?))?m"])
    height = first_range_match(
        compact,
        [r"(?:边坡(?:高度|高)|坡高|最大高差|[，,；;]高)(?:约|为)?(?P<a>\d+(?:\.\d+)?)(?:[-～~](?P<b>\d+(?:\.\d+)?))?m"],
    )
    gradient = first_range_match(
        compact,
        [r"(?:开挖边坡坡度|开挖坡度|边坡坡度|坡度)(?:约|为)?(?P<a>\d+(?:\.\d+)?)(?:[-～~](?P<b>\d+(?:\.\d+)?))?(?:°|º|度)"],
    )
    if length:
        values["slope_length_m"] = max(length)
    if height:
        values["slope_height_min_m"], values["slope_height_max_m"] = min(height), max(height)
    if gradient:
        values["slope_gradient_min_deg"], values["slope_gradient_max_deg"] = min(gradient), max(gradient)

    if any(term in compact for term in ["路堑边坡", "挖方边坡", "开挖边坡"]):
        values["slope_type"] = "路堑"
    elif any(term in compact for term in ["路堤边坡", "填方边坡"]):
        values["slope_type"] = "路堤"

    if "土岩混合" in compact:
        values["material_nature"] = "土岩混合"
    elif "岩质边坡" in compact:
        values["material_nature"] = "岩质"
    elif "土质边坡" in compact:
        values["material_nature"] = "土质"

    structure_terms = terms_in_text(compact, STRUCTURE_TERMS)
    if structure_terms:
        values["slope_structure_code"] = normalize_slope_structure(structure_terms[0])

    planes = extract_structural_planes(text)
    if planes:
        values["structural_planes"] = planes
        slope_plane = next((plane for plane in planes if plane.get("name") == "坡面"), None)
        if slope_plane:
            values["slope_aspect_deg"] = slope_plane.get("dip_direction")

    terrain = re.search(r"(?:属于|为)(?P<value>[^，。；]{2,24}(?:地貌区|地貌))", text)
    if terrain:
        values["geomorphology"] = terrain.group("value").strip()

    vegetation = extract_vegetation_condition(text)
    if vegetation:
        values["vegetation_condition"] = vegetation
    river = extract_river_relation(text)
    if river:
        values["river_relation"] = river

    severity = next(
        (value for value in ["毁坏", "严重破坏", "中等破坏", "轻微破坏", "基本完好"] if value in compact),
        None,
    )
    if severity:
        values["overall_slope_deformation_severity"] = severity
    measures = terms_in_text(compact, PROTECTION_TERMS, longest_first=True)
    if measures:
        values["measures"] = measures

    values["deformation_observations"] = extract_document_observations(text, domain="deformation")
    values["hydrology_observations"] = extract_document_observations(text, domain="hydrology")
    return values


def first_range_match(text: str, patterns: list[str]) -> tuple[float, float] | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            first_value = float(match.group("a"))
            second_value = float(match.group("b") or first_value)
            return first_value, second_value
    return None


def normalize_slope_structure(value: str) -> str:
    return {
        "顺层坡": "顺向坡",
        "顺向坡": "顺向坡",
        "斜交坡": "斜向坡",
        "横向坡": "横向坡",
        "逆向坡": "逆向坡",
        "反倾坡": "逆向坡",
    }.get(value, value)


def extract_structural_planes(text: str) -> list[dict[str, Any]]:
    output = []
    seen: set[tuple[str, int, int]] = set()
    normalized_text = re.sub(r"\s+", " ", text)
    for match in [*STRUCTURAL_PLANE_RE.finditer(normalized_text), *STRUCTURAL_PLANE_TABLE_RE.finditer(normalized_text)]:
        raw_name = match.groupdict().get("name") or match.groupdict().get("code") or "结构面"
        name = re.sub(r"\s+", "", raw_name)
        if name == "岩层":
            name = "岩层面"
        if name == "P":
            name = "坡面"
        elif name == "YC":
            name = "岩层面"
        dip_direction = int(match.group("dip_direction"))
        dip_angle = int(match.group("dip_angle"))
        if dip_direction > 360 or dip_angle > 90:
            continue
        signature = (name, dip_direction, dip_angle)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(
            {
                "name": name,
                "dip_direction": dip_direction,
                "dip_angle": dip_angle,
                "description": match.group(0),
                "temporal_scope": "historical_document_baseline",
            }
        )
    return output


def extract_vegetation_condition(text: str) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", "", text)
    density = None
    if any(term in compact for term in ["无植被", "植被缺失", "大量基岩裸露", "基岩裸露"]):
        density = "无"
    elif any(term in compact for term in ["植被稀疏", "局部植被", "少量植被"]):
        density = "稀疏"
    elif any(term in compact for term in ["植被茂密", "植被发育", "绿植发育", "大量绿植"]):
        density = "茂密"
    if not density:
        return None
    return {
        "density": density,
        "has_deformation_sign": None,
        "description": next((sentence.strip() for sentence in split_sentences(text) if "植被" in sentence or "绿植" in sentence or "基岩裸露" in sentence), None),
        "temporal_scope": "historical_document_baseline",
    }


def extract_river_relation(text: str) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", "", text)
    if not any(term in compact for term in ["临河", "河岸", "河道"]):
        return None
    return {
        "is_riverside": True,
        "bank": "左岸" if "左岸" in compact else "右岸" if "右岸" in compact else None,
        "channel_landform": next((value for value in ["凹岸", "凸岸", "直线段"] if value in compact), None),
        "description": next((sentence.strip() for sentence in split_sentences(text) if any(term in sentence for term in ["临河", "河岸", "河道"])), None),
        "temporal_scope": "historical_document_baseline",
    }


def extract_document_observations(text: str, *, domain: str) -> list[dict[str, Any]]:
    if domain == "deformation":
        keywords = ["裂缝", "垮塌", "掉块", "掉落", "滑塌", "溜滑", "崩塌", "落石", "变形", "隆起", "管涌", "渗水"]
        type_order = ["管涌", "渗水", "隆起", "裂缝", "垮塌", "滑塌", "溜滑", "崩塌", "落石", "掉块", "掉落", "变形"]
    else:
        keywords = ["地下水", "地表水", "汇水", "渗水", "滴水", "冲刷", "排水", "雨季", "降雨", "暴雨"]
        type_order = ["地下水", "地表水", "汇水", "渗水", "滴水", "冲刷", "排水", "暴雨", "降雨", "雨季"]
    output = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        compact = re.sub(r"\s+", "", sentence)
        if not any(term in compact for term in keywords):
            continue
        if domain == "hydrology":
            observed_water_terms = ["地下水", "地表水", "汇水", "渗水", "滴水", "冲刷", "排水"]
            observed_event_terms = ["雨季期间", "降雨期间", "暴雨期间", "雨水冲刷", "受降雨冲刷"]
            if not any(term in compact for term in [*observed_water_terms, *observed_event_terms]):
                continue
        else:
            generic_prediction = any(term in compact for term in ["易发生", "易形成", "可能产生", "可能发生", "不利于", "成因"])
            explicit_history_or_state = any(
                term in compact for term in ["发生过", "已发生", "目前", "现状", "存在", "可见", "出现", "曾发生"]
            )
            observed_event = any(
                term in compact
                for term in ["发生过", "已发生", "目前", "现状", "存在", "可见", "出现", "垮塌", "滑塌", "溜滑", "掉落", "掉块", "裂缝"]
            )
            if generic_prediction and not explicit_history_or_state:
                continue
            if not observed_event:
                continue
        observation_type = next((term for term in type_order if term in compact), domain)
        description = sentence.strip()[:500]
        signature = f"{observation_type}:{description}"
        if signature in seen:
            continue
        seen.add(signature)
        timing_match = re.search(r"(\d+\s*年前|雨季期间|降雨期间|暴雨期间|近期|目前|现状)", description)
        output.append(
            {
                "type": observation_type,
                "location": next((term for term in ["坡顶", "坡面", "坡脚", "前缘", "后缘", "左侧", "右侧"] if term in compact), None),
                "scale": next((term for term in ["大范围", "小范围", "局部", "多处", "少量", "大量"] if term in compact), None),
                "timing": timing_match.group(1).replace(" ", "") if timing_match else "报告形成前或报告描述期",
                "status": "historical_document_observation",
                "description": description,
                "temporal_scope": "historical_document_baseline",
                "current_status_known": False,
            }
        )
        if len(output) >= 8:
            break
    return output


def split_sentences(text: str) -> list[str]:
    return [part for part in re.split(r"[。；;]\s*", text) if part.strip()]


def extract_stability_scenarios(text: str) -> list[dict[str, Any]]:
    """Extract paired survey-condition factors that are too important to leave to an LLM."""
    compact = re.sub(r"\s+", "", text)
    pattern = re.compile(
        r"天然工况下的稳定性系数为(?P<natural>\d+\.\d+)"
        r"(?:，?属于(?P<natural_status>[^，。]{1,12})状态)?"
        r".{0,120}?暴雨工况下稳定性系数为(?P<rain>\d+\.\d+)"
        r"(?:，?属于(?P<rain_status>[^，。]{1,12})状态)?"
    )
    output = []
    for match in pattern.finditer(compact):
        output.extend(
            [
                {
                    "condition": "天然工况",
                    "safety_factor": float(match.group("natural")),
                    "required_factor": None,
                    "status": match.group("natural_status"),
                    "analysis_scope": "现状边坡",
                    "source_kind": "定量计算",
                    "evidence_text": match.group(0),
                    "_evidence_start": match.start(),
                },
                {
                    "condition": "暴雨工况",
                    "safety_factor": float(match.group("rain")),
                    "required_factor": None,
                    "status": match.group("rain_status"),
                    "analysis_scope": "现状边坡",
                    "source_kind": "定量计算",
                    "evidence_text": match.group(0),
                    "_evidence_start": match.start(),
                },
            ]
        )
    state_pattern = re.compile(
        r"(?P<condition>天然|饱和|饱水)状态(?:下)?"
        r"(?:(?!天然状态|饱和状态|饱水状态|稳定性?系数为).){0,40}?稳定性?系数为(?P<value>\d+\.\d+)"
        r"，?(?:(?!；|。).){0,20}?(?:状态为|属于|处于)(?P<status>不稳定|欠稳定|基本稳定|稳定)(?:状态)?"
    )
    for match in state_pattern.finditer(compact):
        condition = "饱和" if match.group("condition") == "饱水" else match.group("condition")
        output.append(
            {
                "condition": f"{condition}状态",
                "safety_factor": float(match.group("value")),
                "required_factor": None,
                "status": match.group("status"),
                "analysis_scope": "现状边坡",
                "source_kind": "定量计算",
                "evidence_text": match.group(0),
                "_evidence_start": match.start(),
            }
        )
    return output


PROTECTION_TERMS = ["坡面清危", "挂网锚喷", "主动防护网", "局部单独锚杆", "单独长锚杆", "框架锚杆", "放坡", "截水沟", "排水沟", "急流槽"]


def split_page_panels(blocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    panels = {"left": [], "right": []}
    for block in blocks:
        bbox = block.get("bbox") or [0, 0, 0, 0]
        center = (float(bbox[0]) + float(bbox[2])) / 2
        panels["left" if center < 600 else "right"].append(block)
    for values in panels.values():
        values.sort(key=lambda row: (bbox_value(row, 1), bbox_value(row, 0)))
    return panels


def treatment_table_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    headers = [block for block in blocks if "治理方案一览表" in block["text"]]
    if headers:
        center_x = sum((bbox_value(block, 0) + bbox_value(block, 2)) / 2 for block in headers) / len(headers)
        use_right = center_x > 600
    else:
        station_blocks = [block for block in blocks if STATION_RE.search(block["text"]) and FS_PAIR_RE.search(block["text"])]
        use_right = bool(station_blocks and bbox_value(station_blocks[0], 0) > 600)
    return [block for block in blocks if (bbox_value(block, 0) >= 590 if use_right else bbox_value(block, 2) <= 590)]


def merge_slope_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for rows in groups:
        for row in rows:
            route_code = str(row.get("route_code") or "ROUTE")
            key = (route_code, row["station"])
            target = merged.setdefault(
                key,
                {"station": row["station"], "route_code": route_code, "sources": [], "_field_sources": {}, "confidence": 0.0},
            )
            source_ref = {
                "method": row["method"],
                "page": row["page"],
                "block_id": row["evidence_block_id"],
                "confidence": row["confidence"],
            }
            for key, value in row.items():
                if key in {"station", "sources", "confidence"} or value in (None, "", [], {}):
                    continue
                if isinstance(value, list) and isinstance(target.get(key), list):
                    target[key] = union(target[key], value)
                elif key not in target or target[key] in (None, "", [], {}):
                    target[key] = value
                target["_field_sources"].setdefault(key, []).append(source_ref)
            target["sources"].append(source_ref)
            target["confidence"] = max(float(target["confidence"]), float(row["confidence"]))
    output = sorted(merged.values(), key=lambda row: (str(row.get("route_code") or "ROUTE"), station_start_m(row["station"])))
    for row in output:
        infer_material_nature(row)
    return output


def infer_material_nature(row: dict[str, Any]) -> None:
    if row.get("material_nature"):
        return
    lithologies = set(row.get("lithology_terms", []))
    rock_terms = {"砂质页岩", "页岩", "灰岩", "泥岩", "砂岩"}
    soil_terms = {"黏土", "粘土", "碎石土", "块石土", "堆积体"}
    has_rock = bool(lithologies & rock_terms) or "危岩体" in str(row.get("source_disaster_label") or "")
    has_soil = bool(lithologies & soil_terms)
    if has_rock and has_soil:
        value = "土岩混合"
    elif has_rock:
        value = "岩质"
    elif has_soil:
        value = "土质"
    else:
        return
    row["material_nature"] = value
    row["material_nature_basis"] = "由已提取岩性词项或危岩体类型归一化"
    source_refs = [
        *row.get("_field_sources", {}).get("lithology_terms", []),
        *row.get("_field_sources", {}).get("source_disaster_label", []),
    ]
    if source_refs:
        row.setdefault("_field_sources", {})["material_nature"] = source_refs


def build_assertions(slopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assertions = []
    skip = {
        "station", "sources", "_field_sources", "confidence", "method", "page",
        "evidence_block_id", "evidence_block_ids", "bbox", "raw_row", "raw_text",
        "points", "section_evidence", "section_text_samples", "material_nature_basis",
    }
    for slope in slopes:
        for key, value in slope.items():
            if key in skip or value in (None, "", [], {}):
                continue
            assertions.append(
                {
                    "id": f"assert_{len(assertions)+1:04d}",
                    "subject_key": slope["station"],
                    "property_code": key,
                    "value": value,
                    "confidence": slope.get("confidence"),
                    "evidence": slope.get("_field_sources", {}).get(key, []),
                    "extraction_method": "deterministic_native_layout",
                    "review_status": "pending",
                }
            )
    return assertions


def candidate_row(station: str, page: int, block: dict[str, Any], method: str, confidence: float, **values: Any) -> dict[str, Any]:
    return {
        "station": station,
        "page": page,
        "evidence_block_id": block["id"],
        "evidence_block_ids": [block["id"]],
        "document_id": block.get("document_id"),
        "bbox": block.get("bbox"),
        "method": method,
        "confidence": confidence,
        **values,
    }


def normalize_station_match(match: re.Match[str] | None) -> str | None:
    if not match:
        return None
    start_km = int(match.group("start_km"))
    start_m = int(match.group("start_m"))
    end_km_raw = match.group("end_km")
    end_km = int(end_km_raw) if end_km_raw else start_km
    end_m = int(match.group("end_m"))
    return f"K{start_km}+{start_m:03d}-K{end_km}+{end_m:03d}"


def mark_station_resolution(candidate: dict[str, Any]) -> None:
    """Keep malformed source rows for review without creating false slopes."""
    bounds = station_bounds_for_validation(candidate.get("station"))
    if bounds is None:
        candidate["eligible_as_slope_root"] = False
        candidate["entity_resolution_status"] = "invalid_station_syntax"
        candidate["quality_issues"] = ["invalid_station_syntax"]
    elif bounds[1] <= bounds[0]:
        candidate["eligible_as_slope_root"] = False
        candidate["entity_resolution_status"] = "non_increasing_station_range"
        candidate["quality_issues"] = ["non_increasing_station_range"]
    else:
        candidate["eligible_as_slope_root"] = True
        candidate["entity_resolution_status"] = "station_range_valid"


def station_bounds_for_validation(station: Any) -> tuple[int, int] | None:
    match = STATION_RE.search(str(station or ""))
    if not match:
        return None
    start = int(match.group("start_km")) * 1000 + int(match.group("start_m"))
    end_km = int(match.group("end_km") or match.group("start_km"))
    end = end_km * 1000 + int(match.group("end_m"))
    return start, end


def station_start_m(station: str) -> int:
    match = STATION_RE.search(station)
    return int(match.group("start_km")) * 1000 + int(match.group("start_m")) if match else 0


def parse_leading_int(lines: list[str]) -> int | None:
    if not lines:
        return None
    match = re.match(r"^(\d{1,2})$", lines[0])
    return int(match.group(1)) if match else None


def parse_registry_length(lines: list[str], station: str, row_no: int | None = None) -> float | None:
    for index, line in enumerate(lines):
        if re.fullmatch(r"\d+(?:\.\d+)?", line):
            value = float(line)
            if index == 0 and row_no is not None and value == row_no:
                continue
            if value > 0:
                return value
    return None


def registry_table_header(table: dict[str, Any]) -> bool:
    rows = table.get("rows", [])[:3]
    header = " ".join(str(cell or "") for row in rows for cell in row)
    has_station = any(term in header for term in ["起讫点桩号", "桩号", "主要灾点", "主要灾害点"])
    has_type = any(term in header for term in ["灾害类型", "类型及规模", "灾点类型", "主要灾点"])
    return has_station and has_type


def page_has_registry_title(blocks: list[dict[str, Any]]) -> bool:
    text = " ".join(str(block.get("text", "")) for block in blocks)
    return any(term in text for term in REGISTRY_TITLE_TERMS) or (
        "地质灾害分布情况" in text and "表" in text
    )


def hazard_label(text: str) -> str | None:
    compact = " ".join(str(text or "").split())
    matches: list[str] = []
    for term in HAZARD_TERMS:
        if term in compact and not any(term in selected for selected in matches):
            matches.append(term)
    if not matches:
        return None
    # Preserve useful combinations such as "崩塌、危岩体" without returning
    # a whole paragraph as the classification label.
    return "、".join(dict.fromkeys(matches))


def first_integer_cell(cells: list[str]) -> int | None:
    for cell in cells[:2]:
        if re.fullmatch(r"\d{1,3}", cell.strip()):
            return int(cell)
    return None


def registry_length_from_cells(cells: list[str], station: str, no: int | None) -> float | None:
    span = station_span_m(station)
    values: list[float] = []
    for index, cell in enumerate(cells):
        value = cell.strip()
        if index == 0 and no is not None and value == str(no):
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            values.append(float(value))
    if span is not None:
        exact = next((value for value in values if abs(value - span) < 0.01), None)
        if exact is not None:
            return exact
    return values[0] if values else span


def station_span_m(station: str) -> float | None:
    match = STATION_RE.search(station)
    if not match:
        return None
    start = int(match.group("start_km")) * 1000 + int(match.group("start_m"))
    end_km = int(match.group("end_km") or match.group("start_km"))
    end = end_km * 1000 + int(match.group("end_m"))
    return float(abs(end - start))


def infer_route_code_from_values(*values: Any) -> str:
    match = ROUTE_CODE_RE.search(" ".join(str(value or "") for value in values))
    return match.group(1).upper() if match else "ROUTE"


def discover_single_site_document(
    document: dict[str, Any],
    by_page: dict[tuple[str, int], list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Return one root for a report devoted to a single slope/hazard site."""
    document_id = document["id"]
    route_code = infer_route_code_from_values(document.get("file_name"), document.get("title"))
    ranked: list[tuple[int, int, int, dict[str, Any], re.Match[str], str]] = []
    for (doc_id, page), blocks in by_page.items():
        if doc_id != document_id:
            continue
        for panel_blocks in split_page_panels(blocks).values():
            if not panel_blocks:
                continue
            compact = " ".join(" ".join(str(block.get("text", "")).split()) for block in panel_blocks)
            for match in STATION_RE.finditer(compact):
                window = compact[max(0, match.start() - 240) : min(len(compact), match.end() + 900)]
                score = 0
                score += 24 if "影响公路里程" in window else 0
                score += 18 if "边坡灾害防治工程" in window else 0
                score += 16 if any(term in window for term in ["滑坡工程", "山体滑坡", "滑坡体"]) else 0
                score += 12 if "治理段长度" in window or "治理段边坡" in window else 0
                score += 8 if hazard_label(window) else 0
                score += 4 if any(term in window for term in ["勘察说明", "勘察报告", "工程地质"] ) else 0
                score -= 12 if any(term in window for term in ["路线起点", "路线终点", "项目范围", "调查路线长度"]) else 0
                score -= 6 if "目录" in window else 0
                evidence_block = next(
                    (block for block in panel_blocks if normalize_station_match(STATION_RE.search(str(block.get("text", "")))) == normalize_station_match(match)),
                    panel_blocks[0],
                )
                ranked.append((score, -page, -match.start(), evidence_block, match, window))
    if not ranked:
        return None
    score, _, _, block, match, window = max(ranked, key=lambda item: item[:3])
    if score < 12:
        return None
    station = normalize_station_match(match)
    if not station:
        return None
    reported = re.search(r"(?:治理段长度|边坡长度|影响公路里程(?:长度)?)[^\d]{0,12}(\d+(?:\.\d+)?)\s*m", window, re.IGNORECASE)
    label = hazard_label(window) or "边坡"
    return candidate_row(
        station,
        int(block["page"]),
        block,
        "single_site_document_context",
        0.94,
        route_code=route_code,
        side=next((value for value in ["左侧", "右侧"] if value in window), None),
        slope_length_m=float(reported.group(1)) if reported else station_span_m(station),
        source_alias=f"{station} {label}",
        source_disaster_label=label,
        raw_text=window,
    )


def bbox_value(block: dict[str, Any], index: int) -> float:
    bbox = block.get("bbox") or [0, 0, 0, 0]
    return float(bbox[index])


def panel_bbox(blocks: list[dict[str, Any]]) -> list[float] | None:
    boxes = [block.get("bbox") for block in blocks if block.get("bbox")]
    if not boxes:
        return None
    return [min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)]


def terms_in_text(text: str, terms: list[str], longest_first: bool = False) -> list[str]:
    ordered = sorted(terms, key=len, reverse=True) if longest_first else terms
    found = []
    remainder = text
    for term in ordered:
        if term in remainder:
            found.append(term)
            if longest_first:
                remainder = remainder.replace(term, "")
    return found


def union(left: list[Any], right: list[Any]) -> list[Any]:
    result = list(left)
    for item in right:
        if item not in result:
            result.append(item)
    return result


def dedupe_by_station(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = result.get(row["station"])
        if existing is None or float(row.get("confidence", 0)) > float(existing.get("confidence", 0)):
            result[row["station"]] = row
    return sorted(result.values(), key=lambda row: station_start_m(row["station"]))


def dedupe_by_route_station(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("route_code") or "ROUTE"), row["station"])
        existing = result.get(key)
        if existing is None or float(row.get("confidence", 0)) > float(existing.get("confidence", 0)):
            result[key] = row
    return sorted(result.values(), key=lambda row: (str(row.get("route_code") or "ROUTE"), station_start_m(row["station"])))


def evaluate_extraction(
    extracted: dict[str, Any],
    reference_records: list[dict[str, Any]],
    reference_coordinates: dict[int, tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> dict[str, Any]:
    reference = {normalize_reference_station(row["stake"]): row for row in reference_records}
    predicted = {row["station"]: row for row in extracted.get("slopes", [])}
    reference_keys = set(reference)
    predicted_keys = set(predicted)
    true_keys = reference_keys & predicted_keys
    precision = len(true_keys) / len(predicted_keys) if predicted_keys else 0
    recall = len(true_keys) / len(reference_keys) if reference_keys else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0

    side_correct = sum(predicted[key].get("side") == reference[key].get("side") for key in true_keys)
    length_correct = sum(float(predicted[key].get("slope_length_m", -1)) == float(reference[key].get("length_m", -2)) for key in true_keys)
    coordinate_correct = 0
    coordinate_total = 0
    treatment_fs_correct = 0
    treatment_fs_total = 0
    height_correct = 0
    height_total = 0
    angle_correct = 0
    angle_total = 0
    lithology_correct = 0
    lithology_total = 0
    stratum_correct = 0
    stratum_total = 0
    for key in true_keys:
        pred = predicted[key]
        ref = reference[key]
        no = int(ref["no"])
        if pred.get("start_coordinate") and pred.get("end_coordinate") and no in reference_coordinates:
            coordinate_total += 1
            expected_start, expected_end = reference_coordinates[no]
            if point_matches(pred["start_coordinate"], expected_start) and point_matches(pred["end_coordinate"], expected_end):
                coordinate_correct += 1
        expected_pairs = fs_pairs(reference[key].get("treated_safety_factor", ""))
        predicted_pairs = pred.get("safety_factor_pairs", [])
        if expected_pairs:
            treatment_fs_total += 1
            if all(any(abs(ep["actual"] - pp["actual"]) < 1e-6 and abs(ep["required"] - pp["required"]) < 1e-6 for pp in predicted_pairs) for ep in expected_pairs):
                treatment_fs_correct += 1
        expected_height = number_range(ref.get("height_m"))
        if expected_height and pred.get("slope_height_min_m") is not None:
            height_total += 1
            if range_matches(pred.get("slope_height_min_m"), pred.get("slope_height_max_m"), expected_height):
                height_correct += 1
        expected_angle = number_range(ref.get("slope_angle"))
        if expected_angle and pred.get("slope_gradient_min_deg") is not None:
            angle_total += 1
            if range_matches(pred.get("slope_gradient_min_deg"), pred.get("slope_gradient_max_deg"), expected_angle):
                angle_correct += 1
        if ref.get("lithology") and pred.get("lithology_terms"):
            lithology_total += 1
            expected_terms = [term for term in LITHOLOGY_TERMS if term in ref["lithology"]]
            if expected_terms and all(term in pred["lithology_terms"] for term in expected_terms):
                lithology_correct += 1
        if ref.get("stratum") and pred.get("stratum_terms"):
            stratum_total += 1
            if any(term in ref["stratum"] and term in pred["stratum_terms"] for term in STRATUM_TERMS):
                stratum_correct += 1

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference": "G209 manually reviewed seed (gold-lite, not independent test set)",
        "registry": metric_counts(len(true_keys), len(predicted_keys) - len(true_keys), len(reference_keys) - len(true_keys), precision, recall, f1),
        "field_accuracy": {
            "side": accuracy(side_correct, len(true_keys)),
            "length": accuracy(length_correct, len(true_keys)),
            "coordinates": accuracy(coordinate_correct, coordinate_total),
            "treatment_safety_factor": accuracy(treatment_fs_correct, treatment_fs_total),
            "height_range": accuracy(height_correct, height_total),
            "slope_angle_range": accuracy(angle_correct, angle_total),
            "lithology_terms": accuracy(lithology_correct, lithology_total),
            "stratum_terms": accuracy(stratum_correct, stratum_total),
        },
        "automatic_coverage": {
            "registry": coverage_count(predicted, "side", len(reference_keys)),
            "coordinates": coverage_count(predicted, "start_coordinate", len(reference_keys)),
            "treatment": coverage_count(predicted, "safety_factor_pairs", len(reference_keys)),
            "geometry": coverage_count(predicted, "slope_height_min_m", len(reference_keys)),
            "lithology": coverage_count(predicted, "lithology_terms", len(reference_keys)),
            "stratum": coverage_count(predicted, "stratum_terms", len(reference_keys)),
        },
        "parser": extracted.get("stats", {}),
        "limitations": [
            "参考集来自当前种子数据，只能做回归评测，不能替代独立人工金标准。",
            "复杂岩性、结构面、机理和关系抽取尚未纳入本轮指标。",
            "OCR候选页仅完成路由，未执行的OCR页不计识别准确率。",
        ],
    }


def apply_extracted_candidates(graph: dict[str, Any], extracted: dict[str, Any]) -> dict[str, Any]:
    """Merge deterministic candidates into provisional slope snapshots.

    The graph remains reviewable: candidate values never mark a node approved,
    and the full property assertions are retained beside the graph.
    """
    slope_nodes = [node for node in graph.get("nodes", []) if node.get("type") == "Slope"]
    by_station = {
        f"{node.get('props', {}).get('start_station_raw')}-{node.get('props', {}).get('end_station_raw')}": node
        for node in slope_nodes
    }
    applied = 0
    for candidate in extracted.get("slopes", []):
        node = by_station.get(candidate["station"])
        if not node:
            continue
        props = node.setdefault("props", {})
        mapping = {
            "source_alias": "source_alias",
            "side": "side",
            "slope_length_m": "slope_length_m",
            "start_coordinate": "start_coordinate",
            "end_coordinate": "end_coordinate",
            "coordinate_crs": "coordinate_crs",
            "slope_height_min_m": "slope_height_min_m",
            "slope_height_max_m": "slope_height_max_m",
            "slope_gradient_min_deg": "slope_gradient_min_deg",
            "slope_gradient_max_deg": "slope_gradient_max_deg",
            "lithology_terms": "lithology_terms_extracted",
            "stratum_terms": "stratum_terms_extracted",
            "slope_structure_terms": "slope_structure_terms_extracted",
            "causal_factor_terms": "causal_factor_terms_extracted",
        }
        fields = []
        for source_key, target_key in mapping.items():
            value = candidate.get(source_key)
            if value in (None, "", [], {}):
                continue
            props[target_key] = value
            fields.append(target_key)
            applied += 1
        props["automatic_extraction"] = {
            "method": "deterministic_native_layout",
            "confidence": candidate.get("confidence"),
            "fields": sorted(fields),
            "source_count": len(candidate.get("sources", [])),
            "review_status": "pending",
        }
    graph.setdefault("meta", {})["automatic_extraction"] = {
        "candidate_slopes": len(extracted.get("slopes", [])),
        "property_assertions": len(extracted.get("assertions", [])),
        "applied_property_values": applied,
        "mode": extracted.get("mode"),
        "review_status": "pending",
    }
    graph["property_assertions"] = extracted.get("assertions", [])
    return graph


def normalize_reference_station(value: str) -> str:
    match = STATION_RE.search(value.replace("～", "-").replace("~", "-"))
    return normalize_station_match(match) or value


def point_matches(point: dict[str, Any], expected: tuple[float, float, float], tolerance: float = 0.002) -> bool:
    return all(abs(float(point[key]) - float(value)) <= tolerance for key, value in zip(["x", "y", "z"], expected))


def fs_pairs(value: str) -> list[dict[str, float]]:
    return [{"actual": float(m.group("actual")), "required": float(m.group("required"))} for m in FS_PAIR_RE.finditer(value)]


def number_range(value: Any) -> tuple[float, float] | None:
    values = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))]
    return (min(values), max(values)) if values else None


def range_matches(actual_min: Any, actual_max: Any, expected: tuple[float, float], tolerance: float = 0.01) -> bool:
    return abs(float(actual_min) - expected[0]) <= tolerance and abs(float(actual_max) - expected[1]) <= tolerance


def coverage_count(predicted: dict[str, dict[str, Any]], field: str, total: int) -> dict[str, Any]:
    count = sum(1 for row in predicted.values() if row.get(field) not in (None, "", [], {}))
    return {"available": count, "total": total, "coverage": round(count / total, 4) if total else 0}


def accuracy(correct: int, total: int) -> dict[str, Any]:
    return {"correct": correct, "total": total, "accuracy": round(correct / total, 4) if total else None}


def metric_counts(tp: int, fp: int, fn: int, precision: float, recall: float, f1: float) -> dict[str, Any]:
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
