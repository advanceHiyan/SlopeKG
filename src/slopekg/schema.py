from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from typing import Any

from .config import ATTRIBUTE_DICTIONARY_FILE
from .multimodal import MULTIMODAL_SCHEMA_VERSION, multimodal_contract


SCHEMA_VERSION = "1.1.0-layered-parsing"


NODE_TYPES: dict[str, dict[str, Any]] = {
    "Project": {"label_zh": "项目", "category": "organization"},
    "RouteSegment": {"label_zh": "路线段", "category": "organization"},
    "Slope": {"label_zh": "边坡", "category": "core"},
    "HazardBody": {"label_zh": "灾害体/隐患体", "category": "hazard"},
    "HazardType": {"label_zh": "灾害类型", "category": "taxonomy"},
    "HazardSusceptibilityAssessment": {"label_zh": "易感性评价", "category": "assessment"},
    "Lithology": {"label_zh": "岩性", "category": "geology"},
    "Stratum": {"label_zh": "地层", "category": "geology"},
    "StructuralPlane": {"label_zh": "结构面", "category": "geology"},
    "TerrainSetting": {"label_zh": "地形地貌", "category": "environment"},
    "CausalFactor": {"label_zh": "影响因素", "category": "hazard"},
    "ProtectionType": {"label_zh": "防护类型", "category": "taxonomy"},
    "ProtectionWork": {"label_zh": "防护工程", "category": "protection"},
    "DeformationObservation": {"label_zh": "变形现象", "category": "deformation"},
    "HydrologyObservation": {"label_zh": "水文现象", "category": "environment"},
    "UpliftObservation": {"label_zh": "隆起观测", "category": "deformation"},
    "SeepageObservation": {"label_zh": "管涌/渗水观测", "category": "deformation"},
    "FacilityDamageObservation": {"label_zh": "设施损毁观测", "category": "deformation"},
    "VegetationSurvey": {"label_zh": "植被调查", "category": "environment"},
    "MonitoringProgram": {"label_zh": "监测方案", "category": "monitoring"},
    "StabilityAnalysis": {"label_zh": "稳定性分析", "category": "analysis"},
    "MaterialParameterSet": {"label_zh": "材料计算参数", "category": "analysis"},
    "Drawing": {"label_zh": "图纸", "category": "evidence"},
    "Document": {"label_zh": "文档", "category": "evidence"},
    "Standard": {"label_zh": "规范", "category": "rule"},
    "StandardClause": {"label_zh": "规范条文", "category": "rule"},
    "Formula": {"label_zh": "计算公式", "category": "rule"},
    "ThresholdTable": {"label_zh": "阈值表", "category": "rule"},
    "RiskRule": {"label_zh": "风险规则", "category": "rule"},
    "RiskAssessment": {"label_zh": "风险评估", "category": "assessment"},
    "ExposureObject": {"label_zh": "暴露对象", "category": "risk"},
    "VisualAsset": {"label_zh": "视觉资产", "category": "multimodal"},
    "AcquisitionEvent": {"label_zh": "采集事件", "category": "multimodal"},
    "SpatialFootprint": {"label_zh": "空间覆盖范围", "category": "multimodal"},
    "RasterAsset": {"label_zh": "栅格资产", "category": "multimodal"},
    "PointCloudAsset": {"label_zh": "点云资产", "category": "multimodal"},
    "ThreeDModelAsset": {"label_zh": "三维模型资产", "category": "multimodal"},
    "MonitoringSeries": {"label_zh": "监测时序", "category": "multimodal"},
    "DerivedMetric": {"label_zh": "派生指标", "category": "multimodal"},
    "ProcessingRun": {"label_zh": "处理运行", "category": "provenance"},
    "QualityReport": {"label_zh": "质量报告", "category": "quality"},
    "AnnotationSet": {"label_zh": "标注集", "category": "quality"},
}


RELATION_TYPES: dict[str, dict[str, str]] = {
    "HAS_ROUTE_SEGMENT": {"label_zh": "包含路线段"},
    "HAS_SLOPE": {"label_zh": "包含边坡"},
    "BELONGS_TO_PROJECT": {"label_zh": "属于项目"},
    "CONTAINS_HAZARD_BODY": {"label_zh": "包含灾害体"},
    "HAS_SUSCEPTIBILITY_ASSESSMENT": {"label_zh": "具有易感性评价"},
    "FOR_HAZARD_TYPE": {"label_zh": "针对灾害类型"},
    "HAS_LITHOLOGY": {"label_zh": "具有岩性"},
    "HAS_STRATUM": {"label_zh": "具有地层"},
    "DEVELOPS_STRUCTURAL_PLANE": {"label_zh": "发育结构面"},
    "HAS_TERRAIN_SETTING": {"label_zh": "具有地形地貌"},
    "INFLUENCED_BY": {"label_zh": "受影响于"},
    "HAS_PROTECTION_DESIGN": {"label_zh": "具有防护设计"},
    "HAS_EXISTING_PROTECTION": {"label_zh": "具有既有防护"},
    "INSTANCE_OF": {"label_zh": "实例类型"},
    "HAS_STABILITY_ANALYSIS": {"label_zh": "具有稳定性分析"},
    "DOCUMENTS_STABILITY_ANALYSIS": {"label_zh": "记录归属待核实的稳定性分析"},
    "HAS_MATERIAL_PARAMETERS": {"label_zh": "具有材料计算参数"},
    "SHOWN_IN": {"label_zh": "显示于图纸"},
    "IN_DOCUMENT": {"label_zh": "位于文档"},
    "RECORDED_IN": {"label_zh": "记录于文档"},
    "SUPPORTED_BY": {"label_zh": "证据支持"},
    "HAS_DOCUMENT": {"label_zh": "具有资料文档"},
    "HAS_DEFORMATION_OBSERVATION": {"label_zh": "具有变形观测"},
    "HAS_HYDROLOGY_OBSERVATION": {"label_zh": "具有水文观测"},
    "HAS_PROTECTION_INSPECTION": {"label_zh": "具有防护设施检查"},
    "AFFECTS_PROTECTION_WORK": {"label_zh": "损毁对象"},
    "HAS_VEGETATION_SURVEY": {"label_zh": "具有植被调查"},
    "HAS_MONITORING_PROGRAM": {"label_zh": "具有监测方案"},
    "CONTAINS_CLAUSE": {"label_zh": "包含条文"},
    "DEFINES_FORMULA": {"label_zh": "定义公式"},
    "DEFINES_THRESHOLD_TABLE": {"label_zh": "定义阈值表"},
    "DEFINES_RULE": {"label_zh": "定义规则"},
    "HAS_VISUAL_ASSET": {"label_zh": "具有视觉资产"},
    "HAS_SPATIAL_ASSET": {"label_zh": "具有空间资产"},
    "HAS_MONITORING_SERIES": {"label_zh": "具有监测时序"},
    "ACQUIRED_DURING": {"label_zh": "采集于"},
    "COVERS_FOOTPRINT": {"label_zh": "覆盖空间范围"},
    "DERIVED_FROM": {"label_zh": "派生自"},
    "GENERATED_BY": {"label_zh": "生成于处理运行"},
    "HAS_QUALITY_REPORT": {"label_zh": "具有质量报告"},
    "HAS_ANNOTATION_SET": {"label_zh": "具有标注集"},
    "OBSERVES_SLOPE": {"label_zh": "观测边坡"},
}


SLOPE_FIELDS: list[dict[str, Any]] = [
    {"code": "slope_id", "label_zh": "边坡稳定编号", "group": "identity", "requirement": "core_required"},
    {"code": "source_alias", "label_zh": "原资料名称", "group": "identity", "requirement": "core_required"},
    {"code": "route_code_cache", "label_zh": "路线编号", "group": "location", "requirement": "core_required"},
    {"code": "start_station_raw", "label_zh": "起点桩号", "group": "location", "requirement": "core_required"},
    {"code": "end_station_raw", "label_zh": "止点桩号", "group": "location", "requirement": "core_required"},
    {"code": "start_station_m", "label_zh": "标准化起点里程", "group": "location", "requirement": "core_required"},
    {"code": "end_station_m", "label_zh": "标准化止点里程", "group": "location", "requirement": "core_required"},
    {"code": "side", "label_zh": "左右侧", "group": "location", "requirement": "core_required"},
    {"code": "coordinate_crs", "label_zh": "坐标参考系", "group": "location", "requirement": "conditional_required"},
    {"code": "control_points", "label_zh": "测量控制点（非边坡端点）", "group": "location", "requirement": "optional"},
    {"code": "start_coordinate", "label_zh": "起点坐标", "group": "location", "requirement": "risk_required"},
    {"code": "end_coordinate", "label_zh": "止点坐标", "group": "location", "requirement": "risk_required"},
    {"code": "slope_type", "label_zh": "路堤/路堑", "group": "geometry", "requirement": "risk_required"},
    {"code": "material_nature", "label_zh": "土质/岩质/土岩混合", "group": "geometry", "requirement": "risk_required"},
    {"code": "slope_height_m", "label_zh": "坡高", "group": "geometry", "requirement": "risk_required", "alternative_fields": ["slope_height_min_m", "slope_height_max_m"]},
    {"code": "slope_length_m", "label_zh": "坡长", "group": "geometry", "requirement": "risk_required"},
    {"code": "slope_gradient_deg", "label_zh": "坡度", "group": "geometry", "requirement": "risk_required", "alternative_fields": ["slope_gradient_min_deg", "slope_gradient_max_deg"]},
    {"code": "slope_aspect_deg", "label_zh": "坡向", "group": "geometry", "requirement": "risk_required"},
    {"code": "slope_structure_code", "label_zh": "斜坡结构", "group": "geometry", "requirement": "conditional_required"},
    {"code": "river_relation", "previous_code": "river_relative_position", "label_zh": "临河关系", "group": "environment", "requirement": "optional"},
    {"code": "vegetation_condition", "label_zh": "植被情况", "group": "environment", "requirement": "optional"},
    {"code": "overall_slope_deformation_severity", "previous_code": "overall_damage_severity", "label_zh": "边坡总体变形严重程度", "group": "deformation", "requirement": "risk_required"},
]


DOMAIN_CHECKS: list[dict[str, Any]] = [
    {"code": "lithology", "label_zh": "岩性", "domain": "geology", "relation": "HAS_LITHOLOGY", "requirement": "risk_required"},
    {"code": "stratum", "label_zh": "地层", "domain": "geology", "relation": "HAS_STRATUM", "requirement": "risk_required"},
    {"code": "structural_plane", "label_zh": "结构面", "domain": "geology", "relation": "DEVELOPS_STRUCTURAL_PLANE", "requirement": "risk_required"},
    {"code": "hazard_body", "label_zh": "灾害体/隐患体", "domain": "hazard", "relation": "CONTAINS_HAZARD_BODY", "requirement": "conditional_required"},
    {"code": "deformation_observation", "label_zh": "近期变形巡检", "domain": "deformation", "relation": "HAS_DEFORMATION_OBSERVATION", "requirement": "risk_required", "interface": "/api/inspections"},
    {"code": "groundwater", "label_zh": "近期地下水", "domain": "environment", "relation": "HAS_GROUNDWATER_OBSERVATION", "requirement": "risk_required", "interface": "/api/environment/latest"},
    {"code": "rainfall", "label_zh": "近期降雨", "domain": "environment", "relation": "AFFECTED_BY_RAINFALL_EVENT", "requirement": "scenario_required", "interface": "/api/environment/latest"},
    {"code": "protection_design", "label_zh": "防护设计", "domain": "protection", "relation": "HAS_PROTECTION_DESIGN", "requirement": "conditional_required"},
    {"code": "protection_condition", "label_zh": "防护设施现状", "domain": "protection", "relation": "HAS_PROTECTION_INSPECTION", "requirement": "risk_required", "interface": "/api/inspections"},
    {"code": "maintenance", "label_zh": "养护历史", "domain": "maintenance", "relation": "HAS_MAINTENANCE_EVENT", "requirement": "risk_required", "interface": "/api/maintenance/events"},
    {"code": "monitoring", "label_zh": "监测数据", "domain": "monitoring", "relation": "HAS_MONITORING_PROGRAM", "requirement": "recommended", "interface": "/api/monitoring/observations"},
    {"code": "stability", "label_zh": "稳定性分析", "domain": "analysis", "relation": "HAS_STABILITY_ANALYSIS", "requirement": "risk_required"},
    {"code": "exposure", "label_zh": "暴露对象与后果", "domain": "risk", "relation": "THREATENS", "requirement": "risk_required", "interface": "/api/exposure"},
    {"code": "risk_rules", "label_zh": "已发布风险规则", "domain": "risk", "global": True, "requirement": "risk_required", "interface": "/api/risk/rules"},
]


INTERFACES: list[dict[str, Any]] = [
    {"path": "/api/documents", "method": "GET", "status": "implemented", "domain": "ingestion", "description": "PDF资料清单、解析状态和逐页策略统计"},
    {"path": "/api/documents/upload", "method": "POST", "status": "implemented", "domain": "ingestion", "description": "安全上传一个或多个PDF，自动校验类型、大小并避免覆盖同名文件"},
    {"path": "/api/pipeline/jobs", "method": "POST", "status": "implemented", "domain": "ingestion", "description": "按 parse_mode=basic|deep 启动基础解析或可选深度解析任务"},
    {"path": "/api/pipeline/jobs/{job_id}", "method": "GET", "status": "implemented", "domain": "ingestion", "description": "查询后台任务真实阶段、进度、结果和错误"},
    {"path": "/api/pipeline/jobs/latest", "method": "GET", "status": "implemented", "domain": "ingestion", "description": "恢复当前服务进程中的最近一次解析任务"},
    {"path": "/api/export/standalone", "method": "POST", "status": "implemented", "domain": "export", "description": "按自定义文件名导出单文件离线HTML，可选择基础、深度或当前图谱"},
    {"path": "/api/export/risk-standalone", "method": "POST", "status": "implemented", "domain": "export", "description": "按自定义文件名导出含筛查结果、证据和交互功能的单文件风险研判HTML"},
    {"path": "/api/slopes", "method": "GET", "status": "implemented", "domain": "slope", "description": "边坡列表及当前基础档案"},
    {"path": "/api/slopes/{slope_id}", "method": "GET", "status": "implemented", "domain": "slope", "description": "单边坡、邻接节点、关系和证据"},
    {"path": "/api/manual/schema", "method": "GET", "status": "implemented", "domain": "manual", "description": "人工补录字段、规则字段及可执行运算符白名单"},
    {"path": "/api/manual/slopes/{slope_id}", "method": "GET/POST/DELETE", "status": "implemented", "domain": "manual", "description": "查询、保存或撤销单边坡人工补录；人工层独立于PDF自动结果"},
    {"path": "/api/manual/rules", "method": "POST", "status": "implemented", "domain": "rules", "description": "新增或修改人工规则；白名单内结构化规则可影响P1-P4排序"},
    {"path": "/api/manual/rules/{rule_id}", "method": "DELETE", "status": "implemented", "domain": "rules", "description": "删除人工规则，不允许删除PDF自动提取规则"},
    {"path": "/api/schema", "method": "GET", "status": "implemented", "domain": "schema", "description": "节点、关系和边坡字段 Schema"},
    {"path": "/api/attribute-dictionary", "method": "GET", "status": "implemented", "domain": "schema", "description": "交通部研究院反馈版边坡属性数据字典 v0.2"},
    {"path": "/api/interfaces", "method": "GET", "status": "implemented", "domain": "schema", "description": "接口状态注册表"},
    {"path": "/api/evaluation", "method": "GET", "status": "implemented", "domain": "quality", "description": "自动抽取回归评测与解析覆盖指标"},
    {"path": "/api/extracted/slopes", "method": "GET", "status": "implemented", "domain": "extraction", "description": "从PDF原生版面自动抽取的候选边坡数据"},
    {"path": "/api/assertions", "method": "GET", "status": "implemented", "domain": "evidence", "description": "带证据和置信度的候选属性断言"},
    {"path": "/api/extracted/llm-candidates", "method": "GET", "status": "implemented", "domain": "extraction", "description": "大模型复杂关系候选；始终保持待人工复核状态"},
    {"path": "/api/completeness", "method": "GET", "status": "implemented", "domain": "quality", "description": "全部边坡的数据完整性报告"},
    {"path": "/api/completeness/{slope_id}", "method": "GET", "status": "implemented", "domain": "quality", "description": "单边坡缺失项和风险就绪度"},
    {"path": "/api/monitoring/observations", "method": "GET", "status": "reserved_not_connected", "domain": "monitoring", "description": "位移、裂缝、地下水、孔压等监测数据"},
    {"path": "/api/environment/latest", "method": "GET", "status": "reserved_not_connected", "domain": "environment", "description": "近期降雨、气温、冻融和地下水"},
    {"path": "/api/maintenance/events", "method": "GET", "status": "reserved_not_connected", "domain": "maintenance", "description": "养护、维修、清危和加固历史"},
    {"path": "/api/inspections", "method": "GET", "status": "reserved_not_connected", "domain": "inspection", "description": "裂缝、滑塌、渗水和设施状态巡检"},
    {"path": "/api/exposure", "method": "GET", "status": "reserved_not_connected", "domain": "risk", "description": "交通量、人员和重要设施等暴露数据"},
    {"path": "/api/risk/rules", "method": "GET", "status": "implemented", "domain": "risk", "description": "候选及已审核风险规则、来源证据和版本；仅approved规则可执行"},
    {"path": "/api/rules/library", "method": "GET", "status": "implemented", "domain": "rules", "description": "规范、条文、公式、阈值表和候选规则完整知识库"},
    {"path": "/api/rules/documents", "method": "GET", "status": "implemented", "domain": "rules", "description": "规则原始资料目录、解析质量和覆盖页"},
    {"path": "/api/rules/formulas", "method": "GET", "status": "implemented", "domain": "rules", "description": "公式候选、变量、单位、适用条件和PDF证据"},
    {"path": "/api/rules/jobs", "method": "POST", "status": "implemented", "domain": "rules", "description": "后台启动规则、公式和阈值提取任务"},
    {"path": "/api/risk/screening", "method": "GET", "status": "implemented", "domain": "risk", "description": "根据现有资料生成宏观人工复核优先级，不等同于正式风险等级"},
    {"path": "/api/risk/evaluation", "method": "GET", "status": "implemented", "domain": "risk", "description": "使用独立专家金标准评测P1-P4一致性、重点对象召回率和严重漏判；无标签时明确返回不可评测"},
    {"path": "/api/risk/readiness", "method": "GET", "status": "implemented", "domain": "risk", "description": "风险研判前的数据就绪检查"},
    {"path": "/api/risk/assess", "method": "POST", "status": "implemented", "domain": "risk", "description": "运行单边坡宏观筛查；正式风险等级仍因规则和动态数据未批准而保持空值"},
    {"path": "/api/multimodal/schema", "method": "GET", "status": "implemented", "domain": "multimodal", "description": "多模态资产、采集、处理、质检和溯源数据契约"},
    {"path": "/api/multimodal/assets", "method": "GET", "status": "implemented", "domain": "multimodal", "description": "STAC式多模态资产目录；未生成时明确返回not_generated"},
    {"path": "/api/multimodal/quality", "method": "GET", "status": "implemented", "domain": "multimodal", "description": "资产元数据完整性和缺失字段质量报告"},
]


def schema_payload() -> dict[str, Any]:
    return {
        "schema_name": "slope_risk_knowledge_graph",
        "schema_version": SCHEMA_VERSION,
        "status": "draft_for_review",
        "node_types": NODE_TYPES,
        "relation_types": RELATION_TYPES,
        "slope_fields": SLOPE_FIELDS,
        "domain_checks": DOMAIN_CHECKS,
        "attribute_dictionary": {
            "path": "/api/attribute-dictionary",
            "schema_version": "0.2.0",
            "status": "feedback_updated",
        },
        "multimodal": {
            "schema_version": MULTIMODAL_SCHEMA_VERSION,
            "contract_path": "/api/multimodal/schema",
            "entity_types": list(multimodal_contract()["entity_types"]),
        },
        "missing_value_semantics": ["present", "absent", "unknown", "not_observed", "not_applicable"],
    }


def attribute_dictionary_payload() -> dict[str, Any]:
    return json.loads(ATTRIBUTE_DICTIONARY_FILE.read_text(encoding="utf-8"))


def interface_payload() -> dict[str, Any]:
    counts = Counter(row["status"] for row in INTERFACES)
    return {"updated_at": datetime.now().isoformat(timespec="seconds"), "status_counts": dict(counts), "interfaces": INTERFACES}


def build_completeness(graph: dict[str, Any]) -> dict[str, Any]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    slopes = [node for node in nodes if node.get("type") == "Slope"]
    outgoing: dict[str, set[str]] = {}
    outgoing_targets: dict[tuple[str, str], list[str]] = {}
    node_by_id = {node.get("id"): node for node in nodes}
    for edge in edges:
        outgoing.setdefault(edge["source"], set()).add(edge["relation"])
        outgoing_targets.setdefault((edge["source"], edge["relation"]), []).append(edge["target"])

    reports = []
    for slope in slopes:
        props = slope.get("props", {})
        fields = []
        for spec in SLOPE_FIELDS:
            value = props.get(spec["code"])
            available = value not in (None, "", [], {})
            if not available and spec.get("alternative_fields"):
                alternative_values = [props.get(code) for code in spec["alternative_fields"]]
                available = any(item not in (None, "", [], {}) for item in alternative_values)
                if available:
                    value = {code: props.get(code) for code in spec["alternative_fields"]}
            fields.append({**spec, "status": "available" if available else "missing", "value": value})
        for spec in DOMAIN_CHECKS:
            if spec.get("global"):
                status = "interface_reserved"
            else:
                direct_codes = {
                    "stability": ("stability_factor", "stability_status"),
                    "exposure": ("consequence_level", "exposure"),
                }.get(spec["code"], (spec["code"],))
                direct_available = any(props.get(code) not in (None, "", [], {}, "未核查") for code in direct_codes)
                available = direct_available or spec["relation"] in outgoing.get(slope["id"], set())
                if available and not direct_available and spec["code"] == "deformation_observation":
                    target_nodes = [
                        node_by_id.get(target_id, {})
                        for target_id in outgoing_targets.get((slope["id"], spec["relation"]), [])
                    ]
                    available = any(
                        node.get("props", {}).get("current_status_known") is True
                        or node.get("props", {}).get("temporal_scope") in {"current", "recent_inspection", "real_time_monitoring"}
                        for node in target_nodes
                    )
                if available:
                    status = "available"
                elif spec.get("interface"):
                    status = "interface_reserved"
                else:
                    status = "missing"
            fields.append({**spec, "status": status, "value": None})

        risk_fields = [row for row in fields if row.get("requirement") in {"risk_required", "scenario_required"}]
        available_count = sum(1 for row in risk_fields if row["status"] == "available")
        completeness = round(available_count / len(risk_fields), 3) if risk_fields else 0
        blocking = [row for row in risk_fields if row["status"] != "available"]
        reports.append(
            {
                "slope_id": slope["id"],
                "label": slope["label"],
                "review_status": props.get("review_status", "pending"),
                "entity_resolution_status": props.get("entity_resolution_status", "provisional"),
                "risk_data_completeness": completeness,
                "risk_readiness": "not_ready" if blocking else "ready_for_rule_engine",
                "blocking_count": len(blocking),
                "blocking_items": [row["code"] for row in blocking],
                "fields": fields,
            }
        )
    average = round(sum(row["risk_data_completeness"] for row in reports) / len(reports), 3) if reports else 0
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "slopes": len(reports),
            "average_risk_data_completeness": average,
            "ready": sum(1 for row in reports if row["risk_readiness"] != "not_ready"),
            "not_ready": sum(1 for row in reports if row["risk_readiness"] == "not_ready"),
        },
        "reports": reports,
    }
