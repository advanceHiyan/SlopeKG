from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Callable

from .llm import call_deepseek_json
from .observation import supported_observation, belongs_to_station


PROMPT_VERSION = "slope-semantic-v8-calculation-object-identity"
SEMANTIC_FIELDS = {
    "hazard_body_type",
    "hazard_types",
    "mechanism_summary",
    "failure_modes",
    "lithology_terms",
    "stratum_terms",
    "slope_structure",
    "slope_type",
    "material_nature",
    "slope_length_m",
    "slope_height_min_m",
    "slope_height_max_m",
    "slope_gradient_min_deg",
    "slope_gradient_max_deg",
    "slope_aspect_deg",
    "vegetation_condition",
    "river_relation",
    "overall_slope_deformation_severity",
    "geomorphology",
    "structural_planes",
    "stability_conclusions",
    "deformation_observations",
    "hydrology_observations",
    "protection_records",
    "causal_factors",
    "protection_rationale",
    "uncertainties",
    "evidence_quotes",
}

SYSTEM_PROMPT = """你是公路边坡工程资料结构化抽取器。只允许依据输入原文，不得使用常识补全。
输出严格JSON对象，且只包含以下字段：
hazard_body_type(string|null)、hazard_types(array<string>)、mechanism_summary(string|null)、failure_modes(array<string>)、
lithology_terms(array<string>)、stratum_terms(array<string>)、slope_structure(string|null)、
slope_type(string|null)、material_nature(string|null)、slope_length_m(number|null)、
slope_height_min_m(number|null)、slope_height_max_m(number|null)、slope_gradient_min_deg(number|null)、
slope_gradient_max_deg(number|null)、slope_aspect_deg(number|null)、
vegetation_condition(object|null)、river_relation(object|null)、overall_slope_deformation_severity(string|null)、
geomorphology(string|null)、structural_planes(array<object>)、stability_conclusions(array<object>)、
deformation_observations(array<object>)、hydrology_observations(array<object>)、protection_records(array<object>)、causal_factors(array<string>)、
protection_rationale(string|null)、uncertainties(array<string>)、evidence_quotes(array<object>)。
structural_planes每项包含name、dip_direction、dip_angle、description；未知值用null。
stability_conclusions每项包含condition、safety_factor、required_factor、status、analysis_scope、source_kind、body_id、failure_mode、section；未知值用null，必须分别保留天然、暴雨、地震、治理前、治理后等不同工况。analysis_scope写整体边坡、危岩体、浅层边坡或计算断面；危岩编号写body_id，破坏模式写failure_mode，剖面编号写section，禁止把它们混写成来源类型。共同标题包含多个坡段而计算表未明确唯一归属时，不要将其作为目标边坡独有结果，记录uncertainties。
坡高和坡度只填写目标坡段一般描述；典型剖面、局部危岩尺寸、邻侧自然山体尺寸不能替代整坡属性。基岩坡体上部有土覆盖层不等于整个坡体为土质。
deformation_observations每项包含type、location、scale、timing、status、description；裂缝、掉块、局部垮塌、滑塌、鼓胀等分别记录。
hydrology_observations每项包含type、location、timing、value、unit、description；地下水、地表水、降雨、汇水、渗水和排水条件分别记录。
protection_records每项包含measure、status、location、parameters、description；status只能表达existing、recommended、designed、constructed、damaged或unknown，不得把拟建工程写成现状工程。
枚举规范：slope_type只能为路堤或路堑；material_nature只能为土质、岩质或土岩混合；
vegetation_condition包含density、has_deformation_sign、description，density只能为茂密、稀疏、无或null；
river_relation包含is_riverside、bank、channel_landform、description，其中bank只能为左岸、右岸或null，channel_landform只能为凹岸、凸岸、直线段或null；
overall_slope_deformation_severity只能为基本完好、轻微破坏、中等破坏、严重破坏、毁坏或null。
历史工程报告中的变形、水文和防护描述必须保留其历史时效，不得表述成当前巡检或实时监测。
evidence_quotes每项包含page、quote、supports；quote必须逐字摘自输入原文，建议截取8至80字的短句，禁止改写；
supports是该引句直接支撑的字段名数组，只能从上述字段中选择。每个非空事实字段至少应被一条引句支撑。
同一输入可能包含目录、总表或其他边坡内容，只抽取“目标边坡”的事实。"""

SECTION_KEYWORDS = {
    "slope_type": ["路堑边坡", "路堤边坡", "挖方边坡", "填方边坡", "开挖边坡"],
    "material_nature": ["岩质边坡", "土质边坡", "土岩混合"],
    "slope_height_max_m": ["边坡高", "坡高", "最大高差"],
    "slope_gradient_max_deg": ["边坡坡度", "开挖坡度", "坡度约"],
    "vegetation_condition": ["植被", "绿植", "基岩裸露"],
    "river_relation": ["临河", "河岸", "河道", "凹岸", "凸岸"],
    "deformation_observations": ["裂缝", "变形", "垮塌", "掉块", "滑塌", "崩塌", "落石", "鼓胀", "隆起"],
    "hydrology_observations": ["地下水", "地表水", "降雨", "汇水", "渗水", "滴水", "排水", "水文"],
    "protection_records": ["防护", "治理", "锚喷", "防护网", "放坡", "锚杆", "截水沟", "排水沟", "急流槽"],
    "stability_conclusions": ["稳定性", "安全系数", "稳定系数", "基本稳定", "不稳定", "欠稳定"],
    "structural_planes": ["结构面", "节理", "裂隙", "坡面产状", "岩层产状"],
}


def run_semantic_extraction(
    extracted: dict[str, Any],
    *,
    enabled: bool,
    model: str = "deepseek-v4-flash",
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
        }
    )
    return rows, summary


def select_source_samples(slope: dict[str, Any]) -> list[dict[str, Any]]:
    station = slope["station"]
    variants = station_variants(station)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for sample in slope.get("section_text_samples", []):
        text = str(sample.get("text", ""))
        if sample.get("station_context") == station:
            snippets = [text[:7000]]
        else:
            snippets = context_windows(text, variants)
        if not snippets and not re.search(r"K\s*\d+\s*\+", text) and any(
                term and term in text for term in [slope.get("source_alias", ""), station]):
            snippets = [text[:5000]]
        for snippet in snippets:
            key = (int(sample["page"]), snippet[:120])
            if key in seen:
                continue
            seen.add(key)
            selected.append({"document_id": sample.get("document_id"), "page": int(sample["page"]),
                             "station_context": station, "text": snippet})
    selected.sort(key=lambda row: source_sample_score(row["text"]), reverse=True)
    return selected[:12]


def station_variants(station: str) -> list[str]:
    variants = {station, station.replace("-K", "-")}
    match = re.match(r"(K\d+\+\d+)-K(\d+)\+(\d+)", station)
    if match and match.group(1).startswith(f"K{match.group(2)}+"):
        variants.add(f"{match.group(1)}-{match.group(3)}")
    return sorted(variants, key=len, reverse=True)


def context_windows(text: str, variants: list[str], radius: int = 2600) -> list[str]:
    from .extractors import STATION_RE, normalize_station_match, station_section_segments
    mentions = {normalize_station_match(m) for m in STATION_RE.finditer(text)}
    target = normalize_station_match(STATION_RE.search(variants[0])) if variants else None
    if target and len(mentions) > 1:
        segments, _ = station_section_segments(text, mentions, None, mentions)
        target_segments = [segment for station, segment in segments if station == target]
        if target_segments:
            return target_segments
        # Multiple sites without clear section boundaries need review, not a radius guess.
        return []
    windows = []
    for variant in variants:
        start = 0
        while True:
            index = text.find(variant, start)
            if index < 0:
                break
            windows.append(text[max(0, index - 500) : min(len(text), index + radius)])
            start = index + len(variant)
    return list(dict.fromkeys(windows))[:2]


def source_sample_score(text: str) -> int:
    """Prefer factual slope sections over contents pages and repeated headers."""
    score = sum(3 for words in SECTION_KEYWORDS.values() for word in words if word in text)
    score += sum(2 for word in ["基本情况", "工程地质条件", "地质特征", "稳定性评价", "治理措施"] if word in text)
    score -= 8 if "目录" in text and len(text) < 1200 else 0
    return score


def compact_deterministic_facts(slope: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "source_alias",
        "source_disaster_label",
        "side",
        "slope_length_m",
        "slope_height_min_m",
        "slope_height_max_m",
        "slope_gradient_min_deg",
        "slope_gradient_max_deg",
        "slope_type",
        "material_nature",
        "slope_aspect_deg",
        "slope_structure_code",
        "vegetation_condition",
        "river_relation",
        "overall_slope_deformation_severity",
        "structural_planes",
        "deformation_observations",
        "hydrology_observations",
        "geomorphology",
        "measures",
        "safety_factor_pairs",
        "stability_scenarios",
    ]
    return {key: slope.get(key) for key in keys if slope.get(key) not in (None, "", [], {})}


def validate_candidate(candidate: Any, samples: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(candidate, dict):
        candidate = {
            "geomorphology": None,
            "slope_type": None,
            "material_nature": None,
            "slope_length_m": None,
            "slope_height_min_m": None,
            "slope_height_max_m": None,
            "slope_gradient_min_deg": None,
            "slope_gradient_max_deg": None,
            "slope_aspect_deg": None,
            "vegetation_condition": None,
            "river_relation": None,
            "overall_slope_deformation_severity": None,
            "deformation_observations": [],
            "hydrology_observations": [],
            "protection_records": [],
            **candidate,
        }
    if not isinstance(candidate, dict) or not SEMANTIC_FIELDS.issubset(candidate):
        return {}, {"accepted": False, "schema_valid": False, "verified_quotes": 0, "submitted_quotes": 0}
    list_fields = [
        "hazard_types",
        "failure_modes",
        "lithology_terms",
        "stratum_terms",
        "structural_planes",
        "stability_conclusions",
        "deformation_observations",
        "hydrology_observations",
        "protection_records",
        "causal_factors",
        "uncertainties",
        "evidence_quotes",
    ]
    if not all(isinstance(candidate.get(field), list) for field in list_fields):
        return {}, {"accepted": False, "schema_valid": False, "verified_quotes": 0, "submitted_quotes": 0}
    page_text: dict[int, str] = {}
    for sample in samples:
        page = int(sample["page"])
        page_text[page] = page_text.get(page, "") + "\n" + normalize_text(sample["text"])
    canonical_page_text = {page: canonical_evidence_text(text) for page, text in page_text.items()}
    submitted = candidate.get("evidence_quotes", [])
    verified = []
    corrected_quote_pages = 0
    punctuation_normalized_quotes = 0
    for item in submitted:
        if not isinstance(item, dict) or item.get("page") is None or not item.get("quote") or not isinstance(item.get("supports"), list):
            continue
        try:
            page = int(item["page"])
        except (TypeError, ValueError):
            continue
        quote = normalize_text(str(item["quote"]))
        matched_page = page if quote in page_text.get(page, "") else None
        if matched_page is None:
            canonical_quote = canonical_evidence_text(quote)
            if len(canonical_quote) >= 8 and canonical_quote in canonical_page_text.get(page, ""):
                matched_page = page
                punctuation_normalized_quotes += 1
        if matched_page is None and len(quote) >= 8:
            matched_page = next((actual_page for actual_page, text in page_text.items() if quote in text), None)
        if matched_page is None and len(quote) >= 8:
            canonical_quote = canonical_evidence_text(quote)
            matched_page = next(
                (
                    actual_page
                    for actual_page, text in canonical_page_text.items()
                    if len(canonical_quote) >= 8 and canonical_quote in text
                ),
                None,
            )
            if matched_page is not None:
                punctuation_normalized_quotes += 1
        if matched_page is not None and matched_page != page:
            corrected_quote_pages += 1
        if len(quote) >= 8 and matched_page is not None:
            supports = [field for field in item["supports"] if field in SEMANTIC_FIELDS and field not in {"evidence_quotes", "uncertainties"}]
            verified.append({"page": matched_page, "quote": str(item["quote"]).strip(), "supports": supports})
    cleaned = {key: candidate.get(key) for key in SEMANTIC_FIELDS}
    cleaned["evidence_quotes"] = verified
    quote_supported_fields = {field for quote in verified for field in quote.get("supports", [])}
    automatically_verified_fields: set[str] = set()
    source_all = "".join(page_text.values())

    for field in ["hazard_types", "failure_modes", "lithology_terms", "stratum_terms", "causal_factors"]:
        retained = [value for value in cleaned.get(field, []) if isinstance(value, str) and normalize_text(value) in source_all]
        cleaned[field] = retained
        if retained:
            automatically_verified_fields.add(field)
    for field in [
        "hazard_body_type", "slope_structure", "slope_type", "material_nature",
        "overall_slope_deformation_severity", "geomorphology", "mechanism_summary",
        "protection_rationale",
    ]:
        value = cleaned.get(field)
        if isinstance(value, str) and normalize_text(value) in source_all:
            automatically_verified_fields.add(field)
    for field in [
        "slope_length_m", "slope_height_min_m", "slope_height_max_m",
        "slope_gradient_min_deg", "slope_gradient_max_deg", "slope_aspect_deg",
    ]:
        value = cleaned.get(field)
        if isinstance(value, (int, float)) and number_appears(value, source_all):
            automatically_verified_fields.add(field)
        elif value is not None and field not in quote_supported_fields:
            cleaned[field] = None
    object_fields = {
        "vegetation_condition": {"density", "has_deformation_sign", "description"},
        "river_relation": {"is_riverside", "bank", "channel_landform", "description"},
    }
    for field, required_keys in object_fields.items():
        value = cleaned.get(field)
        if not isinstance(value, dict) or not required_keys.issubset(value):
            cleaned[field] = None
        elif field in quote_supported_fields:
            automatically_verified_fields.add(field)

    verified_stability = []
    for item in cleaned["stability_conclusions"]:
        if (
            not isinstance(item, dict)
            or not {"condition", "safety_factor", "required_factor", "status", "analysis_scope", "source_kind"}.issubset(item)
            or item.get("condition") in (None, "")
            or (item.get("safety_factor") is None and item.get("status") in (None, ""))
        ):
            continue
        from .stability import normalize_stability_identity
        item = normalize_stability_identity(item)
        support_page = stability_support_page(item, page_text)
        if support_page is None:
            continue
        source_ids = {s.get("document_id") for s in samples if s["page"] == support_page}
        verified_stability.append({**item, "evidence_page": support_page,
                                   "evidence_document_id": next(iter(source_ids)) if len(source_ids) == 1 else None})
    cleaned["stability_conclusions"] = verified_stability
    if cleaned["stability_conclusions"]:
        automatically_verified_fields.add("stability_conclusions")
    cleaned["structural_planes"] = [
        item
        for item in cleaned["structural_planes"]
        if isinstance(item, dict)
        and {"name", "dip_direction", "dip_angle", "description"}.issubset(item)
        and normalize_text(str(item.get("name") or "")) in source_all
        and (item.get("dip_direction") is None or str(item["dip_direction"]) in source_all)
        and (item.get("dip_angle") is None or str(item["dip_angle"]) in source_all)
    ]
    if cleaned["structural_planes"]:
        automatically_verified_fields.add("structural_planes")
    object_schemas = {
        "deformation_observations": {"type", "location", "scale", "timing", "status", "description"},
        "hydrology_observations": {"type", "location", "timing", "value", "unit", "description"},
        "protection_records": {"measure", "status", "location", "parameters", "description"},
    }
    for field, required_keys in object_schemas.items():
        cleaned[field] = [item for item in cleaned[field] if isinstance(item, dict) and required_keys.issubset(item)]
        if field in {"deformation_observations", "hydrology_observations"}:
            domain = "deformation" if field == "deformation_observations" else "hydrology"
            cleaned[field] = [item for item in cleaned[field] if supported_observation(item, source_all, domain=domain)]
            targets = {s["station_context"] for s in samples if s.get("station_context")}
            if len(targets) == 1:
                target = next(iter(targets))
                cleaned[field] = [item for item in cleaned[field] if belongs_to_station(item, target)]
        if cleaned[field] and field in quote_supported_fields:
            automatically_verified_fields.add(field)
    supported_fields = quote_supported_fields | automatically_verified_fields
    submitted_fact_fields = {
        key for key, value in candidate.items() if key not in {"evidence_quotes", "uncertainties"} and value not in (None, "", [], {})
    }
    for field in submitted_fact_fields - supported_fields:
        cleaned[field] = [] if isinstance(candidate.get(field), list) else None
    field_evidence_coverage = len(submitted_fact_fields & supported_fields) / len(submitted_fact_fields) if submitted_fact_fields else 1.0
    accepted = len(verified) >= 2 and len(submitted_fact_fields & supported_fields) >= 5
    return cleaned if accepted else {}, {
        "accepted": accepted,
        "schema_valid": True,
        "submitted_quotes": len(submitted),
        "verified_quotes": len(verified),
        "evidence_quote_precision": round(len(verified) / len(submitted), 4) if submitted else 0.0,
        "submitted_fact_fields": len(submitted_fact_fields),
        "supported_fact_fields": len(submitted_fact_fields & supported_fields),
        "field_evidence_coverage": round(field_evidence_coverage, 4),
        "quote_supported_fields": sorted(quote_supported_fields),
        "automatically_verified_fields": sorted(automatically_verified_fields),
        "corrected_quote_pages": corrected_quote_pages,
        "punctuation_normalized_quotes": punctuation_normalized_quotes,
    }


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


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("º", "°")


def canonical_evidence_text(value: str) -> str:
    """Ignore layout punctuation while preserving the exact character sequence."""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalize_text(value), flags=re.UNICODE)


def number_appears(value: int | float, source: str) -> bool:
    try:
        target = float(value)
    except (TypeError, ValueError):
        return False
    return any(abs(float(token) - target) < 1e-9 for token in re.findall(r"\d+(?:\.\d+)?", source))


def stability_support_page(item: dict[str, Any], page_text: dict[int, str]) -> int | None:
    """Require condition, factor and status to co-occur in one local source window."""
    condition = normalize_text(str(item.get("condition") or ""))
    condition_core = re.sub(r"(工况|状态|条件|下)$", "", condition)
    condition_terms = [condition, condition_core]
    if "饱和" in condition:
        condition_terms.append("饱水")
    condition_terms = [term for term in dict.fromkeys(condition_terms) if len(term) >= 2]
    status = normalize_text(str(item.get("status") or ""))
    factor = item.get("safety_factor")
    required = item.get("required_factor")

    for page, source in page_text.items():
        candidate_positions: list[int] = []
        if factor is not None:
            try:
                target = float(factor)
            except (TypeError, ValueError):
                continue
            candidate_positions = [
                match.start()
                for match in re.finditer(r"\d+(?:\.\d+)?", source)
                if abs(float(match.group(0)) - target) < 1e-9
            ]
        else:
            candidate_positions = [
                index
                for term in condition_terms
                if (index := source.find(term)) >= 0
            ]
        for position in candidate_positions:
            window = source[max(0, position - 140) : min(len(source), position + 180)]
            scope = str(item.get("analysis_scope") or "")
            # “The number occurs on this page” cannot justify relabelling a
            # rock-body calculation as an overall slope calculation.
            sentence_start = max(source.rfind("。", 0, position), source.rfind("；", 0, position)) + 1
            local_subject = source[max(sentence_start, position - 100):position + 70]
            if scope in {"现状边坡", "整体边坡"} and "危岩体" in local_subject and "整体边坡" not in local_subject:
                continue
            if condition_terms and not any(term in window for term in condition_terms):
                continue
            if status and status not in window:
                continue
            if required is not None and not number_appears(required, window):
                continue
            return page
    return None
