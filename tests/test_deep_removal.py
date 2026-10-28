"""Retired text extraction must not be reachable or silently fall back."""
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import fitz

from scripts import run_pipeline, rebuild_from_sidecars
from slopekg.pipeline import DemoPipeline
from slopekg.server import SlopeKGHandler
from slopekg.storage import read_json, write_json
from tests.support import sample_graph, workspace_paths


class DeepRemovalTests(unittest.TestCase):
    def test_old_cli_flags_fail_before_pipeline_creation(self):
        for args in [['--deep'], ['--mode', 'deep'], ['--no-llm'], ['--llm-model', 'any']]:
            with self.subTest(args=args), patch('sys.argv', ['run_pipeline.py', *args]), \
                    patch('scripts.run_pipeline.DemoPipeline') as factory, redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    run_pipeline.main()
                self.assertEqual(error.exception.code, 2)
                factory.assert_not_called()

    def test_old_http_options_are_rejected(self):
        for options in [{'parse_mode': 'deep'}, {'use_llm': True}, {'use_llm': False}, {'llm_model': 'any'}]:
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, '已移除'):
                SlopeKGHandler.pipeline_options(None, options)

    def test_old_direct_call_does_not_touch_existing_results(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            write_json(paths.graph_json, sample_graph())
            original = paths.graph_json.read_bytes()
            pipeline = DemoPipeline(paths)
            with patch.object(pipeline, 'parse_documents') as parser:
                with self.assertRaisesRegex(ValueError, '已移除'):
                    pipeline.run(parse_mode='deep')
                parser.assert_not_called()
            with self.assertRaisesRegex(ValueError, '已移除'):
                pipeline.write_graph_outputs(sample_graph(), parse_mode='deep', activate=True)
            self.assertEqual(paths.graph_json.read_bytes(), original)

    def test_basic_and_cache_rebuild_never_use_text_models_or_old_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = workspace_paths(Path(directory))
            paths.raw_dir.mkdir(parents=True)
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), 'Anonymous report with enough native text for local parsing.')
            doc.save(paths.raw_dir / 'sample.pdf')
            doc.close()
            # Export templates are unchanged production templates, supplied explicitly to this isolated workspace.
            from slopekg.config import PATHS
            paths.web_dir.mkdir(parents=True)
            for name in ['standalone-report.template.html', 'risk-standalone.template.html']:
                (paths.web_dir / name).write_bytes((PATHS.web_dir / name).read_bytes())
            old = paths.extracted_dir / 'llm_candidates.jsonl'
            old.parent.mkdir(parents=True)
            old.write_text('invalid old deep cache; must never be read', encoding='utf-8')
            with patch('slopekg.llm.read_deepseek_key', side_effect=AssertionError('unexpected credential read')), \
                    patch('slopekg.model_client.CompatibleVisionClient.complete', side_effect=AssertionError('unexpected API call')):
                state = DemoPipeline(paths).run(ocr_pages=0)
                self.assertEqual(state['parse_mode'], 'basic')
                self.assertEqual(state['processing_status'], 'completed')
                with patch('scripts.rebuild_from_sidecars.PATHS', paths), \
                        patch('scripts.rebuild_from_sidecars.DemoPipeline', return_value=DemoPipeline(paths)):
                    from contextlib import redirect_stdout
                    with redirect_stdout(StringIO()):
                        rebuild_from_sidecars.main()
                self.assertEqual(read_json(paths.state_json)['parse_mode'], 'basic')
                self.assertEqual(read_json(paths.graph_json)['meta']['pipeline']['layers'],
                                 ['adaptive_pdf_parse', 'deterministic_extraction'])
            self.assertEqual(old.read_text(encoding='utf-8'), 'invalid old deep cache; must never be read')


if __name__ == '__main__':
    unittest.main()
