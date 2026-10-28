"""PDF workflow orchestration; model transport, evidence and review live in separate layers."""
from __future__ import annotations
import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Callable
from .model_client import JsonVisionModel, ModelError, content_hash
from .storage import write_json, write_jsonl
from .visual_contract import (VERSION, LAYOUT, SEGMENT, READ, AUDIT, FACT_KEYS,
                              ContractError, bbox, inside, page_card, reading,
                              resolve_conflicts, consolidate, rows, text)
from .visual_document import VisualDocument
from .visual_settings import VisualSettings
from .visual_batching import compact, pack, audit_units, layout_anchors, verification_batches
from .visual_evidence import normalized, literal_supported, source_contains
from .visual_runtime import VisualRuntime
from .visual_review import VisualReviewer
from .visual_sources import content_coverage, related_sources, unaccounted_content, fact_source_bindings


class VisualPipeline:
    def __init__(self, model: JsonVisionModel, output_dir: Path, *, settings: VisualSettings | None = None,
                 ocr_evidence: Callable | None = None, review_model: JsonVisionModel | None = None,
                 progress_callback: Callable | None = None):
        self.model, self.output_dir = model, output_dir.resolve()
        self.review_model = review_model or model
        self.settings = settings or VisualSettings()
        self.ocr_evidence, self.progress_callback = ocr_evidence, progress_callback
        self.runtime = VisualRuntime(model, self.review_model, self.output_dir, self.settings)
        self.reviewer = VisualReviewer(self.settings, self.runtime.ask_async)

    @property
    def calls(self):
        return self.runtime.calls

    @property
    def cache_hits(self):
        return self.runtime.cache_hits

    @property
    def peak_concurrency(self):
        return self.runtime.peak_concurrency

    @property
    def receipts(self):
        return self.runtime.receipts

    async def _ask_async(self, *args):
        return await self.runtime.ask_async(*args)

    def run(self, pdfs: list[Path], *, prepare_only: bool = False,
            page_selections: dict[str, list[int]] | None = None) -> dict:
        return asyncio.run(self.arun(pdfs, prepare_only=prepare_only, page_selections=page_selections))


    async def arun(self, pdfs: list[Path], *, prepare_only: bool = False,
                   page_selections: dict[str, list[int]] | None = None) -> dict:
        from .pipeline_lock import pipeline_run_guard
        from .provenance import runtime_identity
        if not pdfs:
            raise ValueError("Explicit input PDFs required")
        sources = [p.resolve(strict=True) for p in pdfs]
        if len(set(sources)) != len(sources) or any(p.suffix.lower() != ".pdf" for p in sources):
            raise ValueError("Duplicate or non-PDF input")
        selections = {str(Path(p).resolve()): numbers for p, numbers in (page_selections or {}).items()}
        if set(selections) - {str(p) for p in sources}:
            raise ValueError("Page selection refers to an input that was not supplied")
        if any(self.output_dir == p.parent or self.output_dir in p.parents for p in sources):
            raise ValueError("Output directory must not contain input PDFs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with pipeline_run_guard(self.output_dir / ".visual.lock"):
            self.runtime.reset()
            self._work_slots = asyncio.Semaphore(self.settings.model_concurrency)
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
                    report = await self._document(source, prepare_only, selections.get(str(source)))
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
                             "calls": self.calls, "cache_hits": self.cache_hits, "peak_concurrency": self.peak_concurrency}
        result["receipts"] = list(self.receipts)
        result["environment_variables_used"] = sorted({v for r in self.receipts for v in r.get("environment_variables_used", [])})
        return result


    async def _document(self, source: Path, prepare_only: bool, selected_pages: list[int] | None = None) -> dict:
        doc = VisualDocument(source, self.output_dir)
        report = {"id": doc.id, "path": str(source), "source_sha256": doc.sha256,
                  "status": "prepared" if prepare_only else "partial", "pages": [], "facts": []}
        try:
            numbers = list(range(1, len(doc.pdf) + 1)) if selected_pages is None else selected_pages
            if (not isinstance(numbers, list) or not numbers or any(type(n) is not int or n < 1 or n > len(doc.pdf) for n in numbers)
                    or len(set(numbers)) != len(numbers)):
                raise ValueError("Invalid physical page selection")
            numbers = sorted(numbers)
            report.update(source_page_count=len(doc.pdf), selected_pages=numbers,
                          scope="whole_document" if len(numbers) == len(doc.pdf) else "selected_pages_only")
            report["pages"] = [{**doc.page_info(number), "status": "prepared", "regions": [], "errors": [], "audit": []}
                               for number in numbers]
            report["planning_estimate"] = {
                "calls_assuming_one_region_per_page": (4 + int(self.settings.independent_review)) * len(numbers) + (len(numbers) + self.settings.window_pages - 1) // self.settings.window_pages,
                "note": "Estimate only: layout, read, optional blind read, verification and audit; dense regions/repair need additional calls.",
                "run_call_limit": self.settings.max_calls, "limit_scope": "all documents in this run"}
            async def layout_page(state):
                async with self._work_slots:
                    number = state["page"]
                    try:
                        anchors = doc.geometric_evidence(number)
                        if not self.settings.use_native_text:
                            anchors=[{k:v for k,v in a.items() if k!="text"} for a in anchors]
                        state["geometric_anchors"] = anchors
                        # Geometry is still useful when all anchor text would
                        # exceed the context budget. Full evidence is retained.
                        wire_anchors,representation=layout_anchors(anchors,self.settings.max_prompt_chars//2)
                        state["layout_anchor_representation"]=representation
                        def layout_card(answer):
                            card=page_card(answer,number,anchors,self.settings.crop_padding)
                            for region in card["regions"]:
                                region["pre_guard_bbox"]=list(region["bbox"])
                                region["bbox"]=doc.safe_bounds(number,region["bbox"])
                                region["crop_guard"]="source_cells_no_text_inference"
                            return card
                        state["card"] = await self._ask_async("layout", LAYOUT, {"document_id": doc.id, "sha256": doc.sha256,
                            "page": number, "page_width":state["width"],"page_height":state["height"],"geometric_anchors":wire_anchors,
                            "geometry_hints":{"representation":representation,"source_anchor_count":len(anchors),
                                "instruction":"锚点是可选辅助。若列表为空，直接按全页图提出语义区域且anchor_ids为空；原始几何仍由程序保留用于裁图边界与查漏。"}},
                            [doc.render(number, edge=self.settings.overview_edge,
                                        max_pixels=min(self.settings.max_pixels_per_image, self.settings.max_image_pixels - 8192))],
                            layout_card)
                    except (ModelError, ContractError) as exc:
                        state["errors"].append(str(exc))
                        # Universal recovery is the full original page, not a document-specific template.
                        state["card"] = {"page": number, "title": "", "summary": "layout unresolved", "blank": False,
                                         "regions": [{"id": "unresolved", "kind": "unknown", "bbox": [0,0,1,1],
                                                      "objects": [], "summary": "full-page recovery"}]}
                    self._checkpoint(report)
            if prepare_only:
                self._checkpoint(report)
                return report
            assignments = {}
            # Layout and segmentation share the same bounded frontier as
            # reading/review. One optional lookahead page preserves boundary
            # hints, but must not spend the current window's minimum budget.
            for offset in range(0,len(report['pages']),self.settings.window_pages):
                end=min(offset+self.settings.window_pages,len(report['pages']))
                window=report['pages'][offset:end]
                layout_states=[s for s in window if 'card' not in s]
                minimum=(4+int(self.settings.independent_review))*len(window)+1
                if (end<len(report['pages']) and 'card' not in report['pages'][end]
                        and self.settings.max_calls-self.calls>minimum):
                    layout_states.append(report['pages'][end])
                outcomes=await asyncio.gather(*(layout_page(s) for s in layout_states),return_exceptions=True)
                for state,outcome in zip(layout_states,outcomes):
                    if isinstance(outcome,BaseException):
                        state['errors'].append('Layout failure: '+type(outcome).__name__)
                        state.setdefault('card',{'page':state['page'],'title':'','summary':'layout unresolved','blank':False,
                            'regions':[{'id':'unresolved','kind':'unknown','bbox':[0,0,1,1],'objects':[],'summary':'full-page recovery'}]})
                assignments=await self._segment(doc,report,start=offset,stop=end,assignments=assignments)
                report['active_window']=[window[0]['page'],window[-1]['page']]
                await self._recognize_window(doc,report,assignments,window)
            report.pop('active_window',None)
            report["facts"].sort(key=lambda f: (f["page"], f["region_id"], f["id"]))
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


    async def _recognize_window(self, doc, report, assignments, states):
        jobs=[]
        async def read_job(state,region):
            async with self._work_slots:
                number = state["page"]
                hints=state["auxiliary_evidence"]
                rid=f"p{number}:{region['id']}"
                target={**region,"id":rid}
                assignment=assignments.get(rid,{})
                entry=await self._read_region(doc,state,target,assignment,hints,report,defer_review=True)
                return (state,target,assignment,hints,entry)
        for state in states:
            state["auxiliary_evidence"]=self._hints(doc,state["page"])
            if len(state["card"]["regions"]) > self.settings.max_regions_per_page:
                state["errors"].append("Region budget exhausted; remaining regions NOT read")
                state["unread_regions"]=state["card"]["regions"][self.settings.max_regions_per_page:]
        # Round-robin page frontiers prevent an early dense page from
        # consuming every reading slot before later pages get a first look.
        units=[(s,s["card"]["regions"][i])
               for i in range(min(self.settings.max_regions_per_page,max(len(s["card"]["regions"]) for s in states)))
               for s in states if i<len(s["card"]["regions"])]
        outcomes = await asyncio.gather(*(read_job(state,r) for state,r in units), return_exceptions=True)
        for (state,region),outcome in zip(units,outcomes):
            if isinstance(outcome, BaseException):
                state.update(status="partial")
                state["errors"].append("Region failure: " + type(outcome).__name__)
                for fact in report["facts"]:
                    if fact["page"] == state["page"] and fact["region_id"]==f"p{state['page']}:{region['id']}":
                        fact.update(status="pending", reason="region_failed")
            elif outcome[-1].get("reading"):
                jobs.append(outcome)
        # A bounded breadth-first frontier: every primary region gets a
        # source reading before any review or optional recursive zoom.
        # Parallel reviewers share the request semaphore, so first batches
        # from other regions can run before one dense region's next batch.
        async def review_job(job):
            state,target,assignment,hints,entry=job
            await self._complete_region(doc,state,target,assignment,hints,report,entry,defer_recovery=True)
        await asyncio.gather(*(review_job(job) for job in jobs))
        async def audit_page(state):
            async with self._work_slots:
                await self._audit(doc,state,state.get("auxiliary_evidence",{}),report)
                state["status"]=("completed" if not state["errors"] and state["audit"][-1].get("complete")
                                 and all(r["status"]=="completed" for r in state["regions"]) else "partial")
                self._checkpoint(report)
        async def audit_frontier(states):
            outcomes=await asyncio.gather(*(audit_page(state) for state in states),return_exceptions=True)
            for state,outcome in zip(states,outcomes):
                if isinstance(outcome,BaseException):
                    state["status"]="partial"
                    state["errors"].append("Audit failure: "+type(outcome).__name__)
        # Independent coverage gets a turn before optional uncertainty zooms
        # consume the remaining budget. Any later source changes require a
        # fresh audit; the earlier certificate is never reused as success.
        await audit_frontier(states)
        recovery_jobs=[]
        parents=[]
        for state,target,assignment,hints,entry in jobs:
            for request in entry.get("reread_requests",[]):
                refined=await self._read_region(doc,state,request["target"],assignment,hints,report,
                                               depth=1,rotation=request["rotation"],defer_review=True)
                request["region_id"]=refined["id"]
                parents.append((entry,request,refined))
                if refined.get("reading"):
                    recovery_jobs.append((state,request["target"],assignment,hints,refined))
        await asyncio.gather(*(review_job(job) for job in recovery_jobs))
        for entry,request,refined in parents:
            if refined["status"]=="completed":
                entry["unresolved"]=[i for i in entry["unresolved"] if i!=request["issue"]]
            entry["status"]="completed" if not entry["unresolved"] and not entry.get("review_contract_issues") else "partial"
        changed_pages={job[0]["page"] for job in recovery_jobs}
        if changed_pages:
            await audit_frontier([s for s in states if s["page"] in changed_pages])

    def _checkpoint(self, report: dict):
        write_json(self.output_dir / "documents" / (report["id"] + ".json"), report)
        if self.progress_callback:
            pages=report['pages']
            read=sum(bool(r.get('reading')) for p in pages for r in p['regions'])
            adopted=sum(f['status']=='verified_model' for f in report['facts'])
            stage=('visual_audit' if any(p['audit'] for p in pages) else
                   'visual_review' if any(f.get('review') for f in report['facts']) else
                   'visual_read' if read else 'visual_layout')
            # No fabricated end-to-end percentage from a variable-sized call
            # budget. Keep that progress indeterminate, show observed counts.
            self.progress_callback(0,stage,f"{len(pages)}页；已细读{read}个区域；候选{len(report['facts'])}条；自动核验通过{adopted}条；调用{self.calls}/{self.settings.max_calls}")


    async def _segment(self, doc: VisualDocument, report: dict, *, start=0, stop=None, assignments=None) -> dict:
        assignments = {} if assignments is None else assignments
        changed=set()
        stop=len(report['pages']) if stop is None else stop
        for offset in range(start, stop, self.settings.window_pages):
            window = report["pages"][offset:min(stop,offset + self.settings.window_pages)]
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
                    if (not following and offset + len(window) < len(report["pages"])
                            and 'card' in report['pages'][offset+len(window)]):
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
                    answer = await self._ask_async("segment", SEGMENT,
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
                            assignment["section_scope"] = prior.get("section_scope",assignment["continues_from"])
                        else:
                            assignment["source_context_page"] = pages[assignment["region"]]
                            assignment["section_scope"] = assignment["region"]
                        assignments[assignment["region"]] = assignment
                        changed.add(assignment['region'])
                        if assignment['continues_from']:
                            changed.add(assignment['continues_from'])
            except (ContractError, ModelError) as exc:
                for state in window:
                    state["errors"].append(str(exc))
        report["assignments"] = list(assignments.values())
        # Model-declared continuation links provide source context, never an
        # automatic assertion that adjacent objects or facts are the same.
        lookup={f"p{s['page']}:{r['id']}":{"region_id":f"p{s['page']}:{r['id']}","page":s["page"],"bbox":r["bbox"]}
                for s in report["pages"] for r in s.get("card",{}).get("regions",[])}
        # Streaming must not rescan every assignment for every prior region
        # after each window. Index children once, refresh only changed links.
        children={}
        for identifier,assignment in assignments.items():
            children.setdefault(assignment.get('continues_from',''),[]).append(identifier)
        for identifier in changed & assignments.keys():
            assignment=assignments[identifier]
            links=[assignment.get("continues_from","")]
            links += children.get(identifier,[])
            assignment["source_context_regions"]=[lookup[key] for key in dict.fromkeys(links)
                if key in lookup and abs(lookup[key]["page"]-lookup[identifier]["page"]) <= self.settings.window_pages][:2]
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


    def _images(self, doc, number, region, rotation=0, reread=False, assignment=None, pixel_budget=None):
        # Bounded linked-page context, current overview and the original source crop.
        has_context = self.settings.max_images >= 3 and number > 1
        pixels = (pixel_budget or self.settings.max_image_pixels) - 16384
        cap = self.settings.max_pixels_per_image
        related=[r for r in (assignment or {}).get("source_context_regions",[]) if abs(r["page"]-number)<=self.settings.window_pages]
        related=related[:max(0,self.settings.max_images-2-int(has_context))]
        crop_pixels=min(cap,int(pixels*.55/max(1,len(related)+1)))
        images = [doc.render(number, edge=self.settings.overview_edge, max_pixels=min(cap, int(pixels * .25))),
                  doc.render(number, region, edge=self.settings.reread_edge if reread else self.settings.detail_edge,
                             rotation=rotation, max_pixels=crop_pixels)]
        if has_context:
            anchor = (assignment or {}).get("source_context_page", number - 1)
            if anchor == number or abs(anchor-number)>self.settings.window_pages:
                anchor = number - 1
            images.insert(0, doc.render(anchor, edge=min(1600, self.settings.overview_edge), max_pixels=min(cap, int(pixels * .20))))
        for context in related:
            images.append(doc.render(context['page'],context["bbox"],edge=self.settings.reread_edge if reread else self.settings.detail_edge,
                                     max_pixels=crop_pixels))
        return images


    async def _read_region(self, doc, state, target, assignment, hints, report, depth=0, rotation=0, defer_review=False):
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
                       "target": {k: target[k] for k in ("id","bbox","kind") if k in target},
                       "segmentation_hint": assignment, "auxiliary_evidence": evidence}
            if target.get("repair_feedback"):
                context["repair_feedback"]=target["repair_feedback"]
            if "parent_bbox" in target:
                context["recovery_geometry"]={k:target[k] for k in ("parent_bbox","issue_bbox")}
            images = self._images(doc, number, target["bbox"], rotation, depth > 0, assignment)
            def validate(answer):
                reading(answer)
                return answer
            answer = await self._ask_async("read", READ, context, images, validate)
            entry["reading"] = answer
            entry['content_coverage'] = content_coverage(answer)
            outside = [item for kind in ("tables","dimensions","uncertainties") for item in answer[kind]
                       if not inside(item["bbox"], target["bbox"])]
            candidates = []
            bindings = fact_source_bindings(answer)
            for index,raw in enumerate(answer["facts"]):
                fact = {**raw, "id": content_hash([doc.id, number, target["id"], depth, raw])[:24],
                        "document_id": doc.id, "page": number, "source_sha256": doc.sha256,
                        "region_id": target["id"], "status": "pending", "reason": "awaiting_source_review"}
                fact['source_bindings']=bindings[index]
                fact["section_scope"] = assignment.get("section_scope",target["id"].split(":zoom",1)[0])
                fact["object_binding"] = "section_scoped_source_phrase"
                anchor=doc.locate_quote(number,raw["quote"],target["bbox"]) if self.settings.use_native_text else None
                if anchor:
                    fact.update(model_bbox=raw["bbox"],bbox=anchor["bbox"],geometry_source="unique_source_quote_anchor",source_anchor_id=anchor["id"])
                candidates.append(fact)
                report["facts"].append(fact)
                if not inside(fact["bbox"],target["bbox"]): outside.append(fact)
            entry["boundary_issues"] = [{"bbox":item["bbox"],"reason":"outside coarse target; retained for expanded reread"}
                                        for item in outside]
            entry["rotation"]=rotation
            entry["outside_boxes"]=[item["bbox"] for item in outside]
            self._checkpoint(report)  # Preserve source readings before a slow/interruptible review.
            if not defer_review:
                await self._complete_region(doc,state,target,assignment,hints,report,entry)
        except (ModelError, ContractError) as exc:
            entry["error"] = str(exc)
        self._checkpoint(report)
        return entry


    async def _complete_region(self,doc,state,target,assignment,hints,report,entry,defer_recovery=False):
        number=state["page"];depth=entry["depth"];rotation=entry.get("rotation",0)
        answer=entry["reading"]
        candidates=[f for f in report["facts"] if f["page"]==number and f["region_id"]==entry["id"]]
        outside=entry.get("outside_boxes",[])
        try:
            # Re-render from source for review; never let two calls merely agree
            # on the same low-resolution artifact when a better view is available.
            # Shared high-resolution region preserves headers and conditions;
            # uncertainty recovery, rather than mandatory tight crops, zooms in.
            review_images = self._images(doc, number, target["bbox"], rotation, True, assignment,
                                         pixel_budget=self.settings.max_image_pixels) if candidates else []
            independent=None
            if candidates and self.settings.independent_review:
                independent=await self._ask_async('blind_read', READ,
                    {'document_id':doc.id,'sha256':doc.sha256,'page':number,
                     'target':{k:target[k] for k in ('id','bbox','kind') if k in target},
                     'purpose':'independent_source_read; no previous candidates, transcript or segmentation answers'},
                    review_images, reading)
                entry['independent_read']=independent
            review_errors=await self.reviewer.review(doc, number, target, [f for f in candidates if inside(f["bbox"],target["bbox"])],
                               answer["transcript"], review_images, assignment,
                               support_context=related_sources(report,number,entry['id'],assignment,self.settings.window_pages),
                               independent_read=independent,
                               table_source=bool(answer["tables"]),
                               checkpoint=lambda:self._checkpoint(report))
            entry["review_contract_issues"]=review_errors
            for fact in candidates:
                if not inside(fact["bbox"], target["bbox"]):
                    fact.update(status="pending", reason="evidence_outside_coarse_target")
            unresolved = []
            issues = list(answer["uncertainties"])
            for index,fact in enumerate(candidates):
                if fact.get('reason') in {'independent_source_disagreement','independent_field_conflict','incomplete_or_unbound_fields'}:
                    issues.append({'bbox':fact['bbox'],'reason':fact['reason'], 'kind':'binding',
                                   'rotation':0,'fact_indices':[index]})
            if outside:
                # The crop was proposed by a model and is approximate. Expand
                # within the source page rather than clipping facts or guessing
                # that an adjacent object's evidence belongs to this region.
                union = [min([target["bbox"][0]]+[b[0] for b in outside]),
                         min([target["bbox"][1]]+[b[1] for b in outside]),
                         max([target["bbox"][2]]+[b[2] for b in outside]),
                         max([target["bbox"][3]]+[b[3] for b in outside])]
                issues.insert(0,{"bbox":union,"reason":"Expand model-proposed crop to resolve out-of-target evidence",
                                 "rotation":0,"boundary_recovery":True,"kind":"crop_boundary"})
            entry["reread_requests"]=[]
            for index, issue in enumerate(issues):
                # Earlier readings under an ambiguous crop cannot win by majority vote.
                for fact_index,candidate in enumerate(candidates):
                    a, b = candidate["bbox"], issue["bbox"]
                    if (not issue.get("boundary_recovery") and
                            ("fact_indices" not in issue or fact_index in issue["fact_indices"]) and
                            a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]):
                        candidate.update(status="pending", reason="ambiguous_source_requires_reread")
                if depth == 0 and index < self.settings.max_rereads_per_region:
                    box=(doc.recovery_bounds(number,target["bbox"],issue["bbox"],issue.get("edges",[]))
                         if issue.get("kind")=="crop_boundary" else doc.safe_bounds(number,issue["bbox"]))
                    refined_target={"id":target["id"]+f":zoom{index}","bbox":box,"kind":"reread",
                                    "summary":issue["reason"],"objects":[],"parent_bbox":target["bbox"],
                                    "issue_bbox":issue["bbox"]}
                    if defer_recovery:
                        entry["reread_requests"].append({"target":refined_target,"rotation":issue.get("rotation",0),"issue":issue})
                        unresolved.append(issue)
                        continue
                    refined = await self._read_region(doc,state,refined_target,assignment,hints,report,depth+1,issue.get("rotation",0))
                    if refined["status"] != "completed":
                        unresolved.append(issue)
                else:
                    unresolved.append(issue)
            entry["unresolved"] = unresolved
            entry["status"] = "completed" if not unresolved and not review_errors else "partial"
        except (ModelError, ContractError) as exc:
            entry["error"] = str(exc)
        self._checkpoint(report)
        return entry


    async def _audit(self, doc, state, hints, report):
        number = state["page"]
        for round_no in range(self.settings.audit_rounds):
            try:
                # Compact source readings, not a growing conversation or whole PDF.
                summaries = [unit for r in state["regions"] for unit in audit_units(r,
                              [f for f in report["facts"] if f["region_id"]==r["id"] and f["page"]==number],
                              self.settings.max_prompt_chars//2)]
                # Dense source geometry is losslessly sharded like transcripts,
                # rather than repeated in every prompt until no audit can send.
                uncovered=[a for a in state.get("geometric_anchors",[])
                    if a["id"] in state["card"].get("unassigned_anchor_ids",[]) and not any(
                        r.get("reading") and inside(a["bbox"],r["bbox"]) for r in state["regions"])]
                state["geometrically_uncovered_anchor_ids"]=[a["id"] for a in uncovered]
                summaries.extend({"part":"unassigned_geometric_anchor","anchor":a} for a in uncovered)
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
                    audit = await self._ask_async("audit", AUDIT,
                        {"document_id": doc.id, "sha256": doc.sha256, "page": number,
                         "all_regions": [{"id": r["id"], "bbox": r["bbox"], "status": r["status"]} for r in state["regions"]],
                         "readings": shard, "shard_count": len(shards),
                         "unassigned_geometric_anchors_in_readings":True,
                         "shard_note": "readings按part拆分：transcript以offset连续切片；table_cell凭table_index/row/column关联；fact_status标记断言是否通过。其它分片缺席不等于未读或漏提，不能因此重读整页。只核对本分片可判断的问题，同时检查all_regions几何上未覆盖的区域。"},
                        [doc.render(number, edge=self.settings.reread_edge,
                                    max_pixels=min(self.settings.max_pixels_per_image, self.settings.max_image_pixels - 8192))], validate)
                    audits.append(audit)
                missing = [r for a in audits for r in a["missing_regions"]]
                unmapped=unaccounted_content(state['regions'])
                if unmapped:
                    missing += [{'bbox':c['bbox'],'reason':'Source cell/dimension has no explicit fact link or reference-only disposition'} for c in unmapped]
                current = {"round": round_no, "complete": all(a["complete"] for a in audits),
                           "missing_regions": missing, "issues": [i for a in audits for i in a["issues"]]}
                if unmapped:
                    current.update(complete=False,unaccounted_content_count=len(unmapped))
                    current['issues'].append('Unaccounted source content; transcript coverage is not fact coverage')
                state["audit"].append(current)
                if current["complete"]:
                    break
                if round_no + 1 < self.settings.audit_rounds:
                    unique = {compact(m["bbox"]): m for m in missing}
                    if len(unique) > self.settings.max_audit_regions:
                        state["errors"].append("Audit recovery budget exhausted; missing regions retained")
                    for index, item in enumerate(list(unique.values())[:self.settings.max_audit_regions]):
                        feedback=[]
                        for fact in report["facts"]:
                            a,b=fact["bbox"],item["bbox"]
                            if (fact["page"]==number and fact["status"]!="verified_model" and
                                    a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]):
                                feedback.append({"id":fact["id"],"candidate":{k:fact[k] for k in FACT_KEYS},
                                    "status":fact["status"],"reason":fact["reason"],
                                    "checks":fact.get("admission_checks",{}),
                                    "review_reason":fact.get("review",{}).get("reason","")})
                        # Bound repair context without hiding omitted diagnostics.
                        # These are fallible prior candidates, never reference answers.
                        selected=[]
                        for f in feedback:
                            if len(compact(selected+[f]))>self.settings.max_prompt_chars//4:break
                            selected.append(f)
                        await self._read_region(doc, state, {"id": f"p{number}:audit{round_no}:{index}", "bbox": item["bbox"],
                            "kind": "audit_recovery", "objects": [], "summary": item["reason"],
                            "repair_feedback":{"issue":item["reason"],"candidates":selected,
                                "omitted_count":len(feedback)-len(selected),
                                "instruction":"旧候选可能错误。独立读图修复失败字段或保留不确定；不得抄旧值或降低核验要求。"}}, {}, hints, report)
            except (ContractError, ModelError) as exc:
                state["audit"].append({"round": round_no, "complete": False, "error": str(exc)})
                break
