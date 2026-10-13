"""Read-only application queries with explicitly supplied workspace paths."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from .config import PATHS, DemoPaths
from .manual import apply_slope_overrides, merged_rule_library
from .risk import build_risk_screening
from .risk_evaluation import evaluate_risk_screening
from .schema import build_completeness
from .storage import discover_pdf_files, ensure_dir, read_json, read_jsonl
from .parsers import file_sha256

def document_inventory(paths: DemoPaths = PATHS) -> dict[str, Any]:
    documents = read_jsonl(paths.parsed_dir / "documents.jsonl")
    pages = read_jsonl(paths.parsed_dir / "pages.jsonl")
    text_blocks = read_jsonl(paths.parsed_dir / "text_blocks.jsonl")
    tables = read_jsonl(paths.parsed_dir / "tables.jsonl")
    ocr_tasks = read_jsonl(paths.parsed_dir / "ocr_tasks.jsonl")
    by_name = {row.get("file_name"): row for row in documents}
    page_by_doc: dict[str, dict[str, int]] = {}
    for row in pages:
        stats = page_by_doc.setdefault(row.get("document_id", ""), {})
        page_type = row.get("page_type", "unknown")
        stats[page_type] = stats.get(page_type, 0) + 1
    text_by_doc: dict[str, int] = {}
    for row in text_blocks:
        doc_id = row.get("document_id", "")
        text_by_doc[doc_id] = text_by_doc.get(doc_id, 0) + 1
    table_by_doc: dict[str, int] = {}
    for row in tables:
        doc_id = row.get("document_id", "")
        table_by_doc[doc_id] = table_by_doc.get(doc_id, 0) + 1
    ocr_by_doc: dict[str, dict[str, int]] = {}
    for row in ocr_tasks:
        stats = ocr_by_doc.setdefault(row.get("document_id", ""), {})
        status = row.get("status", "unknown")
        stats[status] = stats.get(status, 0) + 1

    rows = []
    ensure_dir(paths.raw_dir)
    for file in discover_pdf_files(paths.raw_dir):
        parsed_record = by_name.get(file.name)
        doc_id = parsed_record.get("id") if parsed_record else None
        stat = file.stat()
        current = bool(
            parsed_record
            and parsed_record.get("source_size_bytes") == stat.st_size
            and parsed_record.get("source_mtime_ns") == stat.st_mtime_ns
        )
        if current and parsed_record.get("source_sha256"):
            try:
                current = file_sha256(file) == parsed_record["source_sha256"]
            except OSError:
                current = False
        stale = bool(parsed_record and not current)
        rows.append(
            {
                "file_name": file.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "parsed": current,
                "stale": stale,
                "change_status": "current" if current else ("changed" if stale else "new"),
                "document_id": doc_id,
                "kind": parsed_record.get("kind") if parsed_record else None,
                "pages": parsed_record.get("pages") if parsed_record else None,
                "parse_scope": parsed_record.get("parse_scope") if parsed_record else None,
                "page_types": page_by_doc.get(doc_id, {}) if doc_id else {},
                "text_blocks": text_by_doc.get(doc_id, 0) if doc_id else 0,
                "tables": table_by_doc.get(doc_id, 0) if doc_id else 0,
                "ocr_status_counts": ocr_by_doc.get(doc_id, {}) if doc_id else {},
            }
        )
    return {
        "count": len(rows),
        "parsed_count": sum(1 for row in rows if row["parsed"]),
        "unparsed_count": sum(1 for row in rows if not row["parsed"]),
        "stale_count": sum(1 for row in rows if row["stale"]),
        "rows": rows,
    }


def empty_rule_library() -> dict[str, Any]:
    return {
        "schema_name": "slope_rule_library",
        "schema_version": "1.0.0-candidate",
        "publication_status": "not_generated",
        "execution_enabled": False,
        "documents": [],
        "clauses": [],
        "formulas": [],
        "threshold_tables": [],
        "rules": [],
        "stats": {},
    }


def current_graph(paths: DemoPaths = PATHS) -> dict[str, Any]:
    base = read_json(paths.graph_json, {"nodes": [], "edges": [], "evidence": []})
    return apply_slope_overrides(base, paths=paths)


def current_rule_library(paths: DemoPaths = PATHS) -> dict[str, Any]:
    base = read_json(paths.rules_json, empty_rule_library())
    return merged_rule_library(base, paths=paths)


def current_completeness(paths: DemoPaths = PATHS) -> dict[str, Any]:
    return build_completeness(current_graph(paths))


def find_slope(slope_id: str, paths: DemoPaths = PATHS) -> dict[str, Any] | None:
    return next(
        (row for row in current_graph(paths).get("nodes", []) if row.get("id") == slope_id and row.get("type") == "Slope"),
        None,
    )


def current_risk_screening(paths: DemoPaths = PATHS) -> dict[str, Any]:
    return assess_graph(current_graph(paths), paths)[1]


def assess_graph(graph: dict[str, Any], paths: DemoPaths = PATHS) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assess one effective graph snapshot using the current reviewed rules."""
    library = current_rule_library(paths)
    completeness = build_completeness(graph)
    return completeness, build_risk_screening(graph, library, completeness)


def evaluate_risk_result(screening: dict[str, Any], paths: DemoPaths = PATHS) -> dict[str, Any]:
    gold = read_json(paths.root / "data" / "validation" / "risk_gold.json", {"labels": []})
    return evaluate_risk_screening(screening, gold)


def current_risk_evaluation(paths: DemoPaths = PATHS) -> dict[str, Any]:
    return evaluate_risk_result(current_risk_screening(paths), paths)
