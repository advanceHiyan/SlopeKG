"""Continuation preserves source identity and never schedules read pages again."""
import copy,importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('validation_continuation',Path(__file__).resolve().parents[1]/'scripts/continue_recognition_validation.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def run(pages,facts):
    return {'model':{'name':'fixture'},'producer':{'version':'fixture'},'summary':{},'documents':[
        {'id':'same','sha256':'same-bytes','pages':pages,'facts':facts}]}

class ContinuationTests(unittest.TestCase):
    def test_unread_targets_and_old_readings_survive_continuation(self):
        old=run([{'page':1,'status':'partial','regions':[{'reading':{'facts':[]}}]},
            {'page':2,'status':'partial','regions':[{'reading':None}]}],[{'id':'old','status':'verified_model'}])
        new=run([{'page':2,'status':'completed','regions':[{'reading':{'facts':[]}}]}],[{'id':'new','status':'pending'}])
        self.assertEqual(module.unread_pages(old),[2])
        with patch.object(module,'consolidate',side_effect=lambda rows:rows),patch.object(module,'resolve_conflicts'):
            result=module.merge_visual(old,new)
        self.assertEqual([p['page'] for p in result['documents'][0]['pages']],[1,2])
        self.assertEqual({f['id'] for f in result['documents'][0]['facts']},{'old','new'})
        self.assertEqual(result['summary']['verified_model'],1)

    def test_different_source_bytes_cannot_be_merged(self):
        old=run([{'page':1,'status':'partial','regions':[]}],[]);new=copy.deepcopy(old)
        new['documents'][0]['sha256']='changed'
        with self.assertRaises(ValueError):module.merge_visual(old,new)

    def test_previously_read_page_cannot_be_silently_rescheduled(self):
        old=run([{'page':1,'status':'partial','regions':[{'reading':{'facts':[]}}]}],[])
        with self.assertRaises(ValueError):module.merge_visual(old,copy.deepcopy(old))
