"""Legacy fixed-schema source validation, preserved separately from orchestration."""
import re
from typing import Any
from .observation import supported_observation, belongs_to_station
from .semantic_contract import SEMANTIC_FIELDS


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
