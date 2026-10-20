"""Manual entry field definitions and public schema; no disk or HTTP I/O."""
from copy import deepcopy
from typing import Any


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


SLOPE_FIELD_GROUPS = [
    {"title": "工程与环境", "description": "边坡几何、材料与周边环境信息。",
     "fields": ["slope_type", "material_nature", "slope_height_m", "slope_gradient_deg", "slope_length_m",
                "slope_aspect_deg", "slope_structure_code", "structural_plane", "river_relation", "vegetation_condition"]},
    {"title": "近期观测与处置", "description": "近期巡检、降雨及监测信息须填写观测日期。",
     "fields": ["deformation_observation", "overall_slope_deformation_severity", "rainfall", "groundwater",
                "monitoring", "protection_condition", "maintenance"]},
    {"title": "稳定性与后果", "description": "保留评价工况，区分原报告结论与近期现场状态。",
     "fields": ["hazard_body", "stability_factor", "stability_status", "stability_condition", "consequence_level"]},
]


def manual_schema() -> dict[str, Any]:
    payload = {
        "slope_fields": MANUAL_FIELD_SPECS,
        "slope_field_groups": SLOPE_FIELD_GROUPS,
        "dynamic_fields": sorted(DYNAMIC_FIELDS),
        "rule_fields": RULE_FIELD_SPECS,
        "rule_operators": [{"code": code, "label": label} for code, label in RULE_OPERATORS.items()],
        "rule_outputs": [
            {"code": "P1", "label": "P1 优先复核"}, {"code": "P2", "label": "P2 重点复核"},
            {"code": "P3", "label": "P3 常规复核"}, {"code": "P4", "label": "P4 资料补录"},
        ],
        "boundary": "结构化规则只影响P1-P4人工复核优先级，不生成正式风险等级。复杂公式和自然语言逻辑可登记，但不会自动执行。",
    }
    return deepcopy(payload)
