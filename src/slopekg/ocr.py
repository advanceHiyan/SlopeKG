from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import configure_runtime_cache
from .deps import bundled_poppler_exe, has_module
from .storage import ensure_dir


class OcrRunner:
    def __init__(self, root: Path, assets_dir: Path):
        self.root = root
        self.assets_dir = assets_dir
        self.last_selected_ids: set[str] = set()
        configure_runtime_cache()

    def run(
        self,
        tasks: list[dict[str, Any]],
        max_pages: int = 1000,
        force: bool = False,
        task_callback: Callable[[int, int, dict[str, Any], list[dict[str, Any]]], None] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        updated_tasks: list[dict[str, Any]] = []
        eligible = tasks if force else [task for task in tasks if task.get("status") != "done"]
        selected = (
            sorted(
                eligible,
                key=lambda item: (
                    -int(item.get("priority", 0)),
                    str(item.get("file_name", "")).lower(),
                    int(item.get("page", 0)),
                ),
            )[:max_pages]
            if max_pages > 0
            else []
        )
        selected_ids = {task["id"] for task in selected}
        self.last_selected_ids = selected_ids
        paddle_available = has_module("paddleocr")
        ocr_engine = None
        engine_error = None
        if paddle_available and selected:
            try:
                ocr_engine = make_paddle_ocr()
            except Exception as exc:  # pragma: no cover - runtime/model specific
                engine_error = str(exc)
        completed_selected = 0

        def completed(updated: dict[str, Any], page_results: list[dict[str, Any]]) -> None:
            nonlocal completed_selected
            completed_selected += 1
            updated_tasks.append(updated)
            results.extend(page_results)
            if task_callback:
                task_callback(completed_selected, len(selected), updated, page_results)

        for task in tasks:
            if task["id"] not in selected_ids:
                updated_tasks.append(task)
                continue
            if not paddle_available or ocr_engine is None:
                status = "failed" if engine_error else "engine_missing"
                updated = {**task, "status": status, "engine": "PaddleOCR"}
                if engine_error:
                    updated["error"] = f"OCR engine initialization failed: {engine_error}"
                completed(updated, [])
                continue
            try:
                image_path = self.render_page(task, force=force)
            except Exception as exc:
                completed({**task, "status": "failed", "error": str(exc), "engine": "PaddleOCR"}, [])
                continue
            try:
                raw = call_paddle_ocr(ocr_engine, image_path)
            except Exception as exc:  # pragma: no cover - engine-specific
                completed({**task, "status": "failed", "image_path": rel(image_path, self.root), "error": str(exc), "engine": "PaddleOCR"}, [])
                continue
            page_results = normalize_paddle_result(task, raw, image_path, self.root)
            completed({**task, "status": "done", "image_path": rel(image_path, self.root), "engine": "PaddleOCR", "items": len(page_results)}, page_results)
        return updated_tasks, results

    def render_page(self, task: dict[str, Any], force: bool = False) -> Path:
        source = self.root / task["path"]
        out_dir = ensure_dir(self.assets_dir / "ocr_pages")
        source_tag = str(task.get("source_sha256") or "legacy")[:10]
        prefix = out_dir / f"{Path(task['file_name']).stem}_{source_tag}_p{int(task['page']):04d}"
        expected = Path(f"{prefix}-{int(task['page']):02d}.png")
        # pdftoppm names single-page outputs using the actual page number with
        # zero padding. Some builds use 1-based suffix without leading document
        # page width; find any matching file after rendering.
        existing = sorted(out_dir.glob(f"{prefix.name}-*.png"))
        if existing and not force:
            return existing[0]
        if has_module("fitz"):
            region = task.get("region", "full_page")
            target = Path(f"{prefix}-{region}-fitz.png")
            return render_with_pymupdf(source, int(task["page"]), target, region=region)
        exe = bundled_poppler_exe("pdftoppm")
        if not exe:
            raise RuntimeError("pdftoppm not found; install poppler or use the conda environment")
        try:
            subprocess.run(
                [
                    str(exe),
                    "-png",
                    "-r",
                    "150",
                    "-f",
                    str(task["page"]),
                    "-l",
                    str(task["page"]),
                    str(source),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else str(exc)
            raise RuntimeError(f"pdftoppm failed for {task['id']}: {message}") from exc
        rendered = sorted(out_dir.glob(f"{prefix.name}-*.png"))
        if not rendered:
            # Rare fallback for poppler naming.
            alt = Path(f"{prefix}.png")
            if alt.exists():
                return alt
            raise RuntimeError(f"rendered page image missing for {task['id']}")
        return rendered[0]


def render_with_pymupdf(source: Path, page_number: int, target: Path, region: str = "full_page") -> Path:
    import fitz  # type: ignore

    ensure_dir(target.parent)
    document = fitz.open(source)
    try:
        page = document.load_page(page_number - 1)
        clip = None
        if region == "title_block":
            rect = page.rect
            clip = fitz.Rect(rect.width * 0.5, rect.height * 0.52, rect.width, rect.height)
        dpi = 300 if region == "title_block" else 250
        pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
        pixmap.save(target)
    finally:
        document.close()
    return target


def make_paddle_ocr() -> Any:
    from paddleocr import PaddleOCR  # type: ignore

    configs = [
        {
            "lang": "ch",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "enable_mkldnn": False,
            "device": "cpu",
        },
        {"lang": "ch", "use_textline_orientation": False, "enable_mkldnn": False, "device": "cpu"},
        {"lang": "ch", "use_angle_cls": True, "show_log": False},
        {"lang": "ch"},
    ]
    last_error: Exception | None = None
    for kwargs in configs:
        try:
            return PaddleOCR(**kwargs)
        except TypeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return PaddleOCR()


def call_paddle_ocr(ocr_engine: Any, image_path: Path) -> Any:
    try:
        return ocr_engine.ocr(str(image_path), cls=True)
    except TypeError:
        return ocr_engine.ocr(str(image_path))


def normalize_paddle_result(task: dict[str, Any], raw: Any, image_path: Path, root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not raw:
        return rows
    if isinstance(raw, dict) or hasattr(raw, "get"):
        append_mapping_result(rows, task, raw, image_path, root)
        return rows
    if isinstance(raw, list) and raw and (isinstance(raw[0], dict) or hasattr(raw[0], "get")):
        for item in raw:
            append_mapping_result(rows, task, item, image_path, root)
        return rows
    page_items = raw[0] if isinstance(raw, list) and raw else []
    for idx, item in enumerate(page_items, start=1):
        try:
            bbox, payload = item
            text, confidence = payload
        except Exception:
            continue
        rows.append(make_result_row(task, idx, text, confidence, bbox, image_path, root))
    return rows


def append_mapping_result(
    rows: list[dict[str, Any]],
    task: dict[str, Any],
    result: Any,
    image_path: Path,
    root: Path,
) -> None:
    texts = first_present(result, "rec_texts", "texts") or []
    scores = first_present(result, "rec_scores", "scores")
    boxes = first_present(result, "rec_boxes", "dt_polys", "boxes")
    for local_index, text in enumerate(texts):
        row_index = len(rows) + 1
        rows.append(make_result_row(task, row_index, text, score_at(scores, local_index), box_at(boxes, local_index), image_path, root))


def first_present(mapping: Any, *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def make_result_row(
    task: dict[str, Any],
    idx: int,
    text: Any,
    confidence: Any,
    bbox: Any,
    image_path: Path,
    root: Path,
) -> dict[str, Any]:
    return {
        "id": f"{task['id']}_item_{idx:04d}",
        "task_id": task["id"],
        "document_id": task["document_id"],
        "page_id": task["page_id"],
        "file_name": task["file_name"],
        "page": task["page"],
        "text": str(text),
        "bbox": to_jsonable(bbox),
        "confidence": to_float(confidence),
        "engine": "PaddleOCR",
        "image_path": rel(image_path, root),
        "review_status": "pending",
        "source_sha256": task.get("source_sha256"),
    }


def score_at(scores: Any, index: int) -> Any:
    try:
        return scores[index]
    except Exception:
        return None


def box_at(boxes: Any, index: int) -> Any:
    try:
        return boxes[index]
    except Exception:
        return None


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
