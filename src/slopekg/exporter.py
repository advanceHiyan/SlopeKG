from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import PATHS, DemoPaths
from .manual import apply_slope_overrides
from .queries import current_risk_screening, evaluate_risk_result
from .schema import build_completeness
from .storage import read_json
from .storage import _atomic_write_text


TEMPLATE_FILE = PATHS.web_dir / "standalone-report.template.html"
DEFAULT_OUTPUT = PATHS.output_dir / "share" / "SlopeKG_成果展示_离线版.html"
RISK_TEMPLATE_FILE = PATHS.web_dir / "risk-standalone.template.html"
DEFAULT_RISK_OUTPUT = PATHS.output_dir / "share" / "SlopeKG_风险研判_离线版.html"


def export_standalone_html(
    output: Path = DEFAULT_OUTPUT,
    *,
    paths: DemoPaths = PATHS,
    graph_mode: str = "active",
    graph_override: dict[str, Any] | None = None,
    completeness_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export the current result as one offline HTML file with embedded data."""
    graph_files = {
        "basic": paths.basic_graph_json,
        "active": paths.graph_json,
    }
    graph_file = graph_files.get(graph_mode)
    if graph_file is None:
        raise ValueError("graph_mode must be basic or active; legacy text deep has been removed")
    if graph_mode != "active" and graph_file.exists() and paths.graph_json.exists():
        mode_graph = read_json(graph_file, {})
        active_graph = read_json(paths.graph_json, {})
        if source_signature(mode_graph) != source_signature(active_graph):
            raise ValueError("所选图谱与当前PDF版本不一致，请先执行对应模式解析，或导出当前图谱")
    graph = graph_override if graph_mode == "active" and graph_override is not None else read_json(graph_file, None)
    if not graph:
        raise FileNotFoundError(f"Graph output is missing: {graph_file}")
    if graph_override is None or graph_mode != "active":
        graph = apply_slope_overrides(graph, paths)

    state_file = {
        "active": paths.state_json, "basic": paths.basic_state_json,
    }[graph_mode]
    evaluation_file = paths.evaluation_json if graph_mode == "active" else paths.output_dir / f"evaluation.{graph_mode}.json"

    graph_for_export = compact_graph_for_offline(graph)
    payload = {
        "export": {
            "format": "slopekg_single_file_offline_html",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "graph_mode": graph_mode,
            "source_graph": str(graph_file.relative_to(paths.root)),
        },
        "graph": graph_for_export,
        "state": read_json(state_file, {}),
        "completeness": completeness_override if completeness_override is not None else build_completeness(graph),
        "evaluation": read_json(evaluation_file, {}),
        "schema": read_json(paths.schema_json, {}),
    }
    written = _write_embedded_report(output, TEMPLATE_FILE, "__SLOPEKG_EMBEDDED_DATA__", payload)
    return {
        **written,
        "graph_mode": graph_mode,
        "nodes": len(graph.get("nodes", [])),
        "edges": len(graph.get("edges", [])),
        "evidence": len(graph.get("evidence", [])),
    }


def export_risk_standalone_html(
    output: Path = DEFAULT_RISK_OUTPUT,
    *,
    paths: DemoPaths = PATHS,
    risk_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export the current macro risk screening as an interactive single HTML file."""
    risk_file = paths.output_dir / "risk" / "screening.json"
    if risk_override is not None:
        risk = risk_override
    elif paths.graph_json.exists():
        risk = current_risk_screening(paths)
    else:
        risk = read_json(risk_file, None)
    if not risk:
        raise FileNotFoundError(f"Risk screening output is missing: {risk_file}")

    payload = {
        "export": {
            "format": "slopekg_risk_single_file_offline_html",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": str(risk_file.relative_to(paths.root)),
        },
        "risk": risk,
        "evaluation": evaluate_risk_result(risk, paths),
    }
    written = _write_embedded_report(output, RISK_TEMPLATE_FILE, "__SLOPEKG_RISK_DATA__", payload)
    assessments = risk.get("assessments", [])
    return {
        **written,
        "slopes": len(assessments),
        "priority_counts": risk.get("summary", {}).get("priority_counts", {}),
    }


def _write_embedded_report(output: Path, template_file: Path, marker: str, payload: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = template_file.read_text(encoding="utf-8")
    if marker not in template:
        raise ValueError("Standalone template data marker is missing")
    html = template.replace(marker, serialized)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(output, html)
    return {
        "output": str(output), "bytes": len(html.encode("utf-8")),
        "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
    }


def source_signature(graph: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    rows = graph.get("meta", {}).get("pipeline", {}).get("source_documents", [])
    return tuple(sorted((row.get("file_name"), row.get("size_bytes"), row.get("mtime_ns"), row.get("sha256")) for row in rows))


def compact_graph_for_offline(graph: dict[str, Any]) -> dict[str, Any]:
    """Keep all visual graph content while omitting trace tables unused by the offline viewer.

    ``property_assertions``, ``source_records`` and ``processing_audit`` are retained in the machine-readable
    graph JSON.  The standalone viewer resolves provenance through node/edge evidence,
    so embedding the two redundant trace tables would more than double its size.
    """
    compact = {
        key: value
        for key, value in graph.items()
        if key not in {"property_assertions", "source_records", "processing_audit"}
    }
    compact_meta = dict(compact.get("meta") or {})
    compact_meta["offline_compaction"] = {
        "omitted_property_assertions": len(graph.get("property_assertions", [])),
        "omitted_source_records": len(graph.get("source_records", [])),
        "omitted_processing_candidates": len(graph.get("processing_audit", {}).get("candidates", [])),
        "full_trace_available_in_graph_json": True,
        "visual_edge_provenance_stored_on_asset_node": True,
    }
    compact["meta"] = compact_meta
    # Visual asset provenance is already present in the asset's properties.
    # The offline viewer reads those properties, not these duplicated edge
    # fields. Keep every node/edge and other edge evidence unchanged.
    visual_ids = {n["id"] for n in graph.get("nodes", []) if n.get("props", {}).get("asset_catalog_member")}
    compact["edges"] = [
        {**edge, "props": {k: v for k, v in edge.get("props", {}).items() if k not in {"source_document_id", "source_page"}}}
        if edge.get("relation") in {"HAS_VISUAL_ASSET", "DERIVED_FROM"}
        and (edge.get("source") in visual_ids or edge.get("target") in visual_ids)
        else edge
        for edge in graph.get("edges", [])
    ]
    return compact
