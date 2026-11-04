"""Read-only, paged debug views over explicit recognition stores.

No model client, OCR inference, arbitrary file paths or evaluation-tree browsing.
Only the current production run and named, already open examples are supported.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .storage import read_json, read_jsonl
from .provenance import version_status

DENIED_HASHES = {
    'c325fc949c6ed19e380321693b8f2d7f109948a893fbfef6f3f7ae869cb227d4',
    'b10a9b843c1c02975009a7c48404fc60b073bc4985cb359896d9f3933c8ba9c7',
}
OPEN_RUNS = {
    'open-g359': ('开放施工图 · v3.8缓存回归', 'outputs/20261101_知联目录容量修复/after/G359/knowledge/run.json'),
    'open-report': ('开放勘察报告 · 历史实发', 'outputs/20261031_固定回归与隐藏盲验/baseline/pdf_48c5da9cf568db6a/knowledge/run.json'),
    'open-paper': ('开放论文 · 历史实发', 'outputs/20261031_固定回归与隐藏盲验/baseline/pdf_01015fc12c9f8a74/knowledge/run.json'),
    'open-guide': ('开放指南 · 历史实发', 'outputs/20261031_固定回归与隐藏盲验/baseline/pdf_113cb4d072120fd7/knowledge_environment_recovery/run.json'),
}
STAGES = {
    'knowledge': [('input', '输入与参数', '查看本次文件、代码版本、设置和处理范围。'),
        ('pages', '逐页覆盖', '区分原生文字、扫描图、未读图形和待OCR页面。'),
        ('ocr', 'OCR识别原文', 'OCR就是从图片读文字；保留识别内容、坐标和置信度。'),
        ('units', '原文与表格', '查看实际进入知联的文字、表格行、表头和来源标识。'),
        ('chunks', '分块与归属', '查看每块包含哪些原文，以及关联给了哪些对象。'),
        ('entities', '对象目录', '查看发现的对象、别名、提及位置及参考概念。'),
        ('discovery', '发现与关联', '查看对象发现、分块归属的执行记录及失败。'),
        ('synthesis', '知识重组', '查看每个对象拿到的证据、重组任务和未完成项。'),
        ('calls', '大模型输入输出', '逐次查看完整提示词、实际请求参数、响应原文和解析结果。'),
        ('gates', '校验与拒绝', '查看证据、对象归属、单位等检查及保留待审的原因。'),
        ('facts', '最终候选', '查看完整事实和关系；自动通过不代表人工确认。'),
        ('errors', '错误与缺口', '集中查看失败、未覆盖页和容量阻塞。')],
    'visual': [('input', '输入与参数', '查看模型、图像设置和处理范围。'),
        ('pages', '逐页与版面', '查看页面卡片、区域、几何锚点与覆盖。'),
        ('ocr', '已有OCR证据', '查看基础解析中已有的OCR；视读直接读取图片。'),
        ('regions', '区域读取与复核', '查看区域中间状态、重读和审核。'),
        ('calls', '大模型输入输出', '逐次查看完整文字与图像请求、原始响应和解析结果。'),
        ('gates', '校验与拒绝', '查看事实的校验状态及原因。'),
        ('facts', '最终候选', '查看完整事实记录。'), ('errors', '错误与缺口', '集中查看失败和未读区域。')],
    'basic': [('input', '输入与参数', '查看基础解析状态及文件。'),
        ('pages', '逐页清单', '查看页面类型、文字量和图像数量。'),
        ('ocr', 'OCR识别原文', '查看OCR任务与全部识别文字，包括失败和待处理任务。'),
        ('units', '原生文字', '查看PDF本身携带的文字及坐标。'),
        ('tables', '表格结构', '查看提取的表格行列与错误。'),
        ('facts', '字段提取候选', '查看基础解析产生的字段断言。'),
        ('errors', '错误与缺口', '查看OCR失败、空页与待处理记录。')],
}


def _safe_location(path, root):
    path = Path(path)
    path = (path if path.is_absolute() else Path(root) / path).resolve()
    lower = path.as_posix().lower()
    if (not path.is_relative_to(Path(root).resolve()) or '/.private/evaluation/' in lower
            or re.search(r'/(?:sealed_holdout|r0[123])/', lower)):
        raise ValueError('该资料不在开放调试范围')
    return path


def _read(path, root, default=None):
    from .debug_history import checked_artifact
    path = _safe_location(path, root)
    checked_artifact(path)
    return read_json(path, {} if default is None else default)


def _jsonl(paths, name):
    from .debug_history import checked_artifact
    path = _safe_location(paths.parsed_dir / (name + '.jsonl'), paths.root)
    checked_artifact(path)
    return read_jsonl(path)


def _store(paths, route, run_id):
    if route not in STAGES:
        raise ValueError('无效识别路线')
    if not isinstance(run_id, str):
        raise ValueError('无效识别记录')
    if run_id.startswith('history-'):
        from .debug_history import history_store
        return history_store(paths, route, run_id)
    if run_id != 'live':
        if route != 'knowledge' or run_id not in OPEN_RUNS:
            raise ValueError('无效开放记录')
        run_path = paths.root / OPEN_RUNS[run_id][1]
        return _read(run_path, paths.root), run_path.parent
    if route == 'basic':
        state = _read(paths.basic_state_json, paths.root)
        return {**state, 'documents': _jsonl(paths, 'documents')}, paths.output_dir
    folder = paths.output_dir / ('knowledge-recognition' if route == 'knowledge' else 'recognition')
    return _read(folder / 'run.json', paths.root), folder


def _allowed_doc(paths, doc, run_id):
    if doc.get('source_sha256') in DENIED_HASHES:
        return False
    if not doc.get('path'):
        return False
    try:
        source = _safe_location(doc['path'], paths.root)
        return run_id != 'live' or source.is_relative_to(paths.raw_dir.resolve())
    except ValueError:
        return False


def _documents(paths, run, run_id):
    return [d for d in run.get('documents', []) if _allowed_doc(paths, d, run_id)]


def _document(paths, route, run_id, document):
    run, folder = _store(paths, route, run_id)
    doc = next((d for d in _documents(paths, run, run_id) if d.get('id') == document), None)
    if doc is None:
        raise ValueError('请选择当前记录中的开放资料')
    return run, folder, doc


def _clean(value):
    # Credentials are never part of debug output, including legacy metadata.
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()
                if k.lower() not in {'api_key', 'api_key_file', 'api_key_env', 'api_key_field',
                                     'authorization', 'password', 'access_token', 'secret'}}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def debug_catalog(paths, route='knowledge', run_id='live'):
    run, _ = _store(paths, route, run_id)
    from .debug_history import list_records
    history = list_records(paths, limit=100)['records']
    if run_id.startswith('history-') and not any('history-' + r['id'] == run_id for r in history):
        from .debug_history import get_record
        history.insert(0, get_record(paths, run_id.removeprefix('history-')))
    return {'route': route, 'run_id': run_id,
        'runs': [{'id': 'live', 'label': '当前生产记录'}] + ([{'id': k, 'label': v[0]}
            for k, v in OPEN_RUNS.items() if (paths.root / v[1]).is_file()] if route == 'knowledge' else [])
            + [{'id': 'history-' + r['id'], 'label': '历史 · ' + r['label']}
               for r in history if r['route'] == route and r.get('snapshot_ready')],
        'documents': [{'id': d['id'], 'name': Path(d.get('original_source_path', d['path'])).name,
            'status': d.get('status'), 'pages': d.get('source_page_count', d.get('pages', 0))
                if not isinstance(d.get('pages'), list) else d.get('source_page_count', len(d['pages']))}
            for d in _documents(paths, run, run_id)],
        'status': run.get('processing_status', run.get('status', 'not_generated')),
        'implementation_status': version_status(run.get('producer')),
        'stages': [{'id': k, 'label': label, 'help': help_text} for k, label, help_text in STAGES[route]],
        'note': '仅展示已保存证据；缺失记录不能重建为历史实发。调试页读取不调用模型。'}


def _basic_rows(paths, name, doc):
    return [r for r in _jsonl(paths, name) if r.get('document_id') == doc['id']]


def _ocr_rows(paths, folder, doc):
    """Keep raw OCR, including duplicate text excluded from knowledge units."""
    digest = doc.get('source_sha256')
    local = _read(folder / 'source-cache' / (str(digest) + '.json'), paths.root)
    rows = [{'record_type': 'raw_ocr_result', **r} for r in local.get('rows', [])
            if r.get('source_sha256') == digest]
    rows += [{'record_type': 'raw_ocr_task', **t} for t in local.get('tasks', [])
             if t.get('source_sha256') == digest]
    manifests = [d for d in _jsonl(paths, 'documents') if d.get('source_sha256') == digest]
    for manifest in manifests:
        for name in ['ocr_results', 'ocr_tasks']:
            rows.extend({'record_type': name, **r} for r in _basic_rows(paths, name, manifest)
                        if r.get('source_sha256') == digest)
    return rows


def _call_refs(paths, run, folder, doc):
    trace = _read(folder / 'debug-trace.json', paths.root, [])
    # Receipts prove membership in this run; never enumerate orphan cache files.
    references = [*trace, *run.get('receipts', [])]
    seen, rows = set(), []
    for ref in references:
        key = ref.get('cache_key', '')
        if not re.fullmatch('[a-f0-9]{64}', key) or key in seen:
            continue
        inp = _read(folder / 'inputs' / (key + '.json'), paths.root)
        assigned = ref.get('document_id') or inp.get('document_id')
        if assigned and assigned != doc['id']:
            continue
        if not assigned and len(run.get('documents', [])) != 1:
            # Legacy calls without document binding cannot safely be shown
            # beside one document (or reveal another excluded document).
            continue
        seen.add(key)
        cached = _read(folder / 'cache' / (key + '.json'), paths.root)
        receipt = cached.get('receipt', ref)
        rows.append({'id': key, 'stage': ref.get('stage', cached.get('stage')),
            'status': cached.get('status', ref.get('status', 'unknown')),
            'cache_hit': any(r.get('cache_key') == key and r.get('cache_hit') for r in run.get('receipts', [])),
            'started_at': ref.get('started_at'), 'duration_seconds': ref.get('duration_seconds'),
            'error': cached.get('error', ref.get('error')), 'receipt': receipt,
            'input_saved': bool(inp), 'raw_exchange_saved': (folder / 'exchanges' / (key + '.json')).is_file(),
            'prompt_chars': len(inp.get('system', '')) + len(inp.get('prompt', '')),
            'image_count': len(inp.get('images', [])), 'pages': sorted(_page_numbers(inp)),
            'evidence_kind': '当前调用追踪' if assigned else '单文件历史回执'})
    return rows


def _page_numbers(value):
    pages = set()
    if isinstance(value, dict):
        if type(value.get('page')) is int:
            pages.add(value['page'])
        if isinstance(value.get('pages'), list):
            pages.update(n for n in value['pages'] if type(n) is int)
        for key, child in value.items():
            if key == 'prompt' and isinstance(child, str) and '\n输入：' in child:
                try:
                    pages.update(_page_numbers(json.loads(child.split('\n输入：', 1)[1])))
                except ValueError:
                    pass
            elif isinstance(child, (dict, list)):
                pages.update(_page_numbers(child))
    elif isinstance(value, list):
        for child in value:
            pages.update(_page_numbers(child))
    return pages


def _records(paths, run, folder, doc, route, stage):
    if stage == 'input':
        return [{'id': 'run', **{k: v for k, v in run.items() if k not in {'documents', 'receipts'}},
                 'document': {k: v for k, v in doc.items() if k not in {
                     'units', 'chunks', 'entities', 'references', 'facts', 'relations', 'pages'}},
                 'source_path': doc.get('path')}]
    if stage == 'calls':
        return _call_refs(paths, run, folder, doc)
    if route == 'basic':
        if stage == 'facts':
            return [r for r in read_jsonl(_safe_location(paths.extracted_dir / 'property_assertions.jsonl', paths.root))
                    if r.get('document_id') == doc['id'] or r.get('source_document_id') == doc['id']]
        if stage == 'errors':
            return [r for r in _basic_rows(paths, 'ocr_tasks', doc) if r.get('status') != 'done']
        if stage == 'ocr':
            return [*({'record_type': 'ocr_result', **r} for r in _basic_rows(paths, 'ocr_results', doc)),
                    *({'record_type': 'ocr_task', **r} for r in _basic_rows(paths, 'ocr_tasks', doc))]
        return _basic_rows(paths, {'units': 'text_blocks'}.get(stage, stage), doc)
    if stage == 'pages':
        return doc.get('pages', [])
    if stage == 'ocr':
        if route == 'knowledge':
            return [*_ocr_rows(paths, folder, doc),
                    *({'record_type': 'ocr_unit', **r} for r in doc.get('units', []) if r.get('source') == 'ocr'),
                    *({'record_type': 'ocr_task', **r} for r in doc.get('ocr_receipts', [])),
                    *({'record_type': 'page_ocr_state', **r} for r in doc.get('pages', [])
                      if r.get('ocr_pending') or r.get('ocr_regions_processed'))]
        return _basic_rows(paths, 'ocr_results', doc)
    if stage == 'units':
        return doc.get('units', [])
    if stage == 'chunks':
        units = {r['id']: r for r in doc.get('units', [])}
        return [{**r, 'source_units': [units[k] for k in r.get('unit_ids', []) if k in units]}
                for r in doc.get('chunks', [])]
    if stage == 'entities':
        return [*doc.get('entities', []), *({'record_type': 'reference', **r} for r in doc.get('references', []))]
    if stage in {'discovery', 'synthesis'}:
        names = ['discovery_batches', 'attribution_batches'] if stage == 'discovery' else ['synthesis_jobs', 'review_jobs', 'recovery_jobs']
        return [{'record_type': name, **r} for name in names for r in doc.get(name, [])]
    if stage == 'regions':
        return [{'page': p['page'], **r} for p in doc.get('pages', []) for r in p.get('regions', [])]
    if stage in {'gates', 'facts'}:
        return [*doc.get('facts', []), *({'record_type': 'relation', **r} for r in doc.get('relations', []))]
    if stage == 'errors':
        rows = [{'record_type': 'document_error', 'error': error} for error in doc.get('errors', [])]
        rows += [{'record_type': 'page_gap', **p} for p in doc.get('pages', []) if p.get('errors') or p.get('graphics_unread') or p.get('ocr_pending') or p.get('unread_regions') or p.get('status') == 'failed']
        for key in ['capacity_failure', 'coverage', 'source_inventory']:
            if doc.get(key):
                rows.append({'record_type': key, **doc[key]})
        rows += [r for r in _call_refs(paths, run, folder, doc) if r.get('status') in {'failed', 'started', 'running'}]
        return rows
    return []


def debug_records(paths, *, route='knowledge', run_id='live', document='', stage='input', page=0, q='', offset=0, limit=25, failures=False):
    if (stage not in {s[0] for s in STAGES.get(route, [])} or type(page) is not int or page < 0
            or offset < 0 or not 1 <= limit <= 100 or len(q) > 300):
        raise ValueError('无效调试筛选参数')
    run, folder, doc = _document(paths, route, run_id, document)
    from .debug_history import artifact_paths
    paths = artifact_paths(paths, run, folder)
    trace = folder / 'debug-trace.json'
    snapshot = hashlib.sha256((json.dumps(run, sort_keys=True, ensure_ascii=False)
        + str(trace.stat().st_mtime_ns if trace.exists() else 0)).encode('utf-8')).hexdigest()
    rows = _records(paths, run, folder, doc, route, stage)
    trace_coverage = ({'calls': len(rows), 'saved_inputs': sum(bool(r.get('input_saved')) for r in rows),
                       'saved_raw_exchanges': sum(bool(r.get('raw_exchange_saved')) for r in rows)}
                      if stage == 'calls' else None)
    # Show raw coordinates unchanged; add a separate display-only normalized
    # box for Basic OCR/native rows where page geometry is available.
    if stage in {'ocr', 'units', 'tables'}:
        from .knowledge_document import normalized_box
        pages = {p.get('page'): p for p in (doc.get('pages', []) if isinstance(doc.get('pages'), list)
                                          else _basic_rows(paths, 'pages', doc))}
        if route == 'basic':
            rows = [{**r, 'debug_bbox': normalized_box(r, pages.get(r.get('page'), {}))} for r in rows]
    if page:
        # Rows without a page are document-wide; preserve them explicitly.
        rows = [r for r in rows if not _page_numbers(r) or page in _page_numbers(r)]
    if q:
        rows = [r for r in rows if q.casefold() in json.dumps(r, ensure_ascii=False).casefold()]
    if failures:
        rows = [r for r in rows if r.get('status') in {'failed', 'pending', 'rejected', 'started', 'running'}
                or r.get('error') or r.get('errors') or r.get('quality_issues') or r.get('ocr_pending') or r.get('graphics_unread')]
    return _clean({'route': route, 'run_id': run_id, 'document': document, 'stage': stage,
        'source_sha256': doc.get('source_sha256'), 'source_page_count': doc.get('source_page_count', doc.get('pages')),
        'implementation_status': version_status(run.get('producer')),
        'snapshot': snapshot, 'trace_coverage': trace_coverage, 'offset': offset, 'limit': limit, 'count': len(rows), 'rows': rows[offset:offset + limit],
        'note': '完整字段按记录分页；正文不截断。无页码记录属于整份资料。空列表表示当前未保存该类记录。'
                + (' 原始OCR缓存可能包含后来补识别的结果；本次实际入链文字以ocr_unit为准。' if stage == 'ocr' and route == 'knowledge' else '')})


def debug_call(paths, *, route, run_id, document, key):
    if not re.fullmatch('[a-f0-9]{64}', key):
        raise ValueError('无效调用标识')
    run, folder, doc = _document(paths, route, run_id, document)
    refs = _call_refs(paths, run, folder, doc)
    if not any(r['id'] == key for r in refs):
        raise ValueError('调用不属于当前开放记录')
    inp = _read(folder / 'inputs' / (key + '.json'), paths.root)
    cached = _read(folder / 'cache' / (key + '.json'), paths.root)
    exchange = _read(folder / 'exchanges' / (key + '.json'), paths.root)
    received_response = any(a.get('response') is not None or 'raw_response_body' in a
                            for a in exchange.get('attempts', []))
    previews = []
    from urllib.parse import urlencode
    for index, img in enumerate(inp.get('images', [])):
        try:
            debug_image(paths, route=route, run_id=run_id, document=document, key=key, index=index)
            available = True
        except (ValueError, OSError):
            available = False
        previews.append({'index': index, 'label': img.get('label', ''), 'available': available,
            'url': '/api/recognition-debug/image?' + urlencode({'route': route, 'run': run_id,
                'document': document, 'key': key, 'index': index}) if available else None})
    return _clean({'cache_key': key, 'input': inp or None, 'cache': cached or None,
        'exchange': exchange or None, 'image_previews': previews,
        'notes': [*(['历史未保存完整输入，不能据当前提示词冒充当时请求。'] if not inp else []),
                  *(['未保存供应商响应原文；缓存answer是解析/校验后的对象。'] if not exchange else []),
                  *(['已保存请求正文，但未收到或未保存供应商回复；发送与计费状态须结合失败回执，不能据此确认请求已到达服务商。']
                    if exchange and not received_response else []),
                  *(['当前输入由缓存查找重建，未重新发送模型。'] if inp.get('input_capture') == 'reconstructed_for_cache_lookup' else [])]})


def debug_image(paths, *, route, run_id, document, key, index):
    from .storage import verify_file_sha256
    from .debug_history import checked_artifact
    if not re.fullmatch('[a-f0-9]{64}', key) or type(index) is not int or index < 0:
        raise ValueError('无效请求图像标识')
    run, folder, doc = _document(paths, route, run_id, document)
    if not any(r['id'] == key for r in _call_refs(paths, run, folder, doc)):
        raise ValueError('图像不属于本次调用')
    inp = _read(folder / 'inputs' / (key + '.json'), paths.root)
    images = inp.get('images', [])
    if index >= len(images):
        raise ValueError('图像不存在')
    mapping = _read(folder / 'image-map.json', paths.root)
    mapped = mapping.get(f'{key}:{index}', {})
    value = folder / mapped['path'] if mapped.get('path') else images[index].get('path')
    if not value:
        raise ValueError('历史没有保存图像文件')
    path = _safe_location(value, paths.root)
    if not path.is_relative_to(folder.resolve()) or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
        raise ValueError('图像路径越界')
    checked_artifact(path)
    verify_file_sha256(path, images[index].get('sha256'))
    return path


def debug_source(paths, *, route, run_id, document):
    _, _, doc = _document(paths, route, run_id, document)
    source = _safe_location(doc['path'], paths.root)
    from .debug_history import checked_artifact
    checked_artifact(source)
    expected = doc.get('source_sha256')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest in DENIED_HASHES or expected and digest != expected:
        raise ValueError('原件哈希已变化或不在开放调试范围，禁止混用旧坐标')
    if source.suffix.lower() != '.pdf':
        raise ValueError('只允许PDF原件')
    return source


def get_debug_route(handler, parsed, paths):
    if not parsed.path.startswith('/api/recognition-debug'):
        return False
    from .debug_workspace_http import get_workspace_route
    if get_workspace_route(handler, parsed, paths):
        return True
    from http import HTTPStatus
    from urllib.parse import parse_qs
    query = parse_qs(parsed.query)
    value = lambda name, default='': query.get(name, [default])[0]
    common = {'route': value('route', 'knowledge'), 'run_id': value('run', 'live'), 'document': value('document')}
    try:
        if parsed.path == '/api/recognition-debug/catalog':
            result = debug_catalog(paths, common['route'], common['run_id'])
        elif parsed.path == '/api/recognition-debug/call':
            result = debug_call(paths, **common, key=value('key'))
        elif parsed.path == '/api/recognition-debug/image':
            path = debug_image(paths, **common, key=value('key'), index=int(value('index', '0')))
            content = path.read_bytes()
            mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}[path.suffix.lower()]
            handler.send_response(HTTPStatus.OK)
            handler.send_header('Content-Type', mime)
            handler.send_header('Content-Length', str(len(content)))
            handler.end_headers()
            handler.wfile.write(content)
            return True
        elif parsed.path in {'/api/recognition-debug/source.pdf', '/api/recognition-debug/page.png'}:
            source = debug_source(paths, **common)
            if parsed.path.endswith('page.png'):
                import fitz
                number = int(value('page', '1'))
                with fitz.open(source) as pdf:
                    if not 1 <= number <= len(pdf):
                        raise ValueError('页码超出原件范围')
                    pg = pdf[number - 1]
                    scale = 1800 / max(pg.rect.width, pg.rect.height)
                    body = pg.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png')
                content_type = 'image/png'
            else:
                body, content_type = source.read_bytes(), 'application/pdf'
            handler.send_response(HTTPStatus.OK)
            handler.send_header('Content-Type', content_type)
            handler.send_header('Content-Length', str(len(body)))
            handler.send_header('Cache-Control', 'no-store')
            handler.end_headers()
            handler.wfile.write(body)
            return True
        elif parsed.path == '/api/recognition-debug':
            result = debug_records(paths, **common, stage=value('stage', 'input'),
                page=int(value('page', '0')), q=value('q'), offset=int(value('offset', '0')),
                limit=int(value('limit', '25')), failures=value('failures') == '1')
        else:
            handler.write_json({'error': 'not found'}, status=HTTPStatus.NOT_FOUND)
            return True
        handler.write_json(result)
    except (ValueError, TypeError, OSError, KeyError) as exc:
        handler.write_json({'error': str(exc)}, status=HTTPStatus.BAD_REQUEST)
    return True
