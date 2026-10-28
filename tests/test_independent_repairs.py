import copy
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from slopekg.pipeline import DemoPipeline
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



    def test_job_and_cli_do_not_turn_incomplete_processing_into_success(self):
        for status in ['failed','partial']:
            with self.subTest(status=status):
                state={'processing_status':status,'publication_stage':'semantic_'+status,'processing_message':'基础可用，深度未完成'}
                manager=PipelineJobManager(Mock(run=Mock(return_value=state)))
                with patch('slopekg.jobs.threading.Thread'):
                    job,_=manager.start({});manager._run(job['id'])
                self.assertEqual(manager.latest()['status'],status);self.assertEqual(manager.latest()['result'],state)
                with patch('sys.argv',['run_pipeline.py']),patch('scripts.run_pipeline.DemoPipeline') as pipeline,redirect_stdout(StringIO()),self.assertRaises(SystemExit) as error:
                    pipeline.return_value.run.return_value=state;run_pipeline.main()
                self.assertEqual(error.exception.code,1)
