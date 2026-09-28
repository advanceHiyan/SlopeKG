import unittest

from slopekg.engineering import extract_factor_cells
from slopekg.extractors import extract_document_observations, extract_section_values, extract_stability_scenarios, station_section_segments, extract_structural_planes
from slopekg.observation import supported_observation, belongs_to_station
from slopekg.semantic import select_source_samples, context_windows


class EvaluationRegressionTests(unittest.TestCase):
    def test_negation_does_not_erase_neighbouring_positive_observation(self):
        rows = extract_document_observations('挡墙未见变形，但后缘有裂缝。坡顶无地裂缝。', domain='deformation')
        self.assertEqual([r['type'] for r in rows], ['裂缝'])
        self.assertEqual(rows[0]['location'], '后缘')

    def test_observed_deformation_does_not_turn_future_rockfall_into_observation(self):
        text = '岩体出现显著的卸荷变形，在坡体表面形成松动岩体，在重力作用下，易发生落石掉块。'
        rows = extract_document_observations(text, domain='deformation')
        self.assertEqual([r['type'] for r in rows], ['变形'])
        self.assertFalse(supported_observation({'type':'落石','description':text}, text, domain='deformation'))
        self.assertTrue(supported_observation({'type':'变形','description':text}, text, domain='deformation'))

    def test_existing_water_survives_future_hazard_but_simulation_is_not_observation(self):
        self.assertEqual(len(extract_document_observations('坡脚可见地下水，在暴雨作用下，易发生滑塌。', domain='hydrology')), 1)
        self.assertEqual(extract_document_observations('通过模拟，落石出现跳跃，最大速度20m/s。', domain='deformation'), [])

    def test_observation_needs_its_own_source_not_just_a_field_quote(self):
        self.assertFalse(supported_observation({'type':'裂缝','description':'后缘可见裂缝'}, '坡脚有地下水', domain='deformation'))
        self.assertFalse(belongs_to_station({'description':'K1+000~K1+100坡脚可见地下水'},'K2+000-K2+100'))
        self.assertTrue(belongs_to_station({'description':'K1+000~K1+100坡脚可见地下水'},'K1+000-K1+100'))

    def test_no_empty_alias_fallback_and_no_neighbouring_heading(self):
        slope={'station':'K1+000-K1+100','section_text_samples':[{'page':1,'text':'完全无关的坡脚地下水描述'}]}
        self.assertEqual(select_source_samples(slope), [])
        text='3.1 K1+000-K1+100 边坡，天然稳定系数1.2。3.2 K2+000-K2+100 边坡，天然稳定系数0.8。'
        selected=context_windows(text,['K1+000-K1+100'])
        self.assertTrue(selected)
        self.assertNotIn('0.8',''.join(selected))

    def test_side_and_cut_slope_angle_are_not_opposite_mountain_or_original_slope(self):
        self.assertEqual(extract_section_values('左侧山体植被发育。该段边坡整体上位于线路右侧山体，坡度50~85°。')['side'],'右侧')
        values=extract_section_values('路基至边坡开挖坡口线处坡度介于80～90°，坡口线外为原始坡面，坡度40~55°。')
        self.assertEqual((values['slope_gradient_min_deg'],values['slope_gradient_max_deg']),(80,90))

    def test_table_keeps_both_body_and_mode_through_merged_cells(self):
        rows=[['位置','破坏形式','工况','稳定性系数K','稳定性判断'],
              ['A危岩','模式一','天然','2.6','稳定'],['','','暴雨','2.4','稳定'],
              ['','模式二','天然','1.3','基本稳定'],['','','暴雨','1.2','基本稳定']]
        result=extract_factor_cells(rows)
        self.assertEqual([(r['body_id'],r['failure_mode'],r['condition'],r['safety_factor']) for r in result],
                         [('A危岩','模式一','天然',2.6),('A危岩','模式一','暴雨',2.4),('A危岩','模式二','天然',1.3),('A危岩','模式二','暴雨',1.2)])
        rows.append(['B危岩','','天然','1.5','稳定'])
        self.assertNotIn('failure_mode',extract_factor_cells(rows)[-1])

    def test_rock_body_factor_is_not_an_overall_slope_factor(self):
        rows=extract_stability_scenarios('坠落式危岩体计算。天然状态下稳定系数为1.23，危岩体处于基本稳定；饱水状态下稳定系数为1.13，危岩体处于欠稳定状态。')
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['analysis_scope']=='危岩体' for r in rows))

    def test_joint_heading_shares_geometry_but_not_individual_orientation(self):
        a,b='K1+000-K1+100','K2+000-K2+100'
        text=f'3.4.1 {a}、{b}崩塌。两段边坡高26~28m。{a}段岩体产状151°∠40°。{b}段岩体产状160°∠38°。'
        segments,_=station_section_segments(text,{a,b},None,{a,b})
        actual=dict(segments)
        self.assertIn('26~28m',actual[a])
        self.assertIn('26~28m',actual[b])
        self.assertNotIn('160°',actual[a])
        self.assertNotIn('151°',actual[b])

    def test_figure_caption_is_not_a_section_boundary(self):
        a,b='K1+000-K1+100','K2+000-K2+100'
        segments,heading=station_section_segments(f'图5.2 {b} 边坡照片。坡脚有地下水。',{a,b},a,{a,b})
        self.assertIsNone(heading)
        self.assertEqual(segments[0][0],a)

    def test_unnamed_joint_list_keeps_orientation_pairs(self):
        rows=extract_structural_planes('岩体产状151°∠40°，发育两组主控结构面（325°∠54°、233°∠70°）。')
        self.assertEqual([(r['dip_direction'],r['dip_angle']) for r in rows],[(151,40),(325,54),(233,70)])

    def test_merged_layered_table_does_not_swap_shallow_overall_and_force(self):
        rows=[['计算剖面','浅层稳定性系数 整体','稳定性系数 剩余下滑力',''],
              ['', '暴雨 天然','暴雨 天然（KN/m）','暴雨（KN/m）'],
              ['A-A现状边坡','0.91 1.17','1.02 132.37','198.67']]
        values=extract_factor_cells(rows)
        self.assertEqual([(r['analysis_scope'],r['condition'],r['safety_factor']) for r in values],
                         [('浅层边坡','暴雨',.91),('整体边坡','天然',1.17),('整体边坡','暴雨',1.02)])
        rows[2][2]='1.02'
        self.assertEqual(extract_factor_cells(rows),[])
        rows[2][2]='1.02 132.37'
        rows[1][1]='天然 暴雨'
        self.assertEqual(extract_factor_cells(rows),[])
