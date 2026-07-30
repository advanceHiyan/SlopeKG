from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader

from .config import PATHS, DemoPaths
from .llm import call_deepseek_json, llm_status
from .ocr import call_paddle_ocr, make_paddle_ocr
from .storage import ensure_dir, read_json, write_json, write_jsonl


RULE_PROMPT_VERSION = "slope-rule-extraction-v1-evidence-bound"
RULE_KEYWORDS = (
    "稳定系数", "安全系数", "稳定状态", "危险性", "易发性", "发育程度", "危害程度",
    "风险等级", "预警等级", "报警等级", "判定", "分级", "工况", "计算公式",
)

RULE_SOURCE_PROFILES: list[dict[str, Any]] = [
    {
        "pattern": "2021地质灾害评估规范.pdf",
        "code": "GB/T 40112-2021",
        "title": "地质灾害危险性评估规范",
        "authority": "国家标准",
        "source_type": "official_standard",
        "priority": "primary",
        "target_pages": [*range(8, 13), *range(27, 33)],
        "topics": ["发育程度", "危害程度", "易发性", "危险性分级矩阵"],
    },
    {
        "pattern": "地质灾害危险性评估规范-国标.pdf",
        "code": "GB/T 40112-2021",
        "title": "地质灾害危险性评估规范（辅助文本版本）",
        "authority": "国家标准",
        "source_type": "duplicate_reference",
        "priority": "supporting",
        "target_pages": [],
        "topics": ["重复版本核对"],
    },
    {
        "pattern": "公路滑坡防治设计规范",
        "code": "JTG/T 3334-2018",
        "title": "公路滑坡防治设计规范",
        "authority": "交通运输部",
        "source_type": "official_standard",
        "priority": "primary",
        "target_pages": list(range(34, 41)),
        "topics": ["滑坡稳定性", "工程等级", "安全系数", "稳定性公式"],
    },
    {
        "pattern": "公路工程地质勘察规范",
        "code": "JTG C20-2011",
        "title": "公路工程地质勘察规范",
        "authority": "交通运输部",
        "source_type": "official_standard",
        "priority": "primary",
        "target_pages": list(range(30, 39)),
        "topics": ["高边坡", "滑坡", "崩塌", "风险输入要求"],
    },
    {
        "pattern": "公路边坡监测试点技术指南",
        "code": "公路边坡监测试点技术指南-2024",
        "title": "公路边坡监测试点技术指南",
        "authority": "交通运输部公路局",
        "source_type": "technical_guideline",
        "priority": "primary",
        "target_pages": list(range(30, 35)),
        "topics": ["预警等级", "稳定度等级", "预警响应"],
    },
    {
        "pattern": "公路技术状况评定标准",
        "code": "JTG 5210-2018",
        "title": "公路技术状况评定标准",
        "authority": "交通运输部",
        "source_type": "web_print_reference",
        "priority": "supporting",
        "target_pages": [11, 12],
        "topics": ["技术状况等级"],
    },
    {
        "pattern": "滑坡变形的普适性规律",
        "code": "RESEARCH-XUQIANG-LANDSLIDE-DEFORMATION",
        "title": "滑坡变形的普适性规律及其力学机制",
        "authority": "研究文献",
        "source_type": "research_reference",
        "priority": "supporting",
        "target_pages": list(range(1, 16)),
        "topics": ["位移阶段", "加速变形", "监测预警机理"],
    },
    {
        "pattern": "滑坡分类等.pdf",
        "code": "SLOPE-CLASSIFICATION-REFERENCE",
        "title": "地质灾害与公路边坡分类图表",
        "authority": "资料汇编",
        "source_type": "taxonomy_reference",
        "priority": "supporting",
        "target_pages": [],
        "topics": ["灾害分类", "边坡分类"],
    },
]

RULE_SYSTEM_PROMPT = """你是公路边坡规范条文、公式和风险规则结构化抽取器。
只能依据输入页面原文，不得补充常识，不得修改数值、比较符号、单位和适用条件。
输出严格JSON对象，字段为：
rules(array)、formulas(array)、threshold_tables(array)、notes(array)。
rules每项字段：title、rule_kind、hazard_type、scenario、inputs(array<string>)、
condition_text、output_text、exceptions(array<string>)、page(integer)、evidence_quote。
formulas每项字段：name、expression_text、latex、variables(array<object>)、
applicability、page(integer)、evidence_quote；variables每项含symbol、meaning、unit。
threshold_tables每项字段：title、dimensions(array<string>)、rows(array<object>)、
page(integer)、evidence_quote。
evidence_quote必须逐字摘自对应页面OCR原文，长度8至300字。无法可靠识别的公式不要猜测，
放入notes说明。所有结果均为候选，不代表规则已经批准。"""


class RuleExtractionPipeline:
    def __init__(self, paths: DemoPaths = PATHS) -> None:
        self.paths = paths

    def run(
        self,
        *,
        use_llm: bool = True,
        llm_model: str = "deepseek-v4-flash",
        force_ocr: bool = False,
        progress_callback: Callable[[int, str, str], None] | None = None,
    ) -> dict[str, Any]:
        def progress(percent: int, stage: str, message: str) -> None:
            if progress_callback:
                progress_callback(percent, stage, message)

        progress(1, "cataloging", "正在递归登记规范、指南、论文和工程应用资料")
        ensure_dir(self.paths.rules_dir)
        ensure_dir(self.paths.rules_dir / "pages")
        documents = self.catalog_documents()
        cached_pages = {
            (row.get("document_id"), int(row.get("page", 0)), row.get("source_sha256")): row
            for row in read_json(self.paths.rules_dir / "page_text.json", [])
        }
        selected_total = sum(len(row.get("target_pages", [])) for row in documents)
        processed = 0
        page_rows: list[dict[str, Any]] = []
        ocr_engine: Any = None
        for document in documents:
            pdf = self.paths.root / document["path"]
            native_pages = extract_native_candidate_pages(pdf, document)
            target_pages = sorted(set(document.get("target_pages", [])) | set(native_pages))
            document["selected_pages"] = target_pages
            document["selected_page_count"] = len(target_pages)
            for page_no in target_pages:
                cache_key = (document["id"], page_no, document["source_sha256"])
                cached = cached_pages.get(cache_key)
                if cached and not force_ocr:
                    row = cached
                else:
                    row, ocr_engine = extract_rule_page(
                        pdf,
                        document,
                        page_no,
                        self.paths.rules_dir / "pages",
                        root=self.paths.root,
                        ocr_engine=ocr_engine,
                    )
                row = {
                    **row,
                    "source_type": document["source_type"],
                    "document_priority": document["priority"],
                    "authority": document["authority"],
                }
                page_rows.append(row)
                processed += 1
                progress(
                    5 + round(50 * processed / max(selected_total, processed, 1)),
                    "ocr",
                    f"规则页识别 {document['code']} 第{page_no}页（{processed}/{max(selected_total, processed)}）",
                )
        write_json(self.paths.rules_dir / "page_text.json", page_rows)
        write_jsonl(self.paths.rules_dir / "page_text.jsonl", page_rows)

        progress(58, "structuring", "正在切分条文、识别阈值片段和公式候选")
        clauses = extract_clauses(page_rows)
        deterministic_rules = extract_deterministic_rule_candidates(page_rows)
        formulas = extract_formula_candidates(page_rows)
        llm_rules: list[dict[str, Any]] = []
        llm_formulas: list[dict[str, Any]] = []
        threshold_tables: list[dict[str, Any]] = []
        llm_runs: list[dict[str, Any]] = []
        llm_cache_path = self.paths.rules_dir / "llm_cache.json"
        llm_cache = read_json(llm_cache_path, {})
        if use_llm:
            primary_docs = [row for row in documents if row.get("priority") == "primary"]
            for index, document in enumerate(primary_docs, start=1):
                samples = [row for row in page_rows if row["document_id"] == document["id"] and row.get("text")]
                if not samples:
                    continue
                progress(
                    60 + round(25 * (index - 1) / max(len(primary_docs), 1)),
                    "semantic",
                    f"正在结构化 {document['code']} 的规则、公式和阈值表",
                )
                cache_key = semantic_cache_key(document, samples, llm_model)
                cached_result = llm_cache.get(cache_key)
                if isinstance(cached_result, dict) and isinstance(cached_result.get("payload"), dict):
                    payload = cached_result["payload"]
                    metadata = {**cached_result.get("metadata", {}), "cache_hit": True}
                else:
                    payload, metadata = extract_with_llm(document, samples, model=llm_model)
                    metadata = {**metadata, "cache_hit": False}
                    llm_cache[cache_key] = {"payload": payload, "metadata": metadata}
                    write_json(llm_cache_path, llm_cache)
                validated = validate_llm_payload(document, samples, payload)
                llm_rules.extend(validated["rules"])
                llm_formulas.extend(validated["formulas"])
                threshold_tables.extend(validated["threshold_tables"])
                llm_runs.append(
                    {
                        "document_id": document["id"],
                        "model": metadata.get("model", llm_model),
                        "cache_hit": metadata.get("cache_hit", False),
                        "usage": metadata.get("usage", {}),
                        "accepted_rules": len(validated["rules"]),
                        "accepted_formulas": len(validated["formulas"]),
                        "accepted_tables": len(validated["threshold_tables"]),
                        "rejected": validated["rejected"],
                    }
                )
        table_rules = derive_table_rules(threshold_tables)
        rules = [
            classify_rule(row)
            for row in deduplicate_rules([*deterministic_rules, *llm_rules, *table_rules])
        ]
        formulas = deduplicate_formulas([*formulas, *llm_formulas])
        library = {
            "schema_name": "slope_rule_library",
            "schema_version": "1.0.0-candidate",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "publication_status": "candidate_not_approved",
            "execution_enabled": False,
            "documents": documents,
            "clauses": clauses,
            "formulas": formulas,
            "threshold_tables": threshold_tables,
            "rules": rules,
            "llm_runs": llm_runs,
            "stats": {
                "documents": len(documents),
                "selected_pages": len(page_rows),
                "native_pages": sum(1 for row in page_rows if row.get("extractor") == "pymupdf_native"),
                "ocr_pages": sum(1 for row in page_rows if row.get("extractor") == "PaddleOCR"),
                "clauses": len(clauses),
                "formulas": len(formulas),
                "threshold_tables": len(threshold_tables),
                "rules": len(rules),
                "risk_engine_candidates": sum(bool(row.get("risk_engine_candidate")) for row in rules),
                "table_row_rules": len(table_rules),
                "approved_rules": 0,
                "evidence_validated_rules": sum(1 for row in rules if row.get("evidence_validation") == "passed"),
                "partial_evidence_rules": sum(1 for row in rules if row.get("evidence_validation") == "partial_fuzzy"),
            },
        }
        progress(91, "writing", "正在写入规则库、公式库、证据链和质量报告")
        write_json(self.paths.rules_json, library)
        write_jsonl(self.paths.rules_dir / "documents.jsonl", documents)
        write_jsonl(self.paths.rules_dir / "clauses.jsonl", clauses)
        write_jsonl(self.paths.rules_dir / "formulas.jsonl", formulas)
        write_jsonl(self.paths.rules_dir / "threshold_tables.jsonl", threshold_tables)
        write_jsonl(self.paths.rules_dir / "rules.jsonl", rules)
        state = {
            "updated_at": library["generated_at"],
            "status": "completed",
            "publication_status": library["publication_status"],
            "execution_enabled": False,
            "stats": library["stats"],
            "llm": {**llm_status(), "enabled": use_llm, "model": llm_model, "runs": llm_runs},
            "quality": rule_quality_report(library),
            "outputs": {
                "library": str(self.paths.rules_json.relative_to(self.paths.root)),
                "rules_dir": str(self.paths.rules_dir.relative_to(self.paths.root)),
            },
        }
        write_json(self.paths.rules_state_json, state)
        progress(100, "completed", f"候选规则库已生成：{len(rules)}条规则、{len(formulas)}个公式")
        return state

    def catalog_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        seen: set[Path] = set()
        for profile in RULE_SOURCE_PROFILES:
            matches = [
                path for path in self.paths.raw_dir.rglob("*.pdf")
                if profile["pattern"].lower() in path.name.lower()
            ]
            for pdf in matches:
                if pdf in seen:
                    continue
                seen.add(pdf)
                documents.append(document_record(pdf, self.paths, profile))
        for pdf in sorted(self.paths.raw_dir.glob("G*.pdf")):
            if pdf in seen:
                continue
            profile = {
                "code": f"CASE-{pdf.stem.split('-')[0]}",
                "title": pdf.stem,
                "authority": "工程项目资料",
                "source_type": "engineering_application",
                "priority": "application",
                "target_pages": [],
                "topics": ["稳定性计算应用", "工程安全系数", "工况评价"],
            }
            documents.append(document_record(pdf, self.paths, profile))
        documents.sort(key=lambda row: (priority_order(row["priority"]), row["code"], row["file_name"]))
        return documents


def document_record(pdf: Path, paths: DemoPaths, profile: dict[str, Any]) -> dict[str, Any]:
    source_hash = sha256_file(pdf)
    reader = PdfReader(str(pdf))
    doc_id = f"rule_doc_{hashlib.sha1(str(pdf.relative_to(paths.root)).encode('utf-8')).hexdigest()[:12]}"
    return {
        "id": doc_id,
        "code": profile["code"],
        "title": profile["title"],
        "authority": profile["authority"],
        "source_type": profile["source_type"],
        "priority": profile["priority"],
        "topics": profile.get("topics", []),
        "file_name": pdf.name,
        "path": str(pdf.relative_to(paths.root)),
        "pages": len(reader.pages),
        "target_pages": [page for page in profile.get("target_pages", []) if page <= len(reader.pages)],
        "source_sha256": source_hash,
        "review_status": "source_cataloged",
    }


def extract_native_candidate_pages(pdf: Path, document: dict[str, Any]) -> list[int]:
    if document["source_type"] not in {"engineering_application", "research_reference"}:
        return []
    import fitz  # type: ignore

    selected: list[tuple[int, int]] = []
    fitz_doc = fitz.open(pdf)
    try:
        for index, page in enumerate(fitz_doc):
            text = page.get_text("text") or ""
            score = sum(text.count(keyword) for keyword in RULE_KEYWORDS)
            if score:
                selected.append((score, index + 1))
    finally:
        fitz_doc.close()
    selected.sort(key=lambda row: (-row[0], row[1]))
    limit = 12 if document["source_type"] == "engineering_application" else 15
    return sorted(page for _, page in selected[:limit])


def extract_rule_page(
    pdf: Path,
    document: dict[str, Any],
    page_no: int,
    image_dir: Path,
    *,
    root: Path = PATHS.root,
    ocr_engine: Any,
) -> tuple[dict[str, Any], Any]:
    import fitz  # type: ignore

    fitz_doc = fitz.open(pdf)
    try:
        page = fitz_doc.load_page(page_no - 1)
        native = clean_text(page.get_text("text") or "")
        compact = "".join(native.split())
        use_native = len(compact) >= 120 and mojibake_ratio(native) < 0.08
        image_name = f"{document['id']}_p{page_no:04d}.png"
        image_path = image_dir / image_name
        if not image_path.exists():
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2), alpha=False)
            pixmap.save(image_path)
    finally:
        fitz_doc.close()
    if use_native:
        text = native
        extractor = "pymupdf_native"
        confidence = 1.0
    else:
        if ocr_engine is None:
            ocr_engine = make_paddle_ocr()
        raw = call_paddle_ocr(ocr_engine, image_path)
        items = normalize_ocr_text(raw)
        text = "\n".join(row["text"] for row in items)
        extractor = "PaddleOCR"
        scores = [row["confidence"] for row in items if row["confidence"] is not None]
        confidence = round(sum(scores) / len(scores), 4) if scores else None
    return {
        "id": f"{document['id']}_p{page_no:04d}",
        "document_id": document["id"],
        "document_code": document["code"],
        "page": page_no,
        "text": text,
        "extractor": extractor,
        "confidence": confidence,
        "image_path": str(image_path.relative_to(root)),
        "source_path": document["path"],
        "source_sha256": document["source_sha256"],
        "text_chars": len("".join(text.split())),
        "review_status": "automatic_extraction_pending_review",
    }, ocr_engine


def normalize_ocr_text(raw: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not raw:
        return rows
    mappings = raw if isinstance(raw, list) else [raw]
    for item in mappings:
        if isinstance(item, dict) or hasattr(item, "get"):
            texts = item.get("rec_texts") or item.get("texts") or []
            scores = item.get("rec_scores") or item.get("scores") or []
            for index, text in enumerate(texts):
                score = scores[index] if index < len(scores) else None
                rows.append({"text": str(text), "confidence": float(score) if score is not None else None})
            continue
        if isinstance(item, list):
            for nested in item:
                if isinstance(nested, dict) or hasattr(nested, "get"):
                    rows.extend(normalize_ocr_text(nested))
                elif isinstance(nested, list) and len(nested) == 2:
                    try:
                        text, score = nested[1]
                        rows.append({"text": str(text), "confidence": float(score)})
                    except Exception:
                        continue
    return rows


def extract_clauses(page_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    heading = re.compile(r"(?m)^(?P<number>\d+(?:\.\d+){1,3})\s+(?P<title>[^\n]{2,80})$")
    for page in page_rows:
        if page.get("source_type") not in {"official_standard", "technical_guideline", "web_print_reference"}:
            continue
        text = page.get("text", "")
        matches = list(heading.finditer(text))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.start():end].strip()
            clauses.append(
                {
                    "id": f"clause_{page['document_id']}_{page['page']:04d}_{index+1:02d}",
                    "document_id": page["document_id"],
                    "document_code": page["document_code"],
                    "clause_no": match.group("number"),
                    "title": match.group("title").strip(),
                    "text": body,
                    "page": page["page"],
                    "evidence_page_id": page["id"],
                    "review_status": "automatic_candidate",
                }
            )
    return clauses


def extract_deterministic_rule_candidates(page_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    operators = re.compile(r"(?:F[sS]?|K|稳定系数|安全系数).{0,30}(?:≤|≥|＜|＞|<|>|=).{0,30}\d")
    for page in page_rows:
        if page.get("source_type") not in {"official_standard", "technical_guideline", "web_print_reference"}:
            continue
        lines = [line.strip() for line in page.get("text", "").splitlines() if line.strip()]
        for index, line in enumerate(lines):
            window = " ".join(lines[max(0, index - 1): min(len(lines), index + 2)])
            if not operators.search(line):
                continue
            rules.append(
                {
                    "id": f"rule_det_{hashlib.sha1((page['id']+window).encode('utf-8')).hexdigest()[:14]}",
                    "title": infer_rule_title(window),
                    "rule_kind": "threshold_candidate",
                    "hazard_type": infer_hazard_type(window),
                    "scenario": infer_scenario(window),
                    "inputs": infer_inputs(window),
                    "condition_text": window,
                    "output_text": infer_output(window),
                    "exceptions": [],
                    "document_id": page["document_id"],
                    "document_code": page["document_code"],
                    "page": page["page"],
                    "evidence_quote": window,
                    "evidence_page_id": page["id"],
                    "extraction_method": "deterministic_threshold_pattern",
                    "evidence_validation": "passed",
                    "approval_status": "candidate_pending_review",
                    "execution_enabled": False,
                }
            )
    return rules


def extract_formula_candidates(page_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formulas: list[dict[str, Any]] = []
    for page in page_rows:
        if page.get("source_type") != "official_standard":
            continue
        lines = [line.strip() for line in page.get("text", "").splitlines() if line.strip()]
        for line in lines:
            if "=" not in line or not re.search(r"[A-Za-zα-ωΑ-Ω]", line):
                continue
            if len(line) > 240 or len(re.findall(r"[A-Za-zα-ωΑ-Ω]", line)) < 2:
                continue
            if not re.search(r"[+\-*/∑()]", line):
                continue
            formulas.append(
                {
                    "id": f"formula_det_{hashlib.sha1((page['id']+line).encode('utf-8')).hexdigest()[:14]}",
                    "name": "公式候选",
                    "expression_text": line,
                    "latex": None,
                    "variables": [],
                    "applicability": None,
                    "document_id": page["document_id"],
                    "document_code": page["document_code"],
                    "page": page["page"],
                    "evidence_quote": line,
                    "evidence_page_id": page["id"],
                    "extraction_method": "deterministic_formula_line",
                    "evidence_validation": "passed",
                    "approval_status": "candidate_pending_formula_review",
                    "execution_enabled": False,
                }
            )
    return formulas


def extract_with_llm(
    document: dict[str, Any],
    pages: list[dict[str, Any]],
    *,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = "\n\n".join(f"[PDF第{row['page']}页]\n{row['text']}" for row in pages)
    prompt = (
        f"文档：{document['code']} {document['title']}\n"
        f"来源类型：{document['source_type']}\n"
        f"主题：{json.dumps(document.get('topics', []), ensure_ascii=False)}\n\n"
        f"页面原文：\n{source[:120000]}"
    )
    return call_deepseek_json(system_prompt=RULE_SYSTEM_PROMPT, user_prompt=prompt, model=model)


def validate_llm_payload(
    document: dict[str, Any],
    pages: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    by_page = {int(row["page"]): row for row in pages}
    result: dict[str, Any] = {"rules": [], "formulas": [], "threshold_tables": [], "rejected": 0}
    for kind in ("rules", "formulas", "threshold_tables"):
        for item in payload.get(kind, []) if isinstance(payload.get(kind), list) else []:
            evidence_validation = "passed"
            page_no = safe_int(item.get("page"))
            page = by_page.get(page_no)
            quote = clean_text(str(item.get("evidence_quote") or ""))
            if not page or len(quote) < 8 or normalized_quote(quote) not in normalized_quote(page["text"]):
                result["rejected"] += 1
                continue
            if kind == "formulas":
                expression = clean_text(str(item.get("expression_text") or ""))
                if len(normalized_quote(expression)) < 4 or normalized_quote(expression) not in normalized_quote(page["text"]):
                    result["rejected"] += 1
                    continue
            if kind == "threshold_tables":
                table_status = table_support_status(item, page["text"])
                if table_status is None:
                    result["rejected"] += 1
                    continue
                evidence_validation = table_status
            base = {
                **item,
                "document_id": document["id"],
                "document_code": document["code"],
                "page": page_no,
                "evidence_page_id": page["id"],
                "extraction_method": "deepseek_structured_candidate",
                "prompt_version": RULE_PROMPT_VERSION,
                "evidence_validation": evidence_validation,
                "approval_status": "candidate_pending_review",
                "execution_enabled": False,
            }
            prefix = {"rules": "rule_llm", "formulas": "formula_llm", "threshold_tables": "table_llm"}[kind]
            base["id"] = f"{prefix}_{hashlib.sha1((document['id']+str(page_no)+quote).encode('utf-8')).hexdigest()[:14]}"
            result[kind].append(base)
    return result


def deduplicate_rules(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if row.get("extraction_method") == "validated_threshold_table_row":
            key = (
                row.get("threshold_table_id"),
                normalized_quote(row.get("condition_text", "")),
                normalized_quote(row.get("output_text", "")),
            )
        else:
            key = (row.get("document_id"), row.get("page"), normalized_quote(row.get("evidence_quote", ""))[:160])
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def deduplicate_formulas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        expression = normalized_quote(str(row.get("expression_text") or row.get("evidence_quote") or ""))
        key = (row.get("document_id"), row.get("page"), expression[:160])
        if not expression or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def derive_table_rules(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    relevant = re.compile(r"危险性|稳定状态|安全系数|预警|发育程度")
    output_names = ("危险性等级", "滑坡稳定状态", "评估级别", "稳定安全系数K", "预警等级", "发育程度")
    for table in tables:
        title = str(table.get("title") or "")
        if not relevant.search(title):
            continue
        rows = table.get("rows", [])
        if not isinstance(rows, list):
            continue
        for index, values in enumerate(rows, start=1):
            if not isinstance(values, dict) or len(values) < 2:
                continue
            output_key = next((name for name in output_names if name in values), None)
            if output_key is None:
                output_key = next((key for key in values if any(term in key for term in ("等级", "状态", "系数"))), None)
            if output_key is None:
                continue
            output_value = values.get(output_key)
            conditions = {key: value for key, value in values.items() if key != output_key}
            if output_value in (None, "") or not conditions:
                continue
            condition_text = "；".join(f"{key}={value}" for key, value in conditions.items())
            evidence = str(table.get("evidence_quote") or "")
            rule_id_seed = f"{table.get('id')}|{index}|{json.dumps(values, ensure_ascii=False, sort_keys=True)}"
            rules.append(
                {
                    "id": f"rule_table_{hashlib.sha1(rule_id_seed.encode('utf-8')).hexdigest()[:14]}",
                    "title": f"{title}：{output_value}",
                    "rule_kind": "threshold_table_row",
                    "hazard_type": infer_hazard_type(title),
                    "scenario": title,
                    "inputs": list(conditions),
                    "input_dimensions": conditions,
                    "condition_text": condition_text,
                    "output_text": f"{output_key}={output_value}",
                    "exceptions": [],
                    "document_id": table["document_id"],
                    "document_code": table["document_code"],
                    "page": table["page"],
                    "evidence_quote": evidence,
                    "evidence_page_id": table["evidence_page_id"],
                    "threshold_table_id": table["id"],
                    "extraction_method": "validated_threshold_table_row",
                    "evidence_validation": table.get("evidence_validation", "passed"),
                    "approval_status": "candidate_pending_review",
                    "execution_enabled": False,
                }
            )
    return rules


def rule_quality_report(library: dict[str, Any]) -> dict[str, Any]:
    rules = library.get("rules", [])
    formulas = library.get("formulas", [])
    documents = library.get("documents", [])
    return {
        "source_catalog_coverage": 1.0 if documents else 0.0,
        "rule_evidence_coverage": ratio(sum(row.get("evidence_validation") == "passed" for row in rules), len(rules)),
        "formula_evidence_coverage": ratio(sum(row.get("evidence_validation") == "passed" for row in formulas), len(formulas)),
        "partial_evidence_rule_count": sum(row.get("evidence_validation") == "partial_fuzzy" for row in rules),
        "approved_rule_count": sum(row.get("approval_status") == "approved" for row in rules),
        "executable_rule_count": sum(bool(row.get("execution_enabled")) for row in rules),
        "risk_engine_candidate_count": sum(bool(row.get("risk_engine_candidate")) for row in rules),
        "limitations": [
            "候选规则尚未经过交通部研究院审核，不参与正式风险计算。",
            "公式必须逐符号核验，当前仅作为公式候选展示。",
            "规范版本有效性和引用关系仍需建立审核记录。",
        ],
    }


def infer_rule_title(text: str) -> str:
    if "预警" in text:
        return "预警等级阈值候选"
    if "滑坡" in text:
        return "滑坡稳定状态阈值候选"
    if "崩塌" in text or "危岩" in text:
        return "崩塌稳定状态阈值候选"
    return "稳定性阈值候选"


def infer_hazard_type(text: str) -> str | None:
    for term in ("滑坡", "崩塌", "泥石流", "落石"):
        if term in text:
            return term
    return None


def infer_scenario(text: str) -> str | None:
    for term in ("天然工况", "正常工况", "暴雨工况", "饱和", "地震工况", "校核工况"):
        if term in text:
            return term
    return None


def infer_inputs(text: str) -> list[str]:
    inputs = []
    if re.search(r"F[sS]?|稳定系数", text):
        inputs.append("stability_factor")
    if "安全系数" in text or "Fst" in text:
        inputs.append("required_safety_factor")
    if "预警" in text:
        inputs.append("monitoring_indicator")
    return inputs


def infer_output(text: str) -> str | None:
    for term in ("不稳定", "欠稳定", "基本稳定", "红色预警", "橙色预警", "黄色预警", "蓝色预警"):
        if term in text:
            return term
    if re.search(r"(?:为|属于|判定为)\s*稳定(?:状态|等级)?", text):
        return "稳定"
    return None


def classify_rule(row: dict[str, Any]) -> dict[str, Any]:
    kind = str(row.get("rule_kind") or "")
    current_system = not re.search(
        r"工业与民用|房屋建筑|隧道|桥梁|输油气|管道|阀室|场站|储油|坝址|水库|码头|船坞",
        str(row.get("title") or row.get("scenario") or ""),
    )
    if kind in {"definition", "requirement"}:
        scope = "data_validation"
    elif kind in {"procedure", "response"}:
        scope = "monitoring_workflow"
    elif kind == "parameter":
        scope = "engineering_parameter"
    else:
        scope = "risk_assessment"
    return {
        **row,
        "rule_scope": scope,
        "system_applicability": "current_highway_slope" if current_system else "reference_other_engineering",
        "risk_engine_candidate": current_system and kind in {
            "classification", "threshold", "threshold_candidate", "threshold_table_row",
            "trigger", "adjustment", "parameter",
        },
    }


def table_support_status(item: dict[str, Any], source_text: str) -> str | None:
    from rapidfuzz.fuzz import partial_ratio

    source = normalized_quote(source_text)
    values: list[str] = []
    for row in item.get("rows", []) if isinstance(item.get("rows"), list) else []:
        if not isinstance(row, dict):
            continue
        for value in row.values():
            if isinstance(value, (str, int, float)) and len(normalized_quote(str(value))) >= 2:
                values.append(normalized_quote(str(value)))
    if not values:
        return None
    exact = sum(value in source for value in values)
    if exact / len(values) >= 0.8:
        return "passed"
    scores = [100.0 if value in source else partial_ratio(value, source) for value in values]
    fuzzy_supported = sum(score >= 80 for score in scores)
    if sum(scores) / len(scores) >= 85 and fuzzy_supported / len(scores) >= 0.75:
        return "partial_fuzzy"
    return None


def semantic_cache_key(document: dict[str, Any], pages: list[dict[str, Any]], model: str) -> str:
    payload = {
        "prompt_version": RULE_PROMPT_VERSION,
        "model": model,
        "document_id": document["id"],
        "source_sha256": document["source_sha256"],
        "pages": [(row["page"], row.get("text", "")) for row in pages],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\r", "\n")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines() if line.strip())


def normalized_quote(value: str) -> str:
    return re.sub(r"\s+", "", clean_text(str(value))).replace("＜", "<").replace("＞", ">")


def mojibake_ratio(value: str) -> float:
    if not value:
        return 1.0
    suspicious = sum(1 for char in value if (ord(char) < 32 and char not in "\n\t") or char in "犐犆犛狆犲犮犻")
    return suspicious / max(len(value), 1)


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def priority_order(value: str) -> int:
    return {"primary": 0, "supporting": 1, "application": 2}.get(value, 9)
