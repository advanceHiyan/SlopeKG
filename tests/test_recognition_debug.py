"""Debug read boundaries and lossless opt-in exchange capture, without network."""
import hashlib
import json
from pathlib import Path
import tempfile
import io
import unittest
from unittest.mock import Mock, patch

from slopekg.recognition_debug import debug_catalog, debug_records, debug_call, debug_source, DENIED_HASHES
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.model_client import CompatibleVisionClient, ModelError, ModelProfile
from slopekg.visual_runtime import VisualRuntime
from slopekg.storage import write_json, write_jsonl, read_json
from slopekg.server import SlopeKGHandler
from tests.support import workspace_paths


class DebugReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = workspace_paths(Path(self.temp.name))
        self.paths.raw_dir.mkdir(parents=True)
        self.source = self.paths.raw_dir / 'anonymous.pdf'
        self.source.write_bytes(b'anonymous source bytes')
        self.folder = self.paths.output_dir / 'knowledge-recognition'
        self.key = 'a' * 64
        self.doc = {'id': 'doc', 'path': str(self.source),
            'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
            'source_page_count': 2, 'status': 'partial',
            'pages': [{'page': 1, 'ocr_pending': True}],
            'units': [{'id': 'u1', 'source': 'ocr', 'page': 1, 'text': 'OCR result <script>oops</script>'},
                      {'id': 'u2', 'source': 'pdf_native', 'page': 2, 'text': 'Native text'}],
            'chunks': [{'id': 'c1', 'unit_ids': ['u1'], 'pages': [1]}],
            'facts': [{'id': 'f1', 'status': 'pending', 'quality_issues': ['unit_missing'],
                       'evidence': [{'page': 1, 'span_id': 'u1'}]}]}
        self.run = {'documents': [self.doc], 'receipts': [{'cache_key': self.key, 'stage': 'knowledge_discover'}]}
        self.save()

    def save(self):
        write_json(self.folder / 'run.json', self.run)

    def records(self, **kwargs):
        return debug_records(self.paths, document='doc', **kwargs)

    def test_catalog_and_each_record_preserve_source_linkage_and_full_text(self):
        text = '完整输入' * 30000
        self.doc['units'][0]['text'] = text
        self.save()
        catalog = debug_catalog(self.paths)
        self.assertEqual(catalog['documents'][0]['id'], 'doc')
        self.assertEqual(self.records(stage='units', page=1)['rows'][0]['text'], text)
        self.assertEqual(self.records(stage='chunks')['rows'][0]['source_units'][0]['text'], text)
        self.assertEqual(self.records(stage='units', page=2)['count'], 1)
        self.assertEqual(self.records(stage='gates', failures=True)['count'], 1)
        self.assertEqual(self.records(stage='units', q='Native')['count'], 1)
        self.assertEqual(self.records(stage='units', limit=1, offset=1)['rows'][0]['id'], 'u2')

    def test_only_current_run_receipts_and_document_bound_calls_are_readable(self):
        write_json(self.folder / 'inputs' / (self.key + '.json'), {'prompt': 'exact full input', 'system': 'system'})
        write_json(self.folder / 'cache' / (self.key + '.json'), {'status': 'completed', 'answer': {'items': []}})
        self.assertEqual(self.records(stage='calls')['count'], 1)
        filtered=self.records(stage='calls',q='does-not-match')
        self.assertEqual(filtered['count'],0)
        self.assertEqual(filtered['trace_coverage'],{'calls':1,'saved_inputs':1,'saved_raw_exchanges':0})
        call = debug_call(self.paths, route='knowledge', run_id='live', document='doc', key=self.key)
        self.assertEqual(call['input']['prompt'], 'exact full input')
        self.assertTrue(call['notes'])
        write_json(self.folder / 'exchanges' / (self.key + '.json'), {'attempts': [{'request': {'model': 'fixture'}, 'response': None}]})
        incomplete = debug_call(self.paths, route='knowledge', run_id='live', document='doc', key=self.key)
        self.assertTrue(any('未收到或未保存' in note for note in incomplete['notes']))
        with self.assertRaises(ValueError):
            debug_call(self.paths, route='knowledge', run_id='live', document='doc', key='b' * 64)
        self.run['documents'].append({**self.doc, 'id': 'second'})
        self.save()
        self.assertEqual(self.records(stage='calls')['count'], 0)  # no ambiguous cross-document leak

    def test_private_traversal_and_former_holdout_documents_are_excluded(self):
        self.run['documents'] += [{**self.doc, 'id': 'denied', 'source_sha256': next(iter(DENIED_HASHES))},
            {**self.doc, 'id': 'private', 'path': str(self.paths.root / '.private/evaluation/sealed_holdout/test.pdf')}]
        self.save()
        self.assertEqual([d['id'] for d in debug_catalog(self.paths)['documents']], ['doc'])
        for options in [{'document': '../run'}, {'document': 'denied'}, {'document': 'private'},
                        {'document': 'doc', 'stage': '../cache'}, {'document': 'doc', 'limit': 500}]:
            with self.assertRaises(ValueError):
                debug_records(self.paths, **options)

    def test_source_changes_cannot_reuse_old_coordinates(self):
        self.assertEqual(debug_source(self.paths, route='knowledge', run_id='live', document='doc'), self.source)
        self.source.write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, '哈希'):
            debug_source(self.paths, route='knowledge', run_id='live', document='doc')

    def test_basic_relative_paths_and_ocr_are_read_from_the_correct_document(self):
        basic = {**self.doc, 'path': 'data/rawPDF/anonymous.pdf', 'pages': 2}
        write_jsonl(self.paths.parsed_dir / 'documents.jsonl', [basic])
        write_jsonl(self.paths.parsed_dir / 'ocr_results.jsonl', [
            {'id': 'r1', 'document_id': 'doc', 'page': 1, 'text': 'Exact OCR'},
            {'id': 'r2', 'document_id': 'another', 'page': 1, 'text': 'Other document'}])
        self.assertEqual(debug_catalog(self.paths, 'basic')['documents'][0]['id'], 'doc')
        rows = debug_records(self.paths, route='basic', document='doc', stage='ocr')['rows']
        self.assertEqual([r['text'] for r in rows], ['Exact OCR'])

    def test_http_dispatch_returns_errors_without_falling_back_to_static_files(self):
        handler = object.__new__(SlopeKGHandler)
        handler.path = '/api/recognition-debug?route=knowledge&document=doc&limit=999'
        handler.write_json = Mock()
        with patch('slopekg.server.PATHS', self.paths):
            handler.do_GET()
        self.assertEqual(handler.write_json.call_args.kwargs['status'], 400)


class ExchangeCaptureTests(unittest.TestCase):
    def model(self, response, sent):
        profile = ModelProfile(name='offline-fixture', model='fixture', base_url='https://fixture.invalid',
            credential_endpoint='https://fixture.invalid', api_key_env='NEVER_READ_FIXTURE_KEY')
        return CompatibleVisionClient(profile, Path('.'), transport=lambda payload: (sent.append(payload) or response))

    def test_raw_reply_and_exact_request_survive_validation_and_cache_reuse(self):
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"n":1,"discarded":"keep raw"}'}}],
                    'usage': {'total_tokens': 12}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);sent = [];model = self.model(response, sent)
            runtime = VisualRuntime(model, model, root, KnowledgeSettings(record_model_inputs=True))
            runtime.document_id = 'doc'
            def validate(raw): return {'n': raw['n']}
            for _ in range(2):
                self.assertEqual(runtime.ask('unit', 'full prompt', {'page': 2}, [], validate), {'n': 1})
            self.assertEqual(len(sent), 1)
            exchange = read_json(next((root / 'exchanges').glob('*.json')))
            self.assertEqual(exchange['attempts'][0]['request'], sent[0])
            self.assertEqual(exchange['attempts'][0]['response'], response)
            cached = read_json(next((root / 'cache').glob('*.json')))
            self.assertEqual(cached['raw_answer']['discarded'], 'keep raw')
            self.assertEqual(cached['answer'], {'n': 1})
            self.assertNotIn('Authorization', json.dumps(exchange))
            self.assertEqual(len(read_json(root / 'debug-trace.json')), 2)

    def test_malformed_and_truncated_provider_content_is_retained_but_fails(self):
        for finish in ['stop', 'length']:
            with self.subTest(finish=finish), tempfile.TemporaryDirectory() as temp:
                response = {'choices': [{'finish_reason': finish, 'message': {'content': '{bad original JSON'}}]}
                root=Path(temp);sent=[];model=self.model(response,sent)
                runtime=VisualRuntime(model,model,root,KnowledgeSettings(record_model_inputs=True))
                with self.assertRaises(ModelError):
                    runtime.ask('broken', 'full prompt', {}, [], lambda value:value)
                exchange=read_json(next((root/'exchanges').glob('*.json')))
                self.assertEqual(exchange['attempts'][0]['response'], response)
                self.assertEqual(read_json(next((root/'cache').glob('*.json')))['status'], 'failed')

    def test_recording_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sent=[];model=self.model({'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]},sent)
            runtime=VisualRuntime(model,model,root,KnowledgeSettings())
            runtime.ask('unit','prompt',{},[],lambda value:value)
            self.assertFalse((root/'inputs').exists())
            self.assertFalse((root/'exchanges').exists())

    def test_http_body_is_saved_before_json_decoding_even_when_the_envelope_is_invalid(self):
        for body in [' { "choices" : [{"finish_reason":"stop","message":{"content":"{}"}}] }\n', '\ufeff{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}', 'invalid HTTP JSON body']:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);model=self.model({},[]);model.transport=None
                opener=Mock();opener.open.return_value=io.BytesIO(body.encode('utf-8'))
                runtime=VisualRuntime(model,model,root,KnowledgeSettings(record_model_inputs=True))
                with patch('slopekg.model_client.urllib.request.build_opener',return_value=opener), patch.object(model,'_key',return_value='NEVER_LOG_THIS_SECRET'):
                    if body.startswith('invalid'):
                        with self.assertRaises(ModelError):runtime.ask('http','prompt',{},[],lambda value:value)
                    else:
                        runtime.ask('http','prompt',{},[],lambda value:value)
                exchange=read_json(next((root/'exchanges').glob('*.json')))
                self.assertEqual(exchange['attempts'][0]['raw_response_body'],body)
                self.assertNotIn('NEVER_LOG_THIS_SECRET',json.dumps(exchange))


if __name__ == '__main__':
    unittest.main()
