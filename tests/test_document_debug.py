"""Read-only document debugging: exact provenance, missing history and boundaries."""
import json
import unittest
from unittest.mock import patch, Mock

from tests import test_recognition_debug as base
from slopekg.recognition_debug import debug_catalog, debug_records, debug_call
from slopekg.document_debug import document_trace
from slopekg.storage import write_json
from slopekg.model_client import content_hash
from slopekg.server import SlopeKGHandler


class DocumentDebugTests(unittest.TestCase):
    setUp = base.DebugReadTests.setUp
    save = base.DebugReadTests.save
    # Keep base tests on their original knowledge fixture; document fixtures use
    # another folder to ensure adding the route cannot mix production records.
    def setup_document(self):
        folder = self.paths.output_dir / 'document-recognition'
        raw = {'subject': 'Object A', 'field': 'height', 'value': '12', 'group_ids': ['g1'],
               'evidence': [{'block_id': 'b1', 'quote': '12'}]}
        cid = 'd_' + content_hash(['doc', raw])[:22]
        fact = {**raw, 'id': cid, 'status': 'pending', 'origin_groups': ['g1'],
                'quality_issues': ['ownership'], 'evidence': [{'block_id': 'b1', 'quote': '12', 'page': 2}],
                'parent_evidence': [{'block_id': 'title', 'quote': 'Object A', 'page': 1}]}
        doc = {**self.doc, 'workflow_complete': True, 'selection': {'scope': 'selected_pages', 'selected_pages': [1, 2]},
            'pages': [{'page': 2, 'native_sources': [{'id': 'n1', 'text': '12', 'page': 2}]}],
            'blocks': [{'id': 'b1', 'text': '12', 'page': 2, 'source_ids': ['n1']},
                       {'id': 'title', 'text': 'Object A', 'page': 1, 'source_ids': []}],
            'groups': [{'id': 'g1', 'name': 'Object A', 'source_block_ids': ['title', 'b1']}],
            'facts': [fact], 'claims': [fact], 'dossier_reviews': [], 'repair_queue': []}
        key = 'c' * 64
        write_json(folder / 'run.json', {'schema_version': 'document-v0.2', 'documents': [doc],
            'receipts': [{'stage': 'document_extract', 'cache_key': key, 'cache_hit': True}]})
        ctx = {'groups': doc['groups'], 'blocks': [doc['blocks'][0]]}
        write_json(folder / 'inputs' / (key + '.json'), {'document_id': 'doc', 'prompt': 'instruction\n输入：'+json.dumps(ctx), 'images': []})
        write_json(folder / 'cache' / (key + '.json'), {'status': 'completed', 'answer': {
            '_document_raw': {'claims': [raw]}, '_document_validated': {'claims': [fact]}}})
        return folder, cid, key

    def test_route_stages_preserve_full_source_and_separate_workflow_status(self):
        self.setup_document()
        cat = debug_catalog(self.paths, 'document')
        self.assertTrue(cat['documents'][0]['workflow_complete'])
        self.assertEqual(cat['documents'][0]['status'], 'partial')
        for s in cat['stages']:
            debug_records(self.paths, route='document', document='doc', stage=s['id'])
        block = debug_records(self.paths, route='document', document='doc', stage='blocks', page=2)['rows'][0]
        self.assertEqual(block['native_sources'][0]['text'], '12')
        self.assertNotIn('ocr', [s['id'] for s in cat['stages']])

    def test_trace_joins_by_id_and_exposes_absent_context_and_raw_extraction(self):
        _, cid, key = self.setup_document()
        result = document_trace(self.paths, document='doc', claim_id=cid)
        self.assertEqual(result['calls'][0]['id'], key)
        self.assertEqual(result['calls'][0]['association'], 'exact_claim_id')
        self.assertEqual(result['calls'][0]['raw_claims'][0]['value'], '12')
        self.assertEqual(result['calls'][0]['group_coverage'][0]['absent_block_ids'], ['title'])
        self.assertEqual(result['source_pages'][0]['native_sources'][0]['id'], 'n1')
        self.assertTrue(result['calls'][0]['cache_hit'])
        self.assertFalse(result['calls'][0]['raw_exchange_saved'])

    def test_invalid_trace_and_cross_route_records_fail_closed(self):
        _, cid, _ = self.setup_document()
        for options in [{'claim_id': 'not-in-run'}, {'claim_id': cid, 'route': 'knowledge'},
                        {'claim_id': cid, 'run_id': '../other'}, {'claim_id': cid, 'document': 'other'}]:
            with self.assertRaises(ValueError):
                document_trace(self.paths, **{'document': 'doc', **options})
        with self.assertRaises(ValueError):
            debug_catalog(self.paths, 'visual', 'open-document-survey')

    def test_missing_call_history_is_not_invented_from_final_result(self):
        folder, cid, key = self.setup_document()
        (folder / 'inputs' / (key + '.json')).unlink()
        (folder / 'cache' / (key + '.json')).unlink()
        self.assertEqual(document_trace(self.paths, document='doc', claim_id=cid)['calls'], [])
        info = debug_call(self.paths, route='document', run_id='live', document='doc', key=key)
        self.assertIsNone(info['input'])
        self.assertIsNone(info['exchange'])

    def test_raw_review_comes_from_provider_not_mutated_legacy_cache(self):
        folder, cid, key = self.setup_document()
        from slopekg.storage import read_json
        run = read_json(folder / 'run.json')
        run['receipts'][0]['stage'] = 'document_reconcile'
        write_json(folder / 'run.json', run)
        raw = {'claim_id': cid, 'verdict': 'supported', 'reason': 'original words'}
        enriched = {**raw, 'rechecked_evidence': {'quality_issues': []}}
        write_json(folder / 'cache' / (key + '.json'), {'answer': {
            '_document_raw': {'reviews': [enriched]}, '_document_validated': {'reviews': [enriched]}}})
        write_json(folder / 'exchanges' / (key + '.json'), {'attempts': [{'response': {
            'choices': [{'message': {'content': json.dumps({'reviews': [raw]})}}]}}]})
        result = document_trace(self.paths, document='doc', claim_id=cid)['calls'][0]
        self.assertEqual(result['raw_detail_origin'], 'provider_response_content')
        self.assertNotIn('rechecked_evidence', result['raw_reviews'][0])
        self.assertIn('rechecked_evidence', result['validated_reviews'][0])

    def test_document_snapshot_can_be_reopened_and_filtered_in_history(self):
        _, cid, _ = self.setup_document()
        from slopekg.debug_history import save_snapshot, list_records
        record = save_snapshot(self.paths, route='document', run_id='live', document='doc', label='Document debug test')
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(list_records(self.paths, route='document')['count'], 1)
        result = document_trace(self.paths, run_id='history-'+record['id'], document='doc', claim_id=cid)
        self.assertEqual(result['final']['id'], cid)

    def test_trace_http_dispatch(self):
        _, cid, _ = self.setup_document()
        handler = object.__new__(SlopeKGHandler)
        handler.path = '/api/recognition-debug/document-trace?route=document&document=doc&claim='+cid
        handler.write_json = Mock()
        with patch('slopekg.server.PATHS', self.paths):
            handler.do_GET()
        self.assertEqual(handler.write_json.call_args.args[0]['claim_id'], cid)
