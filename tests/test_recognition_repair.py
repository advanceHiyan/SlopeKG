"""Anonymous adversarial regressions; never open project evaluation assets."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from slopekg.knowledge_evidence import value_supported,unit_supported
from slopekg.knowledge_attribution import source_local,numeric_residuals
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.model_client import ModelError
from slopekg.recognition import build_visual_pipeline
from slopekg.visual_planning import future_call_reserve
from slopekg.visual_runtime import VisualRuntime
from slopekg.visual_settings import VisualSettings
from slopekg.visual_sources import recompose_dimension_field,dimension_semantic_issues
from slopekg.visual_pipeline import VisualPipeline
from slopekg.visual_review import VisualReviewer
from slopekg.visual_contract import REVIEW_CHECKS,AUDIT
from copy import deepcopy
from types import SimpleNamespace
import asyncio


class Model:
    def __init__(self):self.calls=0
    def identity(self):return {'model':'anonymous-offline'}
    def complete(self,**kwargs):
        self.calls+=1
        return {'ok':True},{'usage':{'total_tokens':1}}


def source(key,text,box,*,page=1,kind='pdf_native',**extra):
    return {'id':key,'text':text,'page':page,'source':kind,'bbox':box,**extra}


def evidence(unit):
    return {'span_id':unit['id'],'quote':unit['text'],'page':unit['page'],'bbox':unit['bbox']}


class EvidenceAssemblyTests(unittest.TestCase):
    def test_adjacent_text_can_complete_a_split_word(self):
        a=source('a','The inspection inter',[.1,.1,.8,.13])
        b=source('b','val is weekly',[.1,.135,.8,.165]);units={'a':a,'b':b}
        self.assertTrue(value_supported('inspection interval',[evidence(a),evidence(b)],units))
        self.assertFalse(value_supported('inspection interval',[evidence(a)],units))

    def test_columns_digits_and_omitted_intervening_text_are_not_spliced(self):
        a=source('a','code 12',[.1,.1,.4,.13]);b=source('b','34',[.1,.135,.4,.165])
        self.assertFalse(value_supported('code 1234',[evidence(a),evidence(b)],{'a':a,'b':b}))
        a=source('a','inspection inter',[.1,.1,.4,.13]);b=source('b','val',[.6,.135,.9,.165])
        self.assertFalse(value_supported('inspection interval',[evidence(a),evidence(b)],{'a':a,'b':b}))
        b['bbox']=[.1,.18,.4,.21];middle=source('c','unrelated text',[.1,.14,.4,.17])
        self.assertFalse(value_supported('inspection interval',[evidence(a),evidence(b)],{'a':a,'b':b,'c':middle}))

    def test_table_rows_and_pages_do_not_form_text_continuations(self):
        a=source('a','inspection inter',[.1,.1,.8,.13],kind='pdf_table')
        b=source('b','val',[.1,.135,.8,.165],kind='pdf_table')
        self.assertFalse(value_supported('inspection interval',[evidence(a),evidence(b)],{'a':a,'b':b}))
        a['source']=b['source']='ocr';b['page']=2
        self.assertFalse(value_supported('inspection interval',[evidence(a),evidence(b)],{'a':a,'b':b}))

    def test_unit_aliases_preserve_scale_and_powers(self):
        self.assertTrue(unit_supported('cm',['总长度为23厘米']))
        self.assertTrue(unit_supported('m³',['capacity 81 m3']))
        self.assertFalse(unit_supported('m',['23毫米']))
        self.assertFalse(unit_supported('m²',['81 m³']))
        self.assertFalse(unit_supported('cm',['2 mm']))

    def test_table_unit_must_match_the_value_column(self):
        header=source('h','m | m3',[.1,.1,.9,.2],kind='pdf_table',table_id='t',is_header=True,cells=['m','m3'])
        row=source('r','8.4 | 62',[.1,.2,.9,.3],kind='pdf_table',table_id='t',is_header=False,header_ids=['h'],cells=['8.4','62'])
        units={'h':header,'r':row};subject={'id':'e','name':'Machine A'}
        raw=dict(field='width',value='8.4',unit='m',unit_basis='',qualifier='',scope='Machine A',role='design',condition='',time='',evidence=[{'span_id':'r','quote':row['text']}])
        with tempfile.TemporaryDirectory() as temp:
            pipeline=KnowledgePipeline(Model(),Path(temp),settings=KnowledgeSettings(allow_model_calls=False))
            good=pipeline._item({'id':'d','source_sha256':'hash'},subject,'fact',raw,units,set(units),{'e':subject})
            self.assertNotIn('unit_not_in_cited_source',good['quality_issues'])
            self.assertEqual({e['span_id'] for e in good['evidence']},{'h','r'})
            bad=pipeline._item({'id':'d','source_sha256':'hash'},subject,'fact',{**raw,'unit':'m³'},units,set(units),{'e':subject})
            self.assertIn('unit_not_in_cited_source',bad['quality_issues'])


class SourceAttributionTests(unittest.TestCase):
    def test_numeric_continuation_needs_local_identity_evidence(self):
        entity={'name':'Machine A','aliases':[],'mentions':[{'page':1}]}
        units={'a':source('a','Machine A',[0,0,1,.1]),'b':source('b','37 m²',[0,.1,1,.2],page=2)}
        self.assertFalse(source_local(entity,units['b'],units))
        units['c']=source('c','Machine A continued',[0,0,1,.1],page=2)
        self.assertTrue(source_local(entity,units['b'],units))

    def test_citing_a_whole_sentence_does_not_account_for_every_numeric_fact(self):
        doc={'entities':[{'id':'e','unit_ids':['u']}],
             'units':[source('u','settings 42 and 73',[0,0,1,.1])],
             'facts':[{'value':'42','status':'source_aligned','evidence':[{'span_id':'u','quote':'settings 42 and 73'}]}]}
        residuals=numeric_residuals(doc)
        self.assertEqual(residuals[0]['tokens'],['73'])
        self.assertEqual(doc['facts'][0]['value'],'42')


class VisualReservationTests(unittest.TestCase):
    def test_coalesced_page_uses_spare_image_slot_for_small_source_detail(self):
        class Doc:
            def render(self,number,region=None,**options):
                assert region is None or isinstance(region,list) and len(region)==4
                return {'page':number,'bbox':region,**options}
        with tempfile.TemporaryDirectory() as temp:
            settings=VisualSettings(max_images=3,max_image_pixels=1000000,max_pixels_per_image=600000)
            pipeline=VisualPipeline(Model(),Path(temp),settings=settings)
            small={'kind':'table','bbox':[.2,.5,.4,.8]}
            region={'bbox':[0,0,1,1],'source_regions':[small,{'kind':'drawing','bbox':[.05,.05,.95,.9]}]}
            images=pipeline._images(Doc(),4,region)
            self.assertEqual(len(images),3)
            self.assertEqual({i['page'] for i in images},{4})
            self.assertEqual(images[-1]['bbox'],small['bbox'])
            self.assertLessEqual(sum(i['max_pixels'] for i in images),settings.max_image_pixels)
            linked=pipeline._images(Doc(),4,region,assignment={'source_context_page':3})
            self.assertEqual(linked[0]['page'],3)

    def test_later_page_gets_first_read_even_when_earlier_audits_exhaust_their_share(self):
        from tests.test_visual_pipeline import make_pdf,ScriptedModel
        class Auditor(VisualPipeline):
            async def _audit(self,doc,state,hints,report):
                try:
                    for index in range(50):
                        await self._ask_async('audit',AUDIT,{'page':state['page'],'stress':index},[],lambda a:a)
                except ModelError:pass
                state['audit'].append({'complete':False,'missing_regions':[],'issues':['anonymous audit stress']})
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pdf=root/'anonymous.pdf';make_pdf(pdf,3)
            model=ScriptedModel();pipeline=Auditor(model,root/'run',settings=VisualSettings(max_calls=20,window_pages=2))
            result=pipeline.run([pdf]);last=result['documents'][0]['pages'][2]
            self.assertTrue(any(r.get('reading') for r in last['regions']))
            self.assertLessEqual(result['summary']['calls'],20)

    def test_independent_comparison_uses_same_explicit_measurement_gloss(self):
        fact={'id':'f','object':'pipe A','field':'flow','value':'17','unit':'','unit_basis':'',
              'scope':'pipe A','role':'observation','qualifier':'','condition':'','time':'',
              'quote':'pipe A volumetric flow 17','bbox':[0,0,1,1]}
        meaning={'object':'pipe A','quantity':'flow (volume/time)','basis':'inlet meter'}
        dimension={'text':'17','bbox':[0,0,1,1],'measurement':meaning,'fact_indices':[0],'disposition':'fact'}
        independent={'facts':[deepcopy(fact)],'transcript':fact['quote'],'tables':[],'dimensions':[dimension]}
        fact['source_bindings']=[{'kind':'dimension','text':'17','measurement':meaning}]
        recompose_dimension_field(fact)
        async def verify(*args):return {'verdicts':[{'id':'f','decision':'supported','quote':fact['quote'],'reason':'anonymous source agrees',**{k:True for k in REVIEW_CHECKS}}]}
        reviewer=VisualReviewer(VisualSettings(),verify)
        asyncio.run(reviewer.review(SimpleNamespace(id='d',sha256='hash'),1,{'id':'r','bbox':[0,0,1,1]},
            [fact],fact['quote'],[],{},independent_read=independent))
        self.assertEqual(fact['independent_comparison']['status'],'agreed')
        self.assertEqual(independent['facts'][0]['field'],'flow')

    def test_affordable_future_page_keeps_its_complete_minimum_cycle(self):
        self.assertEqual(future_call_reserve([{'page':5}],44,17,window_pages=4,independent_review=True),6)
        # A large input cannot reserve an impossible all-document run.
        reserve=future_call_reserve([{'page':p} for p in range(5,81)],59,17,window_pages=4,independent_review=True)
        self.assertEqual(reserve,42)

    def test_concurrent_review_cannot_spend_reserved_source_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            model=Model();settings=VisualSettings(max_calls=6,model_concurrency=6)
            runtime=VisualRuntime(model,model,Path(temp),settings);runtime.reserve_calls(3)
            def ask(i):
                try:runtime.ask('verify','anonymous',{'n':i},[],lambda a:a);return True
                except ModelError:return False
            with ThreadPoolExecutor(max_workers=6) as pool:ok=list(pool.map(ask,range(10)))
            self.assertEqual(sum(ok),3);self.assertEqual(runtime.calls,3)
            runtime.reserve_calls(0)
            for i in range(10,13):self.assertTrue(ask(i))
            self.assertEqual(runtime.calls,6);self.assertEqual(model.calls,6)

    def test_service_and_test_factory_include_ocr_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            pipeline=build_visual_pipeline(Model(),Path(temp)/'run',settings=VisualSettings(),parsed_dir=Path(temp)/'parsed')
            self.assertIsNotNone(pipeline.ocr_evidence)

    def test_measurement_gloss_is_preserved_without_turning_width_into_height(self):
        binding={'kind':'dimension','measurement':{'object':'pipe','quantity':'flow (volume/time)','basis':'meter inlet'}}
        fact={'field':'flow','source_bindings':[binding]};recompose_dimension_field(fact)
        self.assertEqual(fact['field'],'flow (volume/time)')
        self.assertEqual(dimension_semantic_issues(binding,fact),[])
        wrong={'field':'height','source_bindings':[binding]};recompose_dimension_field(wrong)
        self.assertIn('dimension_field_not_recomposed',dimension_semantic_issues(binding,wrong))


if __name__=='__main__':unittest.main()
