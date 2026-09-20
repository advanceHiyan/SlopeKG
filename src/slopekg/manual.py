from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from .config import PATHS, DemoPaths
from .storage import ensure_dir, read_json, read_jsonl, write_json, write_jsonl


_WRITE_LOCK = threading.Lock()


MANUAL_FIELD_SPECS: list[dict[str, Any]] = [
    {"code": "slope_type", "label": "边坡类型", "type": "enum", "options": ["路堤", "路堑"]},
    {"code": "material_nature", "label": "材料类型", "type": "enum", "options": ["土质", "岩质", "土岩混合"]},
    {"code": "slope_height_m", "label": "坡高（m）", "type": "number", "min": 0, "max": 2000},
    {"code": "slope_gradient_deg", "label": "坡度（°）", "type": "number", "min": 0, "max": 90},
    {"code": "slope_length_m", "label": "边坡长度（m）", "type": "number", "min": 0, "max": 100000},
    {"code": "slope_aspect_deg", "label": "坡向（°）", "type": "number", "min": 0, "max": 360},
    {"code": "slope_structure_code", "label": "斜坡结构", "type": "enum", "options": ["顺向坡", "斜向坡", "横向坡", "逆向坡", "未确定"]},
    {"code": "structural_plane", "label": "主要结构面", "type": "text"},
    {"code": "hazard_body", "label": "灾害体/隐患体", "type": "enum", "options": ["滑坡", "崩塌", "危岩体", "泥石流", "路基垮塌", "路基下沉", "潜在不稳定边坡", "无明确灾害体"]},
    {"code": "deformation_observation", "label": "近期变形巡检", "type": "text", "dynamic": True},
    {"code": "overall_slope_deformation_severity", "label": "总体变形严重程度", "type": "enum", "options": ["无", "轻微", "中等", "严重"], "dynamic": True},
    {"code": "rainfall", "label": "近期降雨", "type": "text", "dynamic": True},
    {"code": "groundwater", "label": "近期地下水/渗水", "type": "text", "dynamic": True},
    {"code": "monitoring", "label": "监测数据摘要", "type": "text", "dynamic": True},
    {"code": "protection_condition", "label": "防护设施现状", "type": "enum", "options": ["完好", "轻微损坏", "局部失效", "严重损坏", "未核查"], "dynamic": True},
    {"code": "maintenance", "label": "养护处置历史", "type": "text"},
    {"code": "stability_factor", "label": "稳定系数 Fs", "type": "number", "min": 0, "max": 20},
    {"code": "stability_status", "label": "稳定状态", "type": "enum", "options": ["不稳定", "欠稳定", "基本稳定", "稳定", "满足要求"]},
    {"code": "stability_condition", "label": "稳定性计算工况", "type": "text"},
    {"code": "consequence_level", "label": "暴露后果等级", "type": "enum", "options": ["低", "中等", "高"]},
    {"code": "river_relation", "label": "临河关系", "type": "text"},
    {"code": "vegetation_condition", "label": "植被情况", "type": "enum", "options": ["茂密", "稀疏", "无"]},
]

FIELD_SPECS = {row["code"]: row for row in MANUAL_FIELD_SPECS}
DYNAMIC_FIELDS = {row["code"] for row in MANUAL_FIELD_SPECS if row.get("dynamic")}

RULE_FIELD_SPECS: list[dict[str, Any]] = [
    {"code": "hazard_type", "label": "灾害类型", "type": "text"},
    {"code": "hazard_body", "label": "灾害体/隐患体", "type": "text"},
    {"code": "slope_type", "label": "边坡类型", "type": "text"},
    {"code": "material_nature", "label": "材料类型", "type": "text"},
    {"code": "slope_height_m", "label": "坡高（m）", "type": "number"},
    {"code": "slope_gradient_deg", "label": "坡度（°）", "type": "number"},
    {"code": "slope_aspect_deg", "label": "坡向（°）", "type": "number"},
    {"code": "slope_structure_code", "label": "斜坡结构", "type": "text"},
    {"code": "overall_slope_deformation_severity", "label": "总体变形严重程度", "type": "text"},
    {"code": "observed_terms", "label": "异常迹象", "type": "text"},
    {"code": "stability_factor", "label": "稳定系数 Fs", "type": "number"},
    {"code": "stability_status", "label": "稳定状态", "type": "text"},
    {"code": "consequence_level", "label": "暴露后果等级", "type": "text"},
    {"code": "protection_condition", "label": "防护设施现状", "type": "text"},
    {"code": "rainfall", "label": "近期降雨", "type": "text"},
    {"code": "groundwater", "label": "近期地下水/渗水", "type": "text"},
]
RULE_FIELDS = {row["code"]: row for row in RULE_FIELD_SPECS}
RULE_OPERATORS = {
    "eq": "等于", "neq": "不等于", "contains": "包含", "in": "属于任一值",
    "gt": "大于", "gte": "大于等于", "lt": "小于", "lte": "小于等于", "exists": "已有值",
}


def manual_schema() -> dict[str, Any]:
    return {
        "slope_fields": MANUAL_FIELD_SPECS,
        "dynamic_fields": sorted(DYNAMIC_FIELDS),
        "rule_fields": RULE_FIELD_SPECS,
        "rule_operators": [{"code": code, "label": label} for code, label in RULE_OPERATORS.items()],
        "rule_outputs": [
            {"code": "P1", "label": "P1 优先复核"}, {"code": "P2", "label": "P2 重点复核"},
            {"code": "P3", "label": "P3 常规复核"}, {"code": "P4", "label": "P4 资料补录"},
        ],
        "boundary": "结构化规则只影响P1-P4人工复核优先级，不生成正式风险等级。复杂公式和自然语言逻辑可登记，但不会自动执行。",
    }


def slope_override_record(slope_id: str, paths: DemoPaths = PATHS) -> dict[str, Any]:
    payload = read_json(paths.slope_overrides_json, {"records": {}})
    return deepcopy(payload.get("records", {}).get(slope_id, {"slope_id": slope_id, "fields": {}}))


def save_slope_override(slope_id: str, body: dict[str, Any], paths: DemoPaths = PATHS) -> dict[str, Any]:
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("fields 必须是对象")
    unknown = sorted(set(fields) - set(FIELD_SPECS))
    if unknown:
        raise ValueError(f"不支持的补录字段：{', '.join(unknown)}")
    normalized = {}
    for code, value in fields.items():
        normalized_value = normalize_field_value(FIELD_SPECS[code], value)
        if normalized_value not in (None, ""):
            normalized[code] = normalized_value
    observed_at = str(body.get("observed_at") or "").strip()
    if set(normalized) & DYNAMIC_FIELDS:
        if not observed_at:
            raise ValueError("补录近期巡检、降雨、地下水、监测或防护现状时必须填写数据日期")
        try:
            date.fromisoformat(observed_at)
        except ValueError as exc:
            raise ValueError("数据日期必须为 YYYY-MM-DD") from exc
    note = clean_text(body.get("note"), 2000)
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
    for node in result.get("nodes", []):
        if node.get("type") != "Slope" or node.get("id") not in records:
            continue
        record = records[node["id"]]
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
    result.setdefault("meta", {})["manual_override_count"] = len(records)
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
    title = clean_text(body.get("title"), 200)
    if not title:
        raise ValueError("规则名称不能为空")
    conditions = body.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        conditions = []
    if len(conditions) > 10:
        raise ValueError("单条规则最多支持10个条件")
    normalized_conditions = []
    unsupported = []
    for index, condition in enumerate(conditions, start=1):
        if not isinstance(condition, dict):
            unsupported.append(f"条件{index}不是结构化对象")
            continue
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        value = condition.get("value")
        normalized_value = normalize_rule_value(operator, value)
        normalized_conditions.append({"field": field, "operator": operator, "value": normalized_value})
        if field not in RULE_FIELDS:
            unsupported.append(f"条件{index}字段 {field or '为空'} 不受支持")
        if operator not in RULE_OPERATORS:
            unsupported.append(f"条件{index}运算符 {operator or '为空'} 不受支持")
        if operator in {"gt", "gte", "lt", "lte"} and RULE_FIELDS.get(field, {}).get("type") != "number":
            unsupported.append(f"条件{index}只能对数值字段进行大小比较")
        if operator != "exists" and normalized_value in (None, "", []):
            unsupported.append(f"条件{index}缺少比较值")
        if operator in {"gt", "gte", "lt", "lte"}:
            try:
                float(normalized_value)
            except (TypeError, ValueError):
                unsupported.append(f"条件{index}的比较值必须为数字")
    match = str(body.get("match") or "all").strip()
    if match not in {"all", "any"}:
        unsupported.append("条件组合只支持 all 或 any")
    priority = str(body.get("priority") or "").upper().strip()
    if priority not in {"P1", "P2", "P3", "P4"}:
        unsupported.append("输出只支持P1-P4复核优先级")
    logic_note = clean_text(body.get("logic_note"), 4000)
    if not normalized_conditions:
        unsupported.append("没有可执行的结构化条件")
    requested_status = str(body.get("approval_status") or "draft").strip()
    approval_status = requested_status if requested_status in {"draft", "approved", "disabled"} else "draft"
    executable = not unsupported
    execution_enabled = executable and approval_status == "approved"
    rule_id = str(body.get("id") or f"manual_rule_{uuid.uuid4().hex[:12]}")
    if not rule_id.startswith("manual_rule_"):
        raise ValueError("只能通过此接口修改人工规则")
    now = datetime.now().isoformat(timespec="seconds")
    rule = {
        "id": rule_id,
        "title": title,
        "rule_kind": "manual_declarative",
        "hazard_type": None,
        "scenario": "人工配置的宏观复核规则",
        "inputs": sorted({row["field"] for row in normalized_conditions if row["field"]}),
        "conditions": normalized_conditions,
        "match": match,
        "condition_text": human_condition_text(normalized_conditions, match),
        "output_text": f"复核优先级={priority}" if priority else "未形成可执行输出",
        "output": {"screening_priority_code": priority, "reason": clean_text(body.get("reason"), 500) or title},
        "logic_note": logic_note or None,
        "exceptions": [],
        "document_code": "MANUAL",
        "page": None,
        "evidence_quote": logic_note or "人工录入规则，无PDF原文证据。",
        "extraction_method": "manual_input",
        "evidence_validation": "manual_source",
        "approval_status": approval_status,
        "execution_enabled": execution_enabled,
        "execution_status": "executable" if executable else "stored_not_executable",
        "unsupported_reasons": unsupported,
        "risk_engine_candidate": True,
        "updated_at": now,
    }
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
        append_audit(paths, "manual_rule_saved", rule_id, {"execution_status": rule["execution_status"], "enabled": execution_enabled})
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


def normalize_field_value(spec: dict[str, Any], value: Any) -> Any:
    if value in (None, ""):
        return None
    if spec["type"] == "number":
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{spec['label']}必须为数字") from exc
        if number < spec.get("min", number) or number > spec.get("max", number):
            raise ValueError(f"{spec['label']}超出允许范围")
        return number
    text = clean_text(value, 2000)
    if spec["type"] == "enum" and text not in spec.get("options", []):
        raise ValueError(f"{spec['label']}不在允许选项中")
    return text


def normalize_rule_value(operator: str, value: Any) -> Any:
    if operator == "exists":
        return True
    if operator == "in":
        if isinstance(value, list):
            return [clean_text(item, 200) for item in value if clean_text(item, 200)]
        return [part.strip() for part in str(value or "").replace("，", ",").split(",") if part.strip()]
    return clean_text(value, 500)


def clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def human_condition_text(conditions: list[dict[str, Any]], match: str) -> str:
    joiner = " 且 " if match == "all" else " 或 "
    parts = []
    for row in conditions:
        field = RULE_FIELDS.get(row.get("field"), {}).get("label", row.get("field") or "未知字段")
        operator = RULE_OPERATORS.get(row.get("operator"), row.get("operator") or "未知运算符")
        parts.append(f"{field} {operator} {row.get('value')}")
    return joiner.join(parts)


def append_audit(paths: DemoPaths, action: str, target_id: str, detail: dict[str, Any]) -> None:
    rows = read_jsonl(paths.manual_audit_jsonl)
    rows.append({
        "id": f"audit_{uuid.uuid4().hex[:12]}", "time": datetime.now().isoformat(timespec="seconds"),
        "action": action, "target_id": target_id, "detail": detail,
    })
    write_jsonl(paths.manual_audit_jsonl, rows[-5000:])
