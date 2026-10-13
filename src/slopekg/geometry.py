"""OCR/PDF boundary geometry; downstream layout coordinates are PDF points."""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
from typing import Any

from .storage import read_json


def rectangle(value: Any) -> list[float] | None:
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        if len(value) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            xs, ys = [float(value[0]), float(value[2])], [float(value[1]), float(value[3])]
        elif len(value) >= 3 and all(len(p) == 2 for p in value):
            xs, ys = [float(p[0]) for p in value], [float(p[1]) for p in value]
        else:
            return None
        if not all(math.isfinite(v) for v in [*xs, *ys]):
            return None
        if max(xs) <= min(xs) or max(ys) <= min(ys):
            return None
        return [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, OverflowError):
        return None


def project_ocr_boxes(rows: list[dict[str, Any]], tasks: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    """Map OCR pixels to native text coordinates without rewriting raw evidence.

    Old PaddleOCR sidecars stored raw pixels with no unit marker. Recover only
    a known renderer's transform from its actual image/source, never from a
    guessed DPI. Unknown renderer/crop/units remain explicitly unmapped.
    """
    output = [dict(row) for row in rows]
    if not output:
        return output
    try:
        import fitz
    except ImportError:
        for row in output:
            if row.get("bbox_coordinate_space") == "pdf_points":
                continue
            row.setdefault("bbox_original", row.get("bbox"))
            row["image_bbox"] = rectangle(row.get("image_bbox", row.get("bbox")))
            row["bbox"] = None
            row["bbox_coordinate_space"] = "unmapped_image_pixels"
            row["geometry_issue"] = "pdf_geometry_backend_unavailable"
        return output
    task_map = {t["id"]: t for t in tasks}
    groups = defaultdict(list)
    for row in output:
        if row.get("bbox_coordinate_space") != "pdf_points":
            groups[(row.get("task_id"), row.get("image_path"))].append(row)
    for (task_id, image_name), members in groups.items():
        task = task_map.get(task_id, {})
        inverse = None
        try:
            image = root / str(image_name)
            meta = read_json(Path(str(image) + ".geometry.json"), {})
            if meta.get("pdf_to_image"):
                transform = fitz.Matrix(*meta["pdf_to_image"])
            else:
                # Both historic renderer paths are known. Unrecognized images
                # must carry explicit geometry metadata before layout use.
                if not ("fitz" in image.name or "poppler" in image.name):
                    raise ValueError("unknown legacy renderer")
                with fitz.open(root / task["path"]) as doc:
                    page = doc[int(task["page"]) - 1]
                    clip, rotation = page.rect, 0
                    if task.get("region") == "title_block" and "fitz" in image.name:
                        from .ocr import title_block_render_plan
                        values, rotation = title_block_render_plan(clip.width, clip.height)
                        clip = fitz.Rect(*values)
                    turn = fitz.Matrix(1, 1).prerotate(rotation)
                    bounds = clip * turn
                    pixmap = fitz.Pixmap(image)
                    rendered = turn * fitz.Matrix(pixmap.width / bounds.width, pixmap.height / bounds.height)
                    origin = (clip * rendered).irect
                    transform = page.rotation_matrix * rendered
                    transform.e -= origin.x0
                    transform.f -= origin.y0
            if not all(math.isfinite(v) for v in transform) or abs(transform.a * transform.d - transform.b * transform.c) < 1e-12:
                raise ValueError("invalid image transform")
            inverse = ~transform
        except (OSError, RuntimeError, KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
        for row in members:
            pixels = rectangle(row.get("image_bbox", row.get("bbox")))
            row.setdefault("bbox_original", row.get("bbox"))
            row["image_bbox"] = pixels
            if pixels is not None and inverse is not None:
                row["bbox"] = rectangle(list(fitz.Rect(pixels) * inverse))
                row["bbox_coordinate_space"] = "pdf_points"
                row.pop("geometry_issue", None)
            else:
                row["bbox"] = None
                row["bbox_coordinate_space"] = "unmapped_image_pixels"
                row["geometry_issue"] = "missing_or_invalid_pdf_image_transform"
    return output
