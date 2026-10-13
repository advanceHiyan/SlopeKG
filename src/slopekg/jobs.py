"""Background task state and source-directory scheduling, independent of HTTP."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from typing import Any
from .config import PATHS, DemoPaths
from .storage import discover_pdf_files, ensure_dir, read_jsonl

class PipelineJobManager:
    def __init__(self, pipeline: Any, *, completion_message: str = "PDF解析、信息抽取和图谱生成已完成") -> None:
        self.pipeline = pipeline
        self.completion_message = completion_message
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
                "options": self._copy(options),
                "trigger": trigger,
                "result": None,
                "error": None,
            }
            self.jobs[job_id] = job
            self.latest_id = job_id
            self._trim()
        try:
            threading.Thread(target=self._run, args=(job_id,), daemon=True, name=f"pipeline-{job_id}").start()
        except Exception as exc:
            self._update(
                job_id, status="failed", stage="failed", message="无法启动后台任务",
                error=str(exc), finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        return self.get(job_id) or {}, True

    def _run(self, job_id: str) -> None:
        self._update(job_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))

        def on_progress(percent: int, stage: str, message: str) -> None:
            self._update(job_id, progress=max(0, min(100, int(percent))), stage=stage, message=message)

        try:
            options = self.get(job_id)["options"]  # type: ignore[index]
            state = self.pipeline.run(progress_callback=on_progress, **options)
            now = datetime.now().isoformat(timespec="seconds")
            status = state.get("processing_status", "completed")
            status = status if status in {"completed", "partial", "failed"} else "completed"
            self._update(
                job_id,
                status=status,
                progress=100,
                stage=state.get("publication_stage", status) if status != "completed" else "completed",
                message=state.get("processing_message") or self.completion_message,
                result=state,
                error=state.get("processing_message") if status == "failed" else None,
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


def automatic_basic_options() -> dict[str, Any]:
    """Fast, deterministic publication used whenever the source directory changes."""
    return {
        "ocr_pages": 1000,
        "force_ocr": False,
        "parse_mode": "basic",
        "llm_model": "deepseek-v4-flash",
        "reuse_parsed": False,
        "ocr_scope": "changed",
        "ocr_device": "auto",
        "ocr_workers": 4,
        "ocr_batch_size": 0,
    }


class PdfDirectoryWatcher:
    """Poll rawPDF and schedule a basic rebuild after files have stopped changing."""

    def __init__(self, manager: PipelineJobManager, interval_seconds: float = 2.0, *, paths: DemoPaths = PATHS) -> None:
        self.manager = manager
        self.paths = paths
        self.interval_seconds = interval_seconds
        self._candidate: tuple[tuple[str, int, int], ...] | None = None
        self._stable_polls = 0
        self._scheduled_snapshot: tuple[tuple[str, int, int], ...] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="rawpdf-auto-update")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)

    def snapshot(self) -> tuple[tuple[str, int, int], ...]:
        ensure_dir(self.paths.raw_dir)
        rows = []
        for file in discover_pdf_files(self.paths.raw_dir):
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
        if snapshot == self._scheduled_snapshot:
            return False
        # Auto-update needs only source fingerprints, not page/text/table counts.
        # The full inventory is still available to the documents API.
        documents = {row.get("file_name"): row for row in read_jsonl(self.paths.parsed_dir / "documents.jsonl")}
        needs_parsing = any(
            name not in documents
            or documents[name].get("source_size_bytes") != size
            or documents[name].get("source_mtime_ns") != modified
            for name, size, modified in snapshot
        )
        if self._stable_polls < 2 or not needs_parsing:
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
