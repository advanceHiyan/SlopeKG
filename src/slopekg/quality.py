from __future__ import annotations

import re
from datetime import datetime
from typing import Any


STATION_PATTERN = re.compile(r"^K\d+\+\d{3}-K\d+\+\d{3}$")


def evaluate_automatic_pipeline(
    extracted: dict[str, Any],
    semantic_summary: dict[str, Any],
    graph: dict[str, Any],
) -> dict[str, Any]:
    slopes = extracted.get("slopes", [])
    total = len(slopes)
    stations = [row.get("station") for row in slopes]
    valid_stations = sum(bool(STATION_PATTERN.match(str(value or ""))) for value in stations)
    unique_stations = len(set(stations))
    slope_keys = [(str(row.get("route_code") or "ROUTE"), row.get("station")) for row in slopes]
    unique_slope_keys = len(set(slope_keys))
    multi_source = sum(len(row.get("sources", [])) >= 2 for row in slopes)
    assertions = graph.get("property_assertions", [])
    with_evidence = sum(bool(row.get("evidence")) for row in assertions)
    node_ids = [row["id"] for row in graph.get("nodes", [])]
    node_set = set(node_ids)
    graph_slopes = [node for node in graph.get("nodes", []) if node.get("type") == "Slope"]
    relations_by_source: dict[str, set[str]] = {}
    targets_by_source_relation: dict[tuple[str, str], list[dict[str, Any]]] = {}
    graph_nodes_by_id = {node.get("id"): node for node in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        relations_by_source.setdefault(edge.get("source"), set()).add(edge.get("relation"))
        target = graph_nodes_by_id.get(edge.get("target"))
        if target:
            targets_by_source_relation.setdefault((edge.get("source"), edge.get("relation")), []).append(target)
    dangling_edges = sum(edge.get("source") not in node_set or edge.get("target") not in node_set for edge in graph.get("edges", []))
    incoming_or_outgoing = {value for edge in graph.get("edges", []) for value in [edge.get("source"), edge.get("target")]}
    orphan_nodes = sum(node["type"] not in {"Project"} and node["id"] not in incoming_or_outgoing for node in graph.get("nodes", []))
    historical_observations = [
        node for node in graph.get("nodes", [])
        if node.get("type") in {"DeformationObservation", "HydrologyObservation"}
    ]
    observations_with_temporal_boundary = sum(
        node.get("props", {}).get("temporal_scope") not in (None, "")
        and node.get("props", {}).get("current_status_known") is not None
        for node in historical_observations
    )
    coverage = {
        "registry": field_coverage(slopes, "source_alias"),
        "coordinates": field_coverage(slopes, "start_coordinate"),
        "treatment": field_coverage(slopes, "safety_factor_pairs"),
        "geometry": field_coverage(slopes, "slope_height_min_m"),
        "slope_type": field_coverage(slopes, "slope_type"),
        "material_nature": field_coverage(slopes, "material_nature"),
        "slope_aspect": field_coverage(slopes, "slope_aspect_deg"),
        "slope_structure": field_coverage(slopes, "slope_structure_code"),
        "lithology": field_coverage(slopes, "lithology_terms"),
        "stratum": field_coverage(slopes, "stratum_terms"),
        "structural_plane": field_coverage(slopes, "structural_planes"),
        "vegetation": field_coverage(slopes, "vegetation_condition"),
        "historical_deformation": field_coverage(slopes, "deformation_observations"),
        "hydrology_baseline": field_coverage(slopes, "hydrology_observations"),
        "stability": field_coverage(slopes, "stability_scenarios"),
        "multi_source": ratio_metric(multi_source, total),
    }
    active_graph_coverage = {
        "registry": graph_prop_coverage(graph_slopes, "source_alias"),
        "coordinates": graph_prop_coverage(graph_slopes, "start_coordinate"),
        "geometry": graph_prop_coverage(graph_slopes, "slope_height_min_m"),
        "slope_type": graph_prop_coverage(graph_slopes, "slope_type"),
        "material_nature": graph_prop_coverage(graph_slopes, "material_nature"),
        "slope_aspect": graph_prop_coverage(graph_slopes, "slope_aspect_deg"),
        "slope_structure": graph_prop_coverage(graph_slopes, "slope_structure_code"),
        "vegetation": graph_prop_coverage(graph_slopes, "vegetation_condition"),
        "structural_plane": graph_relation_coverage(graph_slopes, relations_by_source, "DEVELOPS_STRUCTURAL_PLANE"),
        "lithology": graph_relation_coverage(graph_slopes, relations_by_source, "HAS_LITHOLOGY"),
        "stratum": graph_relation_coverage(graph_slopes, relations_by_source, "HAS_STRATUM"),
        "protection_design": graph_relation_coverage(graph_slopes, relations_by_source, "HAS_PROTECTION_DESIGN"),
        "stability": graph_relation_coverage(graph_slopes, relations_by_source, "HAS_STABILITY_ANALYSIS"),
        "historical_deformation": graph_relation_coverage(graph_slopes, relations_by_source, "HAS_DEFORMATION_OBSERVATION"),
        "hydrology_baseline": graph_relation_coverage(graph_slopes, relations_by_source, "HAS_HYDROLOGY_OBSERVATION"),
        "current_deformation": ratio_metric(
            sum(
                any(
                    target.get("props", {}).get("current_status_known") is True
                    for target in targets_by_source_relation.get((slope.get("id"), "HAS_DEFORMATION_OBSERVATION"), [])
                )
                for slope in graph_slopes
            ),
            len(graph_slopes),
        ),
    }
    gates = {
        "slope_discovery_nonempty": total > 0,
        "station_syntax_valid": valid_stations == total and total > 0,
        "route_station_identity_unique": unique_slope_keys == total and total > 0,
        "registry_coverage_at_least_95pct": coverage["registry"]["coverage"] >= 0.95,
        "coordinate_coverage_at_least_80pct": coverage["coordinates"]["coverage"] >= 0.80,
        "treatment_coverage_at_least_80pct": coverage["treatment"]["coverage"] >= 0.80,
        "assertion_evidence_coverage_at_least_95pct": (with_evidence / len(assertions) if assertions else 0) >= 0.95,
        "geometry_coverage_at_least_50pct": coverage["geometry"]["coverage"] >= 0.50,
        "structural_plane_coverage_at_least_40pct": coverage["structural_plane"]["coverage"] >= 0.40,
        "historical_observation_temporal_boundary_complete": (
            observations_with_temporal_boundary == len(historical_observations)
        ),
        "graph_has_no_dangling_edges": dangling_edges == 0,
        "graph_has_no_orphan_nodes": orphan_nodes == 0,
    }
    if semantic_summary.get("enabled"):
        gates["semantic_pass_rate_at_least_90pct"] = float(semantic_summary.get("pass_rate") or 0) >= 0.90
        gates["semantic_field_evidence_at_least_80pct"] = float(semantic_summary.get("field_evidence_coverage") or 0) >= 0.80
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "evaluation_mode": "automatic_internal_quality_no_manual_seed",
        "manual_seed_used": False,
        "discovery": {
            "slopes": total,
            "valid_station_syntax": valid_stations,
            "unique_stations": unique_stations,
            "unique_slope_keys": unique_slope_keys,
            "multi_source_slopes": multi_source,
        },
        "automatic_coverage": coverage,
        "active_graph_coverage": active_graph_coverage,
        "semantic": semantic_summary,
        "evidence": {
            "assertions": len(assertions),
            "assertions_with_evidence": with_evidence,
            "coverage": round(with_evidence / len(assertions), 4) if assertions else 0,
        },
        "graph_integrity": {
            "nodes": len(node_ids),
            "duplicate_node_ids": len(node_ids) - len(node_set),
            "edges": len(graph.get("edges", [])),
            "dangling_edges": dangling_edges,
            "orphan_nodes": orphan_nodes,
        },
        "temporal_integrity": {
            "historical_observations": len(historical_observations),
            "with_explicit_temporal_boundary": observations_with_temporal_boundary,
            "coverage": round(observations_with_temporal_boundary / len(historical_observations), 4)
            if historical_observations else 1.0,
        },
        "quality_gates": gates,
        "quality_gate_pass_rate": round(sum(gates.values()) / len(gates), 4),
        "parser": extracted.get("stats", {}),
        "accuracy_status": "requires_independent_gold_set_for_periodic_audit",
        "limitations": [
            "运行时不依赖人工种子；内部指标衡量覆盖、证据和一致性，不等同于真实语义准确率。",
            "独立人工金标准仅用于定期离线审计，不参与生产图谱生成。",
            "扫描页OCR引擎不可用时会降级到原生文本和其他文档来源，并显式记录缺口。",
        ],
    }


def field_coverage(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    available = sum(row.get(field) not in (None, "", [], {}) for row in rows)
    return ratio_metric(available, len(rows))


def ratio_metric(available: int, total: int) -> dict[str, Any]:
    return {"available": available, "total": total, "coverage": round(available / total, 4) if total else 0}


def graph_prop_coverage(nodes: list[dict[str, Any]], field: str) -> dict[str, Any]:
    return ratio_metric(
        sum(node.get("props", {}).get(field) not in (None, "", [], {}) for node in nodes),
        len(nodes),
    )


def graph_relation_coverage(
    nodes: list[dict[str, Any]],
    relations_by_source: dict[str, set[str]],
    relation: str,
) -> dict[str, Any]:
    return ratio_metric(
        sum(relation in relations_by_source.get(node.get("id"), set()) for node in nodes),
        len(nodes),
    )
