"""Synthetic mechanism tests. No real recognition/model prediction is claimed."""
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock,patch
from urllib.parse import urlparse
import zipfile

import fitz
from slopekg.standard_assessment import debug_flow
from slopekg.standard_assessment.contract import CONTRACT_VERSION
from slopekg.standard_assessment.debug_http import get_route,post_route
from slopekg.standard_assessment.http import post_route as assessment_post
from slopekg.debug_history import record_dir,checked_artifact
from slopekg.storage import write_json
from tests.support import workspace_paths

ROOT=Path(__file__).resolve().parents[1]


class AssessmentDebugTests(unittest.TestCase):
    def setUp(self):
        temporary_root=ROOT/'.tmp';temporary_root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='assessment-debug-',dir=temporary_root)
        assert Path(self.temp.name).resolve().is_relative_to(temporary_root.resolve())
        self.addCleanup(self.temp.cleanup)
        self.paths=workspace_paths(Path(self.temp.name));self.paths.raw_dir.mkdir(parents=True)
        dest=self.paths.root/'data/standards/GBT40112-2021';dest.mkdir(parents=True)
        shutil.copy2(ROOT/'data/standards/GBT40112-2021/landslide_current.review.json',dest/'landslide_current.review.json')
        self.texts=['虚构软件样例：青山滑坡','调查日：2026-10-01。采用GB/T 40112-2021；整个滑坡体，天然工况。',
                    '发育程度专业结论：弱发育。','本期受威胁人数为50人。','后缘局部裂缝发育。']
        self.pdf=self.paths.raw_dir/'anonymous.pdf';doc=fitz.open();p=doc.new_page()
        for i,text in enumerate(self.texts):p.insert_text((35,50+i*35),text,fontname='china-s',fontsize=10)
        doc.save(self.pdf);doc.close();self.digest=hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        def f(i,field,value):return {'id':'f'+str(i),'entity_id':'s1','object':'青山滑坡','field':field,'value':value,
            'unit':'人' if field=='受威胁人数' else '', 'scope':'青山滑坡整个滑坡体','role':'observation',
            'time':'2026-10-01','condition':'天然工况','status':'source_aligned',
            'evidence':[{'quote':self.texts[i],'page':1,'span_id':'u'+str(i),'source':'pdf_native'}]}
        self.document={'id':'doc1','path':str(self.pdf),'source_sha256':self.digest,'source_page_count':1,'status':'partial',
            'pages':[{'page':1,'status':'native_text'}],
            'units':[{'id':'u'+str(i),'text':text,'page':1,'source':'pdf_native'} for i,text in enumerate(self.texts)],
            'entities':[{'id':'s1','name':'青山滑坡','kind':'entity','aliases':[],'mentions':[{'quote':'青山滑坡','page':1}]}],
            'facts':[f(2,'发育程度','弱发育'),f(3,'受威胁人数','50'),f(4,'后缘裂缝','后缘局部裂缝发育')],
            'relations':[],'chunks':[],'references':[]}
        self.folder=self.paths.output_dir/'knowledge-recognition'
        self.run={'recognizer':'knowledge','processing_status':'partial','documents':[self.document],'receipts':[]}
        write_json(self.folder/'run.json',self.run)
        self.request={'route':'knowledge','run_id':'live','document':'doc1','subject_id':'s1','assessment_date':'2026-10-01','scenario':'natural'}

    def prepare(self):return debug_flow.prepare(self.paths,self.request)

    def test_catalog_is_read_only_and_no_model_or_main_database(self):
        with patch('slopekg.model_client.CompatibleVisionClient.complete',side_effect=AssertionError('unexpected model')):
            c=debug_flow.catalog(self.paths,self.request)
        self.assertEqual(c['subjects'][0]['label'],'青山滑坡');self.assertIn('2026-10-01',c['dates'])
        self.assertFalse((self.paths.output_dir/'assessment-debug').exists())
        self.assertFalse((self.paths.output_dir/'assessment-pilot').exists())

    def test_actual_snapshot_mapping_lookup_and_trace(self):
        with patch('slopekg.model_client.CompatibleVisionClient.complete',side_effect=AssertionError('unexpected model')):
            made=self.prepare();data=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertEqual(made['result']['table17_result']['grade'],'危险性中等')
        self.assertEqual(data['selected_result']['review_status'],'pending')
        self.assertEqual(len(data['stages']),10)
        self.assertTrue(data['case']['recognition_lineage']['source_context']['run_id'].startswith('history-h_'))
        self.assertEqual(data['records']['judgment'][0]['execution_status'],'not_run')
        self.assertTrue(any(f['variable_code']=='observations' for f in data['facts']))
        self.assertIn('assessment_case=',data['recognition_link'])
        self.assertTrue(debug_flow.catalog(self.paths,self.request)['linked_cases'][0]['same_recognition_snapshot'])

    def test_frozen_source_does_not_follow_later_live_predictions(self):
        made=self.prepare();case_id=made['case']['case_id']
        self.document['facts'][1]['value']='500';write_json(self.folder/'run.json',self.run)
        self.assertEqual(debug_flow.flow(self.paths,case_id)['selected_result']['table17_result']['grade'],'危险性中等')
        self.assertFalse(debug_flow.catalog(self.paths,self.request)['linked_cases'][0]['same_recognition_snapshot'])

    def test_reuses_same_history_scope_without_duplicate_case(self):
        made=self.prepare();lineage=made['case']['recognition_lineage']
        again=debug_flow.prepare(self.paths,{**self.request,**lineage['source_context']})
        self.assertTrue(again['reused']);self.assertEqual(again['case']['case_id'],made['case']['case_id'])

    def test_stale_source_hash_unlisted_date_and_wrong_subject_rejected(self):
        for extra in [{'expected_recognition_hash':'wrong'},{'assessment_date':'1999-01-01'},{'subject_id':'invented'}]:
            with self.assertRaises(ValueError):debug_flow.prepare(self.paths,{**self.request,**extra})

    def test_history_tampering_is_rejected_not_silently_rebound(self):
        made=self.prepare();history=made['case']['recognition_lineage']['history_id']
        p=record_dir(self.paths,history)/'prediction/source.pdf';p.write_bytes(p.read_bytes()+b'changed')
        with self.assertRaises((ValueError,OSError)):debug_flow.flow(self.paths,made['case']['case_id'])

    def test_versions_notes_and_old_result_are_separately_readable(self):
        made=self.prepare();api=debug_flow.service(self.paths);first=made['result']
        api.review_assessment({'contract_version':CONTRACT_VERSION,'case_id':made['case']['case_id'],'expected_resolution_version':1,
            'decision':'note','target_id':first['result_id'],'actor_kind':'assistant','reviewer_ref':'synthetic test','reason':'software check only'})
        api.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':made['case']['case_id'],'expected_resolution_version':2})
        old=debug_flow.flow(self.paths,made['case']['case_id'],first['result_id'])
        self.assertEqual(old['selected_result']['resolution_version'],1)
        self.assertEqual(old['case']['resolution_version'],2)
        self.assertFalse(old['case'].get('formal_assessment_enabled',False))
        self.assertTrue(any(r['record_type']=='review' for r in old['records']['versions']))

    def test_export_contains_recognition_and_full_assessment(self):
        made=self.prepare();data=debug_flow.export(self.paths,made['case']['case_id'])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('recognition-history.zip',z.namelist())
            self.assertIn('records',json.loads(z.read('assessment-flow.json')))

    def test_http_paging_and_no_implicit_model_permission(self):
        made=self.prepare();h=Mock();h.read_json_body.return_value={'workspace':'debug','contract_version':CONTRACT_VERSION,
            'case_id':made['case']['case_id'],'expected_resolution_version':1}
        self.assertTrue(assessment_post(h,urlparse('/api/standard-assessment/judge'),self.paths))
        self.assertEqual(h.write_json.call_args.kwargs['status'],400)
        h.reset_mock();self.assertTrue(get_route(h,urlparse('/api/assessment-debug/records?case_id='+made['case']['case_id']+'&stage=mapping&limit=1'),self.paths))
        self.assertEqual(len(h.write_json.call_args.args[0]['rows']),1)
        self.assertGreater(h.write_json.call_args.args[0]['count'],1)

    def test_unknown_unsupported_fields_are_retained_with_rejection_reason(self):
        self.document['facts'].append({'id':'unhandled','field':'arbitrary','value':'hello','object':'青山滑坡','evidence':[]})
        write_json(self.folder/'run.json',self.run);made=self.prepare();data=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertTrue(any(m['id']=='unhandled' and not m['accepted'] for m in data['records']['mapping']))

    def test_visual_object_labels_and_quotes_stay_conditional_without_ocr(self):
        document=copy.deepcopy(self.document);document['entities']=[];document['units']=[]
        write_json(self.paths.output_dir/'recognition/run.json',{**self.run,'recognizer':'visual','documents':[document]})
        source={**self.request,'route':'visual'}
        c=debug_flow.catalog(self.paths,source);source['subject_id']=c['subjects'][0]['id']
        made=debug_flow.prepare(self.paths,source);f=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertEqual(f['selected_result']['basis_status'],'conditional')
        self.assertTrue(all(x['source_check']['content']=='model_supported' for x in f['facts'] if x['origin_kind']=='visual'))

    def test_basic_without_assessable_fields_is_visible_and_does_not_invent_grade(self):
        from slopekg.storage import write_jsonl
        write_json(self.paths.basic_state_json,{'status':'completed'})
        write_jsonl(self.paths.parsed_dir/'documents.jsonl',[self.document])
        source={**self.request,'route':'basic'}
        c=debug_flow.catalog(self.paths,source);source['subject_id']=c['subjects'][0]['id']
        made=debug_flow.prepare(self.paths,source)
        self.assertIsNone(made['result']['table17_result']['grade'])
        trace=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertEqual(trace['records']['scope'][0]['record_type'],'assessment_scope')
        self.assertIn('assessment_scope',trace['records']['scope'][0])

    def test_existing_main_case_is_visible_without_inventing_recognition_history(self):
        made=self.prepare();source_api=debug_flow.service(self.paths);target_api=debug_flow.service(self.paths,'main')
        exported=source_api.store.export();case=copy.deepcopy(made['case']);case['case_id']='legacy-case'
        for k in list(case):
            if k.startswith('debug_') or k=='recognition_lineage':case.pop(k)
        with target_api.store.transaction() as db:
            for table in ['fact_assertion','source_record','evidence_context','identity_binding']:
                for row in exported[table]:target_api.store.put(table,row,db)
            target_api.store.put('assessment_case',case,db)
        target_api.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':'legacy-case','expected_resolution_version':1})
        data=debug_flow.flow(self.paths,'legacy-case',workspace='main')
        self.assertIsNone(data['recognition_link'])
        self.assertTrue(data['records']['raw'])
        self.assertTrue(any(r.get('record_type')=='recognition_history_unbound' for r in data['records']['source']))


if __name__=='__main__':unittest.main()
