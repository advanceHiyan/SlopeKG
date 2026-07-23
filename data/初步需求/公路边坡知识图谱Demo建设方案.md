# 公路边坡知识图谱 Demo 建设方案

## 1. Demo 定位

结合当前需求、信息抽取技术调研、GraphRAG 和 LightRAG 的源码调研启发，建议先做一个“灾害点中心”的公路边坡知识图谱 Demo。

Demo 不追求一开始覆盖所有多模态能力，而是优先证明三件事：

1. 现有图文 PDF 可以被解析成可追溯的结构化知识。
2. 勘察报告、施工图、规范、图纸页可以围绕同一个灾害点对齐。
3. 图谱可以支撑查询、子图展示和后续 GraphRAG/LightRAG 式问答。

当前最适合做 Demo 的核心数据是：

- `data/rawPDF/G209-2388.976-2435.900-其他文件-185255.pdf`
  - 工程地质勘察报告。
  - 含灾害点目录、地形地貌、地层岩性、地质构造、灾害点成因机制、稳定性分析、工程地质平面图/断面图。
- `data/rawPDF/G209-2388.976-2435.900-施工图设计文件-185203.pdf`
  - 施工图设计文件。
  - 含设计依据、设计标准、灾害点防治设计、工程数量表、防护设计平面图、断面图、大样图。
- `data/rawPDF/2021地质灾害评估规范.pdf`
  - GB/T 40112-2021《地质灾害危险性评估规范》。
  - 扫描型 PDF，适合作为 OCR 和规范条文抽取样例。
- `data/rawPDF/人工智能资料/系统提供属性表-边坡.docx`
  - 可作为领域属性体系、本体字段设计的参考。

## 2. GraphRAG 和 LightRAG 对本项目的启发

### 2.1 从 GraphRAG 借鉴的部分

GraphRAG 的主线是：

```text
documents -> text_units -> entities/relationships -> communities -> community_reports -> RAG
```

对本项目有价值的机制：

- `Document -> TextUnit -> Entity/Relation` 的证据链，每条实体和关系能回溯到原始 chunk。
- 多个 chunk 中同名实体合并，并生成实体摘要。
- 关系按来源 chunk 聚合，保留关系描述和权重。
- 社区检测和社区报告可用于生成路线段、灾害点、措施类型等主题总结。
- Claims/Covariates 思路可用于表达“某灾害点在某工况下稳定性为欠稳定”这类带状态的断言。

### 2.2 从 LightRAG 借鉴的部分

LightRAG 的主线是：

```text
raw file -> parser -> sidecar -> chunks -> entity/relation extraction -> graph/vector/KV storage
```

对本项目有价值的机制：

- Parser registry：不同数据类型走不同解析器。
- Sidecar：表格、图片、公式、图纸、标题、位置等结构化中间结果先保存，不急于全部进图。
- 文档状态管理：支持增量构建和失败重跑。
- 多种 chunker：固定 token、递归字符、语义切块、段落结构切块。
- 图、向量、KV 分层存储，便于工程化落地。
- 多模态 item 可作为图节点注入，例如表格、图片、公式；本项目可扩展为图纸、遥感图、点云对象。

### 2.3 不能直接照搬的地方

GraphRAG 和 LightRAG 主要面向通用文本知识图谱，而本项目是工程领域多模态图谱，需要补强：

- 领域本体：需要 `HazardPoint`、`StakeRange`、`Lithology`、`StructuralPlane`、`ProtectionMeasure` 等专门实体。
- 表格细粒度：稳定性计算表、工程数量表不能只作为一个 table 节点，应解析成属性和断言。
- 图纸理解：平面图、断面图、大样图需要标题栏、图号、比例、桩号、尺寸、符号等专门抽取。
- 空间表达：桩号、左右侧、高程、坡度、坡向、坐标、DEM/点云对象需要进入图谱或空间数据库。
- 关系类型：工程关系需要有明确谓词，而不是泛泛的 “related to”。

## 3. Demo 总体架构

建议采用“五层架构”：

```text
原始资料层
  -> 解析 Sidecar 层
  -> 领域抽取层
  -> 图谱融合层
  -> 检索与可视化层
```

### 3.1 原始资料层

保存原始文件，不直接修改。

```text
data/rawPDF/
  G209-2388.976-2435.900-其他文件-185255.pdf
  G209-2388.976-2435.900-施工图设计文件-185203.pdf
  2021地质灾害评估规范.pdf
  人工智能资料/
```

### 3.2 解析 Sidecar 层

参考 LightRAG 的 sidecar 思路，将 PDF 解析成结构化中间结果：

```text
parsed/
  documents.jsonl
  pages.jsonl
  blocks.jsonl
  tables.jsonl
  drawings.jsonl
  figures.jsonl
  ocr_blocks.jsonl
  assets/
```

核心对象：

- `Document`：原始文件。
- `Page`：页码、尺寸、是否扫描页。
- `TextBlock`：文本块、bbox、页码。
- `Table`：表格区域、表头、行列、来源页。
- `Drawing`：图纸页、图名、图号、比例、标题栏。
- `Figure`：现场照片、地质构造图、示意图。
- `OCRBlock`：扫描规范或图纸 OCR 结果。

### 3.3 领域抽取层

从 sidecar 中抽取领域实体、属性和关系：

```text
extracted/
  hazard_points.jsonl
  lithology.jsonl
  structural_planes.jsonl
  stability_analysis.jsonl
  protection_measures.jsonl
  drawings.jsonl
  evidence.jsonl
```

技术组合：

- 规则抽取：桩号、倾向倾角、长度、高度、坡度、Fs、图号、页码。
- 表格抽取：目录表、稳定性计算表、工程数量表、设计标准表。
- LLM 抽取：成因机制、稳定性结论、防治设计说明。
- OCR 抽取：扫描规范、图纸标题栏、图纸标注。
- 图像理解：现场照片和图纸页先做文字/标题栏级理解，后续再做图元识别。

### 3.4 图谱融合层

将抽取结果转成节点、边和证据：

```text
graph/
  nodes.jsonl
  edges.jsonl
  claims.jsonl
  evidence.jsonl
  communities.jsonl
  reports.jsonl
```

建议 Demo 阶段可以先用 JSONL/CSV/GraphML 保存，后续再接 Neo4j、JanusGraph 或 NebulaGraph。

### 3.5 检索与可视化层

Demo 阶段建议展示三类能力：

- 图查询：按灾害点、桩号、岩性、防护措施查子图。
- 证据回链：实体和关系能回到 PDF 文件、页码、图号。
- 简单问答：先用结构化查询 + 文本证据，后续再接 GraphRAG/LightRAG。

## 4. Demo 的核心图谱对象

### 4.1 节点类型

第一版建议只保留必要节点，避免图谱过散：

| 节点类型 | 中文名 | 示例 |
| --- | --- | --- |
| `Project` | 项目 | G209 宣恩县部分路线段公路沿线灾害防治工程 |
| `RouteSegment` | 路线段 | K2388+976-K2435+900 |
| `HazardPoint` | 灾害点 | K2410+330-K2410+445 左侧危岩体 |
| `StakeRange` | 桩号区间 | K2410+330-K2410+445 |
| `Slope` | 边坡 | K2410+330-K2410+445 左侧边坡 |
| `HazardType` | 灾害类型 | 危岩体、不稳定边坡、崩塌体 |
| `Stratum` | 地层 | 志留系纱帽群 |
| `Lithology` | 岩性 | 砂质页岩、页岩、灰岩 |
| `StructuralPlane` | 结构面 | L1 结构面、L2 结构面、岩层面 |
| `StabilityAnalysis` | 稳定性分析 | 天然工况 Fs=1.22 |
| `ProtectionMeasure` | 防护措施 | 清危、挂网锚喷、截水沟、急流槽 |
| `Drawing` | 图纸 | SDZ-X08-2 断面图 |
| `Document` | 文档 | 勘察报告、施工图设计文件、规范 |
| `StandardClause` | 规范条文 | 安全等级、设计工况、稳定安全系数 |
| `Evidence` | 证据 | 文件、页码、bbox、抽取方式 |

### 4.2 关系类型

| 关系 | 含义 | 示例 |
| --- | --- | --- |
| `BELONGS_TO` | 属于 | 灾害点 -> 项目 |
| `LOCATED_IN` | 位于 | 灾害点 -> 路线段 |
| `HAS_STAKE_RANGE` | 具有桩号 | 灾害点 -> 桩号区间 |
| `HAS_HAZARD_TYPE` | 灾害类型 | 灾害点 -> 危岩体 |
| `HAS_LITHOLOGY` | 具有岩性 | 灾害点 -> 砂质页岩 |
| `HAS_STRATUM` | 具有地层 | 灾害点 -> 志留系纱帽群 |
| `DEVELOPS_STRUCTURAL_PLANE` | 发育结构面 | 边坡 -> L1 结构面 |
| `HAS_STABILITY_ANALYSIS` | 有稳定性分析 | 灾害点 -> 稳定性分析 |
| `USES_MEASURE` | 采用防护措施 | 灾害点 -> 挂网锚喷 |
| `SHOWN_IN` | 显示于图纸 | 灾害点 -> SDZ-X08-2 |
| `RECORDED_IN` | 记录于文档 | 灾害点 -> 勘察报告 |
| `BASED_ON` | 依据 | 设计说明 -> 公路滑坡防治设计规范 |
| `HAS_EVIDENCE` | 有证据 | 关系/属性 -> Evidence |

### 4.3 属性类型

`HazardPoint` 推荐属性：

- `name`
- `stake_start`
- `stake_end`
- `side`
- `hazard_type`
- `length_m`
- `height_m`
- `slope_angle`
- `description`
- `review_status`

`StructuralPlane` 推荐属性：

- `name`
- `dip_direction`
- `dip_angle`
- `density`
- `extension_length`
- `source_page`

`StabilityAnalysis` 推荐属性：

- `condition`
- `gamma`
- `cohesion`
- `friction_angle`
- `fs`
- `status`
- `failure_mode`

`ProtectionMeasure` 推荐属性：

- `name`
- `material`
- `spacing`
- `length`
- `thickness`
- `quantity`
- `construction_note`

## 5. 基于现有 PDF 的 Demo 映射

### 5.1 从勘察报告目录抽灾害点

`G209-2388.976-2435.900-其他文件-185255.pdf` 的目录页可以直接抽出 13 个灾害点：

| 编号 | 灾害点 | 类型 | 侧别 | 勘察图纸 |
| --- | --- | --- | --- | --- |
| 1 | K2388+976-K2389+036 | 危岩体 | 左侧 | SK-X01 |
| 2 | K2397+685-K2397+760 | 危岩体 | 右侧 | SK-X02 |
| 3 | K2398+300-K2398+439 | 危岩体 | 右侧 | SK-X03 |
| 4 | K2401+227-K2401+307 | 危岩体 | 右侧 | SK-X04 |
| 5 | K2402+458-K2402+500 | 危岩体 | 右侧 | SK-X05 |
| 6 | K2402+780-K2402+826 | 危岩体 | 右侧 | SK-X06 |
| 7 | K2410+330-K2410+445 | 危岩体 | 左侧 | SK-X07 |
| 8 | K2412+602-K2412+664 | 不稳定边坡 | 右侧 | SK-X08 |
| 9 | K2391+966-K2392+030 | 危岩体 | 右侧 | SK-X09 |
| 10 | K2429+580-K2429+668 | 危岩体 | 左侧 | SK-X10 |
| 11 | K2434+460-K2434+585 | 危岩体 | 左侧 | SK-X11 |
| 12 | K2435+310-K2435+430 | 危岩体 | 左侧 | SK-X12 |
| 13 | K2435+820-K2435+900 | 危岩体 | 左侧 | SK-X13 |

图谱映射：

```text
(Project:G209宣恩县灾害防治工程)
  <-[:BELONGS_TO]-
(HazardPoint:K2412+602-K2412+664右侧不稳定边坡)
  -[:HAS_STAKE_RANGE]-> (StakeRange:K2412+602-K2412+664)
  -[:HAS_HAZARD_TYPE]-> (HazardType:不稳定边坡)
  -[:SHOWN_IN]-> (Drawing:SK-X08-01工程地质平面图)
  -[:SHOWN_IN]-> (Drawing:SK-X08-02工程地质断面图)
```

### 5.2 从施工图目录对齐防护设计图纸

`G209-2388.976-2435.900-施工图设计文件-185203.pdf` 的目录页可以将同一批灾害点映射到防护设计图：

```text
K2412+602-K2412+664右侧不稳定边坡
  -> SDZ-X08-1 防护设计平面图
  -> SDZ-X08-2 防护设计断面图
  -> SDZ-X08-3 防护设计立面图
  -> SDZ-X08-4 防护大样图
```

图谱映射：

```text
(HazardPoint:K2412+602-K2412+664右侧不稳定边坡)
  -[:SHOWN_IN {purpose:"防护设计"}]-> (Drawing:SDZ-X08-1)
  -[:SHOWN_IN {purpose:"防护设计"}]-> (Drawing:SDZ-X08-2)
  -[:SHOWN_IN {purpose:"防护设计"}]-> (Drawing:SDZ-X08-3)
  -[:SHOWN_IN {purpose:"防护设计"}]-> (Drawing:SDZ-X08-4)
```

### 5.3 从正文抽地质和稳定性知识

勘察报告正文可抽：

- 地形地貌：中低山地貌区。
- 地层岩性：志留系、三叠系、砂质页岩、页岩、灰岩等。
- 成因机制：地形地貌、地层岩性、岩体结构、降雨、人工开挖。
- 结构面：坡面产状、岩层产状、L1/L2 结构面产状。
- 稳定性结论：基本稳定、欠稳定、易形成崩塌落石等。

施工图正文可抽：

- 设计依据：JTG B01-2014、JTG D30-2015、JTG/T 3334-2018 等。
- 设计工况：正常工况、暴雨或连续降雨、地震等。
- 防护措施：清危、挂网锚喷、截水沟、急流槽、挡墙等。
- 材料规格：C20 喷砼、Ф8 钢筋网、锚杆长度、锚杆间距等。
- 工程数量：各类治理工程量。

### 5.4 从图纸页抽图纸知识

第一版不做完整图元识别，只抽标题栏和图纸文字：

- 图名。
- 图号。
- 比例。
- 桩号。
- 页码。
- 图纸类型。
- 主要文字标注。

例如设计断面图页可以抽：

```text
图名：边坡A-A'典型断面防护设计图
比例：水平 1:200，垂直 1:200
图号：SDZ-X08-2
对象：K2412+602右侧不稳定边坡
标注：锚杆、排水沟、地面线、岩性、高程
```

图谱映射：

```text
(Drawing:SDZ-X08-2)
  -[:DESCRIBES]-> (HazardPoint:K2412+602-K2412+664右侧不稳定边坡)
  -[:HAS_DRAWING_TYPE]-> (DrawingType:防护设计断面图)
  -[:HAS_SCALE]-> (Scale:1:200)
  -[:HAS_EVIDENCE]-> (Evidence:施工图设计文件_page96)
```

### 5.5 从扫描规范抽规则知识

`2021地质灾害评估规范.pdf` 是扫描 PDF。Demo 阶段可以只 OCR 少量页，抽：

- 规范名称。
- 标准编号。
- 发布/实施日期。
- 术语。
- 评价流程。
- 危险性评估相关条文。

图谱映射：

```text
(Document:GB/T 40112-2021地质灾害危险性评估规范)
  -[:CONTAINS]-> (StandardClause:危险性评估术语)
  -[:CONTAINS]-> (StandardClause:评估流程)
```

施工图中的“设计依据”可以连接到规范节点：

```text
(DesignDocument:施工图设计文件)
  -[:BASED_ON]-> (Standard:公路滑坡防治设计规范 JTG/T 3334-2018)
  -[:BASED_ON]-> (Standard:岩土工程勘察规范 GB50021-2001)
```

## 6. Demo 的最小图谱样例

以 `K2412+602-K2412+664 右侧不稳定边坡` 为例，Demo 子图可以是：

```text
Project
  G209宣恩县部分路线段公路沿线灾害防治工程

HazardPoint
  K2412+602-K2412+664右侧不稳定边坡

Attributes
  side: 右侧
  hazard_type: 不稳定边坡
  slope_height: 30-40m
  slope_angle: 53-68°
  lithology: 砂质页岩及页岩
  stratum: 志留系

Relations
  HazardPoint -BELONGS_TO-> Project
  HazardPoint -HAS_STAKE_RANGE-> K2412+602-K2412+664
  HazardPoint -HAS_HAZARD_TYPE-> 不稳定边坡
  HazardPoint -HAS_LITHOLOGY-> 砂质页岩及页岩
  HazardPoint -HAS_STRATUM-> 志留系
  HazardPoint -USES_MEASURE-> 锚杆
  HazardPoint -USES_MEASURE-> 喷砼
  HazardPoint -USES_MEASURE-> 排水设施
  HazardPoint -SHOWN_IN-> SDZ-X08-1
  HazardPoint -SHOWN_IN-> SDZ-X08-2
  HazardPoint -RECORDED_IN-> 勘察报告
  HazardPoint -RECORDED_IN-> 施工图设计文件
```

这个子图能回答：

- 这个灾害点在哪里？
- 它属于什么类型？
- 对应哪些勘察图和设计图？
- 岩性是什么？
- 采用了哪些防护措施？
- 证据来自哪个 PDF 的哪一页？

## 7. Demo 实施步骤

### 7.1 第一步：PDF 解析和 sidecar 生成

输入：

- 勘察报告 PDF。
- 施工图设计 PDF。
- 扫描规范 PDF。

输出：

```text
output/demo/parsed/documents.jsonl
output/demo/parsed/pages.jsonl
output/demo/parsed/text_blocks.jsonl
output/demo/parsed/tables.jsonl
output/demo/parsed/drawings.jsonl
output/demo/parsed/assets/
```

实现方式：

- 用 `pypdf` 抽文字型 PDF 正文。
- 用 Poppler 渲染图纸页和扫描规范页。
- 用 OCR 处理扫描规范和图纸标题栏。
- 暂不做复杂图元识别。

### 7.2 第二步：抽取灾害点目录

输入：

- 勘察报告目录页。
- 施工图目录页。

输出：

```text
output/demo/extracted/hazard_points.jsonl
output/demo/extracted/drawings.jsonl
```

抽取字段：

- 灾害点名称。
- 桩号起止。
- 左右侧。
- 灾害类型。
- 勘察图号。
- 设计图号。
- 页码。
- 来源文档。

### 7.3 第三步：抽取正文知识

输入：

- 勘察报告正文页。
- 施工图设计说明页。

输出：

```text
output/demo/extracted/lithology.jsonl
output/demo/extracted/structural_planes.jsonl
output/demo/extracted/stability_analysis.jsonl
output/demo/extracted/protection_measures.jsonl
```

抽取策略：

- 桩号、长度、高度、坡度、倾向倾角：规则抽取。
- 岩性、地层、防护措施：规则 + 词典。
- 成因机制、稳定性结论：LLM 结构化抽取。
- 工程数量表：表格抽取。

### 7.4 第四步：构建图谱

输出：

```text
output/demo/graph/nodes.jsonl
output/demo/graph/edges.jsonl
output/demo/graph/evidence.jsonl
output/demo/graph/demo.graphml
```

规则：

- 实体按规范化名称合并。
- 桩号区间是核心对齐键。
- 图纸通过图号和桩号关联灾害点。
- 每个属性和关系必须有 evidence。
- 不确定结果标记 `review_status=pending`。

### 7.5 第五步：Demo 查询和展示

第一版可以先做命令行或简单页面，支持：

```text
查询某个灾害点子图
查询某种岩性关联的灾害点
查询某种防护措施用于哪些灾害点
查询某张图纸对应哪个灾害点
查询某个灾害点的证据页
```

后续再接图数据库和问答：

- Neo4j/NebulaGraph：图查询和可视化。
- LanceDB/Qdrant：文本块、实体描述、图纸说明向量检索。
- GraphRAG 社区报告：路线段/灾害点/防护措施主题总结。
- LightRAG 式 hybrid query：chunk + entity + relation 混合检索。

## 8. Demo 目录建议

建议新增：

```text
output/demo/
  parsed/
  extracted/
  graph/
  reports/
  assets/

src/
  slopekg/
    parsers/
      pdf_parser.py
      table_parser.py
      drawing_parser.py
      ocr_parser.py
    extractors/
      hazard_point_extractor.py
      geology_extractor.py
      design_extractor.py
      standard_extractor.py
    graph/
      schema.py
      builder.py
      exporters.py
    demo/
      build_demo.py
      query_demo.py
```

如果只是快速演示，也可以先不搭完整 `src`，先用脚本产出 `nodes.jsonl`、`edges.jsonl` 和 `demo.graphml`。

## 9. Demo 成功标准

第一版 Demo 达到以下效果就算成功：

1. 至少抽出 13 个灾害点节点。
2. 每个灾害点关联勘察图纸和施工图纸。
3. 至少对 2-3 个灾害点抽出岩性、结构面、稳定性、防护措施。
4. 能展示一个灾害点中心子图。
5. 每条关键知识能回到 PDF 文件和页码。
6. 能回答 5 类结构化问题：
   - 某灾害点是什么类型？
   - 某灾害点对应哪些图纸？
   - 某灾害点采用什么防护措施？
   - 哪些灾害点与某种岩性有关？
   - 某防护措施应用在哪些灾害点？

## 10. 后续扩展方向

### 10.1 多模态扩展

- 扫描规范 OCR。
- 图纸标题栏 OCR。
- 图纸符号识别。
- 现场照片灾害迹象识别。
- 遥感影像滑坡/崩塌边界识别。
- 点云坡面几何和结构面识别。

### 10.2 图谱增强

- 加入规范条文和设计依据。
- 加入工程数量表。
- 加入监测指标和时序数据。
- 加入空间数据库，支持桩号和坐标映射。
- 加入版本和增量更新机制。

### 10.3 RAG 增强

- 类 GraphRAG 的社区报告。
- 类 LightRAG 的 chunk/entity/relation 三路检索。
- 多模态证据回链。
- 问答结果引用 PDF 页码、图号和原文片段。

## 11. 建议结论

本项目的 Demo 应该以“灾害点”为中心，而不是以“文档 chunk”为中心。

GraphRAG 和 LightRAG 可以提供技术骨架，但咱们需要在前端解析层和领域本体层做定制：

- 用 LightRAG 的 sidecar 思想解决图文 PDF、表格、图纸、扫描件的结构化中间表示。
- 用 GraphRAG 的实体关系合并、社区报告和证据链思想解决图谱构建与检索问答。
- 用公路边坡领域本体解决实体粒度、关系类型和工程属性表达。

第一版 Demo 最应该做的不是“什么都自动抽一点”，而是把已有 G209 图文 PDF 中的 13 个灾害点做成一张能查询、能回链、能扩展的图谱。这样既能贴合需求，又能为后续多模态抽取、GraphRAG、LightRAG 和三维遥感数据接入打基础。

