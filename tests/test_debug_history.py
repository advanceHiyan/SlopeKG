"""Open synthetic checkpoint execution and durable evidence; no model requests."""
from dataclasses import replace
import hashlib
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

import fitz

from slopekg.debug_datasets import save_dataset, get_dataset, execution_pages, dataset_hash
from slopekg.debug_history import (save_snapshot, record_dir, record_detail, append_review,
    compare_records, export_record, get_record, list_records, create_record)
from slopekg.debug_tests import start_test
from slopekg.recognition_debug import debug_records, debug_call, debug_source, debug_catalog, debug_image
from slopekg.model_client import ModelError
from slopekg.storage import write_json, write_jsonl, read_json
from slopekg.server import SlopeKGHandler
from slopekg.jobs import PipelineJobManager
from tests.support import workspace_paths


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.paths=workspace_paths(Path(self.temp.name));self.paths.raw_dir.mkdir(parents=True)
        self.pdf=self.paths.raw_dir/'anonymous.pdf';pdf=fitz.open();pdf.new_page();pdf.new_page();pdf.save(self.pdf);pdf.close()
        self.sha=hashlib.sha256(self.pdf.read_bytes()).hexdigest();self.folder=self.paths.output_dir/'knowledge-recognition';self.key='a'*64
        self.doc={'id':'doc','path':str(self.pdf),'source_sha256':self.sha,'source_page_count':2,'status':'partial',
            'pages':[{'page':1,'status':'native_text'},{'page':2,'status':'native_text'}],
            'units':[{'id':'u1','page':1,'source':'ocr','text':'Height 12m','bbox':[.1,.2,.4,.3]}],
            'chunks':[{'id':'c1','pages':[1],'unit_ids':['u1']}],
            'facts':[{'id':'f1','field':'height','value':'12','unit':'m','status':'source_aligned','page':1,
                      'evidence':[{'page':1,'quote':'Height 12m','span_id':'u1'}]}], 'relations':[]}
        self.run={'recognizer':'knowledge','processing_status':'partial','documents':[self.doc],
                  'receipts':[{'cache_key':self.key,'stage':'read'}]}
        self.save_run()
        write_json(self.folder/'inputs'/f'{self.key}.json',{'prompt':'FULL INPUT','system':'SYSTEM','document_id':'doc'})
        write_json(self.folder/'cache'/f'{self.key}.json',{'status':'completed','answer':{'raw':'full response'}})
        self.source={'route':'knowledge','run_id':'live','document':'doc'}
        self.dataset=save_dataset(self.paths,{'name':'Anonymous checks','source':self.source,'open_development':True,'points':[
            {'id':'p1','label':'Height','pages':[1],'expected':{'value':'12'},'checks':[{'field':'value','op':'equals','value':'12'}]},
            {'id':'p2','label':'Other height','pages':[2],'expected':{'value':'99'},'checks':[{'field':'value','op':'equals','value':'99'}]}]})

    def save_run(self):write_json(self.folder/'run.json',self.run)

    def test_method_filter_is_applied_before_pagination_and_validated(self):
        for route in ('basic','knowledge','visual','knowledge'):
            create_record(self.paths,label='Same document',route=route,mode='snapshot',definition={})
        data=list_records(self.paths,route='knowledge',limit=1,offset=1)
        self.assertEqual(data['count'],2)
        self.assertEqual([r['route'] for r in data['records']],['knowledge'])
        self.assertEqual(list_records(self.paths,route='basic',q='Same')['count'],1)
        with self.assertRaises(ValueError):list_records(self.paths,route='unknown')

    def run_check(self,points=None,**kwargs):
        return start_test(self.paths,{'dataset_id':self.dataset['id'],'point_ids':points or ['p1'],
            'label':'Synthetic selected checks',**kwargs},background=False)

    def test_selected_checkpoints_are_saved_with_complete_independent_evidence(self):
        record=self.run_check();self.assertEqual(record['status'],'completed')
        detail=record_detail(self.paths,record['id']);self.assertEqual([r['point_id'] for r in detail['results']['rows']],['p1'])
        self.assertEqual(detail['results']['rows'][0]['rule_status'],'matched')
        self.assertFalse(detail['definition']['answer_is_model_input'])
        self.pdf.unlink();write_json(self.folder/'run.json',{'documents':[]})
        source=debug_source(self.paths,route='knowledge',run_id='history-'+record['id'],document='doc')
        self.assertTrue(source.is_file())
        rows=debug_records(self.paths,route='knowledge',run_id='history-'+record['id'],document='doc',stage='facts')
        self.assertEqual(rows['rows'][0]['value'],'12')
        call=debug_call(self.paths,route='knowledge',run_id='history-'+record['id'],document='doc',key=self.key)
        self.assertEqual(call['input']['prompt'],'FULL INPUT')
        self.assertEqual(call['cache']['answer']['raw'],'full response')

    def test_hash_mismatch_rejects_corrupted_saved_input_and_pdf(self):
        record=self.run_check();folder=record_dir(self.paths,record['id'])/'prediction'
        write_json(folder/'inputs'/f'{self.key}.json',{'prompt':'tampered'})
        with self.assertRaises(OSError):debug_call(self.paths,route='knowledge',run_id='history-'+record['id'],document='doc',key=self.key)
        (folder/'source.pdf').write_bytes(b'tampered PDF')
        with self.assertRaises(OSError):debug_source(self.paths,route='knowledge',run_id='history-'+record['id'],document='doc')

    def test_reviews_append_and_do_not_change_frozen_machine_results(self):
        record=self.run_check();folder=record_dir(self.paths,record['id']);before=(folder/'results.json').read_bytes()
        append_review(self.paths,record['id'],point_id='p1',verdict='pending',note='Check source')
        detail=append_review(self.paths,record['id'],point_id='p1',verdict='pass',note='Source checked',label='Renamed',tags=['geometry'])
        self.assertEqual([r['verdict'] for r in detail['reviews']],['pending','pass'])
        self.assertEqual(detail['record']['label'],'Renamed');self.assertEqual((folder/'results.json').read_bytes(),before)
        self.assertEqual(list_records(self.paths,q='geometry')['count'],1)
        with self.assertRaises(ValueError):append_review(self.paths,record['id'],point_id='another',verdict='pass',note='bad')

    def test_zip_contains_prediction_source_full_inputs_definition_and_reviews(self):
        record=self.run_check();append_review(self.paths,record['id'],note='Preserve note')
        bundle=zipfile.ZipFile(io.BytesIO(export_record(self.paths,record['id'])))
        for key in ['record.json','definition.json','results.json','reviews.json','manifest.json','prediction/source.pdf',
                    f'prediction/inputs/{self.key}.json',f'prediction/cache/{self.key}.json']:
            self.assertIn(key,bundle.namelist())
        self.assertNotIn('.pipeline.lock',bundle.namelist())

    def test_saved_request_images_survive_original_output_removal(self):
        image_bytes=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=')
        path=self.folder/'images/image.png';path.parent.mkdir();path.write_bytes(image_bytes)
        write_json(self.folder/'inputs'/f'{self.key}.json',{'document_id':'doc','prompt':'INPUT',
            'images':[{'path':str(path),'label':'page=1','sha256':hashlib.sha256(image_bytes).hexdigest()}]})
        record=self.run_check();path.unlink()
        saved=debug_image(self.paths,route='knowledge',run_id='history-'+record['id'],document='doc',key=self.key,index=0)
        self.assertEqual(saved.read_bytes(),image_bytes)

    def test_comparison_uses_same_dataset_and_preserves_old_results(self):
        first=self.run_check();self.doc['facts'][0]['value']='9';self.save_run();second=self.run_check()
        comparison=compare_records(self.paths,first['id'],second['id'])
        self.assertTrue(comparison['same_dataset_version']);self.assertEqual(comparison['changed_points'],1)
        self.assertEqual(comparison['rows'][0]['before']['rule_status'],'matched')
        self.assertEqual(comparison['rows'][0]['after']['rule_status'],'unmatched')

    def test_snapshot_copy_rejects_source_replaced_after_initial_validation(self):
        import shutil
        original_copy=shutil.copy2
        replaced=False
        def copy_with_replacement(src,dst,*args,**kwargs):
            nonlocal replaced
            if Path(src)==self.pdf and not replaced:
                self.pdf.write_bytes(b'different source after validation')
                replaced=True
            return original_copy(src,dst,*args,**kwargs)
        with patch('slopekg.debug_history.shutil.copy2',side_effect=copy_with_replacement):
            with self.assertRaises(ValueError):
                save_snapshot(self.paths,**self.source,label='Concurrent replacement')
        self.assertFalse(list_records(self.paths)['records'][0]['snapshot_ready'])

    def test_two_process_snapshots_do_not_claim_equal_test_datasets(self):
        first=save_snapshot(self.paths,**self.source,label='Before process')
        self.doc['facts'][0]['value']='99';self.save_run()
        second=save_snapshot(self.paths,**self.source,label='After process')
        result=compare_records(self.paths,first['id'],second['id'])
        self.assertFalse(result['same_dataset_version'])
        self.assertFalse(result['has_test_results'])

    def test_point_selection_hash_scope_and_paths_are_validated(self):
        for points in [['unknown'],['p1','p1'],[]]:
            with self.assertRaises(ValueError):start_test(self.paths,{'dataset_id':self.dataset['id'],'point_ids':points},background=False)
        with self.assertRaises(ValueError):record_dir(self.paths,'../secrets')
        other={**self.doc,'source_sha256':'b'*64}
        with self.assertRaises(ValueError):execution_pages(self.dataset,other,self.dataset['points'][0])
        mapped={**self.dataset,'source':{**self.dataset['source'],'original_sha256':'original','page_map':{'1':4,'2':10}}}
        self.assertEqual(execution_pages(mapped,self.doc,{'pages':[10]}),[2])

    def test_datasets_do_not_execute_code_and_versions_do_not_overwrite(self):
        with self.assertRaises(ValueError):save_dataset(self.paths,{'name':'Bad','source':self.source,'open_development':False,'points':[{}]})
        with self.assertRaises(ValueError):save_dataset(self.paths,{'name':'Bad','source':self.source,'open_development':True,
            'points':[{'id':'p','checks':[{'field':'value','op':'python','value':'print(1)'}]}]})
        second=save_dataset(self.paths,{'name':'Version 2','source':self.source,'open_development':True,'points':[{'id':'p3','pages':[1]}]})
        self.assertNotEqual(second['id'],self.dataset['id']);self.assertEqual(len(get_dataset(self.paths,self.dataset['id'])['points']),2)

    def test_restarted_server_can_read_records_and_interrupted_jobs_are_not_retried(self):
        record=self.run_check();other=replace(self.paths)
        self.assertEqual(record_detail(other,record['id'])['record']['status'],'completed')
        pending=create_record(other,label='Interrupted',route='knowledge',mode='recognize')
        folder=record_dir(other,pending['id']);meta=read_json(folder/'record.json');meta['owner_pid']=987654321;write_json(folder/'record.json',meta)
        self.assertEqual(get_record(other,pending['id'])['status'],'interrupted')

    def test_new_recognition_receives_no_checkpoint_or_expected_answer(self):
        profiles=self.paths.root/'config/models';profiles.mkdir(parents=True)
        write_json(profiles/'fixture.json',{'name':'fixture','model':'fixture','base_url':'https://fixture.invalid',
            'credential_endpoint':'https://fixture.invalid','api_key_env':'NEVER_READ_FIXTURE_KEY','max_output_tokens':131072})
        calls=[]
        def run(pdfs,**kwargs):
            calls.append((pdfs,kwargs));source=pdfs[0];result={**self.run,'documents':[{**self.doc,'path':str(source)}]}
            write_json(source.parent/'run.json',result);return result
        with patch('slopekg.knowledge_pipeline.KnowledgePipeline') as pipeline:
            pipeline.return_value.run.side_effect=run
            record=self.run_check(mode='recognize',allow_external=True,model_profile='fixture',scope='point_pages')
            self.assertEqual(record['status'],'completed')
            self.assertEqual(set(pipeline.call_args.kwargs),{'settings','parsed_dir','root','progress_callback'})
            self.assertTrue(pipeline.call_args.kwargs['settings'].record_model_inputs)
        self.assertEqual(set(calls[0][1]),{'page_selections'})
        self.assertEqual(next(iter(calls[0][1]['page_selections'].values())),[1])
        self.assertNotIn('expected',str(calls))
        with self.assertRaises(ValueError):self.run_check(mode='recognize',allow_external=False,model_profile='fixture')

    def test_history_review_cannot_be_reused_against_different_prediction_hash(self):
        dataset=get_dataset(self.paths,self.dataset['id']);p=dataset['points'][0]
        p.update(historical_review={'complete':True,'fact_ids':['f1']},historical_run_sha256=hashlib.sha256((self.folder/'run.json').read_bytes()).hexdigest())
        with patch('slopekg.debug_tests.get_dataset',return_value=dataset):
            first=self.run_check();self.assertTrue(record_detail(self.paths,first['id'])['results']['rows'][0]['historical_review_bound'])
            clone=self.run_check(source={'route':'knowledge','run_id':'history-'+first['id'],'document':'doc'})
            self.assertTrue(record_detail(self.paths,clone['id'])['results']['rows'][0]['historical_review_bound'])
            self.doc['facts'][0]['qualifier']='Changed';self.save_run();second=self.run_check()
            self.assertFalse(record_detail(self.paths,second['id'])['results']['rows'][0]['historical_review_bound'])

    def test_same_origin_and_snapshot_post_routes(self):
        handler=object.__new__(SlopeKGHandler);handler.path='/api/recognition-debug/history'
        handler.request_origin_allowed=Mock(return_value=False);handler.read_json_body=Mock();handler.write_json=Mock()
        handler.do_POST();handler.read_json_body.assert_not_called()
        handler.request_origin_allowed.return_value=True;handler.read_json_body.return_value={**self.source,'label':'HTTP snapshot'}
        with patch('slopekg.server.PATHS',self.paths):handler.do_POST()
        self.assertEqual(handler.write_json.call_args.kwargs['status'],201)

    def test_explicit_recording_jobs_save_history_without_changing_recognition_status(self):
        pipeline=Mock();pipeline.paths=self.paths
        pipeline.run.return_value={**self.run,'settings':{'record_model_inputs':True}}
        manager=PipelineJobManager(pipeline)
        manager.jobs['job']={'id':'job','options':{},'status':'queued'}
        with patch('slopekg.debug_history.save_snapshot',return_value={'id':'h_'+'1'*24}) as save:
            manager._run('job');save.assert_called_once()
        self.assertEqual(manager.jobs['job']['debug_history_ids'],['h_'+'1'*24]);self.assertEqual(manager.jobs['job']['status'],'partial')


if __name__=='__main__':unittest.main()
