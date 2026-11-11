"""Compose a new review case from explicitly linked, frozen source cases."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date
import re
from sqlite3 import Connection
from typing import Callable

from .contract import CONTRACT_VERSION, applicability, identity, validate_fact
from .service import StandardAssessmentService, timestamp


COMPOSITION_VERSION = "workspace-composition-1"
SCENARIO_LABELS = {"natural": "天然工况", "rainstorm": "暴雨工况", "earthquake": "地震工况"}
SCENARIO_NAMES = {label: code for code, label in SCENARIO_LABELS.items()}
REFERENCE_ROLES = {"reference", "design", "recommendation", "prediction", "simulation", "unknown"}
SUBSCOPES = {
    "section", "zone", "subarea", "subscope", "unconfirmed", "component", "point", "instrument", "crack",
}
LOCAL_KINDS = ("断面", "剖面", "分区", "局部", "测点", "裂缝")


def period_key(scope: dict) -> tuple[str, str, str] | None:
    """Compare actual dates rather than display text or document-local citation IDs."""
    time = scope["time"]
    kind = time.get("kind")
    if kind not in {"instant", "report_period", "event_period"}:
        return None
    try:
        start, end = (date.fromisoformat(time.get(key)) for key in ("start", "end"))
    except (TypeError, ValueError):
        return None
    if start > end or (kind == "instant" and start != end):
        return None
    return kind, start.isoformat(), end.isoformat()


def scenario_key(scope: dict) -> str | None:
    scenario = scope["scenario"]
    code = scenario.get("code")
    if isinstance(code, str):
        key = code.strip()
        if key.casefold() == "not_applicable":
            return None
        if key.casefold() not in {"", "unknown", "unspecified", "未知", "未明确"}:
            return key
    raw = scenario.get("raw")
    if isinstance(raw, str):
        return SCENARIO_NAMES.get(raw.strip())
    return None


def _whole_subject(subject: dict) -> bool:
    ref = subject.get("source_object_ref")
    kind = str(subject.get("object_kind") or "").strip().casefold()
    return bool(isinstance(ref, str) and ref.strip()
                and not SUBSCOPES.intersection(re.split(r"\W+|_+", kind))
                and not any(marker in kind for marker in LOCAL_KINDS))


def supported_case(case: dict) -> bool:
    return case.get("hazard_type_supported") is True and _whole_subject(case["scope"]["subject"])


def _same_subject(a: dict, b: dict) -> bool:
    return (
        _whole_subject(a) and _whole_subject(b)
        and a["source_object_ref"] == b["source_object_ref"]
        and a.get("spatial_extent_ref") == b.get("spatial_extent_ref")
        and (not a.get("object_kind") or not b.get("object_kind") or a["object_kind"] == b["object_kind"])
        and (not a.get("canonical_object_id") or not b.get("canonical_object_id")
             or a["canonical_object_id"] == b["canonical_object_id"])
    )


def _case_scope(slope_id: str, anchor: dict, selected_scenario: str | None) -> dict:
    period = period_key(anchor)
    scenario = scenario_key(anchor)
    condition = {
        "code": scenario or "unspecified",
        "raw": anchor["scenario"].get("raw") or SCENARIO_LABELS.get(scenario, scenario) or "工况未明确",
    }
    if selected_scenario is not None:
        if not isinstance(selected_scenario, str) or selected_scenario not in SCENARIO_LABELS:
            raise ValueError("请选择列表中的评估条件")
        if scenario is not None:
            raise ValueError("所选资料已有明确评估条件，请按资料带入的条件评估")
        condition = {"code": selected_scenario, "raw": SCENARIO_LABELS[selected_scenario], "selected_by_operator": True}
    return {
        "subject": {"source_object_ref": slope_id, "canonical_object_id": slope_id, "object_kind": "landslide_body"},
        "time": {
            "kind": period[0] if period else "unknown",
            "start": period[1] if period else None,
            "end": period[2] if period else None,
            "raw": anchor["time"].get("raw") or (" ~ ".join(dict.fromkeys(period[1:])) if period else "时期未明确"),
        },
        "scenario": condition,
    }


def _frozen_context(context: dict, source_hashes: dict) -> bool:
    sha = context.get("source_sha256")
    return (
        isinstance(sha, str) and len(sha) == 64
        and source_hashes.get(context.get("source_path")) == sha
        and any(type(span.get("file_page_1_based")) is int and span["file_page_1_based"] > 0
                and isinstance(span.get("quote"), str) and span["quote"].strip() for span in context.get("spans", []))
    )


def _scope_exclusions(fact: dict, origin: dict, target: dict) -> list[str]:
    if fact.get("derivation_id") or fact["origin_kind"] in {"rule", "llm"} or fact["variable_code"] in {"hazard_level", "stability_axis"}:
        return ["prior_derivation"]
    reasons = []
    scope = fact["scope"]
    if not _same_subject(scope["subject"], origin["subject"]):
        reasons.append("different_or_unresolved_object")
    period, origin_period = period_key(scope), period_key(origin)
    if period is None or origin_period is None:
        reasons.append("period_unresolved")
    elif period != origin_period or period != period_key(target):
        reasons.append("different_period")
    scenario, origin_scenario = scenario_key(scope), scenario_key(origin)
    target_scenario = scenario_key(target)
    # A choice defines the target; only a fact's own explicit condition can match it.
    if scenario is None or (origin_scenario is None and not target["scenario"].get("selected_by_operator")):
        reasons.append("scenario_unresolved")
    elif scenario != target_scenario or (origin_scenario is not None and scenario != origin_scenario):
        reasons.append("different_scenario")
    if not isinstance(fact["role"], str) or not fact["role"].strip() or fact["role"] in REFERENCE_ROLES:
        reasons.append("reference_role")
    return reasons


def _pending_reasons(fact: dict, target: dict, frozen_contexts: set[str]) -> list[str]:
    checks = applicability(fact, {"scope": target})["dimensions"]
    reasons = []
    support = fact["source_check"]["content"]
    if support not in {"literal_matched", "model_supported", "human_supported"}:
        reasons.append("source_support_unresolved")
    if fact["evidence_refs"]:
        if not set(fact["evidence_refs"]) <= frozen_contexts:
            reasons.append("source_evidence_unverified")
    elif support != "human_supported":
        reasons.append("source_evidence_unverified")
    if checks.get("standard") == "unresolved":
        reasons.append("standard_unconfirmed")
    if checks["type"] != "compatible":
        reasons.append("value_unknown" if fact["value"] is None else "value_type_unresolved")
    value = fact["value"] or {}
    if fact["variable_code"] == "fst" and value.get("kind") == "number" and value["value"] <= 1:
        reasons.append("invalid_stability_threshold")
    return reasons


def _canonical_numbers(value: dict | None) -> None:
    # Numeric fields may originate as int from Agent or float from Knowledge.
    # Integral values compare equally across those wire representations.
    if not value:
        return
    keys = {"number": ("value",), "interval": ("lower", "upper")}.get(value["kind"], ())
    for key in keys:
        scalar = value.get(key)
        if type(scalar) is float and scalar.is_integer():
            value[key] = int(scalar)


def _unique_record(records: dict, key: str, row: dict) -> None:
    old = records.setdefault(row[key], row)
    if old != row:
        raise ValueError(f"同一条来源记录标识存在不同内容：{row[key]}")


def _compose_facts(sources: list[dict], scope: dict):
    records, contexts, facts = {}, {}, {}
    trace, candidates, evidence = [], [], defaultdict(list)
    for source in sources:
        origin = source["case"]
        source_ids = {row["record_ref_id"] for row in source["source_records"]}
        context_ids = {row["context_id"] for row in source["contexts"]}
        frozen_contexts = {row["context_id"] for row in source["contexts"] if _frozen_context(row, origin["source_hashes"])}
        for row in source["source_records"]:
            _unique_record(records, "record_ref_id", row)
        for row in source["contexts"]:
            _unique_record(contexts, "context_id", row)
        for original in source["facts"]:
            validate_fact(original)
            reasons = _scope_exclusions(original, origin["scope"], scope)
            refs = set(original["evidence_refs"])
            if not set(original["source_record_refs"]) <= source_ids or not refs <= context_ids:
                reasons.append("source_record_missing")
            row = {
                "workspace": source["workspace"], "source_case_id": origin["case_id"],
                "source_fact_id": original["fact_id"], "source_record_refs": original["source_record_refs"],
                "variable_code": original["variable_code"], "value": original["value"], "scope": original["scope"],
                "evidence_refs": original["evidence_refs"], "role": original["role"],
                "source_check": original["source_check"], "source_review_status": original["review_status"],
                "source_supersedes_fact_id": original["supersedes_fact_id"],
                "fact_id": None, "status": "excluded", "reasons": reasons,
            }
            if not reasons:
                candidates.append((original, row, source["link"]["link_id"], frozen_contexts))
            trace.append(row)
            # Derived users of a quote do not establish its scope. For original
            # assertions, a context shared across incompatible scopes is view-only.
            if "prior_derivation" not in reasons:
                for context_id in refs & context_ids:
                    evidence[context_id].append(row if not reasons and context_id in frozen_contexts else None)
    trusted_contexts = {context_id for context_id, rows in evidence.items() if None not in rows}
    for original, row, link_id, frozen_contexts in candidates:
        fact = deepcopy(original)
        fact.update(scope=deepcopy(scope), review_status="pending", supersedes_fact_id=None)
        fact["workspace_origin"] = {"workspace": row["workspace"], "case_id": row["source_case_id"],
                                    "source_fact_id": original["fact_id"], "link_id": link_id}
        if fact["variable_code"] in {"fs", "fst"}:
            # Preserve pairing within a source calculation after remapping the whole body.
            fact["scope"]["calculation_source"] = {"workspace": row["workspace"], "case_id": row["source_case_id"]}
        _canonical_numbers(fact["value"])
        pending = _pending_reasons(fact, scope, frozen_contexts)
        if set(fact["evidence_refs"]) & (frozen_contexts - trusted_contexts):
            pending.append("ambiguous_evidence_scope")
        if pending and fact["source_check"]["content"] != "unsupported":
            fact["source_check"]["content"] = "unchecked"
        fact["fact_id"] = identity("workspacefact_", {k: v for k, v in fact.items() if k != "fact_id"})
        _unique_record(facts, "fact_id", fact)
        row.update(fact_id=fact["fact_id"], status="requires_review" if pending else "included", reasons=pending)
    evidence_scopes = {
        context_id: {
            "scope": scope, "fact_ids": sorted({row["fact_id"] for row in rows}),
            "source_fact_ids": sorted({row["source_fact_id"] for row in rows}),
            "roles": sorted({row["role"] for row in rows}),
            "variables": sorted({row["variable_code"] for row in rows}),
        } for context_id, rows in evidence.items() if context_id in trusted_contexts
    }
    return records, contexts, facts, trace, evidence_scopes


def compose_case(
    api: StandardAssessmentService,
    slope: dict,
    sources: list[dict],
    anchor_case_id: str | None = None,
    *,
    selected_scenario: str | None = None,
    guard: Callable[[Connection], None] | None = None,
    creation: dict | None = None,
) -> dict:
    """Evaluate a slope case against its selected source snapshots, with provenance."""
    if not sources:
        raise ValueError("至少选择一份已关联的评估来源")
    if len({source["case"]["case_id"] for source in sources}) != len(sources):
        raise ValueError("同一来源案例不能重复选择")
    sources = sorted(sources, key=lambda source: (source["workspace"], source["case"]["case_id"]))
    anchor = next((source["case"] for source in sources if source["case"]["case_id"] == anchor_case_id), None)
    if anchor_case_id is not None and anchor is None:
        raise ValueError("评估时期须来自所选资料")
    anchor = anchor or sources[0]["case"]
    scope = _case_scope(slope["id"], anchor["scope"], selected_scenario)
    source_metadata = []
    source_hashes = {}
    test_flags = {bool(source.get("test_only")) for source in sources}
    if len(test_flags) > 1:
        raise ValueError("测试资料和业务资料须分别评估")
    for source in sources:
        origin = source["case"]
        origin_scope = origin["scope"]
        if not supported_case(origin):
            raise ValueError("评估来源须对应可评估的整体滑坡对象")
        if len(sources) > 1 and (period_key(scope) is None or period_key(origin_scope) != period_key(scope)):
            raise ValueError("所选资料须有相同的明确时期，其他时期请单独评估")
        if len(sources) > 1 and (scenario_key(scope) is None or scenario_key(origin_scope) != scenario_key(scope)):
            raise ValueError("所选资料须有相同的明确工况，其他工况请单独评估")
        for path, digest in origin["source_hashes"].items():
            if source_hashes.setdefault(path, digest) != digest:
                raise ValueError("同一路径的来源版本不同，请分开评估")
        lineage = origin.get("recognition_lineage") or {}
        source_metadata.append({
            "workspace": source["workspace"], "case_id": origin["case_id"],
            "resolution_version": origin["resolution_version"], "link_id": source["link"]["link_id"],
            "link": source["link"],
            "label": source.get("label") or lineage.get("subject_label") or origin["target_ref"],
            "scope": origin_scope, "source_hashes": origin["source_hashes"], "recognition_lineage": lineage,
            "test_only": bool(source.get("test_only")),
        })

    case_id = identity("workspacecase_", {
        "version": COMPOSITION_VERSION, "slope_id": slope["id"],
        "sources": [{k: src[k] for k in ("workspace", "case_id", "resolution_version", "link_id", "source_hashes")}
                    for src in source_metadata],
        "period": period_key(scope), "scenario": scenario_key(scope), "test_only": True in test_flags,
    })
    with api.store.transaction() as db:
        if guard is not None:
            guard(db)
        try:
            case = api.store.get("assessment_case", case_id, db)
        except KeyError:
            records, contexts, facts, trace, evidence = _compose_facts(sources, scope)
            case = {
                "case_id": case_id, "contract_version": CONTRACT_VERSION, "target": "landslide_current",
                "target_ref": slope["id"], "workspace_slope_id": slope["id"], "workspace_slope_label": slope["label"],
                "scope": scope, "resolution_version": 1, "fact_ids": list(facts), "context_ids": list(contexts),
                "source_hashes": source_hashes, "source_snapshot_id": identity("snapshot_", source_hashes),
                "requested_output": "table17_candidate", "hazard_type_supported": True, "selections": {},
                "created_at": timestamp(), "source_coverage": "confirmed_workspace_links_selected_snapshots",
                "workspace_sources": source_metadata, "workspace_anchor_case_id": anchor["case_id"],
                "workspace_fact_trace": trace, "workspace_evidence": evidence, "test_only": True in test_flags,
                "adapter_version": COMPOSITION_VERSION,
            }
            if creation:
                case['workspace_creation'] = creation
            for table, rows in (("source_record", records), ("evidence_context", contexts), ("fact_assertion", facts)):
                for row in rows.values():
                    api.store.put(table, row, db)
            api.store.put("assessment_case", case, db)
        current = any(
            result["case_id"] == case_id and result["resolution_version"] == case["resolution_version"]
            and result["rule_snapshot_hash"] == api.rules_hash
            for result in api.store.rows("assessment_result", db)
        )
        if not current:
            api.evaluate_assessment({"contract_version": CONTRACT_VERSION, "case_id": case_id,
                                     "expected_resolution_version": case["resolution_version"]}, db=db)
    return api.get_case(case_id)
