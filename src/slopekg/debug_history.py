"""Durable, isolated recognition snapshots and append-only debug reviews."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
import zipfile

from .storage import read_json, write_json, write_jsonl, verify_file_sha256
from .pipeline_lock import pipeline_run_guard, process_is_running

_LOCK = threading.RLock()
ID_PATTERN = r'h_[a-f0-9]{24}'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record_dir(paths, record_id):
    if not isinstance(record_id, str) or not re.fullmatch(ID_PATTERN, record_id):
        raise ValueError('无效历史记录ID')
    root = (paths.output_dir / 'debug-history').resolve()
    folder = (root / record_id).resolve()
    if not folder.is_relative_to(root):
        raise ValueError('历史路径越界')
    return folder


def create_record(paths, *, label, route, mode, definition=None):
    from .recognition_debug import STAGES
    if route not in STAGES or not isinstance(label, str) or not label.strip() or len(label) > 160:
        raise ValueError('请填写160字以内的记录名称和有效识别路线')
    record_id = 'h_' + uuid.uuid4().hex[:24]
    folder = record_dir(paths, record_id)
    folder.mkdir(parents=True)
    meta = {'schema_version': 1, 'id': record_id, 'label': label.strip(), 'route': route,
        'mode': mode, 'status': 'queued', 'created_at': now(), 'updated_at': now(),
        'owner_pid': os.getpid(), 'progress': 0, 'snapshot_ready': False,
        'message': '记录已创建', 'dataset_id': (definition or {}).get('dataset', {}).get('id'),
        'selected_points': len((definition or {}).get('points', []))}
    write_json(folder / 'record.json', meta)
    write_json(folder / 'definition.json', definition or {})
    return meta


def update_record(paths, record_id, **values):
    with _LOCK:
        path = record_dir(paths, record_id) / 'record.json'
        meta = read_json(path)
        if not meta:
            raise ValueError('历史记录不存在')
        meta.update(values, updated_at=now())
        write_json(path, meta)
        return meta


def get_record(paths, record_id):
    meta = read_json(record_dir(paths, record_id) / 'record.json')
    if not isinstance(meta, dict):
        raise ValueError('历史记录不存在')
    if meta.get('status') in {'queued', 'running'} and not process_is_running(meta.get('owner_pid', 0)):
        meta = {**meta, 'status': 'interrupted', 'message': '原服务已退出；已有文件保留，可另建记录重测，不自动重发模型请求'}
    return meta


def list_records(paths, *, q='', route='', offset=0, limit=30):
    if offset < 0 or not 1 <= limit <= 100 or len(q) > 300:
        raise ValueError('历史分页或筛选参数无效')
    if route not in ('', 'basic', 'knowledge', 'visual'):
        raise ValueError('无效识别方法')
    root = paths.output_dir / 'debug-history'
    rows = []
    for folder in root.glob('h_*'):
        if not re.fullmatch(ID_PATTERN, folder.name):
            continue
        try:
            row = get_record(paths, folder.name)
        except (OSError, ValueError):
            continue
        if (not route or row.get('route') == route) and (not q or q.casefold() in json.dumps(row, ensure_ascii=False).casefold()):
            rows.append(row)
    rows.sort(key=lambda r: (r.get('created_at', ''), r['id']), reverse=True)
    return {'count': len(rows), 'offset': offset, 'limit': limit, 'records': rows[offset:offset + limit]}


def checked_artifact(path):
    """Verify each frozen file on read; metadata and reviews remain editable."""
    path = Path(path).resolve()
    ancestors = [p for p in path.parents if re.fullmatch(ID_PATTERN, p.name) and p.parent.name == 'debug-history']
    if not ancestors:
        return
    folder = ancestors[0]
    meta = read_json(folder / 'record.json', {})
    if not meta.get('snapshot_ready'):
        raise ValueError('历史快照尚未完整保存')
    manifest_path = folder / 'manifest.json'
    verify_file_sha256(manifest_path, meta.get('manifest_sha256'))
    manifest = read_json(manifest_path, {})
    relative = path.relative_to(folder).as_posix()
    expected = manifest.get('files', {}).get(relative)
    if expected is None:
        if not path.exists():
            return  # Explicitly missing historical input/response stays missing.
        raise ValueError('文件不属于该次冻结记录')
    verify_file_sha256(path, expected)


def history_store(paths, route, run_id):
    record_id = run_id.removeprefix('history-')
    meta = get_record(paths, record_id)
    if meta['route'] != route or not meta.get('snapshot_ready'):
        raise ValueError('历史记录路线不一致或快照尚未完成')
    folder = record_dir(paths, record_id) / 'prediction'
    checked_artifact(folder / 'run.json')
    return read_json(folder / 'run.json'), folder


def artifact_paths(paths, run, folder):
    return replace(paths, parsed_dir=folder / 'parsed', extracted_dir=folder / 'extracted') if run.get('debug_archive_id') else paths


def capture_snapshot(paths, record_id, *, route, run_id, document):
    from .recognition_debug import (_document, _call_refs, _safe_location, _basic_rows,
                                    _jsonl, debug_source, _clean)
    run, origin, doc = _document(paths, route, run_id, document)
    raw_before = json.dumps(run, sort_keys=True, ensure_ascii=False)
    source = debug_source(paths, route=route, run_id=run_id, document=document)
    expected_source_digest = doc.get('source_sha256') or digest(source)
    folder = record_dir(paths, record_id)
    destination = folder / 'prediction'
    destination.mkdir(exist_ok=True)
    source_copy = destination / 'source.pdf'
    shutil.copy2(source, source_copy)
    source_digest = digest(source_copy)
    if source_digest != expected_source_digest or source_digest != digest(source):
        raise ValueError('保存时原件发生变化，快照未发布')
    refs = _call_refs(paths, run, origin, doc) if route != 'basic' else []
    keys = {r['id'] for r in refs}
    copied = []
    for key in keys:
        for name in ['inputs', 'cache', 'exchanges']:
            candidate = _safe_location(origin / name / (key + '.json'), paths.root)
            if candidate.exists():
                checked_artifact(candidate)
                target = destination / name / candidate.name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(candidate, target)
                copied.append((candidate, digest(target)))
    image_map = {}
    previous_images = read_json(origin / 'image-map.json', {})
    if (origin / 'image-map.json').is_file():
        checked_artifact(origin / 'image-map.json')
    for key in keys:
        inp = read_json(destination / 'inputs' / (key + '.json'), {})
        for index, image in enumerate(inp.get('images', [])):
            previous = previous_images.get(f'{key}:{index}', {})
            candidate = origin / previous['path'] if previous.get('path') else image.get('path')
            if not candidate:
                continue
            candidate = _safe_location(candidate, paths.root)
            if not candidate.is_relative_to(origin.resolve()) or candidate.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                raise ValueError('请求图像不在本次识别输出中')
            if not candidate.exists():
                continue
            checked_artifact(candidate)
            verify_file_sha256(candidate, image.get('sha256'))
            target = destination / 'saved-images' / (image['sha256'] + candidate.suffix.lower())
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(candidate, target)
            copied.append((candidate, digest(target)))
            image_map[f'{key}:{index}'] = {'path': target.relative_to(destination).as_posix(), 'sha256': image['sha256']}
    write_json(destination / 'image-map.json', image_map)
    cache = _safe_location(origin / 'source-cache' / (source_digest + '.json'), paths.root)
    if cache.exists():
        checked_artifact(cache)
        (destination / 'source-cache').mkdir(exist_ok=True)
        shutil.copy2(cache, destination / 'source-cache' / cache.name)
        copied.append((cache, digest(destination / 'source-cache' / cache.name)))
    trace_file = origin / 'debug-trace.json'
    if trace_file.exists():
        checked_artifact(trace_file)
        write_json(destination / 'debug-trace.json', [r for r in read_json(trace_file, [])
            if r.get('cache_key') in keys and (not r.get('document_id') or r['document_id'] == doc['id'])])
    original_paths = artifact_paths(paths, run, origin)
    manifests = [d for d in _jsonl(original_paths, 'documents') if d.get('source_sha256') == source_digest]
    for name in ['documents', 'pages', 'text_blocks', 'tables', 'ocr_tasks', 'ocr_results']:
        rows = manifests if name == 'documents' else [r for m in manifests for r in _basic_rows(original_paths, name, m)]
        write_jsonl(destination / 'parsed' / (name + '.jsonl'), rows)
    if route == 'basic':
        from .recognition_debug import _records
        write_jsonl(destination / 'extracted/property_assertions.jsonl',
                    _records(original_paths, run, origin, doc, route, 'facts'))
    archived_doc = {**doc, 'original_source_path': doc.get('original_source_path', doc['path']),
                    'path': str(source_copy), 'source_sha256': source_digest}
    archived_run = {**run, 'documents': [archived_doc],
        'receipts': [r for r in run.get('receipts', []) if r.get('cache_key') in keys],
        'debug_archive_id': record_id, 'debug_original_run': run_id,
        'debug_source_run_sha256': hashlib.sha256(raw_before.encode('utf-8')).hexdigest()}
    if (origin / 'run.json').is_file():
        archived_run['debug_source_run_file_sha256'] = run.get('debug_source_run_file_sha256') or digest(origin / 'run.json')
    write_json(destination / 'run.json', _clean(archived_run))
    after, _, _ = _document(paths, route, run_id, document)
    if json.dumps(after, sort_keys=True, ensure_ascii=False) != raw_before or any(digest(p) != h for p, h in copied):
        raise ValueError('识别记录正在变化，请任务结束后重试；未发布混合快照')
    return archived_run, destination


def seal_record(paths, record_id, *, run, extra=None):
    folder = record_dir(paths, record_id)
    files = {}
    # Only these owned subtrees participate; mutable status/reviews are separate.
    candidates = [folder / 'definition.json', folder / 'results.json',
                  *list((folder / 'prediction').rglob('*')),
                  *list((folder / 'implementation').rglob('*'))]
    for path in candidates:
        if path.is_file() and not path.name.endswith(('.lock', '.tmp')):
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError('快照文件越界')
            files[path.relative_to(folder).as_posix()] = digest(path)
    write_json(folder / 'manifest.json', {'schema_version': 1, 'created_at': now(), 'files': files})
    return update_record(paths, record_id, snapshot_ready=True, manifest_sha256=digest(folder / 'manifest.json'),
        document_id=run['documents'][0]['id'], source_name=Path(run['documents'][0].get('original_source_path', run['documents'][0]['path'])).name,
        source_sha256=run['documents'][0]['source_sha256'], processing_status=run.get('processing_status'), **(extra or {}))


def save_snapshot(paths, *, route, run_id, document, label):
    meta = create_record(paths, label=label, route=route, mode='snapshot')
    record_id = meta['id']
    try:
        run, _ = capture_snapshot(paths, record_id, route=route, run_id=run_id, document=document)
        seal_record(paths, record_id, run=run)
        return update_record(paths, record_id, status='completed', progress=100, finished_at=now(), message='完整快照已保存')
    except Exception as exc:
        update_record(paths, record_id, status='failed', error=str(exc), message='快照保存失败；已保留诊断', finished_at=now())
        raise


def record_detail(paths, record_id):
    folder = record_dir(paths, record_id)
    meta = get_record(paths, record_id)
    for name in ['definition.json', 'results.json']:
        if meta.get('snapshot_ready'):
            checked_artifact(folder / name)
    return {'record': meta, 'definition': read_json(folder / 'definition.json', {}),
        'results': read_json(folder / 'results.json', {}), 'reviews': read_json(folder / 'reviews.json', [])}


def append_review(paths, record_id, *, point_id='', verdict='note', note='', label=None, tags=None):
    if verdict not in {'note', 'pass', 'fail', 'pending', 'not_applicable'} or not isinstance(note, str) or not note.strip() or len(note) > 8000:
        raise ValueError('请填写复核说明，最多8000字')
    folder = record_dir(paths, record_id)
    detail = record_detail(paths, record_id)
    if point_id and point_id not in {p['id'] for p in detail['definition'].get('points', [])}:
        raise ValueError('测试点不属于此历史记录')
    if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 160):
        raise ValueError('记录名称最多160字')
    if tags is not None and (not isinstance(tags, list) or len(tags) > 12 or any(not isinstance(t, str) or len(t) > 40 for t in tags)):
        raise ValueError('标签最多12个，每个40字以内')
    with _LOCK, pipeline_run_guard(folder / '.review.lock'):
        rows = read_json(folder / 'reviews.json', [])
        rows.append({'id': uuid.uuid4().hex, 'at': now(), 'point_id': point_id, 'verdict': verdict,
                     'note': note, 'label': label, 'tags': tags, 'author': 'local_user',
                     'previous_label': detail['record']['label'], 'previous_tags': detail['record'].get('tags', [])})
        write_json(folder / 'reviews.json', rows)
        update_record(paths, record_id, **({'label': label.strip()} if label is not None else {}),
                      **({'tags': tags} if tags is not None else {}))
    return record_detail(paths, record_id)


def export_record(paths, record_id):
    folder = record_dir(paths, record_id)
    meta = get_record(paths, record_id)
    files = ['record.json', 'definition.json', 'reviews.json']
    if meta.get('snapshot_ready'):
        manifest = read_json(folder / 'manifest.json')
        files += ['manifest.json', *manifest['files']]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name in dict.fromkeys(files):
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError('归档路径越界')
            if path.is_file():
                if name not in {'record.json', 'reviews.json', 'manifest.json'} and meta.get('snapshot_ready'):
                    checked_artifact(path)
                bundle.write(path, name)
    return stream.getvalue()


def compare_records(paths, left, right):
    a, b = record_detail(paths, left), record_detail(paths, right)
    dataset_hash = a['definition'].get('dataset_hash')
    same_dataset = bool(dataset_hash) and dataset_hash == b['definition'].get('dataset_hash')
    before = {r['point_id']: r for r in a['results'].get('rows', [])}
    after = {r['point_id']: r for r in b['results'].get('rows', [])}
    rows = [{'point_id': key, 'before': before.get(key), 'after': after.get(key),
             'changed': before.get(key) != after.get(key)} for key in sorted(before.keys() | after.keys())]
    return {'left': a['record'], 'right': b['record'], 'same_dataset_version': same_dataset,
        'has_test_results': bool(before or after),
        'rows': rows, 'changed_points': sum(r['changed'] for r in rows),
        'note': ('过程快照没有测试点结果；不能把零个测试点变化解释为两次识别一致。请打开完整过程逐步对照。' if not before and not after else
                 '数据集版本不同时不能直接解释为识别退步或提升。自动规则、历史复核与本地人工复核分别保留。'),
        'left_reviews': a['reviews'], 'right_reviews': b['reviews']}
