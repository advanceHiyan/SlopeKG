"""Bounded legacy text sampling; no provider calls, publication or visual routing."""
import re
from typing import Any
from .semantic_contract import SECTION_KEYWORDS


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
