"""Exercise workspace edits against isolated PDFs, facts and assessment history."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import fitz

from slopekg.standard_assessment.contract import CONTRACT_VERSION, VersionConflict, enumeration, fact_record, identity, number
from slopekg.standard_assessment.service import StandardAssessmentService
from slopekg.standard_assessment.workspace_cases import compose_case
from slopekg.standard_assessment.workspace_review import save_evidence
from tests.support import workspace_paths


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceEvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp_root = ROOT / '.tmp'
        tmp_root.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='workspace-evidence-', dir=tmp_root)
        assert Path(temp.name).resolve().is_relative_to(tmp_root.resolve())
        self.addCleanup(temp.cleanup)
        self.paths = workspace_paths(Path(temp.name))
        self.paths.raw_dir.mkdir(parents=True)
        rules = self.paths.root / 'data/standards/GBT40112-2021/landslide_current.review.json'
        rules.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / rules.relative_to(self.paths.root), rules)
        self.api = StandardAssessmentService(self.paths, database=self.paths.output_dir / 'workspace.sqlite')
        self.scope = {
            'subject': {'source_object_ref': 'anonymous-report:body', 'object_kind': 'landslide_body'},
            'time': {'kind': 'instant', 'start': '2026-11-09', 'end': '2026-11-09'},
            'scenario': {'code': 'natural', 'raw': '天然工况'},
        }
        self.slope = {'id': 'test-slope', 'label': '软件测试滑坡'}
        self.rows = [
            ('development', 'development', enumeration('development', '弱发育'), '发育程度专业结论：弱发育。'),
            ('count', 'threatened_people', number(120, 'person'), '本期受威胁人数：8人。'),
            ('other', 'observations', None, '本期复查受威胁人数：80人。'),
            ('deaths', 'observations', None, '调查死亡人数：8人。'),
            ('past', 'observations', None, '前期受威胁人数：18人。'),
            ('loss', 'observations', None, '本期可能直接经济损失：800,000元。'),
            ('interval', 'observations', None, '本期受威胁人数：18至20人。'),
            ('approx', 'observations', None, '本期受威胁人数约18人。'),
            ('full_quantity', 'observations', None, '本期另一处文字显示受威胁人数：127人。'),
            ('cropped_quantity', 'threatened_people', None, '27人'),
            ('unsupported_quote', 'observations', None, '本期受威胁人数：7人。'),
        ]
        self.pdf = self.paths.raw_dir / 'evidence-test.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page()
            lines = ['GB/T 40112-2021 软件测试资料，2026-11-09，天然工况。']
            lines.extend(quote for label, _, _, quote in self.rows if label not in {'unsupported_quote', 'cropped_quantity'})
            for line, text in enumerate(lines):
                page.insert_text((40, 60 + 24 * line), text, fontname='china-s', fontsize=10)
            pdf.save(self.pdf)
        self.source = self.source_bundle(self.pdf)
        self.initial = compose_case(self.api, self.slope, [self.source])
        self.case_id = self.initial['case']['case_id']

    def source_bundle(self, path, *, selected_rows=None, case_id='original-case'):
        digest = sha256(path.read_bytes()).hexdigest()
        source_path = path.relative_to(self.paths.root).as_posix()
        contexts, records, facts = [], [], []
        for label, variable, value, quote in selected_rows or self.rows:
            context_id, source_id = f'{case_id}-{label}', f'{case_id}-{label}-record'
            contexts.append({'context_id': context_id, 'source_path': source_path, 'source_sha256': digest,
                             'spans': [{'file_page_1_based': 1, 'quote': quote}]})
            records.append({'record_ref_id': source_id, 'source_path': source_path,
                            'snapshot_sha256': digest, 'original_record': {'quote': quote, 'value': value}})
            scope = deepcopy(self.scope)
            if label == 'past':
                scope['time'].update(start='2026-11-08', end='2026-11-08')
            if value is None and variable == 'observations':
                value = {'kind': 'text', 'text': quote}
            facts.append(fact_record(variable, value, scope,
                                     source_refs=[source_id], evidence_refs=[context_id], origin='knowledge',
                                     feature=label, source_standard='GB/T 40112-2021', support='literal_matched'))
        return {'workspace': 'debug', 'link': {'link_id': case_id + '-confirmed'}, 'contexts': contexts,
                'source_records': records, 'facts': facts,
                'case': {'case_id': case_id, 'target_ref': self.scope['subject']['source_object_ref'],
                         'resolution_version': 1, 'source_hashes': {source_path: digest},
                         'hazard_type_supported': True, 'scope': deepcopy(self.scope)}}

    def request(self, **changes):
        return {'case_id': self.case_id, 'expected_resolution_version': 1, 'actor': '隔离测试操作者',
                'reason': '核对软件测试原页', 'variable': 'threatened_people', 'value': number(8, 'person'),
                'evidence_id': 'original-case-count', 'evidence_span_index': 0, 'source_page_reviewed': True, **changes}

    @staticmethod
    def current(bundle):
        return next(result for result in bundle['results'] if result['is_current'])

    def original_count(self):
        return next(fact for fact in self.initial['facts'] if fact['variable_code'] == 'threatened_people')

    def test_correction_changes_grade_and_preserves_old_version_and_fact(self):
        old = self.original_count()
        request = self.request(fact_id=old['fact_id'])
        revised = save_evidence(self.api, request, correct=True)
        result = self.current(revised)
        self.assertEqual(result['table17_result']['grade'], '危险性小')
        self.assertEqual(result['review_status'], 'pending')
        self.assertEqual([r['table17_result']['grade'] for r in revised['results']], ['危险性中等', '危险性小'])
        self.assertEqual(self.api.store.get('fact_assertion', old['fact_id']), old)
        receipt = revised['saved_change']
        self.assertEqual(receipt['previous_value']['value'], 120)
        self.assertEqual(receipt['evidence']['file_page_1_based'], 1)
        self.assertEqual(receipt['evidence']['page_check'], 'native_text_matched')
        self.assertFalse(receipt['professional_acceptance'])
        self.assertEqual(revised['reviews'][-1], receipt)
        active = next(f for f in revised['facts'] if f['fact_id'] == receipt['replacement_fact_id'])
        self.assertEqual(active['value']['value'], 8)
        self.assertEqual(active['supersedes_fact_id'], old['fact_id'])
        with self.assertRaises(VersionConflict):
            save_evidence(self.api, request, correct=True)
        with self.assertRaisesRegex(ValueError, '当前版本'):
            save_evidence(self.api, {**request, 'expected_resolution_version': 2}, correct=True)

    def test_supplement_keeps_contradictory_observation_visible(self):
        self.api.review_assessment({'contract_version': CONTRACT_VERSION, 'case_id': self.case_id,
                                    'expected_resolution_version': 1, 'decision': 'select_branch',
                                    'target_id': self.original_count()['fact_id'], 'actor_kind': 'human',
                                    'reviewer_ref': '测试操作者', 'reason': '保留当时来源选择'})
        updated = save_evidence(self.api, self.request(expected_resolution_version=2, value=number(80, 'person'),
                                                     evidence_id='original-case-other'))
        self.assertEqual(updated['case']['resolution_version'], 3)
        self.assertNotIn('threatened_people', updated['case']['selections'])
        self.assertEqual(self.current(updated)['workflow_status'], 'needs_resolution')
        self.assertIsNone(self.current(updated)['table17_result']['grade'])
        counts = [f['value']['value'] for f in updated['facts'] if f['variable_code'] == 'threatened_people' and f['value']]
        self.assertEqual(set(counts), {80, 120})

    def test_corrected_count_agrees_with_unqualified_agent_count(self):
        source = self.source_bundle(self.pdf, case_id='optional-qualifier', selected_rows=[
            self.rows[0], ('count', 'threatened_people', {'kind': 'number', 'value': 8, 'unit': 'person'},
                           '本期受威胁人数：8人。'),
        ])
        original = source['facts'][1]
        original['origin_kind'] = 'agent'
        original['source_check']['content'] = 'model_supported'
        original['fact_id'] = identity('test_fact_', {k: v for k, v in original.items() if k != 'fact_id'})
        initial = compose_case(self.api, self.slope, [source])
        case_id = initial['case']['case_id']
        self.assertEqual(self.current(initial)['table17_result']['grade'], '危险性小')
        wrong = save_evidence(self.api, self.request(case_id=case_id, mode='field_observation',
                              evidence_id=None, value=number(80, 'person')))
        self.assertEqual(self.current(wrong)['workflow_status'], 'needs_resolution')
        corrected = save_evidence(self.api, self.request(case_id=case_id, expected_resolution_version=2,
                                  fact_id=wrong['saved_change']['replacement_fact_id'],
                                  evidence_id='optional-qualifier-count'), correct=True)
        result = self.current(corrected)
        self.assertEqual(result['table17_result']['grade'], '危险性小')
        self.assertEqual(result['resolved_variables']['threatened_people']['selected_fact_id'],
                         corrected['saved_change']['replacement_fact_id'])
        self.assertFalse(result['conflicts'])
        self.assertTrue(any(f['origin_kind'] == 'agent' and 'qualifier' not in f['value']
                            for f in corrected['facts'] if f['variable_code'] == 'threatened_people'))
        evaluated = self.api.evaluate_assessment({'contract_version': CONTRACT_VERSION, 'case_id': case_id,
                                                  'expected_resolution_version': 3})
        current = [r['result_id'] for r in self.api.get_case(case_id)['results'] if r['is_current']]
        self.assertEqual(current, [evaluated['result_id']])

    def test_refuses_wrong_metric_partial_number_unmatched_scope_and_unverified_quote(self):
        checks = [
            (dict(evidence_id='original-case-other'), '未明确支持'),
            (dict(evidence_id='original-case-deaths'), '未明确支持'),
            (dict(evidence_id='original-case-past', value=number(18, 'person')), '同期'),
            (dict(evidence_id='original-case-unsupported_quote', value=number(7, 'person')), '文字层不一致'),
            (dict(evidence_id='original-case-cropped_quantity', value=number(27, 'person')), '文字层不一致'),
            (dict(source_page_reviewed=False), '确认已核对'),
            (dict(evidence_span_index=True), '原文片段不存在'),
            (dict(value=number(8, '10k_CNY')), 'wrong canonical unit'),
            (dict(evidence_id='original-case-interval', value=number(20, 'person')), '未明确支持'),
            (dict(evidence_id='original-case-approx', value=number(18, 'person')), '未明确支持'),
        ]
        before = self.api.store.export()
        for payload, message in checks:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, message):
                save_evidence(self.api, self.request(**payload))
        self.assertEqual(self.api.store.export(), before)

    def test_quote_proof_preserves_reported_units_and_number_precision(self):
        added = save_evidence(self.api, self.request(variable='potential_loss_wan', value=number(80, '10k_CNY'),
                                                   evidence_id='original-case-loss'))
        receipt = added['saved_change']
        self.assertEqual(receipt['replacement_value']['value'], 80)
        self.assertIn('800,000元', receipt['evidence']['quote'])
        self.assertEqual(self.current(added)['partials']['table15']['indicator_results'][-1]['variable'], 'potential_loss_wan')
        added = save_evidence(self.api, self.request(expected_resolution_version=2, value=number(18, 'person', 'approximate'),
                                                   evidence_id='original-case-approx'))
        self.assertEqual(added['saved_change']['replacement_value']['qualifier'], 'approximate')

    def test_manual_field_observation_has_explicit_origin_and_no_pdf_claim(self):
        result = save_evidence(self.api, self.request(mode='field_observation', evidence_id=None,
                                                    variable='observations', value={'kind': 'text', 'text': '本期现场观察记录。'}))
        receipt = result['saved_change']
        self.assertEqual(receipt['source_mode'], 'field_observation')
        self.assertIsNone(receipt['evidence'])
        fact = next(row for row in result['facts'] if row['fact_id'] == receipt['replacement_fact_id'])
        self.assertEqual(fact['origin_kind'], 'manual')
        self.assertEqual(fact['source_check']['locator'], 'unlocated')
        self.assertEqual(fact['basis_status'], 'conditional')
        self.assertEqual(fact['scope'], result['case']['scope'])

    def test_hash_and_recalculation_failure_leave_database_untouched(self):
        before = self.api.store.export()
        request = self.request(fact_id=self.original_count()['fact_id'])
        with patch.object(self.api, 'evaluate_assessment', side_effect=RuntimeError('recalculation failed')):
            with self.assertRaisesRegex(RuntimeError, 'recalculation failed'):
                save_evidence(self.api, request, correct=True)
        self.assertEqual(self.api.store.export(), before)
        with self.pdf.open('ab') as stream:
            stream.write(b'changed after snapshot')
        with self.assertRaises(OSError):
            save_evidence(self.api, request, correct=True)
        self.assertEqual(self.api.store.export(), before)

    def test_scan_correction_records_visual_check_as_conditional(self):
        scan = self.paths.raw_dir / 'scanned-test.pdf'
        with fitz.open(self.pdf) as text_pdf, fitz.open() as scanned_pdf:
            page = scanned_pdf.new_page()
            page.insert_image(page.rect, pixmap=text_pdf[0].get_pixmap())
            scanned_pdf.save(scan)
        source = self.source_bundle(scan, selected_rows=self.rows[:2], case_id='scanned-case')
        composed = compose_case(self.api, self.slope, [source])
        target = next(f for f in composed['facts'] if f['variable_code'] == 'threatened_people')
        saved = save_evidence(self.api, self.request(case_id=composed['case']['case_id'], evidence_id='scanned-case-count',
                                                  fact_id=target['fact_id']), correct=True)
        self.assertEqual(saved['saved_change']['evidence']['page_check'], 'human_visual_review')
        self.assertEqual(self.current(saved)['basis_status'], 'conditional')
        self.assertEqual(self.current(saved)['table17_result']['grade'], '危险性小')


if __name__ == '__main__':
    unittest.main()
