from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader


@dataclass(frozen=True)
class PdfParseResult:
    documents: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    text_blocks: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    ocr_tasks: list[dict[str, Any]]


class PdfParser:
    """Page-adaptive PDF parser.

    Native text is preferred. OCR is scheduled only for scanned pages or
    drawings whose native text layer is insufficient. Every native block keeps
    its bounding box so extracted facts can link back to page evidence.
    """

    def __init__(self, root: Path):
        self.root = root

    def parse_many(
        self,
        files: list[Path],
        text_sample_pages: int | None = None,
        progress_callback: Callable[[int, int, str, int], None] | None = None,
    ) -> PdfParseResult:
        documents: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        text_blocks: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        ocr_tasks: list[dict[str, Any]] = []
        page_counts = [len(PdfReader(str(file)).pages) for file in files]
        total_pages = sum(page_counts)
        completed_pages = 0
        for file, page_count in zip(files, page_counts):
            def on_page(page_no: int, *, current_file: Path = file, offset: int = completed_pages) -> None:
                if progress_callback:
                    progress_callback(offset + page_no, total_pages, current_file.name, page_no)

            result = self.parse(file, text_sample_pages=text_sample_pages, page_callback=on_page)
            documents.extend(result.documents)
            pages.extend(result.pages)
            text_blocks.extend(result.text_blocks)
            tables.extend(result.tables)
            ocr_tasks.extend(result.ocr_tasks)
            completed_pages += page_count
        return PdfParseResult(documents, pages, text_blocks, tables, ocr_tasks)

    def parse(
        self,
        file: Path,
        text_sample_pages: int | None = None,
        page_callback: Callable[[int], None] | None = None,
    ) -> PdfParseResult:
        reader = PdfReader(str(file))
        source_stat = file.stat()
        doc_id = doc_id_for(file)
        rel_path = str(file.relative_to(self.root))
        kind = infer_doc_kind(file.name)
        page_limit = len(reader.pages) if text_sample_pages is None else min(len(reader.pages), text_sample_pages)
        documents = [
            {
                "id": doc_id,
                "title": file.stem,
                "file_name": file.name,
                "path": rel_path,
                "kind": kind,
                "pages": len(reader.pages),
                "parser": "pymupdf_layout+pypdf_fallback",
                "parse_scope": "full" if text_sample_pages is None else f"sample:{page_limit}",
                "source_size_bytes": source_stat.st_size,
                "source_mtime_ns": source_stat.st_mtime_ns,
                "source_sha256": file_sha256(file),
            }
        ]
        pages: list[dict[str, Any]] = []
        text_blocks: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        ocr_tasks: list[dict[str, Any]] = []

        fitz_doc = open_fitz(file)
        plumber_doc = open_pdfplumber(file)
        try:
            for index, pypdf_page in enumerate(reader.pages):
                page_no = index + 1
                page_id = f"{doc_id}_p{page_no:04d}"
                if index >= page_limit:
                    pages.append(skipped_page(page_id, doc_id, file.name, page_no))
                    if page_callback:
                        page_callback(page_no)
                    continue

                layout = extract_layout_page(fitz_doc, index, pypdf_page)
                full_text = layout["text"]
                compact = " ".join(full_text.split())
                classification = classify_page(
                    kind=kind,
                    page_no=page_no,
                    page_count=len(reader.pages),
                    text=full_text,
                    text_chars=len(compact),
                    width=layout["width"],
                    height=layout["height"],
                    image_count=layout["image_count"],
                    drawing_count=layout["drawing_count"],
                )

                pages.append(
                    {
                        "id": page_id,
                        "document_id": doc_id,
                        "file_name": file.name,
                        "page": page_no,
                        "width": round(layout["width"], 2),
                        "height": round(layout["height"], 2),
                        "orientation": classification["orientation"],
                        "text_chars": len(compact),
                        "text_sampled": True,
                        "native_block_count": len(layout["blocks"]),
                        "image_count": layout["image_count"],
                        "drawing_count": layout["drawing_count"],
                        "page_type": classification["page_type"],
                        "processing_strategy": classification["processing_strategy"],
                        "needs_ocr": classification["needs_ocr"],
                        "ocr_region": classification.get("ocr_region"),
                        "table_candidate": classification["table_candidate"],
                        "is_scanned_or_drawing": classification["page_type"] in {"scanned", "drawing"},
                        "extraction_error": layout.get("error"),
                    }
                )

                for block_index, block in enumerate(layout["blocks"], start=1):
                    text = clean_text(block["text"])
                    if not text:
                        continue
                    text_blocks.append(
                        {
                            "id": f"{page_id}_text_{block_index:04d}",
                            "document_id": doc_id,
                            "page_id": page_id,
                            "page": page_no,
                            "text": text,
                            "bbox": block.get("bbox"),
                            "block_type": block.get("block_type", "text"),
                            "extractor": layout["extractor"],
                            "reading_order": block_index,
                        }
                    )

                if classification["page_type"] == "table":
                    tables.extend(extract_tables(plumber_doc, index, doc_id, page_id, page_no))

                if classification["needs_ocr"]:
                    ocr_tasks.append(
                        {
                            "id": f"{page_id}_ocr_{classification['ocr_region']}",
                            "document_id": doc_id,
                            "page_id": page_id,
                            "file_name": file.name,
                            "path": rel_path,
                            "page": page_no,
                            "status": "pending",
                            "reason": classification["ocr_reason"],
                            "region": classification["ocr_region"],
                            "priority": classification["ocr_priority"],
                            "page_type": classification["page_type"],
                            "source_sha256": documents[0]["source_sha256"],
                        }
                    )
                if page_callback:
                    page_callback(page_no)
        finally:
            if fitz_doc is not None:
                fitz_doc.close()
            if plumber_doc is not None:
                plumber_doc.close()
        return PdfParseResult(documents, pages, text_blocks, tables, ocr_tasks)


def file_sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_fitz(file: Path) -> Any:
    try:
        import fitz  # type: ignore

        return fitz.open(file)
    except Exception:
        return None


def open_pdfplumber(file: Path) -> Any:
    try:
        import pdfplumber  # type: ignore

        return pdfplumber.open(file)
    except Exception:
        return None


def extract_layout_page(fitz_doc: Any, index: int, pypdf_page: Any) -> dict[str, Any]:
    if fitz_doc is not None:
        try:
            page = fitz_doc.load_page(index)
            raw_blocks = page.get_text("blocks", sort=True)
            blocks = []
            for raw in raw_blocks:
                if len(raw) < 7 or int(raw[6]) != 0:
                    continue
                text = clean_text(raw[4])
                if text:
                    blocks.append({"text": text, "bbox": [round(float(v), 2) for v in raw[:4]], "block_type": "text"})
            try:
                drawings = len(page.get_cdrawings()) if hasattr(page, "get_cdrawings") else len(page.get_drawings())
            except Exception:
                drawings = 0
            return {
                "text": "\n".join(block["text"] for block in blocks),
                "blocks": blocks,
                "width": float(page.rect.width),
                "height": float(page.rect.height),
                "image_count": len(page.get_images(full=True)),
                "drawing_count": drawings,
                "extractor": "pymupdf",
                "error": None,
            }
        except Exception as exc:
            fitz_error = str(exc)
    else:
        fitz_error = "pymupdf_unavailable"

    try:
        text = clean_text(pypdf_page.extract_text() or "")
        width = float(pypdf_page.mediabox.width)
        height = float(pypdf_page.mediabox.height)
        blocks = [{"text": text, "bbox": None, "block_type": "page_text"}] if text else []
        return {
            "text": text,
            "blocks": blocks,
            "width": width,
            "height": height,
            "image_count": 0,
            "drawing_count": 0,
            "extractor": "pypdf_fallback",
            "error": fitz_error,
        }
    except Exception as exc:
        return {
            "text": "",
            "blocks": [],
            "width": 0.0,
            "height": 0.0,
            "image_count": 0,
            "drawing_count": 0,
            "extractor": "none",
            "error": f"{fitz_error}; pypdf:{exc}",
        }


def classify_page(
    *,
    kind: str,
    page_no: int,
    page_count: int,
    text: str,
    text_chars: int,
    width: float,
    height: float,
    image_count: int,
    drawing_count: int,
) -> dict[str, Any]:
    orientation = "landscape" if width > height * 1.08 else "portrait"
    table_candidate = looks_like_table(text)
    # These engineering PDFs use landscape pages for both the report and the
    # drawings. Vector line count alone therefore misclassifies report tables
    # as drawings. Actual drawing sheets in the sample have almost no native
    # text and thousands of vector primitives, which is a much stronger signal.
    drawing_signal = kind in {"施工图", "勘察报告"} and text_chars < 200 and drawing_count >= 20
    scanned_signal = text_chars < 20 and image_count > 0 and drawing_count < 10

    if drawing_signal:
        page_type = "drawing"
    elif scanned_signal:
        page_type = "scanned"
    elif text_chars < 20 and image_count == 0 and drawing_count == 0:
        page_type = "blank"
    elif table_candidate:
        page_type = "table"
    elif image_count > 0 and text_chars < 300:
        page_type = "mixed"
    else:
        page_type = "native_text"

    needs_ocr = False
    ocr_region = None
    ocr_reason = None
    ocr_priority = 0
    if page_type == "scanned":
        needs_ocr = True
        ocr_region = "full_page"
        ocr_reason = "scanned_page_without_native_text"
        ocr_priority = 80 if kind == "规范" else 70
        processing_strategy = ["render_300dpi", "ocr_full_page", "layout_rebuild"]
    elif page_type == "drawing":
        needs_ocr = text_chars < 120
        ocr_region = "title_block" if needs_ocr else None
        ocr_reason = "drawing_native_text_insufficient" if needs_ocr else None
        ocr_priority = 60 if needs_ocr else 0
        processing_strategy = ["native_layout", "drawing_metadata", "ocr_title_block"] if needs_ocr else ["native_layout", "drawing_metadata"]
    elif page_type == "mixed" and text_chars < 120:
        needs_ocr = True
        ocr_region = "full_page"
        ocr_reason = "mixed_page_sparse_native_text"
        ocr_priority = 50
        processing_strategy = ["native_layout", "ocr_full_page", "merge_native_ocr"]
    elif page_type == "table":
        processing_strategy = ["native_layout", "table_extractor"]
    elif page_type == "blank":
        processing_strategy = ["skip_blank"]
    else:
        processing_strategy = ["native_layout", "section_parser"]

    return {
        "page_type": page_type,
        "orientation": orientation,
        "table_candidate": table_candidate,
        "needs_ocr": needs_ocr,
        "ocr_region": ocr_region,
        "ocr_reason": ocr_reason,
        "ocr_priority": ocr_priority,
        "processing_strategy": processing_strategy,
        "page_position": round(page_no / max(page_count, 1), 4),
    }


def looks_like_table(text: str) -> bool:
    compact = " ".join(text.split())
    keywords = ["一览表", "工程数量表", "控制点", "安全系数", "治理方案", "序号", "表 ", "表1", "表2", "表3", "表4", "表5", "表6"]
    keyword_score = sum(1 for word in keywords if word in compact)
    numeric_tokens = len(re.findall(r"\b\d+(?:\.\d+)?\b", compact))
    station_tokens = len(re.findall(r"K\s*\d+\s*\+\s*\d+", compact, re.IGNORECASE))
    return keyword_score >= 2 or station_tokens >= 3 or (keyword_score >= 1 and numeric_tokens >= 12)


def extract_tables(document: Any, page_index: int, doc_id: str, page_id: str, page_no: int) -> list[dict[str, Any]]:
    if document is None:
        return []
    try:
        page = document.pages[page_index]
        finder = page.find_tables()
        rows = []
        for index, table in enumerate(finder, start=1):
            data = [[clean_cell(cell) for cell in row] for row in table.extract()]
            data = [row for row in data if any(cell for cell in row)]
            if not data:
                continue
            rows.append(
                {
                    "id": f"{page_id}_table_{index:03d}",
                    "document_id": doc_id,
                    "page_id": page_id,
                    "page": page_no,
                    "bbox": [round(float(v), 2) for v in table.bbox],
                    "rows": data,
                    "row_count": len(data),
                    "column_count": max((len(row) for row in data), default=0),
                    "extractor": "pdfplumber",
                    "review_status": "pending",
                }
            )
        return rows
    except Exception as exc:
        return [
            {
                "id": f"{page_id}_table_error",
                "document_id": doc_id,
                "page_id": page_id,
                "page": page_no,
                "bbox": None,
                "rows": [],
                "row_count": 0,
                "column_count": 0,
                "extractor": "pdfplumber",
                "error": str(exc),
                "review_status": "failed",
            }
        ]


def skipped_page(page_id: str, doc_id: str, file_name: str, page_no: int) -> dict[str, Any]:
    return {
        "id": page_id,
        "document_id": doc_id,
        "file_name": file_name,
        "page": page_no,
        "text_chars": None,
        "text_sampled": False,
        "page_type": "not_parsed",
        "processing_strategy": ["skipped_by_limit"],
        "needs_ocr": False,
        "extraction_error": "skipped_by_sample_limit",
    }


def clean_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0B\x0C\x0E-\x1F]", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def clean_cell(value: Any) -> str:
    return " ".join(clean_text(value).split())


def doc_id_for(path: Path) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
    stem = "".join(ch if ch.isalnum() else "_" for ch in path.stem.lower()).strip("_")
    return f"doc_{stem[:28]}_{digest}"


def infer_doc_kind(name: str) -> str:
    if "施工图" in name:
        return "施工图"
    if "其他文件" in name or "勘察" in name:
        return "勘察报告"
    if "规范" in name or "标准" in name:
        return "规范"
    return "资料"
