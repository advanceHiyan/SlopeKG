"""Master identity, staging and concurrency checks. No model requests."""
from concurrent.futures import ThreadPoolExecutor
import unittest
from slopekg import slope_library as lib, slope_identity as identity
from tests import test_slope_library as lifecycle
from slopekg.agent_gateway import dump


class SlopeIdentityTests(unittest.TestCase):
    setUp = lifecycle.SlopeLibraryTests.setUp
    run_data = lifecycle.SlopeLibraryTests.run_data
    def pending(self):
        return lib.catalog(self.paths)['unassigned'][0]

    def decide(self, item, **extra):
        return lib.bind(self.paths, {'binding_id': item['id'], 'revision': item['revision'],
            'actor':'reviewer', 'reason':'original-page identity check', 'confirmed':True, **extra})

    def test_staging_does_not_create_master_and_renamed_rerun_stays_pending(self):
        run=self.run_data();lib.ingest(self.paths,run)
        self.assertEqual(lib.catalog(self.paths)['counts']['slopes'],0)
        result=self.decide(self.pending(),name='实际边坡')
        slope=result['slope']['id'];self.assertTrue(slope.startswith('slope-'))
        run['run_id']='later';run['documents'][0]['objects'][0]['name']='模型换了一个边坡名称'
        lib.ingest(self.paths,run)
        c=lib.catalog(self.paths)
        self.assertEqual(c['counts']['slopes'],1);self.assertEqual(c['counts']['unassigned'],1)
        self.assertEqual(c['slopes'][0]['id'],slope)

    def test_scoped_external_keys_and_old_key_aliases_are_unique(self):
        lib.ingest(self.paths,self.run_data());a=self.decide(self.pending(),name='一号边坡',identity={'namespace':'项目A','external_id':' A-01 '})
        aid=a['slope']['id']
        found=identity.preview(self.paths,namespace='项目a',external_id='a-01')
        self.assertEqual(found['slope_id'],aid)
        changed=identity.update(self.paths,{'slope_id':aid,'revision':1,'attributes':{'namespace':'项目A','external_id':'A-02'},
            'name':'新名称','actor':'reviewer','reason':'official renumbering','confirmed':True})
        self.assertEqual(changed['revision'],2)
        self.assertEqual(identity.preview(self.paths,namespace='项目A',external_id='A-01')['slope_id'],aid)
        lib.ingest(self.paths,self.run_data('second.pdf',run_id='r2'))
        item=self.pending()
        with self.assertRaises(identity.IdentityConflict):
            self.decide(item,name='另一个边坡',identity={'namespace':'项目A','external_id':'A-01'})
        self.assertEqual(len(lib.catalog(self.paths)['slopes']),1)
        b=self.decide(item,name='另一个边坡',identity={'namespace':'项目B','external_id':'A-01'})
        self.assertNotEqual(b['slope']['id'],aid)
        with self.assertRaises(identity.IdentityConflict):
            identity.update(self.paths,{'slope_id':aid,'revision':1,'attributes':{},'actor':'reviewer','reason':'stale','confirmed':True})

    def test_later_batch_links_same_master_without_overwriting_prior_values_or_dates(self):
        first=self.run_data();first['started_at']='2026-11-01T08:00:00Z';first['documents'][0]['document_date']='2025-01-01'
        lib.ingest(self.paths,first);a=self.decide(self.pending(),name='同一处边坡')
        second=self.run_data('second.pdf',run_id='r2');second['started_at']='2026-11-11T08:00:00Z';second['documents'][0]['document_date']='2026-10-01';second['documents'][0]['facts'][0]['value']=12
        lib.ingest(self.paths,second);item=self.pending();p=identity.preview(self.paths,binding_id=item['id'])
        self.assertEqual(p['candidates'][0]['slope_id'],a['slope']['id']);self.assertFalse(p['auto_linked'])
        result=self.decide(item,slope_id=a['slope']['id'],target_revision=1)
        versions=[v for s in result['sources'] for v in s['versions']]
        self.assertEqual({v['object']['facts'][0]['raw']['value'] for v in versions},{10,12})
        self.assertEqual({v['document_date'] for v in versions},{'2025-01-01','2026-10-01'})
        self.assertEqual(len(lib.catalog(self.paths)['slopes']),1)

    def test_side_conflict_requires_explicit_resolution_and_keeps_record(self):
        run=self.run_data();run['documents'][0]['objects'][0]['name']='G209 K1+000-K1+100 右侧边坡'
        lib.ingest(self.paths,run);slope=self.decide(self.pending(),name='右侧登记边坡',identity={'route_code':'G209','side':'右侧','start_station_m':1000,'end_station_m':1100})['slope']
        other=self.run_data('other.pdf');other['documents'][0]['objects'][0]['name']='G209 K1+000-K1+100 左侧边坡';lib.ingest(self.paths,other)
        item=self.pending();p=identity.preview(self.paths,binding_id=item['id'],slope_id=slope['id'])
        self.assertTrue(p['candidates'][0]['conflicts'])
        with self.assertRaises(identity.IdentityConflict):self.decide(item,slope_id=slope['id'],target_revision=1)
        d=self.decide(item,slope_id=slope['id'],target_revision=1,conflict_confirmed=True,conflict_reason='checked left/right transcription against original')
        self.assertTrue(next(s for s in d['sources'] if s['id']==item['id'])['history'][0]['conflict_reason'])

    def test_duplicate_creation_needs_distinction_and_source_release_keeps_evidence(self):
        lib.ingest(self.paths,self.run_data());a=self.decide(self.pending(),name='测试边坡')
        lib.ingest(self.paths,self.run_data('other.pdf'));item=self.pending()
        with self.assertRaises(identity.IdentityConflict):self.decide(item,name='测试边坡')
        result=self.decide(item,slope_id=a['slope']['id'],target_revision=1)
        binding=next(s for s in result['sources'] if s['id']==item['id'])
        released=self.decide(binding,release=True)
        self.assertIsNone(released['slope']);self.assertEqual(len(released['sources'][0]['versions']),1)
        self.assertEqual(released['sources'][0]['history'][0]['action'],'release_source_identity')

    def test_competing_confirmations_do_not_create_two_masters(self):
        lib.ingest(self.paths,self.run_data());item=self.pending()
        def confirm(_):
            try:return self.decide(item,name='同一处边坡')['slope']['id']
            except identity.IdentityConflict:return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(confirm,range(2)))
        self.assertEqual(results.count('stale'),1);self.assertEqual(len(lib.catalog(self.paths)['slopes']),1)

    def test_existing_ids_and_fields_survive_profile_registration(self):
        dump(self.paths.graph_json,{'nodes':[{'id':'legacy-id','type':'Slope','label':'G209 K1+000-K1+100 左侧边坡',
            'props':{'slope_height_raw':'10m','slope_id':'G209-AUTO-001'}}]})
        c=lib.catalog(self.paths);self.assertEqual(c['slopes'][0]['id'],'legacy-id')
        d=lib.detail(self.paths,slope_id='legacy-id');self.assertTrue(d['legacy_fields']);self.assertEqual(d['identity']['keys'],[])
        identity.update(self.paths,{'slope_id':'legacy-id','revision':0,'attributes':{'namespace':'养护单位A','external_id':'S-01'},
            'actor':'reviewer','reason':'external registry','confirmed':True})
        self.assertEqual(lib.catalog(self.paths)['slopes'][0]['id'],'legacy-id')
        self.assertTrue(lib.detail(self.paths,slope_id='legacy-id')['legacy_fields'])

    def test_validation_rejects_partial_key_and_nonfinite_station(self):
        for attrs in ({'external_id':'A01'},{'start_station_m':float('nan'),'end_station_m':20},
                      {'start_station_m':30,'end_station_m':20},{'side':'左右侧'}):
            with self.subTest(attrs=attrs),self.assertRaises(ValueError):identity.validate_attributes(attrs)

    def test_identity_changes_invalidate_previous_source_confirmation(self):
        run=self.run_data();lib.ingest(self.paths,run)
        first=self.decide(self.pending(),name='实际边坡')
        sid=first['slope']['id'];self.assertTrue(first['sources'][0]['identity_ready'])
        identity.update(self.paths,{'slope_id':sid,'revision':1,'attributes':{},'name':'新登记名称',
            'actor':'reviewer','reason':'registry corrected','confirmed':True})
        after=lib.detail(self.paths,slope_id=sid)
        self.assertFalse(after['sources'][0]['identity_ready'])
        with self.assertRaises(identity.IdentityConflict):self.decide(after['sources'][0],slope_id=sid,target_revision=1)
        confirmed=self.decide(after['sources'][0],slope_id=sid,target_revision=2)
        self.assertTrue(confirmed['sources'][0]['identity_ready'])

    def test_source_identity_change_is_not_hidden_by_old_manual_override(self):
        run=self.run_data();doc=run['documents'][0]
        doc['objects'][0]['name']='G209 K1+000-K1+100 左侧边坡'
        lib.ingest(self.paths,run)
        first=self.decide(self.pending(),name='实际边坡',identity={'side':'右侧'},
            conflict_confirmed=True,conflict_reason='original left/right corrected')
        self.assertTrue(first['sources'][0]['identity_ready'])
        run['run_id']='rerun';doc['facts'].append({**doc['facts'][0],'id':'route','field':'route_code','value':'G318'})
        lib.ingest(self.paths,run)
        self.assertFalse(lib.detail(self.paths,slope_id=first['slope']['id'])['sources'][0]['identity_ready'])

    def test_new_identity_cannot_hide_conflicting_source_without_acknowledgement(self):
        run=self.run_data();run['documents'][0]['objects'][0]['name']='G209 K1+000-K1+100 左侧边坡'
        lib.ingest(self.paths,run)
        with self.assertRaises(identity.IdentityConflict):self.decide(self.pending(),name='新边坡',identity={'side':'右侧'})
        self.assertEqual(len(lib.catalog(self.paths)['slopes']),0)

    def test_http_identity_lookup_and_conflict_status(self):
        from urllib.parse import urlparse
        from slopekg.slope_library_http import route
        paths=self.paths
        class Handler:
            command='GET'
            body={}
            def read_json_body(self):return self.body
            def write_json(self,payload,status=200):self.result,self.status=payload,status
        lib.ingest(paths,self.run_data());item=self.pending();h=Handler()
        route(h,urlparse('/api/slope-library/identity?binding_id='+item['id']),paths)
        self.assertEqual(h.status,200);self.assertFalse(h.result['auto_linked'])
        created=self.decide(item,name='测试边坡')
        h.command='POST';h.body={'operation':'bind','binding_id':item['id'],'revision':0,'slope_id':created['slope']['id'],
            'target_revision':1,'actor':'reviewer','reason':'stale click','confirmed':True}
        route(h,urlparse('/api/slope-library'),paths)
        self.assertEqual(h.status,409);self.assertEqual(h.result['error_type'],'identity_conflict')
