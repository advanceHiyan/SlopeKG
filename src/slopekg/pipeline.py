from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable

from .config import PATHS, DemoPaths
from .deps import dependency_report
from .extractors import extract_domain_candidates
from .exporter import export_risk_standalone_html, export_standalone_html
from .graph_builder import attach_visual_assets, build_automatic_graph, write_graphml
from .multimodal import build_multimodal_outputs
from .llm import llm_status, read_deepseek_key
from .manual import apply_slope_overrides, merged_rule_library
from .ocr import OcrRunner, with_layout_groups
from .parsers import PdfParser
from .quality import evaluate_automatic_pipeline
from .risk import build_risk_screening
from .risk_evaluation import evaluate_risk_screening
from .schema import build_completeness, interface_payload, schema_payload
from .semantic import run_semantic_extraction
from .storage import ensure_dir, read_json, read_jsonl, write_json, write_jsonl


class PipelineAlreadyRunningError(RuntimeError):
    pass


def single_pipeline_run(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def guarded(self: "DemoPipeline", *args: Any, **kwargs: Any) -> dict[str, Any]:
        ensure_dir(self.paths.output_dir)
        with pipeline_run_guard(self.paths.output_dir / ".pipeline.lock"):
            return method(self, *args, **kwargs)

    return guarded


@contextmanager
def pipeline_run_guard(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": datetime.now().isoformat(timespec="seconds")}
    descriptor: int | None = None
    for attempt in range(2):
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            break
        except FileExistsError:
            owner = read_pipeline_lock(lock_path)
            owner_pid = owner.get("pid")
            if attempt == 0 and owner_pid and not process_is_running(int(owner_pid)):
                lock_path.unlink(missing_ok=True)
                continue
            started_at = owner.get("started_at") or "未知时间"
            raise PipelineAlreadyRunningError(
                f"已有PDF解析任务正在运行（PID {owner_pid or '未知'}，开始于 {started_at}）。"
                "请等待前端进度完成，不要同时从命令行重复启动流水线。"
            )
    if descriptor is None:
        raise PipelineAlreadyRunningError("无法取得PDF解析任务锁。")
    try:
        yield
    finally:
        os.close(descriptor)
        current = read_pipeline_lock(lock_path)
        if current.get("pid") == os.getpid():
            lock_path.unlink(missing_ok=True)


def read_pipeline_lock(lock_path: Path) -> dict[str, Any]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On POSIX, signal 0 probes a process without changing it. On Windows,
        # os.kill(pid, 0) may call TerminateProcess instead, so use the native
        # read-only process query API.
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Access denied also proves that the process exists.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class DemoPipeline:
    def __init__(self, paths: DemoPaths = PATHS):
        self.paths = paths

    @single_pipeline_run
    def run(
        self,
        ocr_pages: int = 1000,
        force_ocr: bool = False,
        parse_mode: str = "basic",
        use_llm: bool | None = None,
        llm_model: str = "deepseek-v4-flash",
        reuse_parsed: bool = False,
        ocr_scope: str = "all",
        ocr_device: str = "auto",
        ocr_workers: int = 4,
        ocr_batch_size: int = 0,
        progress_callback: Callable[[int, str, str], None] | None = None,
    ) -> dict[str, Any]:
        if use_llm is not None:
            parse_mode = "deep" if use_llm else "basic"
        if parse_mode not in {"basic", "deep"}:
            raise ValueError("parse_mode must be 'basic' or 'deep'")
        if ocr_scope not in {"all", "changed"}:
            raise ValueError("ocr_scope must be 'all' or 'changed'")
        if ocr_device not in {"auto", "cpu", "gpu"}:
            raise ValueError("ocr_device must be 'auto', 'cpu' or 'gpu'")
        ocr_workers = max(1, min(16, int(ocr_workers)))
        ocr_batch_size = max(0, min(32, int(ocr_batch_size)))
        def progress(percent: int, stage: str, message: str) -> None:
            if progress_callback:
                progress_callback(percent, stage, message)

        progress(1, "preparing", "正在检查输入目录和运行环境")
        ensure_dir(self.paths.output_dir)
        if reuse_parsed:
            progress(8, "parsing", "正在载入已有逐页解析结果")
            parsed = self.load_parsed_documents()
            progress(50, "parsing", f"已载入 {len(parsed['documents'])} 份文档、{len(parsed['pages'])} 页")
        else:
            parsed = self.parse_documents(
                progress_callback=lambda current, total, file_name, page_no: progress(
                    5 + round(45 * current / max(total, 1)),
                    "parsing",
                    f"逐页解析 {file_name} 第 {page_no} 页（{current}/{total}）",
                )
            )

        # Publish the native-text result before OCR. A slow or failed OCR pass must
        # never leave the website pointing at a graph from an older PDF set.
        current_tasks = {row.get("id"): row for row in parsed["ocr_tasks"]}
        existing_ocr_results = [
            row for row in read_jsonl(self.paths.parsed_dir / "ocr_results.jsonl")
            if ocr_result_is_current(row, current_tasks.get(row.get("task_id")))
        ]
        checkpoint_tasks = mark_cached_ocr_tasks(parsed["ocr_tasks"], existing_ocr_results)
        checkpoint_parsed = {**parsed, "ocr_results": existing_ocr_results}
        if existing_ocr_results:
            checkpoint_parsed["text_blocks"] = [*parsed["text_blocks"], *existing_ocr_results]
        progress(52, "graph", "正在先发布原生文本基础图谱，避免OCR阻塞更新")
        checkpoint_extracted = extract_domain_candidates(checkpoint_parsed)
        checkpoint_semantic = run_semantic_extraction(checkpoint_extracted, enabled=False, model=llm_model)[1]
        checkpoint_graph, checkpoint_evaluation = self.build_graph(
            checkpoint_parsed,
            checkpoint_tasks,
            existing_ocr_results,
            checkpoint_extracted,
            [],
            checkpoint_semantic,
            parse_mode="basic",
            activate=True,
        )
        self.publish_state(
            checkpoint_parsed,
            checkpoint_tasks,
            existing_ocr_results,
            checkpoint_graph,
            checkpoint_extracted,
            checkpoint_evaluation,
            checkpoint_semantic,
            parse_mode="basic",
            publication_stage="native_checkpoint",
        )

        all_ocr_tasks = parsed["ocr_tasks"]
        changed_document_ids = set(parsed.get("changed_document_ids", []))
        selected_ocr_tasks = (
            [row for row in all_ocr_tasks if row.get("document_id") in changed_document_ids]
            if ocr_scope == "changed"
            else all_ocr_tasks
        )
        progress(55, "ocr", f"基础图谱已更新；正在处理 {len(selected_ocr_tasks)} 个按页识别任务")
        ocr_tasks, ocr_results = self.run_ocr(
            selected_ocr_tasks,
            max_pages=ocr_pages,
            force=force_ocr,
            all_tasks=all_ocr_tasks,
            device=ocr_device,
            workers=ocr_workers,
            batch_size=ocr_batch_size,
            progress_callback=lambda current, total: progress(
                55 + round(7 * current / max(total, 1)),
                "ocr",
                f"基础图谱已发布；按页识别 {current}/{total}",
            ),
        )
        parsed["ocr_results"] = ocr_results
        if ocr_results:
            parsed["text_blocks"] = [*parsed["text_blocks"], *ocr_results]
        progress(63, "extracting", "正在抽取边坡、工程属性与证据")
        extracted = extract_domain_candidates(parsed)
        basic_semantic_summary = run_semantic_extraction(extracted, enabled=False, model=llm_model)[1]
        progress(68, "graph", "正在构建可独立使用的基础图谱")
        basic_graph, basic_evaluation = self.build_graph(
            parsed, ocr_tasks, ocr_results, extracted, [], basic_semantic_summary, parse_mode="basic", activate=parse_mode == "basic"
        )
        if parse_mode == "deep":
            if not read_deepseek_key():
                raise RuntimeError("深度解析已选择，但 DeepSeek API key 未配置；基础图谱已经生成")
            cached_semantic = read_jsonl(self.paths.extracted_dir / "llm_candidates.jsonl")
            progress(70, "semantic", "正在基础结果上执行大模型深度解析与证据校验")
            semantic_rows, semantic_summary = run_semantic_extraction(
                extracted,
                enabled=True,
                model=llm_model,
                cached_rows=cached_semantic,
                progress_callback=lambda current, total, station: progress(
                    70 + round(18 * current / max(total, 1)),
                    "semantic",
                    f"深度语义识别与校验 {station}（{current}/{total}）",
                ),
            )
            progress(90, "graph", "正在构建深度图谱、字段证据链和章节完整性报告")
            graph, evaluation = self.build_graph(
                parsed, ocr_tasks, ocr_results, extracted, semantic_rows, semantic_summary, parse_mode="deep", activate=True
            )
        else:
            semantic_rows, semantic_summary = [], basic_semantic_summary
            graph, evaluation = basic_graph, basic_evaluation
        progress(97, "writing", "正在写入图谱、评测和接口数据")
        state = self.publish_state(
            parsed,
            ocr_tasks,
            ocr_results,
            graph,
            extracted,
            evaluation,
            semantic_summary,
            parse_mode=parse_mode,
            publication_stage="completed",
        )
        progress(100, "completed", f"{('深度' if parse_mode == 'deep' else '基础')}解析和图谱生成已完成")
        return state

    def load_parsed_documents(self) -> dict[str, list[dict[str, Any]]]:
        names = ["documents", "pages", "text_blocks", "tables", "ocr_tasks"]
        parsed = {name: read_jsonl(self.paths.parsed_dir / f"{name}.jsonl") for name in names}
        if not parsed["documents"] or not parsed["pages"]:
            raise FileNotFoundError("Parsed sidecars are missing; run without --reuse-parsed first")
        parsed["changed_document_ids"] = []
        return parsed

    def parse_documents(
        self,
        progress_callback: Callable[[int, int, str, int], None] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        parser = PdfParser(self.paths.root)
        pdf_files = sorted(self.paths.raw_dir.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"No PDF files found in {self.paths.raw_dir}")
        sidecar_names = ["documents", "pages", "text_blocks", "tables", "ocr_tasks"]
        existing = {name: read_jsonl(self.paths.parsed_dir / f"{name}.jsonl") for name in sidecar_names}
        document_by_name = {row.get("file_name"): row for row in existing["documents"]}
        unchanged_names = {
            file.name
            for file in pdf_files
            if source_file_matches_record(file, document_by_name.get(file.name))
        }
        unchanged_ids = {
            row.get("id") for row in existing["documents"] if row.get("file_name") in unchanged_names
        }
        changed_files = [file for file in pdf_files if file.name not in unchanged_names]
        if changed_files:
            result = parser.parse_many(changed_files, text_sample_pages=None, progress_callback=progress_callback)
            fresh = {
                "documents": result.documents,
                "pages": result.pages,
                "text_blocks": result.text_blocks,
                "tables": result.tables,
                "ocr_tasks": result.ocr_tasks,
            }
        else:
            fresh = {name: [] for name in sidecar_names}
            if progress_callback:
                progress_callback(1, 1, "无新增或变更PDF", 0)
        combined = {
            "documents": [row for row in existing["documents"] if row.get("id") in unchanged_ids] + fresh["documents"],
            "pages": [row for row in existing["pages"] if row.get("document_id") in unchanged_ids] + fresh["pages"],
            "text_blocks": [row for row in existing["text_blocks"] if row.get("document_id") in unchanged_ids] + fresh["text_blocks"],
            "tables": [row for row in existing["tables"] if row.get("document_id") in unchanged_ids] + fresh["tables"],
            "ocr_tasks": [row for row in existing["ocr_tasks"] if row.get("document_id") in unchanged_ids] + fresh["ocr_tasks"],
        }
        combined["documents"].sort(key=lambda row: str(row.get("file_name", "")).lower())
        ensure_dir(self.paths.parsed_dir)
        for name in sidecar_names:
            write_jsonl(self.paths.parsed_dir / f"{name}.jsonl", combined[name])
        combined["changed_document_ids"] = [row.get("id") for row in fresh["documents"]]
        return combined

    def run_ocr(
        self,
        tasks: list[dict[str, Any]],
        max_pages: int,
        force: bool,
        all_tasks: list[dict[str, Any]] | None = None,
        device: str = "auto",
        workers: int = 4,
        batch_size: int = 0,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        all_tasks = all_tasks if all_tasks is not None else tasks
        existing_results = read_jsonl(self.paths.parsed_dir / "ocr_results.jsonl")
        current_tasks = {row.get("id"): row for row in all_tasks}
        existing_results = [row for row in existing_results if ocr_result_is_current(row, current_tasks.get(row.get("task_id")))]
        prepared_tasks = tasks if force else mark_cached_ocr_tasks(tasks, existing_results)
        runner = OcrRunner(
            self.paths.root,
            self.paths.assets_dir,
            device=device,
            render_workers=workers,
            batch_size=batch_size,
        )
        tasks_by_id = {row["id"]: row for row in mark_cached_ocr_tasks(all_tasks, existing_results)}
        results_by_id = {row["id"]: row for row in existing_results}

        def on_task(current: int, total: int, updated: dict[str, Any], page_results: list[dict[str, Any]]) -> None:
            tasks_by_id[updated["id"]] = updated
            for row in page_results:
                results_by_id[row["id"]] = row
            if current % 5 == 0 or current == total:
                write_jsonl(self.paths.parsed_dir / "ocr_tasks.jsonl", tasks_by_id.values())
                write_jsonl(self.paths.parsed_dir / "ocr_results.jsonl", results_by_id.values())
            if progress_callback:
                progress_callback(current, total)

        updated_selected_tasks, new_results = runner.run(
            prepared_tasks,
            max_pages=max_pages,
            force=force,
            task_callback=on_task,
        )
        if force:
            existing_results = [row for row in existing_results if row.get("task_id") not in runner.last_selected_ids]
        for row in updated_selected_tasks:
            tasks_by_id[row["id"]] = row
        updated_tasks = list(tasks_by_id.values())
        results_by_id = {row["id"]: row for row in [*existing_results, *new_results]}
        results = with_layout_groups(list(results_by_id.values()))
        write_jsonl(self.paths.parsed_dir / "ocr_tasks.jsonl", updated_tasks)
        write_jsonl(self.paths.parsed_dir / "ocr_results.jsonl", results)
        return updated_tasks, results

    def build_graph(
        self,
        parsed: dict[str, list[dict[str, Any]]],
        ocr_tasks: list[dict[str, Any]],
        ocr_results: list[dict[str, Any]],
        extracted: dict[str, Any],
        semantic_rows: list[dict[str, Any]],
        semantic_summary: dict[str, Any],
        parse_mode: str,
        activate: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        graph = build_automatic_graph(parsed, extracted, semantic_rows)
        multimodal = build_multimodal_outputs(
            [self.paths.assets_dir, self.paths.root / "data" / "multimodal"],
            self.paths.root, self.paths.output_dir / "multimodal",
            documents=parsed["documents"],
            slope_ids={n["id"] for n in graph["nodes"] if n["type"] == "Slope"},
            active_pdf_asset_hrefs={
                str(row.get("image_path"))
                for row in [*ocr_tasks, *ocr_results]
                if row.get("image_path")
            },
            slopes=[n for n in graph["nodes"] if n["type"] == "Slope"],
            text_blocks=parsed["text_blocks"],
            ocr_results=ocr_results,
        )
        attach_visual_assets(graph, multimodal["catalog"])
        evaluation = evaluate_automatic_pipeline(extracted, semantic_summary, graph)
        evaluation["multimodal"] = multimodal["quality"]["summary"]
        graph["meta"]["pipeline"] = {
            "mode": "basic" if parse_mode == "basic" else "deep",
            "layers": ["adaptive_pdf_parse", "deterministic_extraction"] + (["validated_llm_semantics"] if parse_mode == "deep" else []),
            "documents": len(parsed["documents"]),
            "pages": len(parsed["pages"]),
            "text_blocks": len(parsed["text_blocks"]),
            "tables": len(parsed["tables"]),
            "ocr_tasks": len(ocr_tasks),
            "ocr_done": sum(1 for item in ocr_tasks if item.get("status") == "done"),
            "ocr_engine_missing": sum(1 for item in ocr_tasks if item.get("status") == "engine_missing"),
            "ocr_results": len(ocr_results),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "manual_seed_used": False,
            "source_documents": [
                {
                    "file_name": row.get("file_name"),
                    "size_bytes": row.get("source_size_bytes"),
                    "mtime_ns": row.get("source_mtime_ns"),
                    "sha256": row.get("source_sha256"),
                }
                for row in parsed["documents"]
            ],
            "note": "图谱仅依据提交文档自动发现、抽取和校验生成；人工复核不是运行前置条件。",
        }
        graph["meta"]["dependencies"] = dependency_report()
        graph["meta"]["llm"] = {**llm_status(), "enabled": semantic_summary.get("enabled", False), "run": semantic_summary}
        effective_graph = apply_slope_overrides(graph, self.paths)
        completeness = build_completeness(effective_graph)
        rule_library = merged_rule_library(
            read_json(self.paths.rules_json, {"execution_enabled": False, "publication_status": "not_generated", "rules": []}),
            self.paths,
        )
        risk_screening = build_risk_screening(effective_graph, rule_library, completeness)
        risk_gold_path = self.paths.root / "data" / "validation" / "risk_gold.json"
        risk_evaluation = evaluate_risk_screening(
            risk_screening,
            read_json(risk_gold_path, {"labels": []}) if risk_gold_path.exists() else {"labels": []},
        )
        graph["meta"]["completeness"] = completeness["summary"]
        self.write_graph_outputs(graph, parse_mode=parse_mode, activate=activate)
        write_json(self.paths.schema_json, schema_payload())
        write_json(self.paths.output_dir / f"completeness.{parse_mode}.json", completeness)
        write_json(self.paths.interfaces_json, interface_payload())
        ensure_dir(self.paths.extracted_dir)
        write_json(self.paths.extracted_dir / "extraction_summary.json", extracted)
        write_json(self.paths.extracted_dir / "basic_extraction_summary.json", extracted)
        write_jsonl(self.paths.extracted_dir / "slopes.jsonl", extracted.get("slopes", []))
        write_jsonl(self.paths.extracted_dir / f"property_assertions.{parse_mode}.jsonl", graph.get("property_assertions", []))
        write_json(self.paths.extracted_dir / f"processing_audit.{parse_mode}.json", graph["processing_audit"])
        if activate:
            write_jsonl(self.paths.extracted_dir / "property_assertions.jsonl", graph.get("property_assertions", []))
        if parse_mode == "deep":
            write_jsonl(self.paths.extracted_dir / "llm_candidates.jsonl", semantic_rows)
            write_json(self.paths.extracted_dir / "llm_evaluation.json", semantic_summary)
        write_json(self.paths.output_dir / f"evaluation.{parse_mode}.json", evaluation)
        ensure_dir(self.paths.output_dir / "risk")
        write_json(self.paths.output_dir / "risk" / f"screening.{parse_mode}.json", risk_screening)
        write_json(self.paths.output_dir / "risk" / f"evaluation.{parse_mode}.json", risk_evaluation)
        if activate:
            write_json(self.paths.completeness_json, completeness)
            write_json(self.paths.evaluation_json, evaluation)
            write_json(self.paths.output_dir / "risk" / "screening.json", risk_screening)
            write_json(self.paths.output_dir / "risk" / "evaluation.json", risk_evaluation)
        return graph, evaluation

    def write_graph_outputs(self, graph: dict[str, Any], *, parse_mode: str, activate: bool) -> None:
        ensure_dir(self.paths.graph_dir)
        ensure_dir(self.paths.web_data_dir)
        mode_graph = self.paths.basic_graph_json if parse_mode == "basic" else self.paths.deep_graph_json
        write_json(mode_graph, graph)
        write_json(self.paths.graph_dir / f"{parse_mode}_graph.json", graph)
        write_graphml(self.paths.graph_dir / f"{parse_mode}.graphml", graph)
        if not activate:
            return
        write_json(self.paths.graph_json, graph)
        write_json(self.paths.graph_dir / "nodes.json", graph["nodes"])
        write_json(self.paths.graph_dir / "edges.json", graph["edges"])
        write_json(self.paths.graph_dir / "evidence.json", graph["evidence"])
        write_jsonl(self.paths.graph_dir / "nodes.jsonl", graph["nodes"])
        write_jsonl(self.paths.graph_dir / "edges.jsonl", graph["edges"])
        write_jsonl(self.paths.graph_dir / "evidence.jsonl", graph["evidence"])
        write_graphml(self.paths.graph_dir / "demo.graphml", graph)

    def make_state(
        self,
        parsed: dict[str, list[dict[str, Any]]],
        ocr_tasks: list[dict[str, Any]],
        ocr_results: list[dict[str, Any]],
        graph: dict[str, Any],
        extracted: dict[str, Any],
        evaluation: dict[str, Any],
        semantic_summary: dict[str, Any],
        parse_mode: str,
        publication_stage: str = "completed",
    ) -> dict[str, Any]:
        ocr_status_counts: dict[str, int] = {}
        ocr_device_counts: dict[str, int] = {}
        for task in ocr_tasks:
            status = task.get("status", "unknown")
            ocr_status_counts[status] = ocr_status_counts.get(status, 0) + 1
            device = task.get("device")
            if device:
                ocr_device_counts[str(device)] = ocr_device_counts.get(str(device), 0) + 1
        deep_graph = read_json(self.paths.deep_graph_json, {}) if self.paths.deep_graph_json.exists() else {}
        deep_exists = bool(deep_graph)
        deep_fresh = deep_exists and graph_matches_documents(deep_graph, parsed["documents"])
        return {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "parse_mode": parse_mode,
            "publication_stage": publication_stage,
            "base_layer_available": self.paths.basic_graph_json.exists(),
            "deep_layer_available": deep_fresh,
            "deep_layer_stale": deep_exists and not deep_fresh,
            "dependencies": dependency_report(),
            "llm": {**llm_status(), "enabled": semantic_summary.get("enabled", False), "run": semantic_summary},
            "parsed": {
                "documents": len(parsed["documents"]),
                "pages": len(parsed["pages"]),
                "text_blocks": len(parsed["text_blocks"]),
                "tables": len(parsed["tables"]),
                "ocr_tasks": len(ocr_tasks),
                "ocr_results": len(ocr_results),
                "ocr_status_counts": ocr_status_counts,
                "ocr_device_counts": ocr_device_counts,
            },
            "graph": graph["meta"]["stats"],
            "completeness": graph["meta"].get("completeness", {}),
            "extracted": extracted.get("stats", {}),
            "evaluation": evaluation,
            "outputs": {
                "graph_json": str((self.paths.web_data_dir / "demo_graph.json").relative_to(self.paths.root)),
                "basic_graph_json": str(self.paths.basic_graph_json.relative_to(self.paths.root)),
                "deep_graph_json": str(self.paths.deep_graph_json.relative_to(self.paths.root)) if self.paths.deep_graph_json.exists() else None,
                "graphml": str((self.paths.graph_dir / "demo.graphml").relative_to(self.paths.root)),
                "parsed_dir": str(self.paths.parsed_dir.relative_to(self.paths.root)),
                "assets_dir": str(self.paths.assets_dir.relative_to(self.paths.root)),
                "schema_json": str(self.paths.schema_json.relative_to(self.paths.root)),
                "completeness_json": str(self.paths.completeness_json.relative_to(self.paths.root)),
                "interfaces_json": str(self.paths.interfaces_json.relative_to(self.paths.root)),
                "extracted_dir": str(self.paths.extracted_dir.relative_to(self.paths.root)),
                "evaluation_json": str(self.paths.evaluation_json.relative_to(self.paths.root)),
            },
        }

    def publish_state(
        self,
        parsed: dict[str, list[dict[str, Any]]],
        ocr_tasks: list[dict[str, Any]],
        ocr_results: list[dict[str, Any]],
        graph: dict[str, Any],
        extracted: dict[str, Any],
        evaluation: dict[str, Any],
        semantic_summary: dict[str, Any],
        *,
        parse_mode: str,
        publication_stage: str,
    ) -> dict[str, Any]:
        state = self.make_state(
            parsed,
            ocr_tasks,
            ocr_results,
            graph,
            extracted,
            evaluation,
            semantic_summary,
            parse_mode,
            publication_stage,
        )
        write_json(self.paths.state_json, state)
        write_json(self.paths.deep_state_json if parse_mode == "deep" else self.paths.basic_state_json, state)
        standalone_path = self.paths.output_dir / "share" / "SlopeKG_成果展示_离线版.html"
        effective_graph = apply_slope_overrides(graph, self.paths)
        export_result = export_standalone_html(
            standalone_path,
            paths=self.paths,
            graph_mode="active",
            graph_override=effective_graph,
            completeness_override=build_completeness(effective_graph),
        )
        state["standalone_export"] = export_result
        state["outputs"]["standalone_html"] = str(standalone_path.relative_to(self.paths.root))
        risk_standalone_path = self.paths.output_dir / "share" / "SlopeKG_风险研判_离线版.html"
        risk_export_result = export_risk_standalone_html(risk_standalone_path, paths=self.paths)
        state["risk_standalone_export"] = risk_export_result
        state["outputs"]["risk_standalone_html"] = str(risk_standalone_path.relative_to(self.paths.root))
        write_json(self.paths.state_json, state)
        write_json(self.paths.deep_state_json if parse_mode == "deep" else self.paths.basic_state_json, state)
        return state

    def status(self) -> dict[str, Any]:
        state = read_json(self.paths.state_json, None)
        if state is None:
            state = {
                "updated_at": None,
                "dependencies": dependency_report(),
                "llm": llm_status(),
                "parsed": {
                    "documents": len(read_jsonl(self.paths.parsed_dir / "documents.jsonl")),
                    "pages": len(read_jsonl(self.paths.parsed_dir / "pages.jsonl")),
                    "text_blocks": len(read_jsonl(self.paths.parsed_dir / "text_blocks.jsonl")),
                    "tables": len(read_jsonl(self.paths.parsed_dir / "tables.jsonl")),
                    "ocr_tasks": len(read_jsonl(self.paths.parsed_dir / "ocr_tasks.jsonl")),
                    "ocr_results": len(read_jsonl(self.paths.parsed_dir / "ocr_results.jsonl")),
                    "ocr_status_counts": {},
                },
            }
        state["graph_available"] = self.paths.graph_json.exists()
        return state

    def parsed_table(self, name: str) -> list[dict[str, Any]]:
        allowed = {
            "documents": "documents.jsonl",
            "pages": "pages.jsonl",
            "text_blocks": "text_blocks.jsonl",
            "tables": "tables.jsonl",
            "ocr_tasks": "ocr_tasks.jsonl",
            "ocr_results": "ocr_results.jsonl",
        }
        if name not in allowed:
            raise KeyError(name)
        return read_jsonl(self.paths.parsed_dir / allowed[name])

    def parsed_extracted_table(self, name: str) -> list[dict[str, Any]]:
        allowed = {
            "slopes": "slopes.jsonl",
            "property_assertions": "property_assertions.jsonl",
            "llm_candidates": "llm_candidates.jsonl",
        }
        if name not in allowed:
            raise KeyError(name)
        return read_jsonl(self.paths.extracted_dir / allowed[name])


def graph_matches_documents(graph: dict[str, Any], documents: list[dict[str, Any]]) -> bool:
    expected = {
        (row.get("file_name"), row.get("source_size_bytes"), row.get("source_mtime_ns"))
        for row in documents
    }
    actual = {
        (row.get("file_name"), row.get("size_bytes"), row.get("mtime_ns"))
        for row in graph.get("meta", {}).get("pipeline", {}).get("source_documents", [])
    }
    return bool(expected) and expected == actual


def ocr_result_is_current(result: dict[str, Any], task: dict[str, Any] | None) -> bool:
    if task is None:
        return False
    task_hash = task.get("source_sha256")
    return not task_hash or result.get("source_sha256") == task_hash


def mark_cached_ocr_tasks(tasks: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    done_ids = {row.get("task_id") for row in results}
    return [{**task, "status": "done"} if task.get("id") in done_ids else task for task in tasks]


def source_file_matches_record(file: Path, record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    stat = file.stat()
    return record.get("source_size_bytes") == stat.st_size and record.get("source_mtime_ns") == stat.st_mtime_ns
