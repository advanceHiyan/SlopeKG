from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
from typing import Any


RISK_ENGINE_VERSION = "multi-axis-screening-v2.0"
# Operational freshness bands are data-quality defaults, not engineering warning
# thresholds.  They only decide whether a manually supplied observation may be
# described as "recent"; projects can revise them after the review workflow is
# agreed with domain specialists.
DYNAMIC_CURRENT_DAYS = 30
DYNAMIC_MAX_AGE_DAYS = 90
PRIORITY_LABELS = {
    "P1": "优先复核",
    "P2": "重点复核",
    "P3": "常规复核",
    "P4": "资料补录",
}
CURRENT_STABILITY_STATUSES = {"不稳定", "欠稳定", "基本稳定", "稳定"}
ALARM_TERMS = ("贯通裂缝", "新裂缝", "裂缝发展", "明显位移", "加速变形", "管涌", "渗水", "隆起", "坍塌")
GENERIC_HAZARD_BODY_TYPES = {"灾害体（类型未明确）", "灾害体", "边坡"}
ADMINISTRATIVE_FACT_TYPES = {
    "Project", "Document", "RouteSegment", "HazardBody", "HazardSusceptibilityAssessment", "HazardType",
}
HAZARD_PROFILE_SPECS = {
    "滑坡": {
        "aliases": ("滑坡", "不稳定边坡", "潜在不稳定边坡"),
        "key_inputs": ("stability", "structural_plane", "groundwater", "rainfall", "deformation_observation"),
        "boundary": "稳定系数和历史变形用于工况关注；缺少近期水文与位移时不推断发生时间。",
    },
    "崩塌/危岩/落石": {
        "aliases": ("崩塌", "危岩", "落石", "路基垮塌"),
        "key_inputs": ("structural_plane", "slope_height_m", "slope_gradient_deg", "deformation_observation", "protection_condition"),
        "boundary": "坡高、坡度和危岩记录只能支持静态关注；块体几何、结构面与近期掉块情况决定进一步分析。",
    },
    "泥石流": {
        "aliases": ("泥石流",),
        "key_inputs": ("rainfall", "deformation_observation", "material_nature", "exposure"),
        "boundary": "当前字段尚不能完整表达沟域、物源与汇流条件，只生成待专项复核提示。",
    },
    "路基变形": {
        "aliases": ("路基下沉", "路基垮塌"),
        "key_inputs": ("deformation_observation", "groundwater", "protection_condition", "maintenance", "exposure"),
        "boundary": "需要区分路基病害、边坡失稳及防护设施问题，不能仅凭名称归因。",
    },
}
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
    dynamic_counts = Counter(row["dynamic_alert"]["level"] for row in assessments)
    static_counts = Counter(row["static_susceptibility"]["level"] for row in assessments)
    consequence_counts = Counter(row["consequence_assessment"]["level"] for row in assessments)
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
            "static_susceptibility_counts": dict(static_counts),
            "dynamic_alert_counts": dict(dynamic_counts),
            "consequence_counts": dict(consequence_counts),
            "with_current_dynamic_evidence": sum(row["temporal_validity"]["current_dynamic_evidence"] for row in assessments),
            "with_stability_conflicts": sum(bool(row["data_conflicts"]) for row in assessments),
        },
        "assessments": assessments,
        "boundary": {
            "can_do": [
                "依据已识别灾害体、坡高坡度、现状/工况稳定性结论和明确变形迹象安排人工复核优先级。",
                "逐条展示命中事实、PDF证据、缺失数据和建议复核内容。",
                "分别输出静态易发性关注、动态异常状态和后果资料状态，避免混成一个模糊分数。",
            ],
            "cannot_do": [
                "在缺少近期巡检、降雨、地下水、监测时序和暴露数据时，不输出正式风险等级。",
                "不预测具体失稳时间，不输出滑坡概率，不作“一定安全”或“一定失稳”判断。",
                "治理设计安全系数只说明设计验算结果，不代表当前防护已实施、完好或当前边坡安全。",
                "时间有效性分段是项目数据质量默认值，不是滑坡预警阈值。",
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
    temporal_validity = dynamic_temporal_validity(props)
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
    temporal_validity["current_dynamic_evidence"] = bool(observations) or temporal_validity["eligible_as_current"]

    manual_hazard_body = props.get("hazard_body")
    manual_hazard_values = manual_hazard_body if isinstance(manual_hazard_body, list) else [manual_hazard_body]
    hazard_types = unique(
        [
            *(term for term in ("滑坡", "崩塌", "泥石流", "落石") if term in str(slope.get("summary") or "")),
            *(term for term in ("滑坡", "崩塌", "泥石流", "落石") if term in str(props.get("hazard_type") or manual_hazard_body or "")),
            *(
                value
                for fact in susceptibility
                for value in fact["node"].get("props", {}).get("candidate_hazard_types", [])
            ),
        ]
    )
    body_types = unique([
        *(fact["node"].get("props", {}).get("body_type") or "灾害体（类型未明确）" for fact in hazard_bodies),
        *(value for value in manual_hazard_values if value and value != "无明确灾害体"),
    ])
    stability_scenarios = unique_stability_records([stability_record(fact) for fact in stability])
    if props.get("stability_factor") not in (None, "") or props.get("stability_status") not in (None, ""):
        stability_scenarios.append(
            {
                "node_id": f"manual_stability_{slope['id']}",
                "condition": str(props.get("stability_condition") or "人工补录工况"),
                "fs": safe_float(props.get("stability_factor")),
                "required_fs": None,
                "status": props.get("stability_status"),
                "analysis_scope": "现状边坡",
                "section": None,
                "body_id": None,
                "failure_mode": None,
                "source_kind": "人工补录",
                "value_source": "manual_verified_input",
                "quality_issues": [],
                "eligible_for_screening": True,
                "review_note": "人工补录数据；应结合数据日期和来源说明复核",
                "evidence": None,
                "document_scope_is_existing_slope": True,
                "current_status_known": True,
                "evidence_alignment": "manual",
            }
        )
    scenario_stability = [
        row for row in stability_scenarios
        if row["analysis_scope"] == "现状边坡" or (
            "治理设计" not in row["condition"] and any(term in row["condition"] for term in ("天然", "暴雨", "地震", "饱和"))
        )
    ]
    current_dynamic_props = props if temporal_validity["eligible_as_current"] else {}
    observed_terms = detect_alarm_terms(current_dynamic_props, observations)
    historical_observed_terms = unique([
        *detect_alarm_terms({}, historical_observations),
        *(detect_alarm_terms(props, []) if not temporal_validity["eligible_as_current"] else []),
    ])
    height = maximum_number(props.get("slope_height_m"), props.get("slope_height_max_m"), props.get("slope_height_min_m"))
    gradient = maximum_number(props.get("slope_gradient_deg"), props.get("slope_gradient_max_deg"), props.get("slope_gradient_min_deg"))
    hazard_identity_reliable = any(value not in GENERIC_HAZARD_BODY_TYPES for value in body_types) or any(
        hazard_evidence_is_specific(fact.get("evidence"), body_types, hazard_types)
        for fact in [*hazard_bodies, *susceptibility]
    )
    supporting_context_available = (
        height is not None
        or gradient is not None
        or bool(historical_observations)
        or any(fact["node"].get("type") not in ADMINISTRATIVE_FACT_TYPES for fact in related)
    )
    basis: list[dict[str, Any]] = []

    worst_stability = worst_stability_record(scenario_stability)
    stability_conflicts = detect_stability_conflicts(scenario_stability)
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
                "interpretation": "工程资料中存在明确灾害证据" if hazard_identity_reliable else "仅为自动识别候选，尚缺少灾害文字证据",
                "source": "图谱灾害体节点",
                "evidence": first_evidence(hazard_bodies) if hazard_identity_reliable else None,
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
        hazard_identity_reliable=hazard_identity_reliable,
        supporting_context_available=supporting_context_available,
    )
    manual_rule_matches = evaluate_manual_rules(
        rule_library,
        {
            **props,
            "hazard_type": hazard_types,
            "hazard_body": body_types,
            "slope_height_m": height,
            "slope_gradient_deg": gradient,
            "observed_terms": observed_terms,
            "stability_factor": (worst_stability or {}).get("fs"),
            "stability_status": (worst_stability or {}).get("status"),
        },
    )
    for match in manual_rule_matches:
        requested = match["screening_priority_code"]
        reasons.append(match["reason"])
        if priority_order(requested) < priority_order(priority):
            priority = requested
        basis.append(
            {
                "kind": "manual_rule",
                "label": f"人工规则：{match['title']}",
                "value": f"命中后建议 {requested}",
                "interpretation": "该结构化规则已审核启用；只调整人工复核顺序，不产生正式风险等级。",
                "source": "人工规则库",
                "evidence": None,
            }
        )
    if not basis:
        reasons.append("现有资料不足以形成灾害筛查依据")

    current_protection_known = props.get("protection_condition") not in (None, "", "未核查") or any(
        fact["node"].get("props", {}).get("current_condition") not in {None, "", "unknown"}
        for fact in protection
    )
    exposure_known = bool(exposure) or bool(props.get("exposure")) or bool(props.get("consequence_level"))
    consequence_level = consequence_from_exposure(exposure, props)
    likelihood_level = likelihood_from_stability(worst_stability, observed_terms)
    static_susceptibility = static_susceptibility_assessment(
        body_types=body_types,
        hazard_types=hazard_types,
        stability=worst_stability,
        historical_terms=historical_observed_terms,
        height=height,
        gradient=gradient,
        hazard_identity_reliable=hazard_identity_reliable,
    )
    dynamic_alert = dynamic_alert_assessment(
        observed_terms=observed_terms,
        props=props,
        current_observations=observations,
        temporal_validity=temporal_validity,
    )
    consequence_assessment = {
        "level": consequence_level or "无法判断",
        "status": "available" if consequence_level else "missing",
        "interpretation": (
            "已有暴露/后果等级，可用于参考矩阵；仍需核对数据范围和日期。"
            if consequence_level else
            "缺少交通量、人员、道路中断和重要设施信息，不能形成综合风险。"
        ),
    }
    provisional_risk_level = risk_matrix(likelihood_level, consequence_level)
    formal_gate = formal_risk_gate(
        rule_library=rule_library,
        likelihood_level=likelihood_level,
        consequence_level=consequence_level,
        current_dynamic_evidence=dynamic_alert["current_evidence_available"],
        dynamic_rule_result_available=dynamic_alert["threshold_evaluated"],
        conflicts=stability_conflicts,
    )
    formal_risk_level = provisional_risk_level if formal_gate["ready"] else None

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
    confidence = confidence_label(
        worst_stability,
        observed_terms,
        exposure_known,
        len(gaps),
        has_conflicts=bool(stability_conflicts),
        temporal_status=temporal_validity["status"],
    )
    confidence_assessment = build_confidence_assessment(
        confidence=confidence,
        stability=worst_stability,
        observed_terms=observed_terms,
        exposure_known=exposure_known,
        gaps=gaps,
        conflicts=stability_conflicts,
        temporal_validity=temporal_validity,
    )
    hazard_profiles = build_hazard_profiles(
        body_types=body_types,
        hazard_types=hazard_types,
        gaps=gaps,
        stability=worst_stability,
        observed_terms=observed_terms,
    )
    calculation_readiness = stability_calculation_readiness(
        props=props,
        related=related,
        rule_library=rule_library,
        height=height,
        gradient=gradient,
    )
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
        "static_susceptibility": static_susceptibility,
        "dynamic_alert": dynamic_alert,
        "consequence_assessment": consequence_assessment,
        "hazard_profiles": hazard_profiles,
        "stability_calculation_readiness": calculation_readiness,
        "temporal_validity": temporal_validity,
        "data_conflicts": stability_conflicts,
        "likelihood_level": likelihood_level,
        "likelihood_interpretation": "基于历史稳定性工况和近期异常的定性关注，不是时间概率。" if likelihood_level else "资料不足，未形成发生可能性等级。",
        "consequence_level": consequence_level,
        "provisional_risk_matrix_level": provisional_risk_level,
        "formal_risk_gate": formal_gate,
        "formal_risk_level": formal_risk_level,
        "confidence": confidence,
        "confidence_assessment": confidence_assessment,
        "risk_data_completeness": (completeness_report or {}).get("risk_data_completeness", 0),
        "screening_basis_status": {
            "hazard_identity_reliable": hazard_identity_reliable,
            "supporting_context_available": supporting_context_available,
            "minimum_basis_met": hazard_identity_reliable or supporting_context_available or bool(worst_stability) or bool(observed_terms),
        },
        "risk_data_inventory": risk_data_inventory,
        "basis": basis,
        "matched_rule_references": matched_rules,
        "executed_manual_rules": manual_rule_matches,
        "critical_data_gaps": gaps,
        "recommended_action": recommended_action(priority, worst_stability, current_protection_known),
        "limitations": [
            "这是资料驱动的宏观复核优先级，不是正式风险等级。",
            "缺失项不会按安全值处理，因而不能将P3/P4解释为低风险。",
            "没有近期日期的稳定性结论均按历史工程资料使用。",
            "参考矩阵结果即使可计算，也不等于已发布的正式风险等级。",
            "时间有效性分段只用于数据质量控制，不能解释为工程预警阈值。",
        ],
        "engine_version": RISK_ENGINE_VERSION,
    }


def dynamic_temporal_validity(props: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Classify dated manual dynamic data without treating the bands as warning thresholds."""
    dynamic_fields = {
        "deformation_observation", "overall_slope_deformation_severity", "rainfall",
        "groundwater", "monitoring", "protection_condition",
    }
    manual = props.get("manual_override") if isinstance(props.get("manual_override"), dict) else {}
    supplied_fields = {str(value) for value in manual.get("fields", [])}
    present_fields = sorted(
        code for code in dynamic_fields
        if code in supplied_fields and props.get(code) not in (None, "", [], {}, "未核查")
    )
    observed_at = manual.get("observed_at")
    if not present_fields:
        return {
            "status": "not_provided", "label": "无带日期的近期数据", "observed_at": observed_at,
            "age_days": None, "eligible_as_current": False, "current_dynamic_evidence": False,
            "dynamic_fields": [], "policy": temporal_policy_note(),
        }
    if not observed_at:
        return {
            "status": "undated", "label": "动态数据缺少日期", "observed_at": None,
            "age_days": None, "eligible_as_current": False, "current_dynamic_evidence": False,
            "dynamic_fields": present_fields, "policy": temporal_policy_note(),
        }
    try:
        observed_date = date.fromisoformat(str(observed_at)[:10])
    except ValueError:
        return {
            "status": "invalid_date", "label": "动态数据日期无效", "observed_at": observed_at,
            "age_days": None, "eligible_as_current": False, "current_dynamic_evidence": False,
            "dynamic_fields": present_fields, "policy": temporal_policy_note(),
        }
    age_days = ((today or date.today()) - observed_date).days
    if age_days < 0:
        status, label, eligible = "future_date", "日期晚于系统日期", False
    elif age_days <= DYNAMIC_CURRENT_DAYS:
        status, label, eligible = "current", "近期有效", True
    elif age_days <= DYNAMIC_MAX_AGE_DAYS:
        status, label, eligible = "aging", "临近复核", True
    else:
        status, label, eligible = "stale", "已过期，仅作历史资料", False
    return {
        "status": status, "label": label, "observed_at": str(observed_at)[:10],
        "age_days": age_days, "eligible_as_current": eligible,
        "current_dynamic_evidence": eligible, "dynamic_fields": present_fields,
        "policy": temporal_policy_note(),
    }


def temporal_policy_note() -> str:
    return f"项目默认：0—{DYNAMIC_CURRENT_DAYS}天为近期，{DYNAMIC_CURRENT_DAYS + 1}—{DYNAMIC_MAX_AGE_DAYS}天提示复核，超过后仅作历史证据；该分段不是工程预警阈值。"


def static_susceptibility_assessment(
    *,
    body_types: list[str],
    hazard_types: list[str],
    stability: dict[str, Any] | None,
    historical_terms: list[str],
    height: float | None,
    gradient: float | None,
    hazard_identity_reliable: bool,
) -> dict[str, Any]:
    status = str((stability or {}).get("status") or "")
    fs = (stability or {}).get("fs")
    reasons: list[str] = []
    if status in {"不稳定", "欠稳定"} or (fs is not None and fs < 1.05):
        reasons.append("历史现状/不利工况达到不稳定或欠稳定")
    if historical_terms:
        reasons.append("历史资料记录变形、渗流或坍塌迹象")
    if reasons:
        level = "较高关注"
    elif "潜在不稳定边坡" in " ".join(body_types):
        level, reasons = "较高关注", ["工程资料标记为潜在不稳定对象"]
    elif hazard_identity_reliable and (body_types or hazard_types):
        level, reasons = "中等关注", ["已有明确灾害体或灾害类型证据"]
    elif (height is not None and height >= 30) or (gradient is not None and gradient >= 60):
        level, reasons = "一般关注", ["现有几何资料显示坡体较高或较陡，但灾害证据不足"]
    else:
        level, reasons = "资料不足", ["静态条件不足，不能判断易发性高低"]
    return {
        "level": level,
        "reasons": reasons,
        "temporality": "historical_baseline",
        "interpretation": "表示历史资料支持的静态关注程度，不是年发生概率，也不代表当前状态。",
    }


def dynamic_alert_assessment(
    *,
    observed_terms: list[str],
    props: dict[str, Any],
    current_observations: list[dict[str, Any]],
    temporal_validity: dict[str, Any],
) -> dict[str, Any]:
    current_available = bool(current_observations) or bool(temporal_validity.get("eligible_as_current"))
    threshold_results = [
        fact["node"].get("props", {})
        for fact in current_observations
        if fact["node"].get("props", {}).get("threshold_status") not in (None, "")
        or fact["node"].get("props", {}).get("alarm_level") not in (None, "")
    ]
    severity = str(props.get("overall_slope_deformation_severity") or "")
    if observed_terms:
        level = "发现异常"
        interpretation = "近期资料中存在明确异常词，需结合量值、变化趋势和现场复核。"
    elif current_available and severity == "无":
        level = "近期未见明确异常"
        interpretation = "只表示本次有日期记录未见明确异常，不构成安全保证。"
    elif current_available:
        level = "已有近期数据，尚无阈值结论"
        interpretation = "已有近期输入，但缺少经审核的量化阈值或结构化变化趋势。"
    elif temporal_validity.get("status") == "stale":
        level = "近期状态未知（旧资料已过期）"
        interpretation = "旧记录已转为历史证据，不参与当前异常触发。"
    else:
        level = "无法判断"
        interpretation = "缺少带日期的近期巡检、降雨、地下水或监测资料。"
    return {
        "level": level,
        "current_evidence_available": current_available,
        "observed_terms": observed_terms,
        "threshold_evaluated": bool(threshold_results),
        "threshold_results": threshold_results,
        "interpretation": interpretation,
        "not_a_forecast": True,
    }


def detect_stability_conflicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag competing values only within the same object/scope/scenario key."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("quality_issues") or not row.get("eligible_for_screening", True):
            continue
        key = (
            canonical_stability_condition(row.get("condition")), row.get("analysis_scope"),
            row.get("section"), row.get("body_id"), row.get("failure_mode"),
        )
        groups.setdefault(key, []).append(row)
    conflicts = []
    for key, candidates in groups.items():
        statuses = {str(row.get("status")) for row in candidates if row.get("status")}
        factors = sorted({float(row["fs"]) for row in candidates if row.get("fs") is not None})
        if len(statuses) <= 1 and (len(factors) <= 1 or factors[-1] - factors[0] <= 0.02):
            continue
        conflicts.append({
            "kind": "stability_scenario_conflict",
            "scenario": key[0] or "未注明工况",
            "analysis_scope": key[1], "section": key[2], "body_id": key[3], "failure_mode": key[4],
            "statuses": sorted(statuses), "fs_values": factors,
            "record_ids": [row.get("node_id") for row in candidates],
            "handling": "最不利记录继续用于保守排序；正式结论阻断，需核对对象、断面、工况和原页。",
        })
    return conflicts


def build_hazard_profiles(
    *,
    body_types: list[str],
    hazard_types: list[str],
    gaps: list[dict[str, Any]],
    stability: dict[str, Any] | None,
    observed_terms: list[str],
) -> list[dict[str, Any]]:
    combined = " ".join([*body_types, *hazard_types])
    gap_by_code = {str(row.get("code")): row for row in gaps}
    profiles = []
    for profile_type, spec in HAZARD_PROFILE_SPECS.items():
        if not any(alias in combined for alias in spec["aliases"]):
            continue
        missing = [code for code in spec["key_inputs"] if code in gap_by_code]
        profiles.append({
            "hazard_type": profile_type,
            "status": "screening_profile",
            "key_inputs": list(spec["key_inputs"]),
            "missing_key_inputs": missing,
            "evidence_flags": {"stability": bool(stability), "current_anomaly": bool(observed_terms)},
            "boundary": spec["boundary"],
        })
    if not profiles:
        profiles.append({
            "hazard_type": "未明确",
            "status": "type_confirmation_required",
            "key_inputs": ["hazard_body", "deformation_observation"],
            "missing_key_inputs": [code for code in ("hazard_body", "deformation_observation") if code in gap_by_code],
            "evidence_flags": {"stability": bool(stability), "current_anomaly": bool(observed_terms)},
            "boundary": "灾害类型未可靠确定，不能选择专项模型或专项规则。",
        })
    return profiles


def stability_calculation_readiness(
    *,
    props: dict[str, Any],
    related: list[dict[str, Any]],
    rule_library: dict[str, Any],
    height: float | None,
    gradient: float | None,
) -> dict[str, Any]:
    """Report whether an auditable limit-equilibrium calculation can be run.

    Extracted result tables are not reverse-engineered into missing calculation
    inputs.  A method is executable only after its formula, variables, units and
    applicability have been reviewed and explicitly enabled.
    """
    node_types = [fact["node"].get("type") for fact in related]
    formula_rows = rule_library.get("formulas", [])
    approved_methods = [
        row for row in formula_rows
        if row.get("approval_status") in {"approved", "published"} and row.get("execution_enabled") is True
    ]
    checks = {
        "method_formula_approved": bool(approved_methods),
        "full_section_or_block_geometry": bool(props.get("section_geometry") or props.get("block_geometry")),
        "basic_height_and_gradient": height is not None and gradient is not None,
        "material_strength_parameters": "MaterialParameterSet" in node_types,
        "slip_surface_or_structural_model": bool(props.get("slip_surface_geometry")) or "StructuralPlane" in node_types,
        "groundwater_scenario": bool(props.get("groundwater_scenario")),
        "seismic_scenario_parameters": bool(props.get("seismic_coefficient") or props.get("seismic_parameters")),
    }
    hard_requirements = (
        "method_formula_approved", "full_section_or_block_geometry",
        "material_strength_parameters", "slip_surface_or_structural_model",
    )
    ready = all(checks[code] for code in hard_requirements)
    return {
        "status": "ready_for_reviewed_solver" if ready else "not_executable",
        "ready": ready,
        "candidate_method_family": "极限平衡法（具体方法和适用破坏模式待审核）",
        "checks": checks,
        "blocking_items": [code for code in hard_requirements if not checks[code]],
        "approved_method_ids": [row.get("id") for row in approved_methods],
        "boundary": "现有报告中的Fs作为历史计算结果和测试依据保存；在输入、公式、单位及适用条件未齐全前不自动复算。",
    }


def formal_risk_gate(
    *,
    rule_library: dict[str, Any],
    likelihood_level: str | None,
    consequence_level: str | None,
    current_dynamic_evidence: bool,
    dynamic_rule_result_available: bool,
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    publication_status = str(rule_library.get("publication_status") or "not_generated")
    checks = {
        "approved_rule_set": publication_status in {"approved", "published", "released"} and bool(rule_library.get("execution_enabled")),
        "hazard_or_likelihood_available": bool(likelihood_level),
        "consequence_available": bool(consequence_level),
        "current_dynamic_evidence": bool(current_dynamic_evidence),
        "approved_dynamic_rule_result": bool(dynamic_rule_result_available),
        "no_unresolved_stability_conflict": not conflicts,
    }
    missing = [code for code, passed in checks.items() if not passed]
    return {
        "ready": not missing,
        "checks": checks,
        "blocking_items": missing,
        "message": "满足正式矩阵计算门槛，仍需按发布流程审核。" if not missing else "未满足正式风险发布门槛，仅输出分项关注与复核顺序。",
    }


def build_confidence_assessment(
    *,
    confidence: str,
    stability: dict[str, Any] | None,
    observed_terms: list[str],
    exposure_known: bool,
    gaps: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    temporal_validity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "label": confidence,
        "not_probability": True,
        "dimensions": {
            "stability_evidence": "available" if stability else "missing",
            "current_anomaly_evidence": "available" if observed_terms else "missing_or_no_affirmed_anomaly",
            "consequence_evidence": "available" if exposure_known else "missing",
            "temporal_validity": temporal_validity.get("status"),
            "data_gaps": len(gaps),
            "unresolved_conflicts": len(conflicts),
        },
        "interpretation": "表示当前结论的证据充分程度，不是风险概率或模型准确率。",
    }


def stability_record(fact: dict[str, Any]) -> dict[str, Any]:
    props = fact["node"].get("props", {})
    evidence = compact_evidence(fact.get("evidence"))
    row = {
        "node_id": fact["node"].get("id"),
        "condition": str(props.get("condition") or "未注明工况"),
        "fs": safe_float(props.get("fs")),
        "required_fs": safe_float(props.get("required_fs")),
        "status": props.get("status"),
        "analysis_scope": props.get("analysis_scope"),
        "section": props.get("section"), "body_id": props.get("body_id"), "failure_mode": props.get("failure_mode"),
        "source_kind": props.get("source_kind") or props.get("value_source"),
        "value_source": props.get("value_source"),
        "quality_issues": props.get("quality_issues", []),
        "eligible_for_screening": props.get("eligible_for_screening", True),
        "review_note": ("原文桩号或结论存在疑点，暂不参与筛查" if props.get("quality_issues")
                        else "已提取计算表，适用范围待核验，暂不参与筛查" if props.get("eligible_for_screening") is False
                        else "历史工程结论，不代表当前现场状态"),
        "evidence": evidence,
        "document_scope_is_existing_slope": props.get("analysis_scope") == "现状边坡",
        "current_status_known": False,  # A report's "现状" does not establish today's conditions.
    }
    alignment = stability_evidence_alignment(row, evidence)
    row["evidence_alignment"] = alignment
    if alignment == "mismatch" and evidence:
        row["evidence"] = {
            **evidence,
            "text": "工况引文未通过一致性校验，已隐藏原引文，仅保留页码定位。",
            "validation_status": "scenario_mismatch_suppressed",
        }
    return row


def unique_stability_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate extraction paths without merging distinct scopes or conditions."""
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for row in rows:
        key = (
            canonical_stability_condition(row.get("condition")),
            row.get("fs"),
            row.get("required_fs"),
            row.get("status"),
            row.get("analysis_scope"),
            row.get("section"), row.get("body_id"), row.get("failure_mode"),
        )
        if key not in selected:
            selected[key] = row
            order.append(key)
            continue
        if stability_record_quality(row) > stability_record_quality(selected[key]):
            selected[key] = row
    return [selected[key] for key in order]


def stability_record_quality(row: dict[str, Any]) -> tuple[int, int, int]:
    alignment_rank = {"exact": 3, "page_only": 2, "missing": 1, "mismatch": 0}
    deterministic = "deterministic" in str(row.get("value_source") or "") or row.get("source_kind") == "定量计算"
    return (
        alignment_rank.get(str(row.get("evidence_alignment")), 0),
        int(bool(deterministic)),
        int(bool(row.get("evidence"))),
    )


def stability_evidence_alignment(row: dict[str, Any], evidence: dict[str, Any] | None) -> str:
    if not evidence:
        return "missing"
    text = str(evidence.get("text") or "")
    if not text.strip():
        return "page_only"
    compact = text.replace(" ", "")
    fs = row.get("fs")
    if fs is not None and not any(abs(number - float(fs)) < 1e-9 for number in numbers_in_text(compact)):
        return "mismatch"
    status = str(row.get("status") or "")
    if status and status not in compact:
        return "mismatch"
    condition_terms = stability_condition_terms(row.get("condition"))
    if condition_terms and not any(term in compact for term in condition_terms):
        return "mismatch"
    return "exact"


def numbers_in_text(value: str) -> list[float]:
    return [float(token) for token in re.findall(r"\d+(?:\.\d+)?", value)]


def canonical_stability_condition(value: Any) -> str:
    text = str(value or "").replace(" ", "")
    aliases = {
        "天然": "天然", "天然状态": "天然", "天然工况": "天然",
        "饱和": "饱和", "饱和状态": "饱和", "饱水": "饱和", "饱水状态": "饱和",
    }
    return aliases.get(text, text)


def stability_condition_terms(value: Any) -> list[str]:
    canonical = canonical_stability_condition(value)
    aliases = {
        "天然": ["天然"],
        "饱和": ["饱和", "饱水"],
        "暴雨工况": ["暴雨"],
        "现状工况": ["现状", "现在工况"],
        "治理设计工况": ["治理", "设计"],
    }
    return aliases.get(canonical, [canonical] if canonical else [])


def worst_stability_record(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = [row for row in rows if row.get("eligible_for_screening", True) and not row.get("quality_issues")]
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
    hazard_identity_reliable: bool = True,
    supporting_context_available: bool = True,
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
    if (body_types or hazard_types) and (hazard_identity_reliable or supporting_context_available):
        reasons.append("已识别灾害体或灾害类型，但缺少近期状态数据")
        return "P3", reasons
    if body_types or hazard_types:
        return "P4", ["仅识别到未经证据定位的候选灾害类型，尚缺少支撑其筛查的基础资料"]
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
    return [term for term in ALARM_TERMS if alarm_term_is_affirmed(combined, term)]


def alarm_term_is_affirmed(text: str, term: str) -> bool:
    """Avoid turning common negative inspection statements into alarms."""
    for match in re.finditer(re.escape(term), text):
        prefix = text[max(0, match.start() - 18):match.start()]
        prefix = re.split(r"[，。；;,.!?！？\n]", prefix)[-1]
        if re.search(r"(?:无|未见|未发现|未出现|不存在|未观察到|否认)[^，。；;,.!?！？\n]{0,12}$", prefix):
            continue
        return True
    return False


def hazard_evidence_is_specific(
    evidence: dict[str, Any] | None,
    body_types: list[str],
    hazard_types: list[str],
) -> bool:
    """A page locator alone does not prove an automatically inferred hazard identity."""
    text = str((evidence or {}).get("text") or "").replace(" ", "")
    if not text:
        return False
    expected = [
        *hazard_types,
        *(value for value in body_types if value not in GENERIC_HAZARD_BODY_TYPES),
    ]
    return any(str(value).replace(" ", "") in text for value in expected if value)


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


def evaluate_manual_rules(library: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute only the small, audited declarative subset supported by this engine.

    Automatically extracted prose/table rules are deliberately excluded. A manual
    rule must be approved, explicitly enabled, and marked executable when saved.
    """
    matches: list[dict[str, Any]] = []
    for rule in library.get("rules", []):
        if rule.get("rule_kind") != "manual_declarative":
            continue
        if rule.get("approval_status") != "approved" or not rule.get("execution_enabled"):
            continue
        if rule.get("execution_status") != "executable":
            continue
        conditions = rule.get("conditions", [])
        if not isinstance(conditions, list) or not conditions:
            continue
        results = [evaluate_condition(context.get(row.get("field")), row.get("operator"), row.get("value")) for row in conditions]
        matched = all(results) if rule.get("match", "all") == "all" else any(results)
        if not matched:
            continue
        output = rule.get("output", {})
        priority = str(output.get("screening_priority_code") or "").upper()
        if priority not in PRIORITY_LABELS:
            continue
        matches.append(
            {
                "id": rule.get("id"),
                "title": rule.get("title") or rule.get("id"),
                "screening_priority_code": priority,
                "reason": str(output.get("reason") or f"命中人工规则：{rule.get('title') or rule.get('id')}"),
            }
        )
    return matches


def evaluate_condition(actual: Any, operator: Any, expected: Any) -> bool:
    if operator == "exists":
        return actual not in (None, "", [], {})
    if operator == "contains":
        if isinstance(actual, (list, tuple, set)):
            return any(str(expected) in str(item) for item in actual)
        return str(expected) in str(actual or "")
    if operator == "in":
        choices = expected if isinstance(expected, list) else [expected]
        if isinstance(actual, (list, tuple, set)):
            return any(str(item) in {str(choice) for choice in choices} for item in actual)
        return str(actual) in {str(choice) for choice in choices}
    if operator in {"gt", "gte", "lt", "lte"}:
        left = safe_float(actual)
        right = safe_float(expected)
        if left is None or right is None:
            return False
        return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[str(operator)]
    if operator == "eq":
        if isinstance(actual, (list, tuple, set)):
            return str(expected) in {str(item) for item in actual}
        left_number, right_number = safe_float(actual), safe_float(expected)
        if left_number is not None and right_number is not None:
            return left_number == right_number
        return str(actual) == str(expected)
    if operator == "neq":
        return not evaluate_condition(actual, "eq", expected)
    return False


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
    *,
    has_conflicts: bool = False,
    temporal_status: str = "not_provided",
) -> str:
    if stability and observed_terms and exposure_known and gap_count <= 3:
        level = "较高"
    elif stability and gap_count <= 8:
        level = "中等"
    elif stability or observed_terms:
        level = "较低"
    else:
        level = "低"
    order = ["低", "较低", "中等", "较高"]
    maximum = "较低" if has_conflicts or temporal_status in {"stale", "invalid_date", "future_date"} else "较高"
    return order[min(order.index(level), order.index(maximum))]


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
