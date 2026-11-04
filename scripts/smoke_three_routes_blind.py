"""Bounded, answer-blind three-route development regression experiment.

Preparation reads source metadata and opaque answer-file hashes only. Prediction
denies all evaluation/review-file reads. Evaluation is a separate, later step.
No production recognition algorithm, model profile or database is modified.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from slopekg import knowledge_prompts, visual_contract
from slopekg.basic_pipeline import BasicPipeline
from slopekg.config import DemoPaths
from slopekg.extractors import extract_domain_candidates
from slopekg.graph_builder import build_automatic_graph
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.model_client import CompatibleVisionClient, ModelError, ModelProfile
from slopekg.parsers import PdfParser
from slopekg.provenance import runtime_identity
from slopekg.storage import write_json, write_jsonl
from slopekg.visual_pipeline import VisualPipeline
from slopekg.visual_settings import VisualSettings


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SmokeVisualSettings(VisualSettings):
    # Shared production runtime supports these; Visual's default remains intact.
    max_transient_retries: int = 1
    transient_retry_budget: int = 2
    retry_backoff_seconds: float = 2.0
    record_model_inputs: bool = True


def profiles():
    knowledge = ModelProfile.load(ROOT / 'config/models/deepseek-knowledge.json')
    visual = ModelProfile.load(ROOT / 'config/models/deepseek.json')
    return knowledge, visual


def settings():
    return (KnowledgeSettings(max_calls=8, model_concurrency=3, max_review_calls=1,
                             max_transient_retries=1, transient_retry_budget=2,
                             record_model_inputs=True),
            SmokeVisualSettings(max_calls=24, model_concurrency=3, audit_rounds=1,
                                max_rereads_per_region=1))


def signature():
    kp, vp = profiles()
    ks, vs = settings()
    return {'code_revision': runtime_identity()['code_revision'],
            'runner_sha256': sha(__file__), 'python': sys.version.split()[0],
            'profiles': {'knowledge': kp.identity(), 'visual': vp.identity()},
            'timeouts': {'knowledge': kp.timeout_seconds, 'visual': vp.timeout_seconds},
            'settings': {'knowledge': asdict(ks), 'visual': asdict(vs)},
            'prompts': {name: {k: v for k, v in vars(module).items()
                               if k.isupper() and isinstance(v, str)}
                        for name, module in [('knowledge', knowledge_prompts), ('visual', visual_contract)]}}


def local_paths(out):
    return replace(DemoPaths(), output_dir=out, parsed_dir=out / 'parsed',
                   graph_dir=out / 'graph', web_data_dir=out / 'web/data',
                   assets_dir=out / 'assets', extracted_dir=out / 'extracted',
                   rules_dir=out / 'rules', multimodal_dir=out / 'multimodal', cache_dir=out / 'cache')


def prepare(out, selection='prefix'):
    if (out / 'protocol.json').exists() or (out / 'execution.json').exists():
        raise ValueError('Experiment already exists; keep its frozen protocol')
    metadata = ROOT / '.private/evaluation/current/sources.jsonl'
    sources = [json.loads(line) for line in metadata.read_text(encoding='utf-8').splitlines() if line.strip()]
    chosen = min((r for r in sources if r['document_kind'] == '施工图'),
                 key=lambda r: (r['physical_pages'], r['sha256']))
    source = ROOT / chosen['path']
    assert sha(source) == chosen['sha256']
    # Page numbers are a source-only deterministic rule, computed before the
    # evaluation-side membership check. Never use answer values to select pages.
    if selection == 'third_window':
        start = chosen['physical_pages'] // 3 + 1
        pages = list(range(start, min(start + 2, chosen['physical_pages'] + 1)))
    else:
        pages = list(range(1, min(4, chosen['physical_pages']) + 1))
    core_metadata = None
    if selection == 'third_window':
        core_ids = set(read(ROOT / '.private/evaluation/current/regression_core.json')['point_ids'])
        members = []
        # Evaluation process only: project metadata, discard every answer field.
        for line in (ROOT / '.private/evaluation/current/test_points.jsonl').read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if (row['id'] in core_ids and row['source_sha256'] == chosen['sha256']
                    and set(row['pages']) <= set(pages)):
                members.append({'id': row['id'], 'pages': row['pages'], 'carrier': row['carrier']})
        if not members:
            raise ValueError('Source-only window has no fixed-core coverage; no model calls authorized by this protocol')
        core_metadata = {'fixed_core_count': len(members), 'metadata_only': True,
                         'selection_precedes_membership_check': True}
    protocol = {'created_at': stamp(), 'signature': signature(),
        'source': {k: chosen[k] for k in ('id', 'path', 'sha256', 'physical_pages', 'document_kind', 'exposure_status')},
        'selected_pages': pages,
        'selection_rule': ('shortest construction-drawing document; source-only two-page window starting floor(N/3)+1'
                           if selection == 'third_window' else 'shortest construction-drawing document; first four physical pages'),
        'evaluation_side_core_coverage': core_metadata,
        'answer_commitments': {str(p.relative_to(ROOT)): sha(p) for p in [
            ROOT / '.private/evaluation/current/test_points.jsonl',
            ROOT / '.private/evaluation/current/regression_core.json']},
        'evidence_boundary': 'answer-blind development regression; historical development exposure declared; not independent holdout or expert certification',
        'basic': {'entry': 'production PdfParser / BasicPipeline.run_ocr / extract_domain_candidates / build_automatic_graph',
                  'local_ocr_page_cap': 4, 'model_calls': 0,
                  'native_parse': 'whole source locally for complete manifest; extraction/OCR and model inputs restricted to selected pages'},
        'stages': ['all three routes on one frozen input batch', 'freeze all predictions and hashes',
                   'open current answers and score applicable fixed-core points',
                   'stop this stage; expand only after assessing recorded failures'],
        'stop_rules': ['provider/transport fatal errors stop new transport attempts for that profile',
                       'answer/source access violation or hash mismatch invalidates run',
                       'systematic wrong object/value/evidence, or <50% strict point coverage with >=5 applicable points stops expansion',
                       'insufficient core points or material budget exhaustion means inconclusive; no claim of passing'],
        'authorized_cap': {'knowledge': 8, 'visual': 24, 'total': 32, 'retries_included': True},
        'human_authorization': 'User authorized direct API calls and staged small samples, stopping on major issues in this chat on 2026-10-31.'}
    write_json(out / 'protocol.json', protocol)
    print(json.dumps({'prepared': True, 'source': chosen['path'], 'pages': protocol['selected_pages'],
                      'request_caps': protocol['authorized_cap'], 'core_metadata': core_metadata,
                      'answer_values_used_for_selection_or_prediction': False}, ensure_ascii=False), flush=True)


def install_read_guard(out):
    denied = []
    prohibited = [str(ROOT / '.private/evaluation').lower(),
                  str(ROOT / '.private/reviews').lower(), str(ROOT / 'tests/fixtures').lower()]
    def audit(event, args):
        if event != 'open' or not args or not isinstance(args[0], (str, bytes, Path)):
            return
        mode = args[1] if len(args) > 1 else None
        if isinstance(mode, str) and not ('r' in mode or '+' in mode):
            return
        path = str(Path(args[0]).resolve()).lower()
        if any(path == root or path.startswith(root + '\\') or path.startswith(root + '/') for root in prohibited):
            denied.append({'path': path, 'at': stamp()})
            write_json(out / 'denied_reads.json', denied)
            raise PermissionError('Prediction must not open evaluation/reference-review assets')
    sys.addaudithook(audit)
    return denied


class LoggedClient:
    """Archives actual transport entries separately from pipeline reservations."""
    def __init__(self, profile, folder):
        self.client = CompatibleVisionClient(profile, ROOT)
        self.folder = folder
        self.lock = threading.Lock()
        self.rows = []
        self.blocked = getattr(type(self), 'experiment_blocked', None)
        self.active = self.peak = 0

    def identity(self):
        return self.client.identity()

    def complete(self, *, system, prompt, images):
        with self.lock:
            if self.blocked:
                raise ModelError('Experiment transport stopped: ' + self.blocked,
                                 receipt={'not_sent': True, 'experiment_circuit_open': True})
            ordinal = len(self.rows) + 1
            row = {'ordinal': ordinal, 'status': 'started', 'started_at': stamp(),
                   'image_count': len(images)}
            self.rows.append(row)
            self.active += 1
            self.peak = max(self.peak, self.active)
            write_json(self.folder / 'transport.json', self.rows)
        write_json(self.folder / f'input-{ordinal:03d}.json', {
            'system': system, 'prompt': prompt, 'model': self.identity(),
            'images': [{'path': str(img.path), 'sha256': sha(img.path)} for img in images]})
        try:
            result, receipt = self.client.complete(system=system, prompt=prompt, images=images)
            with self.lock:
                row.update(status='finished', finished_at=stamp(), receipt=receipt)
            return result, receipt
        except ModelError as exc:
            with self.lock:
                row.update(status='failed_or_unknown', finished_at=stamp(), error=str(exc), receipt=exc.receipt)
                if (exc.receipt or {}).get('http_status') in {400, 401, 402, 403, 404, 422}:
                    self.blocked = 'non-transient HTTP ' + str(exc.receipt['http_status'])
                elif (exc.receipt or {}).get('finish_reason') == 'length':
                    self.blocked = 'known output truncation with provider receipt; stop without automatic resend'
                elif (exc.receipt or {}).get('usage'):
                    self.blocked = 'invalid completed model response with provider receipt'
                elif not (exc.receipt or {}).get('http_status'):
                    self.blocked = 'unknown transport outcome; no automatic resend or further dispatch'
                if self.blocked:
                    type(self).experiment_blocked = self.blocked
            raise
        finally:
            with self.lock:
                self.active -= 1
                write_json(self.folder / 'transport.json', self.rows)


def basic_predict(source, pages, out):
    raw = asdict(PdfParser(ROOT).parse(source))
    parsed_dir = out / 'parsed'
    for key, value in raw.items():
        write_jsonl(parsed_dir / f'{key}.jsonl', value)
    pipeline = BasicPipeline(local_paths(out))
    selected_tasks = [t for t in raw['ocr_tasks'] if t['page'] in pages]
    print(json.dumps({'route': 'basic', 'stage': 'local_ocr', 'selected_tasks': len(selected_tasks)}, ensure_ascii=False), flush=True)
    tasks, ocr = pipeline.run_ocr(selected_tasks, max_pages=4, force=False,
                                all_tasks=raw['ocr_tasks'], device='auto', workers=3, batch_size=0)
    # A complete source-matched manifest lets Knowledge reuse the very same OCR.
    raw.update(ocr_tasks=tasks, ocr_results=ocr)
    write_json(out / 'parsed.json', raw)
    selected = {k: v if k == 'documents' else [r for r in v if r.get('page') in pages]
                for k, v in raw.items()}
    selected['text_blocks'] = [*selected['text_blocks'], *selected['ocr_results']]
    extracted = extract_domain_candidates(selected)
    graph = build_automatic_graph(selected, extracted, [])
    write_json(out / 'extracted.json', extracted)
    write_json(out / 'web/data/demo_graph.json', graph)
    result = {'route': 'basic', 'selected_pages': pages, 'source_sha256': sha(source),
              'producer': runtime_identity(), 'calls': 0, 'ocr_tasks': selected['ocr_tasks'],
              'ocr_results': selected['ocr_results'], 'extracted': extracted,
              'graph': graph, 'scope': 'selected-page production recognition components; isolated graph, no main publication'}
    write_json(out / 'run.json', result)
    return result


def predict(out):
    protocol = read(out / 'protocol.json')
    assert protocol['signature'] == signature(), 'Frozen code/profile/settings changed'
    source = ROOT / protocol['source']['path']
    assert sha(source) == protocol['source']['sha256']
    if (out / 'execution.json').exists():
        raise ValueError('Already started; unknown outcomes are not automatically rerun')
    write_json(out / 'execution.json', {'status': 'started', 'at': stamp(), 'protocol_sha256': sha(out / 'protocol.json')})
    denied = install_read_guard(out)
    started = time.monotonic()
    pages = protocol['selected_pages']
    basic = basic_predict(source, pages, out / 'basic')
    write_json(out / 'basic_receipt.json', {'model_calls': 0, 'selected_pages': pages,
        'ocr_tasks': len(basic['ocr_tasks']), 'ocr_results': len(basic['ocr_results']),
        'ocr_statuses': {s: sum(t.get('status') == s for t in basic['ocr_tasks'])
                         for s in sorted({t.get('status', 'unknown') for t in basic['ocr_tasks']})}})
    kp, vp = profiles()
    ks, vs = settings()
    receipts = {}
    for route, profile, config in [('knowledge', kp, ks), ('visual', vp, vs)]:
        client = LoggedClient(profile, out / route / 'transport')
        def progress(percent, stage, message, route=route):
            print(json.dumps({'route': route, 'percent': percent, 'stage': stage, 'message': message}, ensure_ascii=False), flush=True)
        route_started = time.monotonic()
        if route == 'knowledge':
            runner = KnowledgePipeline(client, out / route, settings=config,
                        parsed_dir=out / 'basic/parsed', root=ROOT, progress_callback=progress)
        else:
            runner = VisualPipeline(client, out / route, settings=config, progress_callback=progress)
        result = runner.run([source], page_selections={str(source.resolve()): pages})
        usage = [r.get('receipt') or {} for r in client.rows if (r.get('receipt') or {}).get('usage')]
        receipts[route] = {'actual_transport_entries': len(client.rows), 'usage_receipts': len(usage),
            'usage': {k: sum(r['usage'].get(k, 0) for r in usage)
                      for k in ['prompt_tokens', 'completion_tokens', 'total_tokens']},
            'transport_peak_concurrency': client.peak, 'circuit_open_reason': client.blocked,
            'elapsed_seconds': round(time.monotonic() - route_started, 2),
            'pipeline_summary': result.get('summary'), 'processing_status': result.get('processing_status')}
        assert len(client.rows) <= config.max_calls
        print(json.dumps({'route': route, 'receipt': receipts[route]}, ensure_ascii=False), flush=True)
    frozen = {'frozen_at': stamp(), 'protocol_sha256': sha(out / 'protocol.json'),
              'predictions': {route: {'path': str((out / route / 'run.json').relative_to(ROOT)),
                                     'sha256': sha(out / route / 'run.json')}
                              for route in ['basic', 'knowledge', 'visual']},
              'denied_answer_reads': denied, 'answer_content_opened_during_prediction': False,
              'note': 'All three predictions are frozen before any scoring process may read current answers.'}
    assert not denied
    write_json(out / 'predictions_frozen.json', frozen)
    result = {'status': 'predictions_frozen_awaiting_evaluation', 'finished_at': stamp(),
              'elapsed_seconds': round(time.monotonic() - started, 2), 'routes': receipts,
              'total_actual_transport_entries': sum(r['actual_transport_entries'] for r in receipts.values()),
              'answer_reads_denied': len(denied), 'environment_variables_used': []}
    write_json(out / 'execution.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'predict'])
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/20261031_三路盲测_阶段1')
    parser.add_argument('--selection', choices=['prefix','third_window'], default='prefix')
    args = parser.parse_args()
    if args.phase == 'prepare':
        prepare(args.output.resolve(),args.selection)
    else:
        predict(args.output.resolve())
