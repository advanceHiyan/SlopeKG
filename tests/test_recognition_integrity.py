"""Generalized regressions from source review; no private PDF, key or live model."""
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, ProxyHandler, build_opener

from slopekg.jobs import PipelineJobManager
from slopekg.model_client import CompatibleVisionClient, ModelProfile
from slopekg.pipeline import DemoPipeline
from slopekg.recognition import profile_catalog, recognition_documents, recognition_payload, visual_job_options
from slopekg.storage import write_json
from slopekg.visual_contract import REVIEW_CHECKS, ContractError, reading, review_result, resolve_conflicts
from slopekg.visual_pipeline import VisualPipeline, VisualSettings
from slopekg.visual_sources import content_coverage, fact_quality_issues, related_sources
from tests.support import workspace_paths
from tests.test_visual_pipeline import ScriptedModel, make_pdf, source_read


class RecognitionIntegrityTests(unittest.TestCase):
    def test_blind_read_never_receives_initial_answers_and_disagreement_cannot_be_approved(self):
        class Anchored(ScriptedModel):
            def complete(self, **kwargs):
                answer, receipt=super().complete(**kwargs)
                stage, context, _=self.events[-1]
                if stage=='read':
                    if context.get('purpose'):
                        assert not {'candidates','auxiliary_evidence','segmentation_hint','repair_feedback'} & context.keys()
                        answer=source_read(context['target'],value='7.83')
                    else:
                        answer=source_read(context['target'],value='9.83')
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'unnamed.pdf';make_pdf(pdf)
            model=Anchored()
            result=VisualPipeline(model,root/'out',settings=VisualSettings(max_rereads_per_region=0)).run([pdf])
            self.assertEqual(result['summary']['verified_model'],0)
            self.assertTrue(any(f.get('admission_checks',{}).get('independent_source_grounded') is False
                                for f in result['documents'][0]['facts']))
            self.assertTrue(any(c.get('purpose') for s,c,_ in model.events if s=='read'))

    def test_identifier_must_be_supported_even_if_price_and_semantic_flags_are_true(self):
        fact={'object':'material 7812356','scope':'material 7812356','field':'unit price','value':'82.7','unit':'CNY','unit_basis':'kg'}
        self.assertEqual(fact_quality_issues(fact,['material 7812356 kg price 82.7 CNY']),[])
        self.assertIn('object_identifier_not_grounded',fact_quality_issues(fact,['material 7812956 kg price 82.7 CNY']))
        self.assertIn('object_identifier_not_grounded',fact_quality_issues(fact,['7812 | 356 kg 82.7 CNY']))
        fact['object']=fact['scope']='part RX43'
        self.assertIn('object_identifier_not_grounded',fact_quality_issues(fact,['part RX48 kg 82.7 CNY']))

    def test_price_basis_is_explicit_and_cannot_be_borrowed_from_another_unit(self):
        fact={'object':'material','scope':'material','field':'预算单价','value':'82.7','unit':'元'}
        self.assertIn('unit_basis_missing',fact_quality_issues(fact,['material 82.7 元']))
        fact['unit_basis']='m³'
        self.assertIn('unit_basis_not_grounded',fact_quality_issues(fact,['material 82.7 元/m²']))
        self.assertEqual(fact_quality_issues(fact,['material 82.7 元/m³']),[])

    def test_price_basis_conflicts_are_preserved_for_review(self):
        fact={**source_read({'bbox':[0,0,1,1]})['facts'][0],
              'document_id':'d','page':1,'id':'a','status':'verified_model','unit_basis':'kg'}
        alternative={**fact,'id':'b','unit_basis':'t'}
        resolve_conflicts([fact,alternative])
        self.assertEqual([fact['status'],alternative['status']],['pending','pending'])
        self.assertEqual(fact['conflict_id'],alternative['conflict_id'])

    def test_known_contract_alias_is_repaired_but_conflicts_and_nulls_are_not(self):
        verdict={'id':'f','decision':'supported','quote':'source','reason':'visible',**{k:True for k in REVIEW_CHECKS}}
        del verdict['qualifiers_supported'];verdict['qualifier_supported']=True
        repaired=review_result({'verdicts':[verdict]},{'f'})['verdicts'][0]
        self.assertTrue(repaired['qualifiers_supported']);self.assertTrue(repaired['contract_repairs'])
        verdict['qualifiers_supported']=False
        conflicting=review_result({'verdicts':[verdict]},{'f'})['verdicts'][0]
        self.assertFalse(conflicting['qualifiers_supported']);self.assertIn('qualifiers_supported',conflicting['validation_errors'])

    def test_transcribed_cell_or_dimension_is_not_automatically_extracted(self):
        answer=source_read({'bbox':[0,0,1,1]})
        answer['tables']=[{'bbox':[0,0,1,1],'caption':'arbitrary table','cells':[
            {'row':1,'column':2,'row_span':1,'column_span':1,'text':'4.51','header':'height'}]}]
        answer['dimensions']=[{'bbox':[0,0,1,1],'text':'82.6'}]
        reading(answer)
        self.assertTrue(all(not x['accounted'] for x in content_coverage(answer)))
        cell=answer['tables'][0]['cells'][0];cell.update(disposition='fact',fact_indices=[0])
        self.assertTrue(content_coverage(reading(answer))[0]['accounted'])
        cell['text']='74.8'
        self.assertFalse(content_coverage(reading(answer))[0]['accounted'])
        cell['fact_indices']=[999]
        with self.assertRaises(ContractError):reading(answer)

    def test_only_explicit_bounded_cross_page_links_provide_source_text(self):
        def state(page):
            return {'page':page,'regions':[{'id':f'p{page}:r','bbox':[0,0,1,1],
                'reading':{'tables':[],'transcript':f'actual page {page}'}}]}
        report={'pages':[state(n) for n in [1,2,3,9]]}
        assignment={'source_context_regions':[{'region_id':'p1:r','page':1},{'region_id':'p9:r','page':9}]}
        contexts=related_sources(report,2,'p2:r',assignment,2)
        self.assertEqual([c['page'] for c in contexts],[1])

    def test_bounded_windows_reach_review_and_audit_before_later_page_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'new.pdf';make_pdf(pdf,3)
            model=ScriptedModel()
            VisualPipeline(model,root/'out',settings=VisualSettings(window_pages=1)).run([pdf])
            stages=[(s,c.get('page')) for s,c,_ in model.events]
            self.assertLess(stages.index(('audit',1)),stages.index(('read',2)))


class RecognitionServiceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.paths=workspace_paths(Path(temp.name));root=self.paths.root
        (root/'config/models').mkdir(parents=True);(self.paths.raw_dir/'nested').mkdir(parents=True)
        make_pdf(self.paths.raw_dir/'nested/source.pdf')
        write_json(root/'config/models/provider.json',{'name':'test provider','base_url':'https://example.invalid/v1',
            'credential_endpoint':'https://example.invalid/v1','model':'fixture','api_key_file':'.secrets/private-key.txt'})

    def test_http_options_are_local_by_default_and_never_accept_arbitrary_endpoints(self):
        body={'input_pdfs':['nested/source.pdf'],'model_profile':'provider'}
        self.assertTrue(visual_job_options(self.paths,body)['prepare_only'])
        with self.assertRaises(ValueError):visual_job_options(self.paths,{**body,'prepare_only':False})
        allowed=visual_job_options(self.paths,{**body,'prepare_only':False,'allow_external':True})
        self.assertFalse(allowed['prepare_only'])
        for extra in [{'model_profile':'../provider'},{'input_pdfs':['../../outside.pdf']},
                      {'prepare_only':'false'},{'visual_settings':{'retry_failed':True}},
                      {'parse_mode':'deep'},{'visual_settings':{'max_calls':True}}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):visual_job_options(self.paths,{**body,**extra})

    def test_nested_pdf_catalog_and_profile_listing_do_not_read_secrets(self):
        self.assertEqual(recognition_documents(self.paths)['documents'][0]['path'],'nested/source.pdf')
        with patch.object(CompatibleVisionClient,'_key',side_effect=AssertionError('no credentials in listing')):
            catalog=profile_catalog(self.paths)
        self.assertEqual(catalog['profiles'][0]['id'],'provider')
        self.assertNotIn('private-key',json.dumps(catalog))

    def test_local_prepare_uses_no_model_or_legacy_parser(self):
        options=visual_job_options(self.paths,{'input_pdfs':['nested/source.pdf'],'model_profile':'provider'})
        with (patch.object(CompatibleVisionClient,'complete',side_effect=AssertionError('no API')),
              patch.object(DemoPipeline,'parse_documents',side_effect=AssertionError('no legacy parser'))):
            result=DemoPipeline(self.paths).run(**options)
        self.assertEqual(result['processing_status'],'prepared');self.assertEqual(result['summary']['calls'],0)

    def test_legacy_positional_arguments_remain_compatible(self):
        with patch('slopekg.legacy_pipeline.LegacyPipeline.run',return_value={'ok':True}) as legacy:
            self.assertEqual(DemoPipeline(self.paths).run(0,False,parse_mode='basic'),{'ok':True})
        legacy.assert_called_once_with(0,False,parse_mode='basic')
        with self.assertRaises(ValueError):DemoPipeline(self.paths).run(0,recognizer='multimodal')

    def test_real_http_queue_runs_local_preparation_and_exposes_paginated_results(self):
        from slopekg.server import SlopeKGHandler
        class QuietHandler(SlopeKGHandler):
            def log_message(self,*args):pass
        manager=PipelineJobManager(DemoPipeline(self.paths))
        opener=build_opener(ProxyHandler({}))
        with (patch('slopekg.server.PATHS',self.paths),patch('slopekg.server.JOB_MANAGER',manager),
              patch.object(CompatibleVisionClient,'_key',side_effect=AssertionError('local prepare cannot read a key')),
              patch.object(CompatibleVisionClient,'complete',side_effect=AssertionError('no remote calls'))):
            server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            def request(route,body=None):
                encoded=json.dumps(body).encode('utf-8') if body is not None else None
                req=Request(base+route,data=encoded,headers={'Content-Type':'application/json'})
                with opener.open(req,timeout=5) as response:return json.load(response)
            try:
                self.assertEqual(request('/api/recognition/profiles')['profiles'][0]['id'],'provider')
                self.assertEqual(request('/api/recognition/documents')['documents'][0]['path'],'nested/source.pdf')
                job=request('/api/pipeline/jobs',{'recognizer':'multimodal','input_pdfs':['nested/source.pdf'],
                                                'model_profile':'provider'})
                deadline=time.monotonic()+5
                while job['status'] in {'queued','running'} and time.monotonic()<deadline:
                    time.sleep(.02);job=request('/api/pipeline/jobs/'+job['id'])
                self.assertEqual(job['status'],'prepared');self.assertEqual(job['result']['summary']['calls'],0)
                self.assertNotIn('documents',job['result'])
                result=request('/api/recognition?offset=0&limit=1')
                self.assertEqual(result['processing_status'],'prepared');self.assertEqual(result['count'],0)
                self.assertNotIn('private-key',json.dumps(result))
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_result_api_is_paginated_and_does_not_publish_prompts_keys_or_absolute_paths(self):
        fact={**source_read({'bbox':[0,0,1,1]})['facts'][0],'id':'f','document_id':'d','page':1,'status':'pending'}
        write_json(self.paths.output_dir/'recognition/run.json',{'processing_status':'partial',
            'model':{'name':'provider','model':'fixture','api_key_file':'.secrets/private-key.txt'},
            'receipts':[{'private':'not public'}], 'documents':[{'id':'d','path':str(self.paths.raw_dir/'nested/source.pdf'),
            'pages':[],'facts':[fact,{**fact,'id':'g','status':'verified_model'}]}]})
        result=recognition_payload(self.paths,status='pending',limit=1)
        self.assertEqual(result['count'],1);self.assertEqual(result['rows'][0]['source_path'],'nested/source.pdf')
        self.assertIsNone(result['precision']);self.assertNotIn('private-key',json.dumps(result))
        self.assertNotIn(str(self.paths.root),json.dumps(result))

    def test_job_manager_preserves_prepared_and_keeps_heavy_results_out_of_polling(self):
        pipeline=Mock();pipeline.run.return_value={'recognizer':'multimodal','processing_status':'prepared',
            'summary':{'calls':0},'documents':[{'facts':['large']}], 'processing_message':'local only'}
        manager=PipelineJobManager(pipeline)
        with patch('slopekg.jobs.threading.Thread'):
            job,_=manager.start({})
        manager._run(job['id']);result=manager.get(job['id'])
        self.assertEqual(result['status'],'prepared');self.assertNotIn('documents',result['result'])


if __name__=='__main__':unittest.main()
