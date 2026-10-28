"""Source structure proposes ownership; only fresh semantic review promotes it."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from slopekg.knowledge_ownership import explicit_section_owners,owner_proposals
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.visual_evidence import literal_supported
from slopekg import knowledge_prompts as prompts

def fixture():
    entities=[{'id':key,'name':name,'kind':kind,'aliases':[],'context':'','description':name,'unit_ids':[]}
        for key,name,kind in [('A','Reactor A','entity'),('B','Reactor B','entity'),('T','Pressure monitoring','concept')]]
    units=[{'id':key,'page':page,'text':text,'bbox':[0,0,1,1]} for key,page,text in [
        ('hA',1,'1.1 Reactor A'),('a',2,'Pressure 2.50 MPa under cold condition.'),
        ('hB',3,'1.2 Reactor B'),('b',3,'Pressure 3.50 MPa under hot condition.')]]
    return {'id':'D','source_sha256':'same-source','entities':entities,'units':units,'facts':[]}

class ReviewModel:
    def __init__(self,decision='supported'):self.decision=decision;self.calls=0
    def identity(self):return {'name':'offline-owner-test','model':'fixture'}
    def complete(self,*,system,prompt,images):
        self.calls+=1;instruction,raw=prompt.split('\n输入：',1)
        assert instruction==prompts.OWNERSHIP_VERIFY
        context=json.loads(raw)
        return {'verdicts':[{'id':i['id'],'decision':self.decision,'source_supported':True,
            'identity_supported':True,'meaning_supported':True,'scope_supported':True,
            'qualifiers_supported':True,'reason':'source fixture'} for i in context['items']]},{'model':'fixture'}

class OwnershipTests(unittest.TestCase):
    def test_explicit_names_and_section_boundaries_find_distinct_subjects(self):
        doc=fixture();scopes=explicit_section_owners(doc)
        self.assertEqual(scopes['a']['owner_id'],'A');self.assertEqual(scopes['b']['owner_id'],'B')
        self.assertEqual(scopes['a']['end_span_id'],'hB')

    def test_shared_alias_and_numeric_units_do_not_create_an_owner_guess(self):
        doc=fixture();doc['entities'][0]['aliases']=['R'];doc['entities'][1]['aliases']=['R']
        doc['units'][0]['text']='1.1 R'
        self.assertIsNone(explicit_section_owners(doc)['a'])
        doc=fixture();doc['units'].insert(1,{'id':'quantity','page':1,'text':'2.50 MPa','bbox':[0,0,1,1]})
        self.assertEqual(explicit_section_owners(doc)['a']['owner_id'],'A')

    def test_new_owner_needs_fresh_review_and_preserves_local_scope(self):
        for decision in ['supported','uncertain']:
            with self.subTest(decision=decision),tempfile.TemporaryDirectory() as directory:
                doc=fixture();units={u['id']:u for u in doc['units']};known={e['id']:e for e in doc['entities']}
                model=ReviewModel(decision);pipeline=KnowledgePipeline(model,Path(directory))
                raw={'field':'pressure','value':'2.50','unit':'MPa','unit_basis':'','scope':'local sample',
                    'role':'observation','qualifier':'','condition':'cold','time':'',
                    'evidence':[{'span_id':'a','quote':units['a']['text']}]}
                old=pipeline._item(doc,known['T'],'fact',raw,units,{'a'},known)
                old.update(status='verified_model',review={'id':old['id'],'decision':'supported'})
                doc['facts']=[old]
                asyncio.run(pipeline._reconcile_owners(doc,units,known))
                self.assertEqual(model.calls,1)
                new=next(f for f in doc['facts'] if f['entity_id']=='A')
                self.assertEqual(new['scope'],'local sample');self.assertNotEqual(new['id'],old['id'])
                if decision=='supported':
                    self.assertEqual(new['status'],'verified_model')
                    self.assertFalse(any(f['entity_id']=='T' for f in doc['facts']))
                    self.assertEqual(doc['topic_examples'][0]['topic_id'],'T')
                else:self.assertTrue(all(f['status']=='pending' for f in doc['facts']))

    def test_evidence_from_multiple_subject_sections_does_not_get_one_owner(self):
        doc=fixture();doc['units'][-1]['text']='Pressure 2.50 MPa under cold condition.'
        doc['facts']=[{'id':'old','entity_id':'T','value':'2.50','status':'verified_model','quality_issues':[],
            'evidence':[{'span_id':'a','quote':doc['units'][1]['text']},{'span_id':'b','quote':doc['units'][-1]['text']}]}]
        self.assertEqual(owner_proposals(doc,literal_supported),[])
