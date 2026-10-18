"""One PDF imaging route for native text, scans and vector-only drawings."""
from __future__ import annotations

import hashlib
import io
import math
import os
from pathlib import Path
import tempfile
import re
import unicodedata

import fitz

from .model_client import ImageInput, content_hash
from .storage import read_json, write_json
from .visual_contract import bbox


class VisualDocument:
    def __init__(self, path: Path, work_dir: Path):
        self.path = path.resolve()
        self.sha256 = self._hash()
        self.id = "pdf_" + self.sha256[:20]
        self.work_dir = work_dir
        self._native_cache: dict[int,list[dict]] = {}
        self._geometry_cache: dict[int,list[dict]] = {}
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
        return {"page": number, "width": page.rect.width, "height": page.rect.height,
                "original_rotation": rotation, "coordinate_system": "display_page_normalized"}

    def native_evidence(self, number: int, region: list | None = None) -> list[dict]:
        if number in self._native_cache:
            values=self._native_cache[number]
            if region is None: return values
            return [v for v in values if v["bbox"][0]<region[2] and v["bbox"][2]>region[0]
                    and v["bbox"][1]<region[3] and v["bbox"][3]>region[1]]
        page = self.pdf[number - 1]
        rect = page.rect
        result = []
        for index, block in enumerate(page.get_text("blocks")):
            if len(block) < 7 or block[6] != 0 or not block[4].strip():
                continue
            displayed = fitz.Rect(block[:4]) * page.rotation_matrix
            box = [max(0, displayed.x0 / rect.width), max(0, displayed.y0 / rect.height),
                   min(1, displayed.x1 / rect.width), min(1, displayed.y1 / rect.height)]
            result.append({"id": f"native_p{number}_b{index}", "source": "pdf_native", "page": number, "bbox": box, "text": block[4]})
        self._native_cache[number]=result
        return self.native_evidence(number,region)

    def locate_quote(self, number: int, quote: str, region: list) -> dict | None:
        clean=lambda s: re.sub(r"\s+","",unicodedata.normalize("NFC",s))
        needle=clean(quote)
        from .visual_geometry import intersects
        lines=[a for a in self.geometric_evidence(number) if a["source"]=="pdf_native_line"
               and intersects(a["bbox"],region) and needle and needle in clean(a["text"])]
        if len(lines)==1:return lines[0]
        matches=[a for a in self.native_evidence(number,region) if needle and needle in clean(a["text"])]
        return matches[0] if len(matches)==1 else None

    def geometric_evidence(self, number: int) -> list[dict]:
        """One coordinate contract for native lines and raster-only ink cells."""
        if number in self._geometry_cache:return self._geometry_cache[number]
        from .visual_geometry import image_line_cells, intersects
        page=self.pdf[number-1];rect=page.rect;result=[]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines",[]):
                value="".join(span["text"] for span in line["spans"])
                if not value.strip():continue
                b=fitz.Rect(line["bbox"])*page.rotation_matrix
                box=[max(0,b.x0/rect.width),max(0,b.y0/rect.height),min(1,b.x1/rect.width),min(1,b.y1/rect.height)]
                if box[0]>=box[2] or box[1]>=box[3]:continue
                result.append({"id":f"line_p{number}_{len(result)}","source":"pdf_native_line","page":number,
                               "bbox":box,"text":value})
        raster_boxes=[]
        if not result:
            raster_boxes=[[0,0,1,1]]
        else:
            # Do not run image line clustering over native columns: a raster
            # detector can join two text columns into one false full-width line.
            # Restrict it to actual embedded image extents, mapping back through
            # display rotation just like native source cells.
            for info in page.get_image_info():
                b=fitz.Rect(info["bbox"])*page.rotation_matrix
                box=[max(0,b.x0/rect.width),max(0,b.y0/rect.height),min(1,b.x1/rect.width),min(1,b.y1/rect.height)]
                if box[0]<box[2] and box[1]<box[3] and box not in raster_boxes:raster_boxes.append(box)
        native=list(result)
        for raster in raster_boxes:
            image=self.render(number,raster,edge=1800)
            for local in image_line_cells(image.path):
                box=[raster[0]+local[0]*(raster[2]-raster[0]),raster[1]+local[1]*(raster[3]-raster[1]),
                     raster[0]+local[2]*(raster[2]-raster[0]),raster[1]+local[3]*(raster[3]-raster[1])]
                area=(box[2]-box[0])*(box[3]-box[1])
                covered=sum(max(0,min(box[2],c["bbox"][2])-max(box[0],c["bbox"][0]))*
                            max(0,min(box[3],c["bbox"][3])-max(box[1],c["bbox"][1]))
                            for c in native if intersects(box,c["bbox"]))
                if covered>=area*.8:continue
                result.append({"id":f"ink_p{number}_{len(result)}","source":"image_ink_geometry",
                               "page":number,"bbox":box})
        self._geometry_cache[number]=result
        return result

    def safe_bounds(self, number: int, region: list) -> list:
        from .visual_geometry import contain_source_cells
        return contain_source_cells(region,self.geometric_evidence(number))

    def recovery_bounds(self, number: int, parent: list, issue: list, edges=()) -> list:
        from .visual_geometry import recovery_bounds
        return recovery_bounds(parent,issue,self.geometric_evidence(number),edges)

    def render(self, number: int, region: list | None = None, *, edge: int = 1800,
               rotation: int = 0, max_pixels: int = 12000000) -> ImageInput:
        region = bbox(region or [0, 0, 1, 1])
        if rotation not in (0, 90, 180, 270) or not 256 <= edge <= 10000 or max_pixels < 65536:
            raise ValueError("Invalid image render budget")
        name = content_hash(["display-v2", self.sha256, number, region, edge, max_pixels, rotation])[:32] + ".png"
        target = self.work_dir / "images" / name
        label = f"page={number}; bbox={region}; clockwise_rotation={rotation}; original PDF render; display coordinates"
        metadata = read_json(target.with_suffix(".json"), {})
        if target.exists() and metadata.get("sha256") == hashlib.sha256(target.read_bytes()).hexdigest():
            return ImageInput(target, label, metadata["width"], metadata["height"])
        page = self.pdf[number - 1]
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
            image = Image.open(io.BytesIO(pix.tobytes("png"))).rotate(-rotation, expand=True)
            data = io.BytesIO()
            image.save(data, format="PNG")
            raw, width, height = data.getvalue(), image.width, image.height
        else:
            raw, width, height = pix.tobytes("png"), pix.width, pix.height
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix=".png.tmp")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            Path(temporary).replace(target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        write_json(target.with_suffix(".json"), {"width":width,"height":height,"sha256":hashlib.sha256(raw).hexdigest()})
        return ImageInput(target, label, width, height)


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
