"""Explicitly open development checkpoints; never imported into model prompts."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from pathlib import Path

from .storage import read_json, write_json

OPEN_CASES = {
    'G359': ('open-g359', 'add4ffe1054112eafdb441e490767b9214f1dead8a9af0229f99dc578c59f659'),
    'G209-report': ('open-report', '48c5da9cf568db6af93fee6ce5df0b579f8c511a762b6c5167505a3403c05c0e'),
    'paper': ('open-paper', '01015fc12c9f8a74b7275a5c6d89685fa258ae73d4d184454944157c520c28b1'),
    'guide': ('open-guide', '113cb4d072120fd77e61c5a9b973be4d62df1af0a56c56701bc64c7a1360a928'),
}
FIELDS = {'object', 'field', 'value', 'unit', 'unit_basis', 'scope', 'role', 'condition', 'time', 'qualifier', 'status'}


def dataset_hash(dataset):
    return hashlib.sha256(json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _builtins(paths):
    from .recognition_debug import _store, _documents, _safe_location
    export = read_json(_safe_location(paths.root / 'outputs/知联调试/debug_data.json', paths.root), {})
    result = []
    for case in export.get('cases', []):
        if case.get('id') not in OPEN_CASES:
            continue
        run_id, expected_hash = OPEN_CASES[case['id']]
        if case.get('original_sha256') != expected_hash:
            raise ValueError('开放样例身份与冻结来源不一致')
        run, _ = _store(paths, 'knowledge', run_id)
        docs = _documents(paths, run, run_id)
        if len(docs) != 1:
            continue
        doc = docs[0]
        relative = _store_path(paths, run_id).relative_to(paths.root).as_posix()
        variant = next((v for v in case['variants'] if v['doc']['source_sha256'] == doc['source_sha256']
                       and v['path'].replace('\\', '/') == relative), None)
        if not variant:
            raise ValueError('开放样例预测版本无法对应')
        descriptor = {'route': 'knowledge', 'run_id': run_id, 'document': doc['id'],
            'source_sha256': doc['source_sha256'], 'original_sha256': expected_hash,
            'source_name': case['source_name'], 'page_map': variant.get('page_map', {}),
            'input_pages': doc.get('source_page_count'), 'tested_original_pages': case.get('tested_original_pages', [])}
        points = []
        by_id = {p['id']: p for p in case.get('points', [])}
        latest = {r['point_id']: r.get('after', {}) for r in case.get('latest_review', {}).get('rows', [])}
        for row in case.get('judgments', []):
            point_id = row['point_id']
            expected = copy.deepcopy(by_id.get(point_id, {}).get('expected', {}))
            review = latest.get(point_id, row.get('knowledge', {})) if case['id'] == 'G359' else row.get('knowledge', {})
            # Historical review is bound to the exact saved prediction hash.
            # New predictions only get the generic rule check / review queue.
            points.append({'id': point_id, 'label': ' · '.join(str(expected[k]) for k in ['field', 'value'] if expected.get(k)) or point_id,
                'pages': row.get('pages', []), 'carrier': row.get('carrier', ''), 'expected': expected,
                'description': review.get('reason_zh', review.get('reason', '')), 'checks': [],
                'historical_review': review, 'historical_run_sha256': variant['run_sha256'],
                'expert_validated': False})
        dataset = {'id': 'builtin-' + case['id'].lower(), 'name': case['title'], 'kind': 'open_development',
            'source': descriptor, 'points': points, 'built_in': True,
            'note': '仅限已开放开发检查点。历史结论原样保留且绑定原预测；不作为新模型评分或独立验收。部分旧检查点元数据仍待核。'}
        result.append(dataset)
    return result


def _store_path(paths, run_id):
    from .recognition_debug import OPEN_RUNS
    return paths.root / OPEN_RUNS[run_id][1]


def list_datasets(paths):
    rows = _builtins(paths)
    for file in (paths.output_dir / 'debug-datasets').glob('ds_*.json'):
        if re.fullmatch(r'ds_[a-f0-9]{24}\.json', file.name):
            rows.append(read_json(file))
    return {'datasets': [{**d, 'dataset_hash': dataset_hash(d)} for d in rows],
        'note': '数据集与答案仅用于检查和复核，不参与识别提示词、对象发现或训练。'}


def get_dataset(paths, identifier):
    if not isinstance(identifier, str):
        raise ValueError('无效数据集ID')
    if identifier.startswith('builtin-'):
        dataset = next((d for d in _builtins(paths) if d['id'] == identifier), None)
    elif re.fullmatch(r'ds_[a-f0-9]{24}', identifier):
        dataset = read_json(paths.output_dir / 'debug-datasets' / (identifier + '.json'))
    else:
        dataset = None
    if not dataset:
        raise ValueError('数据集不存在或不在开放开发范围')
    return dataset


def save_dataset(paths, body):
    from .recognition_debug import _document, debug_source
    if body.get('open_development') is not True:
        raise ValueError('只能登记已开放开发资料，不能导入隐藏或曾作为隐藏集的原件')
    name, points, source = body.get('name'), body.get('points'), body.get('source')
    if not isinstance(name, str) or not name.strip() or len(name) > 160 or not isinstance(source, dict):
        raise ValueError('请填写数据集名称并从当前记录选择资料')
    if not isinstance(points, list) or not 1 <= len(points) <= 1000:
        raise ValueError('请提供1～1000个测试点')
    common = {k: source.get(k) for k in ['route', 'run_id', 'document']}
    run, _, doc = _document(paths, **common)
    debug_source(paths, **common)
    validated, seen = [], set()
    count = doc.get('source_page_count', doc.get('pages', 0))
    if isinstance(count, list):
        count = len(count)
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError('测试点必须是对象')
        point_id = point.get('id') or 'p_' + str(index + 1).zfill(4)
        if not isinstance(point_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', point_id) or point_id in seen:
            raise ValueError('测试点ID必须唯一，且只含字母、数字、下划线或短横线')
        seen.add(point_id)
        label = point.get('label', point_id)
        pages = point.get('pages', [])
        if (not isinstance(label, str) or len(label) > 300 or not isinstance(pages, list)
                or any(type(n) is not int or not 1 <= n <= count for n in pages)):
            raise ValueError('测试点名称或物理页码无效')
        checks = point.get('checks', [])
        if not isinstance(checks, list) or len(checks) > 32:
            raise ValueError('每点最多32条匹配条件')
        for check in checks:
            if (not isinstance(check, dict) or check.get('field') not in FIELDS
                    or check.get('op') not in {'equals', 'contains'} or not isinstance(check.get('value'), str)
                    or len(check['value']) > 4000 or check['value'] == ''):
                raise ValueError('条件仅支持事实字段的equals/contains及非空文本；不执行代码或正则')
        expected = point.get('expected', {})
        if not isinstance(expected, dict) or len(json.dumps(expected, ensure_ascii=False)) > 12000:
            raise ValueError('预期结果须为12000字以内的JSON对象')
        if type(point.get('require_evidence', True)) is not bool:
            raise ValueError('require_evidence必须是布尔值')
        description = point.get('description', '')
        if not isinstance(description, str) or len(description) > 8000:
            raise ValueError('测试点描述最多8000字')
        validated.append({'id': point_id, 'label': label, 'pages': sorted(set(pages)),
            'expected': expected, 'checks': checks, 'require_evidence': point.get('require_evidence', True),
            'description': description})
    dataset = {'id': 'ds_' + uuid.uuid4().hex[:24], 'name': name.strip(), 'built_in': False,
        'kind': 'open_development', 'source': {**common, 'source_sha256': doc['source_sha256'],
            'original_sha256': doc['source_sha256'], 'source_name': Path(doc['path']).name,
            'page_map': {}, 'input_pages': count}, 'points': validated,
        'note': '用户定义的开放开发检查点；自动匹配与本地人工判定分开记录。修改定义请另存新数据集版本。'}
    write_json(paths.output_dir / 'debug-datasets' / (dataset['id'] + '.json'), dataset)
    return {**dataset, 'dataset_hash': dataset_hash(dataset)}


def execution_pages(dataset, doc, point):
    source = dataset['source']
    if doc['source_sha256'] == source.get('original_sha256'):
        return point.get('pages', [])
    if doc['source_sha256'] != source['source_sha256']:
        raise ValueError('识别资料哈希与数据集不一致，不能混测不同原件')
    inverse = {v: int(k) for k, v in source.get('page_map', {}).items()}
    if inverse:
        if any(n not in inverse for n in point.get('pages', [])):
            raise ValueError('检查点页面不在本次输入PDF中')
        return sorted({inverse[n] for n in point.get('pages', [])})
    return point.get('pages', [])
