from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any


RISK_ENGINE_VERSION = "macro-screening-v1.0"
PRIORITY_LABELS = {
    "P1": "优先复核",
    "P2": "重点复核",
    "P3": "常规复核",
    "P4": "资料补录",
}
CURRENT_STABILITY_STATUSES = {"不稳定", "欠稳定", "基本稳定", "稳定"}
ALARM_TERMS = ("贯通裂缝", "新裂缝", "裂缝发展", "明显位移", "加速变形", "管涌", "渗水", "隆起", "坍塌")
GAP_GUIDANCE = {
    "deformation_observation": {
        "priority": 0,
        "priority_label": "第一优先",
        "accuracy_role": "近期失稳迹象",
        "why": "裂缝、位移、滑塌、掉块是否正在发展，是判断当前风险最直接的证据。",
        "how": "补充带日期的巡检记录，至少记录位置、规模、是否发展及现场照片。",
    },
    "overall_slope_deformation_severity": {
        "priority": 0,
        "priority_label": "第一优先",
        "accuracy_role": "当前变形程度",
        "why": "总体变形严重程度直接影响复核优先级，不能由历史灾害名称代替。",
        "how": "按无、轻微、中等、严重补录整处边坡的总体变形，并保留判定日期和依据。",
    },
    "rainfall": {
        "priority": 0,
        "priority_label": "第一优先",
        "accuracy_role": "降雨触发条件",
        "why": "现有多处边坡在暴雨或饱和工况下稳定性明显下降，近期雨量决定不利工况是否正在接近。",
        "how": "接入附近雨量站或现场雨量计，至少提供24小时、72小时、7日累计雨量和数据时间。",
    },
    "groundwater": {
        "priority": 0,
        "priority_label": "第一优先",
        "accuracy_role": "地下水触发条件",
        "why": "地下水位、渗水和孔压变化会降低有效抗剪强度，是降雨后风险变化的关键变量。",
        "how": "补充地下水位、渗水/管涌位置和变化趋势；有条件时接入孔压或水位时序。",
    },
    "monitoring": {
        "priority": 0,
        "priority_label": "第一优先",
        "accuracy_role": "变化速度",
        "why": "位移和裂缝变化速率比单次静态照片更能识别正在发展的变形。",
        "how": "优先记录位移、裂缝宽度、倾角或GNSS时序，必须包含观测时间、单位和质量标记。",
    },
    "stability": {
        "priority": 0,
        "priority_label": "第一优先",
        "accuracy_role": "稳定性基准",
        "why": "缺少现状及不利工况稳定性计算时，只能根据灾害名称和几何条件作粗筛。",
        "how": "补充正确关联到边坡和断面的天然、暴雨/饱和等工况Fs、Fst、专家结论和报告日期。",
    },
    "protection_condition": {
        "priority": 1,
        "priority_label": "第二优先",
        "accuracy_role": "防护有效性",
        "why": "设计文件只能证明计划采用的措施，不能证明工程已经实施且目前完好。",
        "how": "巡检挡墙、锚杆框架、截排水和防护网，记录完好、局部损坏或失效及检查日期。",
    },
    "structural_plane": {
        "priority": 1,
        "priority_label": "第二优先",
        "accuracy_role": "破坏模式",
        "why": "结构面产状及其与坡向的组合关系决定岩质边坡更可能滑移、倾倒还是崩塌。",
        "how": "补录主要结构面倾向、倾角、间距、延伸、充填和与坡向关系。",
    },
    "slope_height_m": {
        "priority": 1,
        "priority_label": "第二优先",
        "accuracy_role": "坡体规模",
        "why": "坡高影响潜在失稳规模和几何不利程度。",
        "how": "从测绘、断面图或现场测量补录最小/最大坡高及单位。",
    },
    "slope_gradient_deg": {
        "priority": 1,
        "priority_label": "第二优先",
        "accuracy_role": "几何不利程度",
        "why": "坡度是判断重力作用和危岩崩塌条件的重要基础参数。",
        "how": "从断面图或现场测量补录代表性坡度范围，不使用示意图估读值。",
    },
    "material_nature": {
        "priority": 1,
        "priority_label": "第二优先",
        "accuracy_role": "模型选择",
        "why": "土质、岩质和土岩混合边坡的破坏机制及所需参数不同。",
        "how": "明确边坡材料类别，并关联地层、岩性和风化程度证据。",
    },
    "hazard_body": {
        "priority": 1,
        "priority_label": "第二优先",
        "accuracy_role": "灾害对象",
        "why": "未明确灾害体类型时，无法选择对应的稳定性规则和复核重点。",
        "how": "确认滑坡、崩塌、危岩体、泥石流或其他病害对象，并记录范围和规模。",
    },
    "exposure": {
        "priority": 1,
        "priority_label": "第二优先",
        "accuracy_role": "后果等级",
        "why": "没有交通量、人员和重要设施暴露数据，只能判断失稳关注，不能形成完整风险等级。",
        "how": "补充交通量、道路中断影响、邻近居民及桥隧、管线、铁塔等重要设施。",
    },
    "maintenance": {
        "priority": 2,
        "priority_label": "基础补录",
        "accuracy_role": "历史变化",
        "why": "既往清危、加固和重复病害可帮助判断问题是否持续或复发。",
        "how": "补录处置时间、措施、范围、效果和之后是否再次出现变形。",
    },
    "slope_type": {
        "priority": 2,
        "priority_label": "基础补录",
        "accuracy_role": "工程类型",
        "why": "路堤、路堑的受力条件和常见破坏模式不同。",
        "how": "从设计资料或现场确认路堤、路堑及填挖结合情况。",
    },
    "slope_aspect_deg": {
        "priority": 2,
        "priority_label": "基础补录",
        "accuracy_role": "结构组合",
        "why": "坡向用于判断岩层和结构面相对坡面的顺向、逆向及切向关系。",
        "how": "补录坡面倾向角，并与结构面产状使用同一方位基准。",
    },
    "river_relation": {
        "priority": 2,
        "priority_label": "基础补录",
        "accuracy_role": "坡脚冲刷条件",
        "why": "临河位置和凹岸冲刷可能改变坡脚支撑及地下水条件。",
        "how": "补录是否临河、左/右岸以及凹岸、凸岸或直线段。",
    },
    "vegetation_condition": {
        "priority": 2,
        "priority_label": "基础补录",
        "accuracy_role": "地表状态",
        "why": "植被覆盖和变形迹象可辅助识别近期扰动，但不能单独判定稳定性。",
        "how": "记录茂密、稀疏或无，并单独记录植被是否存在倾斜、拉裂等变形迹象。",
    },
    "risk_rules": {
        "priority": 3,
        "priority_label": "制度项",
        "accuracy_role": "结论发布",
        "why": "已审核规则决定结果能否作为正式结论发布，本身不替代现场数据。",
        "how": "由研究院审核规则适用对象、阈值、工况和版本后发布。",
    },
}


def build_risk_screening(
    graph: dict[str, Any],
    rule_library: dict[str, Any] | None = None,
    completeness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a conservative review-priority screen from currently available facts.

    This deliberately does not turn missing fields into zero-risk values and does
    not publish a formal risk level without both likelihood and consequence data.
    """
    rule_library = rule_library or {}
    completeness = completeness or {}
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    evidence = {row.get("id"): row for row in graph.get("evidence", [])}
    nodes_by_id = {row.get("id"): row for row in nodes}
    reports = {row.get("slope_id"): row for row in completeness.get("reports", [])}
    assessments = []
    for slope in nodes:
        if slope.get("type") != "Slope":
            continue
        related = related_facts(slope["id"], edges, nodes_by_id, evidence)
        assessments.append(
            assess_slope(
                slope,
                related,
                reports.get(slope["id"]),
                rule_library,
            )
        )
    assessments.sort(key=lambda row: (priority_order(row["screening_priority_code"]), row["slope_label"]))
    counts = Counter(row["screening_priority_code"] for row in assessments)
    stability_counts = Counter(row["stability_concern"] for row in assessments)
    return {
        "schema_name": "slope_macro_risk_screening",
        "engine_version": RISK_ENGINE_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "assessment_mode": "macro_screening_for_field_review",
        "formal_risk_assessment_enabled": False,
        "rule_library_status": rule_library.get("publication_status", "not_generated"),
        "rule_library_execution_enabled": bool(rule_library.get("execution_enabled", False)),
        "summary": {
            "slopes": len(assessments),
            "priority_counts": {code: counts.get(code, 0) for code in PRIORITY_LABELS},
            "with_any_stability_records": sum(bool(row["stability_scenarios"]) for row in assessments),
            "with_scenario_stability_conclusion": sum(row["stability_concern"] != "无法判断" for row in assessments),
            "formal_risk_level_available": sum(row["formal_risk_level"] is not None for row in assessments),
            "stability_concern_counts": dict(stability_counts),
        },
        "assessments": assessments,
        "boundary": {
            "can_do": [
                "依据已识别灾害体、坡高坡度、现状/工况稳定性结论和明确变形迹象安排人工复核优先级。",
                "逐条展示命中事实、PDF证据、缺失数据和建议复核内容。",
            ],
            "cannot_do": [
                "在缺少近期巡检、降雨、地下水、监测时序和暴露数据时，不输出正式风险等级。",
                "不预测具体失稳时间，不输出滑坡概率，不作“一定安全”或“一定失稳”判断。",
                "治理设计安全系数只说明设计验算结果，不代表当前防护已实施、完好或当前边坡安全。",
            ],
        },
    }


def related_facts(
    slope_id: str,
    edges: list[dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for edge in edges:
        if edge.get("source") != slope_id:
            continue
        node = nodes_by_id.get(edge.get("target"))
        if not node:
            continue
        evidence_id = edge.get("props", {}).get("evidence")
        facts.append(
            {
                "relation": edge.get("relation"),
                "node": node,
                "evidence": evidence_by_id.get(evidence_id) if evidence_id else None,
            }
        )
    return facts


def assess_slope(
    slope: dict[str, Any],
    related: list[dict[str, Any]],
    completeness_report: dict[str, Any] | None,
    rule_library: dict[str, Any],
) -> dict[str, Any]:
    props = slope.get("props", {})
    hazard_bodies = [fact for fact in related if fact["node"].get("type") == "HazardBody"]
    susceptibility = [fact for fact in related if fact["node"].get("type") == "HazardSusceptibilityAssessment"]
    stability = [fact for fact in related if fact["node"].get("type") == "StabilityAnalysis"]
    causal = [fact for fact in related if fact["node"].get("type") == "CausalFactor"]
    protection = [fact for fact in related if fact["node"].get("type") == "ProtectionWork"]
    exposure = [fact for fact in related if fact["node"].get("type") == "ExposureObject"]
    all_observations = [
        fact for fact in related
        if fact["node"].get("type") in {"DeformationObservation", "MonitoringObservation", "InspectionRecord"}
    ]
    observations = [
        fact for fact in all_observations
        if fact["node"].get("props", {}).get("current_status_known") is True
        or fact["node"].get("props", {}).get("temporal_scope") in {"current", "recent_inspection", "real_time_monitoring"}
    ]
    historical_observations = [fact for fact in all_observations if fact not in observations]

    hazard_types = unique(
        [
            *(term for term in ("滑坡", "崩塌", "泥石流", "落石") if term in str(slope.get("summary") or "")),
            *(
                value
                for fact in susceptibility
                for value in fact["node"].get("props", {}).get("candidate_hazard_types", [])
            ),
        ]
    )
    body_types = unique(
        fact["node"].get("props", {}).get("body_type") or "灾害体（类型未明确）"
        for fact in hazard_bodies
    )
    stability_scenarios = [stability_record(fact) for fact in stability]
    scenario_stability = [
        row for row in stability_scenarios
        if row["analysis_scope"] == "现状边坡" or (
            "治理设计" not in row["condition"] and any(term in row["condition"] for term in ("天然", "暴雨", "地震", "饱和"))
        )
    ]
    observed_terms = detect_alarm_terms(props, observations)
    historical_observed_terms = detect_alarm_terms({}, historical_observations)
    height = maximum_number(props.get("slope_height_max_m"), props.get("slope_height_min_m"))
    gradient = maximum_number(props.get("slope_gradient_max_deg"), props.get("slope_gradient_min_deg"))
    basis: list[dict[str, Any]] = []

    worst_stability = worst_stability_record(scenario_stability)
    stability_concern = "无法判断"
    if worst_stability:
        stability_concern = stability_concern_label(worst_stability)
        basis.append(
            {
                "kind": "scenario_stability",
                "label": f"{worst_stability['condition']}稳定性",
                "value": f"Fs={worst_stability['fs']}" if worst_stability["fs"] is not None else worst_stability["status"],
                "interpretation": worst_stability["status"] or stability_concern,
                "source": "PDF稳定性分析结论",
                "evidence": worst_stability["evidence"],
                "boundary": "报告形成时间和现场现状未核验，作为工况筛查依据，不等同于实时监测。",
            }
        )
    if body_types:
        basis.append(
            {
                "kind": "hazard_body",
                "label": "灾害体类型",
                "value": "、".join(body_types),
                "interpretation": "已在工程资料中登记灾害体",
                "source": "图谱灾害体节点",
                "evidence": first_evidence(hazard_bodies),
            }
        )
    if height is not None or gradient is not None:
        value = "，".join(
            text for text in (
                f"坡高约{height:g}m" if height is not None else "",
                f"最大坡度约{gradient:g}°" if gradient is not None else "",
            ) if text
        )
        basis.append(
            {
                "kind": "geometry",
                "label": "坡体几何",
                "value": value,
                "interpretation": "只用于复核优先级，不单独推导风险等级",
                "source": "PDF边坡一览表/工程资料",
                "evidence": None,
            }
        )
    if observed_terms:
        basis.append(
            {
                "kind": "deformation_observation",
                "label": "明确变形/渗流迹象",
                "value": "、".join(observed_terms),
                "interpretation": "存在需现场核验的异常迹象",
                "source": "巡检或监测节点",
                "evidence": first_evidence(observations),
            }
        )
    if historical_observed_terms:
        basis.append(
            {
                "kind": "historical_deformation_observation",
                "label": "历史工程资料中的变形迹象",
                "value": "、".join(historical_observed_terms),
                "interpretation": "用于静态易发性关注，不代表当前仍在发展",
                "source": "PDF历史工程资料",
                "evidence": first_evidence(historical_observations),
                "boundary": "必须通过近期现场巡检确认其是否持续、扩大或已经处置。",
            }
        )

    priority, reasons = screening_priority(
        body_types=body_types,
        hazard_types=hazard_types,
        stability=worst_stability,
        observed_terms=observed_terms,
        height=height,
        gradient=gradient,
    )
    if not basis:
        reasons.append("现有资料不足以形成灾害筛查依据")

    current_protection_known = any(
        fact["node"].get("props", {}).get("current_condition") not in {None, "", "unknown"}
        for fact in protection
    )
    exposure_known = bool(exposure) or bool(props.get("exposure"))
    consequence_level = consequence_from_exposure(exposure, props)
    likelihood_level = likelihood_from_stability(worst_stability, observed_terms)
    formal_risk_level = risk_matrix(likelihood_level, consequence_level)
    # Formal output remains disabled while the candidate rule library is unpublished.
    if not rule_library.get("execution_enabled"):
        formal_risk_level = None

    gaps = critical_gaps(
        completeness_report,
        has_stability=bool(scenario_stability),
        has_observation=bool(observations),
        has_exposure=exposure_known,
        current_protection_known=current_protection_known,
    )
    tracked_risk_codes = {
        str(field.get("code"))
        for field in (completeness_report or {}).get("fields", [])
        if field.get("code") in GAP_GUIDANCE and field.get("code") != "risk_rules"
    }
    unavailable_risk_codes = {str(item.get("code")) for item in gaps}
    risk_data_inventory = {
        "total_categories": len(tracked_risk_codes),
        "available_categories": len(tracked_risk_codes - unavailable_risk_codes),
        "unavailable_categories": len(tracked_risk_codes & unavailable_risk_codes),
        "first_round_recommended": min(5, len(gaps)),
        "note": "未具备的类别不要求一次性全部补齐，应按优先级分轮补充。",
    }
    confidence = confidence_label(worst_stability, observed_terms, exposure_known, len(gaps))
    matched_rules = matched_rule_references(rule_library, hazard_types, worst_stability, observed_terms)
    return {
        "slope_id": slope["id"],
        "business_id": props.get("slope_id"),
        "slope_label": slope.get("label"),
        "route_code": props.get("route_code_cache"),
        "station": f"{props.get('start_station_raw', '')}-{props.get('end_station_raw', '')}".strip("-"),
        "hazard_types": hazard_types,
        "hazard_body_types": body_types,
        "assessment_status": "screening_only_not_formal_risk",
        "screening_priority_code": priority,
        "screening_priority": PRIORITY_LABELS[priority],
        "screening_reasons": unique(reasons),
        "stability_concern": stability_concern,
        "stability_scenarios": stability_scenarios,
        "historical_deformation_terms": historical_observed_terms,
        "likelihood_level": likelihood_level,
        "consequence_level": consequence_level,
        "formal_risk_level": formal_risk_level,
        "confidence": confidence,
        "risk_data_completeness": (completeness_report or {}).get("risk_data_completeness", 0),
        "risk_data_inventory": risk_data_inventory,
        "basis": basis,
        "matched_rule_references": matched_rules,
        "critical_data_gaps": gaps,
        "recommended_action": recommended_action(priority, worst_stability, current_protection_known),
        "limitations": [
            "这是资料驱动的宏观复核优先级，不是正式风险等级。",
            "缺失项不会按安全值处理，因而不能将P3/P4解释为低风险。",
            "没有近期日期的稳定性结论均按历史工程资料使用。",
        ],
        "engine_version": RISK_ENGINE_VERSION,
    }


def stability_record(fact: dict[str, Any]) -> dict[str, Any]:
    props = fact["node"].get("props", {})
    return {
        "node_id": fact["node"].get("id"),
        "condition": str(props.get("condition") or "未注明工况"),
        "fs": safe_float(props.get("fs")),
        "required_fs": safe_float(props.get("required_fs")),
        "status": props.get("status"),
        "analysis_scope": props.get("analysis_scope"),
        "source_kind": props.get("source_kind") or props.get("value_source"),
        "evidence": compact_evidence(fact.get("evidence")),
        "current_status_known": props.get("analysis_scope") == "现状边坡",
    }


def worst_stability_record(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    status_rank = {"不稳定": 0, "欠稳定": 1, "基本稳定": 2, "稳定": 3, "满足要求": 4}
    return min(
        rows,
        key=lambda row: (
            status_rank.get(str(row.get("status")), 9),
            row["fs"] if row["fs"] is not None else 99,
        ),
    )


def stability_concern_label(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "")
    if status == "不稳定" or (row.get("fs") is not None and row["fs"] < 1.0):
        return "高关注（不稳定）"
    if status == "欠稳定" or (row.get("fs") is not None and row["fs"] < 1.05):
        return "较高关注（欠稳定）"
    if status == "基本稳定":
        return "需关注（基本稳定）"
    if status == "稳定":
        return "该工况结论稳定（非实时）"
    return "有稳定性资料，状态需复核"


def screening_priority(
    *,
    body_types: list[str],
    hazard_types: list[str],
    stability: dict[str, Any] | None,
    observed_terms: list[str],
    height: float | None,
    gradient: float | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    status = str((stability or {}).get("status") or "")
    fs = (stability or {}).get("fs")
    body_text = " ".join(body_types)
    if observed_terms:
        reasons.append("资料中存在明确变形、渗流或坍塌迹象")
    if status in {"不稳定", "欠稳定"} or (fs is not None and fs < 1.05):
        reasons.append("现状/不利工况稳定性结论达到不稳定或欠稳定")
    if any(term in body_text for term in ("路基垮塌", "路基下沉")) or "不稳定边坡" in body_types:
        reasons.append("工程资料将对象标记为病害或不稳定对象，资料时效尚待现场核实")
    if reasons:
        return "P1", reasons
    if "潜在不稳定边坡" in body_text:
        reasons.append("工程资料标记为潜在不稳定边坡")
    if status == "基本稳定" or (fs is not None and 1.05 <= fs < 1.15):
        reasons.append("现状/工况稳定性处于基本稳定或临界关注区间")
    steep_or_high = (height is not None and height >= 30) or (gradient is not None and gradient >= 60)
    if "危岩体" in body_text and steep_or_high:
        reasons.append("已识别危岩体，且现有几何数据表明坡体较高或较陡")
    if reasons:
        return "P2", reasons
    if body_types or hazard_types:
        reasons.append("已识别灾害体或灾害类型，但缺少近期状态数据")
        return "P3", reasons
    return "P4", ["尚未形成可靠灾害类型及近期状态资料"]


def detect_alarm_terms(props: dict[str, Any], observations: list[dict[str, Any]]) -> list[str]:
    texts = []
    for key in (
        "deformation_observation", "overall_slope_deformation_severity",
        "crack", "bulge", "piping_seepage", "facility_damage",
    ):
        value = props.get(key)
        if value not in (None, "", [], {}):
            texts.append(str(value))
    for fact in observations:
        texts.extend(
            [
                str(fact["node"].get("label") or ""),
                str(fact["node"].get("summary") or ""),
                str(fact["node"].get("props") or ""),
            ]
        )
    combined = " ".join(texts)
    return [term for term in ALARM_TERMS if term in combined]


def likelihood_from_stability(stability: dict[str, Any] | None, observed_terms: list[str]) -> str | None:
    if observed_terms:
        return "较高"
    if not stability:
        return None
    status = str(stability.get("status") or "")
    fs = stability.get("fs")
    if status in {"不稳定", "欠稳定"} or (fs is not None and fs < 1.05):
        return "较高"
    if status == "基本稳定" or (fs is not None and fs < 1.15):
        return "中等"
    if status == "稳定":
        return "较低"
    return None


def consequence_from_exposure(exposure: list[dict[str, Any]], props: dict[str, Any]) -> str | None:
    values = [
        str(props.get("consequence_level") or ""),
        *(str(fact["node"].get("props", {}).get("consequence_level") or "") for fact in exposure),
    ]
    text = " ".join(values)
    for value in ("重大", "高", "大"):
        if value in text:
            return "高"
    if "中" in text:
        return "中等"
    if any(value in text for value in ("低", "小")):
        return "低"
    return None


def risk_matrix(likelihood: str | None, consequence: str | None) -> str | None:
    if not likelihood or not consequence:
        return None
    matrix = {
        ("较高", "高"): "高",
        ("较高", "中等"): "较高",
        ("较高", "低"): "中等",
        ("中等", "高"): "较高",
        ("中等", "中等"): "中等",
        ("中等", "低"): "较低",
        ("较低", "高"): "中等",
        ("较低", "中等"): "较低",
        ("较低", "低"): "低",
    }
    return matrix.get((likelihood, consequence))


def critical_gaps(
    report: dict[str, Any] | None,
    *,
    has_stability: bool,
    has_observation: bool,
    has_exposure: bool,
    current_protection_known: bool,
) -> list[dict[str, Any]]:
    preferred = {
        "deformation_observation": 0,
        "overall_slope_deformation_severity": 1,
        "rainfall": 2,
        "groundwater": 3,
        "monitoring": 4,
        "stability": 5,
        "protection_condition": 6,
        "structural_plane": 7,
        "slope_height_m": 8,
        "slope_gradient_deg": 9,
        "material_nature": 10,
        "hazard_body": 11,
        "exposure": 12,
        "maintenance": 20,
    }
    gaps = []
    for field in (report or {}).get("fields", []):
        if field.get("status") == "available":
            continue
        code = field.get("code")
        # “建议补充的数据”只列会影响风险研判的边坡数据。
        # 坐标系、来源别名等档案字段，以及“风险规则是否发布”这类系统级条件，
        # 不应混入单个边坡的待补数据数量。
        if code not in GAP_GUIDANCE or code == "risk_rules":
            continue
        if code == "stability" and has_stability:
            continue
        gaps.append(enrich_gap(code, field.get("label_zh"), field.get("status"), field.get("interface")))
    explicit = [
        ("deformation_observation", "近期变形巡检", has_observation, "/api/inspections"),
        ("protection_condition", "防护设施当前状态", current_protection_known, "/api/inspections"),
        ("exposure", "交通量、人员与设施暴露", has_exposure, "/api/exposure"),
    ]
    existing = {row["code"] for row in gaps}
    for code, label, available, interface in explicit:
        if not available and code not in existing:
            gaps.append(enrich_gap(code, label, "missing", interface))
    gaps.sort(
        key=lambda row: (
            int(row.get("improvement_priority", 9)),
            preferred.get(str(row.get("code")), 99),
            str(row.get("label")),
        )
    )
    return gaps


def enrich_gap(code: Any, label: Any, status: Any, interface: Any) -> dict[str, Any]:
    guidance = GAP_GUIDANCE.get(str(code), {})
    gap_scope = "interface_pending" if status == "interface_reserved" else "slope_data_missing"
    return {
        "code": code,
        "label": label,
        "status": status,
        "interface": interface,
        "gap_scope": gap_scope,
        "gap_scope_label": "接口待接入/补录" if gap_scope == "interface_pending" else "该边坡资料未提取",
        "improvement_priority": guidance.get("priority", 2),
        "improvement_priority_label": guidance.get("priority_label", "基础补录"),
        "accuracy_role": guidance.get("accuracy_role", "基础信息"),
        "why_it_matters": guidance.get("why", "补齐该字段可减少未知项并提高结论可解释性。"),
        "collection_hint": guidance.get("how", "从原始资料或现场调查补录，并保留来源和时间。"),
    }


def matched_rule_references(
    library: dict[str, Any],
    hazard_types: list[str],
    stability: dict[str, Any] | None,
    observed_terms: list[str],
) -> list[dict[str, Any]]:
    matched = []
    for rule in library.get("rules", []):
        title = str(rule.get("title") or "")
        if stability and "滑坡" in hazard_types and "滑坡稳定状态" in title:
            matched.append(rule_reference(rule, "稳定状态参考阈值"))
        elif observed_terms and "现场核查预警" in title:
            matched.append(rule_reference(rule, "现场异常预警参考"))
    unique_rows = []
    seen = set()
    for row in matched:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        unique_rows.append(row)
    return unique_rows[:8]


def rule_reference(rule: dict[str, Any], use: str) -> dict[str, Any]:
    return {
        "id": rule.get("id"),
        "title": rule.get("title"),
        "document_code": rule.get("document_code"),
        "page": rule.get("page"),
        "approval_status": rule.get("approval_status"),
        "execution_enabled": bool(rule.get("execution_enabled")),
        "use": use,
    }


def confidence_label(
    stability: dict[str, Any] | None,
    observed_terms: list[str],
    exposure_known: bool,
    gap_count: int,
) -> str:
    if stability and observed_terms and exposure_known and gap_count <= 3:
        return "较高"
    if stability and gap_count <= 8:
        return "中等"
    if stability or observed_terms:
        return "较低"
    return "低"


def recommended_action(
    priority: str,
    stability: dict[str, Any] | None,
    current_protection_known: bool,
) -> list[str]:
    actions = {
        "P1": [
            "优先组织现场核查，确认裂缝、位移、掉块、隆起、渗水和管涌是否仍在发展。",
            "补录近期降雨、地下水和防护设施完好状态；发现持续变形时立即按现行预案会商处置。",
        ],
        "P2": [
            "纳入重点复核清单，补做近期巡检并核对原稳定性计算的对象、工况和形成日期。",
            "核查坡顶、坡面、坡脚及既有防护设施，必要时补充暴雨工况稳定性分析。",
        ],
        "P3": [
            "按计划开展现场巡检，补齐灾害类型、坡体结构、变形迹象和防护设施现状。",
            "资料补齐前不得据此判断为低风险。",
        ],
        "P4": [
            "优先完成基础档案补录和灾害类型确认，再进入风险筛查。",
        ],
    }[priority]
    if stability and not stability.get("current_status_known"):
        actions.append("现有稳定性结论缺少实时性证明，应复核报告日期和现场变化。")
    if not current_protection_known:
        actions.append("设计防护措施的实施情况与当前完好程度未知，需现场确认。")
    return unique(actions)


def compact_evidence(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if not evidence:
        return None
    return {
        "id": evidence.get("id"),
        "source_file": evidence.get("source_file"),
        "page": evidence.get("page"),
        "text": evidence.get("text"),
        "validation_status": evidence.get("validation_status"),
    }


def first_evidence(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((compact_evidence(fact.get("evidence")) for fact in facts if fact.get("evidence")), None)


def maximum_number(*values: Any) -> float | None:
    numbers = [number for value in values if (number := safe_float(value)) is not None]
    return max(numbers) if numbers else None


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def unique(values: Any) -> list[Any]:
    result = []
    seen = set()
    for value in values:
        if value in (None, ""):
            continue
        marker = str(value)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def priority_order(value: str) -> int:
    return {"P1": 0, "P2": 1, "P3": 2, "P4": 3}.get(value, 9)
