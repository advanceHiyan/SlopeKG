"""Preview tests use synthetic inputs and the same evaluator as final intake."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests import test_agent_assessment as fixtures
from slopekg.standard_assessment.agent_preview import preview_submission
from slopekg.standard_assessment import debug_flow
from slopekg.storage import write_json


class AgentPreviewTests(unittest.TestCase):
    setUp = fixtures.AgentAssessmentTests.setUp
    persist = fixtures.AgentAssessmentTests.persist
    def workspace(self):
        work = self.agent_root/'workspace'
        write_json(work/'manifest.json', {'documents': [{'document_id': 'doc1',
            'original_filename': self.pdf.name, 'original_source_sha256': self.digest}]})
        write_json(work/'outputs/objects.json', {'objects': self.agent_run['documents'][0]['objects']})
        (work/'outputs/facts.jsonl').write_text('\n'.join(json.dumps(f,ensure_ascii=False)
            for f in self.agent_run['documents'][0]['facts'])+'\n',encoding='utf-8')
        write_json(work/'outputs/assessment.json', self.proposal)
        return work

    def test_preview_explains_scope_block_and_matches_final_table(self):
        self.proposal['cases'][0]['inputs'][1]['scope']='其他对象，未知日期'
        work=self.workspace()
        snapshot={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (work/'outputs').iterdir()}
        with patch('slopekg.model_client.CompatibleVisionClient.complete',side_effect=AssertionError('no model')):
            before=preview_submission(self.paths,work)
        self.assertIsNone(before['cases'][0]['table17']['grade'])
        item=next(i for i in before['cases'][0]['inputs'] if i['id']=='i2')
        self.assertEqual(item['dimensions']['subject'],'incompatible')
        self.assertFalse(item['selected'])
        self.assertEqual(snapshot,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (work/'outputs').iterdir()})
        self.assertFalse(list((work/'scratch').iterdir()))
        self.assertFalse((self.paths.output_dir/'assessment-debug').exists())
        self.proposal['cases'][0]['inputs'][1]['scope']={'object_id':'s1'}
        self.workspace()
        after=preview_submission(self.paths,work)
        self.assertEqual(after['cases'][0]['table17']['grade'],'危险性中等')
        self.persist()
        final=debug_flow.prepare(self.paths,self.req)['result']
        self.assertEqual(after['cases'][0]['table17'],final['table17_result'])
        self.assertEqual(after['cases'][0]['review_status'],'pending')
        self.assertEqual(after['persisted_cases'],0)

    def test_preview_keeps_missing_conflicts_and_historical_reference(self):
        p=self.proposal['cases'][0]
        other=copy.deepcopy(p['inputs'][1]);other.update(id='i3',value={'kind':'number','value':500,'unit':'person'})
        p['inputs'].append(other)
        c=preview_submission(self.paths,self.workspace())['cases'][0]
        self.assertEqual(c['workflow_status'],'needs_resolution')
        self.assertIsNone(c['table17']['grade'])
        p['inputs'].pop();p['inputs'][0]['role']='reference'
        c=preview_submission(self.paths,self.workspace())['cases'][0]
        self.assertIsNone(c['table17']['grade'])
        self.assertEqual(c['inputs'][0]['dimensions']['role'],'incompatible')
        p['inputs'][0]['role']='judgment'
        p['inputs'][1]['scope']={'time':{'kind':'instant','start':'2000-01-01','end':'2000-01-01'}}
        c=preview_submission(self.paths,self.workspace())['cases'][0]
        self.assertIsNone(c['table17']['grade'])
        self.assertEqual(c['inputs'][1]['dimensions']['time'],'incompatible')

    def test_preview_rejects_foreign_refs_before_creating_a_database(self):
        self.proposal['cases'][0]['inputs'][0]['fact_ids']=['foreign']
        work=self.workspace()
        with self.assertRaises(ValueError):preview_submission(self.paths,work)
        self.assertFalse((work/'scratch').exists())
