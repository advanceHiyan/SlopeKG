"""Route-name compatibility and dispatch safety; never call a model."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from slopekg.pipeline import DemoPipeline
from slopekg.recognition_routes import normalize_recognizer, recognition_route
from slopekg.server import SlopeKGHandler
from tests.support import workspace_paths


class RecognitionRouteNamesTests(unittest.TestCase):
    def test_old_identifiers_resolve_to_named_routes_without_reviving_deep(self):
        for value, expected in (('basic', 'basic'), ('legacy', 'basic'),
                                ('knowledge', 'knowledge'), ('visual', 'visual'),
                                ('multimodal', 'visual')):
            self.assertEqual(normalize_recognizer(value), expected)
        for value in ('deep', 'semantic', '', None):
            with self.assertRaises(ValueError):
                normalize_recognizer(value)

    def test_dispatch_and_default_keep_their_original_implementations(self):
        with TemporaryDirectory() as directory:
            pipeline = DemoPipeline(workspace_paths(Path(directory)))
            with patch('slopekg.legacy_pipeline.LegacyPipeline.run', return_value={'marker': 'local'}) as basic:
                self.assertEqual(pipeline.run()['route']['id'], 'basic')
                self.assertEqual(pipeline.run(recognizer='legacy')['marker'], 'local')
                self.assertEqual(basic.call_count, 2)
            with patch('slopekg.recognition.run_visual_recognition', return_value={'marker': 'image'}) as visual:
                for value in ('visual', 'multimodal'):
                    result = pipeline.run(recognizer=value, input_pdfs=['not-read.pdf'], model_profile='not-read.json')
                    self.assertEqual(result['route']['label'], '视读 Visual')
                    self.assertEqual(result['marker'], 'image')
                self.assertEqual(visual.call_count, 2)
            with patch('slopekg.knowledge_service.run_knowledge_recognition', return_value={'marker': 'text'}) as knowledge:
                result = pipeline.run(recognizer='knowledge', input_pdfs=['not-read.pdf'], model_profile='not-read.json')
                self.assertEqual(result['route']['label'], '知联 Knowledge')
                knowledge.assert_called_once()

    def test_http_aliases_preserve_external_send_confirmation(self):
        with TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            paths.raw_dir.mkdir(parents=True)
            (paths.raw_dir / 'sample.pdf').write_bytes(b'not opened by option validation')
            profiles = paths.root / 'config/models'
            profiles.mkdir(parents=True)
            (profiles / 'test.json').write_text(json.dumps({
                'name': 'test', 'model': 'fixture', 'base_url': 'https://fixture.invalid'}), encoding='utf-8')
            with patch('slopekg.server.PATHS', paths):
                for value in ('visual', 'multimodal', 'knowledge'):
                    body = {'recognizer': value, 'input_pdfs': ['sample.pdf'], 'model_profile': 'test'}
                    options = SlopeKGHandler.pipeline_options(None, body)
                    self.assertTrue(options['prepare_only'])
                    self.assertEqual(normalize_recognizer(options['recognizer']), normalize_recognizer(value))
                    with self.assertRaises(ValueError):
                        SlopeKGHandler.pipeline_options(None, {**body, 'prepare_only': False})


if __name__ == '__main__':
    unittest.main()
