"""Anonymous capacity and provenance regressions, with no model API calls."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from slopekg import knowledge_prompts as prompts
from slopekg.knowledge_batching import (fit_groups, pack_chunks, prompt_chars, split_chunk,
                                       replace_chunks, RequestCapacityError)
from slopekg.knowledge_document import wire_chunks
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_recovery import recovery_package
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.visual_batching import compact
from tests.test_knowledge_pipeline import document
from tests.test_knowledge_v3 import BatchModel


def sources(count=36, cell_chars=1000):
    units={'header':{'id':'header','page':1,'text':'Item | Unit | Value','source':'pdf_table',
                     'cells':['Item','Unit','Value'],'is_header':True,'bbox':[0,.1,1,.2]}}
    for i in range(count):
        key=f's{i}'
        units[key]={'id':key,'page':1+i//10,'text':f'Part {i} | 3.2', 'source':'pdf_table',
            'cells':['Long independent cell '+('字\\\n"'*cell_chars),'3.2'],
            'header_ids':['header'],'is_header':False,'bbox':[0,.234567891234,1,.987654321]}
    return units


class CapacityTests(unittest.TestCase):
    def test_full_capacity_packing_preserves_every_original_source_and_header(self):
        units=sources(30,100)
        chunks=[{'id':f'c{i:04d}','unit_ids':['header',*list(units)[1+i*10:1+(i+1)*10]]} for i in range(3)]
        original=deepcopy(units)
        context=lambda b:{'registry':[{'description':'R'*1500}], 'chunks':wire_chunks(b,units)}
        batches=list(pack_chunks(chunks,units,context,prompts.ATTRIBUTE,16000))
        self.assertEqual({k for b in batches for c in b for k in c['unit_ids']},set(units))
        self.assertEqual(units,original)
        self.assertTrue(all(prompt_chars(prompts.ATTRIBUTE,context(b))<=16000 for b in batches))
        doc={'chunks':chunks}
        for b in batches:replace_chunks(doc,b)
        self.assertEqual({c['id'] for c in doc['chunks']},{c['id'] for b in batches for c in b})
        self.assertEqual(len(doc['chunks']),len({c['id'] for c in doc['chunks']}))
        self.assertGreater(prompt_chars(prompts.ATTRIBUTE,context([*batches[0],batches[1][0]])),16000)

    def test_metadata_and_escaping_count_and_single_chunk_splits_without_lost_headers(self):
        units=sources(12,200)
        chunk={'id':'c0001','unit_ids':list(units),'pages':[1,2], 'assignments':[], 'attribution_status':'pending'}
        context=lambda b:{'registry':[],'chunks':wire_chunks(b,units)}
        self.assertGreater(prompt_chars(prompts.ATTRIBUTE,context([chunk])),16000)
        groups=list(fit_groups([[chunk]],context,prompts.ATTRIBUTE,16000,lambda c:split_chunk(c,units)))
        self.assertGreater(len(groups),1)
        self.assertEqual({k for g in groups for c in g for k in c['unit_ids']},set(units))
        for g in groups:
            self.assertLessEqual(prompt_chars(prompts.ATTRIBUTE,context(g)),16000)
            for c in g:self.assertIn('header',c['unit_ids'])
        doc={'chunks':[chunk]}
        for group in groups:replace_chunks(doc,group)
        self.assertEqual([c['id'] for c in doc['chunks']], [c['id'] for g in groups for c in g])

    def test_registry_growth_is_checked_between_yields(self):
        units=sources(8,100)
        chunks=[{'id':f'c{i:04d}','unit_ids':list(units)[1+i*4:1+(i+1)*4]} for i in range(2)]
        registry=[]
        context=lambda b:{'registry':registry,'chunks':wire_chunks(b,units)}
        queue=fit_groups([[c] for c in chunks],context,prompts.DISCOVER,16000,lambda c:split_chunk(c,units))
        first=next(queue);self.assertEqual(first[0]['id'],'c0000')
        registry.append({'description':'R'*12000})
        rest=list(queue)
        self.assertGreater(len(rest),1)
        for b in rest:self.assertLessEqual(prompt_chars(prompts.DISCOVER,context(b)),16000)

    def test_indivisible_context_fails_without_slicing_text(self):
        units=sources(1,5000);original=deepcopy(units)
        chunk={'id':'c0001','unit_ids':list(units)}
        context=lambda b:{'chunks':wire_chunks(b,units)}
        with self.assertRaisesRegex(RequestCapacityError,'source retained'):
            list(fit_groups([[chunk]],context,prompts.DISCOVER,16000,lambda c:split_chunk(c,units)))
        self.assertEqual(units,original)

    def test_exact_limit_and_delimiter_are_consistent(self):
        context={'text':'中文"\\\n'};size=len(prompts.SYSTEM)+len(prompts.DISCOVER+'\n输入：'+compact(context))
        self.assertEqual(prompt_chars(prompts.DISCOVER,context),size)
        item={'id':'c'}
        self.assertEqual(list(fit_groups([[item]],lambda _:context,prompts.DISCOVER,size,lambda _:[])),[[item]])
        with self.assertRaises(RequestCapacityError):list(fit_groups([[item]],lambda _:context,prompts.DISCOVER,size-1,lambda _:[]))

    def test_recovery_fits_registry_and_preserves_deferred_ids_and_headers(self):
        units=sources(15,150)
        units['heading']={'id':'heading','text':'2. Material record','page':1,'source':'pdf_native'}
        doc={'units':list(units.values()),'entities':[], 'references':[{'mentions':[{'span_id':'heading'}]}]}
        settings=KnowledgeSettings(max_prompt_chars=16000,batch_chars=6000,synthesis_chars=6000)
        context={'registry':[{'description':'r'*7000}]}
        package=recovery_package(doc,settings,context=context)
        sent={k:v for k,v in package.items() if k!='remaining_span_ids'}
        self.assertLessEqual(prompt_chars(prompts.RECOVER,{**context,**sent}),16000)
        self.assertTrue(package['remaining_span_ids'])
        self.assertIn('header',{s['id'] for s in package['source_spans']})
        self.assertEqual(set(package['unassigned_span_ids'])|set(package['remaining_span_ids']),set(units)-{'header'})

    def test_dense_metadata_pipeline_visits_all_sources_before_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.pdf';document(path)
            from slopekg.knowledge_document import prepare_document
            settings=KnowledgeSettings(max_prompt_chars=16000,batch_chars=6000,synthesis_chars=6000,
                max_calls=80,review_mode='none',max_review_calls=0,model_concurrency=3)
            doc=prepare_document(path,settings)
            for unit in doc['units']:unit['cells']=['Metadata '+('Q'*2800)]
            old=deepcopy(doc['units']);model=BatchModel()
            with patch('slopekg.knowledge_pipeline.prepare_document',return_value=doc):
                result=KnowledgePipeline(model,Path(tmp)/'out',settings=settings).run([path])
            final=result['documents'][0]
            self.assertFalse(final['errors'])
            self.assertEqual(final['units'],old)
            self.assertTrue(all(c['attribution_status']=='completed' for c in final['chunks']))
            for stage in ('discovery_batches','attribution_batches','synthesis_jobs'):
                self.assertTrue(final[stage])
                self.assertTrue(all(j['status']=='completed' for j in final[stage]),final[stage])
                self.assertTrue(all(j['prompt_chars']<=16000 for j in final[stage]))

