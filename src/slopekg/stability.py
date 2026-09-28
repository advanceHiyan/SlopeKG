"""Shared identity and scope rules for extracted stability calculations."""
from __future__ import annotations

import re


def normalize_stability_identity(item: dict) -> dict:
    result = dict(item)
    scope = str(result.get("analysis_scope") or "")
    kind = str(result.get("source_kind") or "")
    if re.fullmatch(r"WY\s*\d+", scope, re.I):
        result.setdefault("body_id", re.sub(r"\s+", "", scope).upper())
        result["analysis_scope"] = "危岩体"
    if kind in {"坠落式", "滑移式", "倾倒式"}:
        result.setdefault("failure_mode", kind)
        result["source_kind"] = "报告稳定性计算"
    if result.get("body_id") or result.get("failure_mode"):
        # Preserve an explicit contradiction for review instead of relabelling it.
        if result.get("analysis_scope") in {None, "", "危岩体"}:
            result["analysis_scope"] = "危岩体"
    return result


def eligible_whole_slope(item: dict) -> bool:
    return (not item.get("quality_issues")
            and not item.get("body_id") and not item.get("failure_mode")
            and item.get("analysis_scope") in {"整体边坡", "现状边坡"})
