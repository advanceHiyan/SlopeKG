import unittest

from slopekg.engineering import extract_engineering_tables
from slopekg.extractors import extract_section_values, station_section_segments, extract_section_facts, extract_structural_planes
from slopekg.graph_builder import build_automatic_graph, stability_scenario_key
from slopekg.stability import normalize_stability_identity, eligible_whole_slope
from slopekg.schema import RELATION_TYPES


class ScopeIdentityTests(unittest.TestCase):
    def test_contents_do_not_assign_introductory_facts_to_last_station(self):
        s='K1+000-K1+100'
        blocks={('d',1):[dict(id='toc',document_id='d',page=1,bbox=[10,10,500,50],text=f'目录 5.1 {s} 段崩塌...........21')],
                ('d',2):[dict(id='intro',document_id='d',page=2,bbox=[10,10,500,50],text='区域土质边坡坡度30°，地层复杂。')],
                ('d',3):[dict(id='body',document_id='d',page=3,bbox=[10,10,500,50],text=f'5.1 {s} 段崩塌。基岩陡壁高40m，坡度60~70°。')]}
        rows=extract_section_facts({'d'},blocks,{s},{'d':{s,'K2+000-K2+100'}})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['material_nature'],'岩质')
        self.assertEqual(rows[0]['slope_gradient_min_deg'],60)
        self.assertEqual([s['page'] for s in rows[0]['section_text_samples']],[3])

    def test_maximum_height_is_not_design_lift_height_or_invented_minimum(self):
        values=extract_section_values('基岩陡壁立面呈三角形，最高处35m。设计边坡四级，每级坡高8m。')
        self.assertEqual(values['slope_height_max_m'],35)
        self.assertNotIn('slope_height_min_m',values)
        self.assertNotIn('slope_height_max_m',extract_section_values('设计边坡四级，每级坡高8m。'))
        planes=extract_structural_planes('基岩边坡边坡为逆层边坡，产状为304∠67°，层面张开。')
        self.assertEqual([(p['dip_direction'],p['dip_angle']) for p in planes],[(304,67)])
    def test_numbered_heading_after_figure_caption_and_explicit_continuation(self):
        self.assertEqual(extract_section_values('两处边坡路堑开挖均采取一坡到顶。')['slope_type'],'路堑')
        a,b='K1+000-K1+100','K2+000-K2+100'
        text=f'图3.24 赤平投影分析图 3.4.7 {b} 崩塌。{b}崩塌位于公路左侧，坡高35米。'
        rows,last=station_section_segments(text,{a,b},a,{a,b})
        self.assertEqual(last,b)
        self.assertTrue(any(owner==b and '35米' in text for owner,text in rows))
        self.assertFalse(any(owner==a and '35米' in text for owner,text in rows))
        rows,last=station_section_segments(f'{b}崩塌位于公路左侧，边坡高45m。',{a,b},a,{a,b})
        self.assertEqual(last,b)
        self.assertEqual(rows[0][0],b)
    def test_covered_bedrock_and_simulation_profile_are_not_soil_and_whole_geometry(self):
        result = extract_section_values('内侧基岩陡壁高20~70m，以白云岩为主。'
            '选取2-2’典型剖面，基岩边坡高74m，平均坡度84°，上部为碎石土覆盖层。')
        self.assertEqual(result['material_nature'], '岩质')
        self.assertEqual((result['slope_height_min_m'], result['slope_height_max_m']), (20, 70))
        self.assertNotIn('slope_gradient_max_deg', result)
        self.assertEqual(result['scoped_geometry'][0]['height_m'], [74, 74])
        self.assertEqual(result['scoped_geometry'][0]['section'], '2-2’')
        profile_only = extract_section_values('3-3′剖面边坡高61m，平均坡度72°。')
        self.assertNotIn('slope_height_max_m', profile_only)
        self.assertEqual(profile_only['scoped_geometry'][0]['height_m'], [61, 61])

    def fixture(self, heading='3.1 K1+000-K1+100 崩塌'):
        header = ['危岩编号','破坏模式','工况','危岩稳定性系数F','防治安全等级','稳定性评价']
        common = {'document_id':'d','page':1}
        return {'documents':[{'id':'d','file_name':'G555.pdf','kind':'勘察报告'}],
                'pages':[{**common,'width':1200}],
                'text_blocks':[dict(common,id='b1',bbox=[50,20,550,40],text=heading),
                               dict(common,id='b2',bbox=[50,650,550,670],text='表1 危岩稳定性计算结果表')],
                'tables':[dict(common,id='h',bbox=[50,680,550,710],rows=[header]),
                          dict(common,id='t',bbox=[650,50,1150,95],rows=[
                              ['WY99','坠落式','现状工况','1.25','二级','欠稳定'],
                              ['','','暴雨工况','1.11','','欠稳定']])]}

    def test_cross_column_header_join_preserves_subject_and_both_fragments(self):
        parsed = self.fixture()
        registry = [{'route_code':'G555','station':'K1+000-K1+100','document_id':'d'}]
        record = extract_engineering_tables(parsed,registry)[0]
        self.assertEqual(record['station'],registry[0]['station'])
        self.assertEqual([r['safety_factor'] for r in record['stability_results']],[1.25,1.11])
        self.assertTrue(all(r['body_id']=='WY99' and r['failure_mode']=='坠落式' for r in record['stability_results']))
        self.assertEqual([r['id'] for r in record['source_fragments']],['h','t'])
        # Same-shaped tables are not joined across an explicit new section.
        parsed['text_blocks'].append(dict(document_id='d',page=1,id='new',bbox=[650,20,1150,40],text='3.2 K2+000-K2+100 崩塌'))
        self.assertEqual(extract_engineering_tables(parsed,registry),[])

    def test_shared_section_stays_unresolved_in_graph_even_with_semantic_owner(self):
        parsed=self.fixture('3.1 K1+000-K1+100、K2+000-K2+100 崩塌')
        registry=[{'route_code':'G555','station':s,'document_id':'d'} for s in ['K1+000-K1+100','K2+000-K2+100']]
        records=extract_engineering_tables(parsed,registry)
        self.assertIsNone(records[0]['station'])
        self.assertEqual(len(records[0]['station_candidates']),2)
        graph=build_automatic_graph(parsed,{'slopes':registry,'engineering_records':records},[{
            'station':registry[1]['station'],'route_code':'G555','validation_status':'passed',
            'candidate':{'stability_conclusions':[dict(condition='现状工况',safety_factor=1.25,
                analysis_scope='WY99',evidence_page=1,evidence_document_id='d')]}}])
        analyses=[n for n in graph['nodes'] if n['type']=='StabilityAnalysis']
        self.assertEqual(len(analyses),2)
        self.assertTrue(all(n['props']['association_scope']=='unresolved' for n in analyses))
        self.assertFalse(any(e['relation']=='HAS_STABILITY_ANALYSIS' for e in graph['edges']))
        self.assertTrue(all(e['relation'] in RELATION_TYPES for e in graph['edges']))
        self.assertTrue(all(not n['props']['eligible_for_screening'] for n in analyses))

    def test_identity_is_not_deduplicated_by_equal_numbers_or_allowed_into_whole_screening(self):
        base=dict(condition='现状工况',safety_factor=1.2,status='欠稳定',analysis_scope='危岩体',body_id='WY01')
        self.assertNotEqual(stability_scenario_key(base),stability_scenario_key({**base,'body_id':'WY02'}))
        normalized=normalize_stability_identity(dict(analysis_scope='WY01',source_kind='坠落式'))
        self.assertEqual(normalized['body_id'],'WY01')
        self.assertEqual(normalized['failure_mode'],'坠落式')
        self.assertFalse(eligible_whole_slope(normalized))
        self.assertFalse(eligible_whole_slope({'analysis_scope':None}))
        self.assertTrue(eligible_whole_slope({'analysis_scope':'整体边坡'}))


if __name__ == '__main__': unittest.main()
