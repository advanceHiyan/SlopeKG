"""Bounded, resumable layout -> model segmentation -> reading -> review -> audit."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Callable
import unicodedata

from .model_client import JsonVisionModel, ModelError, content_hash
from .storage import read_json, write_json, write_jsonl
from .visual_contract import (VERSION, SYSTEM, LAYOUT, SEGMENT, READ, VERIFY, AUDIT,
                              FACT_KEYS, ContractError, bbox, inside,
                              page_card, reading, resolve_conflicts, consolidate, rows, text)
from .visual_document import VisualDocument


@dataclass(frozen=True)
class VisualSettings:
    overview_edge: int = 1600
    detail_edge: int = 3000
    reread_edge: int = 4500
    window_pages: int = 4
    max_calls: int = 200
    max_prompt_chars: int = 24000
    max_images: int = 6
    max_image_pixels: int = 24000000
    max_pixels_per_image: int = 12000000
    max_image_bytes: int = 30000000
    max_regions_per_page: int = 64
    max_rereads_per_region: int = 2
    audit_rounds: int = 2
    max_audit_regions: int = 8
    use_native_text: bool = True
    use_existing_ocr: bool = True
    retry_failed: bool = False

    def __post_init__(self):
        for key in ("overview_edge", "detail_edge", "reread_edge"):
            if not 256 <= getattr(self, key) <= 10000:
                raise ValueError("Render edges must be 256..10000")
        if not (1 <= self.window_pages <= 16 and 1 <= self.max_calls <= 100000
                and 8000 <= self.max_prompt_chars <= 1000000 and 2 <= self.max_images <= 32
                and self.max_image_pixels >= 1000000 and self.max_pixels_per_image >= 65536 and self.max_image_bytes >= 100000
                and 1 <= self.max_regions_per_page <= 1024 and 0 <= self.max_rereads_per_region <= 8
                and 1 <= self.audit_rounds <= 4 and 1 <= self.max_audit_regions <= 64):
            raise ValueError("Invalid visual pipeline budget")


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def normalized(value: str) -> str:
    # NFKC would collapse superscript units; preserve m² versus m³.
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", value))


def literal_supported(value: str, quote: str, *, unit: bool = False) -> bool:
    """Literal support with token boundaries: 4.5 != 4.51, m != mm or m².

    This is a generic lexical guard, not a unit conversion or semantic judge.
    """
    value = normalized(value)
    quote = unicodedata.normalize("NFC", quote) if unit else normalized(quote)
    if not value:
        return True
    pattern = r"\s*".join(re.escape(c) for c in value) if unit else re.escape(value)
    for match in re.finditer(pattern, quote):
        before = quote[match.start() - 1] if match.start() else ""
        after = quote[match.end()] if match.end() < len(quote) else ""
        if unit:
            def unit_part(char):
                return bool(char) and ((char.isascii() and char.isalpha()) or char.isdigit() or char in "/^⁻⁺")
            # A numeric value directly before a unit is expected. Prefix letters
            # and following powers/compound units change the unit's meaning.
            if (before and before.isascii() and before.isalpha()) or before in ("/", "^") or unit_part(after):
                continue
            following = quote[match.end():].lstrip()
            if following.startswith(("/", "^", "⁻", "⁺")):
                continue
        else:
            if value[0].isdigit() and before and (before.isdigit() or before in ".−-"):
                continue
            if value[-1].isdigit() and after and (after.isdigit() or (after == "." and match.end() + 1 < len(quote)
                                                                                    and quote[match.end() + 1].isdigit())):
                continue
        return True
    return False


def pack(units: list, budget: int) -> list[list]:
    """Partition all units; never silently truncate a tail to meet a budget."""
    batches, current, size = [], [], 2
    for unit in units:
        cost = len(compact(unit)) + 1
        if cost + 2 > budget:
            raise ContractError("One structured item exceeds prompt budget")
        if current and size + cost > budget:
            batches.append(current)
            current, size = [], 2
        current.append(unit)
        size += cost
    if current:
        batches.append(current)
    return batches


class VisualPipeline:
    def __init__(self, model: JsonVisionModel, output_dir: Path, *, settings: VisualSettings | None = None,
                 ocr_evidence: Callable | None = None, review_model: JsonVisionModel | None = None):
        self.model, self.output_dir = model, output_dir.resolve()
        self.review_model = review_model or model
        self.settings = settings or VisualSettings()
        self.ocr_evidence = ocr_evidence
        self.calls = self.cache_hits = 0
        self.receipts: list[dict] = []

    def _ask(self, stage: str, instruction: str, context: dict, images: list, validate: Callable) -> dict:
        model = self.review_model if stage in {"verify", "audit"} else self.model
        prompt = instruction + "\n输入：" + compact(context)
        if len(prompt) + len(SYSTEM) > self.settings.max_prompt_chars:
            raise ContractError("Prompt budget exceeded; request was not sent")
        if (len(images) > self.settings.max_images
                or sum(i.width * i.height for i in images) > self.settings.max_image_pixels
                or sum(i.path.stat().st_size for i in images) > self.settings.max_image_bytes):
            raise ContractError("Image budget exceeded; request was not sent")
        identity = {"version": VERSION, "stage": stage, "model": model.identity(),
                    "system": SYSTEM, "prompt": prompt,
                    "images": [{"label": i.label, "sha256": hashlib.sha256(i.path.read_bytes()).hexdigest()}
                               for i in images]}
        key = content_hash(identity)
        path = self.output_dir / "cache" / (key + ".json")
        cached = read_json(path, {})
        if cached.get("status") == "completed":
            answer = validate(cached["answer"])
            self.cache_hits += 1
            self.receipts.append({"stage": stage, "cache_key": key, "cache_hit": True, **cached["receipt"]})
            return answer
        if cached and not self.settings.retry_failed:
            raise ModelError("Previous failed/interrupted call requires explicit retry_failed; not charged again")
        if self.calls >= self.settings.max_calls:
            raise ModelError("Run call budget exhausted; unfinished work retained")
        self.calls += 1
        previous_attempts = cached.get("attempts", [])
        marker = {"status": "started", "stage": stage, "cache_key": key,
                  "attempts": previous_attempts + [{"status": "started"}]}
        write_json(path, marker)  # Persist before transmitting a potentially billable request.
        raw, receipt = None, None
        try:
            raw, receipt = model.complete(system=SYSTEM, prompt=prompt, images=images)
            receipt = {**receipt, "requested_model_profile": model.identity()}
            answer = validate(raw)
        except Exception as exc:
            # Runtime errors from injected clients can contain secrets; persist types only.
            safe = str(exc) if isinstance(exc, (ModelError, ContractError)) else type(exc).__name__
            marker.update(status="failed", error=safe)
            marker["attempts"][-1] = {"status": "failed", "error": safe}
            receipt = receipt or getattr(exc, "receipt", None)
            if receipt:
                marker["receipt"] = receipt
                marker["attempts"][-1]["receipt"] = receipt
                self.receipts.append({"stage": stage, "cache_key": key, "status": "failed", "cache_hit": False, **receipt})
            if raw is not None:
                marker["invalid_answer"] = raw
            write_json(path, marker)
            raise ModelError(f"{stage}: {safe}") from None
        marker["attempts"][-1] = {"status": "completed"}
        write_json(path, {**marker, "status": "completed", "answer": answer, "receipt": receipt})
        self.receipts.append({"stage": stage, "cache_key": key, "cache_hit": False, **receipt})
        return answer

    def run(self, pdfs: list[Path], *, prepare_only: bool = False) -> dict:
        from .pipeline import pipeline_run_guard
        from .provenance import runtime_identity
        if not pdfs:
            raise ValueError("Explicit input PDFs required")
        sources = [p.resolve(strict=True) for p in pdfs]
        if len(set(sources)) != len(sources) or any(p.suffix.lower() != ".pdf" for p in sources):
            raise ValueError("Duplicate or non-PDF input")
        if any(self.output_dir == p.parent or self.output_dir in p.parents for p in sources):
            raise ValueError("Output directory must not contain input PDFs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with pipeline_run_guard(self.output_dir / ".visual.lock"):
            self.calls = self.cache_hits = 0
            self.receipts = []
            result = {"schema_version": VERSION, "recognizer": "multimodal", "producer": runtime_identity(),
                      "model": self.model.identity(), "review_model": self.review_model.identity(),
                      "settings": asdict(self.settings), "documents": [],
                      "prepare_only": prepare_only, "precision": None, "recall": None,
                      "requested_inputs": [str(s) for s in sources],
                      "metric_note": "Model verification/region coverage are not ground-truth precision or recall."}
            for source in sources:
                result.update(processing_status="running", current_input=str(source))
                write_json(self.output_dir / "run.json", result)
                try:
                    report = self._document(source, prepare_only)
                except Exception as exc:
                    report = {"path": str(source), "status": "failed", "error": type(exc).__name__,
                              "pages": [], "facts": []}
                result["documents"].append(report)
            result.pop("current_input", None)
            result = self._finish(result)
            facts = [f for d in result["documents"] for f in d.get("facts", [])]
            write_jsonl(self.output_dir / "facts.jsonl", facts)
            write_jsonl(self.output_dir / "verified_facts.jsonl", [f for f in facts if f["status"] == "verified_model"])
            write_jsonl(self.output_dir / "review_queue.jsonl", [f for f in facts if f["status"] != "verified_model"])
            write_json(self.output_dir / "coverage.json", [{"document_id": d.get("id"), "status": d["status"],
                "coverage": d.get("coverage"), "pages": [{k: p.get(k) for k in ("page", "status", "errors", "audit", "unread_regions")}
                                                       for p in d["pages"]]} for d in result["documents"]])
            write_json(self.output_dir / "run.json", result)
            return result

    def _finish(self, result: dict) -> dict:
        docs = result["documents"]
        states = [d["status"] for d in docs]
        result["processing_status"] = ("prepared" if result["prepare_only"] and all(s == "prepared" for s in states)
                                       else "completed" if all(s == "completed" for s in states)
                                       else "failed" if all(s == "failed" for s in states) else "partial")
        facts = [f for d in docs for f in d.get("facts", [])]
        result["summary"] = {"documents": len(docs), "pages": sum(len(d["pages"]) for d in docs),
                             "facts": len(facts), "verified_model": sum(f["status"] == "verified_model" for f in facts),
                             "pending": sum(f["status"] == "pending" for f in facts),
                             "rejected": sum(f["status"] == "rejected" for f in facts),
                             "calls": self.calls, "cache_hits": self.cache_hits}
        result["receipts"] = list(self.receipts)
        result["environment_variables_used"] = sorted({v for r in self.receipts for v in r.get("environment_variables_used", [])})
        return result

    def _document(self, source: Path, prepare_only: bool) -> dict:
        doc = VisualDocument(source, self.output_dir)
        report = {"id": doc.id, "path": str(source), "source_sha256": doc.sha256,
                  "status": "prepared" if prepare_only else "partial", "pages": [], "facts": []}
        try:
            report["pages"] = [{**doc.page_info(number), "status": "prepared", "regions": [], "errors": [], "audit": []}
                               for number in range(1, len(doc.pdf) + 1)]
            report["planning_estimate"] = {
                "calls_assuming_one_region_per_page": 4 * len(doc.pdf) + (len(doc.pdf) + self.settings.window_pages - 1) // self.settings.window_pages,
                "note": "Estimate only: one layout, one read, one verification batch and one audit per page; extra regions/rereads cost more.",
                "run_call_limit": self.settings.max_calls, "limit_scope": "all documents in this run"}
            for state in report["pages"]:
                number = state["page"]
                if not prepare_only:
                    try:
                        state["card"] = self._ask("layout", LAYOUT, {"document_id": doc.id, "sha256": doc.sha256, "page": number},
                            [doc.render(number, edge=self.settings.overview_edge,
                                        max_pixels=min(self.settings.max_pixels_per_image, self.settings.max_image_pixels - 8192))],
                            lambda a: page_card(a, number))
                    except (ModelError, ContractError) as exc:
                        state["errors"].append(str(exc))
                        # Universal recovery is the full original page, not a document-specific template.
                        state["card"] = {"page": number, "title": "", "summary": "layout unresolved", "blank": False,
                                         "regions": [{"id": "unresolved", "kind": "unknown", "bbox": [0,0,1,1],
                                                      "objects": [], "summary": "full-page recovery"}]}
                self._checkpoint(report)
            if prepare_only:
                return report
            assignments = self._segment(doc, report)
            for state in report["pages"]:
                number = state["page"]
                hints = self._hints(doc, number)
                state["auxiliary_evidence"] = hints
                regions = state["card"]["regions"]
                for region in regions[:self.settings.max_regions_per_page]:
                    rid = f"p{number}:{region['id']}"
                    self._read_region(doc, state, {**region, "id": rid}, assignments.get(rid, {}), hints, report)
                if len(regions) > self.settings.max_regions_per_page:
                    state["errors"].append("Region budget exhausted; remaining regions NOT read")
                    state["unread_regions"] = regions[self.settings.max_regions_per_page:]
                self._audit(doc, state, hints, report)
                state["status"] = ("completed" if not state["errors"] and state["audit"][-1].get("complete")
                                   and all(r["status"] == "completed" for r in state["regions"]) else "partial")
                self._checkpoint(report)
            report["facts"] = consolidate(report["facts"])
            resolve_conflicts(report["facts"])
            report["status"] = "completed" if all(p["status"] == "completed" for p in report["pages"]) else "partial"
            if doc._hash() != doc.sha256:
                report["status"] = "failed"
                report["error"] = "Source changed during recognition; result invalid"
                for fact in report["facts"]:
                    fact.update(status="pending", reason="source_changed")
            report["coverage"] = {"total_pages": len(report["pages"]),
                                  "completed_pages": sum(p["status"] == "completed" for p in report["pages"]),
                                  "region_note": "All regions and pages tracked; model audit is not measured recall."}
            self._checkpoint(report)
            return report
        except Exception as exc:
            report.update(status="failed", error=type(exc).__name__)
            for state in report["pages"]:
                if state["status"] == "prepared":
                    state.update(status="partial", errors=state["errors"] + ["Document stage interrupted"])
            for fact in report["facts"]:
                fact.update(status="pending", reason="document_failed")
            self._checkpoint(report)
            return report
        finally:
            doc.close()

    def _checkpoint(self, report: dict):
        write_json(self.output_dir / "documents" / (report["id"] + ".json"), report)

    def _segment(self, doc: VisualDocument, report: dict) -> dict:
        assignments = {}
        for offset in range(0, len(report["pages"]), self.settings.window_pages):
            window = report["pages"][offset:offset + self.settings.window_pages]
            units = [{**r, "region": f"p{s['page']}:{r['id']}", "page": s["page"], "title": s["card"]["title"]}
                     for s in window for r in s["card"]["regions"]]
            try:
                batches = pack(units, self.settings.max_prompt_chars // 3)
                for batch in batches:
                    # A bounded overlap plus the previously resolved boundary carries continuation;
                    # full document history never grows with PDF length.
                    index = units.index(batch[0])
                    previous = units[index - 1:index] if index else []
                    if not previous and offset:
                        prior = report["pages"][offset - 1]
                        previous = [{**r, "region": f"p{prior['page']}:{r['id']}", "page": prior["page"]}
                                    for r in prior["card"]["regions"][-1:]]
                    following = units[index + len(batch):index + len(batch) + 1]
                    if not following and offset + len(window) < len(report["pages"]):
                        next_page = report["pages"][offset + len(window)]
                        following = [{**r, "region": f"p{next_page['page']}:{r['id']}", "page": next_page["page"]}
                                     for r in next_page["card"]["regions"][:1]]
                    context = previous + following
                    expected = {u["region"] for u in batch}
                    known = expected | {u["region"] for u in context}
                    order = {u["region"]: (u["page"], i) for i, u in enumerate(previous + batch + following)}
                    def validate(answer):
                        found = rows(answer.get("assignments"), "assignments")
                        if len(found) != len(expected) or {a.get("region") for a in found} != expected:
                            raise ContractError("Segmentation lost or duplicated owned regions")
                        for a in found:
                            for key in ("subject", "section", "continues_from", "reason"):
                                text(a.get(key), key, empty=key != "reason")
                            if a["continues_from"] and a["continues_from"] not in known:
                                raise ContractError("Segmentation cites unseen boundary")
                            if a["continues_from"] and order[a["continues_from"]] >= order[a["region"]]:
                                raise ContractError("Segmentation continuation must reference an earlier region")
                        return answer
                    answer = self._ask("segment", SEGMENT,
                        {"document_id": doc.id, "sha256": doc.sha256, "owned_regions": batch,
                         "context_regions": context, "previous_boundary": [assignments[u["region"]] for u in previous
                                                                           if u["region"] in assignments]}, [], validate)
                    pages = {u["region"]: u["page"] for u in batch + context}
                    for assignment in sorted(answer["assignments"], key=lambda a: order[a["region"]]):
                        prior = assignments.get(assignment["continues_from"], {})
                        # Carry one bounded source anchor, never a growing page history.
                        # It remains a model hypothesis and must be visually verified.
                        if prior.get("subject") and prior["subject"] == assignment["subject"]:
                            assignment["source_context_page"] = prior.get("source_context_page", pages[assignment["continues_from"]])
                        else:
                            assignment["source_context_page"] = pages[assignment["region"]]
                        assignments[assignment["region"]] = assignment
            except (ContractError, ModelError) as exc:
                for state in window:
                    state["errors"].append(str(exc))
        report["assignments"] = list(assignments.values())
        return assignments

    def _hints(self, doc: VisualDocument, number: int) -> dict:
        hints = {"native": doc.native_evidence(number) if self.settings.use_native_text else [],
                 "ocr": {"status": "disabled", "rows": []}}
        if self.settings.use_existing_ocr and self.ocr_evidence:
            try:
                hints["ocr"] = self.ocr_evidence(doc, number)
            except Exception:
                hints["ocr"] = {"status": "failed", "rows": []}
        return hints

    def _images(self, doc, number, region, rotation=0, reread=False, assignment=None):
        # At most one previous-page overview, one current overview and the native crop.
        has_context = self.settings.max_images >= 3 and number > 1
        pixels = self.settings.max_image_pixels - 16384
        cap = self.settings.max_pixels_per_image
        images = [doc.render(number, edge=self.settings.overview_edge, max_pixels=min(cap, int(pixels * .25))),
                  doc.render(number, region, edge=self.settings.reread_edge if reread else self.settings.detail_edge,
                             rotation=rotation, max_pixels=min(cap, int(pixels * .55)))]
        if has_context:
            anchor = (assignment or {}).get("source_context_page", number - 1)
            if anchor == number:
                anchor = number - 1
            images.insert(0, doc.render(anchor, edge=min(1600, self.settings.overview_edge), max_pixels=min(cap, int(pixels * .20))))
        return images

    def _read_region(self, doc, state, target, assignment, hints, report, depth=0, rotation=0):
        number = state["page"]
        entry = {"id": target["id"], "bbox": target["bbox"], "status": "partial", "depth": depth}
        state["regions"].append(entry)
        try:
            evidence = hints
            if len(compact(hints)) > self.settings.max_prompt_chars // 4:
                # Original hints remain in the local result. Visual reading still sees the
                # entire target. Optional hints must not displace source images/context.
                evidence = {"status": "omitted_from_prompt_over_budget", "retained_in_page_result": True}
            context = {"document_id": doc.id, "sha256": doc.sha256, "page": number,
                       "target": target, "segmentation_hint": assignment, "auxiliary_evidence": evidence}
            images = self._images(doc, number, target["bbox"], rotation, depth > 0, assignment)
            def validate(answer):
                reading(answer)
                for item in answer["facts"] + answer["tables"] + answer["dimensions"] + answer["uncertainties"]:
                    if not inside(item["bbox"], target["bbox"]):
                        raise ContractError("Reader extracted evidence outside target region")
                return answer
            answer = self._ask("read", READ, context, images, validate)
            entry["reading"] = answer
            candidates = []
            for raw in answer["facts"]:
                fact = {**raw, "id": content_hash([doc.id, number, target["id"], depth, raw])[:24],
                        "document_id": doc.id, "page": number, "source_sha256": doc.sha256,
                        "region_id": target["id"], "status": "pending", "reason": "awaiting_source_review"}
                candidates.append(fact)
                report["facts"].append(fact)
            # Re-render from source for review; never let two calls merely agree
            # on the same low-resolution artifact when a better view is available.
            review_images = self._images(doc, number, target["bbox"], rotation, True, assignment) if candidates else []
            self._verify(doc, number, target, candidates, answer["transcript"], review_images, assignment)
            unresolved = []
            for index, issue in enumerate(answer["uncertainties"]):
                # Earlier readings under an ambiguous crop cannot win by majority vote.
                for candidate in candidates:
                    a, b = candidate["bbox"], issue["bbox"]
                    if a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]:
                        candidate.update(status="pending", reason="ambiguous_source_requires_reread")
                if depth == 0 and index < self.settings.max_rereads_per_region:
                    refined = self._read_region(doc, state, {"id": target["id"] + f":zoom{index}",
                        "bbox": issue["bbox"], "kind": "reread", "summary": issue["reason"], "objects": []},
                        assignment, hints, report, depth + 1, issue.get("rotation", 0))
                    if refined["status"] != "completed":
                        unresolved.append(issue)
                else:
                    unresolved.append(issue)
            entry["unresolved"] = unresolved
            entry["status"] = "completed" if not unresolved else "partial"
        except (ModelError, ContractError) as exc:
            entry["error"] = str(exc)
        self._checkpoint(report)
        return entry

    def _verify(self, doc, number, target, candidates, transcript, images, assignment):
        for batch in pack(candidates, self.settings.max_prompt_chars // 2):
            expected = {f["id"] for f in batch}
            def validate(answer):
                found = rows(answer.get("verdicts"), "verdicts")
                if len(found) != len(expected) or {v.get("id") for v in found} != expected:
                    raise ContractError("Reviewer lost or duplicated facts")
                for verdict in found:
                    if verdict.get("decision") not in {"supported", "uncertain", "rejected"}:
                        raise ContractError("Invalid review decision")
                    for key in ("object_supported", "field_supported", "value_supported", "unit_supported", "scope_supported", "role_supported",
                                "qualifiers_supported", "condition_supported", "time_supported"):
                        if type(verdict.get(key)) is not bool:
                            raise ContractError("Missing independent review check")
                    text(verdict.get("quote"), "review quote", empty=True)
                    text(verdict.get("reason"), "review reason", empty=True)
                return answer
            answer = self._ask("verify", VERIFY, {"document_id": doc.id, "sha256": doc.sha256,
                "page": number, "target": target, "segmentation_hint": assignment, "candidates": batch}, images, validate)
            by_id = {v["id"]: v for v in answer["verdicts"]}
            for fact in batch:
                review = by_id[fact["id"]]
                fact["review"] = review
                checks = [review[k] for k in ("object_supported", "field_supported", "value_supported", "unit_supported", "scope_supported", "role_supported",
                                              "qualifiers_supported", "condition_supported", "time_supported")]
                literal_tokens = [(fact[k], k == "unit") for k in ("value", "unit", "qualifier") if fact[k]]
                grounded = (normalized(fact["quote"]) in normalized(transcript)
                            and all(literal_supported(token, fact["quote"], unit=unit) for token, unit in literal_tokens)
                            and bool(normalized(review["quote"]))
                            and normalized(review["quote"]) in normalized(transcript)
                            and all(literal_supported(token, review["quote"], unit=unit) for token, unit in literal_tokens))
                explicit = all(fact[k].strip().lower() not in {"unknown", "unresolved", ""} for k in ("object", "scope", "role"))
                if review["decision"] == "rejected":
                    fact.update(status="rejected", reason="review_rejected")
                elif review["decision"] == "supported" and all(checks) and grounded and explicit:
                    fact.update(status="verified_model", reason="source_and_semantics_checked_by_model")
                else:
                    fact.update(status="pending", reason="insufficient_source_or_semantic_support")

    def _audit(self, doc, state, hints, report):
        number = state["page"]
        for round_no in range(self.settings.audit_rounds):
            try:
                # Compact source readings, not a growing conversation or whole PDF.
                summaries = [{"region": r["id"], "bbox": r["bbox"], "status": r["status"],
                              "transcript": r.get("reading", {}).get("transcript", ""),
                              "tables": r.get("reading", {}).get("tables", []),
                              "dimensions": r.get("reading", {}).get("dimensions", []),
                              "facts": [{k: f[k] for k in FACT_KEYS} for f in report["facts"] if f["region_id"] == r["id"]
                                        and f["page"] == number]} for r in state["regions"]]
                shards = pack(summaries, self.settings.max_prompt_chars // 2) or [[]]
                audits = []
                for shard in shards:
                    def validate(answer):
                        if type(answer.get("complete")) is not bool or not isinstance(answer.get("issues"), list):
                            raise ContractError("Invalid coverage audit")
                        for item in rows(answer.get("missing_regions"), "missing_regions"):
                            bbox(item.get("bbox"))
                            text(item.get("reason"), "missing region reason")
                        if answer["complete"] and (answer["missing_regions"] or answer["issues"]):
                            raise ContractError("Contradictory coverage audit")
                        return answer
                    audit = self._ask("audit", AUDIT,
                        {"document_id": doc.id, "sha256": doc.sha256, "page": number,
                         "all_regions": [{"id": r["id"], "bbox": r["bbox"], "status": r["status"]} for r in state["regions"]],
                         "readings": shard, "shard_count": len(shards),
                         "shard_note": "其它已读区域不在本分片内，只对本分片核对遗漏事实，同时检查整页未覆盖区域。"},
                        [doc.render(number, edge=self.settings.reread_edge,
                                    max_pixels=min(self.settings.max_pixels_per_image, self.settings.max_image_pixels - 8192))], validate)
                    audits.append(audit)
                missing = [r for a in audits for r in a["missing_regions"]]
                current = {"round": round_no, "complete": all(a["complete"] for a in audits),
                           "missing_regions": missing, "issues": [i for a in audits for i in a["issues"]]}
                state["audit"].append(current)
                if current["complete"]:
                    break
                if round_no + 1 < self.settings.audit_rounds:
                    unique = {compact(m["bbox"]): m for m in missing}
                    if len(unique) > self.settings.max_audit_regions:
                        state["errors"].append("Audit recovery budget exhausted; missing regions retained")
                    for index, item in enumerate(list(unique.values())[:self.settings.max_audit_regions]):
                        self._read_region(doc, state, {"id": f"p{number}:audit{round_no}:{index}", "bbox": item["bbox"],
                            "kind": "audit_recovery", "objects": [], "summary": item["reason"]}, {}, hints, report)
            except (ContractError, ModelError) as exc:
                state["audit"].append({"round": round_no, "complete": False, "error": str(exc)})
                break
