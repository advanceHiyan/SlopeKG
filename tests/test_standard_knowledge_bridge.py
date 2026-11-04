import copy
import unittest

from slopekg.standard_assessment.knowledge_bridge import candidate,explicit_judgment_candidates,normalize_value


class KnowledgeBridgeTests(unittest.TestCase):
    def raw(self,field,value,unit='',quote=None,**extra):
        return {'field':field,'value':value,'unit':unit,'role':'observation','object':'青山滑坡','scope':'青山滑坡',
                'time':'2026-09-02','condition':'天然工况','status':'source_aligned',
                'evidence':[{'quote':quote or f'{field}：{value}{unit}。','page':1,'source':'pdf_native'}],**extra}

    def test_currency_conversion_retains_decimal_value(self):
        r=normalize_value(self.raw('潜在经济损失','0.13','亿元'))
        self.assertEqual(r['value']['value'],1300)
        self.assertEqual(r['transformations'][0]['raw_unit'],'亿元')

    def test_unknown_is_not_zero(self):
        r=normalize_value(self.raw('受威胁人数','未统计','人'))
        self.assertTrue(r['accepted']);self.assertIsNone(r['value'])

    def test_people_interval_is_not_scalar(self):
        r=normalize_value(self.raw('受威胁人数区间','7～13','人'))
        self.assertEqual((r['value']['lower'],r['value']['upper']),(7,13))
        self.assertEqual(r['value']['kind'],'interval')

    def test_households_and_fractional_people_not_converted(self):
        for raw in [self.raw('受威胁人数','12','户'),self.raw('受威胁人数','2.5','人')]:
            self.assertFalse(normalize_value(raw)['accepted'])

    def test_missing_and_conflicting_currency_units_rejected(self):
        for raw in [self.raw('潜在经济损失','30',''),self.raw('潜在经济损失','0.1亿元','万元')]:
            self.assertFalse(normalize_value(raw)['accepted'])

    def test_ambiguous_loss_field_and_design_capacity_not_inputs(self):
        for field in ['经济损失','设施容量','计划施工人员']:
            self.assertFalse(normalize_value(self.raw(field,'200','人'))['accepted'])

    def test_other_standards_and_negation_not_development(self):
        for raw in [self.raw('稳定状态','基本稳定'),self.raw('危害程度','危害轻'),
                    self.raw('发育程度','弱发育',quote='不得把缺少观察判为弱发育。')]:
            self.assertFalse(normalize_value(raw)['accepted'])

    def scope(self):
        return {'subject':{'source_object_ref':'青山'},'time':{'kind':'instant','raw':'2026-09-02','start':'2026-09-02','end':'2026-09-02'},
                'scenario':{'code':'natural','raw':'天然工况','basis_refs':[]}}

    def mapped(self,raw,page='青山滑坡。调查日：2026-09-02。天然工况，GB/T 40112-2021。',targets=None):
        return candidate(raw,case_scope=self.scope(),target_key='青山',aliases=['青山滑坡'],page_texts={1:page},page_targets={1:targets or {'青山'}})

    def test_historical_period_and_role_preserved(self):
        r=self.mapped(self.raw('受威胁人数','300','人',time='2018-08-01',role='reference'))
        self.assertEqual(r['scope']['time']['start'],'2018-08-01');self.assertEqual(r['role'],'reference')

    def test_prediction_is_not_current_observation(self):
        r=self.mapped(self.raw('预测受威胁人数','200','人',role='prediction',condition='若暴雨'))
        self.assertEqual(r['role'],'prediction');self.assertEqual(r['scope']['scenario']['code'],'rainstorm')

    def test_generic_concept_requires_unique_source_object(self):
        raw=self.raw('受威胁人数','22','人',object='受威胁人数',scope='')
        self.assertTrue(self.mapped(raw)['accepted'])
        self.assertFalse(self.mapped(raw,targets={'青山','白石'})['accepted'])

    def test_missing_fact_date_stays_unknown_without_source_date(self):
        r=self.mapped(self.raw('受威胁人数','22','人',time=''),page='青山滑坡，天然工况。')
        self.assertEqual(r['scope']['time']['kind'],'unknown')

    def test_following_prediction_chapter_does_not_relabel_current_fact(self):
        page='虚构测试档案\n青山滑坡天然工况现状\n调查日：2026-09-02；天然工况，GB/T40112-2021。\n本期受威胁22人。\n下一页是暴雨预测情景，不能替换本期观察。'
        r=self.mapped(self.raw('受威胁人数','22','人'),page=page)
        self.assertEqual(r['role'],'observation')

    def test_subscope_is_not_promoted_to_whole_landslide(self):
        r=self.mapped(self.raw('受威胁人数','22','人',scope='青山滑坡I区'))
        self.assertNotEqual(r['scope']['subject']['source_object_ref'],self.scope()['subject']['source_object_ref'])

    def test_digit_substring_does_not_match_another_number(self):
        r=normalize_value(self.raw('受威胁人数','9','人',quote='受威胁人数为90人。'))
        self.assertFalse(r['accepted'])

    def test_only_explicit_judgment_is_retrieved(self):
        units=[{'id':str(i),'page':1,'source':'ocr','text':text} for i,text in enumerate([
            '发育程度专业结论：中等发育。','后缘裂缝发育。','稳定系数Fs=0.92。',
            '没有形成发育程度结论：弱发育。','不得判为发育程度结论：强发育。'])]
        r=explicit_judgment_candidates(units)
        self.assertEqual([x['value'] for x in r],['中等发育'])

    def test_current_estimate_is_conditional_future_hypothesis_stays_blocked(self):
        raw=self.raw('潜在经济损失','120','万元',role='prediction',quote='青山滑坡 | 2026-09-02 | 潜在经济损失 | 120万元 | 险情估计')
        r=self.mapped(raw);self.assertEqual(r['role'],'reported_estimate');self.assertEqual(r['basis_status'],'conditional')
        raw['condition']='暴雨假设';raw['time']='2026-10-02'
        r=self.mapped(raw);self.assertEqual(r['role'],'prediction')

    def test_normalization_never_mutates_original_fact(self):
        raw=self.raw('潜在经济损失','0.13','亿元');saved=copy.deepcopy(raw)
        self.mapped(raw);self.assertEqual(raw,saved)


if __name__=='__main__':unittest.main()
