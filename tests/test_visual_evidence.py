"""Contract and scheduling regressions. Fake models do not measure recognition quality."""
from pathlib import Path
import copy
import tempfile
import unittest

from slopekg.visual_contract import reading, ContractError
from slopekg.visual_evidence import canonical, field_parts_error, field_is_supported, align_quote
from slopekg.visual_pipeline import VisualPipeline, VisualSettings, literal_supported, source_contains, verification_batches
from tests.test_visual_pipeline import ScriptedModel, make_pdf, source_read


class LiteralEvidenceTests(unittest.TestCase):
    def test_distributed_evidence_requires_explicit_complete_parts(self):
        fact={'value':'A;B','unit':'','qualifier':'usually;approximately',
              'field_evidence':{'value':{'parts':['A','B'],'joiner':';'},
                                'qualifier':{'parts':['usually','approximately'],'joiner':';'}}}
        fragments=['Options A and B usually remain approximately constant.']
        self.assertTrue(field_is_supported(fact,'value',fragments,literal_supported))
        self.assertTrue(field_is_supported(fact,'qualifier',fragments,literal_supported))
        no_parts={**fact,'field_evidence':{}}
        self.assertFalse(field_is_supported(no_parts,'qualifier',fragments,literal_supported))
        self.assertFalse(field_is_supported(fact,'qualifier',['usually constant'],literal_supported))

    def test_cannot_build_a_number_by_joining_digits_or_drop_a_part(self):
        for parts,joiner,value in [(['1','2'],'','12'),(['1','2'],' ','12'),(['A','B'],';','A'),
                                   (['A'],';','A;B'),(['1','2'],';','12')]:
            fact={'value':value,'field_evidence':{'value':{'parts':parts,'joiner':joiner}}}
            self.assertIsNotNone(field_parts_error(fact))

    def test_parts_cannot_certify_numeric_signs_substrings_or_unit_changes(self):
        fact={'value':'4.5;5','unit':'m²','field_evidence':{'value':{'parts':['4.5','5'],'joiner':';'}}}
        self.assertFalse(field_is_supported(fact,'value',['4.51, -5 m³'],literal_supported))
        self.assertFalse(field_is_supported(fact,'unit',['4.5 m³'],literal_supported))
        self.assertIsNotNone(field_parts_error({**fact,'field_evidence':{'unit':{'parts':['m','²'],'joiner':''}}}))

    def test_roman_glyphs_align_without_collapsing_powers_or_formula_symbols(self):
        self.assertEqual(canonical('Ⅶ VII ⅷ viii'),'VII VII viii viii')
        self.assertEqual(canonical('m² m³ x⁻² |x|'),'m² m³ x⁻² |x|')
        self.assertTrue(source_contains('Class Ⅶ','Class VII'))
        self.assertTrue(literal_supported('Ⅶ','Class VII'))
        self.assertFalse(literal_supported('Ⅳ类','Class XIV类'))
        self.assertFalse(literal_supported('Ⅳ','Class XIV'))
        for q,s in [('Ⅵ','VII'),('m²','m³'),('5','-5'),('|x|','x'),('4.5','4.51')]:
            self.assertFalse(source_contains(q,s))

    def test_original_offsets_survive_whitespace_and_roman_expansion(self):
        source='Before. Class Ⅶ is stable. After.'
        match=align_quote('Class VII is stable.',source)
        self.assertEqual(source[match['start']:match['end']],match['text'])
        self.assertEqual(match['text'],'Class Ⅶ is stable.')

    def test_terminal_sentence_stop_is_not_a_numeric_or_operator_wildcard(self):
        self.assertTrue(source_contains('速率基本不变。','速率基本不变，随后加速。'))
        for q,s in [('4.5.','4.51'),('m²。','m³'),('-5。','5'),('x+y。','x-y')]:
            self.assertFalse(source_contains(q,s))

    def test_invalid_declared_evidence_is_quarantined_without_losing_reading(self):
        answer=source_read({'bbox':[0,0,1,1]})
        answer['facts'].append(copy.deepcopy(answer['facts'][0]))
        answer['facts'][0]['field_evidence']={'value':{'parts':['4','51'],'joiner':''}}
        reading(answer)
        self.assertEqual(len(answer['facts']),2)
        self.assertTrue(answer['facts'][0]['evidence_contract_errors'])
        self.assertNotIn('evidence_contract_errors',answer['facts'][1])
        self.assertFalse(field_is_supported(answer['facts'][0],'value',[answer['transcript']],literal_supported))

    def test_shared_source_crop_removes_per_fact_box_fragmentation_losslessly(self):
        facts=[{'id':str(i),'bbox':[0,i/20,1,(i+1)/20]} for i in range(20)]
        batches=verification_batches(facts,12000,3,shared_region=True)
        self.assertEqual([len(b) for b in batches],[12,8])
        self.assertEqual([f for b in batches for f in b],facts)
        self.assertGreater(len(verification_batches(facts,12000,3)),len(batches))


class EvidenceWorkflowTests(unittest.TestCase):
    def test_parts_are_grounded_and_all_semantic_checks_remain_mandatory(self):
        class Qualified(ScriptedModel):
            failed=None
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                stage,_,_=self.events[-1]
                if stage=='read':
                    quote='Device C7 usually remains approximately constant.'
                    fact=answer['facts'][0]
                    fact.update(object='Device C7',field='behaviour',value='constant',unit='',
                                qualifier='usually;approximately',role='reference',quote=quote,
                                field_evidence={'qualifier':{'parts':['usually','approximately'],'joiner':';'}})
                    answer['transcript']=quote
                if stage=='verify' and self.failed:
                    for verdict in answer['verdicts']:verdict[self.failed]=False
                return answer,receipt
        for failed in [None,'object_supported','condition_supported','qualifiers_supported','scope_supported']:
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as td:
                root=Path(td);path=root/'input.pdf';make_pdf(path);model=Qualified();model.failed=failed
                result=VisualPipeline(model,root/'result').run([path])
                self.assertEqual(result['summary']['verified_model'],int(failed is None))
                fact=result['documents'][0]['facts'][0]
                self.assertTrue(fact['admission_checks']['read_fields_supported']['qualifier'])
                self.assertTrue(fact['source_grounding'])

    def test_unknown_scope_is_not_silently_changed_or_admitted(self):
        class UnknownScope(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                if self.events[-1][0]=='read':answer['facts'][0]['scope']='unknown'
                return answer,receipt
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'x.pdf';make_pdf(path)
            result=VisualPipeline(UnknownScope(),root/'out').run([path])
            self.assertEqual(result['summary']['verified_model'],0)
            fact=result['documents'][0]['facts'][0]
            self.assertEqual(fact['scope'],'unknown')
            self.assertFalse(fact['admission_checks']['explicit_object_scope_role'])

    def test_one_region_with_ten_different_fact_boxes_uses_one_shared_review(self):
        class Dense(ScriptedModel):
            def complete(self,**kwargs):
                answer,receipt=super().complete(**kwargs)
                if self.events[-1][0]=='read':
                    fact=answer['facts'][0]
                    answer['facts']=[{**copy.deepcopy(fact),'field':f'field_{i}','bbox':[0,i/10,1,(i+1)/10]} for i in range(10)]
                return answer,receipt
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'x.pdf';make_pdf(path);model=Dense()
            result=VisualPipeline(model,root/'out',settings=VisualSettings(max_calls=6)).run([path])
            self.assertEqual([s for s,_,_ in model.events],['layout','segment','read','read','verify','audit'])
            self.assertEqual(result['summary']['verified_model'],10)
            verify=next(e for e in model.events if e[0]=='verify')
            self.assertEqual(len(verify[1]['candidates']),10)
            self.assertEqual(len(verify[2]),2)

    def test_independent_audit_precedes_optional_zoom_and_stale_audit_cannot_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'x.pdf';make_pdf(path);model=ScriptedModel(zoom=True)
            result=VisualPipeline(model,root/'out',settings=VisualSettings(max_calls=7)).run([path])
            stages=[s for s,_,_ in model.events]
            self.assertEqual(stages[:6],['layout','segment','read','read','verify','audit'])
            self.assertEqual(stages[6],'read')
            self.assertEqual(result['processing_status'],'partial')
            self.assertFalse(result['documents'][0]['pages'][0]['audit'][-1]['complete'])


if __name__=='__main__':unittest.main()
