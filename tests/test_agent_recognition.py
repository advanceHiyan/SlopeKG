"""Fifth-route transport, admission, dispatch and evidence isolation contracts."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from slopekg.agent_gateway import AgentGateway, dump
from slopekg.agent_pipeline import AgentPipeline, validate_outputs
from slopekg.agent_service import agent_artifact, agent_job_options, agent_payload, agent_runs, agent_workspace, stop_agent, select_run
from slopekg.agent_trace import trace_snapshot
from slopekg.agent_settings import AgentSettings
from slopekg.model_client import ModelProfile, ModelError
from slopekg.pipeline import DemoPipeline
from tests.support import workspace_paths


def profile():
    return ModelProfile(name='test',base_url='https://api.deepseek.com',credential_endpoint='https://api.deepseek.com',
        model='deepseek-flash',api_key_env='TEST_ONLY_UNUSED_KEY',max_output_tokens=1)


class AgentContracts(unittest.TestCase):
    def test_atomic_status_write_retries_sharing_failure_and_keeps_old_json(self):
        with TemporaryDirectory() as td:
            target=Path(td)/'run.json';dump(target,{'status':'running'})
            replace=Path.replace;attempts=[]
            def flaky(source,destination):
                attempts.append(1)
                if len(attempts)<3:
                    self.assertEqual(json.loads(target.read_text())['status'],'running')
                    raise PermissionError('simulated Windows sharing collision')
                return replace(source,destination)
            with patch.object(Path,'replace',flaky),patch('slopekg.agent_gateway.time.sleep'):
                dump(target,{'status':'completed'})
            self.assertEqual(len(attempts),3);self.assertEqual(json.loads(target.read_text())['status'],'completed')
            with patch.object(Path,'replace',side_effect=PermissionError('persistent permission failure')),patch('slopekg.agent_gateway.time.sleep'):
                with self.assertRaises(PermissionError):dump(target,{'status':'new'})
            self.assertEqual(json.loads(target.read_text())['status'],'completed')

    def test_resume_drafts_requires_same_inputs_and_preserves_failed_run(self):
        import fitz
        with TemporaryDirectory() as td:
            root=Path(td);paths=workspace_paths(root);config=root/'model.json';dump(config,profile().__dict__)
            pdf=root/'input.pdf'
            with fitz.open() as source:
                source.new_page().insert_text((30,30),'Draft recovery fixture');source.save(pdf)
            pipeline=AgentPipeline(paths,config)
            before=pipeline.run([pdf],prepare_only=True)
            original=Path(before['run_directory']);before['processing_status']='failed'
            dump(original/'run.json',before);dump(paths.output_dir/'agent-recognition/run.json',before)
            dump(original/'workspace/outputs/objects.json',{'draft':True})
            with patch('slopekg.agent_pipeline.AgentGateway',side_effect=AssertionError('no model during preparation')):
                resumed=pipeline.run([pdf],prepare_only=True,resume_from=before['run_id'])
            self.assertEqual(resumed['processing_status'],'prepared');self.assertEqual(resumed['resumed_from'],before['run_id'])
            protocol=json.loads((Path(resumed['run_directory'])/'control/protocol.json').read_text(encoding='utf-8'))
            self.assertIn('objects.json',protocol['initial_draft_sha256'])
            self.assertEqual(json.loads((original/'run.json').read_text(encoding='utf-8'))['processing_status'],'failed')
            other=root/'other.pdf'
            with fitz.open() as source:
                source.new_page().insert_text((30,30),'Changed source');source.save(other)
            rejected=pipeline.run([other],prepare_only=True,resume_from=before['run_id'])
            self.assertEqual(rejected['processing_status'],'failed');self.assertIn('不一致',rejected['errors'][0])

    def test_web_resume_uses_original_pages_and_dispatches_saved_draft(self):
        import fitz
        with TemporaryDirectory() as td:
            root=Path(td);paths=workspace_paths(root)
            config=root/'config/models/deepseek-knowledge-max.json'
            dump(config,profile().__dict__)
            paths.raw_dir.mkdir(parents=True)
            pdf=paths.raw_dir/'input.pdf'
            with fitz.open() as source:
                source.new_page()
                source.new_page().insert_text((30,30),'Selected page two')
                source.save(pdf)
            before=AgentPipeline(paths,config,settings=AgentSettings(task_instruction='Keep original instructions')).run(
                [pdf], prepare_only=True, page_selections={str(pdf):[2]})
            original=Path(before['run_directory'])
            before.update(processing_status='partial',errors=['Fact/object document mismatch: f'])
            dump(original/'run.json',before)
            dump(paths.output_dir/'agent-recognition/run.json',before)
            dump(original/'workspace/outputs/objects.json',{'draft':True})
            body={'resume_run':before['run_id'],'prepare_only':True}
            self.assertTrue(agent_payload(paths,run_id=before['run_id'])['can_resume'])
            with self.assertRaisesRegex(ValueError,'允许'):
                agent_job_options(paths,{**body,'prepare_only':False})
            with self.assertRaisesRegex(ValueError,'原资料'):
                agent_job_options(paths,{**body,'input_pdfs':['another.pdf']})
            options=agent_job_options(paths,body)
            with patch('slopekg.agent_pipeline.AgentGateway',side_effect=AssertionError('no external request')):
                resumed=DemoPipeline(paths).run(**options)
            self.assertEqual(resumed['processing_status'],'prepared')
            self.assertEqual(resumed['resumed_from'],before['run_id'])
            self.assertEqual(resumed['documents'][0]['selection']['selected_pages'],[2])
            protocol=json.loads((Path(resumed['run_directory'])/'control/protocol.json').read_text(encoding='utf-8'))
            self.assertIn('Fact/object document mismatch: f',protocol['task_prompt'])
            self.assertIn('Keep original instructions',protocol['task_prompt'])
            self.assertIn('objects.json',protocol['initial_draft_sha256'])
            self.assertEqual(json.loads((original/'run.json').read_text(encoding='utf-8'))['processing_status'],'partial')
            with pdf.open('ab') as f:f.write(b'changed')
            with self.assertRaisesRegex(ValueError,'不一致'):
                agent_job_options(paths,body)

    def test_defaults_are_unlimited_and_old_explicit_limits_still_validate(self):
        self.assertIsNone(AgentSettings().max_calls)
        self.assertIsNone(AgentSettings().timeout_seconds)
        self.assertEqual(AgentSettings(max_calls=5000,timeout_seconds=90000).max_calls,5000)
        for k in ['max_calls','timeout_seconds']:
            for invalid in [0,-1,True,'unlimited']:
                with self.assertRaises(ValueError):AgentSettings(**{k:invalid})

    def test_gateway_accepts_request_after_old_160_call_boundary(self):
        with TemporaryDirectory() as td:
            def transport(req,timeout):
                stream=io.BytesIO(b'data: {"choices":[{"finish_reason":"stop"}],"usage":{"total_tokens":1}}\n\ndata: [DONE]\n\n');stream.status=200
                return stream
            with patch('slopekg.agent_gateway.CompatibleVisionClient._key',return_value='private-test-secret'):
                with AgentGateway(profile(),Path(td),Path(td)/'audit',transport=transport) as g:
                    g.receipts=[{'number':i,'status':'complete','usage':{}} for i in range(1,161)]
                    req=urllib.request.Request(g.url+'/chat/completions',data=b'{"model":"deepseek-flash"}',headers={'Authorization':'Bearer '+g.secret,'Content-Type':'application/json'})
                    self.assertIn(b'[DONE]',urllib.request.urlopen(req,timeout=5).read())
                    self.assertEqual(len(g.receipts),161)
                    self.assertEqual(g.receipts[-1]['requested_max_output_tokens'],393216)

    def test_incremental_trace_handles_partial_line_completion_and_tool_result(self):
        with TemporaryDirectory() as td:
            root=Path(td);log=root/'control/events.jsonl';log.parent.mkdir()
            def event(seq,kind,data):return json.dumps({'params':{'event':{'seq':seq,'time':seq,'type':kind,'data':data}}})+'\n'
            initial=event(1,'tool/call',{'callId':'c','name':'view_image','arguments':'{"path":"inputs/p2.png"}','step':1})
            result=event(2,'tool/result',{'message':{'source':{'callId':'c'},'content':[{'isError':False}]}})
            log.write_text(initial+result[:-1],encoding='utf-8')
            first=trace_snapshot(root)
            self.assertEqual(first['count'],1);self.assertEqual(first['rows'][0]['status'],'running')
            with log.open('a',encoding='utf-8') as f:f.write('\n'+event(3,'assistant/message',{'step':1,'message':{'content':[{'type':'text','text':'完成'}]}}))
            second=trace_snapshot(root)
            self.assertEqual(second['count'],2);self.assertEqual(second['rows'][0]['status'],'complete')
            self.assertIn('result',trace_snapshot(root,event='1')['event'])
            self.assertEqual(trace_snapshot(root)['count'],2)

    def test_history_selection_artifacts_and_stop_are_bound_to_run(self):
        with TemporaryDirectory() as td:
            paths=workspace_paths(Path(td))
            for key,status in [('agent-old','completed'),('agent-new','running')]:
                root=paths.output_dir/'agent-recognition/runs'/key
                run={'run_id':key,'run_directory':str(root),'processing_status':status,'documents':[]}
                dump(root/'run.json',run);dump(root/'control/protocol.json',{'id':key})
            dump(paths.output_dir/'agent-recognition/run.json',run)
            self.assertEqual(len(agent_runs(paths)['runs']),2)
            self.assertEqual(json.loads(agent_artifact(paths,'control/protocol.json','agent-old').read_text())['id'],'agent-old')
            with self.assertRaises(ValueError):select_run(paths,'../../private')
            with self.assertRaises(ValueError):stop_agent(paths,'agent-old')
            self.assertEqual(stop_agent(paths,'agent-new')['status'],'stopping')
            self.assertTrue((root/'control/stop.request.json').exists())
            self.assertEqual(agent_workspace(paths,run_id='agent-old')['run_id'],'agent-old')

    def test_stop_during_preparation_does_not_start_sdk(self):
        with TemporaryDirectory() as td:
            root=Path(td);paths=workspace_paths(root);dump(root/'model.json',profile().__dict__)
            def prepare(workspace,**kwargs):
                dump(workspace.parent/'control/stop.request.json',{'requested':True})
                return {'documents':[]}
            with patch.object(AgentPipeline,'_prepare',side_effect=prepare),patch('slopekg.agent_pipeline.AgentGateway',side_effect=AssertionError('must not start')):
                result=AgentPipeline(paths,root/'model.json').run(input_pack=root)
            self.assertEqual(result['processing_status'],'cancelled')

    def test_gateway_caps_usage_and_secret_boundary(self):
        for finish,status in [('stop','complete'),('length','incomplete')]:
            with self.subTest(finish=finish), TemporaryDirectory() as td:
                captured=[]
                def transport(req,timeout):
                    captured.append(req)
                    data={'model':'deepseek-flash','choices':[{'finish_reason':finish}],
                          'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12,'prompt_cache_hit_tokens':8,'prompt_cache_miss_tokens':2}}
                    stream=io.BytesIO(('data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n').encode())
                    stream.status=200
                    return stream
                with patch('slopekg.agent_gateway.CompatibleVisionClient._key',return_value='private-test-secret'):
                    with AgentGateway(profile(),Path(td),Path(td)/'audit',transport=transport,max_calls=1) as g:
                        def request(body,token=None):
                            req=urllib.request.Request(g.url+'/chat/completions',data=json.dumps(body).encode(),
                                headers={'Authorization':'Bearer '+(token or g.secret),'Content-Type':'application/json'})
                            return urllib.request.urlopen(req,timeout=5).read()
                        with self.assertRaises(urllib.error.HTTPError) as error: request({'model':'deepseek-flash'},'wrong')
                        self.assertEqual(error.exception.code,401)
                        with self.assertRaises(urllib.error.HTTPError): request({'model':'deepseek-flash','tools':[{'function':{'name':'bash'}}]})
                        request({'model':'deepseek-flash','messages':[],'max_tokens':1,'max_completion_tokens':2})
                        # The receipt is finalized immediately after the response stream is closed.
                        self.assertEqual(g.receipts[0]['status'],status)
                        self.assertEqual(g.usage()['prompt_cache_hit_tokens'],8)
                        with self.assertRaises(urllib.error.HTTPError):request({'model':'deepseek-flash'})
                sent=json.loads(captured[0].data)
                self.assertEqual(sent['max_tokens'],393216)
                self.assertNotIn('max_completion_tokens',sent)
                self.assertEqual(captured[0].get_header('Authorization'),'Bearer private-test-secret')
                self.assertNotIn('private-test-secret',''.join(p.read_text() for p in (Path(td)/'audit').iterdir()))

    def fixture(self,root):
        manifest={'documents':[{'document_id':'d','selected_physical_pages':[2],
            'files':[{'path':'inputs/p2.png','kind':'source_page_image','physical_page':2}]}]}
        objects={'objects':[{'id':'o','document_id':'d','parent_object':'','relations':[]}]}
        fact={'id':'f','document_id':'d','object':'o','parent_object':'','field':'height','value':2,'unit':'m',
            'condition':'','role':'design','evidence':{'physical_page':2,'quote':'2m','locator':'table'},'status':'ok','uncertainty':''}
        dump(root/'outputs/objects.json',objects)
        (root/'outputs/facts.jsonl').write_text(json.dumps(fact)+'\n',encoding='utf-8')
        dump(root/'outputs/coverage.json',{'pages':[{'document_id':'d','physical_page':2,'status':'complete','remaining':[]}]})
        (root/'outputs/report.md').write_text('Report',encoding='utf-8')
        return manifest,fact

    def test_coverage_needs_real_image_read_and_foreign_evidence_is_rejected(self):
        with TemporaryDirectory() as td:
            root=Path(td);manifest,fact=self.fixture(root)
            self.assertTrue(validate_outputs(root,manifest)['errors'])
            calls=[{'tool':'view_image','status':'ok','args':{'path':'./inputs/p2.png'}}]
            self.assertFalse(validate_outputs(root,manifest,calls)['errors'])
            fact['evidence']['physical_page']=3
            (root/'outputs/facts.jsonl').write_text(json.dumps(fact),encoding='utf-8')
            self.assertIn('Invalid evidence: f',validate_outputs(root,manifest,calls)['errors'])

    def test_malformed_model_artifacts_fail_admission_without_crashing(self):
        with TemporaryDirectory() as td:
            root=Path(td);manifest,_=self.fixture(root)
            for value in [None,{'pages':[None]},{'pages':'invalid'}]:
                dump(root/'outputs/coverage.json',value)
                self.assertTrue(validate_outputs(root,manifest)['errors'])

    def test_prepare_only_does_not_load_key_or_sdk_and_selects_exact_page(self):
        import fitz
        with TemporaryDirectory() as td:
            root=Path(td);paths=workspace_paths(root)
            pdfpath=root/'input.pdf'
            with fitz.open() as pdf:
                pdf.new_page().insert_text((30,30),'First')
                pdf.new_page().insert_text((30,30),'Second')
                pdf.save(pdfpath)
            config=root/'profile.json';dump(config,profile().__dict__)
            published=root/'outputs/pilot/runs/agent-prior'
            dump(published/'control/protocol.json',{'historical':True})
            dump(paths.output_dir/'agent-recognition/run.json',{'run_id':'agent-prior','run_directory':str(published),'processing_status':'completed'})
            with patch('slopekg.agent_pipeline.AgentGateway',side_effect=AssertionError('must not initialize transport')):
                r=AgentPipeline(paths,config).run([pdfpath],prepare_only=True,page_selections={str(pdfpath):[2]})
            self.assertEqual(r['processing_status'],'prepared')
            self.assertEqual(r['summary']['pages'],1)
            self.assertEqual(len(list(Path(r['run_directory']).glob('workspace/inputs/**/*.png'))),1)
            self.assertEqual(select_run(paths,'agent-prior')['run_directory'],str(published))
            self.assertEqual(len(agent_runs(paths)['runs']),2)
            self.assertEqual(agent_artifact(paths,'control/protocol.json','agent-prior'),published/'control/protocol.json')

    def test_artifact_endpoint_rejects_parent_traversal_and_internal_home(self):
        with TemporaryDirectory() as td:
            paths=workspace_paths(Path(td));root=paths.output_dir/'agent-recognition/runs/r'
            dump(paths.output_dir/'agent-recognition/run.json',{'run_directory':str(root)})
            dump(root/'control/protocol.json',{'test':True})
            dump(root/'control/dsh-home/private.json',{'private':True})
            self.assertEqual(agent_artifact(paths,'control/protocol.json').name,'protocol.json')
            for p in ['control/dsh-home/private.json','../run.json','../../run.json']:
                with self.assertRaises(ValueError):agent_artifact(paths,p)

    def test_dispatch_keeps_route_settings_separate(self):
        with TemporaryDirectory() as td:
            pipeline=DemoPipeline(workspace_paths(Path(td)))
            with patch('slopekg.agent_service.run_agent_recognition',return_value={'test':True}) as call:
                self.assertEqual(pipeline.run(recognizer='agent',input_pdfs=['x'],model_profile='x')['route']['id'],'agent')
                call.assert_called_once()
            for route in ['basic','knowledge','visual','document']:
                with self.assertRaises(ValueError):pipeline.run(recognizer=route,agent_settings={})
            with self.assertRaises(ValueError):pipeline.run(recognizer='agent',review_profile='x')

    def test_missing_project_key_is_a_retained_failed_run(self):
        with TemporaryDirectory() as td:
            root=Path(td);paths=workspace_paths(root);dump(root/'model.json',profile().__dict__)
            dump(root/'.private/agent-runtime/runtime.json',{'node':str(root/'node'),'modules':str(root/'modules')})
            (root/'node').touch();(root/'modules').mkdir()
            fake={'documents':[]}
            def prepare(workspace,**kwargs):dump(workspace/'manifest.json',fake);return fake
            with patch.object(AgentPipeline,'_prepare',side_effect=prepare), patch('slopekg.agent_gateway.CompatibleVisionClient._key',side_effect=ModelError('Project credential missing')):
                result=AgentPipeline(paths,root/'model.json').run(input_pack=root)
            self.assertEqual(result['processing_status'],'failed')
            self.assertEqual(result['errors'],['Project credential missing'])
            self.assertTrue((Path(result['run_directory'])/'run.json').exists())

    def test_http_requires_explicit_send_and_keeps_runtime_selection_off_wire(self):
        with TemporaryDirectory() as td:
            paths=workspace_paths(Path(td));paths.raw_dir.mkdir(parents=True)
            (paths.raw_dir/'sample.pdf').write_bytes(b'fixture')
            dump(paths.root/'config/models/test.json',profile().__dict__)
            body={'input_pdfs':['sample.pdf'],'model_profile':'test'}
            self.assertTrue(agent_job_options(paths,body)['prepare_only'])
            with self.assertRaises(ValueError):agent_job_options(paths,{**body,'prepare_only':False})
            with self.assertRaises(ValueError):agent_job_options(paths,{**body,'agent_settings':{'modules':'arbitrary'}})
            self.assertFalse(agent_job_options(paths,{**body,'prepare_only':False,'allow_external':True})['prepare_only'])


if __name__=='__main__':unittest.main()
