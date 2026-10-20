"""Manual record persistence, overlays and audit history; schema and validation are separate."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import threading
import uuid
from typing import Any

from .config import PATHS, DemoPaths
from .storage import read_json, read_jsonl, write_json, write_jsonl
from .manual_contract import (MANUAL_FIELD_SPECS, FIELD_SPECS, DYNAMIC_FIELDS, RULE_FIELD_SPECS,
                              RULE_FIELDS, RULE_OPERATORS, manual_schema)
from .manual_validation import (normalize_field_value, normalize_rule_value, clean_text,
                                human_condition_text, normalize_observation_date, normalize_manual_rule, normalize_slope_input)


_WRITE_LOCK = threading.Lock()


def slope_override_record(slope_id: str, paths: DemoPaths = PATHS) -> dict[str, Any]:
    payload = read_json(paths.slope_overrides_json, {"records": {}})
    return deepcopy(payload.get("records", {}).get(slope_id, {"slope_id": slope_id, "fields": {}}))


def save_slope_override(slope_id: str, body: dict[str, Any], paths: DemoPaths = PATHS) -> dict[str, Any]:
    normalized, observed_at, note = normalize_slope_input(body)
    now = datetime.now().isoformat(timespec="seconds")
    record = {
        "slope_id": slope_id,
        "fields": normalized,
        "observed_at": observed_at or None,
        "note": note or None,
        "source_type": "manual_verified_input",
        "updated_at": now,
    }
    with _WRITE_LOCK:
        payload = read_json(paths.slope_overrides_json, {"schema_version": "1.0", "records": {}})
        payload.setdefault("records", {})[slope_id] = record
        payload["updated_at"] = now
        write_json(paths.slope_overrides_json, payload)
        append_audit(paths, "slope_override_saved", slope_id, {"fields": sorted(normalized), "observed_at": observed_at or None})
    return record


def delete_slope_override(slope_id: str, paths: DemoPaths = PATHS) -> bool:
    with _WRITE_LOCK:
        payload = read_json(paths.slope_overrides_json, {"schema_version": "1.0", "records": {}})
        existed = slope_id in payload.get("records", {})
        payload.setdefault("records", {}).pop(slope_id, None)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        write_json(paths.slope_overrides_json, payload)
        if existed:
            append_audit(paths, "slope_override_deleted", slope_id, {})
    return existed


def apply_slope_overrides(graph: dict[str, Any], paths: DemoPaths = PATHS) -> dict[str, Any]:
    result = deepcopy(graph)
    records = read_json(paths.slope_overrides_json, {"records": {}}).get("records", {})
    applied = 0
    for node in result.get("nodes", []):
        if node.get("type") != "Slope" or node.get("id") not in records:
            continue
        record = records[node["id"]]
        applied += 1
        values = deepcopy(record.get("fields", {}))
        props = node.setdefault("props", {})
        props.update(values)
        if "slope_height_m" in values:
            props["slope_height_min_m"] = values["slope_height_m"]
            props["slope_height_max_m"] = values["slope_height_m"]
            props["slope_height_raw"] = f"{values['slope_height_m']:g}m"
        if "slope_gradient_deg" in values:
            props["slope_gradient_min_deg"] = values["slope_gradient_deg"]
            props["slope_gradient_max_deg"] = values["slope_gradient_deg"]
            props["slope_gradient_raw"] = f"{values['slope_gradient_deg']:g}°"
        props["manual_override"] = {
            "fields": sorted(values), "observed_at": record.get("observed_at"),
            "updated_at": record.get("updated_at"), "source_type": record.get("source_type"),
        }
    result.setdefault("meta", {})["manual_override_count"] = applied
    return result


def merged_rule_library(base: dict[str, Any], paths: DemoPaths = PATHS) -> dict[str, Any]:
    result = deepcopy(base)
    manual_rows = read_json(paths.manual_rules_json, {"rules": []}).get("rules", [])
    result["rules"] = [*result.get("rules", []), *deepcopy(manual_rows)]
    stats = result.setdefault("stats", {})
    stats["rules"] = len(result["rules"])
    stats["manual_rules"] = len(manual_rows)
    stats["executable_manual_rules"] = sum(bool(row.get("execution_enabled")) for row in manual_rows)
    stats["risk_engine_candidates"] = sum(bool(row.get("risk_engine_candidate")) for row in result["rules"])
    return result


def save_manual_rule(body: dict[str, Any], paths: DemoPaths = PATHS) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    rule_id = str(body.get("id") or f"manual_rule_{uuid.uuid4().hex[:12]}")
    rule = normalize_manual_rule(body, rule_id=rule_id, now=now)
    with _WRITE_LOCK:
        payload = read_json(paths.manual_rules_json, {"schema_version": "1.0", "rules": []})
        rows = payload.setdefault("rules", [])
        previous = next((index for index, row in enumerate(rows) if row.get("id") == rule_id), None)
        if previous is None:
            rows.append(rule)
        else:
            rule["created_at"] = rows[previous].get("created_at", now)
            rows[previous] = rule
        rule.setdefault("created_at", now)
        payload["updated_at"] = now
        write_json(paths.manual_rules_json, payload)
        append_audit(paths, "manual_rule_saved", rule_id, {"execution_status": rule["execution_status"], "enabled": rule["execution_enabled"]})
    return rule


def delete_manual_rule(rule_id: str, paths: DemoPaths = PATHS) -> bool:
    if not rule_id.startswith("manual_rule_"):
        return False
    with _WRITE_LOCK:
        payload = read_json(paths.manual_rules_json, {"schema_version": "1.0", "rules": []})
        before = len(payload.get("rules", []))
        payload["rules"] = [row for row in payload.get("rules", []) if row.get("id") != rule_id]
        existed = len(payload["rules"]) != before
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        write_json(paths.manual_rules_json, payload)
        if existed:
            append_audit(paths, "manual_rule_deleted", rule_id, {})
    return existed


def append_audit(paths: DemoPaths, action: str, target_id: str, detail: dict[str, Any]) -> None:
    rows = read_jsonl(paths.manual_audit_jsonl)
    rows.append({
        "id": f"audit_{uuid.uuid4().hex[:12]}", "time": datetime.now().isoformat(timespec="seconds"),
        "action": action, "target_id": target_id, "detail": detail,
    })
    write_jsonl(paths.manual_audit_jsonl, rows[-5000:])
