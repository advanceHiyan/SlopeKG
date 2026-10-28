import asyncio,tempfile,unittest
from pathlib import Path
from tests.test_knowledge_ownership import fixture,ReviewModel
from slopekg.knowledge_pipeline import KnowledgePipeline

class OwnerDedupTests(unittest.TestCase):
    def test_existing_identical_concrete_assertion_links_topic_without_new_request(self):
        with tempfile.TemporaryDirectory() as directory:
            doc=fixture();units={u['id']:u for u in doc['units']};known={e['id']:e for e in doc['entities']}
            model=ReviewModel();pipeline=KnowledgePipeline(model,Path(directory))
            raw={'field':'pressure','value':'2.50','unit':'MPa','unit_basis':'','scope':'local sample',
                'role':'observation','qualifier':'','condition':'cold','time':'',
                'evidence':[{'span_id':'a','quote':units['a']['text']}]}
            a=pipeline._item(doc,known['A'],'fact',raw,units,{'a'},known)
            topic=pipeline._item(doc,known['T'],'fact',raw,units,{'a'},known)
            a['status']=topic['status']='verified_model';doc['facts']=[a,topic]
            asyncio.run(pipeline._reconcile_owners(doc,units,known))
            self.assertEqual(model.calls,0);self.assertEqual(len(doc['facts']),1)
            self.assertEqual(doc['topic_examples'][0]['fact_id'],a['id'])
