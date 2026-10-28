"""Third-route regressions with anonymous, non-slope source documents.

These test workflow/evidence invariants, never real model quality.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import fitz

from slopekg import knowledge_prompts as prompts
from slopekg.knowledge_document import prepare_document
from slopekg.knowledge_pipeline import KnowledgePipeline, resolve_source_handle
from slopekg.visual_contract import ContractError
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.knowledge_service import knowledge_job_options, knowledge_payload
from slopekg.model_client import ModelError
from slopekg.pipeline import DemoPipeline
from slopekg.storage import write_json, read_json
from tests.support import workspace_paths


def document(path, name='Reactor A', other='Reactor B', sparse=False):
    pdf = fitz.open()
    texts = [f'{name} is a production reactor with a separately identified inspection record.',
             f'{other} is another reactor. It is separate from {name}; both have alias R.',
             'Maintenance records retain the original observation time and the operating condition.',
             'This intermediate page describes independent archive retention and document provenance.',
             f'Control tag QA17 belongs to {name}. This is an equipment control identifier.',
             'This additional page contains administrative instructions about storage and retrieval.',
             f'{name} pressure 2.50 MPa under cold condition. The value is an observed test result.']
    for text in texts:
        page = pdf.new_page()
        page.insert_textbox(fitz.Rect(30, 30, 550, 220), text, fontsize=11)
    if sparse:
        page = pdf.new_page()
        page.draw_rect(fitz.Rect(50, 50, 400, 600))
        page.insert_text((20, 20), '8')
    pdf.save(path)
    pdf.close()


class KnowledgeModel:
    def __init__(self, name='Reactor A', other='Reactor B', *, bad_quote=False, missing_chunk=False,
                 invented_value=False, fail_review=False):
        self.name, self.other = name, other
        self.bad_quote, self.missing_chunk = bad_quote, missing_chunk
        self.invented_value, self.fail_review = invented_value, fail_review
        self.events = []

    def identity(self):
        return {'name': 'offline-knowledge-fixture', 'model': 'generic-test'}

    def complete(self, *, system, prompt, images):
        instruction, raw = prompt.split('\n输入：', 1)
        context = json.loads(raw)
        self.events.append((instruction, copy.deepcopy(context)))
        assert not images
        if instruction == prompts.DISCOVER:
            spans = [s for c in context['chunks'] for s in c['spans']]
            entities = []
            for name in [self.name, self.other]:
                source = next((s for s in spans if name in s['text']), None)
                if source:
                    old = next((e['id'] for e in context['registry'] if e['name'] == name), '')
                    entities.append({'id': old, 'name': name, 'kind': 'entity', 'aliases': ['R'],
                        'description': 'A separately identified object.',
                        'mentions': [{'span_id': source['id'], 'quote': source['text']}]})
            answer = {'entities': entities}
        elif instruction == prompts.ATTRIBUTE:
            rows = []
            for chunk in context['chunks']:
                bindings = []
                for e in context['registry']:
                    ids = [s['id'] for s in chunk['spans'] if e['name'] in s['text']]
                    if ids:
                        bindings.append({'entity_id': e['id'], 'span_ids': ids, 'reason': 'Explicit object name'})
                rows.append({'chunk_id': chunk['id'], 'entities': bindings, 'unresolved': []})
            answer = {'assignments': rows[:-1] if self.missing_chunk else rows}
        elif instruction == prompts.SYNTHESIZE:
            answer = {'facts': [], 'relations': []}
            if context['subject']['name'] == self.name:
                for s in context['source_spans']:
                    if 'pressure 2.50' in s['text']:
                        answer['facts'].append({'field': 'pressure', 'value': '2.51' if self.invented_value else '2.50',
                            'unit': 'MPa', 'unit_basis': '', 'scope': self.name, 'role': 'observation',
                            'qualifier': '', 'condition': 'cold', 'time': '',
                            'evidence': [{'span_id': s['id'], 'quote': 'Fabricated quote 2.50 MPa' if self.bad_quote else s['text']}]})
                    if 'QA17' in s['text']:
                        answer['facts'].append({'field': 'control tag', 'value': 'QA17', 'unit': '', 'unit_basis': '',
                            'scope': self.name, 'role': 'observation', 'qualifier': '', 'condition': '', 'time': '',
                            'evidence': [{'span_id': s['id'], 'quote': s['text']}]})
        else:
            assert instruction == prompts.VERIFY
            if self.fail_review:
                raise ModelError('Verification unavailable')
            answer = {'verdicts': [{'id': f['id'], 'decision': 'supported', 'source_supported': True,
                'identity_supported': True, 'meaning_supported': True, 'scope_supported': True,
                'qualifiers_supported': True, 'reason': 'Source fixture agrees'} for f in context['items']]}
        return answer, {'model': 'generic-test', 'usage': {'total_tokens': 0}, 'finish_reason': 'stop'}


class KnowledgeWorkflowTests(unittest.TestCase):
    def test_unread_expanded_context_does_not_erase_previously_source_checked_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf);model=KnowledgeModel();out=root/'out'
            first=KnowledgePipeline(model,out).run([pdf]);count=len(model.events)
            for path in (out/'cache').glob('*.json'):
                if read_json(path).get('stage')=='knowledge_synthesize':path.unlink()
            second=KnowledgePipeline(model,out,settings=KnowledgeSettings(allow_model_calls=False)).run([pdf])
            self.assertEqual(len(model.events),count)
            self.assertEqual(second['summary']['calls'],0)
            self.assertEqual(second['summary']['verified_model'],first['summary']['verified_model'])
            self.assertEqual(second['summary']['carried_forward_facts'],2)
            self.assertEqual(second['processing_status'],'partial')
            self.assertTrue(any(j['status']=='failed' for j in second['documents'][0]['synthesis_jobs']))

    def test_carry_forward_rechecks_quotes_and_never_promotes_a_tampered_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf);model=KnowledgeModel();out=root/'out'
            KnowledgePipeline(model,out).run([pdf]);run=read_json(out/'run.json')
            run['documents'][0]['facts'][0]['evidence'][0]['quote']='Invented original quote'
            write_json(out/'run.json',run)
            for path in (out/'cache').glob('*.json'):
                if read_json(path).get('stage')=='knowledge_synthesize':path.unlink()
            replay=KnowledgePipeline(model,out,settings=KnowledgeSettings(allow_model_calls=False)).run([pdf])
            self.assertEqual(replay['summary']['verified_model'],1)

    def test_offset_omission_requires_one_whole_provided_block_and_never_guesses_a_fragment(self):
        units={'p1b0t0':{},'p2b0t0':{},'p2b0t300':{}}
        self.assertEqual(resolve_source_handle('p1b0',units,{'p1b0t0'}),'p1b0t0')
        for handle,allowed in [('p2b0',{'p2b0t0'}),('p1b0',{'p2b0t0'}),('p99b0',set())]:
            with self.subTest(handle=handle,allowed=allowed),self.assertRaises(ContractError):
                resolve_source_handle(handle,units,allowed)

    def test_explicit_ranges_expand_only_the_actual_provided_source_units(self):
        class RangeModel(KnowledgeModel):
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.ATTRIBUTE):
                    for row in answer['assignments']:
                        for binding in row['entities']:
                            ids=binding['span_ids']
                            binding['span_ranges']=[{'start_id':ids[0],'end_id':ids[-1]}]
                            binding['span_ids']=[]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf)
            run=KnowledgePipeline(RangeModel(),root/'out').run([pdf])
            self.assertEqual(run['summary']['verified_model'],2)
            self.assertEqual(run['processing_status'],'completed')

    def test_an_unprovided_source_range_endpoint_is_rejected(self):
        class BadRange(KnowledgeModel):
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.ATTRIBUTE):
                    b=answer['assignments'][0]['entities'][0]
                    b['span_ranges']=[{'start_id':b['span_ids'][0],'end_id':'p999b0t0'}]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf)
            run=KnowledgePipeline(BadRange(),root/'out').run([pdf])
            self.assertEqual(run['summary']['verified_model'],0)
            self.assertEqual(run['processing_status'],'partial')

    def test_explicit_null_relations_adds_no_claim_and_keeps_valid_fact_evidence(self):
        class NullRelations(KnowledgeModel):
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.SYNTHESIZE):answer['relations']=None
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf)
            run=KnowledgePipeline(NullRelations(),root/'out').run([pdf])
            self.assertEqual(run['summary']['verified_model'],2)
            self.assertEqual(run['summary']['relations'],0)

    def test_actual_source_ids_recover_wrong_chunk_containers_and_duplicate_bindings(self):
        class WrongContainer(KnowledgeModel):
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.ATTRIBUTE):
                    rows=answer['assignments'];labels=[r['chunk_id'] for r in rows]
                    for row,label in zip(rows,reversed(labels)):
                        row['chunk_id']=label
                        row['entities']+=copy.deepcopy(row['entities'])
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf)
            with fitz.open(pdf) as source:
                for _ in range(2):
                    source.new_page().insert_text((30,50),'Independent administrative records preserve original document provenance and storage history.')
                source.saveIncr()
            run=KnowledgePipeline(WrongContainer(),root/'out',settings=KnowledgeSettings(chunk_chars=600,batch_chars=4000)).run([pdf])
            doc=run['documents'][0]
            self.assertGreater(len(doc['chunks']),1)
            self.assertEqual(doc['coverage']['attributed_chunks'],len(doc['chunks']))
            self.assertEqual(run['summary']['verified_model'],2)
            self.assertTrue(any(b['source_chunk_repairs'] for b in doc['attribution_batches']))

    def test_source_index_repair_never_accepts_a_span_not_shown_in_that_request(self):
        class UnseenSource(KnowledgeModel):
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.ATTRIBUTE):
                    answer['assignments'][0]['entities'][0]['span_ids'].append('p999b0t0')
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf)
            run=KnowledgePipeline(UnseenSource(),root/'out').run([pdf])
            self.assertEqual(run['processing_status'],'partial')
            self.assertEqual(run['summary']['verified_model'],0)
            self.assertIn('unprovided source',run['documents'][0]['attribution_batches'][0]['error'])

    def test_returned_invalid_packaging_can_be_revalidated_without_another_model_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf);model=KnowledgeModel();out=root/'out'
            KnowledgePipeline(model,out).run([pdf]);count=len(model.events)
            for path in (out/'cache').glob('*.json'):
                value=read_json(path)
                if value.get('stage')=='knowledge_attribute':
                    value.update(status='failed',invalid_answer=value.pop('answer'),error='Previous container validator rejected')
                    write_json(path,value)
            run=KnowledgePipeline(model,out,settings=KnowledgeSettings(allow_model_calls=False)).run([pdf])
            self.assertEqual(len(model.events),count)
            self.assertEqual(run['summary']['calls'],0)
            self.assertEqual(run['summary']['verified_model'],2)
            self.assertTrue(any(read_json(p).get('revalidated_without_request') for p in (out/'cache').glob('*.json')))

    def test_catalog_promotes_to_body_object_and_background_keeps_its_evidence_without_using_focus_budget(self):
        class LayeredModel(KnowledgeModel):
            def complete(self, **kwargs):
                answer,receipt=super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.DISCOVER):
                    a,b=answer['entities'];catalog=copy.deepcopy(a);catalog['salience']='catalog'
                    a['salience']='focus';b['salience']='support'
                    answer['entities']=[catalog,b,a]
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf)
            run=KnowledgePipeline(LayeredModel(),root/'out',settings=KnowledgeSettings(max_entities=1)).run([pdf])
            doc=run['documents'][0]
            self.assertEqual([e['name'] for e in doc['entities']],['Reactor A'])
            self.assertEqual([e['name'] for e in doc['references']],['Reactor B'])
            self.assertTrue(doc['references'][0]['mentions'])
            self.assertEqual(run['summary']['verified_model'],2)
            self.assertNotIn('entity_budget_exhausted',doc['errors'])

    def test_same_name_in_different_parent_contexts_keeps_two_identities(self):
        class ContextModel(KnowledgeModel):
            def complete(self, **kwargs):
                answer, receipt = super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.DISCOVER):
                    for row in answer['entities']:
                        row['context'] = row['name']
                        row['name'] = 'R'
                return answer, receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'anonymous.pdf';document(pdf)
            run=KnowledgePipeline(ContextModel(),root/'out').run([pdf])
            entities=run['documents'][0]['entities']
            self.assertEqual(len(entities),2)
            self.assertEqual({e['context'] for e in entities},{'Reactor A','Reactor B'})
            self.assertEqual(len({e['id'] for e in entities}),2)

    def test_duplicate_assertions_are_not_counted_twice(self):
        class DuplicatingModel(KnowledgeModel):
            def complete(self, **kwargs):
                answer, receipt=super().complete(**kwargs)
                if kwargs['prompt'].startswith(prompts.SYNTHESIZE):
                    answer['facts'] += copy.deepcopy(answer['facts'])
                return answer,receipt
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'anonymous.pdf';document(pdf)
            run=KnowledgePipeline(DuplicatingModel(),root/'out').run([pdf])
            self.assertEqual(run['summary']['facts'],2)
            self.assertEqual(run['summary']['verified_model'],2)
            self.assertTrue(all(f['occurrences']==2 for f in run['documents'][0]['facts']))

    def test_budget_exhaustion_preserves_partial_coverage_and_unprocessed_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'anonymous.pdf';document(pdf)
            run=KnowledgePipeline(KnowledgeModel(),root/'out',settings=KnowledgeSettings(max_calls=1)).run([pdf])
            self.assertEqual(run['summary']['calls'],1)
            self.assertEqual(run['processing_status'],'partial')
            self.assertEqual(run['documents'][0]['coverage']['attributed_chunks'],0)
            self.assertTrue(all(c['attribution_status']=='pending' for c in run['documents'][0]['chunks']))

    def test_remote_evidence_is_reunited_before_fact_reading_and_shared_aliases_do_not_merge_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);pdf = root / 'anonymous.pdf';document(pdf)
            model = KnowledgeModel()
            result = KnowledgePipeline(model, root / 'out').run([pdf])
            doc = result['documents'][0]
            self.assertEqual(len(doc['entities']), 2)
            read = next(c for stage, c in model.events if stage == prompts.SYNTHESIZE and c['subject']['name'] == model.name)
            self.assertTrue({1, 5, 7} <= {u['page'] for u in read['source_spans']})
            self.assertEqual({f['value'] for f in doc['facts'] if f['status'] == 'verified_model'}, {'QA17', '2.50'})
            self.assertEqual(result['recognizer'], 'knowledge')
            self.assertEqual(result['processing_status'], 'completed')
            self.assertIsNone(result['precision'])

    def test_false_quotes_and_invented_numeric_values_cannot_be_promoted_by_agreeing_reviewer(self):
        for kwargs in [{'bad_quote': True}, {'invented_value': True}]:
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as directory:
                root = Path(directory);pdf = root/'source.pdf';document(pdf)
                result = KnowledgePipeline(KnowledgeModel(**kwargs), root/'out').run([pdf])
                pressure = next(f for f in result['documents'][0]['facts'] if f['field'] == 'pressure')
                self.assertEqual(pressure['status'], 'pending')
                self.assertTrue(pressure['quality_issues'])

    def test_silent_chunk_omission_and_failed_verification_stay_partial(self):
        for kwargs in [{'missing_chunk': True}, {'fail_review': True}]:
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as directory:
                root = Path(directory);pdf = root/'source.pdf';document(pdf)
                result = KnowledgePipeline(KnowledgeModel(**kwargs), root/'out').run([pdf])
                self.assertEqual(result['processing_status'], 'partial')
                self.assertTrue(all(f['status'] != 'verified_model' for f in result['documents'][0]['facts']))

    def test_graphical_page_is_never_called_blank_or_complete_by_text_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);pdf=root/'graphics.pdf';document(pdf, sparse=True)
            result=KnowledgePipeline(KnowledgeModel(),root/'out').run([pdf])
            doc=result['documents'][0]
            self.assertEqual(result['processing_status'],'partial')
            self.assertEqual(doc['coverage']['text_processing_status'],'completed')
            self.assertEqual(doc['coverage']['unread_graphics_pages'],[8])

    def test_new_domain_and_renamed_file_do_not_change_source_driven_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'arbitrary.pdf';document(source, name='Archive Sample P', other='Archive Sample Q')
            renamed=root/'irrelevant-filename.pdf';renamed.write_bytes(source.read_bytes())
            a=prepare_document(source,KnowledgeSettings());b=prepare_document(renamed,KnowledgeSettings())
            self.assertEqual(a['chunks'],b['chunks']);self.assertEqual(a['units'],b['units'])
            result=KnowledgePipeline(KnowledgeModel('Archive Sample P','Archive Sample Q'),root/'out').run([renamed])
            self.assertEqual(result['summary']['verified_model'],2)

    def test_prepare_and_cached_replay_do_not_charge_again_or_touch_other_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pdf=root/'source.pdf';document(pdf);model=KnowledgeModel();out=root/'out'
            (root/'recognition').mkdir();old=root/'recognition/run.json';old.write_text('unchanged')
            p=KnowledgePipeline(model,out).run([pdf],prepare_only=True)
            self.assertEqual(model.events,[]);self.assertEqual(p['processing_status'],'prepared')
            first=KnowledgePipeline(model,out).run([pdf]);count=len(model.events)
            replay=KnowledgePipeline(model,out,settings=KnowledgeSettings(allow_model_calls=False)).run([pdf])
            self.assertEqual(len(model.events),count);self.assertEqual(replay['summary']['calls'],0)
            self.assertEqual(replay['summary']['verified_model'],first['summary']['verified_model'])
            self.assertEqual(old.read_text(),'unchanged')


class KnowledgeServiceTests(unittest.TestCase):
    def test_changed_source_cannot_keep_a_current_verified_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);paths=workspace_paths(root);paths.raw_dir.mkdir(parents=True)
            pdf=paths.raw_dir/'source.pdf';document(pdf)
            KnowledgePipeline(KnowledgeModel(),paths.output_dir/'knowledge-recognition').run([pdf])
            self.assertEqual(knowledge_payload(paths)['summary']['verified_model'],2)
            pdf.write_bytes(pdf.read_bytes()+b'\n% changed source')
            payload=knowledge_payload(paths)
            self.assertEqual(payload['processing_status'],'stale')
            self.assertEqual(payload['summary']['verified_model'],0)
            self.assertTrue(all(f['status']=='pending' and f['reason']=='source_changed' for f in payload['rows']))

    def test_third_route_http_permissions_and_result_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);paths=workspace_paths(root);paths.raw_dir.mkdir(parents=True)
            pdf=paths.raw_dir/'generic.pdf';document(pdf);(root/'config/models').mkdir(parents=True)
            write_json(root/'config/models/test.json',{'name':'fixture','base_url':'https://example.invalid',
                'credential_endpoint':'https://example.invalid','model':'test','api_key_file':'.secrets/key.txt'})
            body={'recognizer':'knowledge','input_pdfs':['generic.pdf'],'model_profile':'test'}
            options=knowledge_job_options(paths,body)
            self.assertTrue(options['prepare_only']);self.assertEqual(options['recognizer'],'knowledge')
            with self.assertRaises(ValueError):knowledge_job_options(paths,{**body,'prepare_only':False})
            with self.assertRaises(ValueError):knowledge_job_options(paths,{**body,'input_pdfs':['../generic.pdf']})
            with self.assertRaises(ValueError):knowledge_job_options(paths,{**body,'knowledge_settings':{'retry_failed':True}})
            state=DemoPipeline(paths).run(recognizer='knowledge',input_pdfs=[pdf],model_profile=root/'config/models/test.json',prepare_only=True)
            payload=knowledge_payload(paths)
            self.assertEqual(state['recognizer'],'knowledge');self.assertNotIn('receipts',payload)
            self.assertFalse(paths.graph_json.exists());self.assertFalse((paths.output_dir/'recognition/run.json').exists())


if __name__ == '__main__':
    unittest.main()
