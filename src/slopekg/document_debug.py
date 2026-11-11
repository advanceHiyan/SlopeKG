"""Read-only fourth-route views. Every trace is bound to a saved run and IDs.

No recognition, matching by answer text, or new model requests happen here.
The full provider exchange remains available through the shared call endpoint.
"""
import json


def _evidence(rows):
    return [e for row in rows for key in ('evidence', 'parent_evidence')
            for e in row.get(key, [])]


def document_rows(run, doc, stage):
    blocks = {b['id']: b for b in doc.get('blocks', [])}
    native = {r['id']: r for p in doc.get('pages', []) for r in p.get('native_sources', [])}
    if stage == 'input':
        return [{'id': 'run', **{k: v for k, v in run.items() if k not in {'documents', 'receipts'}},
            'document': {k: doc.get(k) for k in ('id', 'path', 'source_sha256', 'source_page_count',
                'selection', 'status', 'workflow_complete', 'zone_review_mode', 'coverage',
                'dossier_plan', 'completed_batches', 'unresolved')}, 'stages': doc.get('stages', [])}]
    if stage == 'native':
        return [dict(r, page=p['page']) for p in doc.get('pages', []) for r in p.get('native_sources', [])]
    if stage == 'blocks':
        return [{**b, 'native_sources': [native[i] for i in b.get('source_ids', []) if i in native]}
                for b in blocks.values()]
    if stage == 'groups':
        return [{**g, 'pages': sorted({blocks[i]['page'] for i in g.get('source_block_ids', []) if i in blocks}),
                 'source_blocks': [blocks[i] for i in g.get('source_block_ids', []) if i in blocks],
                 'structure_links': [link for link in doc.get('structure_links', [])
                    if link.get('from') in g.get('source_block_ids', []) or link.get('to') in g.get('source_block_ids', [])]}
                for g in doc.get('groups', [])]
    if stage == 'repairs':
        return [*({'record_type': 'applied_repair', **r} for r in doc.get('source_repairs', [])),
                *({'record_type': 'repair_queue', 'status': 'pending', **r} for r in doc.get('repair_queue', []))]
    if stage == 'reviews':
        return [{'record_type': key, 'batch': n, **r} for n, batch in enumerate(doc.get('dossier_reviews', []))
                for key in ('reviews', 'additions') for r in batch.get(key, [])] + [
                {'record_type': 'review_issues', 'batch': n, 'issues': batch['issues']}
                for n, batch in enumerate(doc.get('dossier_reviews', [])) if batch.get('issues')]
    if stage == 'audit':
        audit = doc.get('cross_group_audit', {})
        return [{'record_type': 'cross_group_flag', 'status': 'pending', **r} for r in audit.get('flags', [])] + (
            [{'record_type': 'cross_group_issues', 'issues': audit['issues']}] if audit.get('issues') else [])
    if stage in ('facts', 'claims'):
        return doc.get(stage, [])
    if stage == 'errors':
        return [*({'record_type': 'document_error', 'error': r} for r in doc.get('errors', [])),
                *({'record_type': 'stage_failure', **r} for r in doc.get('stages', []) if r.get('status') != 'completed'),
                *({'record_type': 'repair_queue', 'status': 'pending', **r} for r in doc.get('repair_queue', [])),
                *({'record_type': 'unresolved', 'status': 'pending', 'detail': r} for r in doc.get('unresolved', []))]
    return []


def _context(inp):
    try:
        result = json.loads(inp['prompt'].split('\n输入：', 1)[1])
        return result if isinstance(result, dict) else {}
    except (KeyError, IndexError, ValueError):
        return {}


def _claim_id(document, raw):
    # document-v0.2 records the hash after this explicit kind normalization.
    # This is ID lookup only; never rerun extraction or alter the saved object.
    from .model_client import content_hash
    value = dict(raw)
    if value.get('kind') in {'calculation', 'requirement'} and value.get('kind') == value.get('role') and not value.get('target'):
        value.update(original_kind=value['kind'], kind='fact')
    return 'd_' + content_hash([document, value])[:22]


def _provider_answer(paths, folder, key):
    from .recognition_debug import _read
    exchange = _read(folder / 'exchanges' / (key + '.json'), paths.root)
    for attempt in reversed(exchange.get('attempts', [])):
        try:
            message = attempt['response']['choices'][0]['message']['content']
            answer = json.loads(message)
            if isinstance(answer, dict):
                return answer
        except (KeyError, TypeError, IndexError, ValueError):
            continue
    return None


def document_trace(paths, *, route='document', run_id='live', document='', claim_id=''):
    from .recognition_debug import _document, _read, _call_refs, _clean
    if route != 'document':
        raise ValueError('该追查入口只用于整读记录')
    run, folder, doc = _document(paths, route, run_id, document)
    fact = next((f for f in [*doc.get('facts', []), *doc.get('claims', [])] if f.get('id') == claim_id), None)
    if fact is None:
        raise ValueError('候选不属于当前资料记录')
    ids = {claim_id, *fact.get('duplicate_claim_ids', [])}
    claims = [c for c in doc.get('claims', []) if c.get('id') in ids]
    evidence = _evidence([fact, *claims])
    block_ids = {e.get('block_id') for e in evidence}
    blocks = [b for b in doc.get('blocks', []) if b['id'] in block_ids]
    pages = {e['page'] for e in evidence if e.get('page')}
    pages.update(b['page'] for b in blocks)
    group_ids = {i for c in [fact, *claims] for key in ('group_ids', 'origin_groups') for i in c.get(key, [])}
    groups = [g for g in doc.get('groups', []) if g['id'] in group_ids]
    calls = []
    for ref in _call_refs(paths, run, folder, doc):
        key = ref['id']
        inp = _read(folder / 'inputs' / (key + '.json'), paths.root)
        ctx = _context(inp)
        cached = _read(folder / 'cache' / (key + '.json'), paths.root)
        answer = cached.get('answer') or {}
        valid = answer.get('_document_validated', answer)
        raw = answer.get('_document_raw', cached.get('raw_answer')) or {}
        if not isinstance(raw, dict):
            raw = {}
        if not isinstance(valid, dict):
            valid = {}
        exact = [c for k in ('claims', 'additions') for c in valid.get(k, []) if c.get('id') in ids]
        raw_claims = [c for k in ('claims', 'additions') for c in raw.get(k, [])
                      if _claim_id(document, c) in ids]
        reviews = [r for r in valid.get('reviews', []) if r.get('claim_id') in ids]
        raw_reviews = [r for r in raw.get('reviews', []) if r.get('claim_id') in ids]
        flags = [r for r in valid.get('flags', []) if ids.intersection(r.get('claim_ids', []))]
        input_claims = [c for k in ('claims', 'claim_index') for c in ctx.get(k, []) if c.get('id') in ids]
        call_groups = {g['id'] for g in ctx.get('groups', [])}
        call_blocks = {b['id'] for b in ctx.get('blocks', [])}
        stage = ref['stage']
        structural = stage in ('document_reconstruct', 'document_repair_structure') and any(
            p.get('page') in pages for p in ctx.get('pages', []))
        planning = stage in ('document_zone', 'document_zone_review')
        batch_related = stage == 'document_extract' and bool(group_ids & call_groups)
        if not (exact or reviews or flags or input_claims or raw_claims or structural or planning or batch_related):
            continue
        # Legacy validators mutated raw_answer/_document_raw in place. Only
        # supplier content is labelled original; cache-only detail is explicit.
        origin = 'cache_derived'
        if stage in ('document_extract', 'document_reconcile'):
            provider = _provider_answer(paths, folder, key)
            if provider is not None:
                raw = provider
                origin = 'provider_response_content'
            raw_claims = [c for k in ('claims', 'additions') for c in raw.get(k, []) if _claim_id(document, c) in ids]
            raw_reviews = [r for r in raw.get('reviews', []) if r.get('claim_id') in ids]
        coverage = []
        for g in groups:
            declared = g.get('source_block_ids', [])
            coverage.append({'group_id': g['id'], 'name': g['name'], 'group_in_request': g['id'] in call_groups,
                'included_block_ids': [i for i in declared if i in call_blocks],
                'absent_block_ids': [i for i in declared if i not in call_blocks]})
        calls.append({**ref, 'association': 'exact_claim_id' if exact or reviews or input_claims or raw_claims or flags
                      else 'page_context' if structural else 'document_plan' if planning else 'group_context_only',
            'raw_detail_origin': origin, 'input_claims': input_claims, 'raw_claims': raw_claims, 'validated_claims': exact,
            'raw_reviews': raw_reviews, 'validated_reviews': reviews, 'audit_flags': flags,
            'request_groups': ctx.get('groups', []), 'group_coverage': coverage if stage in ('document_extract', 'document_reconcile') else [],
            'request_block_count': len(call_blocks), 'request_block_ids': sorted(call_blocks),
            'request_pages': [p.get('page') for p in ctx.get('pages', [])],
            'context_available': bool(ctx)})
    native_ids = {i for b in blocks for i in b.get('source_ids', [])}
    source_pages = [{'page': p['page'], 'width': p.get('width'), 'height': p.get('height'),
                     'native_sources': [r for r in p.get('native_sources', []) if r['id'] in native_ids]}
                    for p in doc.get('pages', []) if p['page'] in pages]
    return _clean({'claim_id': claim_id, 'run_id': run_id, 'document': document,
        'source_sha256': doc.get('source_sha256'), 'selection': doc.get('selection'),
        'final': fact, 'claims': claims, 'source_pages': source_pages, 'blocks': blocks, 'groups': groups,
        'links': [r for r in doc.get('structure_links', []) if r.get('from') in block_ids or r.get('to') in block_ids],
        'calls': calls, 'repair_queue': [r for r in doc.get('repair_queue', [])
            if ids.intersection(r.get('claim_ids', [])) or r.get('page') in pages],
        'notes': ['追查按保存的候选ID、合并ID、分组ID与页码关联；“同组上下文”不证明该调用产生了此候选。',
                  '分组覆盖展示最终分组的来源块是否出现在该次保存输入中；缺席可能来自分批或规划变化，不自动判为错误。',
                  '完整请求和供应商原始回复通过“完整输入输出”打开；历史缺失不会补造。']})
