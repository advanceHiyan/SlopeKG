import unittest

from slopekg.engineering import extract_factor_cells, stitch_factor_tables, condition
from slopekg.extractors import extract_section_values, extract_structural_planes
from slopekg.graph_builder import slope_props, range_text


class FocusedExtractionGuards(unittest.TestCase):
    def test_maximum_and_local_angles_do_not_become_whole_ranges(self):
        v=extract_section_values('最大坡高约29m，局部下边坡坡度51-62°，上边坡坡度约77-86°。'
                                 'P 坡面 202 86')
        self.assertNotIn('slope_height_min_m',v)
        self.assertEqual(v['slope_height_max_m'],29)
        self.assertEqual(v['slope_gradient_min_deg'],86)
        v['station']='K1+000-K1+100'
        p=slope_props(v,{'slope_height_min_m':29},'G555',1,1000,1100)
        self.assertIsNone(p['slope_height_min_m'])
        v['slope_height_min_m']=20
        self.assertEqual(slope_props(v,{},'G555',1,1000,1100)['slope_height_min_m'],20)
        self.assertEqual(range_text(None,29),'≤29m')
        self.assertEqual(range_text(0,29),'0-29m')

    def test_general_range_beats_single_face_and_two_faces_are_not_averaged(self):
        v=extract_section_values('坡度45～65°。 P 坡面 240 65')
        self.assertEqual((v['slope_gradient_min_deg'],v['slope_gradient_max_deg']),(45,65))
        v=extract_section_values('P 坡面 240 65 P 坡面 180 80')
        self.assertNotIn('slope_gradient_min_deg',v)
        v=extract_section_values('斜坡倾角61°~79°，坡表存在危岩。P 坡面 121 79')
        self.assertEqual((v['slope_gradient_min_deg'],v['slope_gradient_max_deg']),(61,79))

    def test_design_lift_and_whole_disaster_geometry_are_separate(self):
        v=extract_section_values('此段设计桩号K4+010～K4+110，一级薄层开挖，高2~9m。')
        self.assertNotIn('slope_height_max_m',v)
        self.assertEqual(v['scoped_geometry'][0]['scope'],'设计开挖')
        self.assertEqual(v['scoped_geometry'][0]['height_m'],[2,9])
        v=extract_section_values('设计边坡4级，每级坡高7m。滑坡前后缘高差23m。')
        self.assertEqual(v['slope_height_max_m'],23)
        self.assertEqual(v['scoped_geometry'][0]['scope'],'设计单级')

    def test_body_material_is_not_cover_or_foreign_summary(self):
        v=extract_section_values('坡表基岩大量裸露，岩性为二叠系灰岩。顶部碎石土覆盖，层面粘土充填。')
        self.assertEqual(v['material_nature'],'岩质')
        v=extract_section_values('属浅层小型土质层滑坡。滑床为灰岩，碎石母岩为砂岩。')
        self.assertEqual(v['material_nature'],'土质')
        v=extract_section_values('岩质边坡。土质边坡支护结构坡顶最大水平位移不得超限。')
        self.assertEqual(v['material_nature'],'岩质')
        v=extract_section_values('该段边坡地质年代为寒武系，岩性为白云岩。'
                                'K2+000-K2+100属浅层小型土质层滑坡。',station='K1+000-K1+100')
        self.assertEqual(v['material_nature'],'岩质')

    def test_named_orientation_pairs_not_unrelated_numbers(self):
        p=extract_structural_planes('岩层呈单斜产出，倾向307°，倾角83°。J1：:322∠46°。'
                                    '贯通裂隙产状为190°∠44°。倾向100°，倾角20°。')
        self.assertEqual({(v['dip_direction'],v['dip_angle']) for v in p},{(307,83),(322,46),(190,44)})

    def test_split_grid_columns_follow_header_spans(self):
        rows=[['项目','','FS','','破坏形式'],['','天然','','1.18','滑移式'],['','饱和','','1.03','']]
        self.assertEqual([r['safety_factor'] for r in extract_factor_cells(rows)],[1.18,1.03])
        bad=[rows[0],['天然','饱和','','1.18','滑移式']]
        self.assertEqual(extract_factor_cells(bad),[])
        self.assertEqual(extract_factor_cells([['工况','安全系数'],['天然','1.2']]),[])

    def test_blank_section_header_requires_explicit_section_label(self):
        header=['','工况','稳定性系数','安全系数']
        rows=[header,["Ⅲ-Ⅲ'断面",'天然','1.08','1.15'],['','暴雨','1.01','1.1']]
        self.assertTrue(all(r['section']=="Ⅲ-Ⅲ'断面" for r in extract_factor_cells(rows)))
        rows[1][0]='1'
        self.assertTrue(all('section' not in r for r in extract_factor_cells(rows)))

    def test_cross_page_continuation_requires_layout_and_no_new_caption(self):
        h=['破坏模式','工况','危岩稳定性系数F','防治安全等级','稳定性评价']
        tables=[dict(id='a',document_id='d',page=1,bbox=[650,680,1050,750],rows=[h,['坠落式','现状工况','1.4','一级','基本稳定']]),
                dict(id='b',document_id='d',page=2,bbox=[100,80,500,120],rows=[['滑移式','现在工况','1.22','','基本稳定'],['','暴雨工况','1.08','','欠稳定']])]
        order=lambda x:(x['page'],int(x['bbox'][0]>600),x['bbox'][1])
        r=stitch_factor_tables(tables,order,[])[1]
        self.assertEqual([v['safety_factor'] for v in extract_factor_cells(r['rows'])],[1.22,1.08])
        self.assertEqual(condition('现在工况'),'现状')
        for text in ['5.2 K2+000-K2+100 崩塌','表5-2 新表']:
            block=dict(page=2,bbox=[100,30,500,50],text=text)
            r=stitch_factor_tables(tables,order,[block])[1]
            self.assertNotIn('source_fragments',r)
        tables[1]['bbox'][2]=700
        self.assertNotIn('source_fragments',stitch_factor_tables(tables,order,[])[1])


if __name__=='__main__':unittest.main()
