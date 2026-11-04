"""Freeze and replay one explicitly open development document; never use a network model.

This verifies that debug instrumentation preserves cached recognition predictions.
It is not a new model evaluation or an independent accuracy measurement.
"""
import argparse
import copy
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/20261102_识别调试工作台验证'
if '--output-dir' in sys.argv:
    OUT = ROOT / sys.argv[sys.argv.index('--output-dir') + 1]
sys.path.insert(0, str(ROOT / 'src'))
if len(sys.argv) > 1 and sys.argv[1] == 'before' and (OUT / 'before/source/src').is_dir():
    sys.path.insert(0, str(OUT / 'before/source/src'))
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.storage import read_json, write_json

OPEN = ROOT / 'outputs/20261101_知联目录容量修复/after/G359/knowledge'


class CacheOnly:
    def __init__(self, identity):
        self._identity = identity

    def identity(self):
        return self._identity

    def complete(self, **kwargs):
        raise AssertionError('Network/model access is forbidden in this regression')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['before', 'after', 'after-final', 'final', 'compare'])
    parser.add_argument('--output-dir')
    phase = parser.parse_args().phase
    if phase == 'compare':
        compare_saved()
        return
    dest = OUT / phase
    if (dest / 'verification.json').exists():
        raise SystemExit('Preserve existing frozen evidence; phase already exists')
    resume = (dest / 'freeze.json').is_file()
    dest.mkdir(parents=True, exist_ok=True)
    frozen = {}
    for directory in ['src/slopekg', 'web', 'config/models']:
        for path in (ROOT / directory).rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.js', '.html', '.css', '.json'}:
                relative = path.relative_to(ROOT)
                frozen[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
                target = dest / 'source' / relative
                if not resume:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
    run = read_json(OPEN / 'run.json')
    assert len(run['documents']) == 1
    doc = run['documents'][0]
    source = Path(doc['path'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == doc['source_sha256']
    target = dest / 'knowledge'
    shutil.copytree(OPEN / 'cache', target / 'cache', dirs_exist_ok=True)
    shutil.copytree(OPEN / 'source-cache', target / 'source-cache', dirs_exist_ok=True)
    known = {f.name for f in fields(KnowledgeSettings)}
    settings = KnowledgeSettings(**{**{k: v for k, v in run['settings'].items() if k in known},
                                    'allow_model_calls': False, 'record_model_inputs': True})
    if not resume:
        write_json(dest / 'freeze.json', {'source_files': frozen, 'source_sha256': doc['source_sha256'],
        'source_run_sha256': hashlib.sha256((OPEN / 'run.json').read_bytes()).hexdigest(),
        'settings': settings.__dict__, 'model': run['model'], 'new_model_requests': 0,
            'kind': 'Fixed open G359 cached whole-input replay; not a new model or hidden evaluation'})
    # No OCR inference or model downloads: use the original saved OCR evidence.
    from unittest.mock import patch
    with patch('slopekg.ocr.OcrRunner.run', side_effect=AssertionError('OCR inference forbidden')):
        result = KnowledgePipeline(CacheOnly(run['model']), target, settings=settings,
            root=ROOT, parsed_dir=ROOT / 'output/demo/parsed').run([source])
    # Volatile producer/accounting/input-capture metadata is deliberately excluded.
    predictions = [{k: d.get(k) for k in ['source_sha256', 'pages', 'units', 'chunks', 'entities',
        'references', 'facts', 'relations', 'coverage', 'errors', 'discovery_batches',
        'attribution_batches', 'synthesis_jobs', 'review_jobs', 'recovery_jobs']} for d in result['documents']]
    write_json(dest / 'predictions.json', predictions)
    summary = {'phase': phase, 'new_model_requests': 0, 'summary': result['summary'],
               'processing_status': result['processing_status']}
    if phase.startswith('after') or phase == 'final':
        summary.update(compare_saved(write=False, after_phase=phase))
        assert summary['prediction_records_unchanged'], 'Debug instrumentation changed fixed cached prediction records'
    write_json(dest / 'verification.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


def compare_saved(write=True, after_phase=None):
    after_phase = after_phase or ('after-final' if (OUT / 'after-final/predictions.json').is_file() else 'after')
    before = read_json(OUT / 'before/predictions.json')
    after = read_json(OUT / after_phase / 'predictions.json')
    def canonical(documents):
        documents = copy.deepcopy(documents)
        for doc in documents:
            # Independent concurrent composition jobs append facts as they
            # finish. Compare complete records by stable ID, not arrival order.
            for key in ['facts', 'relations']:
                if isinstance(doc.get(key), list):
                    assert len({r['id'] for r in doc[key]}) == len(doc[key])
                    doc[key].sort(key=lambda r: r['id'])
        return documents
    result = {'prediction_records_unchanged': canonical(before) == canonical(after),
              'raw_prediction_order_unchanged': before == after,
              'new_model_requests': 0,
              'comparison_rule': 'Full facts/relations compared by stable ID; all other fields exactly; original raw predictions preserved'}
    assert result['prediction_records_unchanged']
    if write:
        write_json(OUT / 'comparison.json', result)
        run = read_json(OUT / after_phase / 'knowledge/run.json')
        write_json(OUT / after_phase / 'verification.json', {**result, 'phase': after_phase,
            'summary': run['summary'], 'processing_status': run['processing_status']})
        print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
