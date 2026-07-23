# SlopeKG 公路边坡知识图谱 Demo

本项目是公路边坡多源多模态知识图谱的第一版工程 Demo。生产流水线不依赖人工种子，当前已实现：

- 全页自适应 PDF 解析 sidecar：documents/pages/text_blocks/tables/ocr_tasks；按页选择原生版面、表格抽取、整页 OCR 或图纸标题栏 OCR。
- OCR 队列：扫描页/图纸页可渲染为图片；PaddleOCR 可用时自动识别，不可用时标记为待处理。
- 边坡中心图谱：13 个候选 `Slope`，并区分灾害体、灾害类型、防护工程实例、稳定性分析和证据。
- 数据完整性：逐边坡生成缺失项、待接入项和风险研判就绪度。
- 后端 API：安全上传PDF、后台运行流水线、查询真实进度，以及读取图谱、Schema、边坡档案、完整性和预留接口状态。
- 多页面前端：总览、PDF解析、边坡档案、关系图谱、数据与接口、风险研判就绪检查。

## 环境

Conda:

```bash
conda env create -f environment.yml
conda activate slopekg
```

Windows / PowerShell 建议先切 UTF-8：

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

或 pip:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

当前 Demo 不强制依赖云 OCR。未安装 PaddleOCR 时，OCR 页会渲染并进入 `engine_missing` 状态；安装 PaddleOCR 后再次运行流水线即可尝试本地识别。OCR/Paddle 运行缓存会写入 `output/demo/cache/`，避免污染用户目录。

## 运行流水线

```bash
python scripts/run_pipeline.py --ocr-pages 3
```

输出位于：

```text
output/demo/parsed/
output/demo/graph/
output/demo/assets/
output/demo/web/data/demo_graph.json
output/demo/schema.json
output/demo/completeness.json
output/demo/interfaces.json
output/demo/evaluation.json
output/demo/extracted/
```

## 启动前后端 Demo

一键启动：

```powershell
.\scripts\start_demo.ps1
```

或者双击/命令行运行：

```bat
start_demo.bat
```

后端会同时托管前端页面，默认访问：

```text
http://127.0.0.1:8766/web/index.html
```

PDF上传、解析与提取任务页面：

```text
http://127.0.0.1:8766/web/pdf-ingestion.html
```

如果希望启动前先跑一次解析流水线：

```powershell
.\scripts\start_demo.ps1 -RunPipeline -OcrPages 0
```

其中 `-OcrPages 0` 表示只解析 PDF 和生成图谱，不跑 OCR 推理；需要试 OCR 时可以改成 `-OcrPages 1`。

也可以手动启动：

```bash
python scripts/serve_demo.py --port 8765
```

访问：

```text
http://127.0.0.1:8765/web/index.html
```

## API

```text
GET  /api/status
GET  /api/documents
POST /api/documents/upload
POST /api/pipeline/jobs
GET  /api/pipeline/jobs/{job_id}
GET  /api/pipeline/jobs/latest
GET  /api/graph
GET  /api/schema
GET  /api/attribute-dictionary
GET  /api/slopes
GET  /api/slopes/{slope_id}
GET  /api/completeness
GET  /api/completeness/{slope_id}
GET  /api/interfaces
GET  /api/evaluation
GET  /api/extracted/slopes
GET  /api/assertions
GET  /api/extracted/llm-candidates
GET  /api/risk/readiness?slope_id={slope_id}
GET  /api/parsed/documents
GET  /api/parsed/pages
GET  /api/parsed/text_blocks
GET  /api/parsed/tables
GET  /api/parsed/ocr_tasks
GET  /api/parsed/ocr_results
POST /api/pipeline/run
```

以下接口已预留但尚未连接真实数据源：

```text
GET  /api/monitoring/observations
GET  /api/environment/latest
GET  /api/maintenance/events
GET  /api/inspections
GET  /api/exposure
GET  /api/risk/rules
POST /api/risk/assess
```

预留查询接口返回空数组只表示“数据源尚未接入”，不表示现场不存在相应数据。正式风险评估接口当前返回 `501`，防止在规则和动态数据不完整时输出无依据风险等级。

`POST /api/pipeline/run` 示例：

```json
{
  "ocr_pages": 3,
  "force_ocr": false
}
```

## OCR

默认 OCR 方案是 PaddleOCR：

- 扫描规范 PDF。
- 图纸标题栏。
- 图片表格。
- 工程图纸文字标注。

小规模本地 OCR 不需要调用付费大模型；但 PaddleOCR/PaddlePaddle 本身需要安装本地依赖，并且首次运行会下载本地模型。本机 `slopekg` 环境已安装 PaddleOCR 3.7 / PaddlePaddle 3.3，扫描页冒烟测试已确认 Windows CPU 后端会触发 `ConvertPirAttribute2RuntimeAttribute` 的 oneDNN/PIR 兼容错误；Demo 会保留渲染截图、失败任务和错误信息，不阻塞原生文本与图谱生成。后续工程化建议优先二选一：

- 将 PaddlePaddle 固定到更稳的 2.x CPU 版本。
- 增加 RapidOCR/ONNXRuntime 作为轻量 OCR fallback。

## 大模型

主流水线在检测到项目密钥后默认调用 DeepSeek，对复杂机理文本进行语义抽取；输出必须通过JSON结构、原文页码和逐字引句校验后才会进入自动图谱。可用 `--no-llm` 禁用并降级到确定性抽取：

```powershell
python scripts/run_pipeline.py --ocr-pages 0 --llm-model deepseek-v4-flash
```

命令同时输出 `llm_evaluation.json`，记录结构校验、原文引句验证率和缓存命中情况。人工金标准只用于周期性离线审计，不参与生产图谱生成。

密钥读取顺序（项目专用密钥优先，避免被机器上的旧环境变量覆盖）：

1. `.secrets/deepseek_api_key.txt`（已由 `.gitignore` 整体排除）
2. `%USERPROFILE%\.deepseek_api_key`
3. `%USERPROFILE%\.config\deepseek\api_key`
4. 环境变量 `DEEPSEEK_API_KEY`
5. 环境变量 `DEEPSEEK_V4_API_KEY`

当前大模型适合用于：

- 灾害成因机制结构化。
- 稳定性结论抽取。
- 规范条文解释。
- 多句关系抽取。

## 当前数据说明

当前图谱主体是“自动发现 `data/rawPDF/` 下全部PDF + 全页自适应解析 + 确定性精确字段抽取 + 证据约束的大模型语义抽取 + 自动构图”。人工复核可以纠错，但不是运行前置条件。自动化层包括：

- 265 页逐页分类与策略路由（随 `data/rawPDF/` 中资料增减而动态变化）。
- 带页面坐标的文本块和表格 sidecar。
- OCR 任务队列。
- 边坡清单、坐标、治理方案、安全系数、几何与岩性地层候选抽取。
- 回归精度、字段覆盖率和大模型输出结构评测。
- 图谱生成和存储。
- Schema、完整性报告和接口注册表。
- 前端触发流水线。

当前不具备正式风险评估条件。下一步应继续抽取坡高、坡度、结构面等静态字段，并接入巡检、养护、监测、近期环境和暴露对象数据，最后由交通部研究院确认风险规则和审核流程。
