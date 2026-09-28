"""Conservative, source-addressable extraction of engineering tables.

No report names, station seeds or numerical answers are embedded here. Tables
without an unambiguous section remain available as unassigned candidates.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def number(value: Any) -> float | None:
    value = compact(value)
    return float(value) if re.fullmatch(r"-?\d+(?:\.\d+)?", value) else None


def condition(value: Any) -> str | None:
    value = compact(value).replace("现在工况", "现状工况")
    # Do not turn a header spanning several conditions into one condition.
    matches = [name for name in ("天然", "饱和", "饱水", "暴雨", "地震", "正常", "非正常", "现状") if name in value]
    if "非正常" in matches:
        matches.remove("正常")
    return matches[0] if len(matches) == 1 else None


def parameter(value: Any) -> tuple[str, str] | None:
    value = compact(value).lower().replace("³", "3")
    if ("重度" in value or "γ" in value) and "kn/m3" in value:
        return "unit_weight", "kN/m³"
    if ("内聚力" in value or "黏聚力" in value or "粘聚力" in value or re.match(r"c[/（(]", value)) and "kpa" in value:
        return "cohesion", "kPa"
    if any(word in value for word in ("内摩擦角", "φ", "ϕ")) and any(word in value for word in ("°", "º", "度")):
        return "friction_angle", "°"
    return None


def extract_parameter_cells(rows: list[list[Any]]) -> list[dict[str, Any]]:
    """Support parameter-per-row and merged, two-level parameter-per-column tables."""
    cells = [[compact(cell) for cell in row] for row in rows]
    if not cells or not cells[0]:
        return []
    output = []
    header = cells[0]
    columns = {i: condition(cell) for i, cell in enumerate(header) if condition(cell)}
    if columns and any(cell in {"项目", "参数", "指标"} for cell in header):
        label_col = next(i for i, cell in enumerate(header) if cell in {"项目", "参数", "指标"})
        material = None
        for row_index, row in enumerate(cells[1:], 1):
            if len(row) != len(header):
                continue
            if label_col > 0 and row[0]:
                material = row[0]
            prop = parameter(row[label_col])
            if not prop or not material:
                continue
            for column, state in columns.items():
                value = number(row[column])
                if value is not None:
                    output.append(dict(material=material, parameter=prop[0], unit=prop[1], condition=state,
                                       value=value, row_index=row_index, column_index=column))
    elif any(cell in {"工况", "项目"} for cell in header) and any(parameter(cell) for cell in header):
        state_col = next(i for i, cell in enumerate(header) if cell in {"工况", "项目"})
        material = header[0] if state_col > 0 and any(word in header[0] for word in ("岩", "土")) and header[0] != "岩性" else None
        mapping = {i: parameter(cell) for i, cell in enumerate(header) if parameter(cell)}
        for row_index, row in enumerate(cells[1:], 1):
            if len(row) != len(header):
                continue
            if state_col > 0 and row[0]:
                material = row[0]
            state = condition(row[state_col])
            if not material or not state:
                continue
            for column, prop in mapping.items():
                value = number(row[column])
                if value is not None:
                    output.append(dict(material=material, parameter=prop[0], unit=prop[1], condition=state,
                                       value=value, row_index=row_index, column_index=column))
    elif len(cells) >= 3 and len(cells[1]) == len(header):
        prop = None
        mapping = {}
        for column, label in enumerate(header):
            # Only extend genuinely empty merged header cells.
            prop = parameter(label) if label else prop
            state = condition(cells[1][column])
            if prop and state:
                mapping[column] = (prop, state)
        for row_index, row in enumerate(cells[2:], 2):
            if len(row) != len(header) or not row[0] or number(row[0]) is not None:
                continue
            for column, (prop, state) in mapping.items():
                value = number(row[column])
                if value is not None:
                    output.append(dict(material=row[0], parameter=prop[0], unit=prop[1], condition=state,
                                       value=value, row_index=row_index, column_index=column))
    return output


def extract_layered_factor_cells(rows: list[list[Any]]) -> list[dict[str, Any]]:
    """Recover a fully specified two-level header even if PDF cells merge.

    Only this unambiguous header grammar is accepted. The force columns are
    counted to detect shifted rows, but never interpreted as safety factors.
    """
    if len(rows) < 3:
        return []
    header = "".join(compact(c) for c in rows[0])
    if not re.fullmatch(r"(?:计算剖面|计算断面)浅层稳定性系数整体稳定性系数剩余下滑力", header):
        return []
    subheader = "".join(compact(c) for c in rows[1])
    subheader = subheader.replace("（", "(").replace("）", ")").lower()
    if subheader != "暴雨天然暴雨天然(kn/m)暴雨(kn/m)":
        return []
    result = []
    for row_index, row in enumerate(rows[2:], 2):
        if not row or not compact(row[0]):
            continue
        tokens = " ".join(str(cell or "") for cell in row[1:]).split()
        values = [number(token) for token in tokens]
        if len(values) != 5 or any(v is None for v in values) or any(not 0 < v < 100 for v in values[:3]):
            continue
        for index, (scope, state) in enumerate([("浅层边坡", "暴雨"), ("整体边坡", "天然"), ("整体边坡", "暴雨")]):
            result.append({"condition": state, "analysis_scope": scope, "section": compact(row[0]),
                           "safety_factor": values[index], "required_factor": None, "status": None,
                           "row_index": row_index, "normalized_value_index": index,
                           "header_reconstruction": "verified_layered_factors_and_force_columns"})
    return result


def normalize_factor_grid(rows: list[list[Any]]) -> list[list[Any]]:
    """Collapse artificial subdivisions only inside explicit header spans.

    Some PDF line grids split each real column into several empty columns;
    the header and data text then occupy different subcolumns. Reject spans
    with multiple nonempty values rather than guessing their correspondence.
    """
    if not rows or not any(re.fullmatch(r"F[Ss]|(?:危岩)?稳定性?系数(?:F[Ss]?|K)?", compact(c)) for c in rows[0]):
        return rows
    starts = [i for i,c in enumerate(rows[0]) if compact(c)]
    if not starts or starts[0] != 0 or len(starts) == len(rows[0]):
        return rows
    spans = list(zip(starts, [*starts[1:], len(rows[0])]))
    normalized = []
    for row in rows:
        if len(row) != len(rows[0]):
            return rows
        cells = [[c for c in row[a:b] if compact(c)] for a,b in spans]
        if any(len(c)>1 for c in cells):
            return rows
        normalized.append([c[0] if c else '' for c in cells])
    return normalized


def extract_factor_cells(rows: list[list[Any]]) -> list[dict[str, Any]]:
    layered = extract_layered_factor_cells(rows)
    if layered:
        return layered
    cells = [[compact(cell) for cell in row] for row in normalize_factor_grid(rows)]
    if not cells:
        return []
    header = cells[0]
    actual_col = next((i for i, cell in enumerate(header) if re.fullmatch(
        r"(?:危岩)?稳定性?系数(?:F[Ss]?|K)?|F[Ss]", cell)), None)
    if actual_col is None:
        return []  # A safety threshold alone is not a computed result.
    state_col = next((i for i, cell in enumerate(header) if cell in {"工况", "计算工况", "项目"}), None)
    required_col = next((i for i, cell in enumerate(header) if cell in {"安全系数", "设计安全系数", "要求安全系数"}), None)
    status_col = next((i for i, cell in enumerate(header) if cell in {"稳定性评价", "稳定状态", "稳定性判断"}), None)
    output = []
    if state_col is not None:
        subjects = {}
        subject_columns = {}
        for i, cell in enumerate(header):
            role = ("failure_mode" if cell in {"破坏模式", "破坏形式"} else
                    "body_id" if cell in {"危岩编号", "位置"} else
                    "section" if cell in {"计算剖面", "断面", "剖面"} else None)
            if role:
                subject_columns[i] = role
        # A merged blank top-left header can still have explicit section labels
        # in its data cells. Do not infer a section from an arbitrary number.
        if header[0] == '' and any(re.fullmatch(r"[A-Za-z0-9ⅠⅡⅢⅣⅤ]+[-－—][A-Za-z0-9ⅠⅡⅢⅣⅤ]+[′’']?[剖断]面", row[0]) for row in cells[1:] if row):
            subject_columns[0] = 'section'
        for row_index, row in enumerate(cells[1:], 1):
            if len(row) != len(header):
                continue
            body_column = next((i for i, role in subject_columns.items() if role == "body_id"), None)
            if body_column is not None and row[body_column] and row[body_column] != subjects.get("body_id"):
                subjects.clear()
            for column, role in subject_columns.items():
                if row[column]:
                    subjects[role] = row[column]
            state, value = condition(row[state_col]), number(row[actual_col])
            if state and value is not None and 0 < value < 100:
                output.append(dict(condition=row[state_col], safety_factor=value,
                                   required_factor=number(row[required_col]) if required_col is not None else None,
                                   status=row[status_col] or None if status_col is not None else None,
                                   **subjects, row_index=row_index, column_index=actual_col))
    elif len(cells) >= 3:
        columns = {i: cell for i, cell in enumerate(cells[1]) if condition(cell)}
        for row_index, row in enumerate(cells[2:], 2):
            if len(row) != len(header) or not row[0]:
                continue
            for column, state in columns.items():
                value = number(row[column])
                if value is not None and 0 < value < 100:
                    output.append(dict(condition=state, safety_factor=value, required_factor=None, status=None,
                                       section=row[0], row_index=row_index, column_index=column))
    return output


def stitch_factor_tables(tables, order, blocks):
    """Join adjacent header-only and data-only column fragments conservatively."""
    ordered = sorted((t for t in tables if t.get("bbox") and t.get("review_status") != "failed"), key=order)
    output = []
    for index, table in enumerate(ordered):
        rows = table.get("rows", [])
        previous = ordered[index - 1] if index else None
        if previous and rows and previous.get("rows"):
            header = previous["rows"][0]
            same_page_next_column = (len(previous['rows']) == 1 and order(previous)[:2] == (order(table)[0], 0) and order(table)[1] == 1)
            next_page = (order(previous)[:2] == (order(table)[0]-1, 1) and order(table)[1] == 0
                         and table['bbox'][1] < 150 and previous['bbox'][3] > 600
                         and abs((previous['bbox'][2]-previous['bbox'][0]) / (table['bbox'][2]-table['bbox'][0])-1) < .1
                         and bool(extract_factor_cells(previous['rows'])))
            intervening_heading = any(order(previous) < order(b) < order(table)
                                      and re.match(r"\s*(?:\d+(?:\.\d+){1,3}\s+K|表\s*\d)", b.get("text", "")) for b in blocks)
            merged = [header, *rows]
            if ((same_page_next_column or next_page) and not intervening_heading
                    and all(len(row) == len(header) for row in rows)
                    and not extract_factor_cells(rows) and len(extract_factor_cells(merged)) == len(rows)):
                table = {**table, "rows": merged, "header_fragment": previous,
                         "source_fragments": [{"id": t["id"], "page": t["page"], "bbox": t["bbox"]}
                                              for t in (previous, table)]}
        output.append(table)
    return output


def extract_engineering_tables(parsed: dict[str, Any], registry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .extractors import STATION_RE, normalize_station_match, infer_route_code_from_values

    documents = {doc["id"]: doc for doc in parsed.get("documents", [])}
    pages = {(p["document_id"], p["page"]): p for p in parsed.get("pages", [])}
    allowed = defaultdict(set)
    for slope in registry:
        allowed[slope.get("route_code")].add(slope["station"])
    blocks = defaultdict(list)
    for block in parsed.get("text_blocks", []):
        if block.get("bbox"):
            blocks[block["document_id"]].append(block)

    def order(item):
        page = pages.get((item["document_id"], item["page"]), {})
        midpoint = float(page.get("width") or 1200) / 2
        box = item["bbox"]
        panel = int((box[0] + box[2]) / 2 >= midpoint)
        return (int(item["page"]), panel, box[1])

    output = []
    tables_by_doc = defaultdict(list)
    for table in parsed.get("tables", []):
        tables_by_doc[table["document_id"]].append(table)
    tables = [t for doc_id, items in tables_by_doc.items()
              for t in stitch_factor_tables(items, order, blocks[doc_id])]
    for table in tables:
        doc = documents.get(table["document_id"], {})
        if doc.get("kind") not in {"施工图", "勘察报告"} or table.get("review_status") == "failed" or not table.get("bbox"):
            continue
        material = extract_parameter_cells(table.get("rows", []))
        factors = extract_factor_cells(table.get("rows", []))
        if not material and not factors:
            continue
        route = infer_route_code_from_values(doc.get("file_name"), doc.get("title"))
        candidates = []
        nearby = []
        anchor = table.get("header_fragment", table)
        for block in blocks[table["document_id"]]:
            position = order(block)
            if position > order(anchor):
                continue
            text = str(block.get("text", ""))
            match = STATION_RE.search(text)
            # Ignore figure/table captions, prose, headers and route extent strings.
            if match and re.fullmatch(r"\s*\d+(?:\.\d+){1,3}\s*", text[:match.start()]):
                stations = list(dict.fromkeys(normalize_station_match(m) for m in STATION_RE.finditer(text)
                                             if normalize_station_match(m) in allowed[route]))
                if stations:
                    candidates.append((position, stations, block))
            if position[:2] == order(anchor)[:2] and 0 <= anchor["bbox"][1] - block["bbox"][3] < 110:
                nearby.append(block)
        nearest = max(candidates, key=lambda item: item[0]) if candidates else None
        # Do not drag an old section through a long run of unrelated appendices.
        station_candidates = nearest[1] if nearest and table["page"] - nearest[0][0] <= 3 else []
        station = station_candidates[0] if len(station_candidates) == 1 else None
        single_site = {s["station"] for s in registry if s.get("document_id") == table["document_id"]}
        single_site_association = station is None and not nearest and len(single_site) == 1
        if single_site_association:
            station = next(iter(single_site))
        captions = [b for b in nearby if re.match(r"\s*表\s*\d", b.get("text", ""))]
        caption = max(captions, key=order).get("text", "") if captions else ""
        context = " ".join(b["text"] for b in sorted(nearby, key=order))
        for factor in factors:
            if "整体" in caption:
                factor["analysis_scope"] = "整体边坡"
            elif "危岩" in caption or factor.get("body_id") or factor.get("failure_mode"):
                factor["analysis_scope"] = "危岩体"
                mode = re.search(r"(坠落式|滑移式|倾倒式)", caption)
                if mode:
                    factor.setdefault("failure_mode", mode.group(1))
        document_scope = station is None and not station_candidates and is_document_scope_parameter_table(context, caption, material, factors)
        issues = []
        if len(station_candidates) > 1:
            issues.append("shared_section_station_ambiguous")
        if station is None and not document_scope:
            issues.append("station_unresolved")
        title_match = STATION_RE.search(caption)
        if title_match and normalize_station_match(title_match) != station:
            issues.append("caption_station_mismatch")
        for factor in factors:
            # A nearby explicit current/natural-state assertion is a review signal,
            # not a substitute for the table or permission to change its label.
            statuses = re.findall(r"(?:当前|天然工况下?)(?:处于|为)?(不稳定|欠稳定|基本稳定|稳定)(?:状态)?", compact(context))
            if condition(factor["condition"]) == "天然" and factor.get("status") and any(s != factor["status"] for s in statuses):
                issues.append("possible_text_table_status_conflict")
        output.append({
            "id": table["id"], "document_id": table["document_id"], "source_file": doc.get("file_name"),
            "page": table["page"], "bbox": table["bbox"], "route_code": route, "station": station,
            "station_candidates": station_candidates,
            "source_fragments": table.get("source_fragments", []),
            "association_scope": "document" if document_scope else "slope" if station else "unresolved",
            "association_basis": (
                "document_level_recommended_parameters" if document_scope
                else "single_site_document" if single_site_association
                else "preceding_numbered_section" if station
                else "shared_numbered_section" if len(station_candidates) > 1 else "unresolved"
            ),
            "section_heading": nearest[2]["text"] if nearest else None,
            "section_heading_page": nearest[2]["page"] if nearest else None,
            "caption": caption, "context_text": context,
            "rows": table.get("rows", []), "material_parameters": material, "stability_results": factors,
            "quality_issues": sorted(set(issues)),
            "review_status": (
                "accepted_document_scope" if document_scope
                else "needs_review" if issues
                else "extracted_unreviewed"
            ),
            "temporal_scope": "historical_document_baseline", "current_status_known": False,
            "method": "engineering_table_headers_v1",
        })
    return output


def is_document_scope_parameter_table(
    context: str,
    caption: str,
    material_parameters: list[dict[str, Any]],
    stability_results: list[dict[str, Any]],
) -> bool:
    """Recognize explicitly regional/project-level recommended parameters.

    Such a table is valid source data but must not be attached to the nearest
    slope.  This classification is intentionally conservative: it requires
    material parameters, no computed stability result, a recommendation cue,
    and explicit project/area wording in the surrounding paragraph.
    """
    if not material_parameters or stability_results:
        return False
    text = compact(f"{context} {caption}")
    has_scope = any(term in text for term in ("项目区", "区内", "沿线", "区域经验"))
    has_recommendation = "推荐" in text and any(term in text for term in ("计算参数", "参数推荐", "推荐参数"))
    return has_scope and has_recommendation


def attach_engineering_records(slopes: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    indexed = {(s.get("route_code"), s["station"]): s for s in slopes}
    for record in records:
        slope = indexed.get((record["route_code"], record["station"]))
        if slope is None:
            continue
        slope.setdefault("engineering_records", []).append(record)
        for result in record["stability_results"]:
            matches = [s for s in slope.get("stability_scenarios", [])
                       if s.get("evidence_document_id") == record["document_id"]
                       and s.get("evidence_page") == record["page"]
                       and condition(s.get("condition")) == condition(result["condition"])
                       and s.get("safety_factor") == result["safety_factor"]
                       and not result.get("body_id") and not result.get("failure_mode")
                       and (not result.get("analysis_scope") or s.get("analysis_scope") == result["analysis_scope"])]
            # Existing prose results can be enriched, but never silently corrected.
            if matches:
                for match in matches:
                    match["table_evidence_id"] = record["id"]
                    if match.get("status") and result.get("status") and match["status"] != result["status"]:
                        record["quality_issues"] = sorted(set([*record["quality_issues"], "text_table_status_conflict"]))
                    if record["quality_issues"]:
                        match["quality_issues"] = record["quality_issues"]
                        match["eligible_for_screening"] = False
                    elif result.get("required_factor") is not None:
                        match["required_factor"] = result["required_factor"]
                continue
            slope.setdefault("stability_scenarios", []).append({
                **result, "condition": result["condition"],
                "analysis_scope": result.get("analysis_scope") or "报告计算断面（范围待核验）", "source_kind": "定量计算表格",
                "extraction_method": record["method"], "table_evidence_id": record["id"],
                "evidence_document_id": record["document_id"], "evidence_page": record["page"],
                "evidence_block_ids": [f["id"] for f in record.get("source_fragments", [])] or [record["id"]], "evidence_bbox": record["bbox"],
                "evidence_text": "\n".join(" | ".join(str(c or "") for c in row) for row in record["rows"]),
                "quality_issues": record["quality_issues"], "eligible_for_screening": False,
            })
        if record["quality_issues"]:
            record["review_status"] = "needs_review"
            for scenario in slope.get("stability_scenarios", []):
                if scenario.get("table_evidence_id") == record["id"]:
                    scenario["quality_issues"] = record["quality_issues"]
                    scenario["eligible_for_screening"] = False
