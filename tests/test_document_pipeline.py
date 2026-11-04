"""Workflow contracts and a full frozen-response replay; no external model calls."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import fitz

from slopekg.document_contract import reconstruct,zone_plan,extract_claims,reconcile,consolidate
from slopekg.document_pipeline import DocumentPipeline,extraction_batches
from slopekg.document_settings import DocumentSettings
from slopekg.visual_contract import ContractError
from slopekg.pipeline import DemoPipeline
from slopekg.server import SlopeKGHandler
from slopekg.document_workflow import repair_source_links, wire_claim
from slopekg.model_client import ModelError
from tests.support import workspace_paths


def block(key='p1_b1',text='Pump A flow 12 L',page=1):
    return {'id':key,'text':text,'page':page,'kind':'paragraph','source_ids':['line1'],
            'bbox':[.1,.1,.9,.3],'source_type':'native_reordered','heading_path':[]}


def group(key='g1',ids=None):
    return {'id':key,'kind':'object','name':'Pump A','aliases':[],'block_ids':ids or ['p1_b1'],
            'context_block_ids':[],'source_block_ids':ids or ['p1_b1'],'reason':'explicit name'}


def claim(**changes):
    return {'subject':'Pump A','kind':'fact','field':'flow','value':'12','unit':'L','unit_basis':'L',
            'scope':'Pump A','time':'','condition':'','role':'observation','target':'','group_ids':['g1'],
            'evidence':[{'block_id':'p1_b1','quote':'Pump A flow 12 L'}],**changes}


class DocumentContracts(unittest.TestCase):
    def test_malformed_item_is_quarantined_without_losing_other_claims(self):
        good=claim(kind='calculation',role='calculation')
        result=extract_claims({'claims':[good,claim(value='')]},[block()],[group()],'doc')
        self.assertEqual(len(result['claims']),1);self.assertEqual(len(result['invalid_claims']),1)
        self.assertEqual(result['claims'][0]['original_kind'],'calculation')
        self.assertEqual(result['claims'][0]['kind'],'fact')
        self.assertEqual(result['invalid_claims'][0]['raw_claim']['value'],'')

    def test_subject_correction_requires_literal_source_and_keeps_prior_record(self):
        from slopekg.document_workflow import apply_review
        c=extract_claims({'claims':[claim(subject='Material')]},[block()],[group()],'doc')['claims'][0]
        review=reconcile({'reviews':[{'claim_id':c['id'],'verdict':'supported','reason':'object named in source',
            'correction':{'subject':'Pump A'}}],'additions':[]},[c],[block()],[group()],'doc')
        apply_review([c],review)
        self.assertEqual(c['subject'],'Pump A');self.assertEqual(c['before_evidence_review']['subject'],'Material')
        review=reconcile({'reviews':[{'claim_id':c['id'],'verdict':'supported','reason':'unsupported',
            'correction':{'subject':'Pump Z'}}],'additions':[]},[c],[block()],[group()],'doc')
        apply_review([c],review);self.assertEqual(c['status'],'pending')
        self.assertIn('corrected_subject_not_in_evidence',c['quality_issues'])

    def test_source_repair_requires_unique_text_and_location_and_preserves_history(self):
        b=block(text='Pump A flow 12 L');b['source_ids']=[];b['source_type']='visual_transcription'
        missing={**block('fallback',text='12'),'source_type':'unreconstructed_native','issues':['native_source_not_reconstructed']}
        page={'page':1,'blocks':[b,missing],'issues':['native_sources_omitted']}
        receipt=repair_source_links([page])
        self.assertEqual(len(page['blocks']),1);self.assertEqual(receipt[0]['original_block'],missing)
        self.assertEqual(b['source_type'],'visual_transcription')
        page={'page':1,'blocks':[b,{**b,'id':'another'},missing],'issues':[]}
        self.assertEqual(repair_source_links([page]),[])
        self.assertEqual(len(page['blocks']),3)

    def test_same_child_name_in_different_dossiers_does_not_merge(self):
        a=extract_claims({'claims':[claim(subject='Section A',role='calculation')]},[block()],[group()],'doc')['claims'][0]
        b=extract_claims({'claims':[claim(subject='Section A',role='calculation',group_ids=['g2'])]},[block()],[group('g2')],'doc')['claims'][0]
        self.assertIn('parent_not_explicit',a['quality_issues'])
        self.assertEqual(len(consolidate([a,b])),2)
        self.assertNotIn('source_ids',wire_claim(a)['evidence'][0])

    def test_split_review_keeps_earlier_success_when_later_call_fails(self):
        a=extract_claims({'claims':[claim(scope='a'*6500),claim(scope='b'*6500)]},[block()],[group()],'doc')['claims']
        class Model:
            def identity(self):return {'model':'fixture'}
        with TemporaryDirectory() as tmp:
            pipe=DocumentPipeline(Model(),tmp,settings=DocumentSettings(max_prompt_chars=12000))
            doc={'id':'doc','claims':a,'dossier_reviews':[],'repair_queue':[],'facts':[],'unresolved':[]}
            calls=[]
            def ask(result,doc,stage,instruction,ctx,images,validate):
                calls.append(ctx)
                if len(calls)==2:raise ModelError('fixture interruption')
                return validate({'reviews':[{'claim_id':ctx['claims'][0]['id'],'verdict':'supported','reason':'source'}],'additions':[]})
            with patch.object(pipe,'_ask',side_effect=ask),patch.object(pipe,'_save'):
                with self.assertRaises(ModelError):pipe._review_batch({},doc,{'groups':[group()],'blocks':[block()]},[block()],a,{1:None})
            self.assertEqual(a[0]['status'],'source_aligned');self.assertEqual(a[1]['status'],'pending')

    def test_numeric_substrings_and_lost_signs_cannot_support_values(self):
        for source,value,quote in [('Pump A flow 12 L','2','Pump A flow 12 L'),
                ('Pump A flow 12 L','2','2'),('Pump A temperature -5 C','5','Pump A temperature -5 C')]:
            item=extract_claims({'claims':[claim(value=value,unit='',unit_basis='',
                evidence=[{'block_id':'p1_b1','quote':quote}])]},[block(text=source)],[group()],'doc')['claims'][0]
            self.assertIn('value_not_literal_in_evidence',item['quality_issues'])

    def test_review_can_complete_table_evidence_but_cannot_bypass_checks(self):
        table=block(text='Item | Volume (L)\nPump A | 12');table['kind']='table'
        raw=claim(evidence=[{'block_id':'p1_b1','quote':'Pump A | 12'}])
        c=extract_claims({'claims':[raw]},[table],[group()],'doc')['claims'][0]
        self.assertIn('unit_basis_not_in_evidence',c['quality_issues'])
        def check(quote):
            return reconcile({'reviews':[{'claim_id':c['id'],'verdict':'supported','reason':'table header',
                'supporting_evidence':[{'block_id':'p1_b1','quote':quote}]}],'additions':[],'issues':[]},
                [c],[table],[group()],'doc')['reviews'][0]['rechecked_evidence']
        self.assertEqual(check('Volume (L)')['quality_issues'],[])
        self.assertIn('quote_not_in_provided_block',check('Volume (kg)')['quality_issues'])

    def test_missing_native_content_is_retained_not_silently_completed(self):
        page={'page':1,'native_sources':[{'id':'line1','text':'Pump A flow 12 L','bbox':[.1,.1,.9,.3]}],'has_visible_content':True}
        answer=reconstruct({'pages':[{'page':1,'blocks':[],'excluded_sources':[],'unread_regions':[]}]},[page])
        self.assertEqual(answer['pages'][0]['status'],'partial')
        self.assertIn('Pump A',answer['pages'][0]['blocks'][0]['text'])

    def test_negative_sign_change_is_not_native_preservation(self):
        page={'page':1,'native_sources':[{'id':'line1','text':'Temperature -5 C','bbox':[.1,.1,.9,.3]}]}
        raw={**block(key='b1',text='Temperature 5 C'),'source_ids':['line1']}
        result=reconstruct({'pages':[{'page':1,'blocks':[raw],'excluded_sources':[],'unread_regions':[]}]},[page])
        self.assertEqual(result['pages'][0]['blocks'][0]['source_type'],'visual_transcription')

    def test_zones_overlap_and_unassigned_content_still_enters_extraction(self):
        blocks=[block(),block('p2_b1','No named owner',2)]
        plan=zone_plan({'groups':[group(),group('g2')],'links':[],'issues':[]},blocks)
        self.assertEqual(plan['coverage']['shared_blocks']['p1_b1'],['g1','g2'])
        self.assertEqual(plan['groups'][-1]['kind'],'unresolved')
        batches=list(extraction_batches(plan['groups'],blocks,DocumentSettings()))
        self.assertEqual(len(batches),1)
        self.assertEqual(len(batches[0]['blocks']),2)

    def test_unprovided_block_and_gap_continuation_are_rejected(self):
        with self.assertRaises(ContractError):zone_plan({'groups':[group(ids=['invented'])],'links':[]},[block()])
        with self.assertRaises(ContractError):zone_plan({'groups':[group()],'links':[{'from':'p1_b1','to':'p3_b1','relation':'continues'}]},[block(),block('p3_b1',page=3)])

    def test_unknown_subject_survives_and_wrong_unit_is_pending(self):
        result=extract_claims({'claims':[claim(subject='',unit='m',unit_basis='L')],'unresolved':[]},[block()],[group()],'doc')
        row=result['claims'][0]
        self.assertIn('unresolved_subject',row['quality_issues'])
        self.assertIn('unit_not_supported_by_basis',row['quality_issues'])
        self.assertEqual(row['value'],'12')

    def test_duplicate_sources_do_not_become_independent_evidence(self):
        a=extract_claims({'claims':[claim()],'unresolved':[]},[block()],[group()],'doc')['claims'][0]
        rows=consolidate([a,{**a,'id':'different'}])
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['evidence']),1)
        self.assertEqual(rows[0]['duplicate_claim_ids'],['different'])

    def test_large_block_fails_without_truncating_input(self):
        b=block(text='z'*15000)
        with self.assertRaises(ContractError):list(extraction_batches([group()],[b],DocumentSettings(max_prompt_chars=12000)))
        self.assertEqual(len(b['text']),15000)

    def test_route_dispatch_and_http_consent(self):
        with TemporaryDirectory() as tmp:
            paths=workspace_paths(Path(tmp));paths.raw_dir.mkdir(parents=True)
            (paths.raw_dir/'sample.pdf').write_bytes(b'not read')
            p=paths.root/'config/models';p.mkdir(parents=True)
            (p/'test.json').write_text(json.dumps({'name':'test','model':'fixture','base_url':'https://fixture.invalid'}))
            with patch('slopekg.document_service.run_document_recognition',return_value={'marker':'fourth'}):
                self.assertEqual(DemoPipeline(paths).run(recognizer='document',input_pdfs=['x'],model_profile='x')['route']['id'],'document')
            with patch('slopekg.server.PATHS',paths):
                body={'recognizer':'document','input_pdfs':['sample.pdf'],'model_profile':'test'}
                self.assertTrue(SlopeKGHandler.pipeline_options(None,body)['prepare_only'])
                with self.assertRaises(ValueError):SlopeKGHandler.pipeline_options(None,{**body,'prepare_only':False})

    def test_full_route_and_cache_replay_keep_identical_ids(self):
        class Model:
            count=0
            verdict='supported'
            flags=False
            def identity(self):return {'name':'offline-fixture','model':'offline-fixture'}
            def complete(self,*,system,prompt,images):
                self.count+=1;ctx=json.loads(prompt.split('\n输入：',1)[1])
                if 'pages' in ctx:
                    p=ctx['pages'][0];ids=[s['id'] for s in p['native_sources']]
                    answer={'pages':[{'page':1,'blocks':[{'id':'b1','kind':'paragraph','bbox':[0,0,1,1],
                        'source_ids':ids,'text':'Pump A flow 12 L','heading_path':[],'issues':[]}],
                        'excluded_sources':[],'unread_regions':[]}]}
                elif 'claims' in ctx:
                    answer={'reviews':[{'claim_id':c['id'],'verdict':self.verdict,'reason':'source check'} for c in ctx['claims']],
                            'additions':[],'issues':[]}
                elif 'claim_index' in ctx:
                    answer={'flags':[{'claim_ids':[c['id'] for c in ctx['claim_index']],'reason':'ownership issue'}] if self.flags else [],'issues':[]}
                elif 'groups' in ctx:answer={'claims':[claim()],'unresolved':[]}
                else:answer={'groups':[group()],'links':[],'issues':[]}
                return answer,{'finish_reason':'stop','usage':{},'environment_variables_used':[]}
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.pdf';pdf=fitz.open();page=pdf.new_page();page.insert_text((72,72),'Pump A flow 12 L');pdf.save(path);pdf.close()
            model=Model();out=Path(tmp)/'results'
            first=DocumentPipeline(model,out,settings=DocumentSettings(max_calls=5)).run([path])
            second=DocumentPipeline(model,out,settings=DocumentSettings(max_calls=5,allow_model_calls=False)).run([path])
            self.assertEqual(model.count,5)
            self.assertEqual(first['documents'][0]['facts'],second['documents'][0]['facts'])
            self.assertEqual(second['summary']['cache_hits'],5)
            self.assertEqual(second['processing_status'],'completed')
            self.assertTrue((out/'report.html').is_file())
            model.verdict='contradicted';model.flags=True
            rejected=DocumentPipeline(model,Path(tmp)/'rejected',settings=DocumentSettings(max_calls=5)).run([path])
            self.assertEqual(rejected['documents'][0]['facts'][0]['status'],'rejected')
            self.assertTrue(rejected['documents'][0]['repair_queue'])


if __name__=='__main__':unittest.main()
