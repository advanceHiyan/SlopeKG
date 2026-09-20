# SlopeKG 公路边坡知识图谱 Demo

最新进展见[2026年9月进展报告（截至9月20日，含9月14—20日周进展）](docs/SlopeKG月度进展报告_20260920.md)。报告附代码核查、复测结果和证据摘要，区分已实现功能与尚未验收事项。

本项目是公路边坡多源多模态知识图谱的第一版工程 Demo。生产流水线不依赖人工种子，当前已实现：

- 全页自适应 PDF 解析 sidecar：documents/pages/text_blocks/tables/ocr_tasks；按页选择原生版面、表格抽取、整页 OCR 或图纸标题栏 OCR。
- OCR 队列：扫描页/图纸页可渲染为图片；PaddleOCR 可用时自动识别，不可用时标记为待处理。
- 边坡中心图谱：当前资料对应62处边坡，并区分灾害体、灾害类型、防护工程实例、稳定性分析和证据。
- 数据完整性：逐边坡生成缺失项、待接入项和风险研判就绪度。
- 后端 API：安全上传PDF、后台运行流水线、查询真实进度，以及读取图谱、Schema、边坡档案、完整性和预留接口状态。
- 多模态资产层：数据契约、STAC式资产清单、SHA-256校验、PDF视觉资产溯源和元数据质量报告；缺少原始空间数据时不生成伪分析结果。
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
output/demo/multimodal/
```

现有PDF视觉资产可先建立统一清单；如果以后取得无人机影像、GeoTIFF、LAS/LAZ或三维模型，可把目录作为参数追加扫描：

```bash
python scripts/build_multimodal_catalog.py
python scripts/build_multimodal_catalog.py data/multimodal D:/external/sample_assets
```

空间文件旁可放置同名的`.asset.json`元数据，例如`S01.laz.asset.json`，填写`slope_id`、`crs`、`bbox`、`acquisition_time`和`acquisition_event_id`。缺少这些字段时，质量报告只标记为待补，不会自动猜测。

PDF主流程和本地缓存重建现在会自动刷新图片目录，并将校验通过的图片接入图谱的“来源文档—图片—来源页”关系。文件不存在、图片损坏、校验和不一致、无效页码、无效文档/边坡编号或重复资产ID都会写入质量报告，有错误的资产不进入图谱。单个元数据文件损坏不会中断其他资产的盘点。

从“数据与接口”进入“图片资料”，或打开 `/web/assets.html`，可按文档、PDF页码、图片类型及核对状态筛选并查看原图和来源PDF。图片关联到文档不代表已经确认其边坡归属。即使元数据提供了`slope_id`，也只有明确记录`slope_review_status: "approved"`且编号有效时才建立边坡关系；程序不会自动填写这一审核状态。

文字识别保留原始文本框，对紧邻、居中且较短的中文换行标签额外生成可追溯的拼接候选；图纸标题栏保留完整底部区域。当前固定对照集为11页、55项，覆盖9份PDF，其中新增3页为AI原页核对样本，尚未经独立人工复核。此结果只用于内部回归，不代表全库准确率或风险判断效果。

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
.\scripts\start_demo.ps1 -RunPipeline
```

`-RunPipeline` 默认只做基础解析和图谱更新，不跑 OCR。需要OCR时显式指定页数，例如：

```powershell
.\scripts\start_demo.ps1 -RunPipeline -OcrPages 10
```

OCR设备、并发渲染数和推理批量可显式配置：

```powershell
.\scripts\start_demo.ps1 -RunPipeline -OcrPages 10 -OcrDevice auto -OcrWorkers 4 -OcrBatchSize 0
```

`auto`采用GPU优先、CPU优雅降级的硬件自适应策略：PaddlePaddle支持CUDA且检测到GPU时选择GPU，否则选择CPU；批量值为0时使用CPU 1页/GPU 4页。并发参数用于PDF页面渲染，单GPU通过批量推理提高吞吐，避免同时加载多个模型副本。

新机器可运行下面的脚本自动选择OCR运行后端；有NVIDIA GPU时安装GPU版，没有时安装CPU版：

```powershell
.\scripts\setup_ocr_backend.ps1 -Backend auto
```

OCR初始化或首次下载模型时可能显示多个进度条，这是同一次解析任务的不同模型阶段，不表示服务启动了多次。服务自身有端口单实例检查，重复执行启动命令只会返回现有地址。

服务运行期间会自动监听 `data/rawPDF`。不要在前端解析任务尚未结束时同时运行 `scripts/run_pipeline.py`；程序已增加跨进程任务锁，检测到并发解析时会直接提示已有任务及其进程号，避免两个任务同时写入结果文件。

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
GET  /api/manual/schema
GET  /api/manual/slopes/{slope_id}
POST /api/manual/slopes/{slope_id}
DELETE /api/manual/slopes/{slope_id}
POST /api/manual/rules
DELETE /api/manual/rules/{rule_id}
GET  /api/completeness
GET  /api/completeness/{slope_id}
GET  /api/interfaces
GET  /api/multimodal/schema
GET  /api/multimodal/assets
GET  /api/multimodal/quality
GET  /api/evaluation
GET  /api/extracted/slopes
GET  /api/assertions
GET  /api/extracted/llm-candidates
GET  /api/risk/readiness?slope_id={slope_id}
GET  /api/risk/rules
GET  /api/risk/screening
POST /api/risk/assess
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
```

预留查询接口返回空数组只表示“数据源尚未接入”，不表示现场不存在相应数据。风险接口当前输出P1—P4人工复核顺序，不输出正式风险等级。人工补录保存在独立数据层，不会被PDF重解析覆盖；人工规则仅在字段、运算符和输出均属于白名单且状态为“审核启用”时参与排序，复杂规则会保存为不可执行记录。

`POST /api/pipeline/run` 示例：

```json
{
  "ocr_pages": 3,
  "force_ocr": false,
  "ocr_device": "auto",
  "ocr_workers": 4,
  "ocr_batch_size": 0
}
```

## OCR

默认 OCR 方案是 PaddleOCR：

- 扫描规范 PDF。
- 图纸标题栏。
- 图片表格。
- 工程图纸文字标注。

小规模本地 OCR 不需要调用付费大模型；但 PaddleOCR/PaddlePaddle 本身需要安装本地依赖，并且首次运行会下载本地模型。本机 `slopekg` 环境已安装 PaddleOCR 3.7 / PaddlePaddle GPU 3.3，PP-OCRv6检测和识别模型已在RTX 4060上通过实测。Demo 会保留渲染截图、失败任务和错误信息，不阻塞原生文本与图谱生成。后续工程化建议优先：

- 安装与当前平台兼容的 `paddlepaddle-gpu` 后使用 `-OcrDevice gpu`；
- 保留 RapidOCR/ONNXRuntime 作为CPU轻量OCR fallback；
- 对扫描页、图纸标题栏按收益排序，不对所有PDF页面无差别OCR。

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

- 截至2026年9月20日，现有9份PDF、651页逐页分类与策略路由（资料量随 `data/rawPDF/` 中文件增减而变化；该资料规模不代表9月新增量）。
- 带页面坐标的文本块和表格 sidecar。
- OCR 任务队列。
- 边坡清单、坐标、治理方案、安全系数、几何与岩性地层候选抽取。
- 回归精度、字段覆盖率和大模型输出结构评测。
- 图谱生成和存储。
- Schema、完整性报告和接口注册表。
- 前端触发流水线。

当前不具备正式风险评估条件。下一步应继续抽取坡高、坡度、结构面等静态字段，并接入巡检、养护、监测、近期环境和暴露对象数据，最后由交通部研究院确认风险规则和审核流程。
