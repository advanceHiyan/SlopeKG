"""Selected open checkpoints over frozen predictions or isolated new runs.

The dataset/expected answers are only read by the evaluator. The recognizer
receives a PDF, page selection, named model profile and ordinary settings.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import threading

from .debug_datasets import get_dataset, dataset_hash, execution_pages
from .debug_history import (create_record, update_record, get_record, record_dir, now,
    capture_snapshot, seal_record, digest, artifact_paths)
from .storage import read_json, read_jsonl, write_json, write_jsonl


def evaluate_points(paths, dataset, points, run, folder):
    doc = run['documents'][0]
    facts = (read_jsonl(folder / 'extracted/property_assertions.jsonl') if run.get('debug_route') == 'basic'
             else [*doc.get('facts', []), *doc.get('relations', [])])
    by_id = {f.get('id'): f for f in facts}
    result = []
    for point in points:
        pages = execution_pages(dataset, doc, point)
        def in_scope(fact):
            evidence_pages = {e.get('page') for e in fact.get('evidence', []) if isinstance(e, dict)}
            if fact.get('page') is not None:
                evidence_pages.add(fact['page'])
            return not pages or bool(set(pages) & evidence_pages)
        def matches(fact, check):
            actual = str(fact.get(check['field'], ''))
            return actual == check['value'] if check['op'] == 'equals' else check['value'] in actual
        candidates = [f for f in facts if in_scope(f)]
        checks = point.get('checks', [])
        matched = [f for f in candidates if checks and all(matches(f, c) for c in checks)
            and (not point.get('require_evidence', True) or any(e.get('quote') and type(e.get('page')) is int
                 for e in f.get('evidence', []) if isinstance(e, dict)))]
        status = ('matched' if matched else 'unmatched') if checks else 'needs_review'
        if not facts and (doc.get('status') in {'failed', 'prepared'} or run.get('processing_status') in {'failed', 'prepared'}):
            status = 'execution_unavailable'
        historical = point.get('historical_review')
        binding = bool(historical and point.get('historical_run_sha256') == run.get('debug_source_run_file_sha256')
                       and all(fid in by_id for fid in historical.get('fact_ids', [])))
        hint = point.get('expected', {}).get('value')
        hinted = [f for f in candidates if isinstance(hint, (str, int, float)) and str(hint) in str(f.get('value', ''))]
        ids = list(dict.fromkeys([*(f['id'] for f in matched),
            *(historical.get('fact_ids', []) if binding else []), *(f['id'] for f in hinted)]))
        result.append({'point_id': point['id'], 'label': point.get('label', point['id']),
            'original_pages': point.get('pages', []), 'input_pages': pages,
            'expected': point.get('expected', {}), 'checks': checks, 'rule_status': status,
            'matched_fact_ids': [f['id'] for f in matched], 'candidate_fact_ids': ids,
            'pipeline_admitted_fact_ids': [fid for fid in ids if by_id[fid].get('status') in {'source_aligned', 'verified_model'}],
            'historical_review_bound': binding,
            'historical_review': historical if binding else None,
            'historical_review_available': bool(historical),
            'expert_validated': False,
            'note': ('规则匹配只检查显式条件及有无引文，不代表语义、工程适用性或完整性正确。' if checks else
                     '无显式自动判定规则；保留原预期和候选，等待人工复核。')
                    + (' 历史结论仅对本份原预测有效。' if binding else
                       ' 预测版本不同，未套用旧复核结论。' if historical else '')})
    return {'schema_version': 1, 'rows': result, 'summary': {
        'selected': len(result), 'rule_matched': sum(r['rule_status'] == 'matched' for r in result),
        'rule_unmatched': sum(r['rule_status'] == 'unmatched' for r in result),
        'needs_review': sum(r['rule_status'] == 'needs_review' for r in result),
        'execution_unavailable': sum(r['rule_status'] == 'execution_unavailable' for r in result),
        'historical_complete': sum(r['historical_review_bound'] and bool(r['historical_review'].get('complete')) for r in result),
        'historical_incomplete': sum(r['historical_review_bound'] and not r['historical_review'].get('complete') for r in result)},
        'metric_note': '这是所选开放检查点的调试记录；自动规则、历史复核和本地人工判定分开，不是整文档准确率或独立验收成绩。'}


def start_test(paths, body, *, background=True):
    from .recognition_debug import _document, debug_source
    from .recognition import profile_path
    from .knowledge_settings import KnowledgeSettings
    from .visual_settings import VisualSettings
    dataset = get_dataset(paths, body.get('dataset_id'))
    selected = body.get('point_ids')
    if (not isinstance(selected, list) or not selected or any(not isinstance(key, str) for key in selected)
            or len(selected) != len(set(selected))):
        raise ValueError('请选择至少一个不重复的测试点')
    known = {p['id']: p for p in dataset['points']}
    if set(selected) - known.keys():
        raise ValueError('所选测试点不属于该数据集版本')
    points = [copy.deepcopy(known[key]) for key in selected]
    source = body.get('source') or {k: dataset['source'][k] for k in ['route', 'run_id', 'document']}
    if not isinstance(source, dict):
        raise ValueError('请选择已保存识别记录')
    source = {k: source.get(k) for k in ['route', 'run_id', 'document']}
    route = source['route']
    run, folder, doc = _document(paths, **source)
    pdf = debug_source(paths, **source)
    for point in points:
        execution_pages(dataset, doc, point)
    mode = body.get('mode', 'saved')
    if mode not in {'saved', 'recognize'}:
        raise ValueError('测试方式无效')
    scope = body.get('scope', 'whole_input')
    if scope not in {'whole_input', 'point_pages'}:
        raise ValueError('识别范围无效')
    settings, profile = None, None
    if mode == 'recognize':
        from .provenance import runtime_identity, source_revision
        if runtime_identity()['code_revision'] != source_revision(Path(__file__).parent):
            raise ValueError('识别代码已变化，请先重启后端再运行新模型测试，避免记录的源码与实际加载代码不一致')
        if route not in {'knowledge', 'visual'}:
            raise ValueError('基读请先在PDF工作台解析，再检查已保存结果')
        if body.get('allow_external') is not True:
            raise ValueError('重新识别需要明确允许发送PDF原文或图像到模型接口')
        profile = profile_path(paths, body.get('model_profile'))
        limits = body.get('settings', {})
        if not isinstance(limits, dict) or set(limits) - {'max_calls', 'model_concurrency', 'ocr_pages'}:
            raise ValueError('只接受调用上限、并发和知联OCR补页设置')
        if any(type(v) is not int for v in limits.values()):
            raise ValueError('调用上限、并发和OCR页数必须是整数')
        if route == 'visual' and 'ocr_pages' in limits:
            raise ValueError('视读不能混入知联OCR设置')
        settings = (KnowledgeSettings if route == 'knowledge' else VisualSettings)(**limits, record_model_inputs=True)
    definition = {'dataset': dataset, 'dataset_hash': dataset_hash(dataset), 'points': points,
        'source': source, 'mode': mode, 'scope': scope,
        'model_profile_id': body.get('model_profile') if mode == 'recognize' else None,
        'settings': asdict(settings) if settings else None,
        'answer_is_model_input': False}
    meta = create_record(paths, label=body.get('label') or dataset['name'], route=route,
                         mode=mode, definition=definition)
    record_id = meta['id']

    def worker():
        try:
            update_record(paths, record_id, status='running', message='正在冻结资料与识别记录', progress=2)
            archive = record_dir(paths, record_id)
            implementation = archive / 'implementation'
            implementation.mkdir(exist_ok=True)
            for file in Path(__file__).parent.glob('*.py'):
                shutil.copy2(file, implementation / file.name)
            if mode == 'saved':
                frozen, destination = capture_snapshot(paths, record_id, **source)
            else:
                frozen, destination = _recognize(paths, record_id, dataset, points, source, pdf, folder,
                    profile, settings, scope, route)
            frozen['debug_route'] = route
            write_json(destination / 'run.json', frozen)
            update_record(paths, record_id, progress=95, message='识别已冻结，正在检查所选测试点')
            results = evaluate_points(paths, dataset, points, frozen, destination)
            write_json(archive / 'results.json', results)
            seal_record(paths, record_id, run=frozen, extra={'test_summary': results['summary']})
            update_record(paths, record_id, status='completed', progress=100, finished_at=now(),
                message='所选测试点检查完成；识别本身的未完成项另列',
                evaluation_status='completed')
        except Exception as exc:
            from .model_client import ModelError
            from .pipeline_lock import PipelineAlreadyRunningError
            safe = str(exc) if isinstance(exc, (ValueError, OSError, ModelError, PipelineAlreadyRunningError)) else type(exc).__name__
            if mode == 'recognize':
                # Keep received requests/replies from an interrupted or failed
                # isolated run inspectable; never claim the snapshot is complete.
                try:
                    partial_folder = record_dir(paths, record_id) / 'prediction'
                    partial = read_json(partial_folder / 'run.json', {})
                    if partial.get('documents'):
                        partial['debug_archive_id'] = record_id
                        partial['debug_route'] = route
                        partial['processing_status'] = 'failed'
                        partial['documents'][0].update(path=str(partial_folder / 'source.pdf'),
                            source_sha256=digest(partial_folder / 'source.pdf'))
                        partial['documents'][0].setdefault('id', 'pdf_' + partial['documents'][0]['source_sha256'][:20])
                        write_json(partial_folder / 'run.json', partial)
                        seal_record(paths, record_id, run=partial, extra={'snapshot_partial': True})
                except (OSError, ValueError, KeyError):
                    pass
            update_record(paths, record_id, status='failed', error=safe, finished_at=now(),
                          message='测试未完成，失败记录及已有文件已保存；不会自动重发模型请求')
    if background:
        try:
            threading.Thread(target=worker, daemon=True, name='debug-test-' + record_id).start()
        except RuntimeError as exc:
            update_record(paths, record_id, status='failed', error=type(exc).__name__, finished_at=now())
    else:
        worker()
    return get_record(paths, record_id)


def _recognize(paths, record_id, dataset, points, source, pdf, origin, profile, settings, scope, route):
    from .knowledge_pipeline import KnowledgePipeline
    from .visual_pipeline import VisualPipeline
    from .recognition import build_visual_pipeline
    from .model_client import ModelProfile, CompatibleVisionClient
    from .pipeline_lock import pipeline_run_guard
    from .recognition_debug import _jsonl, _basic_rows
    folder = record_dir(paths, record_id) / 'prediction'
    folder.mkdir(exist_ok=True)
    source_copy = folder / 'source.pdf'
    shutil.copy2(pdf, source_copy)
    if digest(source_copy) != digest(pdf):
        raise ValueError('复制时原件发生变化')
    sha = digest(source_copy)
    old_cache = origin / 'source-cache' / (sha + '.json')
    if old_cache.is_file():
        from .debug_history import checked_artifact
        checked_artifact(old_cache)
        (folder / 'source-cache').mkdir(exist_ok=True)
        shutil.copy2(old_cache, folder / 'source-cache' / old_cache.name)
    selected_pages = sorted({page for p in points for page in execution_pages(dataset, {'source_sha256': sha}, p)})
    if scope == 'point_pages' and not selected_pages:
        raise ValueError('这些测试点没有明确物理页码，不能按测试点裁定识别范围')
    pages = {str(source_copy.resolve()): selected_pages} if scope == 'point_pages' else None
    def progress(percent, stage, message):
        update_record(paths, record_id, progress=min(90, max(3, int(percent * .9))), stage=stage, message=message)
    # Dataset/points/answers are deliberately absent from constructor/run args.
    model = CompatibleVisionClient(ModelProfile.load(profile), paths.root)
    with pipeline_run_guard(paths.output_dir / '.pipeline.lock'):
        if route == 'knowledge':
            pipeline = KnowledgePipeline(model, folder, settings=settings, parsed_dir=paths.parsed_dir,
                                         root=paths.root, progress_callback=progress)
        else:
            pipeline = build_visual_pipeline(model, folder, settings=settings,
                                            parsed_dir=paths.parsed_dir, progress_callback=progress)
        frozen = pipeline.run([source_copy], page_selections=pages)
    raw = folder / 'run.json'
    if raw.exists():
        shutil.copy2(raw, folder / 'original-run.json')
    if not frozen.get('documents'):
        raise ValueError('识别没有产生文档记录')
    frozen['debug_archive_id'] = record_id
    frozen['debug_original_run'] = source['run_id']
    frozen['documents'][0].update(original_source_path=str(pdf), path=str(source_copy), source_sha256=sha)
    frozen['documents'][0].setdefault('id', 'pdf_' + sha[:20])
    # Freeze the matching raw OCR sidecars for later source inspection.
    manifests = [d for d in _jsonl(paths, 'documents') if d.get('source_sha256') == sha]
    for name in ['documents', 'pages', 'text_blocks', 'tables', 'ocr_tasks', 'ocr_results']:
        rows = manifests if name == 'documents' else [r for d in manifests for r in _basic_rows(paths, name, d)]
        write_jsonl(folder / 'parsed' / (name + '.jsonl'), rows)
    return frozen, folder
