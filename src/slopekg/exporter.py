from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import PATHS, DemoPaths
from .storage import read_json
from .storage import _atomic_write_text


TEMPLATE_FILE = PATHS.web_dir / "standalone-report.template.html"
DEFAULT_OUTPUT = PATHS.output_dir / "share" / "SlopeKG_成果展示_离线版.html"


def export_standalone_html(
    output: Path = DEFAULT_OUTPUT,
    *,
    paths: DemoPaths = PATHS,
    graph_mode: str = "active",
) -> dict[str, Any]:
    """Export the current result as one offline HTML file with embedded data."""
    graph_files = {
        "basic": paths.basic_graph_json,
        "deep": paths.deep_graph_json,
        "active": paths.graph_json,
    }
    graph_file = graph_files.get(graph_mode)
    if graph_file is None:
        raise ValueError("graph_mode must be basic, deep, or active")
    if graph_mode == "deep" and graph_file.exists() and paths.graph_json.exists():
        deep_graph = read_json(graph_file, {})
        active_graph = read_json(paths.graph_json, {})
        if source_signature(deep_graph) != source_signature(active_graph):
            raise ValueError("深度图谱与当前PDF版本不一致，请先执行深度解析，或导出当前图谱")
    if not graph_file.exists() and graph_mode == "deep":
        graph_file = paths.graph_json
    graph = read_json(graph_file, None)
    if not graph:
        raise FileNotFoundError(f"Graph output is missing: {graph_file}")

    payload = {
        "export": {
            "format": "slopekg_single_file_offline_html",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "graph_mode": graph_mode,
            "source_graph": str(graph_file.relative_to(paths.root)),
        },
        "graph": graph,
        "state": read_json(paths.state_json, {}),
        "completeness": read_json(paths.completeness_json, {}),
        "evaluation": read_json(paths.evaluation_json, {}),
        "schema": read_json(paths.schema_json, {}),
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    if "__SLOPEKG_EMBEDDED_DATA__" not in template:
        raise ValueError("Standalone template data marker is missing")
    html = template.replace("__SLOPEKG_EMBEDDED_DATA__", serialized)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(output, html)
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    return {
        "output": str(output),
        "bytes": output.stat().st_size,
        "sha256": digest,
        "graph_mode": graph_mode,
        "nodes": len(graph.get("nodes", [])),
        "edges": len(graph.get("edges", [])),
        "evidence": len(graph.get("evidence", [])),
    }


def source_signature(graph: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    rows = graph.get("meta", {}).get("pipeline", {}).get("source_documents", [])
    return tuple(sorted((row.get("file_name"), row.get("size_bytes"), row.get("mtime_ns"), row.get("sha256")) for row in rows))
