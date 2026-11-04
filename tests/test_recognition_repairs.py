"""Generic partition, geometry and transport regressions; no evaluation answers."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

import fitz

from slopekg.knowledge_document import prepare_document, normalized_box
from slopekg.knowledge_pipeline import KnowledgePipeline, partition_composition
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.model_client import ModelProfile, CompatibleVisionClient, ModelError
from slopekg.model_limits import DEEPSEEK_MAX_OUTPUT_TOKENS, KIMI_CONTEXT_TOKENS
from slopekg.parsers import PdfParser, GEOMETRY_VERSION
from slopekg.storage import write_json
from slopekg.visual_runtime import VisualRuntime
from tests.test_knowledge_v3 import BatchModel
from tests.test_knowledge_pipeline import document


def object_row(key, **changes):
    return {'entity_id': key, 'facts': [], 'relations': [], 'unresolved': [], **changes}


class CompositionRepairTests(unittest.TestCase):
    def test_extra_missing_duplicate_and_malformed_objects_do_not_poison_siblings(self):
        rows = [object_row('a', relations=None), object_row('b'), object_row('b'), object_row('c', facts='bad'),
                object_row('external'), {'wrong': 'shape'}]
        value = partition_composition({'objects': rows}, {'a', 'b', 'c', 'd'})
        self.assertEqual([r['entity_id'] for r in value['objects']], ['a'])
        self.assertEqual(value['objects'][0]['relations'], [])
        self.assertEqual(value['object_failures']['b'], 'duplicate_object')
        self.assertEqual(value['object_failures']['d'], 'missing_object')
        self.assertIn('c', value['object_failures'])
        self.assertEqual(partition_composition(value, {'a', 'b', 'c', 'd'}), value)

    def test_real_pipeline_keeps_requested_object_facts_when_model_returns_extra_objects(self):
        class Overresponding(BatchModel):
            def complete(self, **kwargs):
                answer, receipt = super().complete(**kwargs)
                if 'objects' in answer:
                    answer['objects'].append(object_row('not-in-this-batch'))
                return answer, receipt
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'anonymous.pdf'; document(source)
            result = KnowledgePipeline(Overresponding(), root/'out').run([source])
            self.assertEqual(result['summary']['calls'], 3)
            self.assertEqual(result['summary']['source_aligned'], 2)
            doc = result['documents'][0]
            self.assertTrue(all(j['status'] == 'completed' for j in doc['synthesis_jobs']))
            self.assertEqual(doc['composition_diagnostics'][0]['quarantined'][0]['reason'], 'unrequested_or_invalid_object')


class GeometryRepairTests(unittest.TestCase):
    def test_all_pdf_rotations_keep_native_boxes_in_display_frame(self):
        for rotation in [0, 90, 180, 270]:
            with self.subTest(rotation=rotation), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); source = root/'anonymous.pdf'
                pdf = fitz.open(); page = pdf.new_page(width=430, height=650)
                page.insert_text((35, 560), 'Unit Q7 pressure 4.25 kPa. Inspection evidence applies to this unit only.', fontsize=7)
                page.set_rotation(rotation); pdf.save(source); pdf.close()
                parsed = PdfParser(root).parse(source, generic=True)
                self.assertEqual(parsed.documents[0]['geometry_version'], GEOMETRY_VERSION)
                self.assertEqual(parsed.pages[0]['rotation'], rotation)
                doc = prepare_document(source, KnowledgeSettings(), root=root)
                self.assertTrue(doc['units'])
                for unit in doc['units']:
                    self.assertIsNone(unit.get('geometry_issue'))
                    box = unit['bbox']
                    self.assertTrue(0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1, box)

    def test_old_cache_refreshes_native_geometry_and_reuses_matching_ocr(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'anonymous.pdf'
            pdf = fitz.open(); page = pdf.new_page(width=430, height=650)
            page.insert_text((300, 570), 'Unit Q7 inspection', fontsize=9); page.set_rotation(90)
            image_page = pdf.new_page(); image_page.insert_image(image_page.rect,
                stream=pdf[0].get_pixmap().tobytes('png'))
            pdf.save(source); pdf.close()
            raw = asdict(PdfParser(root).parse(source, generic=True))
            self.assertTrue(raw['ocr_tasks'])
            raw['documents'][0].pop('geometry_version')
            raw['text_blocks'][0]['bbox'] = [300, 560, 410, 580]
            raw['text_blocks'][0].pop('bbox_coordinate_frame')
            task = raw['ocr_tasks'][0]; task['status'] = 'done'
            raw['ocr_results'] = [{'id':'cached-ocr','task_id':task['id'],'document_id':task['document_id'],
                'page':task['page'],'source_sha256':task['source_sha256'],'text':'Cached inspection note',
                'bbox':[10,10,30,30],'bbox_coordinate_space':'pdf_points'}]
            cache=root/'basic.json';write_json(cache,raw)
            doc=prepare_document(source,KnowledgeSettings(),parsed_dir=cache,root=root)
            self.assertEqual(doc['source_inventory']['ocr_rows_reused'],1)
            self.assertTrue(all(u['bbox'] is None or max(u['bbox'])<=1 for u in doc['units']))

    def test_unmapped_or_out_of_bounds_boxes_cannot_be_accepted_as_source_aligned(self):
        for box in [[0,0,2,1],[-1,0,1,1],[1,0,0,1],[0,0,float('nan'),1]]:
            self.assertIsNone(normalized_box({'bbox':box},{'width':1,'height':1}))
        pipeline=KnowledgePipeline(None,Path(tempfile.gettempdir())/'unused-repair-test')
        units={'s':{'page':1,'bbox':[0,0,1.4,1],'text':'pressure 4.25 kPa','source':'pdf_native'}}
        evidence,errors=pipeline._evidence([{'span_id':'s','quote':'pressure 4.25 kPa'}],units,{'s'})
        self.assertIn('invalid_source_geometry',errors)
        self.assertIsNone(evidence[0]['bbox'])

    def test_ocr_unrotated_points_use_the_page_rotation(self):
        page={'width':650,'height':430,'rotation':90,'rotation_matrix':[0,1,-1,0,650,0]}
        box=normalized_box({'bbox':[300,560,410,580],'task_id':'ocr','bbox_coordinate_space':'pdf_points'},page)
        self.assertTrue(0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


class MaximumOutputTests(unittest.TestCase):
    def profile(self, base='https://api.deepseek.com', model='deepseek-flash'):
        return ModelProfile('test',base,model,base,api_key_file='.secrets/unused.txt',
                            token_parameter='max_tokens',max_output_tokens=4096)

    def response(self):
        return {'model':'offline','id':'offline','choices':[{'finish_reason':'stop',
                 'message':{'content':'{"ok":true}'}}],'usage':{'total_tokens':4}}

    def test_deepseek_cannot_be_silently_lowered_by_a_call_site(self):
        profile=replace(self.profile(),max_output_tokens=16384)
        self.assertEqual(profile.max_output_tokens,DEEPSEEK_MAX_OUTPUT_TOKENS)
        payloads=[]
        client=CompatibleVisionClient(profile,Path.cwd(),transport=lambda p:payloads.append(p) or self.response())
        _,receipt=client.complete(system='JSON only',prompt='test',images=[])
        self.assertEqual(payloads[0]['max_tokens'],393216)
        self.assertEqual(receipt['requested_max_output_tokens'],393216)

    def test_qwen_uses_its_own_supported_maximum(self):
        self.assertEqual(self.profile('https://test.maas.aliyuncs.com/compatible-mode/v1',
                                      'qwen3.8-max-0902').max_output_tokens,131072)

    def test_kimi_uses_remaining_context_after_its_token_counter(self):
        profile=replace(self.profile('https://api.moonshot.cn/v1','kimi-k3'),token_parameter='max_completion_tokens')
        payloads=[];counts=[]
        client=CompatibleVisionClient(profile,Path.cwd(),transport=lambda p:payloads.append(p) or self.response(),
            token_counter=lambda p:counts.append(p) or 137)
        _,receipt=client.complete(system='JSON only',prompt='test',images=[])
        self.assertEqual(payloads[0]['max_completion_tokens'],KIMI_CONTEXT_TOKENS-137)
        self.assertEqual(counts[0]['messages'],payloads[0]['messages'])
        self.assertEqual(receipt['input_token_count'],137)

    def test_bad_kimi_token_count_never_sends_generation(self):
        for count in [None,-1,KIMI_CONTEXT_TOKENS,True]:
            sent=[]
            client=CompatibleVisionClient(self.profile('https://api.moonshot.cn/v1','kimi-k3'),Path.cwd(),
                transport=lambda p:sent.append(p),token_counter=lambda p:count)
            with self.assertRaises(ModelError) as result:
                client.complete(system='JSON',prompt='test',images=[])
            self.assertFalse(result.exception.receipt['generation_request_sent'])
            self.assertEqual(sent,[])

    def test_output_length_receipt_stops_further_generation_without_retry(self):
        class Truncated:
            calls=0
            def identity(self): return {'model':'offline-truncated'}
            def complete(self,**kwargs):
                self.calls+=1
                raise ModelError('length',receipt={'id':'known-response','finish_reason':'length',
                    'usage':{'total_tokens':17},'failure_kind':'output_truncated'})
        with tempfile.TemporaryDirectory() as temp:
            model=Truncated()
            runtime=VisualRuntime(model,model,Path(temp),KnowledgeSettings())
            for number in [1,2]:
                with self.assertRaises(ModelError):
                    runtime.ask('read','JSON',{'part':number},[],lambda a:a)
            self.assertEqual(model.calls,1)
            self.assertEqual(runtime.transient_retries,0)
            self.assertEqual(runtime.receipts[0]['finish_reason'],'length')
