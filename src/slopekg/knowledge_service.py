"""知联 Knowledge service and bounded public read API."""
from pathlib import Path
import hashlib
from .knowledge_pipeline import KnowledgePipeline
from .knowledge_settings import KnowledgeSettings
from .model_client import ModelProfile, CompatibleVisionClient
from .pipeline_lock import pipeline_run_guard
from .recognition import visual_job_options
from .storage import read_json
from .provenance import version_status
from .recognition_routes import recognition_route


def run_knowledge_recognition(paths, *, input_pdfs, model_profile, review_profile=None,
                              knowledge_settings=None, prepare_only=False, progress_callback=None):
    """知联 Knowledge service; public facade: DemoPipeline.run_knowledge()."""
    if not input_pdfs or model_profile is None:
        raise ValueError('知联 Knowledge 需要明确PDF和模型配置')
    settings = KnowledgeSettings(**(knowledge_settings or {}))
    model = CompatibleVisionClient(ModelProfile.load(Path(model_profile)), paths.root)
    reviewer = CompatibleVisionClient(ModelProfile.load(Path(review_profile)), paths.root) if review_profile else None
    with pipeline_run_guard(paths.output_dir / '.pipeline.lock'):
        return KnowledgePipeline(model, paths.output_dir / 'knowledge-recognition', settings=settings,
            review_model=reviewer, progress_callback=progress_callback,
            parsed_dir=paths.parsed_dir, root=paths.root).run(input_pdfs, prepare_only=prepare_only)


def knowledge_job_options(paths, body):
    if 'visual_settings' in body:
        raise ValueError('知联 Knowledge 不能混用视读 Visual 设置')
    options = visual_job_options(paths, {**body, 'visual_settings': {}})
    options.pop('visual_settings')
    limits = body.get('knowledge_settings', {})
    if not isinstance(limits, dict) or set(limits) - {'max_calls', 'model_concurrency', 'ocr_pages', 'record_model_inputs'}:
        raise ValueError('网页仅接受调用上限和模型并发设置')
    KnowledgeSettings(**limits)
    return {**options, 'recognizer': 'knowledge', 'knowledge_settings': limits}


def knowledge_payload(paths, *, entity_id='', status='', offset=0, limit=50):
    if status not in {'', 'source_aligned', 'pending', 'verified_model', 'rejected'} or offset < 0 or not 1 <= limit <= 200:
        raise ValueError('无效知识结果筛选或分页')
    run = read_json(paths.output_dir / 'knowledge-recognition/run.json', {})
    docs = run.get('documents', [])
    implementation = version_status(run.get('producer'))
    for doc in docs:
        try:
            digest = hashlib.sha256(Path(doc['path']).read_bytes()).hexdigest()
            doc['source_status'] = 'current' if digest == doc['source_sha256'] else 'source_changed'
        except OSError:
            doc['source_status'] = 'source_unavailable'
        reason = doc['source_status'] if doc['source_status'] != 'current' else (
            'implementation_changed' if implementation != 'current' else '')
        if reason:
            doc['status'] = 'stale'
            for item in [*doc.get('facts', []), *doc.get('relations', [])]:
                item.update(stored_status=item['status'], status='pending', reason=reason)
    entities = [{**{k: e.get(k) for k in ('id', 'name', 'kind', 'context', 'aliases', 'description', 'status', 'chunk_ids')},
                  'document_id': d['id'], 'fact_count': len(e.get('fact_ids', []))} for d in docs for e in d.get('entities', [])]
    if entity_id and entity_id not in {e['id'] for e in entities}:
        raise ValueError('对象不存在')
    rows, relations = [], []
    for doc in docs:
        source = Path(doc['path']).resolve()
        source_path = source.relative_to(paths.raw_dir.resolve()).as_posix() if source.is_relative_to(paths.raw_dir.resolve()) else None
        relations.extend({**r, 'file_name': source.name, 'source_path': source_path}
            for r in doc.get('relations', []) if (not entity_id or r['entity_id'] == entity_id)
            and (not status or r['status'] == status))
        for fact in doc.get('facts', []):
            if (entity_id and fact['entity_id'] != entity_id) or (status and fact['status'] != status):
                continue
            keys = ('id', 'entity_id', 'object', 'field', 'value', 'unit', 'unit_basis', 'scope', 'role',
                    'condition', 'time', 'qualifier', 'status', 'reason', 'quality_issues', 'evidence', 'page', 'owner_candidates')
            rows.append({**{k: fact.get(k) for k in keys}, 'file_name': source.name, 'source_path': source_path})
    all_facts = [f for d in docs for f in d.get('facts', [])]
    summary = {**run.get('summary', {}), **{s: sum(f['status'] == s for f in all_facts)
        for s in ('source_aligned', 'verified_model', 'pending', 'rejected')}}
    return {'recognizer': 'knowledge', 'route': recognition_route('knowledge').payload(),
        'processing_status': 'stale' if any(d.get('status') == 'stale' for d in docs) else run.get('processing_status', 'not_generated'),
        'implementation_status': implementation, 'summary': summary, 'metric_note': run.get('metric_note', '自动核验不是人工准确率。'),
        'precision': None, 'recall': None, 'entities': entities, 'count': len(rows), 'rows': rows[offset:offset + limit],
        'offset': offset, 'limit': limit, 'documents': [{'file_name': Path(d['path']).name,
            'status': d.get('status'), 'source_status': d['source_status'], 'coverage': d.get('coverage', {}),
            'source_inventory': d.get('source_inventory', {}), 'call_plan': d.get('call_plan', {})} for d in docs],
        'object_pages': [p for d in docs for p in d.get('object_pages', []) if not entity_id or p['entity_id'] == entity_id],
        'relations': relations[:200], 'relation_count': len(relations), 'relation_limit': 200}
