"""Exercise the public extraction/graph entry points with independent layouts."""
import unittest

from slopekg.extractors import extract_domain_candidates
from slopekg.graph_builder import build_automatic_graph


class SectionPipelineBoundaryTests(unittest.TestCase):
    a, b = 'K70+100-K70+200', 'K71+300-K71+380'

    def block(self, name, page, text, y, x=35, right=260):
        return dict(id=name, document_id='anonymous', page=page, text=text,
                    bbox=[x, y, right, y+15], reading_order=y)

    def parsed(self, blocks, tables=()):
        return dict(documents=[dict(id='anonymous', kind='勘察报告', file_name='G888.pdf')],
                    pages=[dict(document_id='anonymous', page=p, width=600) for p in [1, 2, 3]],
                    text_blocks=blocks, tables=[dict(id='inventory', document_id='anonymous', page=1,
                    bbox=[35, 20, 260, 60], rows=[['序号', '桩号', '灾害类型'], ['1', self.a, '崩塌']]), *tables])

    def factor(self, page=2, y=175):
        return dict(id='factor', document_id='anonymous', page=page, bbox=[35, y, 260, y+50],
                    rows=[['工况', 'Fs'], ['天然', '1.41']])

    def test_same_panel_sibling_does_not_pollute_slope_or_graph(self):
        parsed = self.parsed([self.block('subject', 1, f'3.2.1 {self.a} 崩塌', 100),
            self.block('real', 1, '基本情况：该段边坡存在裂缝。', 125),
            self.block('sibling', 1, '3.3 区域地形说明', 150),
            self.block('regional', 1, '区域边坡高80m。', 175)], [self.factor(page=1, y=210)])
        result = extract_domain_candidates(parsed)
        self.assertNotIn('slope_height_max_m', result['section_facts'][0])
        self.assertTrue(result['section_facts'][0]['deformation_observations'])
        self.assertIsNone(result['engineering_records'][0]['station'])
        graph = build_automatic_graph(parsed, result, [])
        self.assertFalse(any(n['props'].get('slope_height_max_m') == 80 for n in graph['nodes']))

    def test_detached_factor_and_mode_do_not_close_real_continuation(self):
        for value in ['1.37', '2.64']:
            with self.subTest(value=value):
                parsed = self.parsed([self.block('subject', 1, f'3.2.1 {self.a} 崩塌', 100),
                    self.block('real', 1, '基本情况：边坡高15m。', 125),
                    self.block('number', 1, value, 150), self.block('mode', 1, '倾倒式', 175),
                    self.block('continued', 2, '目前该段边坡存在裂缝。', 100)], [self.factor(y=150)])
                result = extract_domain_candidates(parsed)
                facts = result['section_facts'][0]
                self.assertTrue(any('存在裂缝' in v['description'] for v in facts['deformation_observations']))
                self.assertFalse(any(h['number'] == value for h in result['section_catalog']))
                self.assertEqual(result['engineering_records'][0]['station'], self.a)

    def test_table_cells_cannot_be_headings_even_in_a_single_block(self):
        parsed = self.parsed([self.block('subject', 1, f'3.2.1 {self.a} 崩塌', 100),
            self.block('cells', 1, '1.37 滑移式', 170),
            self.block('continued', 2, '目前该段边坡存在裂缝。', 100)], [self.factor(page=1, y=160)])
        result = extract_domain_candidates(parsed)
        self.assertFalse(any(h['number'] == '1.37' for h in result['section_catalog']))
        self.assertTrue(result['section_facts'][0]['deformation_observations'])

    def test_split_number_title_retains_spans_and_drives_prose_and_table(self):
        for layout in ['wrapped_blocks', 'same_line_blocks', 'newline_in_block']:
            with self.subTest(layout=layout):
                heading = [self.block('num', 2, '3.2.2', 100), self.block('title', 2, f'{self.b} 崩塌', 125)]
                if layout == 'same_line_blocks':
                    heading = [self.block('num', 2, '3.2.2', 100, right=80),
                               self.block('title', 2, f'{self.b} 崩塌', 100, x=85)]
                elif layout == 'newline_in_block':
                    heading = [self.block('wrapped', 2, f'3.2.2\n{self.b} 崩塌', 100)]
                parsed = self.parsed([self.block('old', 1, f'3.2.1 {self.a} 崩塌', 100), *heading,
                    self.block('height', 2, '基本情况：边坡高32m。', 150),
                    self.block('support', 3, f'图3.8 {self.b} 崩塌全貌', 100)], [self.factor()])
                result = extract_domain_candidates(parsed)
                self.assertEqual({r['station'] for r in result['registry']}, {self.a, self.b})
                catalog = next(h for h in result['section_catalog'] if h['stations'] == [self.b])
                self.assertEqual(set(catalog['evidence_block_ids']), {b['id'] for b in heading})
                facts = next(s for s in result['section_facts'] if s['station'] == self.b)
                self.assertEqual(facts['slope_height_max_m'], 32)
                record = result['engineering_records'][0]
                self.assertEqual(record['station'], self.b)
                self.assertEqual(record['section_catalog_id'], catalog['id'])
                self.assertEqual(set(record['section_heading_block_ids']), {b['id'] for b in heading})
                graph = build_automatic_graph(parsed, result, [])
                slope = next(n for n in graph['nodes'] if n['type'] == 'Slope' and self.b in n['label'])
                self.assertTrue(any(e['source'] == slope['id'] and e['relation'] == 'HAS_STABILITY_ANALYSIS' for e in graph['edges']))

    def test_unconfirmed_wrapped_subject_blocks_previous_owner(self):
        parsed = self.parsed([self.block('old', 1, f'3.2.1 {self.a} 崩塌', 100),
            self.block('num', 2, '3.2.2', 100), self.block('title', 2, f'{self.b} 崩塌', 125),
            self.block('height', 2, '基本情况：边坡高32m。', 150)], [self.factor()])
        result = extract_domain_candidates(parsed)
        self.assertEqual([r['station'] for r in result['registry']], [self.a])
        self.assertFalse(any(r.get('slope_height_max_m') == 32 for r in result['section_facts']))
        self.assertIsNone(result['engineering_records'][0]['station'])

    def test_detached_numbers_cannot_jump_columns_pages_or_intervening_text(self):
        for kind in ['other_column', 'other_page', 'intervening_text', 'table_number']:
            with self.subTest(kind=kind):
                extra = [self.block('num', 2, '3.2.2', 100), self.block('title', 2, f'{self.b} 崩塌', 125)]
                tables = []
                if kind == 'other_column':
                    extra[1]['bbox'] = [350, 125, 500, 140]
                elif kind == 'other_page':
                    extra[1]['page'] = 3
                elif kind == 'intervening_text':
                    extra.insert(1, self.block('unrelated', 2, '另一段普通文字。', 117))
                else:
                    tables = [self.factor(y=90)]
                parsed = self.parsed([self.block('old', 1, f'3.2.1 {self.a} 崩塌', 100), *extra,
                    self.block('support', 3, f'图3.8 {self.b} 崩塌全貌', 200)], tables)
                result = extract_domain_candidates(parsed)
                self.assertNotIn(self.b, {r['station'] for r in result['registry']})

    def test_page_width_and_reading_order_match_for_prose_and_tables(self):
        factor = self.factor(page=1, y=175)
        factor['bbox'] = [335, 175, 560, 220]
        parsed = self.parsed([self.block('left', 1, f'3.2.1 {self.a} 崩塌', 100),
            self.block('left_body', 1, '基本情况：边坡高15m。', 150),
            self.block('right', 1, f'3.2.2 {self.b} 崩塌', 100, x=335, right=560),
            self.block('right_body', 1, '基本情况：边坡高32m。', 125, x=335, right=560),
            self.block('support', 2, f'图3.8 {self.b} 崩塌全貌', 100, x=335, right=560)], [factor])
        result = extract_domain_candidates(parsed)
        facts = {s['station']: s for s in result['section_facts']}
        self.assertEqual(facts[self.a]['slope_height_max_m'], 15)
        self.assertEqual(facts[self.b]['slope_height_max_m'], 32)
        self.assertEqual(result['engineering_records'][0]['station'], self.b)


if __name__ == '__main__':
    unittest.main()
