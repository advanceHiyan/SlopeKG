from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from .schema import SCHEMA_VERSION


def build_automatic_graph(
    parsed: dict[str, list[dict[str, Any]]],
    extracted: dict[str, Any],
    semantic_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    semantic_by_key = {
        (str(row.get("route_code") or ""), row["station"]): row
        for row in semantic_rows
        if row.get("validation_status") == "passed" and row.get("candidate")
    }
    semantic_by_station = {row["station"]: row for row in semantic_rows if row.get("validation_status") == "passed" and row.get("candidate")}
    documents = {row["id"]: row for row in parsed.get("documents", [])}
    source_items = {
        row["id"]: row
        for row in [*parsed.get("text_blocks", []), *parsed.get("tables", []), *parsed.get("ocr_results", [])]
    }
    slopes = extracted.get("slopes", [])
    default_route_code = infer_route_code(parsed, slopes)
    route_groups: dict[str, list[dict[str, Any]]] = {}
    for slope in slopes:
        route_groups.setdefault(str(slope.get("route_code") or default_route_code), []).append(slope)
    if not route_groups:
        route_groups[default_route_code] = []
    route_codes = sorted(route_groups)
    project_id = stable_id("project", "|".join(route_codes))
    project_label = f"{route_codes[0]} 边坡资料自动抽取项目" if len(route_codes) == 1 else "多路线边坡资料自动抽取项目"
    add_node(nodes, project_id, project_label, "Project", generation_basis="submitted_documents", route_codes=route_codes)
    route_ids: dict[str, str] = {}
    for route_code, route_slopes in route_groups.items():
        starts = [station_bounds(row["station"])[0] for row in route_slopes if station_bounds(row["station"])]
        ends = [station_bounds(row["station"])[1] for row in route_slopes if station_bounds(row["station"])]
        route_id = stable_id("route", f"{route_code}:{min(starts) if starts else 0}:{max(ends) if ends else 0}")
        route_ids[route_code] = route_id
        route_label = f"{route_code} K{format_station(min(starts))}-K{format_station(max(ends))}" if starts and ends else route_code
        add_node(
            nodes,
            route_id,
            route_label,
            "RouteSegment",
            route_code=route_code,
            start_station_m=min(starts) if starts else None,
            end_station_m=max(ends) if ends else None,
        )
        add_edge(edges, project_id, route_id, "HAS_ROUTE_SEGMENT")

    for document in documents.values():
        add_node(
            nodes,
            document["id"],
            document.get("title") or document.get("file_name", document["id"]),
            "Document",
            kind=document.get("kind"),
            file=document.get("file_name"),
            path=document.get("path"),
            pages=document.get("pages"),
            parser=document.get("parser"),
        )
        add_edge(edges, project_id, document["id"], "HAS_DOCUMENT")

    semantic_assertions: list[dict[str, Any]] = []
    for index, slope in enumerate(slopes, start=1):
        station = slope["station"]
        route_code = str(slope.get("route_code") or default_route_code)
        route_id = route_ids[route_code]
        start_m, end_m = station_bounds(station) or (None, None)
        slope_id = stable_id("slope", f"{route_code}:{station}")
        semantic_row = semantic_by_key.get((route_code, station)) or semantic_by_station.get(station, {})
        semantic = semantic_row.get("candidate", {})
        props = slope_props(slope, semantic, route_code, index, start_m, end_m)
        add_node(
            nodes,
            slope_id,
            f"{route_code} {station} {slope.get('side') or ''}边坡".strip(),
            "Slope",
            summary=semantic.get("mechanism_summary") or slope.get("source_disaster_label") or "自动抽取边坡",
            **props,
        )
        add_edge(edges, route_id, slope_id, "HAS_SLOPE")
        add_edge(edges, slope_id, project_id, "BELONGS_TO_PROJECT")

        evidence_ids, document_ids, source_evidence = add_source_evidence(evidence, station, slope, source_items, documents)
        for document_id in sorted(document_ids):
            document_evidence = next((item["id"] for item in evidence if item.get("document_id") == document_id and item["id"] in evidence_ids), None)
            add_edge(edges, slope_id, document_id, "RECORDED_IN", evidence=document_evidence)
        quote_evidence, semantic_evidence = add_quote_evidence(evidence, station, semantic_row, documents)
        all_evidence = list(dict.fromkeys([*evidence_ids, *quote_evidence]))

        lithologies = semantic.get("lithology_terms") or slope.get("lithology_terms", [])
        strata = semantic.get("stratum_terms") or slope.get("stratum_terms", [])
        for value in lithologies:
            node_id = stable_id("lithology", value)
            add_node(nodes, node_id, value, "Lithology", extraction_method="automatic_semantic" if semantic else "deterministic_dictionary")
            add_edge(edges, slope_id, node_id, "HAS_LITHOLOGY", evidence=field_evidence("lithology_terms", semantic_evidence, source_evidence, all_evidence))
        for value in strata:
            node_id = stable_id("stratum", value)
            add_node(nodes, node_id, value, "Stratum", extraction_method="automatic_semantic" if semantic else "deterministic_dictionary")
            add_edge(edges, slope_id, node_id, "HAS_STRATUM", evidence=field_evidence("stratum_terms", semantic_evidence, source_evidence, all_evidence))

        source_label = slope.get("source_disaster_label") or slope.get("source_alias", "")
        hazard_types = canonical_hazard_types(
            [*semantic.get("hazard_types", []), *semantic.get("failure_modes", []), *infer_hazard_types(source_label)]
        )
        body_type = semantic.get("hazard_body_type") or infer_hazard_body(source_label)
        if body_type or hazard_types:
            body_id = stable_id("hazard_body", station)
            add_node(nodes, body_id, f"{station} {body_type or '灾害体'}", "HazardBody", body_type=body_type, failure_modes=semantic.get("failure_modes", []))
            hazard_evidence = field_evidence("hazard_types", semantic_evidence, source_evidence, all_evidence)
            add_edge(edges, slope_id, body_id, "CONTAINS_HAZARD_BODY", evidence=hazard_evidence)
            assessment_id = stable_id("hazard_assessment", station)
            add_node(
                nodes,
                assessment_id,
                f"{station} 灾害类型自动识别",
                "HazardSusceptibilityAssessment",
                candidate_hazard_types=hazard_types,
                method="document_semantic_extraction",
                level="未进行易感性分级",
            )
            add_edge(edges, slope_id, assessment_id, "HAS_SUSCEPTIBILITY_ASSESSMENT", evidence=hazard_evidence)
            for hazard_type in hazard_types:
                type_id = stable_id("hazard_type", hazard_type)
                add_node(nodes, type_id, hazard_type, "HazardType")
                add_edge(edges, assessment_id, type_id, "FOR_HAZARD_TYPE", evidence=hazard_evidence)

        factors = semantic.get("causal_factors") or slope.get("causal_factor_terms", [])
        for factor in factors:
            factor_id = stable_id("factor", factor)
            add_node(nodes, factor_id, factor, "CausalFactor", extraction_method="automatic_semantic" if semantic else "deterministic_dictionary")
            add_edge(edges, slope_id, factor_id, "INFLUENCED_BY", evidence=field_evidence("causal_factors", semantic_evidence, source_evidence, all_evidence))

        for plane_index, plane in enumerate(semantic.get("structural_planes", []), start=1):
            plane_id = stable_id("plane", f"{station}:{plane_index}:{plane.get('name')}")
            add_node(nodes, plane_id, f"{station} {plane.get('name') or '结构面'}", "StructuralPlane", **plane)
            add_edge(edges, slope_id, plane_id, "DEVELOPS_STRUCTURAL_PLANE", evidence=field_evidence("structural_planes", semantic_evidence, source_evidence, all_evidence))

        for measure_index, measure in enumerate(slope.get("measures", []), start=1):
            type_id = stable_id("protection_type", measure)
            work_id = stable_id("protection_work", f"{station}:{measure_index}:{measure}")
            category = protection_category(measure)
            add_node(nodes, type_id, measure, "ProtectionType", category=category, subtype=measure)
            add_node(
                nodes,
                work_id,
                f"{station} {measure}（设计）",
                "ProtectionWork",
                category=category,
                subtype=measure,
                implementation_status="documented_design_current_status_unknown",
                current_condition="unknown",
            )
            add_edge(edges, slope_id, work_id, "HAS_PROTECTION_DESIGN", evidence=method_evidence(source_evidence, ["pdfplumber_treatment_table", "treatment_plan_table"], all_evidence))
            add_edge(edges, work_id, type_id, "INSTANCE_OF")

        for scenario_index, scenario in enumerate(slope.get("stability_scenarios", []), start=1):
            scenario_evidence = add_scenario_evidence(evidence, station, scenario, documents)
            analysis_id = stable_id("stability", f"{station}:survey:{scenario_index}:{scenario.get('condition')}:{scenario.get('safety_factor')}")
            add_node(
                nodes,
                analysis_id,
                f"{station} {scenario.get('condition')} Fs={scenario.get('safety_factor')}",
                "StabilityAnalysis",
                condition=scenario.get("condition"),
                fs=scenario.get("safety_factor"),
                required_fs=scenario.get("required_factor"),
                status=scenario.get("status"),
                analysis_scope=scenario.get("analysis_scope"),
                source_kind=scenario.get("source_kind"),
                value_source="deterministic_survey_sentence_extraction",
            )
            add_edge(
                edges,
                slope_id,
                analysis_id,
                "HAS_STABILITY_ANALYSIS",
                evidence=scenario_evidence or method_evidence(source_evidence, ["section_panel_dictionary"], all_evidence),
            )

        for fs_index, pair in enumerate(slope.get("safety_factor_pairs", []), start=1):
            analysis_id = stable_id("stability", f"{station}:treatment:{fs_index}")
            add_node(
                nodes,
                analysis_id,
                f"{station} 治理设计 Fs={pair.get('actual')}",
                "StabilityAnalysis",
                condition="治理设计工况",
                fs=pair.get("actual"),
                required_fs=pair.get("required"),
                status="满足要求" if pair.get("actual", 0) >= pair.get("required", 0) else "不满足要求",
                value_source="deterministic_table_extraction",
            )
            add_edge(edges, slope_id, analysis_id, "HAS_STABILITY_ANALYSIS", evidence=method_evidence(source_evidence, ["pdfplumber_treatment_table", "treatment_plan_table"], all_evidence))
        for conclusion_index, conclusion in enumerate(semantic.get("stability_conclusions", []), start=1):
            analysis_id = stable_id("stability", f"{station}:semantic:{conclusion_index}:{conclusion.get('condition')}")
            add_node(
                nodes,
                analysis_id,
                f"{station} {conclusion.get('condition') or '资料工况'} Fs={conclusion.get('safety_factor')}",
                "StabilityAnalysis",
                condition=conclusion.get("condition"),
                fs=conclusion.get("safety_factor"),
                required_fs=conclusion.get("required_factor"),
                status=conclusion.get("status"),
                analysis_scope=conclusion.get("analysis_scope"),
                source_kind=conclusion.get("source_kind"),
                value_source="validated_llm_extraction",
            )
            add_edge(edges, slope_id, analysis_id, "HAS_STABILITY_ANALYSIS", evidence=field_evidence("stability_conclusions", semantic_evidence, source_evidence, all_evidence))

        add_semantic_observations(nodes, edges, slope_id, station, semantic, semantic_evidence, source_evidence, all_evidence)

        semantic_assertions.extend(build_semantic_assertions(station, semantic_row, semantic_evidence, all_evidence))

    graph = {
        "meta": {
            "title": "公路边坡风险知识图谱",
            "description": "仅依据已提交文档自动发现、抽取、校验并构建；人工复核不是运行前置条件。",
            "schema_version": SCHEMA_VERSION,
            "generation_basis": "parsed_documents_only_no_manual_seed",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generated_from": [document.get("path") for document in documents.values()],
            "data_status": "自动抽取历史资料基线；动态监测和当前现场状态仍需外部系统接入。",
        },
        "nodes": list(nodes.values()),
        "edges": edges,
        "evidence": evidence,
        "source_records": [public_source_record(slope) for slope in slopes],
        "property_assertions": [*extracted.get("assertions", []), *semantic_assertions],
    }
    graph["meta"]["stats"] = graph_stats(graph)
    graph["meta"]["automatic_extraction"] = {
        "candidate_slopes": len(slopes),
        "deterministic_assertions": len(extracted.get("assertions", [])),
        "semantic_assertions": len(semantic_assertions),
        "semantic_candidates_passed": len(semantic_by_key),
        "manual_seed_used": False,
    }
    return graph


def public_source_record(slope: dict[str, Any]) -> dict[str, Any]:
    """Keep graph payloads interactive; full extraction context remains in extracted/*.json."""
    excluded = {
        "_field_sources",
        "section_text_samples",
        "section_evidence",
        "raw_text",
        "raw_row",
        "evidence_block_ids",
    }
    return {key: value for key, value in slope.items() if key not in excluded}


def slope_props(slope: dict[str, Any], semantic: dict[str, Any], route_code: str, index: int, start_m: int | None, end_m: int | None) -> dict[str, Any]:
    start_raw, end_raw = slope["station"].split("-", 1)
    return {
        "slope_id": f"{route_code}-AUTO-{index:03d}",
        "source_no": slope.get("no"),
        "source_alias": slope.get("source_alias") or slope["station"],
        "route_code_cache": route_code,
        "start_station_raw": start_raw,
        "end_station_raw": end_raw,
        "start_station_m": start_m,
        "end_station_m": end_m,
        "side": slope.get("side"),
        "slope_length_m": slope.get("slope_length_m"),
        "slope_height_min_m": slope.get("slope_height_min_m"),
        "slope_height_max_m": slope.get("slope_height_max_m"),
        "slope_height_raw": range_text(slope.get("slope_height_min_m"), slope.get("slope_height_max_m")),
        "slope_gradient_min_deg": slope.get("slope_gradient_min_deg"),
        "slope_gradient_max_deg": slope.get("slope_gradient_max_deg"),
        "slope_gradient_raw": range_text(slope.get("slope_gradient_min_deg"), slope.get("slope_gradient_max_deg"), "°"),
        "start_coordinate": slope.get("start_coordinate"),
        "end_coordinate": slope.get("end_coordinate"),
        "coordinate_crs": slope.get("coordinate_crs"),
        "slope_structure_code": semantic.get("slope_structure") or first(slope.get("slope_structure_terms", [])),
        "mechanism_summary": semantic.get("mechanism_summary"),
        "protection_rationale": semantic.get("protection_rationale"),
        "entity_resolution_status": "automatically_resolved_by_station_range",
        "review_status": "automatic_evidence_validated_not_human_reviewed",
        "data_status": "fully_automatic_document_extraction",
        "automatic_extraction": {
            "deterministic_confidence": slope.get("confidence"),
            "semantic_used": bool(semantic),
            "human_seed_used": False,
        },
    }


def add_source_evidence(
    evidence: list[dict[str, Any]],
    station: str,
    slope: dict[str, Any],
    source_items: dict[str, dict[str, Any]],
    documents: dict[str, dict[str, Any]],
) -> tuple[list[str], set[str], dict[str, list[str]]]:
    ids: list[str] = []
    document_ids: set[str] = set()
    by_method: dict[str, list[str]] = {}
    for source in slope.get("sources", []):
        block_id = source.get("block_id")
        item = source_items.get(block_id, {})
        document_id = item.get("document_id")
        if document_id:
            document_ids.add(document_id)
        evidence_id = stable_id("evidence", f"{station}:{block_id}:{source.get('method')}")
        if evidence_id not in ids:
            evidence.append(
                {
                    "id": evidence_id,
                    "source_file": documents.get(document_id, {}).get("file_name"),
                    "document_id": document_id,
                    "page": item.get("page") or source.get("page"),
                    "kind": source.get("method"),
                    "block_ids": [block_id] if block_id else [],
                    "bbox": item.get("bbox"),
                    "text": str(item.get("text") or item.get("raw_text") or "")[:1200],
                    "validation_status": "source_located" if item else "source_reference_only",
                }
            )
            ids.append(evidence_id)
            by_method.setdefault(str(source.get("method")), []).append(evidence_id)
    return ids, document_ids, by_method


def add_quote_evidence(
    evidence: list[dict[str, Any]], station: str, semantic_row: dict[str, Any], documents: dict[str, dict[str, Any]]
) -> tuple[list[str], dict[str, list[str]]]:
    ids = []
    by_field: dict[str, list[str]] = {}
    refs_by_page: dict[int, list[str]] = {}
    for ref in semantic_row.get("source_refs", []):
        if ref.get("document_id"):
            refs_by_page.setdefault(int(ref["page"]), []).append(ref["document_id"])
    for index, quote in enumerate(semantic_row.get("candidate", {}).get("evidence_quotes", []), start=1):
        document_id = first(refs_by_page.get(int(quote.get("page") or 0), []))
        evidence_id = stable_id("semantic_evidence", f"{station}:{quote.get('page')}:{quote.get('quote')}")
        evidence.append(
            {
                "id": evidence_id,
                "source_file": documents.get(document_id, {}).get("file_name"),
                "document_id": document_id,
                "page": quote.get("page"),
                "kind": "validated_llm_quote",
                "text": quote.get("quote"),
                "validation_status": "exact_quote_verified",
                "model": semantic_row.get("model"),
                "prompt_version": semantic_row.get("prompt_version"),
                "supports": quote.get("supports", []),
            }
        )
        ids.append(evidence_id)
        for field in quote.get("supports", []):
            by_field.setdefault(str(field), []).append(evidence_id)
    return ids, by_field


def add_scenario_evidence(
    evidence: list[dict[str, Any]],
    station: str,
    scenario: dict[str, Any],
    documents: dict[str, dict[str, Any]],
) -> str | None:
    page = scenario.get("evidence_page")
    text = scenario.get("evidence_text")
    if page is None or not text:
        return None
    document_id = scenario.get("evidence_document_id")
    evidence_id = stable_id("stability_evidence", f"{station}:{page}:{text}")
    if not any(item["id"] == evidence_id for item in evidence):
        evidence.append(
            {
                "id": evidence_id,
                "source_file": documents.get(document_id, {}).get("file_name"),
                "document_id": document_id,
                "page": page,
                "kind": "deterministic_stability_sentence",
                "block_ids": scenario.get("evidence_block_ids", []),
                "text": text,
                "validation_status": "regex_value_and_condition_matched",
                "supports": ["stability_scenarios"],
            }
        )
    return evidence_id


def build_semantic_assertions(
    station: str,
    semantic_row: dict[str, Any],
    evidence_by_field: dict[str, list[str]],
    fallback_evidence: list[str],
) -> list[dict[str, Any]]:
    candidate = semantic_row.get("candidate", {})
    skip = {"evidence_quotes", "uncertainties"}
    output = []
    for key, value in candidate.items():
        if key in skip or value in (None, "", [], {}):
            continue
        output.append(
            {
                "id": stable_id("semantic_assertion", f"{station}:{key}"),
                "subject_key": station,
                "property_code": key,
                "value": value,
                "confidence": semantic_row.get("validation", {}).get("evidence_quote_precision"),
                "evidence": evidence_by_field.get(key, fallback_evidence[:1]),
                "extraction_method": "validated_llm_semantic",
                "model": semantic_row.get("model"),
                "prompt_version": semantic_row.get("prompt_version"),
                "review_status": "automatic_evidence_validated_not_human_reviewed",
            }
        )
    return output


def add_semantic_observations(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    slope_id: str,
    station: str,
    semantic: dict[str, Any],
    semantic_evidence: dict[str, list[str]],
    source_evidence: dict[str, list[str]],
    fallback: list[str],
) -> None:
    geomorphology = semantic.get("geomorphology")
    if geomorphology:
        node_id = stable_id("terrain", f"{station}:{geomorphology}")
        add_node(nodes, node_id, f"{station} 地形地貌", "TerrainSetting", description=geomorphology)
        add_edge(edges, slope_id, node_id, "HAS_TERRAIN_SETTING", evidence=field_evidence("geomorphology", semantic_evidence, source_evidence, fallback))

    for index, item in enumerate(semantic.get("deformation_observations", []), start=1):
        node_id = stable_id("deformation", f"{station}:{index}:{json.dumps(item, ensure_ascii=False, sort_keys=True)}")
        add_node(nodes, node_id, f"{station} {item.get('type') or '变形现象'}", "DeformationObservation", **item)
        add_edge(edges, slope_id, node_id, "HAS_DEFORMATION_OBSERVATION", evidence=field_evidence("deformation_observations", semantic_evidence, source_evidence, fallback))

    for index, item in enumerate(semantic.get("hydrology_observations", []), start=1):
        node_id = stable_id("hydrology", f"{station}:{index}:{json.dumps(item, ensure_ascii=False, sort_keys=True)}")
        add_node(nodes, node_id, f"{station} {item.get('type') or '水文现象'}", "HydrologyObservation", **item)
        add_edge(edges, slope_id, node_id, "HAS_HYDROLOGY_OBSERVATION", evidence=field_evidence("hydrology_observations", semantic_evidence, source_evidence, fallback))

    for index, item in enumerate(semantic.get("protection_records", []), start=1):
        node_id = stable_id("protection_record", f"{station}:{index}:{json.dumps(item, ensure_ascii=False, sort_keys=True)}")
        add_node(nodes, node_id, f"{station} {item.get('measure') or '防护记录'}", "ProtectionWork", **item, extraction_method="validated_llm_extraction")
        relation = "HAS_EXISTING_PROTECTION" if item.get("status") in {"existing", "constructed", "damaged"} else "HAS_PROTECTION_DESIGN"
        add_edge(edges, slope_id, node_id, relation, evidence=field_evidence("protection_records", semantic_evidence, source_evidence, fallback))


def field_evidence(
    field: str,
    semantic_evidence: dict[str, list[str]],
    source_evidence: dict[str, list[str]],
    fallback: list[str],
) -> str | None:
    if semantic_evidence.get(field):
        return semantic_evidence[field][0]
    deterministic_methods = {
        "lithology_terms": ["section_panel_dictionary"],
        "stratum_terms": ["section_panel_dictionary"],
        "causal_factors": ["section_panel_dictionary"],
        "hazard_types": ["slope_registry_table"],
    }
    return method_evidence(source_evidence, deterministic_methods.get(field, []), fallback)


def method_evidence(source_evidence: dict[str, list[str]], methods: list[str], fallback: list[str]) -> str | None:
    for method in methods:
        if source_evidence.get(method):
            return source_evidence[method][0]
    return first(fallback)


def write_graphml(path: Path, graph: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '<key id="label" for="all" attr.name="label" attr.type="string"/>',
        '<key id="type" for="node" attr.name="type" attr.type="string"/>',
        '<key id="relation" for="edge" attr.name="relation" attr.type="string"/>',
        '<graph id="SlopeKG" edgedefault="directed">',
    ]
    for node in graph["nodes"]:
        lines.extend(
            [
                f'  <node id="{escape(node["id"])}">',
                f'    <data key="label">{escape(str(node["label"]))}</data>',
                f'    <data key="type">{escape(node["type"])}</data>',
                "  </node>",
            ]
        )
    for edge in graph["edges"]:
        lines.extend(
            [
                f'  <edge id="{escape(edge["id"])}" source="{escape(edge["source"])}" target="{escape(edge["target"])}">',
                f'    <data key="relation">{escape(edge["relation"])}</data>',
                "  </edge>",
            ]
        )
    lines.extend(["</graph>", "</graphml>"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def graph_stats(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "evidence": len(graph["evidence"]),
        "node_type_counts": dict(Counter(node["type"] for node in graph["nodes"])),
        "edge_type_counts": dict(Counter(edge["relation"] for edge in graph["edges"])),
    }


def add_node(nodes: dict[str, dict[str, Any]], node_id: str, label: str, node_type: str, summary: str = "", **props: Any) -> None:
    if node_id not in nodes:
        nodes[node_id] = {"id": node_id, "label": label, "type": node_type, "summary": summary, "props": props}


def add_edge(edges: list[dict[str, Any]], source: str, target: str, relation: str, **props: Any) -> None:
    signature = (source, target, relation, json.dumps(props, ensure_ascii=False, sort_keys=True, default=str))
    if any((row["source"], row["target"], row["relation"], json.dumps(row["props"], ensure_ascii=False, sort_keys=True, default=str)) == signature for row in edges):
        return
    edges.append({"id": f"e{len(edges)+1:04d}", "source": source, "target": target, "relation": relation, "props": props})


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def infer_route_code(parsed: dict[str, list[dict[str, Any]]], slopes: list[dict[str, Any]]) -> str:
    haystack = " ".join([*(doc.get("file_name", "") for doc in parsed.get("documents", [])), *(slope.get("source_alias", "") for slope in slopes)])
    match = re.search(r"\b([GSXY]\d{2,4})\b", haystack, re.IGNORECASE)
    return match.group(1).upper() if match else "ROUTE"


def station_bounds(station: str) -> tuple[int, int] | None:
    match = re.match(r"K(\d+)\+(\d+)-K(\d+)\+(\d+)", station)
    if not match:
        return None
    return int(match.group(1)) * 1000 + int(match.group(2)), int(match.group(3)) * 1000 + int(match.group(4))


def format_station(value: int) -> str:
    return f"{value // 1000}+{value % 1000:03d}"


def infer_hazard_types(text: str) -> list[str]:
    mapping = {"崩塌": "崩塌", "落石": "崩塌", "滑坡": "滑坡", "泥石流": "泥石流"}
    return list(dict.fromkeys(label for word, label in mapping.items() if word in text))


def canonical_hazard_types(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if "崩塌" in value or "落石" in value:
            result.append("崩塌")
        elif "滑坡" in value:
            result.append("滑坡")
        elif "泥石流" in value:
            result.append("泥石流")
    return list(dict.fromkeys(result))


def infer_hazard_body(text: str) -> str | None:
    for value in ["危岩体", "潜在不稳定边坡", "不稳定边坡", "滑坡体", "崩塌体", "路基沉降", "路基下沉", "路基垮塌"]:
        if value in text:
            return value
    return None


def protection_category(measure: str) -> str:
    if any(word in measure for word in ["护岸", "河", "挡水"]):
        return "沿河防护"
    if any(word in measure for word in ["挡墙", "抗滑桩", "锚杆", "框架"]):
        return "支挡设施"
    return "坡面防护"


def range_text(minimum: Any, maximum: Any, suffix: str = "m") -> str | None:
    if minimum is None and maximum is None:
        return None
    return f"{minimum}{suffix}" if minimum == maximum else f"{minimum}-{maximum}{suffix}"


def first(values: Any) -> Any:
    return values[0] if isinstance(values, list) and values else None
