"""One PDF imaging route for native text, scans and vector-only drawings."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import fitz

from .model_client import ImageInput, content_hash
from .visual_contract import bbox


class VisualDocument:
    def __init__(self, path: Path, work_dir: Path):
        self.path = path.resolve()
        self.sha256 = self._hash()
        self.id = "pdf_" + self.sha256[:20]
        self.work_dir = work_dir
        self.pdf = fitz.open(self.path)
        if self.pdf.needs_pass or not len(self.pdf):
            self.pdf.close()
            raise ValueError("Empty or password-protected PDF")

    def _hash(self) -> str:
        digest = hashlib.sha256()
        with self.path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def close(self):
        self.pdf.close()

    def page_info(self, number: int) -> dict:
        page = self.pdf[number - 1]
        rotation = page.rotation
        page.set_rotation(0)
        return {"page": number, "width": page.rect.width, "height": page.rect.height,
                "original_rotation": rotation, "coordinate_system": "unrotated_pdf_normalized"}

    def native_evidence(self, number: int, region: list | None = None) -> list[dict]:
        page = self.pdf[number - 1]
        page.set_rotation(0)
        rect = page.rect
        result = []
        for block in page.get_text("blocks"):
            if len(block) < 7 or block[6] != 0 or not block[4].strip():
                continue
            box = [max(0, block[0] / rect.width), max(0, block[1] / rect.height),
                   min(1, block[2] / rect.width), min(1, block[3] / rect.height)]
            if region and not (box[0] < region[2] and box[2] > region[0] and box[1] < region[3] and box[3] > region[1]):
                continue
            result.append({"source": "pdf_native", "page": number, "bbox": box, "text": block[4]})
        return result

    def render(self, number: int, region: list | None = None, *, edge: int = 1800,
               rotation: int = 0, max_pixels: int = 12000000) -> ImageInput:
        region = bbox(region or [0, 0, 1, 1])
        if rotation not in (0, 90, 180, 270) or not 256 <= edge <= 10000 or max_pixels < 65536:
            raise ValueError("Invalid image render budget")
        page = self.pdf[number - 1]
        page.set_rotation(0)
        rect = page.rect
        clip = fitz.Rect(region[0] * rect.width, region[1] * rect.height,
                         region[2] * rect.width, region[3] * rect.height)
        # Render the original source at the crop's target resolution. Do not
        # enlarge a thumbnail and pretend it recovers tiny vector annotations.
        scale = min(edge / max(clip.width, clip.height),
                    math.sqrt(max_pixels / (clip.width * clip.height)))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        if pix.width * pix.height > max_pixels:
            scale *= math.sqrt(max_pixels / (pix.width * pix.height)) * .998
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        if rotation:
            from PIL import Image
            import io
            image = Image.open(io.BytesIO(pix.tobytes("png"))).rotate(-rotation, expand=True)
            data = io.BytesIO()
            image.save(data, format="PNG")
            raw, width, height = data.getvalue(), image.width, image.height
        else:
            raw, width, height = pix.tobytes("png"), pix.width, pix.height
        name = content_hash([self.sha256, number, region, edge, max_pixels, rotation])[:32] + ".png"
        target = self.work_dir / "images" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return ImageInput(target, f"page={number}; bbox={region}; clockwise_rotation={rotation}; original PDF render", width, height)


class ExistingOcrEvidence:
    """Read existing project sidecars only when document content hashes match."""

    def __init__(self, parsed_dir: Path):
        from .storage import read_jsonl
        self.documents = read_jsonl(parsed_dir / "documents.jsonl")
        self.results = read_jsonl(parsed_dir / "ocr_results.jsonl")
        self.tasks = {r["id"]: r for r in read_jsonl(parsed_dir / "ocr_tasks.jsonl")}

    def __call__(self, document: VisualDocument, page: int) -> dict:
        matches = [r for r in self.documents if r.get("source_sha256") == document.sha256]
        if len(matches) != 1:
            return {"status": "unavailable_or_stale", "rows": []}
        doc_id = matches[0]["id"]
        values = []
        for row in self.results:
            task = self.tasks.get(row.get("task_id"), {})
            if (row.get("document_id") == doc_id and row.get("page") == page
                    and task.get("source_sha256") == document.sha256
                    and row.get("source_sha256") == document.sha256 and row.get("text")):
                # Old OCR coordinates can be image pixels. Do not mislabel them
                # as page-normalized boxes or use them to cut the source page.
                values.append({"source": "existing_ocr", "text": row["text"],
                               "source_row_id": row.get("id"), "bbox": row.get("bbox"),
                               "bbox_coordinate_space": row.get("bbox_coordinate_space", "unknown")})
        return {"status": "available" if values else "unavailable_or_stale", "rows": values}
