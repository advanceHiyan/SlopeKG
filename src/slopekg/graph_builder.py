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

        structural_planes = unique_object_rows(
            [*slope.get("structural_planes", []), *semantic.get("structural_planes", [])]
        )
        for plane_index, plane in enumerate(structural_planes, start=1):
            plane_id = stable_id("plane", f"{station}:{plane_index}:{plane.get('name')}")
            plane_props = {
                **plane,
                "extraction_method": "validated_llm_extraction" if plane in semantic.get("structural_planes", []) else "deterministic_section_extraction",
            }
            add_node(
                nodes,
                plane_id,
                f"{station} {plane.get('name') or '结构面'}",
                "StructuralPlane",
                **plane_props,
            )
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

        for record in slope.get("engineering_records", []):
            if not record.get("material_parameters"):
                continue
            parameter_id = stable_id("material_parameters", record["id"])
            parameter_evidence = add_scenario_evidence(evidence, station, {
                "evidence_document_id": record["document_id"], "evidence_page": record["page"],
                "evidence_text": "\n".join(" | ".join(str(c or "") for c in row) for row in record["rows"]),
                "evidence_block_ids": [record["id"]], "evidence_bbox": record["bbox"],
                "extraction_method": record["method"], "supports": ["material_parameters"],
            }, documents)
            add_node(nodes, parameter_id, f"{station} 材料参数（第{record['page']}页）", "MaterialParameterSet",
                     parameters=record["material_parameters"], source_file=record["source_file"],
                     source_page=record["page"], quality_issues=record["quality_issues"],
                     review_status=record["review_status"], temporal_scope="historical_document_baseline",
                     current_status_known=False, applicability="报告计算参数，适用断面与工况需核验")
            add_edge(edges, slope_id, parameter_id, "HAS_MATERIAL_PARAMETERS", evidence=parameter_evidence)

        stability_keys: set[tuple[Any, ...]] = set()
        for scenario_index, scenario in enumerate(unique_stability_scenarios(slope.get("stability_scenarios", [])), start=1):
            stability_keys.add(stability_scenario_key(scenario))
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
                value_source=scenario.get("extraction_method", "deterministic_survey_sentence_extraction"),
                section=scenario.get("section"),
                body_id=scenario.get("body_id"),
                failure_mode=scenario.get("failure_mode"),
                table_evidence_id=scenario.get("table_evidence_id"),
                quality_issues=scenario.get("quality_issues", []),
                eligible_for_screening=scenario.get("eligible_for_screening", True),
            )
            add_edge(
                edges,
                slope_id,
                analysis_id,
                "HAS_STABILITY_ANALYSIS",
                evidence=scenario_evidence or method_evidence(source_evidence, ["section_panel_dictionary"], all_evidence),
            )

        for fs_index, pair in enumerate(slope.get("safety_factor_pairs", []), start=1):
            stability_keys.add(stability_scenario_key({
                "condition": "治理设计工况",
                "safety_factor": pair.get("actual"),
                "required_factor": pair.get("required"),
                "status": "满足要求" if pair.get("actual", 0) >= pair.get("required", 0) else "不满足要求",
                "analysis_scope": None,
            }))
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
            table_issues = sorted({issue
                for record in slope.get("engineering_records", [])
                if record.get("page") == conclusion.get("evidence_page")
                for result in record.get("stability_results", [])
                if result.get("safety_factor") == conclusion.get("safety_factor")
                for issue in record.get("quality_issues", [])})
            conclusion_key = stability_scenario_key(conclusion)
            if conclusion_key in stability_keys:
                continue
            stability_keys.add(conclusion_key)
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
                quality_issues=table_issues,
                eligible_for_screening=not table_issues,
            )
            add_edge(
                edges,
                slope_id,
                analysis_id,
                "HAS_STABILITY_ANALYSIS",
                evidence=matching_stability_evidence(conclusion, semantic_evidence.get("stability_conclusions", []), evidence),
            )

        observation_payload = {
            **semantic,
            "geomorphology": semantic.get("geomorphology") or slope.get("geomorphology"),
            "deformation_observations": unique_object_rows(
                [*slope.get("deformation_observations", []), *semantic.get("deformation_observations", [])]
            ),
            "hydrology_observations": unique_object_rows(
                [*slope.get("hydrology_observations", []), *semantic.get("hydrology_observations", [])]
            ),
        }
        add_semantic_observations(nodes, edges, slope_id, station, observation_payload, semantic_evidence, source_evidence, all_evidence)
        vegetation = semantic.get("vegetation_condition") or slope.get("vegetation_condition")
        if vegetation:
            vegetation_id = stable_id("vegetation", f"{station}:{json.dumps(vegetation, ensure_ascii=False, sort_keys=True)}")
            vegetation_props = vegetation if isinstance(vegetation, dict) else {"density": vegetation}
            add_node(nodes, vegetation_id, f"{station} 植被调查", "VegetationSurvey", **vegetation_props)
            add_edge(
                edges,
                slope_id,
                vegetation_id,
                "HAS_VEGETATION_SURVEY",
                evidence=field_evidence("vegetation_condition", semantic_evidence, source_evidence, all_evidence),
            )

        semantic_assertions.extend(build_semantic_assertions(station, semantic_row, semantic_evidence, all_evidence))

    # Project/route-level recommended parameters are source data, not missing
    # slope associations. Keep them attached to their document and never let
    # them silently leak into a nearby slope or risk-screening calculation.
    document_scope_records = []
    for record in extracted.get("engineering_records", []):
        if record.get("association_scope") != "document" or not record.get("material_parameters"):
            continue
        document_id = record.get("document_id")
        parameter_id = stable_id("material_parameters", record["id"])
        evidence_id = add_scenario_evidence(evidence, f"document:{document_id}", {
            "evidence_document_id": document_id,
            "evidence_page": record.get("page"),
            "evidence_text": "\n".join(" | ".join(str(c or "") for c in row) for row in record.get("rows", [])),
            "evidence_block_ids": [record["id"]],
            "evidence_bbox": record.get("bbox"),
            "extraction_method": record.get("method"),
            "supports": ["document_scope_material_parameters"],
        }, documents)
        add_node(
            nodes,
            parameter_id,
            f"{record.get('route_code') or '文档'} 通用材料参数（第{record.get('page')}页）",
            "MaterialParameterSet",
            parameters=record["material_parameters"],
            source_file=record.get("source_file"),
            source_page=record.get("page"),
            association_scope="document",
            applicability="报告项目区推荐参数，未自动分配到具体边坡",
            quality_issues=record.get("quality_issues", []),
            review_status=record.get("review_status"),
            eligible_for_screening=False,
        )
        if document_id in documents:
            add_edge(edges, document_id, parameter_id, "HAS_MATERIAL_PARAMETERS", evidence=evidence_id)
        document_scope_records.append({
            "id": record["id"],
            "document_id": document_id,
            "page": record.get("page"),
            "association_scope": "document",
            "review_status": record.get("review_status"),
        })

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
        "document_scope_records": document_scope_records,
        "source_issues": [
            {
                "kind": "registry_station_issue",
                "route_code": row.get("route_code"),
                "station": row.get("station"),
                "source_file": documents.get(row.get("document_id"), {}).get("file_name"),
                "page": row.get("page"),
                "evidence_block_id": row.get("evidence_block_id"),
                "quality_issues": row.get("quality_issues", []),
                "resolution": "retained_as_source_issue_not_created_as_slope",
            }
            for row in extracted.get("registry_issues", [])
        ] + [
            {
                "kind": "engineering_table_issue",
                "route_code": row.get("route_code"),
                "station": row.get("station"),
                "source_file": row.get("source_file"),
                "page": row.get("page"),
                "evidence_block_id": row.get("id"),
                "quality_issues": row.get("quality_issues", []),
                "resolution": "excluded_from_screening_pending_domain_confirmation",
            }
            for row in extracted.get("engineering_records", [])
            if row.get("quality_issues")
        ],
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
    slope_length = slope.get("slope_length_m") or semantic.get("slope_length_m")
    height_min = slope.get("slope_height_min_m") or semantic.get("slope_height_min_m")
    height_max = slope.get("slope_height_max_m") or semantic.get("slope_height_max_m")
    gradient_min = slope.get("slope_gradient_min_deg") or semantic.get("slope_gradient_min_deg")
    gradient_max = slope.get("slope_gradient_max_deg") or semantic.get("slope_gradient_max_deg")
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
        "slope_length_m": slope_length,
        "slope_height_min_m": height_min,
        "slope_height_max_m": height_max,
        "slope_height_raw": range_text(height_min, height_max),
        "slope_gradient_min_deg": gradient_min,
        "slope_gradient_max_deg": gradient_max,
        "slope_gradient_raw": range_text(gradient_min, gradient_max, "°"),
        "slope_type": semantic.get("slope_type") or slope.get("slope_type"),
        "material_nature": semantic.get("material_nature") or slope.get("material_nature"),
        "slope_aspect_deg": semantic.get("slope_aspect_deg") or slope.get("slope_aspect_deg"),
        "start_coordinate": slope.get("start_coordinate"),
        "end_coordinate": slope.get("end_coordinate"),
        "coordinate_crs": slope.get("coordinate_crs"),
        "control_points": slope.get("control_points", []),
        "coordinate_role": slope.get("coordinate_role"),
        "slope_structure_code": semantic.get("slope_structure") or slope.get("slope_structure_code") or first(slope.get("slope_structure_terms", [])),
        "river_relation": semantic.get("river_relation") or slope.get("river_relation"),
        "vegetation_condition": semantic.get("vegetation_condition") or slope.get("vegetation_condition"),
        "overall_slope_deformation_severity": semantic.get("overall_slope_deformation_severity") or slope.get("overall_slope_deformation_severity"),
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
    evidence_id = stable_id("stability_evidence", f"{document_id}:{station}:{page}:{text}")
    if not any(item["id"] == evidence_id for item in evidence):
        evidence.append(
            {
                "id": evidence_id,
                "source_file": documents.get(document_id, {}).get("file_name"),
                "document_id": document_id,
                "page": page,
                "kind": scenario.get("extraction_method", "deterministic_stability_sentence"),
                "bbox": scenario.get("evidence_bbox"),
                "block_ids": scenario.get("evidence_block_ids", []),
                "text": text,
                "validation_status": "table_header_and_cell_matched" if scenario.get("extraction_method") else "regex_value_and_condition_matched",
                "supports": scenario.get("supports", ["stability_scenarios"]),
            }
        )
    return evidence_id


def unique_stability_scenarios(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    seen = set()
    for row in rows:
        key = (
            row.get("condition"),
            row.get("safety_factor"),
            row.get("required_factor"),
            row.get("status"),
            row.get("analysis_scope"),
            row.get("section"), row.get("body_id"), row.get("failure_mode"),
            row.get("table_evidence_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def stability_scenario_key(row: dict[str, Any]) -> tuple[Any, ...]:
    condition = str(row.get("condition") or "").replace(" ", "")
    condition = {
        "天然状态": "天然", "天然工况": "天然",
        "饱水": "饱和", "饱水状态": "饱和", "饱和状态": "饱和",
    }.get(condition, condition)
    factor = row.get("safety_factor", row.get("fs"))
    required = row.get("required_factor", row.get("required_fs"))
    return (condition, factor, required, row.get("status"), row.get("analysis_scope"))


def matching_stability_evidence(
    scenario: dict[str, Any],
    evidence_ids: list[str],
    evidence: list[dict[str, Any]],
) -> str | None:
    """Bind a semantic stability result only to a quote containing its own condition and value."""
    candidates = {item.get("id"): item for item in evidence if item.get("id") in evidence_ids}
    factor = scenario.get("safety_factor")
    status = str(scenario.get("status") or "")
    condition = stability_condition_terms(scenario.get("condition"))
    for evidence_id in evidence_ids:
        text = str(candidates.get(evidence_id, {}).get("text") or "").replace(" ", "")
        if not text:
            continue
        if factor is not None:
            numbers = [float(token) for token in re.findall(r"\d+(?:\.\d+)?", text)]
            if not any(abs(number - float(factor)) < 1e-9 for number in numbers):
                continue
        if status and status not in text:
            continue
        if condition and not any(term in text for term in condition):
            continue
        return evidence_id
    return None


def stability_condition_terms(value: Any) -> list[str]:
    condition = str(value or "").replace(" ", "")
    if condition in {"天然", "天然状态", "天然工况"}:
        return ["天然"]
    if condition in {"饱和", "饱和状态", "饱水", "饱水状态"}:
        return ["饱和", "饱水"]
    if "暴雨" in condition:
        return ["暴雨"]
    if "现状" in condition or "现在" in condition:
        return ["现状", "现在"]
    if "治理" in condition or "设计" in condition:
        return ["治理", "设计"]
    return [condition] if condition else []


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
        normalized = {
            "temporal_scope": "historical_document_baseline",
            "current_status_known": False,
            **item,
        }
        add_node(nodes, node_id, f"{station} {item.get('type') or '变形现象'}", "DeformationObservation", **normalized)
        add_edge(edges, slope_id, node_id, "HAS_DEFORMATION_OBSERVATION", evidence=field_evidence("deformation_observations", semantic_evidence, source_evidence, fallback))

    for index, item in enumerate(semantic.get("hydrology_observations", []), start=1):
        node_id = stable_id("hydrology", f"{station}:{index}:{json.dumps(item, ensure_ascii=False, sort_keys=True)}")
        normalized = {
            "temporal_scope": "historical_document_baseline",
            "current_status_known": False,
            **item,
        }
        add_node(nodes, node_id, f"{station} {item.get('type') or '水文现象'}", "HydrologyObservation", **normalized)
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
        "structural_planes": ["section_panel_dictionary"],
        "deformation_observations": ["section_panel_dictionary"],
        "hydrology_observations": ["section_panel_dictionary"],
        "vegetation_condition": ["section_panel_dictionary"],
        "hazard_types": ["slope_registry_table"],
    }
    return method_evidence(source_evidence, deterministic_methods.get(field, []), fallback)


def method_evidence(source_evidence: dict[str, list[str]], methods: list[str], fallback: list[str]) -> str | None:
    for method in methods:
        if source_evidence.get(method):
            return source_evidence[method][0]
    return first(fallback)


def unique_object_rows(rows: list[Any]) -> list[dict[str, Any]]:
    output = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        signature = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if signature in seen:
            continue
        seen.add(signature)
        output.append(row)
    return output


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


def attach_visual_assets(graph: dict[str, Any], catalog: dict[str, Any]) -> None:
    """Replace catalog-owned visual nodes; attach only verified provenance.

    A document association never implies a slope association. Even an explicit
    slope ID needs an approved review before it becomes a graph edge.
    """
    previous = {n["id"] for n in graph["nodes"] if n.get("props", {}).get("asset_catalog_member")}
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in previous]
    graph["edges"] = [e for e in graph["edges"] if e["source"] not in previous and e["target"] not in previous]
    nodes = {n["id"]: n for n in graph["nodes"]}
    counts = Counter(str(a.get("id")) for a in catalog.get("assets", []))
    attached = 0
    for asset in catalog.get("assets", []):
        asset_id = asset.get("id")
        document_id = asset.get("source_document_id")
        page = asset.get("source_page")
        if (asset.get("asset_type") != "VisualAsset" or asset.get("validation_status") != "valid"
                or counts[str(asset_id)] != 1 or asset_id in nodes
                or nodes.get(document_id, {}).get("type") != "Document"
                or type(page) is not int or page < 1):
            continue
        props = {key: asset.get(key) for key in (
            "href", "sha256", "media_type", "subtype", "source_document_id", "source_page", "origin",
        )}
        props.update(asset_catalog_member=True, association_scope="document", slope_review_status=asset.get("slope_review_status", "unreviewed"))
        add_node(nodes, asset_id, asset.get("file_name", asset_id), "VisualAsset", **props)
        # Stable edge IDs do not depend on catalog order or the existing edge count.
        def edge(source: str, target: str, relation: str) -> None:
            graph["edges"].append({"id": stable_id("asset_edge", f"{source}:{relation}:{target}"),
                                   "source": source, "target": target, "relation": relation,
                                   "props": {"source_page": page, "source_document_id": document_id}})
        edge(document_id, asset_id, "HAS_VISUAL_ASSET")
        edge(asset_id, document_id, "DERIVED_FROM")
        slope_id = asset.get("slope_id")
        if asset.get("slope_review_status") == "approved" and nodes.get(slope_id, {}).get("type") == "Slope":
            edge(slope_id, asset_id, "HAS_VISUAL_ASSET")
            nodes[asset_id]["props"].update(slope_id=slope_id, association_scope="reviewed_slope")
        attached += 1
    graph["nodes"] = list(nodes.values())
    graph["meta"]["multimodal"] = {"catalog_assets": catalog.get("count", 0), "visual_assets_in_graph": attached,
                                    "generated_at": catalog.get("generated_at")}
    graph["meta"]["stats"] = graph_stats(graph)


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
