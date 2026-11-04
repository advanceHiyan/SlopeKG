"""Prepare/freeze first, then execute one bounded selected-page Knowledge smoke test.

No gold answers, reference wiki or historical candidate enters the model context.
The runner refuses a second execution, including after an uncertain interruption.
"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from slopekg import knowledge_prompts
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings, VERSION
from slopekg.model_client import ModelProfile, CompatibleVisionClient, content_hash
from slopekg.pipeline_lock import pipeline_run_guard
from slopekg.provenance import runtime_identity
from slopekg.storage import read_json, write_json


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--pages', required=True, help='One-based physical pages, comma separated')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parsed', type=Path)
    parser.add_argument('--model-profile', type=Path, default=ROOT / 'config/models/deepseek-knowledge.json',
        help='Choose a profile for the task; deepseek-knowledge-max enables optional max thinking')
    parser.add_argument('--max-calls', type=int, default=18)
    parser.add_argument('--concurrency', type=int, default=6)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    source, out = args.pdf.resolve(strict=True), args.output.resolve()
    pages = sorted({int(p) for p in args.pages.split(',')})
    settings = KnowledgeSettings(max_calls=args.max_calls, model_concurrency=args.concurrency,
        max_review_calls=1, max_transient_retries=2, transient_retry_budget=4, record_model_inputs=True)
    profile = ModelProfile.load(args.model_profile)
    params = {'source': str(source), 'source_sha256': sha(source), 'pages': pages,
        'model': profile.identity(), 'timeout_seconds': profile.timeout_seconds, 'settings': asdict(settings),
        'version': VERSION, 'producer': {k: runtime_identity()[k] for k in ('code_revision', 'python')},
        'runner_sha256': sha(__file__),
        'prompts': {name: value for name, value in vars(knowledge_prompts).items() if name.isupper() and isinstance(value, str)},
        'parsed_sha256': sha(args.parsed) if args.parsed and args.parsed.is_file() else None,
        'scope': 'exposed development document selected-page smoke test; not whole-file or independent acceptance'}
    protocol_id = content_hash(params)
    protocol_file, execution_file = out / 'protocol.json', out / 'execution.json'
    if args.execute:
        frozen = read_json(protocol_file, {})
        if frozen.get('protocol_id') != protocol_id:
            raise ValueError('Source/code/settings changed or protocol not frozen; prepare first')
        if execution_file.exists():
            raise ValueError('This frozen run was already started; no automatic repeat or extra allowance')
    else:
        if execution_file.exists():
            raise ValueError('Keep the existing experiment immutable; use a new folder for a different experiment')
        class NoCalls:
            def identity(self): return profile.identity()
            def complete(self, **kwargs): raise AssertionError('Preparation cannot send a model request')
        prepared = KnowledgePipeline(NoCalls(), out / 'preparation', settings=settings,
            parsed_dir=args.parsed, root=ROOT).run([source], prepare_only=True, page_selections={str(source): pages})
        doc = prepared['documents'][0]
        write_json(protocol_file, {'protocol_id': protocol_id, 'frozen_at': datetime.now(timezone.utc).isoformat(),
            'parameters': params, 'source_inventory': doc['source_inventory'], 'call_plan': doc['call_plan'],
            'evidence_digest': doc['evidence_digest']})
        print(json.dumps({'protocol_id': protocol_id, 'selected_pages': pages, 'calls': 0,
            'call_plan': doc['call_plan'], 'source_inventory': doc['source_inventory']}, ensure_ascii=False), flush=True)
        return
    with pipeline_run_guard(out / '.smoke.lock'):
        if execution_file.exists(): raise ValueError('Experiment already started')
        write_json(execution_file, {'status': 'started', 'protocol_id': protocol_id,
            'authorized_request_cap': settings.max_calls, 'selected_pages': pages,
            'started_at': datetime.now(timezone.utc).isoformat(),
            'authorization': 'User requested selected tests, moderate concurrency and bounded retry in this conversation.'})
        client = CompatibleVisionClient(profile, ROOT)
        def progress(percent, stage, message):
            print(json.dumps({'percent': percent, 'stage': stage, 'message': message}, ensure_ascii=False), flush=True)
        pipeline = KnowledgePipeline(client, out / 'run', settings=settings,
            parsed_dir=args.parsed, root=ROOT, progress_callback=progress)
        started = time.monotonic()
        result = pipeline.run([source], page_selections={str(source): pages})
        records = [read_json(p) for p in (out / 'run/cache').glob('*.json')]
        actual_attempts = sum(len(r['attempts']) for r in records)
        assert actual_attempts == result['summary']['calls'] <= settings.max_calls
        assert {u['page'] for d in result['documents'] for u in d['units']} <= set(pages)
        usage = [r for r in result['receipts'] if not r.get('cache_hit') and r.get('usage')]
        tokens = {k: sum(r['usage'].get(k, 0) for r in usage) for k in
                  ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        receipt = {'status': 'finished', 'protocol_id': protocol_id, 'selected_pages': pages,
            'request_attempts': actual_attempts, 'usage_receipts': len(usage), 'usage': tokens,
            'transient_retries': result['summary']['transient_retries'],
            'peak_concurrency': result['summary']['peak_concurrency'],
            'elapsed_seconds': round(time.monotonic() - started, 2), 'summary': result['summary'],
            'processing_status': result['processing_status'], 'run_sha256': sha(out / 'run/run.json'),
            'environment_variables_used': result.get('environment_variables_used', [])}
        write_json(execution_file, receipt)
        print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
