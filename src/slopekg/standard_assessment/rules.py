"""Deterministic table operations; they do not decide landslide development."""
from __future__ import annotations

import json
from pathlib import Path

from .contract import literal, validate_fact


def load_rules(root: Path) -> dict:
    return json.loads((root / "data/standards/GBT40112-2021/landslide_current.review.json").read_text(encoding="utf-8"))


def coefficient_axis(fs: dict, fst: dict | None = None) -> dict:
    validate_fact(fs)
    if fs["variable_code"] != "fs" or fs["value"]["kind"] != "number":
        raise ValueError("coefficient comparison requires scalar fs")
    x = literal(fs["value"])
    if x <= 1:
        return {"axis": "不稳定", "expression": f"{x} <= 1", "input_fact_ids": [fs["fact_id"]]}
    if fst is None:
        return {"axis": None, "missing": ["fst"], "input_fact_ids": [fs["fact_id"]]}
    validate_fact(fst)
    if fst["variable_code"] != "fst" or fst["value"]["kind"] != "number" or fs["scope"] != fst["scope"]:
        raise ValueError("Fs/Fst must share object, time and scenario")
    y = literal(fst["value"])
    if y <= 1:
        raise ValueError("Fst must exceed 1 for this comparison")
    return {"axis": "欠稳定" if x <= y else "稳定", "expression": f"1 < {x} <= {y}" if x <= y else f"{x} > {y}",
            "input_fact_ids": [fs["fact_id"], fst["fact_id"]]}


def _intersects(lo, hi, lo_closed, hi_closed, a, b, a_closed, b_closed):
    left = max(lo, a)
    right = min(hi, b)
    if left != right:
        return left < right
    return ((left != lo or lo_closed) and (left != a or a_closed)
            and (right != hi or hi_closed) and (right != b or b_closed))


def metric_grades(fact: dict, rules: dict) -> list[str]:
    validate_fact(fact)
    spec = rules["table15"][fact["variable_code"]]
    value = fact["value"]
    if fact["normalization_status"] != "canonical" or value["kind"] not in {"number", "interval"} or value["unit"] != spec["unit"]:
        raise ValueError("table15 requires normalized numeric metric")
    if value["kind"] == "number":
        lo = hi = value["value"]
        lc = hc = True
    else:
        lo = value["lower"] if value["lower"] is not None else float("-inf")
        hi = value["upper"] if value["upper"] is not None else float("inf")
        lc, hc = value["lower_inclusive"], value["upper_inclusive"]
    a, b = spec["low_boundary"], spec["high_boundary"]
    bands = [(0, a, True, False, "危害小"), (a, b, True, True, "危害中等"), (b, float("inf"), False, False, "危害大")]
    return [label for x, y, xc, yc, label in bands if _intersects(lo, hi, lc, hc, x, y, xc, yc)]


def table15(basis: str, facts: list[dict], rules: dict) -> dict:
    if basis not in {"灾情", "险情"}:
        raise ValueError("assessment_basis must be 灾情 or 险情")
    eligible = [f for f in facts if rules["table15"].get(f["variable_code"], {}).get("basis") == basis]
    rows = [{"fact_id": f["fact_id"], "variable": f["variable_code"], "possible_grades": metric_grades(f, rules)} for f in eligible]
    possible = sorted({g for r in rows for g in r["possible_grades"]})
    conflict = len({tuple(r["possible_grades"]) for r in rows}) > 1
    return {"harm": possible[0] if len(possible) == 1 and not conflict else None, "possible_grades": possible,
            "indicator_results": rows, "conflict": conflict, "coverage": {"provided_indicators": len({f["variable_code"] for f in eligible}), "applicable_indicators": 2},
            "rule_ref": "GBT40112-T15", "input_fact_ids": [f["fact_id"] for f in eligible]}


def table17(developments: list[str], harms: list[str], rules: dict) -> dict:
    if any(d not in rules["table17"] for d in developments) or any(h not in {"危害大", "危害中等", "危害小"} for h in harms):
        raise ValueError("table17 vocabulary mismatch")
    branches = [{"development": d, "harm": h, "grade": rules["table17"][d][h]} for d in developments for h in harms]
    possible = sorted({r["grade"] for r in branches})
    return {"grade": possible[0] if len(possible) == 1 else None, "possible_grades": possible, "branches": branches}
