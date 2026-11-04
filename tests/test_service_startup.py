"""Launcher readiness is based on loaded code and working APIs, never just HTTP 200."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from slopekg.service_runtime import service_health
from tests.support import workspace_paths

SPEC = importlib.util.spec_from_file_location('demo_service_probe', Path(__file__).resolve().parents[1] / 'scripts/check_demo_service.py')
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class ServiceStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(PROBE, 'ROOT', self.root), patch.object(PROBE, 'source_revision', return_value='current')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.responses = {
            '/api/health': {'app':'SlopeKG','root':str(self.root),'status':'ready','runtime':{'pid':1234,'code_revision':'current'}},
            '/api/status': {'graph_available':True,'runtime':{'pid':1234,'code_revision':'old'}},
            '/api/recognition-debug/catalog': {'stages':[]}, '/api/recognition-debug/datasets': {'datasets':[]},
            '/api/recognition-debug/history': {'records':[]}, '/api/recognition-debug/history?limit=100': {'records':[]},
            '/api/pipeline/jobs/latest': {'status':'none'}, '/api/rules/jobs/latest': {'status':'none'},
        }

    def fetch(self, path):
        if path not in self.responses:raise urllib.error.HTTPError(path,404,'missing',{},None)
        return self.responses[path]

    def probe(self):return PROBE.probe(8766,fetch=self.fetch)

    def test_current_code_and_all_three_working_debug_apis_are_required(self):
        self.assertEqual(self.probe()['state'],'ready')
        del self.responses['/api/recognition-debug/datasets']
        self.assertEqual(self.probe()['state'],'degraded')

    def test_legacy_status_200_does_not_hide_missing_debug_apis(self):
        del self.responses['/api/health'];del self.responses['/api/recognition-debug/history?limit=100']
        result=self.probe();self.assertEqual(result['state'],'stale');self.assertEqual(result['pid'],1234)

    def test_loaded_code_is_checked_not_only_the_files_on_disk(self):
        self.responses['/api/health']['runtime']['code_revision']='old'
        self.assertEqual(self.probe()['state'],'stale')

    def test_active_pipeline_or_rule_job_prevents_restart(self):
        self.responses['/api/health']['status']='restart_required'
        for path in ['/api/pipeline/jobs/latest','/api/rules/jobs/latest']:
            self.responses[path]['status']='running';self.assertEqual(self.probe()['state'],'busy');self.responses[path]['status']='none'

    def test_active_dataset_test_prevents_restart(self):
        self.responses['/api/health']['status']='restart_required'
        self.responses['/api/recognition-debug/history?limit=100']['records']=[{'status':'running','owner_pid':1234}]
        self.assertEqual(self.probe()['state'],'busy')

    def test_other_workspace_or_missing_job_state_never_allows_termination(self):
        self.responses['/api/health']['root']=str(self.root/'other')
        self.assertEqual(self.probe()['state'],'unknown')
        del self.responses['/api/health'];self.responses['/api/pipeline/jobs/latest']=None
        self.assertEqual(self.probe()['state'],'unknown')

    def test_health_exposes_loaded_and_disk_identity_without_reading_model_results(self):
        with patch('slopekg.service_runtime.runtime_identity',return_value={'pid':1,'code_revision':'old'}), patch('slopekg.service_runtime.source_revision',return_value='new'):
            health=service_health(workspace_paths(self.root))
        self.assertEqual(health['status'],'restart_required');self.assertEqual(health['runtime']['code_revision'],'old')
        self.assertEqual(health['disk_code_revision'],'new');self.assertIn('debug_history',health['capabilities'])


if __name__=='__main__':unittest.main()
