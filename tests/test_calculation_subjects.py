import unittest

from slopekg.engineering import calculation_subject_context, attach_engineering_records
from slopekg.extractors import extract_section_values, extract_structural_planes, merge_slope_candidates


class CalculationSubjectTests(unittest.TestCase):
    def test_maximum_is_not_a_degenerate_range(self):
        for value in (37, 62.5):
            for label in ('最大高差', '最大坡高'):
                result = extract_section_values(f'边坡长度约93m，{label}约{value}m，坡度约61—72°。')
                self.assertEqual(result['slope_height_max_m'], value)
                self.assertNotIn('slope_height_min_m', result)
                self.assertTrue(result['slope_height_maximum_only'])
        result = extract_section_values('边坡高约18—26m。最大高差约31m。'.replace('—', '~'))
        self.assertEqual((result['slope_height_min_m'], result['slope_height_max_m']), (18,31))

    def test_landform_height_does_not_beat_exposed_cut(self):
        for ridge, face in ((73, 29), (29, 73)):
            result = extract_section_values(f'路线绕过小山脊，高{ridge}m。内侧基岩陡壁高12~{face}m。')
            self.assertEqual((result['slope_height_min_m'],result['slope_height_max_m']), (12,face))
            self.assertEqual(result['scoped_geometry'][0]['scope'], '周边地形')

    def test_geological_subject_requires_layer_composition_and_predicate(self):
        for direction, angle in ((218, 37), (81.5, 66.5)):
            result = extract_structural_planes(f'地层属志留系，岩性以砂岩为主，产状为{direction}∠{angle}。')
            self.assertEqual([(p['name'],p['dip_direction'],p['dip_angle']) for p in result], [('岩层面',direction,angle)])
        for text in ('岩性以砂岩为主，产状为218∠37。',
                     '地层属志留系，岩性以砂岩为主。产状为218∠37。',
                     '对岸地层属志留系，岩性以砂岩为主，产状为218∠37。',
                     '地层属志留系，岩性以砂岩为主，节理产状为218∠37。'):
            self.assertFalse(any(p['name']=='岩层面' for p in extract_structural_planes(text)))

    def test_ambiguous_rock_pair_reconciles_only_with_same_typed_pair(self):
        planes=[dict(name=n,dip_direction=d,dip_angle=a) for n,d,a in
                [('岩体',81,37),('岩层面',81,37),('L1',81,37),('岩体',95,46)]]
        row=dict(station='K9+000-K9+080',route_code='G999',method='test',page=1,
                 evidence_block_id='b',confidence=.8,structural_planes=planes)
        result=merge_slope_candidates([row])[0]['structural_planes']
        self.assertEqual([p['name'] for p in result], ['岩层面','L1','岩体'])

    def test_subsection_context_resets_and_requires_unique_body(self):
        blocks=[dict(id='h',text='（3）单个危岩体稳定性计算'),dict(id='b',text='危岩体WY09：破坏模式为滑移式。')]
        self.assertEqual(calculation_subject_context(blocks)['body_id'], 'WY09')
        self.assertNotIn('body_id', calculation_subject_context(blocks+[dict(id='b2',text='危岩体WY10：另一个块体。')]))
        self.assertEqual(calculation_subject_context(blocks+[dict(id='next',text='（4）防治措施')]), {})
        self.assertNotIn('body_id', calculation_subject_context([dict(id='h',text='（3）危岩体稳定性计算'),blocks[1]]))
        for scope in ('上部土质边坡','下部岩质边坡'):
            self.assertEqual(calculation_subject_context([dict(id='h',text=f'2）{scope}稳定性分析'),
                dict(id='numeric',text='1.23  1.08'), dict(id='standard',text='3.2.1 条规定'),
                dict(id='row',text='（1） YC-L1 26 17')])['analysis_scope'], scope)

    def test_table_refines_incomplete_prose_but_not_conflicting_objects(self):
        result=dict(condition='天然',safety_factor=1.07,analysis_scope='上部土质边坡',section='Z-Z′')
        record=dict(id='t',route_code='G999',station='K9+000-K9+080',document_id='d',page=2,bbox=[0,0,100,100],
                    stability_results=[result],quality_issues=[],method='test',rows=[],
                    calculation_subject_context=dict(analysis_scope='上部土质边坡'))
        for scope, count in [('现状边坡',1),('整体边坡',2)]:
            slope=dict(route_code='G999',station=record['station'],stability_scenarios=[dict(
                condition='天然',safety_factor=1.07,analysis_scope=scope,evidence_document_id='d',evidence_page=2)])
            attach_engineering_records([slope],[record])
            self.assertEqual(len(slope['stability_scenarios']),count)
            if count==1:self.assertEqual(slope['stability_scenarios'][0]['section'],'Z-Z′')
        ambiguous={**record,'id':'t2','stability_results':[{**result,'section':'Y-Y′'}]}
        slope=dict(route_code='G999',station=record['station'],stability_scenarios=[dict(
            condition='天然',safety_factor=1.07,analysis_scope='现状边坡',evidence_document_id='d',evidence_page=2)])
        attach_engineering_records([slope],[record,ambiguous])
        self.assertNotIn('section',slope['stability_scenarios'][0])


if __name__=='__main__':unittest.main()
