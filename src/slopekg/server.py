from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import PATHS
from .exporter import export_standalone_html
from .pipeline import DemoPipeline
from .schema import INTERFACES, attribute_dictionary_payload, interface_payload, schema_payload
from .storage import ensure_dir, read_json, read_jsonl


MAX_UPLOAD_REQUEST_BYTES = 512 * 1024 * 1024
MAX_UPLOAD_FILE_BYTES = 256 * 1024 * 1024


def safe_pdf_name(value: str) -> str:
    name = Path(value or "").name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or Path(name).suffix.lower() != ".pdf":
        raise ValueError("只支持扩展名为 .pdf 的文件")
    return name[:240]


def safe_export_name(value: str) -> str:
    """Normalize a user-facing HTML file name without allowing path traversal."""
    raw = str(value or "").strip()
    name = Path(raw).name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "SlopeKG_成果展示_离线版"
    if not name.lower().endswith(".html"):
        name += ".html"
    stem = Path(name).stem[:120].strip(" .") or "SlopeKG_成果展示_离线版"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}:
        stem = f"_{stem}"
    return f"{stem}.html"


def unique_destination(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    index = 1
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def parse_pdf_uploads(content_type: str, raw: bytes) -> list[tuple[str, bytes]]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
    )
    pending: list[tuple[str, bytes]] = []
    for part in message.iter_parts():
        file_name = part.get_filename()
        if not file_name:
            continue
        safe_name = safe_pdf_name(file_name)
        data = part.get_payload(decode=True) or b""
        if not data or len(data) > MAX_UPLOAD_FILE_BYTES:
            raise ValueError(f"{safe_name} 为空或超过 256 MB")
        if not data[:1024].lstrip().startswith(b"%PDF-"):
            raise ValueError(f"{safe_name} 不是有效的 PDF 文件")
        pending.append((safe_name, data))
    if not pending:
        raise ValueError("没有找到可上传的 PDF 文件")
    return pending


def document_inventory() -> dict[str, Any]:
    documents = read_jsonl(PATHS.parsed_dir / "documents.jsonl")
    pages = read_jsonl(PATHS.parsed_dir / "pages.jsonl")
    text_blocks = read_jsonl(PATHS.parsed_dir / "text_blocks.jsonl")
    tables = read_jsonl(PATHS.parsed_dir / "tables.jsonl")
    ocr_tasks = read_jsonl(PATHS.parsed_dir / "ocr_tasks.jsonl")
    by_name = {row.get("file_name"): row for row in documents}
    page_by_doc: dict[str, dict[str, int]] = {}
    for row in pages:
        stats = page_by_doc.setdefault(row.get("document_id", ""), {})
        page_type = row.get("page_type", "unknown")
        stats[page_type] = stats.get(page_type, 0) + 1
    text_by_doc: dict[str, int] = {}
    for row in text_blocks:
        doc_id = row.get("document_id", "")
        text_by_doc[doc_id] = text_by_doc.get(doc_id, 0) + 1
    table_by_doc: dict[str, int] = {}
    for row in tables:
        doc_id = row.get("document_id", "")
        table_by_doc[doc_id] = table_by_doc.get(doc_id, 0) + 1
    ocr_by_doc: dict[str, dict[str, int]] = {}
    for row in ocr_tasks:
        stats = ocr_by_doc.setdefault(row.get("document_id", ""), {})
        status = row.get("status", "unknown")
        stats[status] = stats.get(status, 0) + 1

    rows = []
    ensure_dir(PATHS.raw_dir)
    for file in sorted(PATHS.raw_dir.glob("*.pdf"), key=lambda item: item.name.lower()):
        parsed_record = by_name.get(file.name)
        doc_id = parsed_record.get("id") if parsed_record else None
        stat = file.stat()
        current = bool(
            parsed_record
            and parsed_record.get("source_size_bytes") == stat.st_size
            and parsed_record.get("source_mtime_ns") == stat.st_mtime_ns
        )
        stale = bool(parsed_record and not current)
        rows.append(
            {
                "file_name": file.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "parsed": current,
                "stale": stale,
                "change_status": "current" if current else ("changed" if stale else "new"),
                "document_id": doc_id,
                "kind": parsed_record.get("kind") if parsed_record else None,
                "pages": parsed_record.get("pages") if parsed_record else None,
                "parse_scope": parsed_record.get("parse_scope") if parsed_record else None,
                "page_types": page_by_doc.get(doc_id, {}) if doc_id else {},
                "text_blocks": text_by_doc.get(doc_id, 0) if doc_id else 0,
                "tables": table_by_doc.get(doc_id, 0) if doc_id else 0,
                "ocr_status_counts": ocr_by_doc.get(doc_id, {}) if doc_id else {},
            }
        )
    return {
        "count": len(rows),
        "parsed_count": sum(1 for row in rows if row["parsed"]),
        "unparsed_count": sum(1 for row in rows if not row["parsed"]),
        "stale_count": sum(1 for row in rows if row["stale"]),
        "rows": rows,
    }


class PipelineJobManager:
    def __init__(self, pipeline: DemoPipeline) -> None:
        self.pipeline = pipeline
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.latest_id: str | None = None

    def start(self, options: dict[str, Any], *, trigger: str = "manual") -> tuple[dict[str, Any], bool]:
        with self.lock:
            active = next((job for job in self.jobs.values() if job["status"] in {"queued", "running"}), None)
            if active:
                return self._copy(active), False
            job_id = uuid.uuid4().hex[:12]
            now = datetime.now().isoformat(timespec="seconds")
            job = {
                "id": job_id,
                "status": "queued",
                "progress": 0,
                "stage": "queued",
                "message": "任务已进入队列",
                "created_at": now,
                "started_at": None,
                "updated_at": now,
                "finished_at": None,
                "options": options,
                "trigger": trigger,
                "result": None,
                "error": None,
            }
            self.jobs[job_id] = job
            self.latest_id = job_id
            self._trim()
        threading.Thread(target=self._run, args=(job_id,), daemon=True, name=f"pipeline-{job_id}").start()
        return self.get(job_id) or {}, True

    def _run(self, job_id: str) -> None:
        self._update(job_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))

        def on_progress(percent: int, stage: str, message: str) -> None:
            self._update(job_id, progress=max(0, min(100, int(percent))), stage=stage, message=message)

        try:
            options = self.get(job_id)["options"]  # type: ignore[index]
            state = self.pipeline.run(progress_callback=on_progress, **options)
            now = datetime.now().isoformat(timespec="seconds")
            self._update(
                job_id,
                status="completed",
                progress=100,
                stage="completed",
                message="PDF解析、信息抽取和图谱生成已完成",
                result=state,
                finished_at=now,
            )
        except Exception as exc:  # pragma: no cover - surfaced through the job API
            now = datetime.now().isoformat(timespec="seconds")
            self._update(
                job_id,
                status="failed",
                stage="failed",
                message="处理失败，请查看错误信息",
                error=str(exc),
                finished_at=now,
            )

    def _update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.update(values)
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return self._copy(job) if job else None

    def latest(self) -> dict[str, Any] | None:
        return self.get(self.latest_id) if self.latest_id else None

    def _trim(self) -> None:
        completed = [key for key, job in self.jobs.items() if job["status"] not in {"queued", "running"}]
        for key in completed[:-19]:
            self.jobs.pop(key, None)

    @staticmethod
    def _copy(job: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(job, ensure_ascii=False))


PIPELINE = DemoPipeline()
JOB_MANAGER = PipelineJobManager(PIPELINE)


def automatic_basic_options() -> dict[str, Any]:
    """Fast, deterministic publication used whenever the source directory changes."""
    return {
        "ocr_pages": 1000,
        "force_ocr": False,
        "parse_mode": "basic",
        "llm_model": "deepseek-v4-flash",
        "reuse_parsed": False,
        "ocr_scope": "changed",
    }


class PdfDirectoryWatcher:
    """Poll rawPDF and schedule a basic rebuild after files have stopped changing."""

    def __init__(self, manager: PipelineJobManager, interval_seconds: float = 2.0) -> None:
        self.manager = manager
        self.interval_seconds = interval_seconds
        self._candidate: tuple[tuple[str, int, int], ...] | None = None
        self._stable_polls = 0
        self._scheduled_snapshot: tuple[tuple[str, int, int], ...] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="rawpdf-auto-update")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> tuple[tuple[str, int, int], ...]:
        ensure_dir(PATHS.raw_dir)
        rows = []
        for file in sorted(PATHS.raw_dir.glob("*.pdf"), key=lambda item: item.name.lower()):
            stat = file.stat()
            rows.append((file.name, stat.st_size, stat.st_mtime_ns))
        return tuple(rows)

    def check_once(self) -> bool:
        snapshot = self.snapshot()
        if snapshot != self._candidate:
            self._candidate = snapshot
            self._stable_polls = 1
            return False
        self._stable_polls += 1
        inventory = document_inventory()
        if self._stable_polls < 2 or not inventory["unparsed_count"]:
            return False
        if snapshot == self._scheduled_snapshot:
            return False
        _, created = self.manager.start(automatic_basic_options(), trigger="directory_watch")
        if created:
            self._scheduled_snapshot = snapshot
        return created

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.check_once()
            except Exception as exc:  # pragma: no cover - keep server alive and retry on the next poll
                print(f"rawPDF auto-update check failed: {exc}")


PDF_WATCHER = PdfDirectoryWatcher(JOB_MANAGER)


class SlopeKGHandler(SimpleHTTPRequestHandler):
    pipeline = PIPELINE

    def __init__(self, *args: Any, directory: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PATHS.root), **kwargs)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/web/index.html")
            self.end_headers()
            return
        if parsed.path == "/api/status":
            self.write_json(self.pipeline.status())
            return
        if parsed.path == "/api/documents":
            self.write_json(document_inventory())
            return
        if parsed.path == "/api/pipeline/jobs/latest":
            self.write_json(JOB_MANAGER.latest() or {"status": "none"})
            return
        if parsed.path.startswith("/api/pipeline/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = JOB_MANAGER.get(job_id)
            if job is None:
                self.write_json({"error": f"unknown job: {job_id}"}, status=HTTPStatus.NOT_FOUND)
            else:
                self.write_json(job)
            return
        if parsed.path == "/api/graph":
            self.write_json(read_json(PATHS.graph_json, {"nodes": [], "edges": [], "evidence": []}))
            return
        if parsed.path == "/api/schema":
            self.write_json(read_json(PATHS.schema_json, schema_payload()))
            return
        if parsed.path == "/api/attribute-dictionary":
            self.write_json(attribute_dictionary_payload())
            return
        if parsed.path == "/api/interfaces":
            self.write_json(read_json(PATHS.interfaces_json, interface_payload()))
            return
        if parsed.path == "/api/evaluation":
            self.write_json(read_json(PATHS.evaluation_json, {"status": "not_generated"}))
            return
        if parsed.path == "/api/extracted/slopes":
            rows = self.pipeline.parsed_extracted_table("slopes")
            self.write_json({"name": "extracted_slopes", "rows": rows, "count": len(rows)})
            return
        if parsed.path == "/api/assertions":
            rows = self.pipeline.parsed_extracted_table("property_assertions")
            self.write_json({"name": "property_assertions", "rows": rows, "count": len(rows)})
            return
        if parsed.path == "/api/extracted/llm-candidates":
            rows = self.pipeline.parsed_extracted_table("llm_candidates")
            quality = read_json(PATHS.extracted_dir / "llm_evaluation.json", {"status": "not_generated"})
            self.write_json({"name": "llm_candidates", "rows": rows, "count": len(rows), "quality": quality})
            return
        if parsed.path == "/api/slopes":
            graph = read_json(PATHS.graph_json, {"nodes": []})
            slopes = [node for node in graph.get("nodes", []) if node.get("type") == "Slope"]
            self.write_json({"count": len(slopes), "rows": slopes})
            return
        if parsed.path.startswith("/api/slopes/"):
            slope_id = parsed.path.rsplit("/", 1)[-1]
            self.write_slope_detail(slope_id)
            return
        if parsed.path == "/api/completeness":
            self.write_json(read_json(PATHS.completeness_json, {"summary": {}, "reports": []}))
            return
        if parsed.path.startswith("/api/completeness/"):
            slope_id = parsed.path.rsplit("/", 1)[-1]
            payload = read_json(PATHS.completeness_json, {"reports": []})
            report = next((row for row in payload.get("reports", []) if row.get("slope_id") == slope_id), None)
            if report is None:
                self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
            else:
                self.write_json(report)
            return
        if parsed.path == "/api/risk/readiness":
            slope_id = (parse_qs(parsed.query).get("slope_id") or [None])[0]
            self.write_risk_readiness(slope_id)
            return
        reserved_paths = {
            "/api/monitoring/observations",
            "/api/environment/latest",
            "/api/maintenance/events",
            "/api/inspections",
            "/api/exposure",
            "/api/risk/rules",
        }
        if parsed.path in reserved_paths:
            self.write_reserved_interface(parsed.path, parse_qs(parsed.query))
            return
        if parsed.path.startswith("/api/parsed/"):
            name = parsed.path.rsplit("/", 1)[-1]
            try:
                rows = self.pipeline.parsed_table(name)
            except KeyError:
                self.write_json({"error": f"unknown parsed table: {name}"}, status=HTTPStatus.NOT_FOUND)
                return
            self.write_json({"name": name, "rows": rows, "count": len(rows)})
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/documents/upload":
            self.handle_pdf_upload()
            return
        if parsed.path == "/api/pipeline/jobs":
            try:
                options = self.pipeline_options(self.read_json_body())
                job, created = JOB_MANAGER.start(options)
            except (TypeError, ValueError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self.write_json(job, status=HTTPStatus.ACCEPTED if created else HTTPStatus.CONFLICT)
            return
        if parsed.path == "/api/export/standalone":
            try:
                body = self.read_json_body()
                file_name = safe_export_name(str(body.get("file_name", "")))
                graph_mode = str(body.get("graph_mode", "active")).strip().lower()
                destination = PATHS.output_dir / "share" / file_name
                result = export_standalone_html(destination, graph_mode=graph_mode)
            except (TypeError, ValueError, FileNotFoundError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self.write_json(
                {
                    **result,
                    "file_name": file_name,
                    "download_url": f"/output/demo/share/{quote(file_name)}",
                },
                status=HTTPStatus.CREATED,
            )
            return
        if parsed.path == "/api/risk/assess":
            self.write_json(
                {
                    "status": "blocked",
                    "error": "risk_assessment_not_enabled",
                    "message": "正式风险规则、动态数据和审核流程尚未接入，当前只提供风险就绪度检查。",
                    "readiness_endpoint": "/api/risk/readiness?slope_id={slope_id}",
                },
                status=HTTPStatus.NOT_IMPLEMENTED,
            )
            return
        if parsed.path != "/api/pipeline/run":
            self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            state = self.pipeline.run(**self.pipeline_options(self.read_json_body()))
        except Exception as exc:  # pragma: no cover - surfaced to UI
            self.write_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.write_json(state)

    def pipeline_options(self, body: dict[str, Any]) -> dict[str, Any]:
        ocr_pages = max(0, min(1000, int(body.get("ocr_pages", 1000))))
        llm_model = str(body.get("llm_model", "deepseek-v4-flash")).strip()
        if not llm_model or len(llm_model) > 100:
            raise ValueError("大模型名称不合法")
        parse_mode = str(body.get("parse_mode") or ("deep" if body.get("use_llm") is True else "basic")).strip().lower()
        if parse_mode not in {"basic", "deep"}:
            raise ValueError("parse_mode 只能为 basic 或 deep")
        ocr_scope = str(body.get("ocr_scope", "all")).strip().lower()
        if ocr_scope not in {"all", "changed"}:
            raise ValueError("ocr_scope 只能为 all 或 changed")
        return {
            "ocr_pages": ocr_pages,
            "force_ocr": bool(body.get("force_ocr", False)),
            "parse_mode": parse_mode,
            "llm_model": llm_model,
            "reuse_parsed": bool(body.get("reuse_parsed", False)),
            "ocr_scope": ocr_scope,
        }

    def handle_pdf_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data") or "boundary=" not in content_type:
            self.write_json({"error": "请求必须使用 multipart/form-data"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_REQUEST_BYTES:
            self.write_json({"error": "上传请求为空或超过 512 MB"}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        raw = self.rfile.read(length)
        try:
            pending = parse_pdf_uploads(content_type, raw)
        except ValueError as exc:
            self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        ensure_dir(PATHS.raw_dir)
        saved = []
        for file_name, data in pending:
            destination = unique_destination(PATHS.raw_dir, file_name)
            destination.write_bytes(data)
            saved.append(
                {
                    "original_name": file_name,
                    "file_name": destination.name,
                    "size_bytes": len(data),
                    "renamed": destination.name != file_name,
                }
            )
        job, created = JOB_MANAGER.start(automatic_basic_options(), trigger="upload")
        self.write_json(
            {
                "count": len(saved),
                "files": saved,
                "message": "PDF已安全保存，已自动触发基础解析" if created else "PDF已安全保存；当前任务结束后将自动检测并更新",
                "job": job,
                "job_created": created,
            },
            status=HTTPStatus.CREATED,
        )

    def write_slope_detail(self, slope_id: str) -> None:
        graph = read_json(PATHS.graph_json, {"nodes": [], "edges": [], "evidence": []})
        node = next((item for item in graph.get("nodes", []) if item.get("id") == slope_id and item.get("type") == "Slope"), None)
        if node is None:
            self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
            return
        edges = [edge for edge in graph.get("edges", []) if slope_id in {edge.get("source"), edge.get("target")}]
        related_ids = {edge["target"] if edge["source"] == slope_id else edge["source"] for edge in edges}
        related_nodes = [item for item in graph.get("nodes", []) if item.get("id") in related_ids]
        evidence_ids = {edge.get("props", {}).get("evidence") for edge in edges if edge.get("props", {}).get("evidence")}
        evidence = [item for item in graph.get("evidence", []) if item.get("id") in evidence_ids]
        completeness = read_json(PATHS.completeness_json, {"reports": []})
        report = next((row for row in completeness.get("reports", []) if row.get("slope_id") == slope_id), None)
        self.write_json({"slope": node, "edges": edges, "related_nodes": related_nodes, "evidence": evidence, "completeness": report})

    def write_risk_readiness(self, slope_id: str | None) -> None:
        payload = read_json(PATHS.completeness_json, {"summary": {}, "reports": []})
        reports = payload.get("reports", [])
        if slope_id:
            reports = [row for row in reports if row.get("slope_id") == slope_id]
            if not reports:
                self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
                return
        self.write_json(
            {
                "status": "readiness_only",
                "assessment_enabled": False,
                "blocking_global_items": ["approved_risk_rule_set", "dynamic_data_connectors", "human_review_workflow"],
                "summary": payload.get("summary", {}),
                "reports": reports,
                "message": "当前仅检查数据是否具备研判条件，不输出正式风险等级。",
            }
        )

    def write_reserved_interface(self, path: str, query: dict[str, list[str]]) -> None:
        descriptor = next((item for item in INTERFACES if item["path"] == path and item["method"] == "GET"), None)
        self.write_json(
            {
                "status": descriptor.get("status") if descriptor else "reserved_not_connected",
                "path": path,
                "query": query,
                "rows": [],
                "count": 0,
                "description": descriptor.get("description") if descriptor else "预留接口",
                "message": "接口结构已预留，当前没有已接入的数据源；空数组不代表现场不存在该类数据。",
            }
        )

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1024 * 1024:
            raise ValueError("JSON请求体过大")
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def write_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def translate_path(self, path: str) -> str:
        path = unquote(urlparse(path).path)
        if path.startswith("/output/") or path.startswith("/web/") or path.startswith("/data/"):
            return str((PATHS.root / path.lstrip("/")).resolve())
        return str((PATHS.root / path.lstrip("/")).resolve())

    def guess_type(self, path: str) -> str:
        if path.endswith(".js"):
            return "text/javascript"
        if path.endswith(".json"):
            return "application/json"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    def end_headers(self) -> None:
        if self.path.startswith(("/web/", "/api/", "/output/demo/web/data/")):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    PDF_WATCHER.start()
    httpd = ThreadingHTTPServer((host, port), SlopeKGHandler)
    print(f"SlopeKG demo server: http://{host}:{port}/web/index.html")
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SlopeKG demo backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
