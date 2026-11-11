"""Lifecycle tests use genuine local PDFs and isolated databases; no model calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import fitz

from slopekg import slope_library as lib
from slopekg.agent_gateway import dump
from slopekg.agent_pipeline import validate_outputs
from slopekg.pipeline import DemoPipeline
from tests.support import workspace_paths


class SlopeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = workspace_paths(Path(self.temp.name))
        self.paths.raw_dir.mkdir(parents=True)

    def run_data(self, filename='survey.pdf', route='agent', run_id='run-1'):
        path = self.paths.raw_dir / filename
        pdf = fitz.open();page = pdf.new_page();page.insert_text((30, 40), filename+' height 10 m');pdf.save(path);pdf.close()
        return {'recognizer': route, 'run_id': run_id, 'processing_status': 'completed', 'documents': [{
            'id': 'd1', 'path': str(path), 'source_sha256': lib.file_hash(path),
            'objects': [{'id': 'o1', 'document_id': 'd1', 'name': '测试边坡', 'type': '边坡'}],
            'facts': [{'id': 'f1', 'document_id': 'd1', 'object': 'o1', 'field': '坡高', 'value': 10,
                       'unit': 'm', 'role': 'observed', 'status': 'ok',
                       'evidence': {'physical_page': 1, 'quote': 'height 10 m', 'locator': 'body'}}]}]}

    def confirm_first(self, name='测试边坡'):
        item=lib.catalog(self.paths)['unassigned'][0]
        return lib.bind(self.paths,{'binding_id':item['id'],'revision':item['revision'],'name':name,
            'actor':'tester','reason':'checked original identity','confirmed':True})['slope']

    def test_rerun_and_refresh_do_not_duplicate_or_depend_on_assessment(self):
        run = self.run_data()
        with patch('slopekg.standard_assessment.agent_bridge.validate_submission', side_effect=AssertionError('No assessment')):
            a=lib.ingest(self.paths,run);b=lib.ingest(self.paths,run)
        self.assertEqual(a['snapshots'], b['snapshots'])
        catalog=lib.catalog(self.paths)
        self.assertEqual(len(catalog['slopes']), 0)
        self.assertEqual(len(catalog['snapshots']), 1)
        slope=self.confirm_first()
        d=lib.detail(self.paths,slope_id=slope['id'])
        self.assertEqual(d['sources'][0]['versions'][0]['object']['facts'][0]['admission'], 'candidate')
        run['run_id']='run-2';run['documents'][0]['facts'][0]['value']=11
        lib.ingest(self.paths,run)
        self.assertEqual(len(lib.catalog(self.paths)['slopes']),1)
        self.assertEqual(len(lib.detail(self.paths,slope_id=slope['id'])['sources'][0]['versions']),2)

    def test_cross_document_identity_requires_confirmation_and_keeps_history(self):
        lib.ingest(self.paths,self.run_data())
        slope=self.confirm_first()
        lib.ingest(self.paths,self.run_data('design.pdf',run_id='run-2'))
        catalog=lib.catalog(self.paths);self.assertEqual(len(catalog['slopes']),1)
        source=catalog['unassigned'][0]
        body={'binding_id':source['id'],'revision':0,'slope_id':slope['id'],'target_revision':1,
              'actor':'tester','reason':'matched source identity','confirmed':True}
        with self.assertRaises(ValueError):lib.bind(self.paths,{**body,'confirmed':False})
        result=lib.bind(self.paths,body)
        self.assertEqual(len(result['sources']),2)
        self.assertEqual(sum(len(s['history']) for s in result['sources']),2)
        with self.assertRaises(ValueError):lib.bind(self.paths,body)

    def test_changed_source_blocks_new_import_but_archived_original_survives(self):
        run=self.run_data();lib.ingest(self.paths,run)
        doc=run['documents'][0];Path(doc['path']).write_bytes(b'changed')
        self.assertEqual(lib.ingest(self.paths,run)['status'],'partial')
        self.assertTrue(lib.page_image(self.paths,doc['source_sha256'],1).startswith(b'\x89PNG'))
        with self.assertRaises(ValueError):lib.source_file(self.paths,'../../private')

    def test_cross_document_and_out_of_scope_evidence_is_withheld(self):
        run=self.run_data();doc=run['documents'][0]
        doc['facts'][0]['evidence']['document_id']='foreign'
        doc['facts'][0]['evidence']['physical_page']=2
        lib.ingest(self.paths,run);source=lib.catalog(self.paths)['unassigned'][0]
        fact=lib.detail(self.paths,binding_id=source['id'])['sources'][0]['versions'][0]['object']['facts'][0]
        self.assertEqual(fact['admission'],'withheld');self.assertEqual(len(fact['issues']),2)

    def test_document_visual_and_knowledge_objects_are_adapted(self):
        for route in ('visual','document','knowledge'):
            with self.subTest(route=route):
                run=self.run_data(route+'.pdf',route=route)
                doc=run['documents'][0];doc.pop('objects')
                doc['facts'][0]['object']='另一边坡'
                if route=='knowledge':
                    doc['entities']=[{'id':'e1','name':'另一边坡','kind':'entity'}]
                    doc['facts'][0]['entity_id']='e1'
                self.assertEqual(lib.ingest(self.paths,run)['status'],'stored')
        self.assertEqual(len(lib.catalog(self.paths)['slopes']),0)
        self.assertEqual(len(lib.catalog(self.paths)['unassigned']),3)

    def test_slope_under_project_keeps_child_scope_and_photo_is_not_a_slope(self):
        run=self.run_data();doc=run['documents'][0]
        doc['objects'][0]['parent_object']='project'
        doc['objects'] += [{'id':'project','name':'调查项目','type':'工程'},
                           {'id':'part','name':'坡脚','type':'部位','parent_object':'o1'},
                           {'id':'case','name':'危岩体天然工况计算','type':'calculation_case','parent_object':'o1'},
                           {'id':'concept','name':'岩质边坡成因分类','type':'concept'},
                           {'id':'photo','name':'图5-1 测试边坡现场照','type':'照片'}]
        doc['facts'].append({**doc['facts'][0],'id':'f2','object':'part','field':'坡脚现状'})
        lib.ingest(self.paths,run);catalog=lib.catalog(self.paths)
        self.assertEqual(len(catalog['slopes']),0)
        pending=next(s for s in catalog['unassigned'] if s['label']=='测试边坡')
        detail=lib.detail(self.paths,binding_id=pending['id'])
        self.assertEqual(len(detail['sources']),3)
        self.assertIn('坡脚',[s['label'] for s in detail['sources']])
        self.assertEqual(len(catalog['unassigned']),4)
        root=next(s for s in detail['sources'] if s['label']=='测试边坡')
        moved=lib.bind(self.paths,{'binding_id':root['id'],'revision':0,'name':'核对后的边坡',
            'confirmed':True,'actor':'tester','reason':'same surveyed slope','include_related':True,
            'related_revisions':{s['id']:s['revision'] for s in detail['sources']}})
        self.assertEqual(len(moved['sources']),3)
        self.assertTrue(all(s['history'][0]['related_objects']==3 for s in moved['sources']))

    def test_figure_only_source_remains_visible_and_crop_is_source_backed(self):
        run=self.run_data();doc=run['documents'][0];doc.update(objects=[],facts=[],
            archived_figures=[{'page':1,'bbox':[0,0,.5,.5],'caption':'photo'}])
        receipt=lib.ingest(self.paths,run)
        self.assertEqual(len(lib.catalog(self.paths)['unassigned']),1)
        image=lib.figure_image(self.paths,receipt['snapshots'][0]['id'],0)
        self.assertTrue(image.startswith(b'\x89PNG'))

    def test_dispatch_stores_partial_results_and_prepare_does_not_store(self):
        run=self.run_data();run['processing_status']='partial'
        pipeline=DemoPipeline(self.paths)
        with patch.object(pipeline,'_recognize',side_effect=lambda *a, **k: deepcopy(run)):
            self.assertEqual(pipeline.run(recognizer='agent')['slope_library']['status'],'stored')
            self.assertNotIn('slope_library',pipeline.run(recognizer='agent',prepare_only=True))

    def test_basic_recognition_does_not_execute_or_overwrite_risk_assessment(self):
        self.run_data()
        risk=self.paths.output_dir/'risk/screening.json'
        dump(risk,{'historical_result':'keep'})
        with (patch('slopekg.legacy_pipeline.assess_graph',side_effect=AssertionError('No assessment')),
              patch('slopekg.legacy_pipeline.export_risk_standalone_html',side_effect=AssertionError('No risk export'))):
            result=DemoPipeline(self.paths).run(ocr_pages=0)
        self.assertEqual(result['assessment_status'],'not_requested')
        self.assertEqual(result['slope_library']['status'],'stored')
        self.assertIn('keep',risk.read_text(encoding='utf-8'))

    def test_new_agent_delivery_needs_no_assessment_and_accepts_archived_only_page(self):
        workspace=Path(self.temp.name)/'workspace'
        manifest={'workflow':'recognition_to_library','documents':[{'document_id':'d','selected_physical_pages':[1],
            'files':[{'path':'inputs/p1.png','kind':'source_page_image','physical_page':1}]}]}
        dump(workspace/'outputs/objects.json',{'objects':[]})
        (workspace/'outputs/facts.jsonl').write_text('',encoding='utf-8')
        (workspace/'outputs/report.md').write_text('Images archived',encoding='utf-8')
        dump(workspace/'outputs/coverage.json',{'pages':[{'document_id':'d','physical_page':1,'status':'complete','remaining':[]}]})
        dump(workspace/'outputs/figures.json',{'figures':[{'document_id':'d','page':1,'bbox':[0,0,1,1]}]})
        calls=[{'tool':'view_image','status':'ok','args':{'path':'inputs/p1.png'}}]
        checked=validate_outputs(workspace,manifest,calls)
        self.assertFalse(checked['errors']);self.assertEqual(len(checked['figures']),1)


if __name__ == '__main__':unittest.main()
