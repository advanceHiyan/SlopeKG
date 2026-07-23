from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from slopekg.config import PATHS  # noqa: E402
from slopekg.llm import call_deepseek_json  # noqa: E402
from slopekg.storage import read_json, read_jsonl, write_json, write_jsonl  # noqa: E402


SYSTEM_PROMPT = """你是公路边坡工程资料结构化抽取器。只依据提供的原文抽取，不得补充常识或猜测。
输出严格JSON对象，字段为：mechanism_summary(string|null)、failure_modes(array<string>)、stability_conclusions(array<object>)、causal_factors(array<string>)、protection_rationale(string|null)、uncertainties(array<string>)、evidence_quotes(array<object>)。
stability_conclusions中每项包含condition、safety_factor、status；缺失值用null。evidence_quotes每项包含page和quote，quote必须是原文短句。"""

REQUIRED_FIELDS = {
    "mechanism_summary",
    "failure_modes",
    "stability_conclusions",
    "causal_factors",
    "protection_rationale",
    "uncertainties",
    "evidence_quotes",
}


def candidate_is_schema_valid(candidate: object) -> bool:
    if not isinstance(candidate, dict) or not REQUIRED_FIELDS.issubset(candidate):
        return False
    list_fields = ["failure_modes", "stability_conclusions", "causal_factors", "uncertainties", "evidence_quotes"]
    if not all(isinstance(candidate.get(field), list) for field in list_fields):
        return False
    conclusions_ok = all(
        isinstance(item, dict) and {"condition", "safety_factor", "status"}.issubset(item)
        for item in candidate["stability_conclusions"]
    )
    quotes_ok = all(isinstance(item, dict) and {"page", "quote"}.issubset(item) for item in candidate["evidence_quotes"])
    return conclusions_ok and quotes_ok


def build_quality_summary(rows: list[dict]) -> dict:
    count = len(rows)
    schema_valid = sum(candidate_is_schema_valid(row.get("candidate")) for row in rows)
    with_quotes = sum(bool(row.get("candidate", {}).get("evidence_quotes")) for row in rows)
    pending = sum(row.get("review_status") == "pending" for row in rows)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": count,
        "model": rows[0]["model"] if rows else None,
        "schema_valid": schema_valid,
        "schema_valid_rate": round(schema_valid / count, 4) if count else None,
        "with_evidence_quotes": with_quotes,
        "evidence_quote_coverage": round(with_quotes / count, 4) if count else None,
        "pending_review": pending,
        "accuracy_status": "not_evaluated_without_independent_gold_set",
        "note": "该指标只验证输出结构与证据字段是否存在；语义准确率仍需独立人工金标准评测。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Use DeepSeek V4 Flash for complex slope-section candidate extraction")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--evaluate-existing", action="store_true", help="只重算既有候选质量指标，不调用模型")
    args = parser.parse_args()
    if args.evaluate_existing:
        output = read_jsonl(PATHS.extracted_dir / "llm_candidates.jsonl")
        quality = build_quality_summary(output)
        write_json(PATHS.extracted_dir / "llm_evaluation.json", quality)
        print(json.dumps({"count": len(output), "quality": quality}, ensure_ascii=False, indent=2))
        return
    summary = read_json(PATHS.extracted_dir / "extraction_summary.json", {})
    section_rows = {row["station"]: row for row in summary.get("section_facts", [])}
    output = []
    for slope in summary.get("slopes", []):
        if len(output) >= args.limit:
            break
        section = section_rows.get(slope["station"], {})
        samples = section.get("section_text_samples", [])
        if not samples:
            continue
        text = "\n\n".join(f"[PDF第{sample['page']}页/{sample['panel']}]\n{sample['text']}" for sample in samples)
        user_prompt = f"边坡：{slope['station']}\n\n原文：\n{text[:30000]}"
        extracted, metadata = call_deepseek_json(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt, model=args.model)
        output.append(
            {
                "id": f"llm_candidate_{len(output)+1:03d}",
                "station": slope["station"],
                "candidate": extracted,
                "model": metadata["model"],
                "usage": metadata["usage"],
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "review_status": "pending",
                "source_pages": sorted({sample["page"] for sample in samples}),
            }
        )
    write_jsonl(PATHS.extracted_dir / "llm_candidates.jsonl", output)
    quality = build_quality_summary(output)
    write_json(PATHS.extracted_dir / "llm_evaluation.json", quality)
    print(json.dumps({"count": len(output), "model": args.model, "quality": quality, "rows": output}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
