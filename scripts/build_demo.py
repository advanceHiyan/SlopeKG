from __future__ import annotations

import json
import re
from collections import Counter
from html import escape
from pathlib import Path

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - fallback for environments without pypdf
    PdfReader = None


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "rawPDF"
OUT = ROOT / "output" / "demo"

SURVEY_PDF = RAW / "G209-2388.976-2435.900-其他文件-185255.pdf"
DESIGN_PDF = RAW / "G209-2388.976-2435.900-施工图设计文件-185203.pdf"
STANDARD_PDF = RAW / "2021地质灾害评估规范.pdf"


def normalize_stake(value: str) -> str:
    return value.replace("～", "-").replace("~", "-").replace(" ", "")


HAZARDS = [
    {
        "no": 1,
        "stake": "K2388+976-K2389+036",
        "side": "左侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌、危岩体",
        "length_m": 60,
        "lithology": "砂质页岩、页岩",
        "stratum": "志留系",
        "survey_prefix": "SK-X01",
        "design_prefix": "SDZ-X01",
        "survey_pages": ["30", "31"],
        "design_pages": ["46", "47", "48-49"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.318>1.3",
    },
    {
        "no": 2,
        "stake": "K2397+685-K2397+760",
        "side": "右侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌落石、危岩体",
        "length_m": 75,
        "lithology": "砂质页岩、页岩",
        "stratum": "志留系",
        "survey_prefix": "SK-X02",
        "design_prefix": "SDZ-X02",
        "survey_pages": ["32-33", "34-36"],
        "design_pages": ["50", "51-53", "54-56"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.325>1.3",
    },
    {
        "no": 3,
        "stake": "K2398+300-K2398+439",
        "side": "右侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌落石",
        "length_m": 139,
        "lithology": "砂质页岩、页岩",
        "stratum": "志留系纱帽群",
        "survey_prefix": "SK-X03",
        "design_prefix": "SDZ-X03",
        "survey_pages": ["37", "38-40"],
        "design_pages": ["57", "58-60", "61-63"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.324>1.3",
    },
    {
        "no": 4,
        "stake": "K2401+227-K2401+307",
        "side": "右侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌、危岩体",
        "length_m": 80,
        "lithology": "砂质页岩、页岩",
        "stratum": "志留系纱帽群",
        "survey_prefix": "SK-X04",
        "design_prefix": "SDZ-X04",
        "survey_pages": ["41", "42-43"],
        "design_pages": ["64", "65-66", "67-68"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.313>1.3",
    },
    {
        "no": 5,
        "stake": "K2402+458-K2402+500",
        "side": "右侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌落石",
        "length_m": 42,
        "lithology": "砂质页岩、页岩",
        "stratum": "志留系纱帽群",
        "survey_prefix": "SK-X05",
        "design_prefix": "SDZ-X05",
        "survey_pages": ["44", "45-46"],
        "design_pages": ["69", "70-71", "72-74"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.321>1.3",
    },
    {
        "no": 6,
        "stake": "K2402+780-K2402+826",
        "side": "右侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌落石、危岩体",
        "length_m": 46,
        "lithology": "砂质页岩、页岩",
        "stratum": "志留系纱帽群",
        "survey_prefix": "SK-X06",
        "design_prefix": "SDZ-X06",
        "survey_pages": ["47", "48"],
        "design_pages": ["75", "76", "77-79"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.312>1.3",
    },
    {
        "no": 7,
        "stake": "K2410+330-K2410+445",
        "side": "左侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌落石、危岩体",
        "length_m": 115,
        "height_m": "40-65",
        "slope_angle": "63-72°",
        "lithology": "砂质页岩",
        "stratum": "志留系",
        "survey_prefix": "SK-X07",
        "design_prefix": "SDZ-X07",
        "survey_pages": ["49", "50"],
        "design_pages": ["80", "81-83", "84-86"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.356>1.3",
        "mechanism": "道路开挖形成临空面，节理裂隙和风化作用使表层岩体破碎，降雨期间易形成崩塌落石。",
        "stability": [
            {"condition": "天然", "fs": 1.22, "status": "基本稳定", "failure_mode": "滑移式"},
            {"condition": "饱和", "fs": 1.06, "status": "欠稳定", "failure_mode": "滑移式"},
        ],
        "planes": [
            {"name": "坡面", "dip_direction": 184, "dip_angle": 65},
            {"name": "岩层面", "dip_direction": 292, "dip_angle": 47},
            {"name": "L1结构面", "dip_direction": 148, "dip_angle": 54, "density": "5-6条/m", "extension": "6-8m"},
            {"name": "L2结构面", "dip_direction": 88, "dip_angle": 72, "density": "4-5条/m", "extension": "3-4m"},
            {"name": "L3结构面", "dip_direction": 185, "dip_angle": 62, "density": "2-3条/m", "extension": "3-4m"},
        ],
    },
    {
        "no": 8,
        "stake": "K2412+602-K2412+664",
        "side": "右侧",
        "hazard_type": "不稳定边坡",
        "disaster_type": "不稳定边坡",
        "length_m": 62,
        "height_m": "30-40",
        "slope_angle": "53-68°",
        "lithology": "砂质页岩及页岩",
        "stratum": "志留系",
        "survey_prefix": "SK-X08",
        "design_prefix": "SDZ-X08",
        "survey_pages": ["51", "52"],
        "design_pages": ["87", "88", "89", "90-94"],
        "measure": "下部1:0.8放坡+挂网锚喷，上部1:0.8放坡+框架锚杆",
        "treated_safety_factor": "正常工况1.237>1.15；非正常工况I 1.131>1.05",
        "mechanism": "上部覆盖层较厚，下覆砂质页岩易风化软化；道路开挖形成临空面，降雨入渗降低岩土体参数。",
        "stability": [
            {"condition": "正常工况", "fs": 1.237, "status": "稳定", "failure_mode": "圆弧滑动"},
            {"condition": "非正常工况I", "fs": 1.131, "status": "满足安全系数", "failure_mode": "圆弧滑动"},
        ],
        "planes": [
            {"name": "坡面", "dip_direction": 115, "dip_angle": 40},
            {"name": "岩层面", "dip_direction": 302, "dip_angle": 70},
            {"name": "L1结构面", "dip_direction": 100, "dip_angle": 48, "density": "5-6条/m", "extension": "10-30cm"},
            {"name": "L2结构面", "dip_direction": 33, "dip_angle": 70, "density": "3-4条/m", "extension": "1-2m"},
        ],
    },
    {
        "no": 9,
        "stake": "K2391+966-K2392+030",
        "side": "右侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌、危岩体",
        "length_m": 64,
        "lithology": "灰岩",
        "stratum": "三叠系",
        "survey_prefix": "SK-X09",
        "design_prefix": "SDZ-X09",
        "survey_pages": ["53", "54"],
        "design_pages": ["95", "96", "97"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.305>1.3",
        "stability": [
            {"condition": "天然", "fs": 1.18, "status": "基本稳定", "failure_mode": "滑移式"},
            {"condition": "饱和", "fs": 1.05, "status": "欠稳定", "failure_mode": "滑移式"},
        ],
    },
    {
        "no": 10,
        "stake": "K2429+580-K2429+668",
        "side": "左侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌、危岩体",
        "length_m": 88,
        "height_m": "约50",
        "slope_angle": "80-90°",
        "lithology": "灰岩",
        "stratum": "三叠系嘉陵江组",
        "survey_prefix": "SK-X10",
        "design_prefix": "SDZ-X10",
        "survey_pages": ["55", "56-57"],
        "design_pages": ["98", "99-100", "101-102"],
        "measure": "坡面清危+主动防护网+局部单独锚杆",
        "treated_safety_factor": "1.317>1.3",
        "mechanism": "三叠系灰岩节理裂隙发育，岩体破碎，地表水入渗降低结构面参数，易形成崩塌落石。",
        "stability": [
            {"condition": "天然", "fs": 1.24, "status": "基本稳定", "failure_mode": "滑移式"},
            {"condition": "饱和", "fs": 1.11, "status": "欠稳定", "failure_mode": "滑移式"},
        ],
    },
    {
        "no": 11,
        "stake": "K2434+460-K2434+585",
        "side": "左侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌、危岩体",
        "length_m": 125,
        "lithology": "灰岩",
        "stratum": "三叠系嘉陵江组",
        "survey_prefix": "SK-X11",
        "design_prefix": "SDZ-X11",
        "survey_pages": ["58-59", "60-61"],
        "design_pages": ["103", "104-105", "106-107"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.326>1.3",
    },
    {
        "no": 12,
        "stake": "K2435+310-K2435+430",
        "side": "左侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌、危岩体",
        "length_m": 120,
        "height_m": "约40",
        "slope_angle": "80-90°",
        "lithology": "灰岩",
        "stratum": "三叠系嘉陵江组",
        "survey_prefix": "SK-X12",
        "design_prefix": "SDZ-X12",
        "survey_pages": ["62-63", "64-66"],
        "design_pages": ["108", "109-111", "112-113"],
        "measure": "坡面清危+挂网锚喷",
        "treated_safety_factor": "1.316>1.3",
        "stability": [
            {"condition": "天然", "fs": 1.22, "status": "基本稳定", "failure_mode": "滑移式"},
            {"condition": "饱和", "fs": 1.12, "status": "欠稳定", "failure_mode": "滑移式"},
        ],
    },
    {
        "no": 13,
        "stake": "K2435+820-K2435+900",
        "side": "左侧",
        "hazard_type": "危岩体",
        "disaster_type": "崩塌、危岩体",
        "length_m": 80,
        "height_m": "约50",
        "slope_angle": "80-90°",
        "lithology": "灰岩",
        "stratum": "三叠系嘉陵江组",
        "survey_prefix": "SK-X13",
        "design_prefix": "SDZ-X13",
        "survey_pages": ["67", "68-69"],
        "design_pages": ["114", "115-116", "117-119"],
        "measure": "坡面清危+主动防护网+局部单独锚杆",
        "treated_safety_factor": "1.518>1.5",
        "mechanism": "顺层外倾边坡，灰岩节理裂隙发育且坡表破碎，雨季常有石块掉落。",
        "stability": [
            {"condition": "天然", "fs": 1.23, "status": "基本稳定", "failure_mode": "坠落式"},
            {"condition": "饱和", "fs": 1.13, "status": "欠稳定", "failure_mode": "坠落式"},
        ],
    },
]


GENERAL_FACTORS = ["地形地貌", "地层岩性", "节理裂隙", "降雨", "风化作用", "人类工程活动"]


def pdf_stats(path: Path) -> dict:
    stats = {"file": path.name, "path": str(path.relative_to(ROOT)), "pages": None, "text_chars": 0}
    if PdfReader is None or not path.exists():
        return stats
    reader = PdfReader(str(path))
    stats["pages"] = len(reader.pages)
    chars = 0
    for page in reader.pages[: min(len(reader.pages), 12)]:
        try:
            chars += len(page.extract_text() or "")
        except Exception:
            pass
    stats["text_chars_sample"] = chars
    return stats


def evidence_id(prefix: str, page: str) -> str:
    clean = re.sub(r"[^0-9A-Za-z]+", "_", page).strip("_")
    return f"ev_{prefix}_{clean}"


def add_node(nodes: dict, node_id: str, label: str, node_type: str, **props) -> None:
    nodes.setdefault(
        node_id,
        {
            "id": node_id,
            "label": label,
            "type": node_type,
            "summary": props.pop("summary", ""),
            "props": props,
        },
    )


def add_edge(edges: list, source: str, target: str, relation: str, **props) -> None:
    edges.append(
        {
            "id": f"e{len(edges)+1:04d}",
            "source": source,
            "target": target,
            "relation": relation,
            "props": props,
        }
    )


def drawing_id(prefix: str, suffix: str) -> str:
    return f"drawing_{prefix.lower().replace('-', '_')}_{suffix}"


COORDINATES = {
    1: ((356065.841, 3314980.333, 546.208), (356144.584, 3314935.788, 551.970)),
    2: ((360466.306, 3315415.861, 718.747), (360433.296, 3315342.485, 717.212)),
    3: ((360374.454, 3314746.532, 713.348), (360337.713, 3314662.129, 709.446)),
    4: ((361227.758, 3312930.114, 706.138), (361308.836, 3312924.648, 708.078)),
    5: ((361266.355, 3311813.522, 685.651), (361294.796, 3311749.175, 683.194)),
    6: ((361477.831, 3311542.611, 689.779), (361445.000, 3311473.138, 691.807)),
    7: ((359595.009, 3304752.274, 804.042), (359659.650, 3304761.649, 800.102)),
    8: ((359138.396, 3304012.571, 693.150), (359104.321, 3303932.314, 691.414)),
    9: ((358670.181, 3315226.901, 637.244), (358721.807, 3315306.364, 640.666)),
    10: ((354085.550, 3290701.322, 557.209), (354117.274, 3290567.032, 562.983)),
    11: ((355905.344, 3287581.588, 543.130), (355939.342, 3287491.780, 544.332)),
    12: ((356233.467, 3286951.281, 558.613), (356278.599, 3286808.685, 557.971)),
    13: ((356531.290, 3286626.457, 551.853), (356641.005, 3286638.316, 553.423)),
}


def station_to_m(value: str) -> int | None:
    match = re.fullmatch(r"K(\d+)\+(\d+)", value.strip(), re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1)) * 1000 + int(match.group(2))


def numeric_range(value: object) -> tuple[float | None, float | None]:
    if value in (None, ""):
        return None, None
    values = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(value))]
    if not values:
        return None, None
    return min(values), max(values)


def candidate_hazard_types(record: dict) -> list[str]:
    text = f"{record.get('hazard_type', '')} {record.get('disaster_type', '')} {record.get('mechanism', '')}"
    result: list[str] = []
    if any(word in text for word in ["崩塌", "危岩", "落石"]):
        result.extend(["崩塌", "落石"])
    if any(word in text for word in ["滑坡", "不稳定边坡", "溜滑", "滑塌"]):
        result.append("滑坡")
    return list(dict.fromkeys(result)) or ["待识别"]


def factor_names(record: dict) -> list[str]:
    mechanism = record.get("mechanism", "")
    mapping = {
        "降雨": "降雨",
        "风化": "风化作用",
        "节理": "节理裂隙",
        "裂隙": "节理裂隙",
        "开挖": "人类工程活动",
        "地形": "地形地貌",
        "岩性": "地层岩性",
    }
    return list(dict.fromkeys(label for keyword, label in mapping.items() if keyword in mechanism))


def build_graph() -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    evidence: list[dict] = []

    add_node(
        nodes,
        "project_g209",
        "G209宣恩县部分路线段灾害防治工程",
        "Project",
        summary="K2388+976-K2435+900 路段工程资料包",
        review_status="pending",
    )
    add_node(
        nodes,
        "route_k2388_k2435",
        "G209 K2388+976-K2435+900",
        "RouteSegment",
        route_code="G209",
        road_grade="二级公路",
        length_km=46.924,
        start_station_m=2388976,
        end_station_m=2435900,
    )

    docs = [
        ("doc_survey", "工程地质勘察报告", SURVEY_PDF, "勘察报告"),
        ("doc_design", "施工图设计文件", DESIGN_PDF, "施工图"),
        ("doc_standard_gbt40112", "GB/T 40112-2021 地质灾害危险性评估规范", STANDARD_PDF, "规范"),
    ]
    doc_stats = []
    for doc_id, label, path, kind in docs:
        stats = pdf_stats(path)
        doc_stats.append({"id": doc_id, **stats})
        add_node(nodes, doc_id, label, "Document", kind=kind, file=path.name, path=str(path.relative_to(ROOT)), pages=stats.get("pages"))

    add_edge(edges, "project_g209", "route_k2388_k2435", "HAS_ROUTE_SEGMENT")

    for factor in GENERAL_FACTORS:
        fid = "factor_" + re.sub(r"\W+", "_", factor)
        add_node(nodes, fid, factor, "CausalFactor", review_status="pending")

    for h in HAZARDS:
        slope_id = f"slope_{h['no']:02d}"
        stake = normalize_stake(h["stake"])
        start_station, end_station = stake.split("-", 1)
        lith_id = "lith_" + h["lithology"]
        stratum_id = "stratum_" + h["stratum"]
        height_min, height_max = numeric_range(h.get("height_m"))
        angle_min, angle_max = numeric_range(h.get("slope_angle"))
        start_xyz, end_xyz = COORDINATES.get(h["no"], (None, None))
        source_alias = f"{stake}{h['side']}{h['hazard_type']}"

        add_node(
            nodes,
            slope_id,
            f"G209 {stake} {h['side']}边坡",
            "Slope",
            summary=h.get("mechanism", h["disaster_type"]),
            slope_id=f"G209-S{h['no']:02d}",
            source_no=h["no"],
            source_alias=source_alias,
            route_code_cache="G209",
            start_station_raw=start_station,
            end_station_raw=end_station,
            start_station_m=station_to_m(start_station),
            end_station_m=station_to_m(end_station),
            side=h["side"],
            slope_length_m=h.get("length_m"),
            slope_height_m=height_max if height_min == height_max else None,
            slope_height_min_m=height_min,
            slope_height_max_m=height_max,
            slope_height_raw=h.get("height_m"),
            slope_gradient_deg=angle_max if angle_min == angle_max else None,
            slope_gradient_min_deg=angle_min,
            slope_gradient_max_deg=angle_max,
            slope_gradient_raw=h.get("slope_angle"),
            start_coordinate={"x": start_xyz[0], "y": start_xyz[1], "z": start_xyz[2]} if start_xyz else None,
            end_coordinate={"x": end_xyz[0], "y": end_xyz[1], "z": end_xyz[2]} if end_xyz else None,
            coordinate_crs="CGCS2000（国家2000坐标系，具体3度带参数待核验）" if start_xyz else None,
            lifecycle_status="在役（待业务确认）",
            entity_resolution_status="provisional",
            review_status="pending",
            data_status="真实PDF抽样解析+领域种子，待逐项审核",
        )
        add_node(nodes, lith_id, h["lithology"], "Lithology", review_status="pending", source="勘察资料种子")
        add_node(nodes, stratum_id, h["stratum"], "Stratum", review_status="pending", source="勘察资料种子")
        add_edge(edges, "route_k2388_k2435", slope_id, "HAS_SLOPE")
        add_edge(edges, slope_id, "project_g209", "BELONGS_TO_PROJECT")
        add_edge(edges, slope_id, lith_id, "HAS_LITHOLOGY", evidence=evidence_id("survey", "24"), review_status="pending")
        add_edge(edges, slope_id, stratum_id, "HAS_STRATUM", evidence=evidence_id("survey", "24"), review_status="pending")
        add_edge(edges, slope_id, "doc_survey", "RECORDED_IN", evidence=evidence_id("survey", "7"))
        add_edge(edges, slope_id, "doc_design", "RECORDED_IN", evidence=evidence_id("design", "5"))

        body_id = f"hazard_body_{h['no']:02d}"
        add_node(
            nodes,
            body_id,
            source_alias,
            "HazardBody",
            body_type=h["hazard_type"],
            source_description=h["disaster_type"],
            spatial_scope=stake,
            status="candidate_from_design_material",
            review_status="pending",
        )
        add_edge(edges, slope_id, body_id, "CONTAINS_HAZARD_BODY", evidence=evidence_id("survey", "7"), review_status="pending")

        assessment_id = f"susceptibility_{h['no']:02d}"
        hazard_candidates = candidate_hazard_types(h)
        add_node(
            nodes,
            assessment_id,
            f"{stake} 灾害类型候选评价",
            "HazardSusceptibilityAssessment",
            candidate_hazard_types=hazard_candidates,
            level="待评定",
            method="资料标签映射",
            assessment_time=None,
            review_status="pending",
        )
        add_edge(edges, slope_id, assessment_id, "HAS_SUSCEPTIBILITY_ASSESSMENT", evidence=evidence_id("survey", "7"))
        for hazard_name in hazard_candidates:
            type_id = "hazard_type_" + re.sub(r"\W+", "_", hazard_name)
            add_node(nodes, type_id, hazard_name, "HazardType")
            add_edge(edges, assessment_id, type_id, "FOR_HAZARD_TYPE", confidence="candidate", review_status="pending")

        for factor in factor_names(h):
            fid = "factor_" + re.sub(r"\W+", "_", factor)
            add_edge(edges, slope_id, fid, "INFLUENCED_BY", evidence=evidence_id("survey", "24"), review_status="pending")

        measure_names = re.split(r"[+，、,；;]", h["measure"])
        for measure_index, measure in enumerate([m.strip() for m in measure_names if m.strip()], start=1):
            type_id = "protection_type_" + re.sub(r"\W+", "_", measure)
            work_id = f"protection_work_{h['no']:02d}_{measure_index}"
            add_node(nodes, type_id, measure, "ProtectionType")
            add_node(
                nodes,
                work_id,
                f"{stake} {measure}（设计）",
                "ProtectionWork",
                facility_type=measure,
                implementation_status="design_documented_implementation_unknown",
                current_condition="unknown",
                data_gap="缺少竣工、巡检或现场核验资料",
                review_status="pending",
            )
            add_edge(edges, slope_id, work_id, "HAS_PROTECTION_DESIGN", evidence=evidence_id("design", "44"))
            add_edge(edges, work_id, type_id, "INSTANCE_OF")

        for i, page in enumerate(h["survey_pages"], start=1):
            kind = "工程地质平面图" if i == 1 else "工程地质断面图"
            did = drawing_id(h["survey_prefix"], str(i))
            add_node(nodes, did, f"{h['survey_prefix']}-{i:02d} {kind}", "Drawing", drawing_no=f"{h['survey_prefix']}-{i:02d}", drawing_type=kind, page=page, document="勘察报告")
            add_edge(edges, slope_id, did, "SHOWN_IN", purpose="勘察图", page=page)
            add_edge(edges, did, "doc_survey", "IN_DOCUMENT", page=page)
        design_types = ["防护设计平面图", "防护设计断面图", "防护大样图"]
        if h["no"] == 8:
            design_types = ["防护设计平面图", "防护设计断面图", "防护设计立面图", "防护大样图"]
        for i, page in enumerate(h["design_pages"], start=1):
            kind = design_types[min(i - 1, len(design_types) - 1)]
            did = drawing_id(h["design_prefix"], str(i))
            add_node(nodes, did, f"{h['design_prefix']}-{i} {kind}", "Drawing", drawing_no=f"{h['design_prefix']}-{i}", drawing_type=kind, page=page, document="施工图设计文件")
            add_edge(edges, slope_id, did, "SHOWN_IN", purpose="防护设计图", page=page)
            add_edge(edges, did, "doc_design", "IN_DOCUMENT", page=page)

        if h.get("stability"):
            for row in h["stability"]:
                sid = f"stability_{h['no']:02d}_{row['condition']}"
                add_node(nodes, sid, f"{stake} {row['condition']} Fs={row['fs']}", "StabilityAnalysis", **row, analysis_time=None, review_status="pending")
                add_edge(edges, slope_id, sid, "HAS_STABILITY_ANALYSIS", evidence=evidence_id("survey", "24"))
        treated_match = re.search(r"([0-9.]+)\s*>\s*([0-9.]+)", h.get("treated_safety_factor", ""))
        if treated_match:
            sid = f"stability_{h['no']:02d}_treated"
            add_node(
                nodes,
                sid,
                f"{stake} 治理后设计 Fs={treated_match.group(1)}",
                "StabilityAnalysis",
                condition="治理后设计工况",
                fs=float(treated_match.group(1)),
                required_fs=float(treated_match.group(2)),
                status="设计值满足表列要求",
                analysis_time=None,
                review_status="pending",
                limitation="设计资料值，不代表当前现场状态",
            )
            add_edge(edges, slope_id, sid, "HAS_STABILITY_ANALYSIS", evidence=evidence_id("design", "44"))
        if h.get("planes"):
            for idx, plane in enumerate(h["planes"], start=1):
                pid = f"plane_{h['no']:02d}_{idx}"
                add_node(nodes, pid, f"{stake} {plane['name']}", "StructuralPlane", **plane, review_status="pending")
                add_edge(edges, slope_id, pid, "DEVELOPS_STRUCTURAL_PLANE", evidence=evidence_id("survey", "24"))

    evidence.extend(
        [
            {
                "id": evidence_id("survey", "5"),
                "source_file": SURVEY_PDF.name,
                "page": 5,
                "kind": "目录表",
                "text": "勘察目录列出 13 个灾害点及对应工程地质平面图、断面图页码。",
            },
            {
                "id": evidence_id("survey", "7"),
                "source_file": SURVEY_PDF.name,
                "page": 7,
                "kind": "灾害点一览表",
                "text": "表 1-1 G209 沿线灾害点一览表，包含桩号、长度、灾害类型。",
            },
            {
                "id": evidence_id("survey", "24"),
                "source_file": SURVEY_PDF.name,
                "page": 24,
                "kind": "工程地质正文",
                "text": "K2410+330-K2410+445 和 K2412+602-K2412+664 的岩性、结构面、稳定性分析。",
            },
            {
                "id": evidence_id("survey", "35"),
                "source_file": SURVEY_PDF.name,
                "page": 35,
                "kind": "防治措施建议表",
                "text": "表 6.2-1 G209 宣恩段各灾点防治措施建议。",
            },
            {
                "id": evidence_id("design", "5"),
                "source_file": DESIGN_PDF.name,
                "page": 5,
                "kind": "施工图目录",
                "text": "本册目录列出各灾害点防护设计平面图、断面图、大样图及页码。",
            },
            {
                "id": evidence_id("design", "44"),
                "source_file": DESIGN_PDF.name,
                "page": 44,
                "kind": "治理方案表",
                "text": "地质灾害治理方案一览表列出治理措施和治理后安全系数。",
            },
            {
                "id": evidence_id("standard", "1"),
                "source_file": STANDARD_PDF.name,
                "page": 1,
                "kind": "扫描规范封面",
                "text": "GB/T 40112-2021 地质灾害危险性评估规范，扫描型 PDF，后续需要 OCR。",
            },
        ]
    )

    node_type_counts = Counter(n["type"] for n in nodes.values())
    edge_type_counts = Counter(e["relation"] for e in edges)

    return {
        "meta": {
            "title": "公路边坡风险知识图谱 Demo",
            "description": "以 G209 宣恩县部分路线段 13 个候选边坡为核心的可追溯图谱。",
            "schema_version": "1.0.0-draft",
            "data_status": "历史资料基线；动态监测、养护、现场状态和正式风险规则尚未接入。",
            "generated_from": [str(SURVEY_PDF.relative_to(ROOT)), str(DESIGN_PDF.relative_to(ROOT)), str(STANDARD_PDF.relative_to(ROOT))],
            "stats": {
                "nodes": len(nodes),
                "edges": len(edges),
                "evidence": len(evidence),
                "node_type_counts": dict(node_type_counts),
                "edge_type_counts": dict(edge_type_counts),
            },
            "documents": doc_stats,
        },
        "nodes": list(nodes.values()),
        "edges": edges,
        "evidence": evidence,
        "source_records": HAZARDS,
    }


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def write_graphml(path: Path, graph: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '<key id="label" for="all" attr.name="label" attr.type="string"/>',
        '<key id="type" for="node" attr.name="type" attr.type="string"/>',
        '<key id="relation" for="edge" attr.name="relation" attr.type="string"/>',
        '<graph id="SlopeKGDemo" edgedefault="directed">',
    ]
    for node in graph["nodes"]:
        lines.append(f'  <node id="{escape(node["id"])}">')
        lines.append(f'    <data key="label">{escape(node["label"])}</data>')
        lines.append(f'    <data key="type">{escape(node["type"])}</data>')
        lines.append("  </node>")
    for edge in graph["edges"]:
        lines.append(f'  <edge id="{edge["id"]}" source="{escape(edge["source"])}" target="{escape(edge["target"])}">')
        lines.append(f'    <data key="relation">{escape(edge["relation"])}</data>')
        lines.append("  </edge>")
    lines.extend(["</graph>", "</graphml>"])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    raise SystemExit(
        "This legacy seed fixture is disabled for production output. "
        "Run scripts/run_pipeline.py; the active pipeline builds the graph from submitted PDFs without manual seeds."
    )


if __name__ == "__main__":
    main()
