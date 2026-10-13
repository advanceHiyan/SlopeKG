import copy
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from slopekg.pipeline import DemoPipeline
from slopekg.semantic import run_semantic_extraction
from slopekg.server import SlopeKGHandler
from slopekg.jobs import PipelineJobManager
from slopekg.storage import read_json, write_json
from tests.support import workspace_paths
from scripts import run_pipeline


class IndependentRepairTests(unittest.TestCase):
    def test_upload_preserves_selected_options_and_rejects_bad_options_before_saving(self):
        for options,valid in [(None,True), ('{"ocr_pages":0,"ocr_device":"cpu","ocr_workers":2,"ocr_batch_size":3,"force_ocr":true}',True), ('"bad"',False), ('{"ocr_device":"invalid"}',False), ('{"ocr_pages":null}',False)]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                paths=workspace_paths(Path(directory))
                field='' if options is None else f'--boundary\r\nContent-Disposition: form-data; name="options"\r\n\r\n{options}\r\n'
                body=(field+'--boundary\r\nContent-Disposition: form-data; name="files"; filename="sample.pdf"\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.7 test\r\n--boundary--\r\n').encode()
                h=object.__new__(SlopeKGHandler);h.headers={'Content-Type':'multipart/form-data; boundary=boundary','Content-Length':str(len(body))};h.rfile=BytesIO(body);h.write_json=Mock()
                with patch('slopekg.server.PATHS',paths),patch('slopekg.server.JOB_MANAGER') as manager:
                    manager.start.return_value=({'id':'job'},True);h.handle_pdf_upload()
                if valid:
                    selected=manager.start.call_args.args[0]
                    self.assertEqual(selected['parse_mode'],'basic');self.assertEqual(selected['ocr_scope'],'changed')
                    self.assertEqual(selected['ocr_pages'],1000 if options is None else 0)
                    if options is not None:
                        self.assertEqual((selected['ocr_device'],selected['ocr_workers'],selected['ocr_batch_size'],selected['force_ocr']),('cpu',2,3,True))
                    self.assertEqual(len(list(paths.raw_dir.glob('*.pdf'))),1)
                else:
                    manager.start.assert_not_called();self.assertEqual(list(paths.root.rglob('*.pdf')),[])
                    self.assertEqual(h.write_json.call_args.kwargs['status'],400)

    def test_skipped_objects_are_explicit_without_calling_model(self):
        with patch('slopekg.semantic.call_deepseek_json') as model:
            rows,summary=run_semantic_extraction({'slopes':[{'station':'K1+000-K1+100','route_code':'G999'}]},enabled=True)
        model.assert_not_called();self.assertEqual(rows,[])
        self.assertEqual(summary['requested_slopes'],1);self.assertEqual(summary['skipped_count'],1)
        self.assertEqual(summary['skipped'][0]['reason'],'no_section_source_samples')

    def test_incomplete_deep_run_keeps_prior_deep_layer_and_publishes_base_with_honest_state(self):
        summaries=[({'candidates':1,'passed':0,'failures':1,'skipped_count':0},'failed','semantic_failed'),
                   ({'candidates':2,'passed':1,'failures':1,'skipped_count':0},'partial','semantic_partial'),
                   ({'candidates':0,'passed':0,'failures':0,'skipped_count':1},'partial','semantic_partial'),
                   ({'candidates':0,'passed':0,'failures':0,'skipped_count':0},'completed','semantic_not_applicable')]
        for summary,status,stage in summaries:
            with self.subTest(stage=stage,summary=summary),tempfile.TemporaryDirectory() as directory:
                paths=workspace_paths(Path(directory));p=DemoPipeline(paths)
                prior={'meta':{'pipeline':{'source_documents':[]}},'nodes':[{'id':'prior-deep'}]};write_json(paths.deep_graph_json,prior)
                write_json(paths.deep_state_json,{'prior':'successful'})
                parsed={k:[] for k in ['documents','pages','text_blocks','tables','ocr_tasks']}
                extracted={'slopes':[] if stage=='semantic_not_applicable' else [{'station':'K1+000-K1+100'}]}
                graph={'meta':{'stats':{'nodes':0},'pipeline':{'source_documents':[]}},'nodes':[],'edges':[]}
                def build(*args,**kwargs):
                    write_json(paths.basic_graph_json,graph);return graph,{}
                def semantic(*args,enabled,**kwargs):return ([{'validation_status':'failed'}] if enabled else [],summary if enabled else {'enabled':False})
                events=[]
                with patch.object(p,'load_parsed_documents',return_value=copy.deepcopy(parsed)),patch.object(p,'run_ocr',return_value=([],[])),patch.object(p,'build_graph',side_effect=build) as builder,patch('slopekg.pipeline.extract_domain_candidates',return_value=extracted),patch('slopekg.pipeline.run_semantic_extraction',side_effect=semantic),patch('slopekg.pipeline.read_deepseek_key',return_value='stub'),patch('slopekg.pipeline.llm_status',return_value={}),patch('slopekg.pipeline.export_standalone_html',return_value={}),patch('slopekg.pipeline.export_risk_standalone_html',return_value={}):
                    state=p.run(parse_mode='deep',reuse_parsed=True,ocr_pages=0,progress_callback=lambda percent,stage,message:events.append(stage))
                self.assertEqual(state['processing_status'],status);self.assertEqual(state['publication_stage'],stage)
                self.assertEqual(state['parse_mode'],'basic');self.assertEqual(state['requested_parse_mode'],'deep')
                self.assertEqual(read_json(paths.deep_graph_json),prior);self.assertEqual(read_json(paths.deep_state_json),{'prior':'successful'})
                self.assertTrue(all(c.kwargs['parse_mode']=='basic' for c in builder.call_args_list))
                self.assertEqual(events[-1],stage);self.assertEqual(read_json(paths.state_json)['publication_stage'],stage)

    def test_job_and_cli_do_not_turn_incomplete_processing_into_success(self):
        for status in ['failed','partial']:
            with self.subTest(status=status):
                state={'processing_status':status,'publication_stage':'semantic_'+status,'processing_message':'基础可用，深度未完成'}
                manager=PipelineJobManager(Mock(run=Mock(return_value=state)))
                with patch('slopekg.jobs.threading.Thread'):
                    job,_=manager.start({});manager._run(job['id'])
                self.assertEqual(manager.latest()['status'],status);self.assertEqual(manager.latest()['result'],state)
                with patch('sys.argv',['run_pipeline.py','--deep']),patch('scripts.run_pipeline.DemoPipeline') as pipeline,redirect_stdout(StringIO()),self.assertRaises(SystemExit) as error:
                    pipeline.return_value.run.return_value=state;run_pipeline.main()
                self.assertEqual(error.exception.code,1)
