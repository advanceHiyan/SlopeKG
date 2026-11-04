"""Anonymous regressions for source preservation; no private evaluation input."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from slopekg import knowledge_prompts as prompts
from slopekg.knowledge_claims import partition_attribution
from slopekg.knowledge_evidence import presentation,source_quote,unit_supported,value_supported
from slopekg.knowledge_pipeline import KnowledgePipeline,resolve_source_handle
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.knowledge_tables import header_rows
from slopekg.knowledge_recovery import recovery_package
from slopekg.knowledge_roles import canonical_role
from slopekg.model_client import ModelError
from tests.test_knowledge_pipeline import document
from tests.test_knowledge_v3 import BatchModel


def fact(span,*,value='2.50',quote=None):
    return {'field':'pressure','value':value,'unit':'MPa','unit_basis':'','scope':'Reactor A',
        'role':'observation','qualifier':'','condition':'cold','time':'',
        'evidence':[{'span_id':span['id'],'quote':quote or span['text']}]}


class ClaimModel(BatchModel):
    def __init__(self,*,failed_composition=False,bad_claim=False):
        super().__init__();self.failed_composition=failed_composition;self.bad_claim=bad_claim

    def complete(self,*,system,prompt,images):
        instruction,raw=prompt.split('\n输入：',1);context=json.loads(raw)
        if instruction in {prompts.SYNTHESIZE,prompts.RECOMPOSE} and self.failed_composition:
            raise ModelError('Anonymous composition unavailable',receipt={'not_sent':True})
        answer,receipt=super().complete(system=system,prompt=prompt,images=images)
        if instruction==prompts.ATTRIBUTE:
            by_id={s['id']:s for c in context['chunks'] for s in c['spans']}
            for row in answer['assignments']:
                for binding in row['entities']:
                    for key in binding['span_ids']:
                        span=by_id[key]
                        if 'pressure 2.50' in span['text']:
                            binding['facts']=[fact(span,quote='Invented pressure 2.50 MPa' if self.bad_claim else None)]
        if instruction in {prompts.SYNTHESIZE,prompts.RECOMPOSE}:
            objects=answer['objects'] if 'objects' in answer else [answer]
            for output in objects:output['facts']=[f for f in output['facts'] if f['field']!='pressure']
        return answer,receipt


class TableRecoveryTests(unittest.TestCase):
    def test_wire_role_aliases_do_not_infer_ambiguous_business_meaning(self):
        self.assertEqual(canonical_role('现状'),'observation')
        self.assertEqual(canonical_role('设计'),'design')
        self.assertEqual(canonical_role('引用'),'reference')
        self.assertEqual(canonical_role(' DESIGN '),'design')
        self.assertEqual(canonical_role('要求'),'要求')
        self.assertEqual(canonical_role('署名'),'署名')

    def test_merged_multilevel_headers_and_column_labels_are_retained(self):
        rows=[['Asset','','Settings','',''],['','','Width','Capacity','Mass'],['','','m','m3','kg'],
              ['','','7','8','9'],['Q','west','7.2','61','38'],['Total','','7.2','61','38']]
        self.assertEqual(header_rows(rows),[0,1,2,3])

    def test_numeric_matrices_and_body_rows_are_not_headers(self):
        self.assertEqual(header_rows([['12','18'],['13','19']]),[])
        self.assertEqual(header_rows([['Name','Count'],['Alpha','12'],['Beta','15']]),[0])
        self.assertEqual(header_rows([['Name','State'],['Alpha','open'],['Beta','closed']]),[0])

    def test_width_normalization_retains_original_quote_and_formula_powers(self):
        original='Pressure is １２．７５ ＭＰａ; x² and x₂ are different.'
        self.assertEqual(source_quote('12.75 MPa',original),'１２．７５ ＭＰａ')
        self.assertIsNone(source_quote('12.7',original))
        unit={'id':'s','text':original,'page':1,'source':'pdf_native','bbox':[0,0,1,1]}
        evidence=[{'span_id':'s','quote':original,'page':1,'bbox':unit['bbox']}]
        self.assertTrue(value_supported('12.75',evidence,{'s':unit}))
        self.assertFalse(value_supported('12.7',evidence,{'s':unit}))
        self.assertTrue(unit_supported('MPa',[original]))
        self.assertFalse(unit_supported('Pa',[original]))
        self.assertEqual(presentation('x²+x₂'), 'x²+x₂')
        self.assertFalse(value_supported('x2',evidence,{'s':unit}))
        self.assertFalse(value_supported('1275',evidence,{'s':unit}))

    def test_negative_fullwidth_numbers_keep_the_sign(self):
        u={'id':'s','text':'－３．２','page':1,'bbox':[0,0,1,1]}
        e=[{'span_id':'s','quote':u['text'],'page':1,'bbox':u['bbox']}]
        self.assertTrue(value_supported('-3.2',e,{'s':u}))
        self.assertFalse(value_supported('3.2',e,{'s':u}))


class AttributionPartitionTests(unittest.TestCase):
    def inputs(self):
        units={'s':{'id':'s','text':'Reactor A pressure 2.50 MPa','page':1,'bbox':[0,0,1,1]}}
        batch=[{'id':'c','unit_ids':['s']}]
        known={'e':{'id':'e','name':'Reactor A','aliases':[],'mentions':[{'page':1}]}}
        return units,batch,known

    def test_bad_binding_does_not_discard_other_atomic_claims(self):
        units,batch,known=self.inputs()
        raw={'assignments':[{'chunk_id':'c','entities':[
            {'entity_id':'missing','span_ids':['s']},
            {'entity_id':'e','span_ids':['s'],'facts':[fact(units['s'])]}],'unresolved':[]}]}
        result=partition_attribution(raw,batch,units,known,resolve_source_handle)
        self.assertEqual(len(result['claims']),1)
        self.assertEqual(result['assignments'][0]['entities'][0]['entity_id'],'e')
        self.assertEqual(result['assignments'][0]['attribution_status'],'partial')
        self.assertEqual(len(result['quarantined']),1)

    def test_normalized_claim_cache_can_be_read_without_loss_or_duplication(self):
        units,batch,known=self.inputs()
        raw={'assignments':[{'chunk_id':'c','entities':[
            {'entity_id':'e','span_ids':['s'],'facts':[fact(units['s'])]}],'unresolved':[]}]}
        one=partition_attribution(raw,batch,units,known,resolve_source_handle)
        two=partition_attribution(deepcopy(one),batch,units,known,resolve_source_handle)
        self.assertEqual(one,two)

    def test_unprovided_span_cannot_become_an_attributed_fact(self):
        units,batch,known=self.inputs()
        raw={'assignments':[{'chunk_id':'c','entities':[
            {'entity_id':'e','span_ids':['unseen'],'facts':[fact(units['s'])]}],'unresolved':[]}]}
        result=partition_attribution(raw,batch,units,known,resolve_source_handle)
        self.assertFalse(result['claims'])
        self.assertFalse(result['assignments'][0]['entities'])
        self.assertTrue(result['quarantined'])


class AtomicPreservationTests(unittest.TestCase):
    def run_case(self,**flags):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'any-name.pdf';document(source)
            return KnowledgePipeline(ClaimModel(**flags),root/'out',
                settings=KnowledgeSettings(review_mode='none')).run([source])

    def test_later_summary_omission_cannot_delete_an_attributed_fact(self):
        result=self.run_case()
        facts=[f for f in result['documents'][0]['facts'] if f['field']=='pressure']
        self.assertEqual(len(facts),1)
        self.assertEqual(facts[0]['value'],'2.50')
        self.assertTrue(facts[0]['retained_from_attribution'])
        self.assertEqual(facts[0]['status'],'source_aligned')

    def test_composition_failure_keeps_source_claim_without_claiming_completion(self):
        result=self.run_case(failed_composition=True)
        self.assertTrue(any(f['field']=='pressure' for f in result['documents'][0]['facts']))
        self.assertNotEqual(result['processing_status'],'completed')

    def test_bad_quote_remains_pending_after_preservation(self):
        result=self.run_case(bad_claim=True)
        item=next(f for f in result['documents'][0]['facts'] if f['field']=='pressure')
        self.assertEqual(item['status'],'pending')
        self.assertIn('quote_not_in_source',item['quality_issues'])
        self.assertFalse(item.get('review',{}).get('decision')=='supported')


class DocumentContextTests(unittest.TestCase):
    def test_only_evidence_bound_document_context_reaches_later_stages(self):
        class ContextModel(BatchModel):
            def __init__(self,bad):super().__init__();self.bad=bad
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                instruction,raw=kwargs['prompt'].split('\n输入：',1);context=json.loads(raw)
                if instruction==prompts.DISCOVER:
                    span=next(s for c in context['chunks'] for s in c['spans'] if 'observed test result' in s['text'])
                    answer['document_context']={'purpose':'Equipment observation records',
                        'evidence':[{'span_id':span['id'],'quote':'Invented business purpose' if self.bad else span['text']}]}
                return answer,receipt
        for bad in [False,True]:
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source=root/'arbitrary-file.pdf';document(source);model=ContextModel(bad)
                result=KnowledgePipeline(model,root/'out').run([source])
                context=next(c for instruction,c in model.events if instruction==prompts.ATTRIBUTE)
                self.assertEqual(bool(context['document_context']),not bad)
                self.assertTrue(all('description' in row for row in context['registry']))
                if not bad:
                    self.assertEqual(context['document_context'][0]['status'],'model_proposed_document_context')
                else:self.assertTrue(result['documents'][0]['context_diagnostics'])


class SourceCoverageRecoveryTests(unittest.TestCase):
    def fixture(self):
        def source(key,text,y):return {'id':key,'text':text,'page':1,'source':'pdf_native','bbox':[.1,y,.9,y+.03]}
        units=[source('a','Device A inspection record',.1),source('h','2 Material details',.2),
               source('b','Device A uses a ceramic shell.',.25)]
        def obj(key,name,ids,salience):return {'id':key,'name':name,'kind':'concept' if key=='r' else 'entity',
            'aliases':[],'context':'','description':'Source object','salience':salience,
            'mentions':[{'span_id':'h' if key=='r' else 'a','quote':units[1 if key=='r' else 0]['text'],'page':1}],
            'unit_ids':ids,'chunk_ids':['c'],'status':'candidate'}
        return {'id':'d','source_sha256':'hash','units':units,'entities':[obj('e','Device A',['a'],'focus')],
            'references':[obj('r','Material details',[],'support')],'facts':[],'synthesis_jobs':[],
            'chunks':[{'id':'c','unit_ids':['a','h','b']}], 'explicit_span_owners':{},'section_owners':{}}

    def test_support_heading_does_not_hide_unassigned_body(self):
        doc=self.fixture();package=recovery_package(doc,KnowledgeSettings())
        self.assertIn('b',package['unassigned_span_ids'])
        self.assertEqual({s['id'] for s in package['source_spans']},{'a','h','b'})
        doc['entities'][0]['unit_ids'].extend(['h','b'])
        self.assertIsNone(recovery_package(doc,KnowledgeSettings()))

    def test_recovery_keeps_valid_fact_when_another_item_is_invalid(self):
        class RecoveryModel:
            def identity(self):return {'model':'anonymous-recovery'}
            def complete(self,**kwargs):
                return {'objects':[{'id':'r','facts':[None,{'field':'shell material','value':'ceramic','unit':'',
                    'unit_basis':'','qualifier':'','scope':'Device A shell','role':'observation','condition':'','time':'',
                    'evidence':[{'span_id':'b','quote':'Device A uses a ceramic shell.'}]}]}],
                    'unresolved':[]},{'usage':{'total_tokens':0}}
        import asyncio
        with tempfile.TemporaryDirectory() as temp:
            doc=self.fixture();pipeline=KnowledgePipeline(RecoveryModel(),Path(temp));pipeline._save=lambda result:None
            known={e['id']:e for e in doc['entities']}
            asyncio.run(pipeline._recover_unassigned(doc,{}, {u['id']:u for u in doc['units']},known))
            self.assertEqual(len(doc['facts']),1)
            self.assertEqual(doc['facts'][0]['value'],'ceramic')
            self.assertEqual(doc['facts'][0]['origin_stage'],'knowledge_recover')
            self.assertIn('r',known)
            self.assertEqual(pipeline.runtime.calls,1)
            self.assertTrue(doc['recovery_jobs'][0]['quarantined'])

    def test_recovery_does_not_increase_exhausted_total_budget(self):
        import asyncio
        class NeverCalled:
            def identity(self):return {'model':'anonymous-no-budget'}
            def complete(self,**kwargs):raise AssertionError('No request may be dispatched')
        with tempfile.TemporaryDirectory() as temp:
            doc=self.fixture();pipeline=KnowledgePipeline(NeverCalled(),Path(temp),settings=KnowledgeSettings(max_calls=1))
            pipeline._save=lambda result:None;pipeline.runtime.calls=1
            asyncio.run(pipeline._recover_unassigned(doc,{}, {u['id']:u for u in doc['units']},{'e':doc['entities'][0]}))
            self.assertEqual(pipeline.runtime.calls,1)
            self.assertEqual(doc['recovery_jobs'][0]['status'],'failed')
            self.assertFalse(doc['facts'])
