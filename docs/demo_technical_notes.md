# 公路边坡知识图谱 Demo 技术说明

> 2026-07-14 更新：生产流水线已改为无人工种子自动构图。系统自动发现 `data/rawPDF/` 中的PDF，结合确定性抽取与带原文证据校验的大模型语义抽取生成 `Slope` 中心图谱。人工数据只允许用于独立离线审计，不参与生产生成。

## 1. 当前 Demo 的数据状态

当前 Demo 是第一版“可运行原型”，核心目标是先证明图谱产品形态和数据组织方式可行。现在已经从单纯静态页面扩展为“后端流水线 API + 前端控制台”的分层架构。

当前图谱是完整自动抽取流水线产物，运行时不读取人工种子。

当前流程：

```text
data/rawPDF/ 下全部 PDF
  -> slopekg.parsers.PdfParser 生成 documents/pages/text_blocks/tables/ocr_tasks
  -> slopekg.ocr.OcrRunner 渲染 OCR 页并在 PaddleOCR 可用时识别
  -> 确定性解析桩号、坐标、表格数值和治理措施
  -> DeepSeek 抽取复杂语义，并校验原文引句和字段值
  -> slopekg.graph_builder 直接由自动候选生成 nodes/edges/evidence/GraphML
  -> slopekg.server 提供 API，web/ 前端读取 /api/graph 和 /api/status 展示
```

当前精确字段保留页码、文本块ID和bbox；复杂语义仅在字段值原文匹配或逐字引句校验通过后进入图谱。

当前入口：

- `scripts/run_pipeline.py`
- `scripts/serve_demo.py`
- `scripts/build_demo.py`：仅保留为历史离线夹具，直接执行已被阻断，不能覆盖生产图谱。

当前输出：

```text
output/demo/graph/nodes.json
output/demo/graph/edges.json
output/demo/graph/evidence.json
output/demo/graph/nodes.jsonl
output/demo/graph/edges.jsonl
output/demo/graph/evidence.jsonl
output/demo/graph/demo.graphml
output/demo/web/data/demo_graph.json
```

## 2. 当前 Demo 如何存储和维护

### 2.1 静态图谱文件

当前没有直接接 Neo4j、PostGIS 或向量数据库，而是先用“后端 API + 轻量文件存储”：

- `JSON`：供前端直接读取。
- `JSONL`：便于后续批处理、导入数据库、人工审查。
- `GraphML`：便于 Gephi、NetworkX、图数据库导入测试。
- `pipeline_state.json`：记录解析、OCR、依赖、LLM 密钥检测和图谱输出状态。

这种方式的优点是：

- 启动成本低。
- 不依赖数据库服务。
- 方便版本化和比较差异。
- 前端可以直接演示。

缺点是：

- 不是增量图数据库。
- 无法高效做复杂图查询。
- 证据、版本、审核状态还没有完整数据库约束。

### 2.2 后续建议存储结构

建议逐步演进为四类存储：

```text
文件存储
  原始 PDF、渲染页图、OCR 结果、图纸截图、遥感/点云文件

结构化表
  documents / pages / blocks / tables / drawings / evidence

图数据库
  hazard_points / lithology / structural_planes / measures / drawings / standards

向量数据库
  text_chunks / entity_descriptions / drawing_descriptions / community_reports
```

推荐落地组合：

- Demo 阶段：JSONL + GraphML + 静态前端。
- 开发阶段：SQLite/DuckDB + NetworkX + JSONL。
- 图查询阶段：Neo4j 或 NebulaGraph。
- 空间阶段：PostGIS。
- RAG 阶段：Qdrant、LanceDB 或 Milvus。
- 文件资产：本地文件系统或 MinIO。

## 3. 后续自动信息抽取流水线

后续应从当前静态种子数据演进为可重复流水线：

```text
原始文件注册
  -> PDF/Office/图片解析
  -> page/block/table/drawing sidecar
  -> OCR 和图纸标题栏识别
  -> 文本 chunk 与表格结构化
  -> 领域规则抽取
  -> LLM 结构化抽取
  -> 实体归一和关系融合
  -> 证据绑定
  -> 人工校核
  -> 图谱发布
```

### 3.1 PDF 解析

工具链：

- `pypdf`：读取页数、元数据、文字型 PDF 的基础文本。
- `pdfplumber`：抽取文字坐标、表格、版面块。
- `PyMuPDF`：渲染页面、抽图像、抽块级结构。
- `Poppler`：稳定渲染 PDF 页为图片，方便 OCR 和视觉检查。

适用对象：

- G209 勘察报告。
- G209 施工图设计文件。
- 规范 PDF。
- 后续其他工程文档。

### 3.2 表格抽取

工具链：

- `pdfplumber`：优先处理文字型 PDF 表格。
- `pandas/openpyxl`：处理 Excel。
- `PaddleOCR PP-Structure`：处理扫描表格、图片表格。

优先抽取表：

- 灾害点一览表。
- 勘察目录。
- 施工图目录。
- 稳定性计算表。
- 治理方案表。
- 工程数量表。
- 规范中的判定标准表。

输出结构：

```json
{
  "table_id": "survey_p7_hazard_list",
  "source_file": "G209-...其他文件.pdf",
  "page": 7,
  "rows": []
}
```

### 3.3 文本抽取

工具链：

- 规则抽取：正则、词典、单位归一。
- LLM 抽取：对成因机制、稳定性结论、防治建议做结构化 JSON 抽取。
- `rapidfuzz`：实体别名和相似桩号归一。

规则适合抽：

```text
K2410+330-K2410+445
302º∠70º
Fs=1.22
C20 喷砼
Ф8@300×300mm
锚杆间距 2.1×2.1m
```

LLM 适合抽：

- 成因机制。
- 稳定性评价。
- 防治设计理由。
- 规范条文含义。
- 多句话隐含关系。

### 3.4 图纸和图片抽取

第一阶段只做标题栏、图号、图名、比例、桩号、页码和主要文字标注。

工具链：

- `PyMuPDF/Poppler`：渲染图纸页。
- `PaddleOCR`：识别标题栏和文字标注。
- 规则：图号、桩号、比例、页码解析。

第二阶段再做图元：

- 线段、箭头、等高线、坡面线。
- 锚杆、防护网、截水沟、挡墙等符号。
- 图例和岩性符号。

如果有 CAD/DWG，应优先解析 CAD，而不是从 PDF 图片反推。

## 4. OCR 选型

### 4.1 推荐使用 PaddleOCR

当前项目 OCR 建议默认使用 PaddleOCR，原因：

- 中文识别效果相对更稳。
- 支持文本检测、文本识别、方向分类。
- 有 PP-Structure，可扩展到版面分析和表格结构识别。
- 对扫描规范、图片表格、图纸标题栏更合适。

适用场景：

- `2021地质灾害评估规范.pdf` 这类扫描规范。
- 工程图纸标题栏。
- 图纸中的桩号、比例、图名、尺寸标注。
- 图片表格。
- 拍照文档。

### 4.2 备选方案

- `Tesseract`：部署简单，但中文工程文档、图纸、表格效果通常不如 PaddleOCR。
- `Docling/MinerU`：适合后续做更完整的文档结构化解析，可以作为第二阶段增强。
- 多模态大模型：适合复杂图文页理解和人工辅助抽取，但批量处理时必须加 schema 约束和证据校验。

### 4.3 OCR 输出格式建议

OCR 不应只保存纯文本，应保存结构化结果：

```json
{
  "ocr_id": "ocr_standard_p1_0001",
  "source_file": "2021地质灾害评估规范.pdf",
  "page": 1,
  "text": "地质灾害危险性评估规范",
  "bbox": [120, 320, 680, 380],
  "confidence": 0.97,
  "engine": "PaddleOCR",
  "review_status": "pending"
}
```

## 5. GraphRAG / LightRAG 思路如何融入

### 5.1 借鉴 GraphRAG

后续可以加入：

- `document -> text_unit -> entity/relation` 证据链。
- 实体和关系跨 chunk 合并。
- 社区检测。
- 灾害点报告、路线段报告、防护措施报告。
- 查询时结合社区报告和原文证据。

### 5.2 借鉴 LightRAG

后续可以加入：

- Parser registry。
- Sidecar 中间层。
- 文档处理状态。
- chunk/entity/relation 三类向量索引。
- 多模态 item 注入为图节点。

本项目的定制重点：

- 不是通用实体图，而是公路边坡领域图。
- 图谱中心不是 chunk，而是灾害点、桩号、图纸和工程措施。
- 每条知识必须有 PDF 文件、页码、bbox 或表格行作为证据。

## 6. 环境管理

已提供：

- `environment.yml`
- `requirements.txt`

推荐创建 conda 环境：

```bash
conda env create -f environment.yml
conda activate slopekg
```

如果只用 pip：

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

重新生成 Demo 数据：

```bash
python scripts/run_pipeline.py --ocr-pages 0 --llm-model deepseek-v4-flash
```

启动前端：

```bash
python -m http.server 8765
```

访问：

```text
http://127.0.0.1:8765/web/index.html
```

## 7. 下一步建议

建议下一步把当前静态种子图谱拆成三个可自动运行的模块：

1. `parse_pdfs.py`
   - 生成 documents、pages、text_blocks、rendered_pages。
2. `extract_tables.py`
   - 抽目录表、灾害点表、治理方案表、稳定性表。
3. `build_graph.py`
   - 从抽取结果构建 nodes、edges、evidence。

这样就能从“静态 demo”演进到“可重复构建的工程流水线”。
