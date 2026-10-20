"""Pure input normalization shared by manual records and rules."""
import math
from datetime import date
from typing import Any
from .manual_contract import RULE_FIELDS, RULE_OPERATORS, FIELD_SPECS, DYNAMIC_FIELDS


def normalize_field_value(spec: dict[str, Any], value: Any) -> Any:
    if value in (None, ""):
        return None
    if spec["type"] == "number":
        if isinstance(value, bool):
            raise ValueError(f"{spec['label']}必须为数字，不能为布尔值")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{spec['label']}必须为数字") from exc
        if not math.isfinite(number):
            raise ValueError(f"{spec['label']}必须为有限数字")
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
    return str("" if value is None else value).strip()[:limit]

def human_condition_text(conditions: list[dict[str, Any]], match: str) -> str:
    joiner = " 且 " if match == "all" else " 或 "
    parts = []
    for row in conditions:
        field = RULE_FIELDS.get(row.get("field"), {}).get("label", row.get("field") or "未知字段")
        operator = RULE_OPERATORS.get(row.get("operator"), row.get("operator") or "未知运算符")
        parts.append(f"{field} {operator} {row.get('value')}")
    return joiner.join(parts)


def normalize_observation_date(value: Any, *, required: bool = False) -> str:
    observed_at = str(value or "").strip()
    if required and not observed_at:
        raise ValueError("补录近期巡检、降雨、地下水、监测或防护现状时必须填写数据日期")
    if observed_at:
        try:
            parsed = date.fromisoformat(observed_at)
            if parsed.isoformat() != observed_at:
                raise ValueError("Noncanonical date")
        except ValueError as exc:
            raise ValueError("数据日期必须为 YYYY-MM-DD") from exc
    return observed_at


def normalize_manual_rule(body: dict[str, Any], *, rule_id: str, now: str) -> dict[str, Any]:
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
                if not math.isfinite(float(normalized_value)):
                    raise ValueError("nonfinite threshold")
            except (TypeError, ValueError, OverflowError):
                unsupported.append(f"条件{index}的比较值必须为有限数字")
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
    if not rule_id.startswith("manual_rule_"):
        raise ValueError("只能通过此接口修改人工规则")
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
    return rule


def normalize_slope_input(body: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
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
    observed_at = normalize_observation_date(body.get("observed_at"), required=bool(set(normalized) & DYNAMIC_FIELDS))
    note = clean_text(body.get("note"), 2000)
    return normalized, observed_at, note
