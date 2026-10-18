"""Legacy text semantic orchestration and cache; basic/deep behavior is preserved."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from typing import Any, Callable
from .llm import DEFAULT_DEEPSEEK_MODEL, call_deepseek_json
from .semantic_contract import PROMPT_VERSION, SEMANTIC_FIELDS, SYSTEM_PROMPT, SECTION_KEYWORDS
from .semantic_sources import (select_source_samples, station_variants, context_windows,
                               source_sample_score, compact_deterministic_facts)
from .semantic_validation import (validate_candidate, normalize_text, canonical_evidence_text,
                                  number_appears, stability_support_page)


def run_semantic_extraction(
    extracted: dict[str, Any],
    *,
    enabled: bool,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    cached_rows: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not enabled:
        return [], semantic_summary([], enabled=False, model=model)
    cache = {row.get("source_fingerprint"): row for row in (cached_rows or []) if row.get("source_fingerprint")}
    rows: list[dict[str, Any]] = []
    called = 0
    cache_hits = 0
    cache_revalidated = 0
    failures = 0
    supplemental_calls = 0
    gaps_detected = 0
    gaps_resolved = 0
    skipped = []
    slopes = extracted.get("slopes", [])
    if limit is not None:
        slopes = slopes[:limit]
    total_slopes = len(slopes)
    for slope_index, slope in enumerate(slopes, start=1):
        slope_key = f"{slope.get('route_code') or 'ROUTE'}:{slope['station']}"
        if limit is not None and len(rows) >= limit:
            break
        samples = select_source_samples(slope)
        if not samples:
            skipped.append({"route_code": slope.get("route_code"), "station": slope["station"],
                            "reason": "no_section_source_samples"})
            if progress_callback:
                progress_callback(slope_index, total_slopes, slope["station"])
            continue
        compact = compact_deterministic_facts(slope)
        fingerprint = source_fingerprint(slope_key, samples, model, compact)
        legacy_fingerprint = source_fingerprint(slope["station"], samples, model, compact)
        cached = cache.get(fingerprint) or cache.get(legacy_fingerprint)
        if cached and cached.get("validation_status") == "passed":
            revalidated, validation = validate_candidate(cached.get("candidate"), samples)
            if validation["accepted"]:
                remaining_gaps = detect_section_gaps(revalidated, samples, compact)
                rows.append(
                    {
                        **cached,
                        "route_code": slope.get("route_code"),
                        "source_fingerprint": fingerprint,
                        "candidate": revalidated,
                        "validation": cached.get("validation", validation),
                        "cache_revalidation": validation,
                        "validation_status": "passed",
                        "section_completeness": {
                            "detected_gaps": remaining_gaps,
                            "remaining_gaps": remaining_gaps,
                            "supplemental_call_used": False,
                        },
                    }
                )
                gaps_detected += len(remaining_gaps)
                cache_hits += 1
                if progress_callback:
                    progress_callback(slope_index, total_slopes, slope["station"])
                continue
        if cached and cached.get("raw_candidate"):
            revalidated, validation = validate_candidate(cached["raw_candidate"], samples)
            if validation["accepted"]:
                rows.append(
                    {
                        **cached,
                        "route_code": slope.get("route_code"),
                        "source_fingerprint": fingerprint,
                        "candidate": revalidated,
                        "raw_candidate": None,
                        "validation": validation,
                        "validation_status": "passed",
                        "generated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                cache_revalidated += 1
                if progress_callback:
                    progress_callback(slope_index, total_slopes, slope["station"])
                continue
        source_text = "\n\n".join(f"[PDF第{sample['page']}页]\n{sample['text']}" for sample in samples)
        prompt = f"目标边坡：{slope['station']}\n确定性字段：{json.dumps(compact, ensure_ascii=False)}\n\n原文：\n{source_text[:90000]}"
        try:
            candidate, metadata = call_deepseek_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                model=model,
            )
            called += 1
            validated, validation = validate_candidate(candidate, samples)
            initial_gaps = detect_section_gaps(validated, samples, compact)
            gaps_detected += len(initial_gaps)
            supplemental_metadata: dict[str, Any] | None = None
            if validation["accepted"] and initial_gaps:
                supplement_prompt = (
                    f"目标边坡：{slope['station']}\n"
                    f"第一轮已验证结果：{json.dumps(validated, ensure_ascii=False)}\n"
                    f"需要重点补齐的章节字段：{json.dumps(initial_gaps, ensure_ascii=False)}\n"
                    "重新阅读原文，只补充原文明确存在且第一轮遗漏的事实；仍须输出完整JSON结构和逐字证据。\n\n"
                    f"原文：\n{source_text[:90000]}"
                )
                supplement, supplemental_metadata = call_deepseek_json(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=supplement_prompt,
                    model=model,
                )
                called += 1
                supplemental_calls += 1
                merged = merge_candidates(validated, supplement)
                merged_validated, merged_validation = validate_candidate(merged, samples)
                if merged_validation["accepted"]:
                    candidate = merged
                    validated = merged_validated
                    validation = merged_validation
            remaining_gaps = detect_section_gaps(validated, samples, compact)
            gaps_resolved += len(set(initial_gaps) - set(remaining_gaps))
            rows.append(
                {
                    "id": f"semantic_{len(rows)+1:03d}",
                    "station": slope["station"],
                    "route_code": slope.get("route_code"),
                    "candidate": validated,
                    "raw_candidate": candidate if not validation["accepted"] else None,
                    "model": metadata.get("model", model),
                    "usage": metadata.get("usage", {}),
                    "supplemental_usage": supplemental_metadata.get("usage", {}) if supplemental_metadata else {},
                    "prompt_version": PROMPT_VERSION,
                    "source_fingerprint": fingerprint,
                    "source_pages": sorted({sample["page"] for sample in samples}),
                    "source_refs": [{"document_id": sample.get("document_id"), "page": sample["page"]} for sample in samples],
                    "validation": validation,
                    "section_completeness": {
                        "detected_gaps": initial_gaps,
                        "remaining_gaps": remaining_gaps,
                        "supplemental_call_used": bool(supplemental_metadata),
                    },
                    "validation_status": "passed" if validation["accepted"] else "failed",
                    "review_status": "automatic_evidence_validated_not_human_reviewed",
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            if not validation["accepted"]:
                failures += 1
        except Exception as exc:
            called += 1
            failures += 1
            rows.append(
                {
                    "id": f"semantic_{len(rows)+1:03d}",
                    "station": slope["station"],
                    "route_code": slope.get("route_code"),
                    "candidate": {},
                    "model": model,
                    "prompt_version": PROMPT_VERSION,
                    "source_fingerprint": fingerprint,
                    "source_pages": sorted({sample["page"] for sample in samples}),
                    "source_refs": [{"document_id": sample.get("document_id"), "page": sample["page"]} for sample in samples],
                    "validation": {"accepted": False, "error": str(exc)},
                    "validation_status": "failed",
                    "review_status": "automatic_validation_failed",
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
        if progress_callback:
            progress_callback(slope_index, total_slopes, slope["station"])
    summary = semantic_summary(rows, enabled=True, model=model)
    summary.update(
        {
            "api_calls": called,
            "cache_hits": cache_hits,
            "cache_revalidated": cache_revalidated,
            "supplemental_calls": supplemental_calls,
            "section_gaps_detected": gaps_detected,
            "section_gaps_resolved": gaps_resolved,
            "failures": failures,
            "requested_slopes": total_slopes,
            "skipped_count": len(skipped),
            "skipped": skipped,
        }
    )
    return rows, summary



def detect_section_gaps(
    candidate: dict[str, Any],
    samples: list[dict[str, Any]],
    deterministic: dict[str, Any] | None = None,
) -> list[str]:
    """Find likely omissions only when the source packet actually mentions a section."""
    source = "".join(normalize_text(str(sample.get("text", ""))) for sample in samples)
    gaps = []
    deterministic = deterministic or {}
    for field, keywords in SECTION_KEYWORDS.items():
        if deterministic.get(field) not in (None, "", [], {}):
            continue
        if field == "protection_records" and deterministic.get("measures"):
            continue
        if field == "stability_conclusions" and (deterministic.get("stability_scenarios") or deterministic.get("safety_factor_pairs")):
            continue
        source_has_section = any(normalize_text(keyword) in source for keyword in keywords)
        if source_has_section and candidate.get(field) in (None, "", [], {}):
            gaps.append(field)
    # A nonempty list can still be incomplete. Compare typed source tuples to
    # both extraction layers; never compare angles alone across object roles.
    from .source_candidates import missing_orientations
    planes = [*(deterministic.get("structural_planes") or []), *(candidate.get("structural_planes") or [])]
    if missing_orientations(samples, planes) and "structural_planes" not in gaps:
        gaps.append("structural_planes")
    return gaps



def merge_candidates(base: dict[str, Any], supplement: Any) -> dict[str, Any]:
    """Merge a focused second pass without allowing it to erase validated facts."""
    if not isinstance(supplement, dict):
        return base
    merged: dict[str, Any] = {}
    for field in SEMANTIC_FIELDS:
        left = base.get(field)
        right = supplement.get(field)
        if isinstance(left, list) or isinstance(right, list):
            values = [*(left or []), *(right or [])]
            seen: set[str] = set()
            unique = []
            for value in values:
                signature = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                if signature not in seen:
                    seen.add(signature)
                    unique.append(value)
            merged[field] = unique
        else:
            merged[field] = left if left not in (None, "") else right
    return merged



def semantic_summary(rows: list[dict[str, Any]], *, enabled: bool, model: str) -> dict[str, Any]:
    passed = [row for row in rows if row.get("validation_status") == "passed"]
    verified = sum(int(row.get("validation", {}).get("verified_quotes", 0)) for row in rows)
    submitted = sum(int(row.get("validation", {}).get("submitted_quotes", 0)) for row in rows)
    submitted_fields = sum(int(row.get("validation", {}).get("submitted_fact_fields", 0)) for row in rows)
    supported_fields = sum(int(row.get("validation", {}).get("supported_fact_fields", 0)) for row in rows)
    return {
        "enabled": enabled,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "candidates": len(rows),
        "passed": len(passed),
        "pass_rate": round(len(passed) / len(rows), 4) if rows else None,
        "verified_quotes": verified,
        "submitted_quotes": submitted,
        "evidence_quote_precision": round(verified / submitted, 4) if submitted else None,
        "field_evidence_coverage": round(supported_fields / submitted_fields, 4) if submitted_fields else None,
    }



def source_fingerprint(
    station: str,
    samples: list[dict[str, Any]],
    model: str,
    deterministic: dict[str, Any] | None = None,
) -> str:
    payload = json.dumps(
        {"station": station, "samples": samples, "model": model, "prompt": PROMPT_VERSION, "deterministic": deterministic or {}},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
