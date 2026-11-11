"""Registry and route integration against isolated, anonymous source cases."""
from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import fitz

from slopekg.config import PATHS
from slopekg.server import SlopeKGHandler
from slopekg.standard_assessment.contract import CONTRACT_VERSION, VersionConflict, enumeration, fact_record, number
from slopekg.standard_assessment.service import StandardAssessmentService
from slopekg.standard_assessment.workspace import AssessmentWorkspace
from slopekg.storage import write_json
from tests.support import sample_graph, workspace_paths


class AssessmentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.paths = workspace_paths(Path(directory.name))
        rules = Path('data/standards/GBT40112-2021/landslide_current.review.json')
        destination = self.paths.root / rules
        destination.parent.mkdir(parents=True)
        shutil.copyfile(PATHS.root / rules, destination)
        write_json(self.paths.graph_json, sample_graph())
        self.workspace = AssessmentWorkspace(self.paths)
        self.operator = {'actor': '测试操作者', 'reason': '匿名测试，确认范围'}
        self.a = self.workspace.add_slope({'label': '匿名滑坡A', **self.operator})['slope']['id']
        self.b = self.workspace.add_slope({'label': '匿名滑坡B', **self.operator})['slope']['id']

    def origin(self, name, variable, value, *, subject=None, archived=False, workspace='debug', scenario=None):
        self.paths.raw_dir.mkdir(exist_ok=True, parents=True)
        path = self.paths.raw_dir / (name + '.pdf')
        with fitz.open() as pdf:
            pdf.new_page().insert_text((80, 100), 'Assessment test source ' + name)
            pdf.save(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        source_path = str(path.relative_to(self.paths.root))
        scope = {'subject': {'source_object_ref': subject or name, 'object_kind': 'landslide_body'},
                 'time': {'kind': 'instant', 'start': '2026-11-09', 'end': '2026-11-09', 'raw': '调查日'},
                 'scenario': scenario or {'code': 'natural', 'raw': '天然工况'}}
        context = {'context_id': 'ctx_' + name, 'source_path': source_path, 'source_sha256': digest,
                   'spans': [{'file_page_1_based': 1, 'quote': 'Assessment test source ' + name}]}
        source = {'record_ref_id': 'src_' + name, 'source_path': source_path, 'snapshot_sha256': digest}
        fact = fact_record(variable, value, scope, source_refs=[source['record_ref_id']],
                           evidence_refs=[context['context_id']], origin='agent',
                           source_standard='GB/T 40112-2021', support='model_supported')
        case = {'case_id': name, 'target_ref': 's1' if workspace == 'main' else name, 'scope': scope,
                'source_hashes': {source_path: digest}, 'hazard_type_supported': True,
                'fact_ids': [fact['fact_id']], 'context_ids': [context['context_id']], 'selections': {},
                'resolution_version': 1, 'archived': archived}
        name_db = 'assessment-debug' if workspace == 'debug' else 'assessment-pilot'
        api = StandardAssessmentService(self.paths, database=self.paths.output_dir / name_db / 'assessment.sqlite')
        with api.store.transaction() as db:
            for table, record in [('source_record', source), ('evidence_context', context), ('fact_assertion', fact), ('assessment_case', case)]:
                api.store.put(table, record, db)
        return {'workspace': workspace, 'case_id': name, 'resolution_version': 1}, api.store.path

    def link(self, case, slope=None, **extra):
        request = {**self.operator, **case, 'slope_id': slope or self.a, 'same_object_confirmed': True}
        return self.workspace.link({**request, **extra})

    def combine(self, sources):
        return self.workspace.create_case({'slope_id': self.a, 'sources': sources, **self.operator})

    def test_links_and_history_follow_the_slope_across_requests(self):
        left, db = self.origin('development', 'development', enumeration('development', '中等发育'))
        right, _ = self.origin('exposure', 'threatened_people', number(8, 'person'))
        before = db.read_bytes()
        with self.assertRaisesRegex(ValueError, '先确认'):
            self.combine([left, right])
        a = self.link(left)
        self.assertEqual(a['link_id'], self.link(left)['link_id'])
        self.link(right)
        created = self.combine([left, right])
        case_id = created['case']['case_id']
        self.assertEqual(created['results'][-1]['table17_result']['grade'], '危险性小')
        self.assertEqual(case_id, self.combine([right, left])['case']['case_id'])
        other_request = AssessmentWorkspace(self.paths)
        detail = other_request.slope(self.a)
        self.assertEqual(len(detail['sources']), 2)
        self.assertEqual({c['case_id'] for c in detail['cases']}, {'development', 'exposure', case_id})
        self.assertEqual(db.read_bytes(), before)
        bundle = other_request.case(case_id)
        self.assertEqual(len(bundle['history_facts']), 2)
        for ctx in bundle['contexts']:
            self.assertIn('page_url', ctx['spans'][0])
            image, content_type = other_request.evidence(case_id, ctx['context_id'], 1)
            self.assertEqual(content_type, 'image/png')
            self.assertTrue(image.startswith(b'\x89PNG'))

    def test_binding_conflict_unlink_keeps_read_only_history(self):
        left, _ = self.origin('development', 'development', enumeration('development', '中等发育'), subject='s-ref')
        other, _ = self.origin('other-period', 'observations', {'kind': 'text', 'text': '巡查'}, subject='s-ref')
        self.link(left)
        with self.assertRaises(VersionConflict):
            self.link(other, self.b)
        result = self.combine([left])
        case = result['case']
        self.workspace.link({**self.operator, **left, 'slope_id': self.a}, unlink=True)
        self.link(left, self.b)
        preserved = self.workspace.case(case['case_id'])
        self.assertTrue(preserved['source_links_changed'])
        self.assertEqual(preserved['results'], result['results'])
        with self.assertRaises(VersionConflict):
            self.workspace.evaluate({'case_id': case['case_id'], 'expected_resolution_version': 1})

    def test_report_condition_is_exposed_and_cannot_be_overridden(self):
        source, _ = self.origin('report', 'threatened_people', number(8, 'person'),
                                scenario={'code': 'unspecified', 'raw': '暴雨工况'})
        self.link(source)
        item, = self.workspace.slope(self.a)['sources']
        self.assertEqual(item['scope']['scenario']['code'], 'rainstorm')
        request = {'slope_id': self.a, 'sources': [source], **self.operator}
        made = self.workspace.create_case(request)
        self.assertEqual(made['case']['scope']['scenario']['code'], 'rainstorm')
        self.assertEqual(len(made['facts']), 1)
        with self.assertRaisesRegex(ValueError, '资料已有明确评估条件'):
            self.workspace.create_case({**request, 'scenario': 'natural'})

    def test_frontend_condition_can_be_saved_and_used_to_supplement_unknown_source(self):
        source, original_db = self.origin('report', 'threatened_people', number(8, 'person'),
                                          scenario={'code': 'unspecified', 'raw': '天然工况与暴雨工况另列'})
        before = original_db.read_bytes()
        self.link(source)
        request = {'slope_id': self.a, 'sources': [source], **self.operator}
        draft = self.workspace.create_case(request)
        made = self.workspace.create_case({**request, 'scenario': 'rainstorm'})
        case = made['case']
        self.assertNotEqual(case['case_id'], draft['case']['case_id'])
        self.assertEqual(case['scope']['scenario'], {'code': 'rainstorm', 'raw': '暴雨工况', 'selected_by_operator': True})
        self.assertEqual(case['workspace_creation'], self.operator)
        self.assertEqual(made['facts'], [])
        self.assertEqual(case['workspace_fact_trace'][0]['reasons'], ['scenario_unresolved'])
        self.assertIsNone(made['results'][-1]['table17_result']['grade'])
        self.assertEqual(made['case']['case_id'], self.workspace.create_case({**request, 'scenario': 'rainstorm'})['case']['case_id'])
        added = self.workspace.edit_evidence({**self.operator, 'case_id': case['case_id'], 'expected_resolution_version': 1,
                                             'mode': 'field_observation', 'variable': 'threatened_people',
                                             'value': number(6, 'person')})
        fact, = added['facts']
        self.assertEqual(fact['scope']['scenario']['code'], 'rainstorm')
        self.assertEqual(fact['value']['value'], 6)
        self.assertEqual(original_db.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, '请选择列表中的评估条件'):
            self.workspace.create_case({**request, 'scenario': '任意字符串'})

    def test_ineligible_archive_cannot_feed_live_workbench(self):
        source, _ = self.origin('historical-demo', 'threatened_people', number(8, 'person'), archived=True)
        item, = self.workspace.catalog()['source_cases']
        self.assertTrue(item['archived'])
        self.assertIn('/web/assessment-debug.html?', item['detail_url'])
        with self.assertRaisesRegex(ValueError, '模拟档案'):
            self.link(source)

    def test_existing_graph_case_appears_without_relinking(self):
        self.origin('old-graph', 'development', enumeration('development', '中等发育'), workspace='main')
        current, = self.workspace.slope('s1')['cases']
        self.assertEqual(current['case_id'], 'old-graph')
        self.assertIn('risk.html#case=old-graph', current['detail_url'])

    def test_evidence_rejects_file_change_and_wrong_page(self):
        left, _ = self.origin('development', 'development', enumeration('development', '中等发育'))
        self.link(left)
        bundle = self.combine([left])
        cid = bundle['case']['case_id']
        with self.assertRaises(ValueError):
            self.workspace.evidence(cid, 'ctx_development', 2)
        with self.assertRaises(KeyError):
            self.workspace.evidence(cid, 'ctx_from_another_case', 1)
        (self.paths.raw_dir / 'development.pdf').write_bytes(b'changed')
        with self.assertRaises(OSError):
            self.workspace.evidence(cid, 'ctx_development', 1)
        with self.assertRaises(OSError):
            self.combine([left])

    def test_review_in_isolated_demo_is_not_professional_signoff(self):
        left, _ = self.origin('development', 'development', enumeration('development', '中等发育'))
        right, _ = self.origin('exposure', 'threatened_people', number(8, 'person'))
        self.workspace = AssessmentWorkspace(replace(self.paths, assessment_test_only=True))
        self.link(left)
        self.link(right)
        initial = self.combine([left, right])
        req = {**self.operator, 'case_id': initial['case']['case_id'], 'expected_resolution_version': 1,
               'result_id': initial['results'][-1]['result_id'], 'context_reviewed': True}
        reviewed = self.workspace.review(req)
        self.assertEqual(reviewed['results'][-1]['review_status'], 'accepted')
        self.assertFalse(reviewed['reviews'][-1]['professional_acceptance'])
        self.assertTrue(reviewed['reviews'][-1]['software_test'])
        with self.assertRaises(VersionConflict):
            self.workspace.review(req)

    def test_normal_http_entry_registers_and_reads_the_same_dossier(self):
        with patch('slopekg.server.PATHS', self.paths):
            post = self.handler('/api/slope-assessment/slopes', {'label': 'HTTP测试坡', **self.operator})
            post.do_POST()
            slope = post.write_json.call_args.args[0]['slope']
            get = self.handler('/api/slope-assessment/slope?slope_id=' + slope['id'])
            get.do_GET()
            self.assertEqual(get.write_json.call_args.args[0]['slope'], slope)

    @staticmethod
    def handler(path, body=None):
        handler = object.__new__(SlopeKGHandler)
        handler.path = path
        handler.write_json = Mock()
        handler.read_json_body = Mock(return_value=body)
        handler.request_origin_allowed = Mock(return_value=True)
        return handler
