"""Data-intake integration tests; fixtures are explicitly synthetic."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import test_assessment_debug_flow as fixtures
from slopekg.standard_assessment import debug_flow, agent_bridge
from slopekg.standard_assessment.contract import enumeration, number
from slopekg.storage import write_json
from slopekg.debug_history import record_dir


class AgentAssessmentTests(unittest.TestCase):
    # Reuse setup only, not the old route's test assertions.
    def setUp(self):
        fixtures.AssessmentDebugTests.setUp(self)
        self.agent_root=self.paths.output_dir/'agent-recognition/runs/agent-test-1'
        self.agent_root.mkdir(parents=True)
        facts=[]
        for f in self.document['facts']:
            facts.append({'id':f['id'],'document_id':'doc1','object':'s1','parent_object':'','field':'自由命名，不依赖关键词',
                'value':f['value'],'unit':f['unit'],'condition':'','role':'observed','status':'pending','model_status':'ok',
                'evidence':{'physical_page':1,'quote':f['evidence'][0]['quote'],'locator':'测试页'},'uncertainty':''})
        self.agent_run={'run_id':'agent-test-1','recognizer':'agent','processing_status':'completed','run_directory':str(self.agent_root),
            'documents':[{'id':'doc1','name':self.pdf.name,'path':str(self.pdf),'source_sha256':self.digest,
                'selection':{'selected_pages':[1],'source_pages':1},'objects':[{'id':'s1','document_id':'doc1','name':'对象甲','type':'body'}],
                'facts':facts}]}
        self.proposal={'schema_version':agent_bridge.SUBMISSION_VERSION,'cases':[{
            'id':'c1','document_id':'doc1','object_id':'s1','label':'Agent自主案例',
            'applicability':{'landslide':True,'reason':'原文滑坡体','basis_fact_ids':['f2']},
            'time':{'kind':'instant','start':'2026-10-01','end':'2026-10-01','raw':'调查日','basis_fact_ids':['f2']},
            'scenario':{'code':'natural','raw':'天然工况'},
            'inputs':[{'id':'i1','variable':'development','value':enumeration('development','弱发育'),
                       'fact_ids':['f2'],'semantic':'reported_judgment','source_standard':'GB/T 40112-2021'},
                      {'id':'i2','variable':'threatened_people','value':number(50,'person'),'fact_ids':['f3']}],
            'analysis':'自主分析原文','gaps':['缺现场复核'],'conflicts':[], 'extra':{'自由扩展':'保留'}}]}
        self.req={'route':'agent','run_id':'agent-test-1','document':'doc1','subject_id':'c1'}
        self.persist()

    def persist(self, submission=True):
        write_json(self.agent_root/'run.json',self.agent_run)
        write_json(self.paths.output_dir/'agent-recognition/run.json',self.agent_run)
        write_json(self.agent_root/'workspace/manifest.json',{'documents':[]})
        p=self.agent_root/'workspace/outputs/assessment.json'
        if submission: write_json(p,self.proposal)
        elif p.exists(): p.unlink()

    def test_agent_intake_without_field_classifier_or_new_model(self):
        with patch('slopekg.standard_assessment.knowledge_bridge.candidate',side_effect=AssertionError('no reclassification')),patch('slopekg.model_client.CompatibleVisionClient.complete',side_effect=AssertionError('no model')):
            made=debug_flow.prepare(self.paths,self.req)
        flow=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertEqual(flow['selected_result']['table17_result']['grade'],'危险性中等')
        self.assertFalse(flow['selected_result']['formal_assessment_enabled'])
        self.assertEqual(flow['selected_result']['review_status'],'pending')
        self.assertTrue(all(f['basis_status']=='conditional' for f in flow['facts'] if f['origin_kind']=='agent'))
        self.assertEqual(flow['case']['agent_proposal']['extra'],{'自由扩展':'保留'})
        self.assertTrue(any(r['record_type']=='agent_gaps' for r in flow['records']['gaps']))
        self.assertIn('pdf-ingestion.html',flow['recognition_link'])

    def test_agent_submit_is_idempotent_and_frozen(self):
        first=agent_bridge.submit(self.paths,self.req)
        second=agent_bridge.submit(self.paths,self.req)
        self.assertEqual(first['cases'],second['cases']);self.assertTrue(second['reused'])
        self.proposal['cases'][0]['inputs'][1]['value']=number(500,'person');self.persist()
        third=agent_bridge.submit(self.paths,self.req)
        self.assertNotEqual(first['cases'][0]['case_id'],third['cases'][0]['case_id'])
        old=debug_flow.flow(self.paths,first['cases'][0]['case_id'])
        self.assertEqual(old['selected_result']['table17_result']['grade'],'危险性中等')

    def test_agent_task_receipt_is_retryable_without_restarting_model(self):
        from slopekg.agent_service import intake_assessments,agent_payload
        with patch('slopekg.agent_pipeline.AgentPipeline.run',side_effect=AssertionError('must not restart')):
            first=intake_assessments(self.paths,'agent-test-1')
            second=intake_assessments(self.paths,'agent-test-1')
        self.assertEqual(first['status'],'imported');self.assertEqual(first['cases'],second['cases'])
        payload=agent_payload(self.paths,run_id='agent-test-1')
        self.assertEqual(payload['assessment']['cases'],first['cases'])

    def test_agent_bad_refs_units_and_authority_rejected_before_write(self):
        for change in [lambda c:c['inputs'][0].update(fact_ids=['foreign']),lambda c:c['inputs'][1]['value'].update(value=-1),
                       lambda c:c['time'].update(start=None),lambda c:c['inputs'][0].update(status='accepted')]:
            proposal=copy.deepcopy(self.proposal);change(proposal['cases'][0])
            with self.assertRaises(ValueError):agent_bridge.submit(self.paths,{**self.req,'submission':proposal})
        self.assertFalse((self.paths.output_dir/'assessment-debug').exists())

    def test_agent_conflicts_and_old_dates_do_not_become_current_values(self):
        self.agent_run['documents'][0]['facts'][0]['model_status']='conflict';self.persist()
        result=debug_flow.prepare(self.paths,self.req)['result']
        self.assertIsNone(result['table17_result']['grade'])
        self.agent_run['documents'][0]['facts'][0]['model_status']='ok'
        self.proposal['cases'][0]['inputs'][1]['scope']={'time':{'kind':'instant','start':'2020-01-01','end':'2020-01-01','raw':'历史','basis_fact_ids':['f3']}}
        self.persist();result=debug_flow.prepare(self.paths,self.req)['result']
        self.assertIsNone(result['table17_result']['grade'])
        dims=result['resolved_variables']['threatened_people']['applicability_by_fact'][0]['dimensions']
        self.assertEqual(dims['time'],'incompatible')

    def test_agent_legacy_raw_facts_are_accepted_with_explicit_gap(self):
        self.persist(False)
        made=debug_flow.prepare(self.paths,{**self.req,'subject_id':'s1'})
        flow=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertEqual(len(flow['records']['raw']),3)
        self.assertEqual(flow['selected_result']['workflow_status'],'invalid_request')
        self.assertIsNone(flow['selected_result']['table17_result']['grade'])
        self.assertTrue(flow['case']['agent_proposal']['gaps'])

    def test_agent_unknown_coefficients_remain_unknown_and_do_not_crash(self):
        self.proposal['cases'][0]['inputs'].extend([
            {'id':'unknown_fs','variable':'fs','value':None,'status':'unknown','fact_ids':['f4']},
            {'id':'unknown_fst','variable':'fst','value':None,'status':'unknown','fact_ids':['f4']}])
        self.persist();made=debug_flow.prepare(self.paths,self.req)
        flow=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertTrue(all(f['value'] is None for f in flow['facts'] if f['variable_code'] in {'fs','fst'}))

    def test_agent_equivalent_units_and_null_objects_are_received_losslessly(self):
        self.proposal['cases'][0]['inputs'][1]['value']['unit']='人'
        self.proposal['cases'][0]['inputs'].append({'id':'fs_unknown','variable':'fs','value':{'kind':'null'},'status':'unknown','fact_ids':['f4']})
        self.persist();made=debug_flow.prepare(self.paths,self.req)
        flow=debug_flow.flow(self.paths,made['case']['case_id'])
        f=next(f for f in flow['facts'] if f['variable_code']=='threatened_people')
        self.assertEqual(f['value']['unit'],'person');self.assertEqual(f['normalization_receipt']['original_value']['unit'],'人')
        self.assertEqual(made['result']['table17_result']['grade'],'危险性中等')
        value,_,_=agent_bridge.normalized_value('direct_loss_wan',{'kind':'number','value':50000,'unit':'元'})
        self.assertEqual(value['value'],5)
        self.proposal['cases'][0]['inputs'][1]['value']['unit']='m';self.persist()
        made=debug_flow.prepare(self.paths,self.req)
        self.assertIsNone(made['result']['table17_result']['grade'])

    def test_agent_missing_input_needs_no_invented_source_and_scope_uses_dates(self):
        self.proposal['cases'][0]['inputs'][0]['role']='judgment'
        self.proposal['cases'][0]['inputs'][0]['scope']={'time':{'kind':'instant','start':'2026-10-01','end':'2026-10-01'},
            'scenario':{'code':'natural','raw':'同一个天然工况的不同文字'}}
        self.proposal['cases'][0]['inputs'].append({'id':'not_reported','variable':'fs','value':{'kind':'null'},'status':'unknown','fact_ids':[]})
        self.persist();made=debug_flow.prepare(self.paths,self.req)
        flow=debug_flow.flow(self.paths,made['case']['case_id'])
        self.assertEqual(made['result']['table17_result']['grade'],'危险性中等')
        fact=next(f for f in flow['facts'] if f['variable_code']=='fs')
        self.assertEqual(fact['availability'],'explicit_unknown')
        self.assertEqual(flow['contexts'][-1]['spans'],[])

    def test_agent_text_scope_is_preserved_without_assuming_current_scope(self):
        row=self.proposal['cases'][0]['inputs'][1]
        row['scope']='监测点甲，历史暴雨期；不是滑坡整体'
        self.proposal['cases'][0]['inputs'][0]['scope']=None
        self.persist();made=debug_flow.prepare(self.paths,self.req)
        flow=debug_flow.flow(self.paths,made['case']['case_id'])
        fact=next(f for f in flow['facts'] if f['variable_code']=='threatened_people')
        self.assertEqual(fact['scope']['agent_scope_text'],row['scope'])
        self.assertIsNone(fact['scope']['subject']['source_object_ref'])
        self.assertIsNone(made['result']['table17_result']['grade'])
        self.assertEqual(flow['case']['agent_proposal'],self.proposal['cases'][0])
        self.assertIn('范围为文字描述',flow['case']['debug_mapping'][1]['source_support_note'])

    def test_agent_http_submission_and_frozen_source_tamper(self):
        from slopekg.standard_assessment.debug_http import post_route
        from unittest.mock import Mock
        from urllib.parse import urlparse
        handler=Mock();handler.read_json_body.return_value={**self.req,'submission':self.proposal}
        self.assertTrue(post_route(handler,urlparse('/api/assessment-debug/agent-submit'),self.paths))
        receipt=handler.write_json.call_args.args[0];self.assertEqual(len(receipt['cases']),1)
        path=record_dir(self.paths,receipt['history_id'])/'prediction/source.pdf';path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises((ValueError,OSError)):debug_flow.flow(self.paths,receipt['cases'][0]['case_id'])


