"""Read-only trial of Agent submissions using the actual intake and evaluator.

This contains no extraction, expected case answers or extra model call. The
temporary database is removed on return; the caller retains the preview receipt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from .agent_bridge import prepare, validate_submission
from .contract import applicability, encoded
from .service import StandardAssessmentService
from ..provenance import source_revision


def preview_submission(paths, workspace):
    workspace = Path(workspace).resolve(strict=True)
    snapshots = {}

    def read(name, lines=False):
        p = (workspace / name).resolve(strict=True)
        if not p.is_relative_to(workspace):
            raise ValueError('Preview input escapes task workspace')
        blob = p.read_bytes()
        snapshots[name] = hashlib.sha256(blob).hexdigest()
        text = blob.decode('utf-8-sig')
        return [json.loads(s) for s in text.splitlines() if s.strip()] if lines else json.loads(text)

    manifest = read('manifest.json')
    facts = read('outputs/facts.jsonl', lines=True)
    objects = read('outputs/objects.json')
    objects = objects['objects'] if isinstance(objects, dict) else objects
    submission = read('outputs/assessment.json')
    for rows in (facts, objects):
        ids = [r['id'] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate fact/object IDs')
    documents = []
    for d in manifest['documents']:
        document_id = d['document_id']
        documents.append({'id': document_id, 'name': d.get('original_filename', document_id),
            'source_sha256': d['original_source_sha256'],
            'objects': [o for o in objects if o['document_id'] == document_id],
            'facts': [f for f in facts if f['document_id'] == document_id]})
    run = {'run_id': 'preview-only', 'documents': documents, 'assessment_submission': submission}
    validate_submission(submission, run)
    digest = hashlib.sha256(encoded(run).encode()).hexdigest()
    scratch = workspace / 'scratch'
    scratch.mkdir(exist_ok=True)
    if not scratch.resolve().is_relative_to(workspace):
        raise ValueError('Preview scratch escapes task workspace')
    rows = []
    with tempfile.TemporaryDirectory(prefix='assessment-preview-', dir=scratch) as tmp:
        api = StandardAssessmentService(paths, database=Path(tmp)/'preview.sqlite')
        for proposal in submission['cases']:
            doc = next(d for d in documents if d['id'] == proposal['document_id'])
            # The transient database refers to the exact manifest. It is never
            # published as an immutable PDF history or professional review.
            data = {'run': run, 'run_id': run['run_id'], 'route': 'agent', 'document': doc['id'],
                'doc': doc, 'raw': doc['facts'], 'source': workspace/'manifest.json',
                'sha256': doc['source_sha256'], 'run_hash': digest}
            meta = {'id': 'preview-only', 'manifest_sha256': snapshots['manifest.json']}
            made = prepare(data, {'subject_id': proposal['id']}, paths, meta, {}, digest, api=api)
            case, result = made['case'], made['result']
            inputs = []
            for m in case['debug_mapping']:
                f = api.store.get('fact_assertion', m['typed_fact_id'])
                resolution = result['resolved_variables'].get(f['variable_code'], {})
                check = applicability(f, case)
                inputs.append({'id': m['id'], 'variable': f['variable_code'], 'value': f['value'],
                    'selected': resolution.get('selected_fact_id') == f['fact_id'],
                    'resolution': resolution.get('status', 'retained_context'),
                    'applicability': check['status'], 'dimensions': check['dimensions'],
                    'scope': f['scope'], 'role': f['role'], 'source_fact_ids': m['source_fact_ids'],
                    'note': m['source_support_note']})
            rows.append({'id': proposal['id'], 'label': proposal.get('label', proposal['id']),
                'workflow_status': result['workflow_status'], 'inputs': inputs,
                'table15': result.get('partials', {}).get('table15'),
                'table17': result['table17_result'], 'missing': result.get('minimal_missing', []),
                'conflicts': result.get('conflicts', []), 'review_status': result['review_status'],
                'formal_assessment_enabled': False})
    for name, digest in snapshots.items():
        p = (workspace/name).resolve(strict=True)
        if not p.is_relative_to(workspace) or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError('Inputs changed while preview was running; run check_outputs again')
    return {'mode': 'deterministic_preview_pending_review', 'cases': rows,
        'input_sha256': snapshots, 'rules_sha256': api.rules_hash,
        'evaluator_revision': source_revision(Path(__file__).parent),
        'new_model_requests': 0, 'persisted_cases': 0,
        'note': '这是真实查表程序的试算，不是正确答案或来源验真。仅按原证据修正结构、引用及量义；不得为得到等级改值、删除冲突或把未知改成已知。真实缺口可保留并完成提交。'}
