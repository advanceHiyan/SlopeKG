"""Generic large-directory regressions, independent of evaluation documents."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from slopekg import knowledge_prompts as prompts
from slopekg.knowledge_directory import request_directory
from slopekg.knowledge_batching import pack_chunks,prompt_chars
from slopekg.knowledge_document import wire_chunks
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings
from tests.test_knowledge_pipeline import document
from tests.test_knowledge_v3 import BatchModel


def anonymous_context(count=100):
    unit={'id':'s','page':1,'text':'Device A temperature 23 C.','source':'pdf_native','bbox':[0,0,1,1]}
    registry=[{'id':f'e{i}','name':f'Device {i}','kind':'entity','aliases':[f'Alias {i}'],
        'context':f'Building {i%4}','salience':'focus','description':'Anonymous catalogue description. '*22,
        'source_pages':[i+1], 'source_anchors':[{'span_id':f'prior{i}','page':i+1,'bbox':[0,0,1,1]}]} for i in range(count)]
    return {'registry':registry,'chunks':[{'id':'c0001','spans':[unit]}]}


class DirectoryTests(unittest.TestCase):
    def test_large_directory_does_not_block_a_short_source(self):
        context=anonymous_context();original=deepcopy(context)
        self.assertGreater(prompt_chars(prompts.ATTRIBUTE,context),60000)
        bound,receipt=request_directory(context,20000)
        self.assertLess(prompt_chars(prompts.ATTRIBUTE,bound),60000)
        self.assertEqual(context,original)
        self.assertEqual(len(bound['registry']),100)
        self.assertEqual(bound['chunks'],context['chunks'])
        self.assertEqual(receipt['objects_omitted'],0)
        self.assertFalse(receipt['identity_strings_truncated'])

    def test_aliases_scope_and_late_directory_object_are_preserved(self):
        context=anonymous_context();bound,_=request_directory(context,20000)
        keys=('id','name','aliases','context','kind','salience','source_pages')
        for old,new in zip(context['registry'],bound['registry']):
            self.assertEqual({k:old[k] for k in keys},{k:new[k] for k in keys})
        self.assertEqual(bound['registry'][-1]['id'],'e99')
        units={s['id']:s for c in context['chunks'] for s in c['spans']}
        chunks=[{'id':'c0001','unit_ids':['s']}]
        build=lambda c:request_directory({'registry':context['registry'],'chunks':wire_chunks(c,units)},20000)[0]
        groups=list(pack_chunks(chunks,units,build,prompts.ATTRIBUTE,60000))
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0][0]['unit_ids'],['s'])

    def test_small_inputs_remain_byte_identical(self):
        from slopekg.visual_batching import compact
        context=anonymous_context(2);bound,receipt=request_directory(context,20000)
        self.assertIs(bound,context)
        self.assertIsNone(receipt)
        self.assertEqual(compact(bound),compact(context))

    def test_intent_and_subject_identity_retained_without_duplicate_provenance(self):
        context=anonymous_context()
        context['document_context']=[{'purpose':'Planned equipment maintenance','status':'model_proposed',
            'evidence':[{'span_id':'s','quote':'Long purpose proof. '*2000}]}]
        subject={'id':'e99','name':'Device 99','context':'Building 3','aliases':['Alias 99'],
                 'description':'Optional detail. '*4000,'allowed_span_ids':['s']}
        context['subject']=subject
        bound,receipt=request_directory(context,20000)
        self.assertEqual(bound['subject']['allowed_span_ids'],['s'])
        self.assertEqual(bound['subject']['context'],'Building 3')
        self.assertEqual(bound['document_context'][0]['purpose'],'Planned equipment maintenance')
        self.assertEqual(len(context['document_context'][0]['evidence']),1)
        self.assertLess(prompt_chars(prompts.SYNTHESIZE,bound),60000)

    def test_dense_model_descriptions_do_not_block_complete_pipeline(self):
        class LargeMetadataModel(BatchModel):
            def complete(self,*,system,prompt,images):
                answer,receipt=super().complete(system=system,prompt=prompt,images=images)
                instruction=prompt.split('\n输入：',1)[0]
                if instruction==prompts.DISCOVER:
                    for entity in answer['entities']:
                        entity['description']='Optional object navigation description. '*2000
                return answer,receipt
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'device-record.pdf';document(path);model=LargeMetadataModel()
            settings=KnowledgeSettings(review_mode='all',max_review_calls=4,max_calls=20)
            result=KnowledgePipeline(model,Path(tmp)/'out',settings=settings).run([path])
            doc=result['documents'][0]
            self.assertFalse(doc['errors'])
            for stage in ['discovery_batches','attribution_batches','synthesis_jobs','review_jobs']:
                self.assertTrue(doc[stage])
                self.assertTrue(all(j['status']=='completed' for j in doc[stage]),stage)
            self.assertEqual(doc['directory_delivery']['objects_omitted'],0)
            self.assertTrue(all(len(e['description'])>60000 for e in doc['entities']))
            self.assertEqual(len(doc['facts']),2)
            self.assertTrue(all(f['status']=='verified_model' for f in doc['facts']))
            self.assertTrue(any(rule==prompts.VERIFY for rule,_ in model.events))
            for rule,context in model.events:
                self.assertLessEqual(prompt_chars(rule,context),60000)

