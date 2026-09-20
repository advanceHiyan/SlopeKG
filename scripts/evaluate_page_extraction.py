"""Evaluate visually reviewed pages across native, table, scan and drawing paths.

The fixture is a regression aid only. It checks whether selected key facts seen
on the original pages survive parsing; it is not a corpus-wide OCR benchmark.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.storage import read_json, read_jsonl, write_json
from slopekg.ocr import with_layout_groups


def normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"～": "-", "~": "-", "—": "-", "–": "-", "−": "-", "º": "°"}))
    return "".join(text.split()).lower()


def evaluate(parsed_dir: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    documents = read_jsonl(parsed_dir / "documents.jsonl")
    pages = read_jsonl(parsed_dir / "pages.jsonl")
    blocks = read_jsonl(parsed_dir / "text_blocks.jsonl")
    tables = read_jsonl(parsed_dir / "tables.jsonl")
    ocr = with_layout_groups(read_jsonl(parsed_dir / "ocr_results.jsonl"))
    page_index = {(row["document_id"], int(row["page"])): row for row in pages}
    results = []
    for case in cases:
        matching_documents = [row for row in documents if str(row.get("file_name", "")).startswith(case["file_prefix"])]
        if len(matching_documents) != 1:
            results.append({**case, "passed": False, "expected_facts": len(case["expected_phrases"]),
                            "matched_facts": 0, "error": f"matched_documents={len(matching_documents)}"})
            continue
        document = matching_documents[0]
        document_id = document["id"]
        page_number = int(case["page"])
        actual_page_type = page_index.get((document_id, page_number), {}).get("page_type")
        if case["page_type"] == "table":
            selected_tables = [row for row in tables if row.get("document_id") == document_id and int(row.get("page", 0)) == page_number]
            selected_blocks = [row for row in blocks if row.get("document_id") == document_id and int(row.get("page", 0)) == page_number]
            items = [cell for table in selected_tables for row in table.get("rows", []) for cell in row]
            items.extend(row.get("text", "") for row in selected_blocks)
            confidence_values: list[float] = []
        elif case["page_type"] in {"scanned", "drawing"}:
            selected = [row for row in ocr if row.get("document_id") == document_id and int(row.get("page", 0)) == page_number]
            items = [row.get("text", "") for row in selected]
            confidence_values = [float(row["confidence"]) for row in selected if row.get("confidence") is not None]
        else:
            selected = [row for row in blocks if row.get("document_id") == document_id and int(row.get("page", 0)) == page_number]
            items = [row.get("text", "") for row in selected]
            confidence_values = []
        searchable = normalized(" ".join(str(item or "") for item in items))
        checks = [{"expected": phrase, "matched": normalized(phrase) in searchable} for phrase in case["expected_phrases"]]
        results.append({
            "file": document.get("file_name"),
            "page": page_number,
            "page_type": case["page_type"],
            "classified_page_type": actual_page_type,
            "source_items": len(items),
            "expected_facts": len(checks),
            "matched_facts": sum(row["matched"] for row in checks),
            "checks": checks,
            "mean_ocr_confidence": round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else None,
            "passed": actual_page_type == case["page_type"] and all(row["matched"] for row in checks),
            "review_method": case.get("review_method", "existing_human_review"),
        })
    type_counts = Counter(row.get("page_type") for row in results)
    type_passes = Counter(row.get("page_type") for row in results if row.get("passed"))
    expected = sum(int(row.get("expected_facts", 0)) for row in results)
    matched = sum(int(row.get("matched_facts", 0)) for row in results)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": f"查看原页后建立的{len(cases)}页局部回归集；包含可追溯的版面拼接候选，不代表全库字符级准确率、字段级准确率或工程结论正确性",
        "summary": {
            "cases_passed": sum(bool(row.get("passed")) for row in results),
            "cases_total": len(results),
            "documents_covered": len({row.get("file") for row in results if row.get("file")}),
            "source_documents": len(documents),
            "assistant_reviewed_cases": sum(row.get("review_method") == "assistant_visual_source_check" for row in results),
            "expected_key_facts": expected,
            "matched_key_facts": matched,
            "key_fact_recall": round(matched / expected, 4) if expected else None,
            "by_page_type": {
                page_type: {"passed": type_passes[page_type], "total": type_counts[page_type]}
                for page_type in sorted(type_counts)
            },
        },
        "cases": results,
        "limitations": [
            "未标注页面上的全部字符，因此不能计算字符识别精确率。",
            "关键事实未覆盖所有页面、所有字段和全部图纸方向。",
            "新增AI原页核对样本用于内部回归，尚未经独立人工复核。",
            "表格数值匹配另由evaluation_engineering.json中的单元格级回归集验证。",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed-dir", type=Path, default=ROOT / "output/demo/parsed")
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests/fixtures/page_extraction_gold.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output/demo/evaluation_pages.json")
    args = parser.parse_args()
    report = evaluate(args.parsed_dir, read_json(args.fixture)["cases"])
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # A missed fact is written to the report as a visible improvement item.
    # Fail automation only when the reviewed key-fact survival rate regresses
    # below the agreed local baseline.
    return 0 if float(report["summary"]["key_fact_recall"] or 0) >= 0.90 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
