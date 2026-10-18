from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import math
import re
import tempfile
import threading
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import queries
from .jobs import PipelineJobManager, PdfDirectoryWatcher, automatic_basic_options
from .config import PATHS
from .llm import DEFAULT_DEEPSEEK_MODEL
from .exporter import export_risk_standalone_html, export_standalone_html
from .manual import (
    delete_manual_rule,
    delete_slope_override,
    manual_schema,
    save_manual_rule,
    save_slope_override,
    slope_override_record,
)
from .multimodal import multimodal_contract
from .monitoring import (
    export_monitoring_report, monitoring_graph, monitoring_interfaces,
    observation_payload, public_catalog, replay_payload, source_file,
)
from .inspection_history import HISTORY_INTERFACES, public_history_catalog, history_payload, history_source, history_interfaces
from .monitoring_baseline import baseline_payload, baseline_interfaces
from .pipeline import DemoPipeline
from .recognition import visual_job_options, profile_catalog, recognition_documents, recognition_payload
from .rule_extraction import RuleExtractionPipeline
from .schema import INTERFACES, attribute_dictionary_payload, interface_payload, schema_payload
from .storage import ensure_dir, read_json


MAX_UPLOAD_REQUEST_BYTES = 512 * 1024 * 1024
MAX_UPLOAD_FILE_BYTES = 256 * 1024 * 1024
PUBLIC_STATIC_PREFIXES = (
    "/web/",
    "/data/rawPDF/",
    "/output/demo/assets/ocr_pages/",
    "/output/demo/graph/",
    "/output/demo/rules/pages/",
    "/output/demo/share/",
    "/output/demo/web/data/",
)
DENIED_STATIC_PATH = "__slopekg_access_denied__"
_UPLOAD_LOCK = threading.Lock()


def origin_matches_host(origin: str | None, host: str | None) -> bool:
    """Allow CLI clients and same-origin browser requests, but not foreign pages."""
    if not origin:
        return True
    parsed = urlparse(origin)
    host_name = urlparse(f"//{host or ''}").hostname
    if parsed.scheme not in {"http", "https"} or not host_name or parsed.netloc.casefold() != str(host).casefold():
        return False
    if host_name.casefold() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host_name)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def safe_static_path(request_path: str, root: Path = PATHS.root) -> Path:
    """Map only explicitly public URL trees and keep resolved paths below root."""
    decoded = unquote(urlparse(request_path).path).replace("\\", "/")
    public_prefix = next((prefix for prefix in PUBLIC_STATIC_PREFIXES if decoded.startswith(prefix)), None)
    if public_prefix is None:
        return root / DENIED_STATIC_PATH
    candidate = (root / decoded.lstrip("/")).resolve()
    public_root = (root / public_prefix.lstrip("/")).resolve()
    try:
        candidate.relative_to(public_root)
    except ValueError:
        return root / DENIED_STATIC_PATH
    return candidate


def safe_pdf_name(value: str) -> str:
    name = Path(value or "").name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name or Path(name).suffix.lower() != ".pdf":
        raise ValueError("只支持扩展名为 .pdf 的文件")
    # Keep the validated suffix even when a long name must be shortened.
    suffix = Path(name).suffix
    return name[:240 - len(suffix)] + suffix if len(name) > 240 else name


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


def save_pdf_upload(directory: Path, file_name: str, data: bytes) -> Path:
    """Publish a complete upload and serialize name allocation in this server."""
    ensure_dir(directory)
    handle = tempfile.NamedTemporaryFile(dir=directory, prefix=".upload-", suffix=".tmp", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
        with _UPLOAD_LOCK:
            destination = unique_destination(directory, file_name)
            temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def reserve_export_destination(directory: Path, file_name: str):
    """Reserve a report name atomically even when requests arrive together."""
    ensure_dir(directory)
    while True:
        destination = unique_destination(directory, file_name)
        try:
            with destination.open("xb"):
                pass
            break
        except FileExistsError:
            continue
    try:
        yield destination
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON不能包含非有限数值：{value}")


def finite_json_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        reject_json_constant(value)
    return number


def boolean_option(body: dict[str, Any], name: str, default: bool = False) -> bool:
    value = body.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是布尔值 true 或 false")
    return value


def parse_upload_options(content_type: str, raw: bytes) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
    )
    fields = [part for part in message.iter_parts()
              if part.get_param("name", header="content-disposition") == "options" and not part.get_filename()]
    if not fields:
        return {}
    if len(fields) != 1:
        raise ValueError("上传参数只能提供一次")
    try:
        options = json.loads((fields[0].get_payload(decode=True) or b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("上传参数必须是有效JSON对象") from exc
    if not isinstance(options, dict):
        raise ValueError("上传参数必须是对象")
    return options


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


PIPELINE = DemoPipeline()
JOB_MANAGER = PipelineJobManager(PIPELINE)
RULE_PIPELINE = RuleExtractionPipeline()
RULE_JOB_MANAGER = PipelineJobManager(RULE_PIPELINE, completion_message="候选规则库、公式库和证据链已生成")


PDF_WATCHER = PdfDirectoryWatcher(JOB_MANAGER)


class SlopeKGHandler(SimpleHTTPRequestHandler):
    pipeline = PIPELINE

    def __init__(self, *args: Any, directory: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PATHS.root), **kwargs)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self.request_origin_allowed():
            self.write_json({"error": "cross-origin request denied"}, status=HTTPStatus.FORBIDDEN)
            return
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
        for handler in (
            self._get_pipeline,
            self._get_rules,
            self._get_risk,
            self._get_graph,
            self._get_manual,
            self._get_schema,
            self._get_assets,
            self._get_monitoring,
            self._get_monitoring_baseline,
            self._get_inspection_history,
            self._get_reserved,
        ):
            if handler(parsed):
                return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self.request_origin_allowed():
            self.write_json({"error": "cross-origin request denied"}, status=HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        for handler in (
            self._post_manual,
            self._post_pipeline,
            self._post_rules,
            self._post_exports,
            self._post_risk,
        ):
            if handler(parsed):
                return
        if parsed.path != "/api/pipeline/run":
            self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            options = self.pipeline_options(self.read_json_body())
        except (TypeError, ValueError) as exc:
            self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            state = self.pipeline.run(**options)
        except Exception as exc:  # pragma: no cover - surfaced to UI
            self.write_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.write_json(state)

    def _get_pipeline(self, parsed: Any) -> bool:
        if parsed.path in {'/api/recognition','/api/recognition/profiles','/api/recognition/documents'}:
            try:
                query=parse_qs(parsed.query)
                payload=(profile_catalog(PATHS) if parsed.path.endswith('/profiles') else
                         recognition_documents(PATHS) if parsed.path.endswith('/documents') else
                         recognition_payload(PATHS,status=query.get('status',[''])[0],
                             offset=int(query.get('offset',['0'])[0]),limit=int(query.get('limit',['50'])[0])))
                self.write_json(payload)
            except (ValueError,TypeError) as exc:
                self.write_json({'error':str(exc)},status=HTTPStatus.BAD_REQUEST)
            return True
        if parsed.path == "/api/status":
            self.write_json(self.pipeline.status())
            return True
        if parsed.path == "/api/documents":
            self.write_json(document_inventory())
            return True
        if parsed.path == "/api/pipeline/jobs/latest":
            self.write_json(JOB_MANAGER.latest() or {"status": "none"})
            return True
        if parsed.path.startswith("/api/pipeline/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = JOB_MANAGER.get(job_id)
            if job is None:
                self.write_json({"error": f"unknown job: {job_id}"}, status=HTTPStatus.NOT_FOUND)
            else:
                self.write_json(job)
            return True
        if parsed.path == "/api/evaluation":
            self.write_json(read_json(PATHS.evaluation_json, {"status": "not_generated"}))
            return True
        if parsed.path == "/api/extracted/slopes":
            rows = self.pipeline.parsed_extracted_table("slopes")
            self.write_json({"name": "extracted_slopes", "rows": rows, "count": len(rows)})
            return True
        if parsed.path == "/api/assertions":
            rows = self.pipeline.parsed_extracted_table("property_assertions")
            self.write_json({"name": "property_assertions", "rows": rows, "count": len(rows)})
            return True
        if parsed.path == "/api/extracted/llm-candidates":
            rows = self.pipeline.parsed_extracted_table("llm_candidates")
            quality = read_json(PATHS.extracted_dir / "llm_evaluation.json", {"status": "not_generated"})
            self.write_json({"name": "llm_candidates", "rows": rows, "count": len(rows), "quality": quality})
            return True
        if parsed.path.startswith("/api/parsed/"):
            name = parsed.path.rsplit("/", 1)[-1]
            try:
                rows = self.pipeline.parsed_table(name)
            except KeyError:
                self.write_json({"error": f"unknown parsed table: {name}"}, status=HTTPStatus.NOT_FOUND)
                return True
            self.write_json({"name": name, "rows": rows, "count": len(rows)})
            return True
        return False


    def _get_rules(self, parsed: Any) -> bool:
        if parsed.path == "/api/rules/jobs/latest":
            self.write_json(RULE_JOB_MANAGER.latest() or {"status": "none"})
            return True
        if parsed.path.startswith("/api/rules/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = RULE_JOB_MANAGER.get(job_id)
            if job is None:
                self.write_json({"error": f"unknown rule job: {job_id}"}, status=HTTPStatus.NOT_FOUND)
            else:
                self.write_json(job)
            return True
        if parsed.path == "/api/rules/library":
            self.write_json(current_rule_library())
            return True
        if parsed.path == "/api/rules/documents":
            library = current_rule_library()
            self.write_json({"rows": library.get("documents", []), "count": len(library.get("documents", [])), "stats": library.get("stats", {})})
            return True
        if parsed.path == "/api/rules/formulas":
            library = current_rule_library()
            self.write_json({"rows": library.get("formulas", []), "count": len(library.get("formulas", [])), "stats": library.get("stats", {})})
            return True
        return False


    def _get_risk(self, parsed: Any) -> bool:
        if parsed.path == "/api/risk/rules":
            library = current_rule_library()
            rules = [row for row in library.get("rules", []) if row.get("risk_engine_candidate")]
            self.write_json(
                {
                    "status": library.get("publication_status", "not_generated"),
                    "execution_enabled": library.get("execution_enabled", False),
                    "rows": rules,
                    "count": len(rules),
                    "stats": library.get("stats", {}),
                    "message": "候选规则可查询和复核；只有经审核批准的规则才允许进入正式风险计算。",
                }
            )
            return True
        if parsed.path == "/api/risk/screening":
            query = parse_qs(parsed.query)
            slope_id = query.get("slope_id", [None])[0]
            payload = current_risk_screening()
            if slope_id:
                rows = [row for row in payload.get("assessments", []) if row.get("slope_id") == slope_id]
                if not rows:
                    self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
                    return True
                payload = {**payload, "assessments": rows}
            self.write_json(payload)
            return True
        if parsed.path == "/api/risk/evaluation":
            self.write_json(current_risk_evaluation())
            return True
        if parsed.path == "/api/completeness":
            self.write_json(current_completeness())
            return True
        if parsed.path.startswith("/api/completeness/"):
            slope_id = parsed.path.rsplit("/", 1)[-1]
            payload = current_completeness()
            report = next((row for row in payload.get("reports", []) if row.get("slope_id") == slope_id), None)
            if report is None:
                self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
            else:
                self.write_json(report)
            return True
        if parsed.path == "/api/risk/readiness":
            slope_id = (parse_qs(parsed.query).get("slope_id") or [None])[0]
            self.write_risk_readiness(slope_id)
            return True
        return False


    def _get_graph(self, parsed: Any) -> bool:
        if parsed.path == "/api/graph":
            self.write_json(current_graph())
            return True
        if parsed.path == "/api/slopes":
            graph = current_graph()
            slopes = [node for node in graph.get("nodes", []) if node.get("type") == "Slope"]
            self.write_json({"count": len(slopes), "rows": slopes})
            return True
        if parsed.path.startswith("/api/slopes/"):
            slope_id = parsed.path.rsplit("/", 1)[-1]
            self.write_slope_detail(slope_id)
            return True
        return False


    def _get_manual(self, parsed: Any) -> bool:
        if parsed.path == "/api/manual/schema":
            self.write_json(manual_schema())
            return True
        if parsed.path.startswith("/api/manual/slopes/"):
            slope_id = parsed.path.rsplit("/", 1)[-1]
            if not find_slope(slope_id):
                self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
            else:
                self.write_json(slope_override_record(slope_id))
            return True
        return False


    def _get_schema(self, parsed: Any) -> bool:
        if parsed.path == "/api/schema":
            self.write_json(read_json(PATHS.schema_json, schema_payload()))
            return True
        if parsed.path == "/api/attribute-dictionary":
            self.write_json(attribute_dictionary_payload())
            return True
        if parsed.path == "/api/interfaces":
            self.write_json(baseline_interfaces(history_interfaces(monitoring_interfaces(read_json(PATHS.interfaces_json, interface_payload()), PATHS), PATHS), PATHS))
            return True
        return False


    def _get_assets(self, parsed: Any) -> bool:
        if parsed.path == "/api/multimodal/schema":
            self.write_json(read_json(PATHS.multimodal_contract_json, multimodal_contract()))
            return True
        if parsed.path == "/api/multimodal/assets":
            self.write_json(read_json(PATHS.multimodal_catalog_json, {"status": "not_generated", "count": 0, "assets": []}))
            return True
        if parsed.path == "/api/multimodal/quality":
            self.write_json(read_json(PATHS.multimodal_quality_json, {"status": "not_generated", "summary": {"assets": 0}, "issues": []}))
            return True
        return False


    def _get_reserved(self, parsed: Any) -> bool:
        reserved_paths = {
            "/api/monitoring/observations",
            "/api/environment/latest",
            "/api/maintenance/events",
            "/api/inspections",
            "/api/exposure",
        }
        if parsed.path in reserved_paths:
            self.write_reserved_interface(parsed.path, parse_qs(parsed.query))
            return True
        return False

    def _get_monitoring(self, parsed: Any) -> bool:
        routes = {
            "/api/monitoring/catalog", "/api/monitoring/observations", "/api/monitoring/replay",
            "/api/monitoring/graph", "/api/monitoring/source",
        }
        if parsed.path not in routes:
            return False
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if parsed.path == "/api/monitoring/catalog":
                self.write_json(public_catalog(PATHS))
            elif parsed.path == "/api/monitoring/observations":
                self.write_json(observation_payload(query, PATHS))
            elif parsed.path == "/api/monitoring/replay":
                self.write_json(replay_payload(query, PATHS))
            elif parsed.path == "/api/monitoring/graph":
                self.write_json(monitoring_graph(PATHS))
            else:
                source = source_file(query.get("series_id", [""])[0], PATHS)
                content = source.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_cors_headers()
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(source.name)}")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
        except KeyError as exc:
            self.write_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except (TypeError, ValueError) as exc:
            self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except (FileNotFoundError, OSError) as exc:
            self.write_json({"error": str(exc)}, status=HTTPStatus.CONFLICT)
        return True

    def _get_monitoring_baseline(self, parsed: Any) -> bool:
        if parsed.path != "/api/monitoring/baseline":
            return False
        try:
            self.write_json(baseline_payload(PATHS))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.write_json({"status": "data_unavailable", "error": str(exc), "series": [], "risk_eligible": False, "deployment_ready": False}, status=HTTPStatus.CONFLICT)
        return True

    def _get_inspection_history(self, parsed: Any) -> bool:
        if parsed.path not in {r['path'] for r in HISTORY_INTERFACES}:
            return False
        try:
            if parsed.path.endswith('/catalog'):
                self.write_json(public_history_catalog(PATHS))
            elif parsed.path.endswith('/source'):
                source, content = history_source(PATHS)
                self.send_response(HTTPStatus.OK)
                self.send_cors_headers()
                self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{quote(source.name)}")
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(content)
            else:
                self.write_json(history_payload(parse_qs(parsed.query, keep_blank_values=True), PATHS))
        except (ValueError, TypeError) as exc:
            self.write_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except (OSError, KeyError) as exc:
            self.write_json({'error': str(exc)}, status=HTTPStatus.CONFLICT)
        return True


    def _post_manual(self, parsed: Any) -> bool:
        if parsed.path.startswith("/api/manual/slopes/"):
            slope_id = parsed.path.rsplit("/", 1)[-1]
            if not find_slope(slope_id):
                self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
                return True
            try:
                record = save_slope_override(slope_id, self.read_json_body())
            except (TypeError, ValueError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            assessment = next(
                (row for row in current_risk_screening().get("assessments", []) if row.get("slope_id") == slope_id),
                None,
            )
            report = next(
                (row for row in current_completeness().get("reports", []) if row.get("slope_id") == slope_id),
                None,
            )
            self.write_json({"status": "saved", "record": record, "assessment": assessment, "completeness": report}, status=HTTPStatus.CREATED)
            return True
        if parsed.path == "/api/manual/rules":
            try:
                rule = save_manual_rule(self.read_json_body())
            except (TypeError, ValueError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self.write_json(
                {
                    "status": "saved",
                    "rule": rule,
                    "message": "规则已启用并参与P1-P4排序" if rule.get("execution_enabled") else "规则已保存，但当前不会自动执行",
                },
                status=HTTPStatus.CREATED,
            )
            return True
        return False


    def _post_pipeline(self, parsed: Any) -> bool:
        if parsed.path == "/api/documents/upload":
            self.handle_pdf_upload()
            return True
        if parsed.path == "/api/pipeline/jobs":
            try:
                options = self.pipeline_options(self.read_json_body())
                job, created = JOB_MANAGER.start(options)
            except (TypeError, ValueError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self.write_json(job, status=HTTPStatus.ACCEPTED if created else HTTPStatus.CONFLICT)
            return True
        return False


    def _post_rules(self, parsed: Any) -> bool:
        if parsed.path == "/api/rules/jobs":
            try:
                body = self.read_json_body()
                llm_model = str(body.get("llm_model", DEFAULT_DEEPSEEK_MODEL)).strip()
                if not llm_model or len(llm_model) > 100:
                    raise ValueError("大模型名称不合法")
                options = {
                    "use_llm": boolean_option(body, "use_llm", True),
                    "llm_model": llm_model,
                    "force_ocr": boolean_option(body, "force_ocr"),
                    "include_unselected": boolean_option(body, "include_unselected"),
                    "page_selection": body.get("page_selection"),
                }
                if options["page_selection"] is not None:
                    from .rule_extraction import validate_page_selection
                    validate_page_selection(RULE_PIPELINE.catalog_documents(), options["page_selection"])
                job, created = RULE_JOB_MANAGER.start(options, trigger="rule_extraction")
            except (TypeError, ValueError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self.write_json(job, status=HTTPStatus.ACCEPTED if created else HTTPStatus.CONFLICT)
            return True
        return False


    def _post_exports(self, parsed: Any) -> bool:
        if parsed.path == "/api/export/monitoring":
            try:
                body = self.read_json_body()
                file_name = safe_export_name(str(body.get("file_name") or "会昌监测历史回放"))
                query = {key: [str(body[key])] for key in ("start", "end") if key in body}
                with reserve_export_destination(PATHS.output_dir / "share", file_name) as destination:
                    result = export_monitoring_report(destination, query, PATHS)
            except (TypeError, ValueError, OSError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self.write_json({**result, "file_name": destination.name, "download_url": f"/output/demo/share/{quote(destination.name)}"}, status=HTTPStatus.CREATED)
            return True
        if parsed.path == "/api/export/standalone":
            try:
                body = self.read_json_body()
                file_name = safe_export_name(str(body.get("file_name", "")))
                graph_mode = str(body.get("graph_mode", "active")).strip().lower()
                destination = PATHS.output_dir / "share" / file_name
                result = export_standalone_html(
                    destination,
                    graph_mode=graph_mode,
                    graph_override=current_graph() if graph_mode == "active" else None,
                )
            except (TypeError, ValueError, FileNotFoundError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self.write_json(
                {
                    **result,
                    "file_name": file_name,
                    "download_url": f"/output/demo/share/{quote(file_name)}",
                },
                status=HTTPStatus.CREATED,
            )
            return True
        if parsed.path == "/api/export/risk-standalone":
            try:
                body = self.read_json_body()
                file_name = safe_export_name(str(body.get("file_name") or "SlopeKG_风险研判_离线版"))
                destination = PATHS.output_dir / "share" / file_name
                result = export_risk_standalone_html(destination, risk_override=current_risk_screening())
            except (TypeError, ValueError, FileNotFoundError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self.write_json(
                {
                    **result,
                    "file_name": file_name,
                    "download_url": f"/output/demo/share/{quote(file_name)}",
                },
                status=HTTPStatus.CREATED,
            )
            return True
        return False


    def _post_risk(self, parsed: Any) -> bool:
        if parsed.path == "/api/risk/assess":
            try:
                body = self.read_json_body()
                slope_id = str(body.get("slope_id") or "").strip()
                if not slope_id:
                    raise ValueError("slope_id 不能为空")
                payload = current_risk_screening()
                result = next((row for row in payload.get("assessments", []) if row.get("slope_id") == slope_id), None)
                if result is None:
                    self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
                    return True
            except (TypeError, ValueError) as exc:
                self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return True
            self.write_json(
                {
                    "status": "screening_completed",
                    "formal_risk_assessment_enabled": False,
                    "result": result,
                    "boundary": payload.get("boundary", {}),
                    "message": "已生成宏观复核优先级；这不是正式风险等级。",
                }
            )
            return True
        return False

    def do_DELETE(self) -> None:  # noqa: N802
        if not self.request_origin_allowed():
            self.write_json({"error": "cross-origin request denied"}, status=HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/manual/slopes/"):
            slope_id = parsed.path.rsplit("/", 1)[-1]
            deleted = delete_slope_override(slope_id)
            self.write_json({"status": "deleted" if deleted else "not_found", "slope_id": slope_id}, status=HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/api/manual/rules/"):
            rule_id = parsed.path.rsplit("/", 1)[-1]
            deleted = delete_manual_rule(rule_id)
            self.write_json({"status": "deleted" if deleted else "not_found", "rule_id": rule_id}, status=HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND)
            return
        self.write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def pipeline_options(self, body: dict[str, Any]) -> dict[str, Any]:
        recognizer=body.get('recognizer','legacy')
        if recognizer=='multimodal':
            return visual_job_options(PATHS,body)
        if recognizer!='legacy':
            raise ValueError('未知识别路线')
        if any(k in body for k in ('input_pdfs','model_profile','review_profile','visual_settings','prepare_only','allow_external')):
            raise ValueError('多模态设置需要选择multimodal路线')
        ocr_pages = max(0, min(1000, int(body.get("ocr_pages", 1000))))
        llm_model = str(body.get("llm_model", DEFAULT_DEEPSEEK_MODEL)).strip()
        if not llm_model or len(llm_model) > 100:
            raise ValueError("大模型名称不合法")
        legacy_use_llm = boolean_option(body, "use_llm")
        parse_mode = str(body.get("parse_mode") or ("deep" if legacy_use_llm else "basic")).strip().lower()
        if parse_mode not in {"basic", "deep"}:
            raise ValueError("parse_mode 只能为 basic 或 deep")
        ocr_scope = str(body.get("ocr_scope", "all")).strip().lower()
        if ocr_scope not in {"all", "changed"}:
            raise ValueError("ocr_scope 只能为 all 或 changed")
        ocr_device = str(body.get("ocr_device", "auto")).strip().lower()
        if ocr_device not in {"auto", "cpu", "gpu"}:
            raise ValueError("ocr_device 只能为 auto、cpu 或 gpu")
        ocr_workers = max(1, min(16, int(body.get("ocr_workers", 4))))
        ocr_batch_size = max(0, min(32, int(body.get("ocr_batch_size", 0))))
        return {
            "ocr_pages": ocr_pages,
            "force_ocr": boolean_option(body, "force_ocr"),
            "parse_mode": parse_mode,
            "llm_model": llm_model,
            "reuse_parsed": boolean_option(body, "reuse_parsed"),
            "ocr_scope": ocr_scope,
            "ocr_device": ocr_device,
            "ocr_workers": ocr_workers,
            "ocr_batch_size": ocr_batch_size,
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
            options = self.pipeline_options({
                **automatic_basic_options(), **parse_upload_options(content_type, raw),
                "recognizer": "legacy",
                "parse_mode": "basic", "reuse_parsed": False, "ocr_scope": "changed",
            })
        except (ValueError, TypeError, OverflowError) as exc:
            self.write_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        ensure_dir(PATHS.raw_dir)
        saved = []
        for file_name, data in pending:
            destination = save_pdf_upload(PATHS.raw_dir, file_name, data)
            saved.append(
                {
                    "original_name": file_name,
                    "file_name": destination.name,
                    "size_bytes": len(data),
                    "renamed": destination.name != file_name,
                }
            )
        job, created = JOB_MANAGER.start(options, trigger="upload")
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
        graph = current_graph()
        node = next((item for item in graph.get("nodes", []) if item.get("id") == slope_id and item.get("type") == "Slope"), None)
        if node is None:
            self.write_json({"error": f"unknown slope: {slope_id}"}, status=HTTPStatus.NOT_FOUND)
            return
        edges = [edge for edge in graph.get("edges", []) if slope_id in {edge.get("source"), edge.get("target")}]
        related_ids = {edge["target"] if edge["source"] == slope_id else edge["source"] for edge in edges}
        related_nodes = [item for item in graph.get("nodes", []) if item.get("id") in related_ids]
        evidence_ids = {edge.get("props", {}).get("evidence") for edge in edges if edge.get("props", {}).get("evidence")}
        evidence = [item for item in graph.get("evidence", []) if item.get("id") in evidence_ids]
        completeness = current_completeness()
        report = next((row for row in completeness.get("reports", []) if row.get("slope_id") == slope_id), None)
        self.write_json({"slope": node, "edges": edges, "related_nodes": related_nodes, "evidence": evidence, "completeness": report})

    def write_risk_readiness(self, slope_id: str | None) -> None:
        payload = current_completeness()
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
        if length < 0:
            raise ValueError("Content-Length 不能为负数")
        if length == 0:
            return {}
        if length > 1024 * 1024:
            raise ValueError("JSON请求体过大")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError("JSON请求体不完整")
        raw = data.decode("utf-8")
        body = json.loads(raw, parse_constant=reject_json_constant, parse_float=finite_json_number)
        if not isinstance(body, dict):
            raise ValueError("JSON请求体必须是对象")
        return body

    def write_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin_matches_host(origin, self.headers.get("Host")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def request_origin_allowed(self) -> bool:
        return origin_matches_host(self.headers.get("Origin"), self.headers.get("Host"))

    def translate_path(self, path: str) -> str:
        return str(safe_static_path(path))

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
    httpd = ThreadingHTTPServer((host, port), SlopeKGHandler)
    try:
        PDF_WATCHER.start()
        print(f"SlopeKG demo server: http://{host}:{port}/web/index.html")
        httpd.serve_forever()
    finally:
        PDF_WATCHER.stop()
        httpd.server_close()


def document_inventory() -> dict[str, Any]:
    return queries.document_inventory(PATHS)


def empty_rule_library() -> dict[str, Any]:
    return queries.empty_rule_library()


def current_graph() -> dict[str, Any]:
    return queries.current_graph(PATHS)


def current_rule_library() -> dict[str, Any]:
    return queries.current_rule_library(PATHS)


def current_completeness() -> dict[str, Any]:
    return queries.current_completeness(PATHS)


def find_slope(slope_id: str) -> dict[str, Any] | None:
    return queries.find_slope(slope_id, PATHS)


def current_risk_screening() -> dict[str, Any]:
    return queries.current_risk_screening(PATHS)


def current_risk_evaluation() -> dict[str, Any]:
    return queries.current_risk_evaluation(PATHS)

def main() -> None:
    parser = argparse.ArgumentParser(description="Run SlopeKG demo backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
