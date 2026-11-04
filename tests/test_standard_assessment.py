"""Software boundary tests; synthetic unit inputs never count as real-case success."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from slopekg.config import PATHS
from slopekg.standard_assessment import StandardAssessmentService
from slopekg.standard_assessment.contract import (CONTRACT_VERSION, VersionConflict, enumeration,
    fact_record, number, validate_fact)
from slopekg.standard_assessment.rules import coefficient_axis, load_rules, metric_grades, table15, table17

ROOT = Path(__file__).resolve().parents[1]
CURATED = ROOT/'.private/knowledge/GBT40112-2021/assessment_v1/real_case_evidence.json'
SCOPE = {'subject':{'source_object_ref':'anonymous/unit/body'},
         'time':{'kind':'instant','raw':'2020-01-01','start':'2020-01-01','end':'2020-01-01'},
         'scenario':{'code':'natural','raw':'natural','basis_refs':[]}}


def unit_fact(variable, value, scope=None):
    return fact_record(variable,value,scope or SCOPE,source_refs=['anonymous-unit-source'],evidence_refs=[],
                       origin='manual',support='human_supported')


class TableContractTests(unittest.TestCase):
    """Deliberately anonymous unit data, not engineering validation."""
    def setUp(self):
        self.rules = load_rules(ROOT)

    def test_people_threshold_endpoints_and_zero(self):
        for variable, values in [('deaths',[(0,'危害小'),(2,'危害小'),(3,'危害中等'),(10,'危害中等'),(11,'危害大')]),
                                 ('threatened_people',[(0,'危害小'),(9,'危害小'),(10,'危害中等'),(100,'危害中等'),(101,'危害大')])]:
            for n, grade in values:
                with self.subTest(variable=variable,n=n):
                    self.assertEqual(metric_grades(unit_fact(variable,number(n,'person')),self.rules),[grade])

    def test_loss_endpoints_and_one_metric(self):
        for n, grade in [(0,'危害小'),(99.9,'危害小'),(100,'危害中等'),(500,'危害中等'),(500.1,'危害大')]:
            f = unit_fact('potential_loss_wan',number(n,'10k_CNY'))
            result = table15('险情',[f],self.rules)
            self.assertEqual(result['harm'],grade)
            self.assertEqual(result['coverage']['provided_indicators'],1)

    def test_conflict_retains_both_indicator_results(self):
        result = table15('险情',[unit_fact('threatened_people',number(120,'person')),unit_fact('potential_loss_wan',number(50,'10k_CNY'))],self.rules)
        self.assertIsNone(result['harm'])
        self.assertTrue(result['conflict'])
        self.assertEqual(set(result['possible_grades']),{'危害大','危害小'})

    def test_interval_does_not_use_midpoint(self):
        value = {'kind':'interval','lower':90,'upper':120,'lower_inclusive':True,'upper_inclusive':True,'unit':'person','qualifier':'exact_as_reported','interval_semantics':'reported_range'}
        self.assertEqual(set(metric_grades(unit_fact('threatened_people',value),self.rules)),{'危害中等','危害大'})
        value.update(lower=10,upper=100,upper_inclusive=False)
        self.assertEqual(metric_grades(unit_fact('threatened_people',value),self.rules),['危害中等'])

    def test_invalid_numbers_and_units_are_rejected(self):
        for value in [True,'120',-1,float('inf'),float('nan'),0.1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                unit_fact('threatened_people',number(value,'person'))
        with self.assertRaisesRegex(ValueError,'unit'):
            unit_fact('direct_loss_wan',number(120,'CNY'))

    def test_enum_is_not_interchangeable(self):
        f = unit_fact('development',enumeration('development','中等发育'))
        f['value']['vocabulary'] = 'gb-harm@2021'
        with self.assertRaisesRegex(ValueError,'vocabulary'):
            validate_fact(f)

    def test_table17_all_nine_cells(self):
        expected = [['危险性大','危险性大','危险性中等'],['危险性大','危险性中等','危险性小'],['危险性中等','危险性中等','危险性小']]
        for i,d in enumerate(['强发育','中等发育','弱发育']):
            for j,h in enumerate(['危害大','危害中等','危害小']):
                self.assertEqual(table17([d],[h],self.rules)['grade'],expected[i][j])

    def test_coefficient_branch_and_scenario_mismatch(self):
        self.assertEqual(coefficient_axis(unit_fact('fs',number(1,'1')))['axis'],'不稳定')
        self.assertEqual(coefficient_axis(unit_fact('fs',number(1.1,'1')))['missing'],['fst'])
        other = copy.deepcopy(SCOPE)
        other['scenario']['code'] = 'rainstorm'
        with self.assertRaisesRegex(ValueError,'scenario'):
            coefficient_axis(unit_fact('fs',number(1.1,'1')),unit_fact('fst',number(1.15,'1'),other))


class AnonymousWorkflowTests(unittest.TestCase):
    """Core workflow branches only: these are NOT real engineering cases."""
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='standard-unit-',dir=ROOT/'.tmp')
        self.service = StandardAssessmentService(replace(PATHS,output_dir=Path(self.temp.name)))

    def tearDown(self):
        self.temp.cleanup()

    def test_supplied_interval_is_uncertainty_not_a_missing_input(self):
        development=unit_fact('development',enumeration('development','弱发育'))
        development['source_standard']='GB/T 40112-2021'
        population=unit_fact('threatened_people',{'kind':'interval','lower':7,'upper':14,
            'lower_inclusive':True,'upper_inclusive':True,'unit':'person',
            'qualifier':'exact_as_reported','interval_semantics':'reported_range'})
        result=self.run_case([development,population])
        self.assertIsNone(result['table17_result']['grade'])
        self.assertEqual(len(result['table17_result']['possible_grades']),2)
        self.assertEqual(result['minimal_missing'],[])
        self.assertEqual(result['workflow_status'],'needs_resolution')

    def run_case(self,facts):
        case = {'case_id':'anonymous-software-test','contract_version':CONTRACT_VERSION,'scope':SCOPE,
                'resolution_version':1,'fact_ids':[f['fact_id'] for f in facts],'selections':{},'context_ids':[],
                'hazard_type_supported':True,'source_hashes':{},'source_coverage':'ANONYMOUS UNIT TEST ONLY'}
        with self.service.store.transaction() as db:
            for f in facts:
                self.service.store.put('fact_assertion',f,db)
            self.service.store.put('assessment_case',case,db)
        return self.service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':1})

    def grade(self,var,label):
        f = unit_fact(var,enumeration(var,label))
        f['source_standard'] = 'GB/T 40112-2021'
        return f

    def test_existing_grades_do_not_require_fs_or_all_indicators(self):
        r = self.run_case([self.grade('development','中等发育'),self.grade('harm','危害大')])
        self.assertEqual(r['table17_result']['grade'],'危险性大')
        self.assertEqual(r['basis_status'],'supported')
        self.assertFalse(r['partials']['stability_axes'])
        self.assertEqual(r['review_status'],'pending')
        self.assertFalse(r['formal_assessment_enabled'])

    def test_known_conflicting_metric_is_not_ignored_when_another_is_available(self):
        r = self.run_case([self.grade('development','中等发育'),unit_fact('assessment_basis',enumeration('assessment_basis','险情')),
            unit_fact('threatened_people',number(120,'person')),unit_fact('threatened_people',number(80,'person')),
            unit_fact('potential_loss_wan',number(50,'10k_CNY'))])
        self.assertIsNone(r['table17_result']['grade'])
        self.assertTrue(r['partials']['table15']['conflict'])
        self.assertEqual(len(r['partials']['table15']['indicator_results']),3)
        self.assertEqual(r['workflow_status'],'needs_resolution')

    def test_model_judgment_remains_conditional_after_lookup(self):
        dev = self.grade('development','中等发育')
        dev['origin_kind'] = 'llm'
        dev['source_check']['content'] = 'model_supported'
        r = self.run_case([dev,self.grade('harm','危害大')])
        self.assertEqual(r['table17_result']['grade'],'危险性大')
        self.assertEqual(r['basis_status'],'conditional')
        self.assertEqual(r['review_status'],'pending')

    def test_metric_selection_is_used_and_can_be_cleared(self):
        choices=[unit_fact('threatened_people',number(80,'person')),unit_fact('threatened_people',number(120,'person'))]
        before=self.run_case([self.grade('development','中等发育'),unit_fact('assessment_basis',enumeration('assessment_basis','险情'))]+choices)
        req={'contract_version':CONTRACT_VERSION,'case_id':before['case_id'],'expected_resolution_version':1,
             'target_id':choices[0]['fact_id'],'actor_kind':'human','reviewer_ref':'anonymous-unit-reviewer',
             'reason':'anonymous software test only','decision':'select_branch'}
        self.service.review_assessment(req)
        def run(v):return self.service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':before['case_id'],'expected_resolution_version':v})
        selected=run(2)
        self.assertEqual(selected['table17_result']['grade'],'危险性中等')
        self.assertEqual(selected['partials']['table15']['input_fact_ids'],[choices[0]['fact_id']])
        self.service.review_assessment(dict(req,decision='clear_selection',expected_resolution_version=2))
        cleared=run(3)
        self.assertIsNone(cleared['table17_result']['grade'])
        self.assertTrue(cleared['conflicts'])

    def test_result_confirmation_is_human_only_and_versioned(self):
        before=self.run_case([self.grade('development','中等发育'),self.grade('harm','危害大')])
        req={'contract_version':CONTRACT_VERSION,'case_id':before['case_id'],'expected_resolution_version':1,
             'target_id':before['result_id'],'actor_kind':'assistant','reviewer_ref':'anonymous-unit-reviewer',
             'reason':'anonymous software test only','decision':'confirm_result','context_reviewed':True}
        with self.assertRaises(ValueError):self.service.review_assessment(req)
        review=self.service.review_assessment(dict(req,actor_kind='human'))
        after=self.service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':before['case_id'],'expected_resolution_version':2})
        self.assertEqual(after['review_status'],'accepted')
        self.assertEqual(after['workflow_status'],'reviewed')
        self.assertEqual(after['table17_result']['grade'],'危险性大')
        self.assertTrue(review['review_decision']['professional_acceptance'])
        self.assertTrue(any(not r['is_current'] for r in self.service.get_case(before['case_id'])['results']))

    def test_typed_threatened_count_infers_basis_without_an_extra_input(self):
        result=self.run_case([self.grade('development','中等发育'),unit_fact('threatened_people',number(120,'person'))])
        self.assertEqual(result['table17_result']['grade'],'危险性大')
        self.assertEqual(result['resolved_variables']['assessment_basis']['status'],'derived')
        record=self.service.get_case(result['case_id'])
        self.assertTrue(any(f['variable_code']=='assessment_basis' for f in record['derived_facts']))

    def test_assistant_source_note_is_not_professional_acceptance(self):
        before=self.run_case([self.grade('development','中等发育'),self.grade('harm','危害大')])
        note=self.service.review_assessment({'contract_version':CONTRACT_VERSION,'case_id':before['case_id'],
            'expected_resolution_version':1,'decision':'note','target_id':before['result_id'],
            'actor_kind':'assistant','reviewer_ref':'anonymous-unit-source-check','reason':'anonymous source-date concern'})
        result=self.service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':before['case_id'],'expected_resolution_version':2})
        self.assertEqual(result['review_status'],'pending')
        self.assertFalse(note['review_decision']['professional_acceptance'])
        self.assertEqual(result['review_attention'][0]['reason'],'anonymous source-date concern')

    def test_mixed_disaster_and_threat_metrics_do_not_guess_assessment_basis(self):
        result=self.run_case([self.grade('development','中等发育'),unit_fact('deaths',number(0,'person')),unit_fact('threatened_people',number(120,'person'))])
        self.assertIsNone(result['table17_result']['grade'])


@unittest.skipUnless(PATHS.graph_json.is_file() and CURATED.is_file(),'local real source snapshots unavailable')
class SavedSourceIntegrationTests(unittest.TestCase):
    """Read the actual local graph/quotes and exercise the actual SQLite service."""
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='standard-pilot-',dir=ROOT/'.tmp')
        self.paths = replace(PATHS,output_dir=Path(self.temp.name))
        self.service = StandardAssessmentService(self.paths,curated_evidence=CURATED)

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self,target='G351-AUTO-038',**extra):
        return self.service.prepare_assessment({'contract_version':CONTRACT_VERSION,'target_ref':target,**extra})['case']

    def evaluate(self,case,version=None):
        return self.service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':version or case['resolution_version']})

    def test_real_direct_source_and_partial_derivation_are_persisted(self):
        case = self.prepare()
        result = self.evaluate(case)
        self.assertEqual(len(result['partials']['source_adoptions']),2)
        self.assertEqual({row['axis'] for row in result['partials']['stability_axes']},{'欠稳定'})
        self.assertIsNone(result['table17_result']['grade'])
        self.assertEqual(result['review_status'],'pending')
        reopened = StandardAssessmentService(self.paths,curated_evidence=CURATED).get_case(case['case_id'])
        self.assertTrue(reopened['results'])
        self.assertEqual({m['variable_or_feature'] for m in result['minimal_missing']},{'development','harm'})

    def test_actual_historical_deaths_do_not_fill_current_exposure(self):
        current = self.evaluate(self.prepare())
        self.assertIsNone(current['partials']['table15'])
        past = self.prepare(time_selector={'kind':'event_period','raw':'2016年雨季'})
        history = self.evaluate(past)
        self.assertEqual(history['partials']['table15']['harm'],'危害小')
        self.assertEqual(history['partials']['table15']['coverage']['provided_indicators'],1)
        self.assertFalse(history['partials']['stability_axes'])
        self.assertIsNone(history['table17_result']['grade'])

    def test_real_zones_and_interval_survive_adaptation(self):
        case = self.prepare('G344-AUTO-027')
        data = self.service.get_case(case['case_id'])
        zone_facts = [f for f in data['facts'] if f.get('feature_code') in {'zone_I_status','zone_II_status'}]
        self.assertEqual(len(zone_facts),2)
        self.assertNotEqual(zone_facts[0]['scope']['subject'],zone_facts[1]['scope']['subject'])
        result = self.evaluate(case)
        self.assertEqual(len(result['partials']['normalizations']),1)
        normalized = self.service.store.get('fact_assertion',result['partials']['normalizations'][0])
        self.assertEqual(normalized['value']['lower'],0.1)
        self.assertEqual(normalized['value']['upper'],0.2)
        self.assertEqual(normalized['value']['qualifier'],'approximate')

    def test_actual_conflicting_extraction_is_retained_and_wrong_hazard_stops(self):
        result = self.evaluate(self.prepare('G351-AUTO-045'))
        self.assertEqual(result['workflow_status'],'invalid_request')
        self.assertTrue(any(c.get('kind')=='potential_source_or_scope_conflict' for c in result['conflicts']))
        self.assertIsNone(result['table17_result']['grade'])

    def test_source_binding_correction_preserves_history_and_checks_version(self):
        case = self.prepare()
        before = self.evaluate(case)
        data = self.service.get_case(case['case_id'])
        target = next(f for f in data['facts'] if f['origin_kind']=='basic' and f['value']=={'kind':'text','text':'拉张裂缝宽约1.8m'})
        self.assertEqual(target['source_check']['content'],'unsupported')
        contexts = self.service.store.rows('evidence_context')
        correct = next(c for c in contexts if c['spans'][0]['kind']=='page_text' and '拉张裂缝宽约1.8m' in c['spans'][0]['quote'])
        review = self.service.review_assessment({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':1,
            'target_id':target['fact_id'],'decision':'correct','evidence_refs':[correct['context_id']],
            'actor_kind':'assistant','reviewer_ref':'automated-test-source-check','reason':'Real original body text supports the same observation; old binding only quoted a header.'})
        self.assertFalse(review['review_decision']['professional_acceptance'])
        after = self.evaluate(review['case'])
        self.assertTrue(set(before['trace_derivation_ids'])<=set(after['reused_derivation_ids']))
        self.assertEqual(self.service.store.get('fact_assertion',target['fact_id'])['source_check']['content'],'unsupported')
        replacement = self.service.store.get('fact_assertion',review['review_decision']['replacement_fact_id'])
        self.assertEqual(replacement['source_check']['content'],'literal_matched')
        self.assertEqual(after['review_status'],'pending')
        with self.assertRaises(VersionConflict):
            self.evaluate(case,1)
        self.assertTrue(any(not r['is_current'] for r in self.service.get_case(case['case_id'])['results']))

    def test_context_returns_headers_without_turning_model_review_into_human(self):
        case = self.prepare()
        fs = next(f for f in self.service.get_case(case['case_id'])['facts'] if f['variable_code']=='fs')
        response = self.service.get_fact_context({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'fact_ids':[fs['fact_id']]})
        self.assertIn('安全系数',response['contexts'][0]['table_context']['raw_table_text'])
        self.assertTrue(response['contexts'][0]['snapshot_matches_current_source'])
        self.assertEqual(len(response['rule_context']['qualitative_context']),2)

    def test_http_dispatch_uses_real_service_and_database(self):
        from slopekg.server import SlopeKGHandler
        handler = object.__new__(SlopeKGHandler)
        handler.path = '/api/standard-assessment/prepare'
        handler.request_origin_allowed = Mock(return_value=True)
        handler.read_json_body = Mock(return_value={'contract_version':CONTRACT_VERSION,'target_ref':'G351-AUTO-038'})
        handler.write_json = Mock()
        with patch('slopekg.server.PATHS',self.paths):
            handler.do_POST()
        response = handler.write_json.call_args.args[0]
        self.assertEqual(response['case']['slope_id'],'G351-AUTO-038')
        self.assertTrue(self.service.get_case(response['case']['case_id'])['facts'])

    def test_single_model_proposal_is_grounded_cached_and_not_human_approval(self):
        class GroundedModel:
            calls=0
            def identity(self):return {'name':'anonymous-unit-model'}
            def complete(model,*,system,prompt,images):
                model.calls+=1
                packet=json.loads(prompt)
                observed=next(f for f in packet['facts'] if f['variable']=='observations')
                return {'decision':'propose','development':'中等发育',
                        'support':[{'fact_id':observed['fact_id'],'quote':observed['quotes'][0]['quote'],'reason':'anonymous contract test only'}],
                        'counterevidence':[],'missing':['需专业复核'],'rationale':'anonymous software response, not actual model prediction',
                        'rule_refs':['GBT40112-T4-CONTEXT','GBT40112-T5-CONTEXT']},{'usage':{'total_tokens':10}}
        model=GroundedModel();self.service.judgment_model=model
        case=self.prepare()
        request={'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':1}
        proposed=self.service.judge_assessment(request)
        after=self.evaluate(proposed['case'])
        self.assertEqual(after['partials']['professional_context_task']['execution_status'],'proposed')
        self.assertIsNotNone(after['resolved_variables']['development']['selected_fact_id'])
        dev=next(f for f in self.service.get_case(case['case_id'])['facts'] if f['variable_code']=='development')
        self.assertEqual(dev['origin_kind'],'llm')
        self.assertEqual(after['review_status'],'pending')
        self.assertIsNone(after['table17_result']['grade'])
        self.service.judge_assessment(dict(request,expected_resolution_version=2))
        self.assertEqual(model.calls,1)
        newcase=self.service.add_assertion({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':2,
            'variable':'harm','value':enumeration('harm','危害大'),'actor_kind':'human',
            'actor_ref':'ANONYMOUS_UNIT_TEST_NOT_ACTUAL_ENGINEER','reason':'anonymous downstream lookup test only',
            'source_standard':'GB/T 40112-2021'})['case']
        combined=self.evaluate(newcase)
        self.assertEqual(combined['table17_result']['grade'],'危险性大')
        self.assertEqual(combined['basis_status'],'conditional')
        self.assertEqual(combined['review_status'],'pending')
        self.assertFalse(combined['formal_assessment_enabled'])
        from slopekg.standard_assessment.contract import applicability
        rejected=copy.deepcopy(dev)
        rejected['source_check']['content']='unsupported'
        self.assertEqual(applicability(rejected,proposed['case'])['status'],'incompatible')

    def test_model_cannot_invent_fact_reference_or_quote(self):
        from slopekg.standard_assessment.workflow import AssessmentWorkflow
        packet={'facts':[{'fact_id':'actual','variable':'observations','quotes':[{'quote':'现场后缘有裂缝'}]}]}
        answer={'decision':'propose','development':'强发育','support':[{'fact_id':'invented','quote':'现场后缘有裂缝','reason':'test'}],
                'counterevidence':[],'missing':[],'rationale':'test','rule_refs':['GBT40112-T4-CONTEXT']}
        with self.assertRaises(ValueError):AssessmentWorkflow.validate_judgment(answer,packet)
        answer['support'][0].update(fact_id='actual',quote='虚构的整体位移')
        with self.assertRaises(ValueError):AssessmentWorkflow.validate_judgment(answer,packet)

    def test_registered_manual_assertion_completes_lookup_and_invalidates_old_review(self):
        case=self.prepare()
        for variable,value in [('development',enumeration('development','中等发育')),('harm',enumeration('harm','危害大'))]:
            case=self.service.add_assertion({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],
                  'expected_resolution_version':case['resolution_version'],'variable':variable,'value':value,
                  'actor_kind':'human','actor_ref':'ANONYMOUS_UNIT_TEST_NOT_ACTUAL_ENGINEER','reason':'software entry contract test',
                  'source_standard':'GB/T 40112-2021'})['case']
        result=self.evaluate(case)
        self.assertEqual(result['table17_result']['grade'],'危险性大')
        self.assertEqual(result['workflow_status'],'ready_for_review')
        self.assertEqual(result['review_status'],'pending')
        with self.assertRaises(ValueError):
            self.service.add_assertion({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],
                'expected_resolution_version':case['resolution_version'],'variable':'threatened_people','value':number(120,'person'),
                'actor_kind':'assistant','actor_ref':'test','reason':'test'})

    def test_retrieval_does_not_convert_document_thresholds_to_site_inputs(self):
        case=self.prepare()
        before=self.service.get_case(case['case_id'])['facts']
        retrieved=self.service.retrieve_assessment({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':1})
        self.assertTrue(retrieved['retrieval']['searched'])
        self.assertTrue(all(not h['adopted'] for h in retrieved['retrieval']['hits']))
        self.assertEqual(len(before),len(self.service.get_case(case['case_id'])['facts']))
        self.assertEqual(retrieved['case']['resolution_version'],2)

    def test_development_packet_excludes_other_axes_recommendations_and_old_body(self):
        case=self.prepare('G344-AUTO-027')
        with self.service.store.transaction() as db:
            facts=self.service._facts(case,db)
            packet=self.service.judgment_packet(case,facts,db)
        features={r['feature'] for r in packet['facts']}
        self.assertTrue({'zone_I_status','zone_II_status'}<=features)
        self.assertTrue(features.isdisjoint({'other_standard_harm','proposed_treatment','observed_consequence','old_landslide_reported_status'}))
        self.assertTrue(packet['excluded_facts'])
        self.assertEqual(next(f for f in facts if f.get('feature_code')=='proposed_treatment')['role'],'recommendation')
        historical=next(f for f in facts if f.get('feature_code')=='historical_event')
        self.assertEqual(historical['scope']['time']['kind'],'event_period')
        self.assertNotEqual(historical['scope']['time'],case['scope']['time'])
        self.assertNotIn(historical['fact_id'],{f['fact_id'] for f in packet['facts']})

    def test_rejected_model_response_and_reason_are_retained_without_a_grade(self):
        class BadReferenceModel:
            def identity(self):return {'name':'anonymous-unit-model-invalid-reference'}
            def complete(self,**_):
                return {'decision':'propose','development':'强发育','support':[{'fact_id':'invented','quote':'虚构','reason':'test'}],
                        'counterevidence':[],'missing':[],'rationale':'anonymous software invalid reply','rule_refs':['GBT40112-T4-CONTEXT']},{'id':'unit','usage':{'total_tokens':1}}
        self.service.judgment_model=BadReferenceModel()
        case=self.prepare()
        judged=self.service.judge_assessment({'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':1})
        self.assertEqual(judged['judgment']['execution_status'],'failed')
        self.assertEqual(judged['judgment']['rejected_answer']['support'][0]['fact_id'],'invented')
        self.assertEqual(judged['judgment']['validation_error'],'invalid evidence reference')
        self.assertFalse(judged['judgment']['output_fact_ids'])


if __name__=='__main__':
    unittest.main()
