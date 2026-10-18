"""Legacy text route schema and instructions; independent of visual fact contracts."""

PROMPT_VERSION = "slope-semantic-v8-calculation-object-identity"



SEMANTIC_FIELDS = {
    "hazard_body_type",
    "hazard_types",
    "mechanism_summary",
    "failure_modes",
    "lithology_terms",
    "stratum_terms",
    "slope_structure",
    "slope_type",
    "material_nature",
    "slope_length_m",
    "slope_height_min_m",
    "slope_height_max_m",
    "slope_gradient_min_deg",
    "slope_gradient_max_deg",
    "slope_aspect_deg",
    "vegetation_condition",
    "river_relation",
    "overall_slope_deformation_severity",
    "geomorphology",
    "structural_planes",
    "stability_conclusions",
    "deformation_observations",
    "hydrology_observations",
    "protection_records",
    "causal_factors",
    "protection_rationale",
    "uncertainties",
    "evidence_quotes",
}



SYSTEM_PROMPT = """你是公路边坡工程资料结构化抽取器。只允许依据输入原文，不得使用常识补全。
输出严格JSON对象，且只包含以下字段：
hazard_body_type(string|null)、hazard_types(array<string>)、mechanism_summary(string|null)、failure_modes(array<string>)、
lithology_terms(array<string>)、stratum_terms(array<string>)、slope_structure(string|null)、
slope_type(string|null)、material_nature(string|null)、slope_length_m(number|null)、
slope_height_min_m(number|null)、slope_height_max_m(number|null)、slope_gradient_min_deg(number|null)、
slope_gradient_max_deg(number|null)、slope_aspect_deg(number|null)、
vegetation_condition(object|null)、river_relation(object|null)、overall_slope_deformation_severity(string|null)、
geomorphology(string|null)、structural_planes(array<object>)、stability_conclusions(array<object>)、
deformation_observations(array<object>)、hydrology_observations(array<object>)、protection_records(array<object>)、causal_factors(array<string>)、
protection_rationale(string|null)、uncertainties(array<string>)、evidence_quotes(array<object>)。
structural_planes每项包含name、dip_direction、dip_angle、description；未知值用null。
stability_conclusions每项包含condition、safety_factor、required_factor、status、analysis_scope、source_kind、body_id、failure_mode、section；未知值用null，必须分别保留天然、暴雨、地震、治理前、治理后等不同工况。analysis_scope写整体边坡、危岩体、浅层边坡或计算断面；危岩编号写body_id，破坏模式写failure_mode，剖面编号写section，禁止把它们混写成来源类型。共同标题包含多个坡段而计算表未明确唯一归属时，不要将其作为目标边坡独有结果，记录uncertainties。
坡高和坡度只填写目标坡段一般描述；典型剖面、局部危岩尺寸、邻侧自然山体尺寸不能替代整坡属性。基岩坡体上部有土覆盖层不等于整个坡体为土质。
deformation_observations每项包含type、location、scale、timing、status、description；裂缝、掉块、局部垮塌、滑塌、鼓胀等分别记录。
hydrology_observations每项包含type、location、timing、value、unit、description；地下水、地表水、降雨、汇水、渗水和排水条件分别记录。
protection_records每项包含measure、status、location、parameters、description；status只能表达existing、recommended、designed、constructed、damaged或unknown，不得把拟建工程写成现状工程。
枚举规范：slope_type只能为路堤或路堑；material_nature只能为土质、岩质或土岩混合；
vegetation_condition包含density、has_deformation_sign、description，density只能为茂密、稀疏、无或null；
river_relation包含is_riverside、bank、channel_landform、description，其中bank只能为左岸、右岸或null，channel_landform只能为凹岸、凸岸、直线段或null；
overall_slope_deformation_severity只能为基本完好、轻微破坏、中等破坏、严重破坏、毁坏或null。
历史工程报告中的变形、水文和防护描述必须保留其历史时效，不得表述成当前巡检或实时监测。
evidence_quotes每项包含page、quote、supports；quote必须逐字摘自输入原文，建议截取8至80字的短句，禁止改写；
supports是该引句直接支撑的字段名数组，只能从上述字段中选择。每个非空事实字段至少应被一条引句支撑。
同一输入可能包含目录、总表或其他边坡内容，只抽取“目标边坡”的事实。"""



SECTION_KEYWORDS = {
    "slope_type": ["路堑边坡", "路堤边坡", "挖方边坡", "填方边坡", "开挖边坡"],
    "material_nature": ["岩质边坡", "土质边坡", "土岩混合"],
    "slope_height_max_m": ["边坡高", "坡高", "最大高差"],
    "slope_gradient_max_deg": ["边坡坡度", "开挖坡度", "坡度约"],
    "vegetation_condition": ["植被", "绿植", "基岩裸露"],
    "river_relation": ["临河", "河岸", "河道", "凹岸", "凸岸"],
    "deformation_observations": ["裂缝", "变形", "垮塌", "掉块", "滑塌", "崩塌", "落石", "鼓胀", "隆起"],
    "hydrology_observations": ["地下水", "地表水", "降雨", "汇水", "渗水", "滴水", "排水", "水文"],
    "protection_records": ["防护", "治理", "锚喷", "防护网", "放坡", "锚杆", "截水沟", "排水沟", "急流槽"],
    "stability_conclusions": ["稳定性", "安全系数", "稳定系数", "基本稳定", "不稳定", "欠稳定"],
    "structural_planes": ["结构面", "节理", "裂隙", "坡面产状", "岩层产状"],
}
