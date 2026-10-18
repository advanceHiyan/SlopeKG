"""Offline workflow tests. Fake outputs are NOT recognition quality measurements."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock

import fitz
from PIL import Image

from slopekg.model_client import CompatibleVisionClient, ImageInput, ModelError, ModelProfile, NoRedirect
from slopekg.pipeline import DemoPipeline
from slopekg.storage import read_json, write_json, write_jsonl
from slopekg.visual_contract import (AUDIT, LAYOUT, READ, SEGMENT, VERIFY, ContractError, page_card, review_result, REVIEW_CHECKS,
    bbox, consolidate, reading, resolve_conflicts)
from slopekg.visual_document import ExistingOcrEvidence, VisualDocument
from slopekg.visual_pipeline import VisualPipeline, VisualSettings, pack, audit_units, layout_anchors, verification_batches, normalized, literal_supported, source_contains
from tests.support import workspace_paths


def make_pdf(path: Path, count=1):
    pdf = fitz.open()
    for number in range(count):
        # Different media, dimensions, and page rotation, without source names.
        page = pdf.new_page(width=300 + number * 71, height=500 - number * 19)
        if number % 3 == 0:
            page.insert_text((30, 40), "Object W1 height 4.51 m.")
        elif number % 3 == 1:
            img = Image.new("RGB", (300, 200), "white")
            data = io.BytesIO(); img.save(data, format="PNG")
            page.insert_image(page.rect, stream=data.getvalue())
        else:
            page.draw_rect(fitz.Rect(20, 20, 180, 200))
            page.draw_line((40, 50), (240, 300))
        if number % 2:
            page.set_rotation(90)
    pdf.save(path); pdf.close()


def source_read(target, value="4.51", *, unknown=False):
    quote = f"Object W1 height {value} m."
    return {"transcript": quote, "tables": [], "dimensions": [], "uncertainties": [],
            "facts": [{"object": "unknown" if unknown else "W1", "field": "height", "value": value,
                       "unit": "m", "qualifier": "", "scope": "local", "role": "observation",
                       "condition": "", "time": "", "quote": quote, "bbox": target["bbox"]}]}


class ScriptedModel:
    def __init__(self, *, missed=False, zoom=False, unknown=False, fail=None):
        self.events = []
        self.missed, self.zoom, self.unknown, self.fail = missed, zoom, unknown, fail

    def identity(self):
        return {"name": "offline-fixture", "model": "fixture-v1"}

    def complete(self, *, system, prompt, images):
        instruction, raw = prompt.split("\n输入：", 1)
        context = json.loads(raw)
        stage = {LAYOUT: "layout", SEGMENT: "segment", READ: "read", VERIFY: "verify", AUDIT: "audit"}[instruction]
        self.events.append((stage, copy.deepcopy(context), [i.label for i in images]))
        if self.fail == stage:
            raise RuntimeError("private response body must not be persisted")
        if stage == "layout":
            answer = {"title": "General observations", "summary": "A source page", "blank": False,
                      "regions": [{"id": "r1", "kind": "text", "bbox": [0,0,.5,1] if self.missed else [0,0,1,1],
                                   "summary": "One region", "objects": ["W1"]}], "continuation": ""}
        elif stage == "segment":
            answer = {"assignments": [{"region": r["region"], "subject": "W1", "section": "Observations",
                       "continues_from": "", "reason": "Explicit object label"} for r in context["owned_regions"]]}
        elif stage == "read":
            zoomed = ":zoom" in context["target"]["id"]
            answer = source_read(context["target"], value="4.5" if self.zoom and not zoomed else "4.51", unknown=self.unknown)
            if self.zoom and not zoomed:
                answer["uncertainties"] = [{"bbox": [.1,.1,.9,.9], "reason": "Tiny annotation", "rotation": 90}]
        elif stage == "verify":
            answer = {"verdicts": [{"id": c["id"], "decision": "supported", "quote": c["quote"], "reason": "Visible source",
                       **{k: True for k in ("object_supported", "field_supported", "value_supported", "unit_supported", "scope_supported", "role_supported",
                                           "qualifiers_supported", "condition_supported", "time_supported")}}
                       for c in context["candidates"]]}
        else:
            repaired = any(":audit" in r["id"] for r in context["all_regions"])
            missing = self.missed and not repaired
            answer = {"complete": not missing, "missing_regions": [{"bbox": [.5,0,1,1], "reason": "Omitted right-hand text"}]
                      if missing else [], "issues": []}
        return answer, {"model": "fixture-v1", "usage": {"total_tokens": 0}, "finish_reason": "stop"}


class VisualWorkflowTests(unittest.TestCase):
    def test_cache_only_rebuilds_paid_reads_but_never_sends_missing_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'anonymous.pdf';make_pdf(source)
            model=ScriptedModel();out=root/'out'
            first=VisualPipeline(model,out).run([source]);count=len(model.events)
            rebuilt=VisualPipeline(model,out,settings=VisualSettings(allow_model_calls=False)).run([source])
            self.assertEqual(len(model.events),count)
            self.assertEqual(rebuilt['summary']['verified_model'],first['summary']['verified_model'])
            self.assertEqual(rebuilt['summary']['calls'],0)
            missing=VisualPipeline(model,root/'empty',settings=VisualSettings(allow_model_calls=False)).run([source])
            self.assertEqual(len(model.events),count)
            self.assertEqual(missing['summary']['calls'],0)
            self.assertEqual(missing['summary']['verified_model'],0)
            self.assertEqual(missing['processing_status'],'partial')

    def test_http_429_stops_new_requests_for_same_profile_without_retry_storm(self):
        class Limited(ScriptedModel):
            count=0
            http_status=429
            def complete(self,**kwargs):
                self.count+=1
                exc=ModelError(f'Model HTTP {self.http_status}; not automatically retried')
                exc.receipt={'http_status':self.http_status}
                raise exc
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'anonymous.pdf';make_pdf(source,4)
            for status in (401,402,403,429):
                with self.subTest(status=status):
                    model=Limited();model.http_status=status
                    result=VisualPipeline(model,root/str(status),settings=VisualSettings(model_concurrency=1)).run([source])
                    self.assertEqual(model.count,1)
                    self.assertEqual(result['summary']['calls'],1)
                    self.assertEqual(result['summary']['verified_model'],0)
                    self.assertEqual(result['receipts'][0]['http_status'],status)

    def test_numeric_cells_keep_separators_and_cannot_manufacture_values(self):
        row='Panel A\t1.25\t\t0.75\t2.50'
        for value in ('1.25','0.75','2.50'):
            self.assertTrue(literal_supported(value,row))
        for value,source in [('12','1\t2'),('12','1 2'),('1.2','1 . 2'),('5','- 5'),
                             ('5','−\t5'),('1.25','11.25'),('0.75','0.751'),('25','1.25')]:
            with self.subTest(value=value,source=source):self.assertFalse(literal_supported(value,source))

    def test_dense_anchor_hints_never_overflow_or_change_original_geometry(self):
        anchors=[{'id':f'n{i}','bbox':[.123456789012,.234567890123,.765432109876,.876543210987],
                  'text':'A long optional source hint '*10} for i in range(1000)]
        original=copy.deepcopy(anchors)
        for count in (1,40,1000):
            wire,representation=layout_anchors(anchors[:count],12000)
            self.assertLessEqual(len(json.dumps(wire,separators=(',',':'),ensure_ascii=False)),12000)
            self.assertEqual(len(wire),0 if representation.startswith('image_layout') else count)
        self.assertEqual(anchors,original)

    def test_extreme_geometry_uses_visual_layout_and_does_not_repeat_covered_cells_in_audit(self):
        anchors=[{'id':f'n{i}','bbox':[.1,.1,.2,.2],'source':'pdf_native_line','page':1,'text':'optional hint'} for i in range(1000)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'anonymous.pdf';make_pdf(source)
            model=ScriptedModel()
            with patch.object(VisualDocument,'geometric_evidence',return_value=anchors):
                result=VisualPipeline(model,root/'out').run([source])
            page=result['documents'][0]['pages'][0]
            self.assertEqual(page['layout_anchor_representation'],'image_layout_source_geometry_retained_locally')
            self.assertEqual(len(page['geometric_anchors']),1000)
            self.assertEqual(page['geometrically_uncovered_anchor_ids'],[])
            self.assertEqual(result['processing_status'],'completed')
            layout=next(c for s,c,_ in model.events if s=='layout')
            self.assertEqual(layout['geometric_anchors'],[])
            audits=[c for s,c,_ in model.events if s=='audit']
            self.assertEqual(len(audits),1)

    def test_audit_observes_fact_admission_failures_separately_from_region_status(self):
        fact={**source_read({'bbox':[0,0,1,1]})['facts'][0], 'id':'candidate',
              'status':'pending','reason':'insufficient_source_or_semantic_support',
              'admission_checks':{'explicit_object_scope_role':False}}
        units=audit_units({'id':'r','bbox':[0,0,1,1],'status':'completed'},[fact],12000)
        unit=next(u for u in units if u['part']=='fact')
        self.assertEqual(unit['status'],'completed')
        self.assertEqual(unit['fact_status'],'pending')
        self.assertFalse(unit['admission_checks']['explicit_object_scope_role'])

    def test_audit_recovery_receives_failure_feedback_without_promoting_old_candidate(self):
        class FeedbackRepair(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=='read':
                    if ':audit' in context['target']['id'] and not context.get('purpose'):
                        feedback=context['repair_feedback']['candidates']
                        assert feedback and feedback[0]['candidate']['scope']=='unknown'
                        assert not feedback[0]['checks']['explicit_object_scope_role']
                    elif not context.get('purpose'):answer['facts'][0]['scope']='unknown'
                elif stage=='audit' and not any(':audit' in r['id'] for r in context['all_regions']):
                    answer={'complete':False,'missing_regions':[{'bbox':[0,0,1,1],'reason':'Unresolved assertion scope'}],'issues':[]}
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'anonymous.pdf';make_pdf(source)
            model=FeedbackRepair();result=VisualPipeline(model,root/'out').run([source])
            facts=result['documents'][0]['facts']
            self.assertEqual(sum(f['status']=='verified_model' for f in facts),1)
            self.assertTrue(all(f['status']=='pending' for f in facts if f['scope']=='unknown'))

    def test_quote_repair_is_bounded_and_rechecks_all_guards(self):
        class QuoteRepair(ScriptedModel):
            mode='repair';count=0
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=='verify':
                    self.count+=1
                    if self.count==1 or self.mode=='persistent':
                        for v in answer['verdicts']:v['support_quotes']=['Unseen invented heading']
                    if self.mode=='semantic_failure':
                        for v in answer['verdicts']:v['condition_supported']=False
                    if self.mode=='numeric_failure' and self.count>1:
                        for v in answer['verdicts']:v['quote']='Object W1 height 4.5 m.'
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'anonymous.pdf';make_pdf(source)
            for mode in ('repair','persistent','semantic_failure','numeric_failure'):
                with self.subTest(mode=mode):
                    model=QuoteRepair();model.mode=mode
                    result=VisualPipeline(model,root/mode).run([source])
                    fact=result['documents'][0]['facts'][0]
                    self.assertEqual(model.count,1 if mode=='semantic_failure' else 2)
                    self.assertEqual(fact['status'],'verified_model' if mode=='repair' else 'pending')
                    self.assertEqual(len(fact['review_history']),model.count)
                    if mode!='semantic_failure':
                        self.assertIn('repair_feedback',next(c for s,c,_ in reversed(model.events) if s=='verify'))

    def test_review_batch_budget_counts_shared_evidence_images_once(self):
        facts=[{"id":str(i),"bbox":[0,0,.5,.5]} for i in range(10)]
        self.assertEqual(len(verification_batches(facts,12000,3)),1)
        for i in range(4):facts.append({"id":str(i+10),"bbox":[0,i/10,.9,(i+1)/10]})
        batches=verification_batches(facts,12000,3)
        self.assertEqual([f for batch in batches for f in batch],facts)
        self.assertTrue(all(len({tuple(f['bbox']) for f in batch})<=3 for batch in batches))

    def test_dense_table_audit_preserves_every_cell_and_transcript_tail(self):
        transcript="long source paragraph "*3000+" END OF SOURCE"
        cells=[{"row":r,"column":c,"text":f"cell {r},{c}"} for r in range(80) for c in range(12)]
        region={"id":"r1","bbox":[0,0,1,1],"status":"completed","reading":{
            "transcript":transcript,"tables":[{"caption":"Quantity table","bbox":[0,0,1,1],"cells":cells}]}}
        units=audit_units(region,[],12000)
        shards=pack(units,12000)
        self.assertGreater(len(shards),1)
        self.assertEqual(''.join(u["transcript"] for u in units if u["part"]=="transcript"),transcript)
        self.assertEqual([u["cell"] for u in units if u["part"]=="table_cell"],cells)
        self.assertTrue(all(len(json.dumps(s,ensure_ascii=False,separators=(',',':')))<=12000 for s in shards))

    def test_concurrency_overlaps_models_but_never_renders_pdf_in_worker_threads(self):
        owner = threading.get_ident()
        original = VisualDocument.render
        seen_threads = []
        def render(doc, *args, **kwargs):
            seen_threads.append(threading.get_ident())
            return original(doc, *args, **kwargs)
        class SlowModel(ScriptedModel):
            def complete(self, **kwargs):
                time.sleep(.2)
                return super().complete(**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"x.pdf"; make_pdf(source,5)
            with patch.object(VisualDocument,"render",render):
                result=VisualPipeline(SlowModel(),root/"result",settings=VisualSettings(model_concurrency=4)).run([source])
            self.assertEqual(result["processing_status"],"completed")
            self.assertGreater(result["summary"]["peak_concurrency"],1)
            self.assertLessEqual(result["summary"]["peak_concurrency"],4)
            self.assertEqual(set(seen_threads),{owner})

    def test_concurrent_requests_cannot_overspend_the_run_call_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"x.pdf"; make_pdf(source,5); model=ScriptedModel()
            result=VisualPipeline(model,root/"result",settings=VisualSettings(model_concurrency=4,max_calls=3)).run([source])
            self.assertEqual(len(model.events),3)
            self.assertEqual(result["summary"]["calls"],3)
            self.assertEqual(result["processing_status"],"partial")
            self.assertEqual(len(result["documents"][0]["pages"]),5)

    def test_coarse_boundary_error_preserves_candidate_and_expands_from_source(self):
        class CoarseBoundary(ScriptedModel):
            def complete(self, **kwargs):
                answer, receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=="layout": answer["regions"][0]["bbox"]=[0,0,.5,1]
                if stage=="read": answer["facts"][0]["bbox"]=[.4,.2,.6,.4]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"x.pdf"; make_pdf(source)
            # Keep the fallback where no reliable geometry was found. Native
            # source-cell guards now repair this crop before the reading stage.
            with patch.object(VisualDocument,"geometric_evidence",return_value=[]):
                result=VisualPipeline(CoarseBoundary(),root/"result").run([source])
            state=result["documents"][0]["pages"][0]
            self.assertEqual(result["processing_status"],"completed")
            self.assertTrue(state["regions"][0]["boundary_issues"])
            self.assertGreater(state["regions"][1]["bbox"][2],.5)
            fact=result["documents"][0]["facts"][0]
            self.assertEqual(fact["status"],"verified_model")
            self.assertEqual(len(fact["evidence"]),2)

    def test_expanding_for_outside_note_does_not_revoke_independently_verified_inside_fact(self):
        class OutsideNote(ScriptedModel):
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=="layout":answer['regions'][0]['bbox']=[0,0,.5,1]
                if stage=="read" and ':zoom' not in context['target']['id']:
                    answer['dimensions']=[{'text':'Outside note','bbox':[.7,.1,.9,.2],
                        'disposition':'reference_only','reason':'Non-engineering annotation; keep transcription'}]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'x.pdf';make_pdf(source,2)
            result=VisualPipeline(OutsideNote(),root/'out').run([source],page_selections={str(source):[2]})
            fact=result['documents'][0]['facts'][0]
            self.assertEqual(fact['status'],'verified_model')
            self.assertTrue(all(e['status']=='verified_model' for e in fact['evidence']))

    def test_test_page_selection_preserves_original_physical_page_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"x.pdf"; make_pdf(source,5)
            result=VisualPipeline(ScriptedModel(),root/"result").run([source],page_selections={str(source):[2,4]})
            doc=result["documents"][0]
            self.assertEqual(doc["source_page_count"],5)
            self.assertEqual(doc["scope"],"selected_pages_only")
            self.assertEqual([p["page"] for p in doc["pages"]],[2,4])

    def test_wrong_unit_cannot_pass_even_if_the_reviewer_approves_it(self):
        class WrongUnit(ScriptedModel):
            def complete(self, **kwargs):
                answer, receipt = super().complete(**kwargs)
                if self.events[-1][0] == "read":
                    answer["facts"][0]["unit"] = "m³"  # source says m
                return answer, receipt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source)
            result = VisualPipeline(WrongUnit(), root / "result").run([source])
            self.assertEqual(result["summary"]["verified_model"],0)
            self.assertEqual(result["summary"]["pending"],1)

    def test_all_semantic_checks_must_pass_including_qualifiers_and_conditions(self):
        class MissingQualifier(ScriptedModel):
            failed_check = "qualifiers_supported"
            def complete(self, **kwargs):
                answer, receipt = super().complete(**kwargs)
                if self.events[-1][0] == "verify":
                    answer["verdicts"][0][self.failed_check] = False
                return answer, receipt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source)
            for check in ("qualifiers_supported","field_supported","unit_supported","condition_supported"):
                model = MissingQualifier(); model.failed_check = check
                with self.subTest(check=check):
                    result = VisualPipeline(model, root / check).run([source])
                    self.assertEqual(result["summary"]["verified_model"],0)

    def test_segmentation_cannot_bind_a_region_to_a_future_region(self):
        class FutureBoundary(ScriptedModel):
            def complete(self, **kwargs):
                answer, receipt = super().complete(**kwargs)
                if self.events[-1][0] == "segment":
                    assignments = answer["assignments"]
                    assignments[0]["continues_from"] = assignments[-1]["region"]
                return answer, receipt
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source,2)
            result = VisualPipeline(FutureBoundary(), root / "result").run([source])
            self.assertEqual(result["processing_status"],"partial")
            self.assertTrue(any("earlier region" in e for p in result["documents"][0]["pages"] for e in p["errors"]))

    def test_reviewer_can_use_a_separate_provider_without_changing_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source)
            reader, reviewer = ScriptedModel(), ScriptedModel()
            result = VisualPipeline(reader, root / "result", review_model=reviewer).run([source])
            self.assertEqual(result["processing_status"], "completed")
            self.assertEqual([stage for stage,_,_ in reader.events],["layout","segment","read"])
            self.assertEqual([stage for stage,_,_ in reviewer.events],["read","verify","audit"])

    def test_small_image_budget_scales_images_instead_of_dropping_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source, 2)
            result = VisualPipeline(ScriptedModel(), root / "result", settings=VisualSettings(max_image_pixels=1000000)).run([source])
            self.assertEqual(result["processing_status"], "completed")

    def test_table_evidence_can_span_value_unit_header_and_note(self):
        class TableEvidence(ScriptedModel):
            wrong_unit=False
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                instruction=kwargs["prompt"].split("\n输入：",1)[0]
                if instruction==READ:
                    answer["transcript"]="Wall W1\nVolume\nm³\n125\nEstimated quantity"
                    fact=answer["facts"][0]
                    fact.update(object="Wall W1",field="Volume",value="125",unit="m²" if self.wrong_unit else "m³",
                                qualifier="Estimated",quote="125",support_quotes=["Wall W1","Volume","m³","Estimated quantity"])
                elif instruction==VERIFY:
                    for v in answer["verdicts"]: v.update(quote="125",support_quotes=["Wall W1","Volume","m³","Estimated quantity"])
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/"x.pdf"; make_pdf(source)
            for wrong in (False,True):
                model=TableEvidence(); model.wrong_unit=wrong
                result=VisualPipeline(model,root/str(wrong)).run([source])
                self.assertEqual(result["summary"]["verified_model"],0 if wrong else 1)

    def test_extra_review_quote_requires_a_real_read_region_on_same_page(self):
        class SharedHeading(ScriptedModel):
            invented=False
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,context,_=self.events[-1]
                if stage=='layout':
                    answer['regions']=[{'id':'heading','kind':'text','bbox':[0,0,1,.1],'summary':'heading','objects':[]},
                                       {'id':'body','kind':'text','bbox':[0,.1,1,1],'summary':'body','objects':['W1']}]
                elif stage=='read' and context['target']['id'].endswith(':heading'):
                    answer.update(transcript='Independent heading',facts=[])
                elif stage=='verify':
                    for v in answer['verdicts']:v['support_quotes']=['Unseen invented heading' if self.invented else 'Independent heading']
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'x.pdf';make_pdf(source,2)
            for invented in (False,True):
                model=SharedHeading();model.invented=invented
                result=VisualPipeline(model,root/str(invented)).run([source],page_selections={str(source):[2]})
                self.assertEqual(result['summary']['verified_model'],0 if invented else 1)
                if not invented:self.assertTrue(result['documents'][0]['facts'][0]['support_context'])

    def test_all_page_types_use_same_route_bounded_windows_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "anonymous.pdf"; make_pdf(source, 5)
            model = ScriptedModel(); settings = VisualSettings(window_pages=2)
            pipeline = VisualPipeline(model, root / "results", settings=settings)
            result = pipeline.run([source])
            self.assertEqual(result["processing_status"], "completed")
            self.assertEqual([p["page"] for p in result["documents"][0]["pages"]], [1,2,3,4,5])
            self.assertEqual(result["summary"]["verified_model"], 5)  # No model-proven cross-page entity link.
            self.assertEqual(len(result["documents"][0]["facts"][0]["evidence"]), 1)
            self.assertIsNone(result["precision"]); self.assertIsNone(result["recall"])
            segments = [c for stage,c,_ in model.events if stage == "segment"]
            self.assertEqual([len(c["owned_regions"]) for c in segments], [2,2,1])
            self.assertEqual(segments[1]["context_regions"][0]["region"], "p2:r1")
            self.assertEqual(segments[0]["context_regions"][-1]["region"], "p3:r1")
            self.assertTrue(segments[1]["previous_boundary"])
            for stage, context, images in model.events:
                if stage == "read":
                    self.assertLessEqual(len(images), 3)
                    self.assertNotIn("anonymous.pdf", json.dumps(context))
            count = len(model.events)
            resumed = pipeline.run([source])
            self.assertEqual(len(model.events), count)
            self.assertEqual(resumed["summary"]["calls"], 0)
            self.assertGreater(resumed["summary"]["cache_hits"], 0)
            self.assertTrue((root / "results" / "coverage.json").is_file())
            self.assertEqual(len((root / "results" / "verified_facts.jsonl").read_text().splitlines()), 5)

    def test_model_detected_missing_region_is_read_and_audited_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "unseen.pdf"; make_pdf(source)
            model = ScriptedModel(missed=True)
            result = VisualPipeline(model, root / "result").run([source])
            state = result["documents"][0]["pages"][0]
            self.assertEqual(result["processing_status"], "completed")
            self.assertEqual([a["complete"] for a in state["audit"]], [False, True])
            self.assertTrue(any(r["id"].startswith("p1:audit") for r in state["regions"]))
            evidence = result["documents"][0]["facts"][0]["evidence"]
            self.assertGreaterEqual(len(evidence), 2)
            self.assertTrue(any(":audit" in e["region_id"] for e in evidence))

    def test_uncertainty_uses_source_zoom_rotation_and_retains_initial_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "vector.pdf"; make_pdf(source)
            model = ScriptedModel(zoom=True)
            result = VisualPipeline(model, root / "result").run([source])
            facts = result["documents"][0]["facts"]
            self.assertEqual({f["value"]: f["status"] for f in facts}, {"4.5": "pending", "4.51": "verified_model"})
            zooms = [images for stage,c,images in model.events if stage == "read" and ":zoom" in c["target"]["id"] and not c.get("purpose")]
            self.assertEqual(len(zooms), 1)
            self.assertIn("clockwise_rotation=90", zooms[0][-1])

    def test_unknown_subject_stays_pending_despite_model_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source)
            result = VisualPipeline(ScriptedModel(unknown=True), root / "result").run([source])
            self.assertEqual(result["summary"]["verified_model"], 0)
            self.assertEqual(result["summary"]["pending"], 1)

    def test_budget_failure_tracks_every_page_and_does_not_claim_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source, 3)
            model = ScriptedModel()
            result = VisualPipeline(model, root / "result", settings=VisualSettings(max_calls=1)).run([source])
            self.assertEqual(len(model.events), 1)
            self.assertEqual(result["processing_status"], "partial")
            self.assertEqual(len(result["documents"][0]["pages"]), 3)
            self.assertTrue(all(p["status"] == "partial" for p in result["documents"][0]["pages"]))

    def test_failure_does_not_rebill_on_resume_or_leak_transport_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source)
            model = ScriptedModel(fail="read"); pipeline = VisualPipeline(model, root / "result")
            first = pipeline.run([source]); count = len(model.events)
            second = pipeline.run([source])
            self.assertEqual(first["processing_status"], "partial")
            self.assertEqual(second["processing_status"], "partial")
            self.assertEqual(len(model.events), count)
            files = list((root / "result").rglob("*.json"))
            self.assertFalse(any("private response body" in p.read_text(encoding="utf8") for p in files))
            recovered = VisualPipeline(ScriptedModel(), root / "result", settings=VisualSettings(retry_failed=True)).run([source])
            self.assertEqual(recovered["processing_status"], "completed")
            self.assertTrue(any(len(read_json(p).get("attempts", [])) == 2 for p in (root / "result/cache").glob("*.json")))

    def test_source_change_invalidates_cache_and_mid_run_changes_invalidate_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source)
            model = ScriptedModel(); pipeline = VisualPipeline(model, root / "result")
            pipeline.run([source]); first = len(model.events)
            pdf = fitz.open(source); pdf[0].draw_rect(fitz.Rect(10,100,40,140)); pdf.saveIncr(); pdf.close()
            pipeline.run([source]); self.assertGreater(len(model.events), first)
            original = VisualDocument._hash
            calls = []
            def changed(doc):
                calls.append(1)
                return original(doc) if len(calls) == 1 else "changed"
            with patch.object(VisualDocument, "_hash", changed):
                invalid = VisualPipeline(ScriptedModel(), root / "other").run([source])
            self.assertEqual(invalid["processing_status"], "failed")
            self.assertTrue(all(f["status"] == "pending" for f in invalid["documents"][0]["facts"]))

    def test_route_prepare_only_reads_no_key_ocr_or_legacy_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); paths = workspace_paths(root); source = root / "x.pdf"; make_pdf(source, 2)
            profile = root / "profile.json"
            write_json(profile, {"name":"test", "base_url":"https://example.invalid/v1", "credential_endpoint":"https://example.invalid/v1",
                                 "model":"test", "api_key_file":".secrets/absent.txt"})
            with patch.object(CompatibleVisionClient, "complete", side_effect=AssertionError("No remote call")), \
                    patch.object(CompatibleVisionClient, "_key", side_effect=AssertionError("No key read")), \
                    patch.object(DemoPipeline, "parse_documents", side_effect=AssertionError("No legacy parser")):
                state = DemoPipeline(paths).run(recognizer="multimodal", input_pdfs=[source], model_profile=profile, prepare_only=True)
            self.assertEqual(state["processing_status"], "prepared")
            self.assertEqual(state["summary"]["pages"], 2)
            self.assertFalse(paths.graph_json.exists())
            self.assertFalse(paths.state_json.exists())
            with self.assertRaises(ValueError):
                DemoPipeline(paths).run(recognizer="multimodal", input_pdfs=[source], model_profile=profile, parse_mode="deep")
            with self.assertRaises(ValueError):
                DemoPipeline(paths).run(model_profile=profile)


class EvidenceContractTests(unittest.TestCase):
    def test_source_alignment_preserves_digits_units_and_literal_formula_bars(self):
        self.assertTrue(source_contains('平均高度0.8m，坡率1:1.5','平均高度0.8 m，坡率1：1.5'))
        self.assertTrue(literal_supported('平均高度0.8m，坡率1：1.5','平均高度0.8 m，坡率1:1.5'))
        self.assertTrue(source_contains('Row 1 0.8 m³','Row | 1 | 0.8 | m³',table=True))
        self.assertTrue(source_contains('Row A ｜ … ｜146.2 ｜75.2','Row A | label |146.2 |75.2',table=True))
        self.assertFalse(source_contains('Row A ｜ … ｜9','Row A | 1\nRow B | 9',table=True))
        self.assertFalse(source_contains('Row A ｜ … ｜9 (invented note)','Row A | 9',table=True))
        for quote,source,table in [('12','1 | 2',True),('4.5','4.51',False),('5','-5',False),
                                   ('m²','m³',True),('m','mm',True),('m','m²',True),
                                   ('m','m/s',True),('|x|','x',False)]:
            with self.subTest(quote=quote,source=source):self.assertFalse(source_contains(quote,source,table=table))

    def test_model_table_groups_keep_headers_data_and_totals_together(self):
        answer={"title":"","summary":"","blank":False,"regions":[
            {"id":"header","kind":"table","table_id":"t1","bbox":[.1,.1,.9,.3],"objects":[],"summary":"headers and units"},
            {"id":"data","kind":"table","table_id":"t1","bbox":[.1,.3,.9,.7],"objects":[],"summary":"rows"},
            {"id":"another","kind":"table","table_id":"t2","bbox":[.1,.8,.9,.9],"objects":[],"summary":"another table"}]}
        result=page_card(answer,1,padding=.01)
        self.assertEqual(len(result['regions']),2)
        first=result['regions'][0]
        self.assertEqual(first['source_region_ids'],['header','data'])
        self.assertAlmostEqual(first['bbox'][3],.71)
        snapshot=copy.deepcopy(first['bbox'])
        self.assertEqual(page_card(result,1,padding=.01)['regions'][0]['bbox'],snapshot)

    def test_one_null_review_flag_quarantines_only_that_fact(self):
        base={"decision":"supported","quote":"source","reason":"visible",**{key:True for key in REVIEW_CHECKS}}
        parsed=review_result({"verdicts":[{**base,"id":"good"},{**base,"id":"bad","qualifiers_supported":None}]},{"good","bad"})
        by_id={v["id"]:v for v in parsed["verdicts"]}
        self.assertTrue(all(by_id["good"][key] for key in REVIEW_CHECKS))
        self.assertFalse(by_id["bad"]["qualifiers_supported"])
        self.assertEqual(by_id["bad"]["validation_errors"],["qualifiers_supported"])

    def test_model_groups_source_geometry_and_padding_is_idempotent_on_cache_read(self):
        anchors=[{"id":"a","bbox":[.1,.1,.2,.2]},{"id":"b","bbox":[.1,.22,.5,.3]}]
        raw={"title":"","summary":"","blank":False,"regions":[{"id":"r","kind":"text","objects":[],
            "summary":"a heading and its paragraph","bbox":[.1,.1,.2,.2],"anchor_ids":["a","b"]}]}
        card=page_card(raw,1,anchors,.01)
        self.assertAlmostEqual(card["regions"][0]["bbox"][2],.51)
        self.assertEqual(card,page_card(copy.deepcopy(card),1,anchors,.01))

    def test_repeated_subject_phrases_do_not_merge_or_conflict_across_unlinked_sections(self):
        base=source_read({"bbox":[0,0,1,1]})["facts"][0]
        a={**base,"id":"a","document_id":"d","object":"该边坡","page":1,"region_id":"r1","section_scope":"chapter1",
           "status":"verified_model","reason":"ok"}
        b={**a,"id":"b","page":2,"region_id":"r2","section_scope":"chapter2"}
        self.assertEqual(len(consolidate([a,b])),2)
        b["value"]="8";resolve_conflicts([a,b])
        self.assertEqual((a["status"],b["status"]),("verified_model","verified_model"))
    def test_numeric_and_unit_substrings_cannot_certify_a_different_value(self):
        for value, quote, unit in [("4.5","4.51 m",False),("5","15 m",False),("5","-5 m",False),
                                   ("m","25 m²",True),("m²","25 m³",True),("m","25 mm",True),("m","25 m/s",True)]:
            with self.subTest(value=value,quote=quote): self.assertFalse(literal_supported(value,quote,unit=unit))
        for value, quote, unit in [("4.51","4.51 m.",False),("3–5","约3–5 m",False),("m²","25 m²。",True),
                                   ("kN/m²","25 kN/m²。",True),("25","25 m²。",False),("m","4.51 m high",True)]:
            with self.subTest(value=value,quote=quote): self.assertTrue(literal_supported(value,quote,unit=unit))

    def test_pack_preserves_all_items_and_rejects_an_oversize_item(self):
        items = [{"n": n, "text": "some source content"} for n in range(100)]
        self.assertEqual([i for group in pack(items, 300) for i in group], items)
        with self.assertRaises(ContractError): pack([{"text": "x" * 1000}], 100)

    def test_boxes_finite_ordered_normalized_not_boolean_or_fixed_pdf_coordinates(self):
        for invalid in ([0,0,600,590], [0,0,float("nan"),1], [0,0,False,1], [0,0,0,1], [1,0,0,1]):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError): bbox(invalid)
        self.assertEqual(bbox([.1,.2,.4,.9]), [.1,.2,.4,.9])

    def test_unit_superscripts_and_table_structure_survive_contract(self):
        self.assertNotEqual(normalized("m²"), normalized("m³"))
        answer = source_read({"bbox": [0,0,1,1]})
        answer["tables"] = [{"caption":"", "bbox":[0,0,1,1], "cells":[
            {"row":0,"column":0,"row_span":1,"column_span":2,"header":"type","text":""}]}]
        self.assertIs(reading(answer), answer)
        answer["tables"][0]["cells"][0]["column_span"] = 0
        with self.assertRaises(ContractError): reading(answer)

    def test_conflicts_preserve_scope_condition_and_all_alternatives(self):
        base = source_read({"bbox": [0,0,1,1]})["facts"][0]
        a = {**base,"id":"a","document_id":"d","page":1,"region_id":"r1","status":"verified_model","reason":"ok"}
        b = {**a,"id":"b","value":"4.5"}
        c = {**b,"id":"c","condition":"rainfall"}
        facts = [a,b,c]; resolve_conflicts(facts)
        self.assertEqual([f["status"] for f in facts], ["pending","pending","verified_model"])
        duplicate = consolidate([{**a,"section_scope":"verified_shared_section"},{**a,"id":"d","page":2,"section_scope":"verified_shared_section"}])
        self.assertEqual(len(duplicate),1); self.assertEqual(len(duplicate[0]["evidence"]),2)

    def test_real_source_crop_increases_resolution_and_preserves_coordinate_system(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source, 2)
            doc = VisualDocument(source, root / "work")
            try:
                info = doc.page_info(2)
                self.assertEqual(info["original_rotation"], 90)
                small = doc.render(2, edge=800)
                large = doc.render(2, [.1,.1,.3,.3], edge=2000, rotation=90, max_pixels=3000000)
                self.assertGreater(max(large.width,large.height),max(small.width,small.height))
                self.assertLessEqual(large.width * large.height, 3010000)
                self.assertIn("bbox=[0.1, 0.1, 0.3, 0.3]",large.label)
            finally: doc.close()

    def test_stale_ocr_rows_cannot_borrow_current_task_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "x.pdf"; make_pdf(source)
            doc = VisualDocument(source, root / "work")
            try:
                write_jsonl(root/"parsed/documents.jsonl",[{"id":"d","source_sha256":doc.sha256}])
                write_jsonl(root/"parsed/ocr_tasks.jsonl",[{"id":"t","source_sha256":doc.sha256}])
                stale = {"document_id":"d","page":1,"task_id":"t","text":"wrong","source_sha256":"stale"}
                write_jsonl(root/"parsed/ocr_results.jsonl",[stale])
                self.assertEqual(ExistingOcrEvidence(root/"parsed")(doc,1)["rows"],[])
                write_jsonl(root/"parsed/ocr_results.jsonl",[{**stale,"source_sha256":doc.sha256}])
                self.assertEqual(ExistingOcrEvidence(root/"parsed")(doc,1)["status"],"available")
            finally: doc.close()


class ModelTransportTests(unittest.TestCase):
    def test_http_failure_has_a_receipt_and_never_retries_or_redirects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/".secrets").mkdir(); (root/".secrets/key.txt").write_text("offline-test-value")
            profile = ModelProfile("x","https://example.invalid/v1","m","https://example.invalid/v1",api_key_file=".secrets/key.txt")
            opener = Mock(); opener.open.side_effect = urllib.error.HTTPError("https://example.invalid",429,"limited",None,None)
            with patch("slopekg.model_client.urllib.request.build_opener",return_value=opener):
                with self.assertRaises(ModelError) as caught:
                    CompatibleVisionClient(profile,root).complete(system="",prompt="",images=[])
            self.assertEqual(caught.exception.receipt["http_status"],429)
            self.assertEqual(opener.open.call_count,1)
            with self.assertRaises(ModelError): NoRedirect().redirect_request(None,None,302,"",{},"https://elsewhere.invalid")

    def test_invalid_completed_json_retains_usage_for_billing_audit(self):
        profile = ModelProfile("x","https://example.invalid","m","https://example.invalid",api_key_file=".secrets/key.txt")
        client = CompatibleVisionClient(profile,Path.cwd(),lambda _: {"usage":{"total_tokens":42},
            "choices":[{"finish_reason":"stop","message":{"content":"broken json"}}]})
        with self.assertRaises(ModelError) as caught:
            client.complete(system="",prompt="",images=[])
        self.assertEqual(caught.exception.receipt["usage"]["total_tokens"],42)

    def test_cli_no_llm_cannot_accidentally_start_a_visual_model_call(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable,str(root/"scripts/run_pipeline.py"),"--recognizer","multimodal","--no-llm"],
                                capture_output=True,text=True,encoding="utf-8",cwd=root)
        self.assertEqual(result.returncode,2)
        self.assertIn("--prepare-only",result.stderr)

    def test_provider_profiles_keep_their_distinct_thinking_and_token_controls(self):
        root = Path(__file__).resolve().parents[1]
        for provider in ("deepseek","qwen","kimi"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                profile = ModelProfile.load(root / f"config/models/{provider}.json")
                captured = []
                def transport(payload):
                    captured.append(payload)
                    return {"model":profile.model,"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}
                image_path = Path(directory)/"image.png"; Image.new("RGB",(10,10)).save(image_path)
                client = CompatibleVisionClient(profile, Path(directory), transport)
                with patch.object(client,"_key",side_effect=AssertionError("Offline transport must not read keys")):
                    client.complete(system="rules",prompt="read",images=[ImageInput(image_path,"page=1",10,10)])
                payload = captured[0]
                self.assertEqual(payload[profile.token_parameter],131072)
                self.assertEqual(payload.get("reasoning_effort"),"xhigh" if provider=="qwen" else "max")
                self.assertNotIn("api_key_file",json.dumps(payload))
                if provider=="deepseek": self.assertEqual(payload["thinking"],{"type":"enabled"})
                if provider=="qwen": self.assertTrue(payload["enable_thinking"])

    def test_incomplete_response_is_failure_and_profile_refuses_endpoint_mismatch(self):
        with self.assertRaises(ValueError):
            ModelProfile("x","https://example.invalid","m","https://different.invalid",api_key_file=".secrets/key.txt")
        profile = ModelProfile("x","https://example.invalid","m","https://example.invalid",api_key_file=".secrets/key.txt")
        client = CompatibleVisionClient(profile,Path.cwd(),lambda _: {"choices":[{"finish_reason":"length","message":{"content":"{}"}}]})
        with self.assertRaises(ModelError): client.complete(system="",prompt="",images=[])

    def test_key_source_never_falls_back_or_escapes_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = ModelProfile("x","https://example.invalid","m","https://example.invalid",api_key_file=".secrets/missing.txt")
            with patch.dict("os.environ",{"DEEPSEEK_API_KEY":"other-account"}):
                with self.assertRaises(ModelError): CompatibleVisionClient(profile,root)._key()
            bad = ModelProfile("x","https://example.invalid","m","https://example.invalid",api_key_file="outside.txt")
            with self.assertRaises(ModelError): CompatibleVisionClient(bad,root)._key()


if __name__ == "__main__":
    unittest.main()
