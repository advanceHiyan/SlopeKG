"""The executable subset of the 0.3 review contract; no engineering defaults."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

CONTRACT_VERSION = "slopekg.standard-assessment/0.3.0-review"
ADAPTER_VERSION = "standard-assessment-adapter-3"
DEVELOPMENT = ["强发育", "中等发育", "弱发育"]
HARM = ["危害大", "危害中等", "危害小"]
HAZARD = ["危险性大", "危险性中等", "危险性小"]
UNITS = {"fs": "1", "fst": "1", "body_mean_angle": "deg", "deaths": "person",
         "threatened_people": "person", "direct_loss_wan": "10k_CNY", "potential_loss_wan": "10k_CNY"}
VOCABS = {"development": ("gb-landslide-development@2021", DEVELOPMENT),
          "harm": ("gb-harm@2021", HARM), "hazard_level": ("gb-hazard@2021", HAZARD),
          "assessment_basis": ("assessment-basis@2021", ["灾情", "险情"])}


class VersionConflict(ValueError):
    pass


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(prefix: str, value: Any) -> str:
    return prefix + hashlib.sha256(encoded(value).encode()).hexdigest()[:24]


def number(value: float, unit: str, qualifier: str = "exact_as_reported") -> dict:
    return {"kind": "number", "value": value, "unit": unit, "qualifier": qualifier}


def enumeration(variable: str, label: str) -> dict:
    return {"kind": "enum", "code": label, "label": label, "vocabulary": VOCABS[variable][0]}


def literal(value: dict | None) -> Any:
    if value is None:
        return None
    if value["kind"] == "number":
        return value["value"]
    if value["kind"] == "enum":
        return value["label"]
    if value["kind"] == "text":
        return value["text"]
    if value["kind"] == "boolean":
        return value["value"]
    return copy.deepcopy(value)


def validate_fact(fact: dict) -> None:
    required = {"fact_id", "contract_version", "variable_code", "value", "availability", "normalization_status",
                "scope", "semantic_kind", "origin_kind", "role", "source_record_refs", "evidence_refs",
                "source_standard", "source_check", "review_status", "supersedes_fact_id"}
    missing = required - fact.keys()
    if missing:
        raise ValueError(f"missing fact fields: {sorted(missing)}")
    if fact["contract_version"] != CONTRACT_VERSION:
        raise ValueError("unsupported contract_version")
    if not fact["source_record_refs"] and not fact.get("derivation_id"):
        raise ValueError("fact must retain a source or derivation")
    if fact["normalization_status"] not in {"canonical", "raw", "unmapped"}:
        raise ValueError("invalid normalization_status")
    for dim in ("subject", "time", "scenario"):
        if dim not in fact["scope"]:
            raise ValueError(f"missing scope.{dim}")
    value = fact["value"]
    if value is None:
        if fact["availability"] not in {"explicit_unknown", "explicit_not_applicable"}:
            raise ValueError("null is not a numeric zero or an absent observation")
        return
    if fact["availability"] not in {"present", "explicit_none"}:
        raise ValueError("a present value cannot be marked unknown")
    kind = value.get("kind")
    variable = fact["variable_code"]
    if kind in {"number", "interval"}:
        if not value.get("unit"):
            raise ValueError("numeric unit required")
        values = [value.get("value")] if kind == "number" else [value.get("lower"), value.get("upper")]
        if kind == "interval" and all(x is None for x in values):
            raise ValueError("unbounded unknown is not a numeric interval")
        for x in values:
            if x is None and kind == "interval":
                continue
            if type(x) not in {int, float} or not math.isfinite(x):
                raise ValueError("finite JSON number required; boolean/string rejected")
            if variable in {"deaths", "threatened_people", "direct_loss_wan", "potential_loss_wan", "fs", "fst"} and x < 0:
                raise ValueError("negative metric")
            if variable in {"deaths", "threatened_people"} and int(x) != x:
                raise ValueError("people must be integers")
        if kind == "interval":
            lo, hi = values
            if lo is not None and hi is not None and (lo > hi or (lo == hi and not (value.get("lower_inclusive") and value.get("upper_inclusive")))):
                raise ValueError("invalid interval endpoints")
            if any(type(value.get(k)) is not bool for k in ("lower_inclusive", "upper_inclusive")):
                raise ValueError("interval inclusivity must be explicit")
        if fact["normalization_status"] == "canonical" and variable in UNITS and value["unit"] != UNITS[variable]:
            raise ValueError("wrong canonical unit")
    elif kind == "enum":
        if variable in VOCABS:
            vocabulary, labels = VOCABS[variable]
            if value.get("vocabulary") != vocabulary or value.get("label") not in labels or value.get("code") != value.get("label"):
                raise ValueError("enum vocabulary/label mismatch")
    elif kind == "text":
        if not isinstance(value.get("text"), str) or not value["text"]:
            raise ValueError("nonempty text required")
    elif kind == "boolean":
        if type(value.get("value")) is not bool or not fact.get("feature_code"):
            raise ValueError("boolean requires an explicit predicate")
    else:
        raise ValueError("value kind not implemented in bounded pilot")


def fact_record(variable: str, value: dict | None, scope: dict, *, source_refs: list, evidence_refs: list,
                origin: str, semantic: str = "observation", role: str = "observation", feature: str | None = None,
                source_standard: str = "unknown", support: str = "unchecked", normalization: str = "canonical",
                derivation_id: str | None = None, supersedes: str | None = None) -> dict:
    result = {"contract_version": CONTRACT_VERSION, "variable_code": variable, "feature_code": feature,
              "value": value, "availability": "present" if value is not None else "explicit_unknown",
              "normalization_status": normalization, "scope": copy.deepcopy(scope), "semantic_kind": semantic,
              "origin_kind": origin, "role": role, "source_record_refs": source_refs, "evidence_refs": evidence_refs,
              "source_standard": source_standard, "source_check": {"locator": "located" if evidence_refs else "unlocated", "content": support},
              "review_status": "pending", "supersedes_fact_id": supersedes, "adapter_version": ADAPTER_VERSION}
    if derivation_id:
        result["derivation_id"] = derivation_id
    result["fact_id"] = identity("fact_", result)
    validate_fact(result)
    return result


def applicability(fact: dict, case: dict) -> dict:
    """Compare each dimension. Unknown is never silently promoted to compatible."""
    fs, cs = fact["scope"], case["scope"]
    checks = {}
    for dim in ("subject", "time", "scenario"):
        a, b = fs[dim], cs[dim]
        if dim == "subject":
            # Equal source-local body identifiers suffice for a report replay;
            # canonical cross-document identity remains a separate mapping issue.
            checks[dim] = "compatible" if a.get("source_object_ref") == b.get("source_object_ref") else "incompatible"
        elif dim == "time":
            same_known = a.get('kind') not in {None,'unknown'} and a.get('kind') == b.get('kind') and all(a.get(k) is not None and a.get(k)==b.get(k) for k in ('start','end'))
            if a == b or same_known:
                checks[dim] = "unresolved" if a.get("kind") == "unknown" else "compatible"
            elif a.get("kind") == "not_applicable":
                checks[dim] = "compatible"
            else:
                checks[dim] = "unresolved" if "unknown" in {a.get("kind"), b.get("kind")} else "incompatible"
        else:
            if a.get("code") == "not_applicable" or a.get('code') == b.get('code') and a.get("code") not in {None,"unspecified"}:
                checks[dim] = "compatible"
            elif "unspecified" in {a.get("code"), b.get("code")}:
                checks[dim] = "unresolved"
            else:
                checks[dim] = "incompatible"
    checks["role"] = "incompatible" if fact["role"] in {"reference", "recommendation", "design", "prediction", "simulation"} else "compatible"
    checks["support"] = "incompatible" if fact["source_check"]["content"] == "unsupported" else ("unresolved" if fact["source_check"]["content"] == "unchecked" else "compatible")
    if fact.get("derivation_id") and checks["support"] != "incompatible":
        # A grounded conditional derivation is a usable conditional input.
        # Its uncertainty is retained on basis_status below, independently of
        # whether its provenance supports using the proposed value at all.
        checks["support"] = "compatible" if fact.get("basis_status") in {"supported", "conditional"} else "unresolved"
    checks["type"] = "compatible" if fact["normalization_status"] == "canonical" and fact["value"] is not None else "unresolved"
    if fact["value"] is not None:
        if fact["variable_code"] in UNITS and fact["value"]["kind"] not in {"number", "interval"}:
            checks["type"] = "incompatible"
        if fact["variable_code"] in VOCABS and fact["value"]["kind"] != "enum":
            checks["type"] = "incompatible"
        if fact["value"].get("qualifier") in {"approximate", "rounded", "unspecified"}:
            checks["precision"] = "unresolved"
    if fact["variable_code"] in {"development", "harm"}:
        checks["standard"] = "compatible" if fact["source_standard"] == "GB/T 40112-2021" else "unresolved"
    state = "incompatible" if "incompatible" in checks.values() else ("unresolved" if "unresolved" in checks.values() else "compatible")
    basis = "supported" if state == "compatible" else "conditional"
    if fact["origin_kind"] == "llm" and fact["review_status"] != "accepted":
        basis = "conditional"
    if fact.get("basis_status") == "conditional":
        basis = "conditional"
    return {"fact_id": fact["fact_id"], "status": state, "dimensions": checks, "basis_status": basis}
