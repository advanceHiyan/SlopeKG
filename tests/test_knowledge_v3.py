"""Offline v3 mechanism checks; fixture counts are not real-document accuracy."""
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from slopekg import knowledge_prompts as prompts
from slopekg.knowledge_document import prepare_document
from slopekg.knowledge_composition import prepare_bindings, gate_ownership, concrete_subject
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings, VERSION
from slopekg.parsers import PdfParser
from slopekg.storage import read_json, write_json
from tests.test_knowledge_pipeline import KnowledgeModel, document
from tests.test_knowledge_ownership import fixture


class BatchModel(KnowledgeModel):
    def __init__(self, *, require_review=False, omit_object=False, bad_quote=False):
        super().__init__()
        self.require_review, self.omit_object, self.invalid_quote = require_review, omit_object, bad_quote

    def complete(self, *, system, prompt, images):
        instruction, raw = prompt.split('\n输入：', 1)
        if instruction != prompts.RECOMPOSE:
            return super().complete(system=system, prompt=prompt, images=images)
        context = json.loads(raw)
        self.events.append((instruction, copy.deepcopy(context)))
        outputs = []
        for subject in context['subjects']:
            facts = []
            if subject['name'] == self.name:
                for span in context['source_spans']:
                    if span['id'] not in subject['allowed_span_ids']:
                        continue
                    for marker, field, value, unit in [('pressure 2.50', 'pressure', '2.50', 'MPa'),
                                                        ('QA17', 'control tag', 'QA17', '')]:
                        if marker in span['text']:
                            facts.append({'field': field, 'value': value, 'unit': unit, 'unit_basis': '',
                                'scope': subject['name'], 'role': 'observation',
                                'condition': 'cold' if field == 'pressure' else '', 'time': '',
                                'qualifier': '', 'review_required': self.require_review,
                                'evidence': [{'span_id': span['id'], 'quote':
                                    'Invented quote' if self.invalid_quote else span['text']}]})
            outputs.append({'entity_id': subject['id'], 'facts': facts, 'relations': [], 'unresolved': []})
        if self.omit_object:
            outputs.pop()
        return {'objects': outputs}, {'model': 'offline-batch-fixture', 'usage': {'total_tokens': 0}}


class KnowledgeV3Tests(unittest.TestCase):
    def test_carry_forward_keeps_uncertain_candidates_pending_when_synthesis_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'source.pdf'; document(pdf); model = BatchModel(); out = root / 'out'
            KnowledgePipeline(model, out).run([pdf])
            saved = read_json(out / 'run.json')
            saved['documents'][0]['facts'][0].update(status='pending', reason='uncertain_identity')
            write_json(out / 'run.json', saved)
            for path in (out / 'cache').glob('*.json'):
                if read_json(path).get('stage') == 'knowledge_synthesize': path.unlink()
            replay = KnowledgePipeline(model, out, settings=KnowledgeSettings(allow_model_calls=False)).run([pdf])
            self.assertEqual(replay['summary']['calls'], 0)
            self.assertEqual(replay['summary']['pending'], 1)
            self.assertEqual(replay['summary']['source_aligned'], 1)
            self.assertEqual(replay['summary']['verified_model'], 0)
    def test_optional_review_limit_is_shared_by_the_entire_multi_document_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); one, two = root / 'one.pdf', root / 'two.pdf'
            document(one); document(two); model = BatchModel(require_review=True)
            run = KnowledgePipeline(model, root / 'out', settings=KnowledgeSettings(max_review_calls=1)).run([one, two])
            self.assertEqual(sum(p == prompts.VERIFY for p, _ in model.events), 1)
            self.assertEqual(sum(len(d.get('review_jobs', [])) for d in run['documents']), 1)
            self.assertTrue(all(f['status'] == 'pending' for f in run['documents'][1]['facts']))

    def test_discovery_visits_late_objects_and_default_path_survives_domain_and_file_renaming(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'arbitrary.pdf'
            source = fitz.open()
            texts = ['Archive Sample P is an independently identified collection.'] + [
                'Administrative retention follows the original policy and preserves provenance. ' * 5] * 25 + [
                'Archive Sample Q is another independently identified collection.']
            for text in texts:
                page = source.new_page(); page.insert_textbox(fitz.Rect(30, 30, 550, 300), text)
            source.save(pdf); source.close()
            model = BatchModel(); model.name, model.other = 'Archive Sample P', 'Archive Sample Q'
            settings = KnowledgeSettings(chunk_chars=600, batch_chars=4000)
            run = KnowledgePipeline(model, root / 'out', settings=settings).run([pdf])
            doc = run['documents'][0]
            self.assertEqual({e['name'] for e in doc['entities']}, {model.name, model.other})
            self.assertGreater(len(doc['discovery_batches']), 1)
            self.assertEqual(doc['coverage']['attributed_chunks'], len(doc['chunks']))
            renamed = root / 'completely-different-name.pdf'; renamed.write_bytes(pdf.read_bytes())
            one, two = prepare_document(pdf, settings), prepare_document(renamed, settings)
            self.assertEqual(one['units'], two['units']); self.assertEqual(one['chunks'], two['chunks'])

    def test_default_batches_objects_and_has_no_blanket_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'source.pdf'; document(pdf); model = BatchModel()
            run = KnowledgePipeline(model, root / 'out').run([pdf])
            self.assertEqual(run['summary']['calls'], 3)
            self.assertEqual(run['summary']['source_aligned'], 2)
            self.assertEqual(run['summary']['verified_model'], 0)
            self.assertFalse(any(p == prompts.VERIFY for p, _ in model.events))
            context = next(c for p, c in model.events if p == prompts.RECOMPOSE)
            subject = next(s for s in context['subjects'] if s['name'] == model.name)
            spans = [s for s in context['source_spans'] if s['id'] in subject['allowed_span_ids']]
            self.assertTrue({1, 5, 7} <= {s['page'] for s in spans})
            self.assertEqual(len(run['documents'][0]['object_pages']), 2)
            count = len(model.events)
            replay = KnowledgePipeline(model, root / 'out', settings=KnowledgeSettings(allow_model_calls=False)).run([pdf])
            self.assertEqual(replay['summary']['calls'], 0)
            self.assertEqual(len(model.events), count)
            self.assertEqual(replay['summary']['source_aligned'], 2)

    def test_ambiguity_review_is_bounded_and_cannot_promote_bad_quotes(self):
        for cap, invalid, verified in [(0, False, 0), (1, False, 2), (1, True, 0)]:
            with self.subTest(cap=cap, invalid=invalid), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); pdf = root / 'source.pdf'; document(pdf)
                run = KnowledgePipeline(BatchModel(require_review=True, bad_quote=invalid), root / 'out',
                    settings=KnowledgeSettings(max_review_calls=cap)).run([pdf])
                self.assertEqual(run['summary']['verified_model'], verified)
                self.assertEqual(run['summary']['calls'], 3 + (1 if cap and not invalid else 0))
                if not cap or invalid:
                    self.assertEqual(run['summary']['pending'], 2)

    def test_omitted_object_remains_partial_and_is_not_automatically_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'source.pdf'; document(pdf); model = BatchModel(omit_object=True)
            run = KnowledgePipeline(model, root / 'out').run([pdf])
            self.assertEqual(run['summary']['calls'], 3)
            self.assertEqual(run['processing_status'], 'partial')
            jobs = run['documents'][0]['synthesis_jobs']
            self.assertEqual(sum(j['status'] == 'failed' for j in jobs), 1)
            self.assertEqual(sum(j['status'] == 'completed' for j in jobs), 1)
            count = len(model.events)
            KnowledgePipeline(model, root / 'out').run([pdf])
            self.assertEqual(len(model.events), count)

    def test_each_extra_foreign_citation_blocks_a_correct_value_locally(self):
        doc = fixture()
        doc['chunks'] = [{'unit_ids': [u['id'] for u in doc['units']], 'id': 'c1', 'attribution_status': 'completed'}]
        for entity in doc['entities']:
            entity['chunk_ids'] = []
        prepare_bindings(doc)
        units = {u['id']: u for u in doc['units']}
        known = {e['id']: e for e in doc['entities']}
        with tempfile.TemporaryDirectory() as temp:
            pipeline = KnowledgePipeline(BatchModel(), temp)
            raw = {'field': 'pressure', 'value': '2.50', 'unit': 'MPa', 'scope': 'Reactor A',
                'role': 'observation', 'evidence': [{'span_id': 'a', 'quote': units['a']['text']},
                                                  {'span_id': 'b', 'quote': units['b']['text']}]}
            item = pipeline._item(doc, known['A'], 'fact', raw, units, set(units), known)
            gate_ownership(doc, item, units)
            self.assertEqual(item['status'], 'pending')
            self.assertIn('foreign_object_section_evidence', item['quality_issues'])
            self.assertEqual(pipeline.runtime.calls, 0)

    def test_shared_table_rows_keep_their_subject_and_topic_rebinding_never_transfers_review(self):
        doc = fixture()
        doc['units'] = [{'id': key, 'page': 1, 'text': text, 'bbox': [0, 0, 1, 1],
                         'source': 'pdf_table', 'is_header': False} for key, text in
                        [('a', 'Reactor A | 2.50 | MPa'), ('b', 'Reactor B | 2.50 | MPa')]]
        doc['chunks'] = [{'id': 'c1', 'unit_ids': ['a', 'b'], 'attribution_status': 'completed'}]
        for entity in doc['entities']:
            entity['chunk_ids'] = []
        prepare_bindings(doc); known = {e['id']: e for e in doc['entities']}; units = {u['id']: u for u in doc['units']}
        raw = {'field': 'pressure', 'value': '2.50', 'unit': 'MPa', 'scope': 'Reactor A', 'role': 'observation',
               'evidence': [{'span_id': 'a', 'quote': units['a']['text']}]}
        self.assertEqual(concrete_subject(doc, known['T'], raw, known)['id'], 'A')
        with tempfile.TemporaryDirectory() as temp:
            pipeline = KnowledgePipeline(BatchModel(), temp)
            item = pipeline._item(doc, known['A'], 'fact', raw, units, {'a'}, known)
            gate_ownership(doc, item, units)
            self.assertEqual(item['status'], 'source_aligned')
            wrong = {**raw, 'evidence': [{'span_id': 'b', 'quote': units['b']['text']}]}
            item = pipeline._item(doc, known['A'], 'fact', wrong, units, {'b'}, known)
            gate_ownership(doc, item, units)
            self.assertEqual(item['status'], 'pending')
            self.assertEqual(pipeline.runtime.calls, 0)

    def test_historical_schema_is_archived_without_promoting_old_badges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'source.pdf'; document(pdf); out = root / 'out'
            old = {'schema_version': 'knowledge-v2', 'documents': [], 'summary': {'verified_model': 1605}}
            write_json(out / 'run.json', old)
            run = KnowledgePipeline(BatchModel(), out).run([pdf], prepare_only=True)
            self.assertEqual(read_json(next((out / 'history').glob('*.json'))), old)
            self.assertEqual(run['schema_version'], VERSION)
            self.assertEqual(run['summary']['verified_model'], 0)


def table_and_scan(path):
    pdf = fitz.open(); page = pdf.new_page()
    for x in [30, 240, 450]:
        page.draw_line((x, 50), (x, 140))
    for y in [50, 80, 110, 140]:
        page.draw_line((30, y), (450, y))
    for y, left, right in [(70, 'Object', 'Pressure (MPa)'), (100, 'Reactor A', '2.50'), (130, 'Reactor B', '3.50')]:
        page.insert_text((40, y), left); page.insert_text((250, y), right)
    image_doc = fitz.open(); image_page = image_doc.new_page()
    image_page.insert_text((40, 80), 'Archive Sample C retained for 12 months.')
    png = image_page.get_pixmap().tobytes('png'); image_doc.close()
    page = pdf.new_page(); page.insert_image(page.rect, stream=png)
    pdf.save(path); pdf.close()


class KnowledgeSourceReuseTests(unittest.TestCase):
    def test_deduplication_never_joins_digits_across_table_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'source.pdf'
            source = fitz.open(); page = source.new_page()
            page.insert_text((40, 70), '12')
            page.insert_text((40, 100), 'Independent records preserve the complete original textual evidence.')
            source.save(pdf); source.close()
            parsed = asdict(PdfParser(root).parse(pdf, generic=True)); record = parsed['documents'][0]
            parsed['tables'] = [{'id': 'split-table', 'document_id': record['id'], 'page': 1,
                                 'bbox': [0, 0, 500, 150], 'rows': [['1', '2']]}]
            cache = root / 'parsed.json'; write_json(cache, parsed)
            doc = prepare_document(pdf, KnowledgeSettings(), parsed_dir=cache)
            self.assertTrue(any(u['source'] == 'pdf_native' and u['text'].strip() == '12' for u in doc['units']))
    def test_real_generic_table_parser_and_byte_matched_ocr_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'anything.pdf'; table_and_scan(pdf)
            parsed = asdict(PdfParser(root).parse(pdf, generic=True))
            self.assertTrue(parsed['tables'])
            task = next(t for t in parsed['ocr_tasks'] if t['page'] == 2); task['status'] = 'done'
            parsed['ocr_results'] = [{'id': 'ocr-one', 'task_id': task['id'], 'document_id': task['document_id'],
                'page': 2, 'text': 'Archive Sample C retained for 12 months.', 'source_sha256': task['source_sha256'],
                'confidence': .98, 'bbox': [20, 20, 300, 60], 'bbox_coordinate_space': 'image_pixels'}]
            cache = root / 'parsed.json'; write_json(cache, parsed)
            with patch('slopekg.knowledge_document.PdfParser.parse', side_effect=AssertionError('must reuse')):
                doc = prepare_document(pdf, KnowledgeSettings(), parsed_dir=cache)
            self.assertEqual(doc['source_adapter'], 'basic_cache')
            self.assertEqual(doc['source_inventory']['ocr_rows_reused'], 1)
            data = next(u for u in doc['units'] if u.get('cells') and '2.50' in u['cells'])
            self.assertTrue(data['header_ids'])
            self.assertTrue(any(u['source'] == 'ocr' and u['bbox'] is None for u in doc['units']))
            self.assertTrue(doc['pages'][1]['graphics_unread'])
            self.assertTrue(any('Reactor B' in u['text'] and u['source'] == 'pdf_table' for u in doc['units']))
            parsed['ocr_results'][0]['source_sha256'] = 'outdated'; write_json(cache, parsed)
            stale = prepare_document(pdf, KnowledgeSettings(), parsed_dir=cache)
            self.assertEqual(stale['source_inventory']['ocr_rows_reused'], 0)

    def test_bounded_local_ocr_runner_is_cached_and_prepare_never_runs_it(self):
        class LocalOcr:
            def __init__(self): self.caps = []
            def run(self, tasks, max_pages):
                self.caps.append(max_pages); task = next(t for t in tasks if t['page'] == 2)
                return [{**t, 'status': 'done' if t['id'] == task['id'] else t['status']} for t in tasks], [
                    {'id': 'local-one', 'task_id': task['id'], 'page': 2, 'text': 'Archive Sample C retained for 12 months.',
                     'source_sha256': task['source_sha256'], 'confidence': .97, 'bbox': [1, 2, 3, 4],
                     'bbox_coordinate_space': 'pdf_points'}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); pdf = root / 'source.pdf'; table_and_scan(pdf); local = LocalOcr()
            settings = KnowledgeSettings(ocr_pages=1)
            prepare_document(pdf, settings, run_ocr=False, ocr_runner=local)
            self.assertEqual(local.caps, [])
            doc = prepare_document(pdf, settings, run_ocr=True, ocr_runner=local, ocr_cache_dir=root / 'ocr')
            self.assertEqual(local.caps, [1]); self.assertEqual(doc['source_inventory']['ocr_rows_reused'], 1)
            replay = prepare_document(pdf, settings, run_ocr=False, ocr_cache_dir=root / 'ocr')
            self.assertEqual(replay['source_inventory']['ocr_rows_reused'], 1)
            self.assertEqual(local.caps, [1])


if __name__ == '__main__':
    unittest.main()
