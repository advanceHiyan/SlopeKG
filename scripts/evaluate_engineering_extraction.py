"""Offline evaluation. Gold fixtures never enter the production pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from slopekg.extractors import extract_domain_candidates
from slopekg.storage import read_json, read_jsonl, write_json


def evaluate(records, cases):
    results = []
    for case in cases:
        field = "material_parameters" if case["kind"] == "parameters" else "stability_results"
        selected = [r for r in records if r["source_file"].startswith(case["file_prefix"])
                    and r["page"] == case["page"] and r.get(field)]
        expected, actual = [], []
        if field == "material_parameters":
            definitions = [("unit_weight", "kN/m³"), ("cohesion", "kPa"), ("friction_angle", "°")]
            for material, state, *values in case["parameters"]:
                expected.extend((material, state, name, unit, value) for (name, unit), value in zip(definitions, values))
            actual = [(v["material"], v["condition"], v["parameter"], v["unit"], v["value"])
                      for r in selected for v in r[field]]
        else:
            expected = [tuple(values) for values in case["factors"]]
            actual = [(v["condition"], v["safety_factor"], v["required_factor"], v["status"])
                      for r in selected for v in r[field]]
        hits = sum((Counter(expected) & Counter(actual)).values())
        association_ok = len(selected) == 1 and selected[0]["station"] == case["station"]
        issue_ok = len(selected) == 1 and set(case.get("issues", [])) == set(selected[0]["quality_issues"])
        results.append({"file_prefix": case["file_prefix"], "page": case["page"], "kind": case["kind"],
                        "expected": len(expected), "extracted": len(actual), "correct": hits,
                        "association_ok": association_ok, "issue_detection_ok": issue_ok,
                        "passed": hits == len(expected) == len(actual) and association_ok and issue_ok})
    expected = sum(r["expected"] for r in results)
    extracted = sum(r["extracted"] for r in results)
    correct = sum(r["correct"] for r in results)
    return {"scope": "已人工查看原页的局部表格回归集，不代表全库准确率或覆盖率，也不代表工程结论正确性",
            "cases": results, "cases_passed": sum(r["passed"] for r in results), "cases_total": len(results),
            "expected_records": expected, "extracted_records": extracted, "correct_records": correct,
            "precision": correct / extracted if extracted else 0,
            "recall": correct / expected if expected else 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed-dir", type=Path, default=ROOT / "output/demo/parsed")
    parser.add_argument("--output", type=Path, default=ROOT / "output/demo/evaluation_engineering.json")
    args = parser.parse_args()
    parsed = {k: read_jsonl(args.parsed_dir / f"{k}.jsonl") for k in ("documents", "pages", "text_blocks", "tables")}
    extracted = extract_domain_candidates(parsed)
    gold = read_json(ROOT / "tests/fixtures/engineering_gold.json")
    report = evaluate(extracted["engineering_records"], gold["cases"])
    report["corpus_counts"] = extracted["stats"]
    report["source_issues"] = [{k: r[k] for k in ("source_file", "page", "station", "quality_issues", "caption")}
                               for r in extracted["engineering_records"] if r["quality_issues"]]
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["cases_passed"] == report["cases_total"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
