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
    value = compact(value)
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


def extract_factor_cells(rows: list[list[Any]]) -> list[dict[str, Any]]:
    cells = [[compact(cell) for cell in row] for row in rows]
    if not cells:
        return []
    header = cells[0]
    actual_col = next((i for i, cell in enumerate(header) if cell in {"稳定性系数", "稳定系数", "Fs", "FS", "危岩稳定性系数F"}), None)
    if actual_col is None:
        return []  # A safety threshold alone is not a computed result.
    state_col = next((i for i, cell in enumerate(header) if cell == "工况"), None)
    required_col = next((i for i, cell in enumerate(header) if cell in {"安全系数", "设计安全系数", "要求安全系数"}), None)
    status_col = next((i for i, cell in enumerate(header) if cell in {"稳定性评价", "稳定状态"}), None)
    output = []
    if state_col is not None:
        section = None
        for row_index, row in enumerate(cells[1:], 1):
            if len(row) != len(header):
                continue
            if state_col > 0 and row[0]:
                section = row[0]
            state, value = condition(row[state_col]), number(row[actual_col])
            if state and value is not None and 0 < value < 100:
                subject_role = "failure_mode" if header[0] == "破坏模式" else "body_id" if header[0] == "危岩编号" else "section"
                output.append(dict(condition=row[state_col], safety_factor=value,
                                   required_factor=number(row[required_col]) if required_col is not None else None,
                                   status=row[status_col] or None if status_col is not None else None,
                                   **{subject_role: section}, row_index=row_index, column_index=actual_col))
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
    for table in parsed.get("tables", []):
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
        for block in blocks[table["document_id"]]:
            position = order(block)
            if position > order(table):
                continue
            text = str(block.get("text", ""))
            match = STATION_RE.search(text)
            # Ignore figure/table captions, prose, headers and route extent strings.
            if match and re.fullmatch(r"\s*\d+(?:\.\d+){1,3}\s*", text[:match.start()]):
                station = normalize_station_match(match)
                if station in allowed[route]:
                    candidates.append((position, station, block))
            if position[:2] == order(table)[:2] and 0 <= table["bbox"][1] - block["bbox"][3] < 110:
                nearby.append(block)
        nearest = max(candidates, key=lambda item: item[0]) if candidates else None
        # Do not drag an old section through a long run of unrelated appendices.
        station = nearest[1] if nearest and table["page"] - nearest[0][0] <= 3 else None
        captions = [b for b in nearby if re.match(r"\s*表\s*\d", b.get("text", ""))]
        caption = max(captions, key=order).get("text", "") if captions else ""
        context = " ".join(b["text"] for b in sorted(nearby, key=order))
        document_scope = station is None and is_document_scope_parameter_table(context, caption, material, factors)
        issues = []
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
            "association_scope": "document" if document_scope else "slope" if station else "unresolved",
            "association_basis": (
                "document_level_recommended_parameters" if document_scope
                else "preceding_numbered_section" if station
                else "unresolved"
            ),
            "section_heading": nearest[2]["text"] if station else None,
            "section_heading_page": nearest[2]["page"] if station else None,
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
                       and not result.get("body_id") and not result.get("failure_mode")]
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
                "analysis_scope": "报告计算断面（范围待核验）", "source_kind": "定量计算表格",
                "extraction_method": record["method"], "table_evidence_id": record["id"],
                "evidence_document_id": record["document_id"], "evidence_page": record["page"],
                "evidence_block_ids": [record["id"]], "evidence_bbox": record["bbox"],
                "evidence_text": "\n".join(" | ".join(str(c or "") for c in row) for row in record["rows"]),
                "quality_issues": record["quality_issues"], "eligible_for_screening": False,
            })
        if record["quality_issues"]:
            record["review_status"] = "needs_review"
            for scenario in slope.get("stability_scenarios", []):
                if scenario.get("table_evidence_id") == record["id"]:
                    scenario["quality_issues"] = record["quality_issues"]
                    scenario["eligible_for_screening"] = False
