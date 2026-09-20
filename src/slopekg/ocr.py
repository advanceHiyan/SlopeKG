from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import configure_runtime_cache
from .deps import bundled_poppler_exe, has_module
from .storage import ensure_dir


class OcrRunner:
    def __init__(
        self,
        root: Path,
        assets_dir: Path,
        *,
        device: str = "auto",
        render_workers: int = 4,
        batch_size: int = 0,
    ):
        self.root = root
        self.assets_dir = assets_dir
        self.requested_device = device
        self.render_workers = max(1, min(16, int(render_workers)))
        self.batch_size = max(0, min(32, int(batch_size)))
        self.last_device = "unresolved"
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
                self.last_device = resolve_paddle_device(self.requested_device)
                effective_batch_size = self.batch_size or (4 if self.last_device.startswith("gpu") else 1)
                ocr_engine = make_paddle_ocr(self.last_device, batch_size=effective_batch_size)
            except Exception as exc:  # pragma: no cover - runtime/model specific
                engine_error = str(exc)
        else:
            effective_batch_size = 1
        completed_selected = 0
        updates_by_id: dict[str, dict[str, Any]] = {}

        def completed(updated: dict[str, Any], page_results: list[dict[str, Any]]) -> None:
            nonlocal completed_selected
            completed_selected += 1
            updates_by_id[updated["id"]] = updated
            results.extend(page_results)
            if task_callback:
                task_callback(completed_selected, len(selected), updated, page_results)

        if not selected:
            return list(tasks), results

        if not paddle_available or ocr_engine is None:
            for task in selected:
                status = "failed" if engine_error else "engine_missing"
                updated = {**task, "status": status, "engine": "PaddleOCR", "device": self.last_device}
                if engine_error:
                    updated["error"] = f"OCR engine initialization failed: {engine_error}"
                completed(updated, [])
            return [updates_by_id.get(task["id"], task) for task in tasks], results

        rendered: dict[str, Path] = {}
        worker_count = min(self.render_workers, len(selected))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ocr-render") as executor:
            futures = {executor.submit(self.render_page, task, force): task for task in selected}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    rendered[task["id"]] = future.result()
                except Exception as exc:
                    completed(
                        {**task, "status": "failed", "error": str(exc), "engine": "PaddleOCR", "device": self.last_device},
                        [],
                    )

        inference_tasks = [task for task in selected if task["id"] in rendered]
        for offset in range(0, len(inference_tasks), effective_batch_size):
            batch_tasks = inference_tasks[offset : offset + effective_batch_size]
            batch_paths = [rendered[task["id"]] for task in batch_tasks]
            if not paddle_available or ocr_engine is None:
                break
            try:
                batch_raw = call_paddle_ocr_batch(ocr_engine, batch_paths)
            except Exception as exc:  # pragma: no cover - engine-specific
                # A single malformed page must not discard the rest of the batch.
                for task, image_path in zip(batch_tasks, batch_paths):
                    try:
                        raw = call_paddle_ocr(ocr_engine, image_path)
                    except Exception as page_exc:  # pragma: no cover - engine-specific
                        completed(
                            {
                                **task,
                                "status": "failed",
                                "image_path": rel(image_path, self.root),
                                "error": f"batch={exc}; page={page_exc}",
                                "engine": "PaddleOCR",
                                "device": self.last_device,
                            },
                            [],
                        )
                        continue
                    page_results = normalize_paddle_result(task, raw, image_path, self.root)
                    completed(done_task(task, image_path, page_results, self.root, self.last_device), page_results)
                continue
            for task, image_path, raw in zip(batch_tasks, batch_paths, batch_raw):
                page_results = normalize_paddle_result(task, raw, image_path, self.root)
                completed(done_task(task, image_path, page_results, self.root, self.last_device), page_results)

        updated_tasks = [updates_by_id.get(task["id"], task) for task in tasks]
        return updated_tasks, results

    def render_page(self, task: dict[str, Any], force: bool = False) -> Path:
        source = self.root / task["path"]
        out_dir = ensure_dir(self.assets_dir / "ocr_pages")
        source_tag = str(task.get("source_sha256") or "legacy")[:10]
        prefix = out_dir / f"{Path(task['file_name']).stem}_{source_tag}_p{int(task['page']):04d}"
        region = str(task.get("region") or "full_page")
        render_tag = "fitz-v2" if region == "title_block" else "fitz"
        fitz_target = Path(f"{prefix}-{region}-{render_tag}.png")
        if fitz_target.exists() and not force:
            return fitz_target
        if has_module("fitz"):
            return render_with_pymupdf(source, int(task["page"]), fitz_target, region=region)
        exe = bundled_poppler_exe("pdftoppm")
        if not exe:
            raise RuntimeError("pdftoppm not found; install poppler or use the conda environment")
        # Keep fallback cache names region-specific as well. Poppler still renders
        # the full page; title-block cropping requires PyMuPDF and must not reuse
        # a cached image from a different region.
        poppler_prefix = Path(f"{prefix}-{region}-poppler")
        existing = sorted(out_dir.glob(f"{poppler_prefix.name}-*.png"))
        if existing and not force:
            return existing[0]
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
                    str(poppler_prefix),
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else str(exc)
            raise RuntimeError(f"pdftoppm failed for {task['id']}: {message}") from exc
        rendered = sorted(out_dir.glob(f"{poppler_prefix.name}-*.png"))
        if not rendered:
            # Rare fallback for poppler naming.
            alt = Path(f"{poppler_prefix}.png")
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
        rotation = 0
        if region == "title_block":
            rect = page.rect
            clip_values, rotation = title_block_render_plan(rect.width, rect.height)
            clip = fitz.Rect(*clip_values)
        # Keep the complete bottom title strip, including the left-hand project
        # name/design stage. 240 DPI keeps an A3 width below 4000 pixels.
        dpi = 240
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        if rotation:
            matrix.prerotate(rotation)
        pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        pixmap.save(target)
    finally:
        document.close()
    return target


def title_block_render_plan(width: float, height: float) -> tuple[tuple[float, float, float, float], int]:
    """Return a title-block clip and OCR-facing rotation for drawing sheets.

    Landscape pages need the entire bottom strip: design stage and project name
    can occupy its left half. A small group of source PDFs stores an otherwise
    landscape drawing as a portrait bitmap without a PDF rotation flag.  For
    those pages the same title block is physically on the left edge, so it must
    be cropped there and rotated counter-clockwise before OCR.
    """
    width = float(width)
    height = float(height)
    if height > width * 1.15:
        return (0.0, 0.0, width * 0.30, height), -90
    return (0.0, height * 0.52, width, height), 0


def with_layout_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain OCR boxes and add traceable joins of tightly wrapped short labels.

    Only centered Chinese labels of comparable line height in the same image
    can join. Nearby columns, numeric fields and page boundaries cannot join.
    These are layout candidates, not human-reviewed annotations.
    """
    originals = [row for row in rows if row.get("method") != "ocr_layout_join"]
    by_image: dict[tuple, list[tuple[dict, list[float]]]] = defaultdict(list)
    for row in originals:
        if "title_block" in str(row.get("task_id") or ""):
            continue
        box = row.get("bbox")
        text = str(row.get("text") or "").strip()
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            continue
        height, width = box[3] - box[1], box[2] - box[0]
        if height <= 0 or width <= 0 or width > height * 6 or not re.fullmatch(r"[\u4e00-\u9fff]{2,12}", text):
            continue
        key = (row.get("task_id"), row.get("document_id"), row.get("page"), row.get("image_path"))
        by_image[key].append((row, box))
    groups = []
    for items in by_image.values():
        items.sort(key=lambda item: (item[1][1], item[1][0]))
        for index, (first, a) in enumerate(items):
            if len(str(first["text"]).strip()) < 4:
                continue
            ah = a[3] - a[1]
            candidates = []
            for second, b in items[index + 1:]:
                bh = b[3] - b[1]
                gap = b[1] - a[3]
                if (len(str(second["text"]).strip()) <= 4
                        and str(second["text"]).strip() not in str(first["text"])
                        and b[2] - b[0] <= (a[2] - a[0]) * 0.75
                        and -0.1 * min(ah, bh) <= gap <= 0.25 * min(ah, bh)
                        and 0.65 <= ah / bh <= 1.55
                        and abs((a[0] + a[2] - b[0] - b[2]) / 2) <= min(ah, bh) * 0.35):
                    candidates.append((second, b))
            if len(candidates) != 1:
                continue
            second, b = candidates[0]
            groups.append({**first, "id": f"{first['id']}__join__{second['id']}",
                           "text": str(first["text"]).strip() + str(second["text"]).strip(),
                           "bbox": [min(a[0], b[0]), a[1], max(a[2], b[2]), b[3]],
                           "method": "ocr_layout_join", "review_status": "pending",
                           "source_item_ids": [first["id"], second["id"]]})
    return [*originals, *groups]


def resolve_paddle_device(requested: str = "auto") -> str:
    requested = str(requested or "auto").strip().lower()
    if requested not in {"auto", "cpu", "gpu", "gpu:0"}:
        raise ValueError("OCR device must be auto, cpu or gpu")
    if requested == "cpu":
        return "cpu"
    try:
        import paddle  # type: ignore
    except Exception as exc:
        if requested.startswith("gpu"):
            raise RuntimeError(f"GPU OCR requested but PaddlePaddle could not be loaded: {exc}") from exc
        return "cpu"
    gpu_available = bool(paddle.device.is_compiled_with_cuda()) and int(paddle.device.cuda.device_count()) > 0
    if requested.startswith("gpu") and not gpu_available:
        raise RuntimeError("GPU OCR requested, but the installed PaddlePaddle is CPU-only; install a compatible paddlepaddle-gpu build")
    return "gpu:0" if gpu_available else "cpu"


def make_paddle_ocr(device: str = "auto", batch_size: int = 1) -> Any:
    from paddleocr import PaddleOCR  # type: ignore

    device = resolve_paddle_device(device)
    batch_size = max(1, int(batch_size))
    configs = [
        {
            "lang": "ch",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "enable_mkldnn": False,
            "device": device,
            "text_recognition_batch_size": batch_size,
        },
        {"lang": "ch", "use_textline_orientation": False, "enable_mkldnn": False, "device": device},
        {"lang": "ch", "device": device},
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


def call_paddle_ocr_batch(ocr_engine: Any, image_paths: list[Path]) -> list[Any]:
    if not image_paths:
        return []
    if len(image_paths) == 1:
        return [call_paddle_ocr(ocr_engine, image_paths[0])]
    predicted = list(ocr_engine.predict([str(path) for path in image_paths]))
    if len(predicted) != len(image_paths):
        raise RuntimeError(f"PaddleOCR returned {len(predicted)} results for {len(image_paths)} images")
    return predicted


def done_task(task: dict[str, Any], image_path: Path, page_results: list[dict[str, Any]], root: Path, device: str) -> dict[str, Any]:
    return {
        **task,
        "status": "done",
        "image_path": rel(image_path, root),
        "engine": "PaddleOCR",
        "device": device,
        "items": len(page_results),
    }


def normalize_paddle_result(task: dict[str, Any], raw: Any, image_path: Path, root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not raw:
        return with_layout_groups(rows)
    if isinstance(raw, dict) or hasattr(raw, "get"):
        append_mapping_result(rows, task, raw, image_path, root)
        return with_layout_groups(rows)
    if isinstance(raw, list) and raw and (isinstance(raw[0], dict) or hasattr(raw[0], "get")):
        for item in raw:
            append_mapping_result(rows, task, item, image_path, root)
        return with_layout_groups(rows)
    page_items = raw[0] if isinstance(raw, list) and raw else []
    for idx, item in enumerate(page_items, start=1):
        try:
            bbox, payload = item
            text, confidence = payload
        except Exception:
            continue
        rows.append(make_result_row(task, idx, text, confidence, bbox, image_path, root))
    return with_layout_groups(rows)


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
