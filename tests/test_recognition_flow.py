"""Anonymous offline flow regressions; no project document or gold answers."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fitz

from slopekg.knowledge_document import prepare_document, wire_chunks
from slopekg.knowledge_pipeline import wire_units
from slopekg.knowledge_composition import source_accounting
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.model_client import CompatibleVisionClient, ModelError, ModelProfile
from slopekg.visual_batching import verification_batches
from slopekg.visual_contract import REVIEW_CHECKS
from slopekg.visual_planning import plan_window
from slopekg.visual_review import VisualReviewer
from slopekg.visual_settings import VisualSettings
from slopekg.visual_sources import content_coverage, fact_source_bindings, fact_quality_issues


def source_fixture(root):
    pdf=root/'anonymous.pdf';doc=fitz.open()
    for _ in range(2):doc.new_page(width=400,height=300)
    doc.save(pdf);doc.close()
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    pages=[{'id':f'p{p}','page':p,'width':400,'height':300,'image_count':1,
            'drawing_count':0,'page_type':'drawing'} for p in (1,2)]
    tasks=[{'id':f'p{p}_ocr_title_block','page_id':f'p{p}','document_id':'d','page':p,
            'source_sha256':digest,'region':'title_block','status':'done','items':1,
            'file_name':pdf.name,'path':str(pdf)} for p in (1,2)]
    rows=[{'id':f'o{p}','task_id':tasks[p-1]['id'],'document_id':'d','page':p,
           'source_sha256':digest,'text':'27','bbox':[20,30,40,50],'confidence':.99} for p in (1,2)]
    return pdf,{'documents':[{'id':'d','pages':2}],'pages':pages,'text_blocks':[],
                'tables':[],'ocr_tasks':tasks,'ocr_results':rows}


class KnowledgeFlowTests(unittest.TestCase):
    def test_completed_title_crop_keeps_a_full_page_gap_and_preserves_wire_geometry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pdf,parsed=source_fixture(root)
            with patch('slopekg.knowledge_document.basic_sources',return_value=(parsed,'test')):
                doc=prepare_document(pdf,KnowledgeSettings(),root=root,selected_pages=[1])
            self.assertEqual(doc['source_inventory']['ocr_tasks_pending'],1)
            self.assertEqual(doc['pages'][0]['ocr_regions_processed'],['title_block'])
            self.assertTrue(doc['pages'][0]['full_page_ocr_pending'])
            units={u['id']:u for u in doc['units']}
            self.assertTrue(all('bbox' in u for u in wire_chunks(doc['chunks'],units)[0]['spans']))
            self.assertEqual(wire_units(units,list(units))[0]['bbox'],[.05,.1,.1,round(1/6,6)])

    def test_local_full_page_ocr_is_bounded_and_overlapping_crops_do_not_duplicate_values(self):
        class Runner:
            def __init__(self):self.calls=[]
            def run(self,tasks,max_pages):
                self.calls.append((copy.deepcopy(tasks),max_pages))
                pending=[t for t in tasks if t['status']!='done']
                assert len(pending)==1 and pending[0]['region']=='full_page' and max_pages==1
                fresh=pending[0];fresh['status']='done'
                def row(i,box):return {'id':i,'task_id':fresh['id'],'document_id':'d','page':1,
                    'source_sha256':fresh['source_sha256'],'text':'27','bbox':box,'confidence':.99}
                return tasks,[row('full1',[20,30,40,50]),row('full2',[200,150,220,170])]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pdf,parsed=source_fixture(root);runner=Runner()
            with patch('slopekg.knowledge_document.basic_sources',return_value=(parsed,'test')):
                doc=prepare_document(pdf,KnowledgeSettings(ocr_pages=1),root=root,selected_pages=[1],
                    run_ocr=True,ocr_runner=runner,ocr_cache_dir=root/'cache')
            self.assertEqual(len(runner.calls),1)
            self.assertFalse(doc['pages'][0]['full_page_ocr_pending'])
            self.assertEqual(len(doc['units']),2)  # same value, two distinct source positions
            self.assertEqual(doc['source_inventory']['duplicate_ocr_rows'],1)
            with patch('slopekg.knowledge_document.basic_sources',return_value=(parsed,'test')):
                again=prepare_document(pdf,KnowledgeSettings(ocr_pages=1),root=root,selected_pages=[1],
                    run_ocr=True,ocr_runner=runner,ocr_cache_dir=root/'cache')
            self.assertEqual(len(runner.calls),1)
            self.assertEqual(doc['units'],again['units'])

    def test_unassigned_page_is_visible_without_forcing_numbers_to_an_object(self):
        doc={'pages':[{'page':1,'graphics_unread':True},{'page':2}],
            'units':[{'id':'a','page':1},{'id':'b','page':1},{'id':'c','page':2}],
            'entities':[{'id':'obj','unit_ids':['c']}],'references':[],
            'facts':[{'evidence':[{'span_id':'c'}]}]}
        before=copy.deepcopy(doc);accounting=source_accounting(doc)
        self.assertEqual(accounting['pages_without_attribution'],[1])
        self.assertEqual(accounting['pages'][0]['unassigned_unit_ids'],['a','b'])
        self.assertTrue(accounting['pages'][0]['needs_visual_context'])
        self.assertEqual(doc,before)


class VisualFlowTests(unittest.TestCase):
    def test_shared_source_review_uses_text_capacity_instead_of_twelve_fact_limit(self):
        facts=[{'id':str(i),'bbox':[0,0,1,1]} for i in range(70)]
        batches=verification_batches(facts,12000,0,shared_region=True)
        self.assertEqual(batches,[facts])
        bounded=verification_batches(facts,600,0,shared_region=True)
        self.assertEqual([f for b in bounded for f in b],facts)
        self.assertTrue(all(len(json.dumps(b,separators=(',',':')))<=600 for b in bounded))

    def test_small_budget_coalesces_page_source_and_retains_every_original_region(self):
        def state(page,count):return {'page':page,'card':{'regions':[
            {'id':f'r{i}','bbox':[0,i/count,1,(i+1)/count],'kind':'text','objects':[f'item{i}']}
            for i in range(count)]}}
        states=[state(1,4),state(2,5)];original=copy.deepcopy(states)
        plan=plan_window(states,22,independent_review=True)
        self.assertEqual(plan['primary_regions'],2)
        self.assertTrue(plan['minimum_cycle_fits'])
        for before,after in zip(original,states):
            self.assertEqual(after['original_layout_regions'],before['card']['regions'])
            self.assertEqual(after['card']['regions'][0]['source_regions'],before['card']['regions'])
            self.assertEqual(after['card']['regions'][0]['bbox'],[0,0,1,1])
        self.assertFalse(plan_window(states,2,independent_review=True)['minimum_cycle_fits'])

    def test_numeric_label_is_not_complete_measurement_even_when_review_says_supported(self):
        fact={'id':'f','object':'member','field':'size label','value':'27','unit':'',
              'qualifier':'','scope':'member','role':'design','condition':'','time':'',
              'quote':'27','bbox':[0,0,1,1]}
        dimension={'text':'27','bbox':[0,0,1,1],'fact_indices':[0],'disposition':'fact'}
        answer={'facts':[fact],'tables':[],'dimensions':[dimension]}
        fact['source_bindings']=fact_source_bindings(answer)[0]
        self.assertFalse(content_coverage(answer)[0]['accounted'])
        self.assertIn('dimension_meaning_unresolved',fact_quality_issues(fact,['27']))
        async def supported(*args):
            return {'verdicts':[{'id':'f','decision':'supported','quote':'27','reason':'numeric label seen',
                                **{k:True for k in REVIEW_CHECKS}}]}
        reviewer=VisualReviewer(VisualSettings(independent_review=False),supported)
        asyncio.run(reviewer.review(SimpleNamespace(id='d',sha256='sha'),1,
            {'id':'r','bbox':[0,0,1,1]},[fact],'27',[],{}))
        self.assertEqual(fact['status'],'pending')
        self.assertFalse(fact['admission_checks']['field_quality_complete'])
        dimension['measurement']={'object':'member','quantity':'width','basis':'between indicated left and right faces'}
        fact['field']='width';fact['source_bindings']=fact_source_bindings(answer)[0]
        self.assertTrue(content_coverage(answer)[0]['accounted'])
        self.assertNotIn('dimension_meaning_unresolved',fact_quality_issues(fact,['27']))
        fact['field']='size label'
        self.assertIn('dimension_field_not_recomposed',fact_quality_issues(fact,['27']))


class JsonDiagnosticTests(unittest.TestCase):
    def test_completed_invalid_json_has_safe_shape_and_parser_diagnostics(self):
        profile=ModelProfile(name='offline',base_url='https://api.deepseek.com',
            credential_endpoint='https://api.deepseek.com',model='deepseek-flash',api_key_file='.secrets/unused.txt')
        for body in ['private-source {',[],None,'[]']:
            with self.subTest(body_type=type(body).__name__):
                result={'id':'receipt','usage':{'total_tokens':2},'choices':[
                    {'finish_reason':'stop','message':{'content':body}}]}
                client=CompatibleVisionClient(profile,Path('.'),transport=lambda payload:result)
                with self.assertRaises(ModelError) as caught:client.complete(system='test',prompt='test',images=[])
                receipt=caught.exception.receipt
                self.assertEqual(receipt['failure_kind'],'invalid_json_contract')
                self.assertEqual(receipt['response_diagnostic']['content_type'],type(body).__name__)
                self.assertNotIn('private-source',str(caught.exception)+json.dumps(receipt))
                self.assertEqual(receipt['requested_max_output_tokens'],393216)
                if body=='private-source {':
                    self.assertEqual(receipt['response_diagnostic']['json_error_position'],0)


if __name__=='__main__':unittest.main()
