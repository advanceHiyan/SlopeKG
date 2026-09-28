import itertools
import unittest

from slopekg.engineering import extract_factor_cells
from slopekg.extractors import extract_structural_planes, infer_material_nature
from slopekg.graph_builder import stable_id
from slopekg.semantic import detect_section_gaps
from slopekg.source_candidates import build_processing_audit, material_mentions, orientation_candidates


class SourceCandidateTests(unittest.TestCase):
    def test_header_order_and_numeric_mutations_do_not_change_measure_identity(self):
        columns = [('整体稳定系数', '天然', .82), ('浅层稳定性系数', '暴雨', 1.31),
                   ('剩余下滑力', '暴雨（kN/m）', .77), ('安全系数', '天然', 1.05)]
        for permutation in itertools.permutations(columns):
            for offset in (0, .17):
                with self.subTest(permutation=permutation, offset=offset):
                    rows = [['计算断面', *(c[0] for c in permutation)], ['', *(c[1] for c in permutation)],
                            ['Z-Z′', *(c[2]+offset for c in permutation)]]
                    actual = {(c['analysis_scope'], c['condition'], c['safety_factor']) for c in extract_factor_cells(rows)}
                    self.assertEqual(actual, {('整体边坡', '天然', .82+offset), ('浅层边坡', '暴雨', 1.31+offset)})

    def test_merged_parent_stops_at_unknown_quantity_and_ragged_rows_abstain(self):
        rows = [['剖面', '整体稳定性系数', '', '未知量', ''],
                ['', '天然', '暴雨', '天然', '暴雨'], ['B-B', 1.2, 1.1, .8, .9]]
        self.assertEqual([r['safety_factor'] for r in extract_factor_cells(rows)], [1.2, 1.1])
        rows[-1].pop()
        self.assertEqual(extract_factor_cells(rows), [])

    def test_bedding_prose_and_list_generalize_but_do_not_borrow_foreign_subject(self):
        for direction, angle in [(27, 19), (286.5, 63.5)]:
            text = f'边坡地层岩性为灰岩，产状{direction}°∠{angle}°，215°∠31°。'
            planes = extract_structural_planes(text)
            self.assertEqual({(p['name'],p['dip_direction'],p['dip_angle']) for p in planes},
                             {('岩层面',direction,angle), ('岩层面',215,31)})
        for text in ['灰岩。产状215°∠31°。', '对岸地层岩性为灰岩，产状215°∠31°。',
                     '地层岩性为灰岩。某个数值215°∠31°。', '地层岩性为灰岩，产状415°∠31°。']:
            self.assertEqual(extract_structural_planes(text), [])

    def test_partial_list_completeness_and_object_switch(self):
        samples = [{'text':'岩层产状27°∠19°，节理产状215°∠31°。'}]
        bedding = {'name':'岩层面', 'dip_direction':27, 'dip_angle':19}
        joint = {'name':'节理', 'dip_direction':215, 'dip_angle':31}
        self.assertIn('structural_planes', detect_section_gaps({}, samples, {'structural_planes':[bedding]}))
        self.assertNotIn('structural_planes', detect_section_gaps({'structural_planes':[joint]}, samples, {'structural_planes':[bedding]}))
        wrong_role = {**joint, 'name':'岩层面'}
        self.assertIn('structural_planes', detect_section_gaps({'structural_planes':[wrong_role]}, samples, {'structural_planes':[bedding]}))

    def test_range_endpoints_and_new_section_never_become_extra_scalar_planes(self):
        for text in ['岩层产状37~92°∠21°。', '岩层产状37°∠21~29°。']:
            self.assertEqual(extract_structural_planes(text), [])
            self.assertEqual(orientation_candidates(text)[0]['reason'], 'orientation_range_not_scalar')
        text='3.4.7 K8+000-K8+080 崩塌。地层岩性为灰岩，产状92°∠21°。'
        self.assertEqual(extract_structural_planes(text,station='K9+000-K9+080'), [])
        self.assertEqual(len(extract_structural_planes(text,station='K8+000-K8+080')), 1)
        text='岩层产状37°∠21°，主控：190°∠83°。'
        planes=extract_structural_planes(text)
        self.assertEqual([(p['dip_direction'],p['dip_angle']) for p in planes],[(37,21)])
        planes=extract_structural_planes('两组主控结构面（212.5°∠68.5°、81.3°∠74°）。')
        self.assertEqual([(p['dip_direction'],p['dip_angle']) for p in planes],[(212.5,68.5),(81.3,74)])

    def test_material_roles_preserve_cover_fill_and_sliding_bed(self):
        mentions = material_mentions('坡体为页岩。表层覆盖碎石土。裂隙由黏土充填。滑床为灰岩。')
        self.assertEqual([(m['material'],m['role']) for m in mentions],
                         [('页岩','body'), ('碎石土','cover'), ('黏土','joint_fill'), ('灰岩','sliding_bed')])

    def test_explicit_body_composition_beats_fill_cooccurrence(self):
        for text in ['坡体由砂岩、页岩组成。表层覆盖碎石土。',
                     '边坡地层岩性为灰岩，裂隙黏土充填。']:
            row={'station':'K9+000-K9+080','lithology_terms':['页岩','灰岩','碎石土','黏土'],
                 'section_text_samples':[{'station_context':'K9+000-K9+080','text':text,'page':5}]}
            infer_material_nature(row)
            self.assertEqual(row['material_nature'],'岩质')
            self.assertEqual(row['material_nature_evidence'][0]['page'],5)
        row={'station':'K9+000-K9+080','lithology_terms':['碎石土'],
             'section_text_samples':[{'station_context':'K8+000-K8+080','text':'坡体由页岩组成。'}]}
        infer_material_nature(row)
        self.assertEqual(row['material_nature'],'土质')

    def test_audit_distinguishes_unparsed_unsupported_and_lost_after_merge(self):
        station='K9+000-K9+080'; slope_id=stable_id('slope',f'G999:{station}')
        plane={'name':'岩层面','dip_direction':27,'dip_angle':19}
        extracted={'slopes':[{'route_code':'G999','station':station,'structural_planes':[plane],
                             'section_text_samples':[{'document_id':'d','page':2,'station_context':station,
                                                      'text':'岩层产状27°∠19°。节理215°∠31°。产状12°∠15°。'}]}]}
        parsed={'documents':[{'id':'d','kind':'勘察报告'}], 'tables':[
            {'id':'t1','document_id':'d','page':3,'rows':[['稳定系数','未知表头']]},
            {'id':'t2','document_id':'d','page':4,'rows':[['稳定系数']], 'review_status':'failed'}]}
        graph={'nodes':[{'id':slope_id,'type':'Slope','props':{}}], 'edges':[]}
        result=build_processing_audit(parsed,extracted,graph)
        self.assertFalse(result['is_accuracy_measurement'])
        for state in ['lost_after_extraction','not_extracted','needs_object_review','table_not_extracted','table_parse_failed']:
            self.assertEqual(result['summary'][state],1)
        graph['nodes'].append({'id':'p','type':'StructuralPlane','props':plane})
        graph['edges'].append({'source':slope_id,'target':'p','relation':'DEVELOPS_STRUCTURAL_PLANE'})
        self.assertEqual(build_processing_audit(parsed,extracted,graph)['summary']['represented'],1)


if __name__ == '__main__':
    unittest.main()
