"""Durable slope dossiers and immutable recognition sources, independent of assessment.

Recognition stores candidates, never approved field values or risk conclusions.
Cross-document identity is confirmed by a person; source-local reruns reuse bindings.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import shutil
import sqlite3
import uuid

from .storage import read_json, read_jsonl


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def folder(paths):
    return paths.output_dir / 'slope-library'


@contextmanager
def database(paths):
    base = folder(paths)
    base.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(base / 'library.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
          CREATE TABLE IF NOT EXISTS slopes(id TEXT PRIMARY KEY, name TEXT NOT NULL,
            identity_status TEXT NOT NULL, created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS sources(hash TEXT PRIMARY KEY, name TEXT NOT NULL, size INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, route TEXT NOT NULL, run_id TEXT NOT NULL,
            source_hash TEXT NOT NULL REFERENCES sources(hash), created_at TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS bindings(id TEXT PRIMARY KEY, source_hash TEXT NOT NULL,
            label TEXT NOT NULL, slope_id TEXT REFERENCES slopes(id), revision INTEGER NOT NULL DEFAULT 0,
            group_id TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
            binding_id TEXT NOT NULL REFERENCES bindings(id), payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, binding_id TEXT NOT NULL,
            created_at TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS intakes(id INTEGER PRIMARY KEY, route TEXT NOT NULL,
            run_id TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS records_binding ON records(binding_id);
          CREATE INDEX IF NOT EXISTS snapshots_run ON snapshots(route,run_id);
        ''')
        if 'group_id' not in {row[1] for row in db.execute('PRAGMA table_info(bindings)')}:
            db.execute("ALTER TABLE bindings ADD COLUMN group_id TEXT NOT NULL DEFAULT ''")
        from .slope_identity import schema
        schema(db)
        with db:
            yield db
    finally:
        db.close()


def legacy_slopes(paths):
    # No derived risk/evaluation data is read or migrated here.
    return [n for n in read_json(paths.graph_json, {}).get('nodes', []) if n.get('type') == 'Slope']


def _seed_legacy(db, paths):
    from .slope_identity import seed
    for row in legacy_slopes(paths):
        db.execute('INSERT OR IGNORE INTO slopes VALUES(?,?,?,?)',
                   (row['id'], row.get('label') or row.get('name') or row['id'], 'legacy', now()))
        seed(db, row)


def legacy_fields(paths, slope_id, nodes=None):
    """Read the existing published fields without migrating or approving them."""
    node = (nodes if nodes is not None else {s['id']: s for s in legacy_slopes(paths)}).get(slope_id)
    if not node:
        return []
    labels = {'slope_id':'既有业务标识（可能为自动生成编号）', 'source_alias':'原资料名称',
              'route_code_cache':'路线', 'start_station_raw':'起点桩号', 'end_station_raw':'终点桩号',
              'side':'左右侧', 'slope_length_m':'坡长（米）', 'slope_height_raw':'坡高原值',
              'slope_gradient_raw':'坡度原值', 'material_nature':'物质类型', 'slope_structure_code':'坡体结构',
              'slope_aspect_deg':'坡向（度）', 'vegetation_condition':'植被记录',
              'source_disagreements':'既有来源分歧', 'review_status':'既有核对状态'}
    return [{'field': k, 'label': label, 'value': node.get('props', {}).get(k)} for k, label in labels.items()
            if node.get('props', {}).get(k) not in (None, '', [], {})]


def _is_slope(label, kind='', parent=''):
    return (bool(re.search(r'边坡|滑坡|危岩|崩塌|slope|landslide', str(kind)+' '+label, re.I))
            and not re.search(r'照片|photo|figure|diagram|sketch|unresolved|concept|calculation|case|structural|retaining|document|图件|表格|范围|概念|判定|指标|工况|结构面', str(kind), re.I)
            and not re.search(r'断面|剖面|钻孔|照片|现场照|表格|分区|坡脚|坡顶|工程|监测点|岩层|地层|影响范围|空间范围|发育程度|分类|计算|工况|^[图表]\s*[\d一二三四五六七八九十]', label))


def _root_object(obj, objects):
    """Follow explicit same-document part-of links, retaining each child's scope."""
    seen = set()
    current = obj
    root = None
    while current and current['key'] not in seen:
        seen.add(current['key'])
        if _is_slope(current['label'], current['kind']):
            root = current
        parents = [o for o in objects if o['key'] == current['parent'] or o['label'] == current['parent']]
        current = parents[0] if len(parents) == 1 else None
    return root


def _binding_id(sha, obj, objects):
    parent_name = next((o['label'] for o in objects if o['key'] == obj['parent']), obj['parent'])
    ambiguous = sum(o['label'] == obj['label'] and o['parent'] == obj['parent'] for o in objects) > 1
    return digest([sha, obj['label'], parent_name, obj['key'] if ambiguous else ''])


def _evidence(fact):
    evidence = fact.get('evidence', [])
    if isinstance(evidence, dict):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    if not evidence and fact.get('page'):
        evidence = [{'page': fact['page'], 'quote': fact.get('quote', ''), 'bbox': fact.get('bbox')}]
    return [e for e in evidence if isinstance(e, dict)]


def normalize_objects(doc):
    """Route adapters preserve original values and ownership; no field inference."""
    from .schema import SLOPE_FIELDS
    field_labels = {f['code']: f['label_zh'] for f in SLOPE_FIELDS}
    groups = {}
    for obj in doc.get('objects', doc.get('entities', [])):
        label = str(obj.get('name') or obj.get('label') or obj.get('id') or '').strip()
        key = str(obj.get('id') or label)
        if key:
            groups[key] = {'key': key, 'label': label, 'kind': obj.get('type', obj.get('kind', '')),
                           'parent': obj.get('parent_object', ''), 'object': obj, 'facts': [], 'issues': []}
    for fact in doc.get('facts', []):
        key = str(fact.get('entity_id') or fact.get('object') or fact.get('subject') or '未明确对象')
        if key not in groups:
            matches = [k for k, g in groups.items() if g['label'] == key]
            if len(matches) == 1:
                key = matches[0]
        group = groups.setdefault(key, {'key': key, 'label': str(fact.get('object') or fact.get('subject') or key),
            'kind': '', 'parent': fact.get('parent_subject', fact.get('parent_object', '')),
            'object': {}, 'facts': [], 'issues': []})
        issues = []
        if fact.get('document_id', doc['id']) != doc['id']:
            issues.append('候选标记的来源文件与当前文件不同')
        evidence = _evidence(fact)
        if not evidence:
            issues.append('没有可定位的原页证据')
        for e in evidence:
            if e.get('document_id', doc['id']) != doc['id']:
                issues.append('原文证据指向另一份文件')
            if not str(e.get('quote', '')).strip():
                issues.append('缺少可核对的原文引句')
        if fact.get('status') == 'rejected' or fact.get('model_status', fact.get('status')) in {'conflict','unclear','inferred'}:
            issues.append('识别记录已有冲突、疑点或推断标记')
        field = fact.get('field')
        group['facts'].append({'raw': fact, 'field_label': field_labels.get(field, field) if isinstance(field, str) else '',
                               'evidence': evidence, 'issues': sorted(set(issues)),
                               'admission': 'withheld' if issues else 'candidate'})
    return list(groups.values())


def basic_documents(paths):
    """Keep Basic's source-specific property assertions, not its merged values."""
    docs = read_jsonl(paths.parsed_dir / 'documents.jsonl')
    facts = read_jsonl(paths.extracted_dir / 'property_assertions.jsonl')
    slopes = {s.get('station'): s for s in read_jsonl(paths.extracted_dir / 'slopes.jsonl')}
    blocks = {b['id']: b for name in ['text_blocks', 'tables']
              for b in read_jsonl(paths.parsed_dir / (name+'.jsonl'))}
    pages = read_jsonl(paths.parsed_dir / 'pages.jsonl')
    for doc in docs:
        doc['facts'] = []
        doc['objects'] = []
        doc['archived_figures'] = [a for p in pages if p.get('document_id') == doc['id']
                                   for a in p.get('archived_figures', [])]
        for f in facts:
            local_evidence = []
            for e in _evidence(f):
                block = blocks.get(e.get('block_id'), {})
                if (block.get('document_id') or e.get('document_id') or f.get('document_id') or
                        f.get('source_document_id')) == doc['id']:
                    quote = e.get('quote') or block.get('text') or '\n'.join(
                        ' | '.join(str(c or '') for c in row) for row in block.get('rows', []))
                    local_evidence.append({**e, 'document_id': doc['id'], 'quote': quote,
                                           'page': e.get('page', block.get('page'))})
            if not local_evidence:
                continue
            slope = slopes.get(f.get('subject_key'), {})
            label = f.get('slope_name') or slope.get('name') or slope.get('station') or f.get('subject_key') or '未明确对象'
            if not any(o['id'] == label for o in doc['objects']):
                doc['objects'].append({'id': label, 'name': label, 'type': '边坡' if slope else '未确定',
                                       'document_id': doc['id']})
            doc['facts'].append({**f, 'document_id': doc['id'], 'object': label,
                                'field': f.get('field') or f.get('property_code') or f.get('property'),
                                'evidence': local_evidence})
    return docs


def ingest(paths, run, *, route=None, run_id=None):
    route = route or run.get('recognizer', 'basic')
    route = 'visual' if route == 'multimodal' else route
    if route not in {'agent', 'basic', 'knowledge', 'visual', 'document'}:
        raise ValueError('无效识别路线')
    if run.get('prepare_only') or run.get('processing_status') in {'prepared', 'preparing', 'running', 'ready'}:
        return {'status': 'waiting', 'message': '等待识别结果保存', 'snapshots': []}
    if run.get('test_only') and not paths.assessment_test_only:
        raise ValueError('隔离测试任务不能写入正式边坡资料库')
    run_id = run_id or run_key(run)
    docs = basic_documents(paths) if route == 'basic' else run.get('documents', [])
    receipts, errors = [], []
    if not docs:
        errors.append({'document': '本次任务', 'reason': '还没有可入库的资料记录，请先识别或接续原任务'})
    for doc in docs:
        try:
            receipts.append(_ingest_document(paths, run, doc, route, run_id))
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as exc:
            errors.append({'document': doc.get('name', doc.get('id', '')), 'reason': str(exc)})
    receipt = {'status': 'partial' if errors else 'stored', 'run_id': run_id, 'route': route,
            'snapshots': receipts, 'errors': errors,
            'message': (f'已保存 {len(receipts)} 份识别来源；{len(errors)} 份来源入库受阻，请核对后重试' if errors else
                        f'已保存 {len(receipts)} 份识别来源；归属和候选值可在边坡资料库核对'),
            'url': '/web/slope-library.html'}
    with database(paths) as db:
        db.execute('INSERT INTO intakes(route,run_id,payload) VALUES(?,?,?)', (route, run_id, encoded(receipt)))
    return receipt


def run_key(run):
    return run.get('run_id') or run.get('started_at') or run.get('updated_at') or run.get('built_at') or 'live'


def receipt_for_run(paths, run_id, route='agent'):
    if not (folder(paths)/'library.sqlite3').is_file():
        return None
    with database(paths) as db:
        row = db.execute('SELECT payload FROM intakes WHERE route=? AND run_id=? ORDER BY id DESC LIMIT 1',
                         (route, run_id)).fetchone()
        return json.loads(row[0]) if row else None


def _ingest_document(paths, run, doc, route, run_id):
    from .recognition_debug import _safe_location, DENIED_HASHES
    source = _safe_location(doc.get('path', ''), paths.root)
    if not source.is_relative_to(paths.raw_dir.resolve()) or source.suffix.lower() != '.pdf':
        raise ValueError('只接收业务资料目录中的 PDF')
    sha = doc.get('source_sha256')
    if not re.fullmatch('[0-9a-f]{64}', str(sha)) or sha in DENIED_HASHES or file_hash(source) != sha:
        raise ValueError('原 PDF 与识别时的指纹不一致，保留识别记录，暂不入库')
    objects = normalize_objects(doc)
    foreign_objects = {o.get('id'): o for d in run.get('documents', []) if d.get('id') != doc['id']
                       for o in d.get('objects', [])}
    for obj in objects:
        if not obj['object'] and obj['key'] in foreign_objects:
            foreign = foreign_objects[obj['key']]
            obj.update(label='归属冲突：'+str(foreign.get('name') or '另一份文件中的对象'), kind='unresolved')
            for fact in obj['facts']:
                fact['issues'].append('候选引用的对象属于另一份文件')
    if not objects:
        objects = [{'key': '__document__', 'label': source.name+' · 文档附件', 'kind': 'document',
                    'parent': '', 'object': {}, 'facts': [], 'issues': ['尚无可归属的边坡对象']}]
    import fitz
    with fitz.open(source) as pdf:
        page_count = len(pdf)
    selected = doc.get('selection', {}).get('selected_pages') or doc.get('selected_pages')
    selected = set(selected or range(1, page_count+1))
    assets = list(doc.get('archived_figures', []))
    # Embedded visual objects are preserved without interpreting their content.
    # A full-page raster can be a scanned survey: it is never skipped as a figure.
    with fitz.open(source) as pdf:
        from .recognition_scope import figure
        for number in sorted(selected):
            if type(number) is not int or not 1 <= number <= page_count:
                continue
            page = pdf[number-1]
            for image in page.get_image_info():
                rect = fitz.Rect(image['bbox']) * page.rotation_matrix
                rect &= page.rect
                if rect.is_empty or rect.get_area()/page.rect.get_area() > .85:
                    continue
                box = [max(0, rect.x0/page.rect.width), max(0, rect.y0/page.rect.height),
                       min(1, rect.x1/page.rect.width), min(1, rect.y1/page.rect.height)]
                if not any(a.get('page') == number and a.get('bbox') and
                           all(abs(a['bbox'][i]-box[i]) < .025 for i in range(4)) for a in assets):
                    assets.append(figure(number, box, kind='embedded_visual_unclassified'))
    for obj in objects:
        for fact in obj['facts']:
            for evidence in fact['evidence']:
                page = evidence.get('physical_page', evidence.get('page'))
                if type(page) is not int or page not in selected or not 1 <= page <= page_count:
                    fact['issues'].append('证据页码不在本次识别范围内')
            fact_id = fact['raw'].get('id')
            if fact_id and any(str(error).endswith(': '+str(fact_id)) for error in run.get('errors', [])):
                fact['issues'].append('原任务已记录这条候选的交付校验错误')
            if fact['raw'].get('role') == 'figure':
                fact['issues'].append('图内信息仅归档，不作为识别事实采用')
            fact['admission'] = 'withheld' if fact['issues'] else 'candidate'
    payload = {'document_id': doc['id'], 'name': source.name, 'source_sha256': sha,
        'route': route, 'run_id': run_id, 'status': doc.get('status', run.get('processing_status')),
        'recognized_at': run.get('started_at', run.get('built_at', '')),
        'document_date': doc.get('document_date', ''),
        'selection': doc.get('selection', {}), 'selected_pages': doc.get('selected_pages', []),
        'errors': [*run.get('errors', []), *doc.get('errors', [])], 'coverage': doc.get('coverage', {}),
        'objects': objects, 'figures': assets,
        'image_policy': run.get('image_policy', 'historical_unspecified')}
    snapshot_id = digest(payload)
    target = folder(paths) / 'sources' / (sha+'.pdf')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if file_hash(target) != sha:
            raise ValueError('已归档原件指纹损坏，请恢复后重试')
    else:
        temp = target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        shutil.copyfile(source, temp)
        if file_hash(temp) != sha:
            raise ValueError('复制期间原 PDF 发生变化，未入库')
        temp.replace(target)
    with database(paths) as db:
        _seed_legacy(db, paths)
        db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)', (sha, source.name, target.stat().st_size))
        db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?)',
                   (snapshot_id, route, run_id, sha, now(), encoded(payload)))
        for obj in objects:
            # Save source-local observations first. Only a reviewed binding may
            # resolve an actual slope; a new recognition never creates a master.
            binding = _binding_id(sha, obj, objects)
            existing = db.execute('SELECT id FROM bindings WHERE id=?', (binding,)).fetchone()
            if not existing:
                root = _root_object(obj, objects)
                group_id = _binding_id(sha, root, objects) if root else binding
                known = db.execute('SELECT slope_id FROM bindings WHERE id=?', (group_id,)).fetchone()
                slope_id = known['slope_id'] if known else None
                db.execute('INSERT INTO bindings(id,source_hash,label,slope_id,revision,group_id) VALUES(?,?,?,?,0,?)',
                           (binding, sha, obj['label'], slope_id, group_id))
            db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?,?)',
                       (digest([snapshot_id, obj['key']]), snapshot_id, binding, encoded(obj)))
    return {'id': snapshot_id, 'document_id': doc['id'], 'objects': len(objects),
            'facts': sum(len(o['facts']) for o in objects), 'url': '/web/slope-library.html?snapshot='+snapshot_id}


def ingest_saved(paths, body):
    from .recognition_debug import _store
    route, run_id = body.get('route', ''), body.get('run', 'live')
    if route == 'agent':
        from .agent_service import select_run
        run = select_run(paths, run_id if run_id != 'live' else '')
    else:
        run, _ = _store(paths, route, run_id)
    if not run:
        raise ValueError('没有已保存的识别结果')
    return ingest(paths, run, route=route)


def catalog(paths, *, snapshot='', run_id='', route=''):
    if run_id == 'live' and route:
        from .recognition_debug import _store
        current, _ = _store(paths, route, 'live')
        run_id = run_key(current)
    with database(paths) as db:
        _seed_legacy(db, paths)
        rows = [dict(r) for r in db.execute('''SELECT s.*, count(DISTINCT b.id) AS sources,
                  count(DISTINCT r.id) AS versions FROM slopes s LEFT JOIN bindings b ON b.slope_id=s.id
                  LEFT JOIN records r ON r.binding_id=b.id
                  WHERE s.identity_status!='pending' OR b.id IS NOT NULL
                  GROUP BY s.id ORDER BY (count(r.id)>0) DESC,s.created_at DESC,s.name''')]
        pending_objects = [dict(r) for r in db.execute('SELECT * FROM bindings WHERE slope_id IS NULL ORDER BY label')]
        groups = {}
        for item in pending_objects:
            groups.setdefault(item['group_id'] or item['id'], []).append(item)
        pending = [{**next((r for r in members if r['id'] == group), members[0]),
                    'source_objects': len(members)} for group, members in groups.items()]
        from .slope_identity import profile
        legacy = {s['id']: s for s in legacy_slopes(paths)}
        for row in rows:
            row['identity'] = profile(db, row['id'])
            row['legacy_fields'] = len(legacy_fields(paths, row['id'], legacy))
        snapshots = [dict(r) for r in db.execute('SELECT id,route,run_id,source_hash,created_at FROM snapshots ORDER BY created_at DESC')]
        selected = {r['id'] for r in snapshots if (snapshot and r['id'] == snapshot) or
                    (run_id and r['run_id'] == run_id and (not route or r['route'] == route))}
        if snapshot or run_id:
            bindings = {r[0] for r in db.execute('SELECT DISTINCT binding_id FROM records WHERE snapshot_id IN ('+
                         ','.join('?' for _ in selected)+')', tuple(selected))} if selected else set()
            slope_ids = {r[0] for r in db.execute('SELECT slope_id FROM bindings WHERE id IN ('+
                          ','.join('?' for _ in bindings)+')', tuple(bindings))} if bindings else set()
            rows = [r for r in rows if r['id'] in slope_ids]
            pending = [r for r in pending if r['id'] in bindings or any(m['id'] in bindings for m in groups.get(r['group_id'], []))]
        receipts = [json.loads(r[0]) for r in db.execute('SELECT payload FROM intakes WHERE run_id=?'+
                    (' AND route=?' if route else '')+' ORDER BY id DESC LIMIT 10',
                    (run_id, route) if route else (run_id,))] if run_id else []
        shown_snapshots = [r for r in snapshots if r['id'] in selected] if snapshot or run_id else snapshots
        return {'slopes': rows, 'unassigned': pending, 'snapshots': shown_snapshots, 'receipts': receipts,
                'counts': {'slopes': len(rows), 'unassigned': len(pending), 'snapshots': len(shown_snapshots)}}


def detail(paths, *, slope_id='', binding_id=''):
    with database(paths) as db:
        _seed_legacy(db, paths)
        slope = db.execute('SELECT * FROM slopes WHERE id=?', (slope_id,)).fetchone() if slope_id else None
        if slope_id:
            bindings = [dict(r) for r in db.execute('SELECT * FROM bindings WHERE slope_id=?', (slope_id,))]
        else:
            selected = db.execute('SELECT * FROM bindings WHERE id=?', (binding_id,)).fetchone()
            bindings = [dict(r) for r in db.execute('SELECT * FROM bindings WHERE group_id=? AND slope_id IS ? ORDER BY (id=?) DESC,id',
                        (selected['group_id'], selected['slope_id'], selected['group_id']))] if selected else []
        if not slope and not bindings:
            raise KeyError('找不到这份边坡档案或待归属资料')
        sources = []
        for binding in bindings:
            records = db.execute('''SELECT r.*,s.route,s.run_id,s.source_hash,s.created_at,s.payload AS snapshot
                FROM records r JOIN snapshots s ON r.snapshot_id=s.id WHERE binding_id=?
                ORDER BY s.created_at DESC,r.id''', (binding['id'],)).fetchall()
            versions = []
            for row in records:
                snap = json.loads(row['snapshot']);obj = json.loads(row['payload'])
                for index, asset in enumerate(snap['figures']):
                    asset['image_url'] = '/api/slope-library/figure?snapshot='+row['snapshot_id']+'&index='+str(index)
                for fact in obj['facts']:
                    for e in fact['evidence']:
                        page = e.get('physical_page', e.get('page'))
                        if type(page) is int and page > 0:
                            e['image_url'] = '/api/slope-library/page?source='+row['source_hash']+'&page='+str(page)
                versions.append({'id': row['snapshot_id'], 'route': row['route'], 'run_id': row['run_id'],
                    'created_at': row['created_at'], 'document': snap['name'], 'object': obj,
                    'recognized_at': snap.get('recognized_at', ''), 'document_date': snap.get('document_date', ''),
                    'errors': snap['errors'], 'figures': snap['figures'],
                    'document_id': snap['document_id'],
                    'pdf_url': '/api/slope-library/source?source='+row['source_hash']})
            history = [json.loads(r[0]) for r in db.execute('SELECT payload FROM history WHERE binding_id=? ORDER BY id DESC', (binding['id'],))]
            from .slope_identity import profile, source_identity, compare
            identity_issues = []
            identity_ready = False
            if binding['slope_id']:
                master = profile(db, binding['slope_id'])
                observed = source_identity(db, binding)
                identity_issues = [*observed['conflicts'], *compare(observed['attributes'], master['attributes'])[1]]
                latest = history[0] if history else {}
                if latest.get('target_revision') != master['revision']:
                    identity_issues.append('边坡身份资料已更新或缺少核对版本，请重新核对归属')
                if latest.get('source_identity') != observed['attributes']:
                    identity_issues.append('来源身份线索已变化或未留核对记录，请重新核对归属')
                fresh = (latest.get('target_revision') == master['revision'] and
                         latest.get('source_identity') == observed['attributes'])
                acknowledged = bool(latest.get('conflict_reason')) and fresh
                identity_ready = bool(history) and fresh and (not identity_issues or acknowledged)
            sources.append({**binding, 'versions': versions, 'history': history,
                            'identity_ready': identity_ready, 'identity_issues': identity_issues})
        from .slope_identity import profile
        return {'slope': dict(slope) if slope else None, 'sources': sources,
                'identity': profile(db, slope_id) if slope else None,
                'legacy_fields': legacy_fields(paths, slope_id) if slope else []}


def bind(paths, body):
    from .slope_identity import (IdentityConflict, reviewer, source_identity, proposals, profile,
                                 validate_attributes, save_profile, compare, MATCHING_VERSION)
    actor, reason = reviewer(body)
    with database(paths) as db:
        db.execute('BEGIN IMMEDIATE')
        _seed_legacy(db, paths)
        old = db.execute('SELECT * FROM bindings WHERE id=?', (body.get('binding_id'),)).fetchone()
        if not old:
            raise KeyError('找不到待核对来源')
        if type(body.get('revision')) is not int or old['revision'] != body['revision']:
            raise IdentityConflict('归属已被其他操作修改，请刷新后再核对')
        related = [old]
        if body.get('include_related') is True and old['group_id']:
            related = db.execute('SELECT * FROM bindings WHERE group_id=? AND slope_id IS ?',
                                  (old['group_id'], old['slope_id'])).fetchall()
            expected = body.get('related_revisions', {})
            if not isinstance(expected, dict) or any(expected.get(r['id']) != r['revision'] for r in related):
                raise IdentityConflict('同一来源边坡的子对象已变化，请刷新后核对完整范围')
        target = body.get('slope_id', '')
        source = source_identity(db, old)
        candidates = proposals(db, source, target_id=target)
        decision = {'matching_version': MATCHING_VERSION, 'source_identity': source['attributes']}
        if body.get('release') is True:
            target = None
            decision['action'] = 'release_source_identity'
        elif not target:
            label = str(body.get('name', '')).strip()
            if not label or len(label) > 250:
                raise ValueError('请填写实际边坡名称或编号')
            attrs = validate_attributes(body.get('identity', {}))
            conflicts = [*source['conflicts'], *compare(source['attributes'], attrs)[1]]
            if conflicts and not (body.get('conflict_confirmed') is True and str(body.get('conflict_reason', '')).strip()):
                raise IdentityConflict('新身份与来源存在冲突：'+'；'.join(conflicts)+'。请填写处理依据并明确确认')
            duplicate_source = {'label': label, 'attributes': attrs}
            duplicates = [r for r in proposals(db, duplicate_source) if r['rank'] >= 70 and not r['conflicts']]
            if duplicates and not (body.get('distinct_confirmed') is True and str(body.get('distinct_reason', '')).strip()):
                raise IdentityConflict('已有相似边坡，请优先关联；确为不同边坡时请填写区分依据并明确确认')
            target = 'slope-'+uuid.uuid4().hex
            db.execute('INSERT INTO slopes VALUES(?,?,?,?)', (target, label, 'confirmed', now()))
            save_profile(db, target, {**body, 'revision': 0, 'attributes': attrs, 'name': label})
            decision.update(action='create_and_confirm_identity', distinct_reason=body.get('distinct_reason', ''),
                            conflicts=conflicts, conflict_reason=body.get('conflict_reason', ''), target_revision=1)
        else:
            identity = profile(db, target)
            if type(body.get('target_revision')) is not int or body['target_revision'] != identity['revision']:
                raise IdentityConflict('目标边坡身份已变化，请重新查看身份后再确认归属')
            candidate = next(r for r in candidates if r['slope_id'] == target)
            conflicts = [*source['conflicts'], *candidate['conflicts']]
            if conflicts and not (body.get('conflict_confirmed') is True and str(body.get('conflict_reason', '')).strip()):
                raise IdentityConflict('身份存在冲突：'+'；'.join(conflicts)+'。请核对原页并填写处理依据后明确确认')
            decision.update(action='confirm_source_identity', matches=candidate['reasons'], conflicts=conflicts,
                            conflict_reason=body.get('conflict_reason', ''), target_revision=identity['revision'])
        for item in related:
            db.execute('UPDATE bindings SET slope_id=?,revision=revision+1 WHERE id=?', (target, item['id']))
            history = {'at': now(), 'actor': actor, 'reason': reason, 'from_slope': item['slope_id'],
                       'to_slope': target, 'revision': item['revision']+1, **decision,
                       'related_objects': len(related)}
            db.execute('INSERT INTO history(binding_id,created_at,payload) VALUES(?,?,?)', (item['id'], now(), encoded(history)))
    return detail(paths, slope_id=target) if target else detail(paths, binding_id=body['binding_id'])


def source_file(paths, source_hash):
    if not re.fullmatch('[0-9a-f]{64}', source_hash):
        raise ValueError('无效原件编号')
    with database(paths) as db:
        if not db.execute('SELECT hash FROM sources WHERE hash=?', (source_hash,)).fetchone():
            raise KeyError('原件尚未入库')
    target = folder(paths) / 'sources' / (source_hash+'.pdf')
    if file_hash(target) != source_hash:
        raise ValueError('归档原件指纹不一致')
    return target


def page_image(paths, source_hash, page, box=None):
    import fitz
    source = source_file(paths, source_hash)
    with fitz.open(source) as pdf:
        if not 1 <= page <= len(pdf):
            raise ValueError('原页页码超出范围')
        p = pdf[page-1]
        scale = 1800/max(p.rect.width, p.rect.height)
        clip = None
        if box is not None:
            from .recognition_scope import normalized_box
            if not normalized_box(box):
                raise ValueError('无效图片区域')
            clip = fitz.Rect(box[0]*p.rect.width, box[1]*p.rect.height,
                             box[2]*p.rect.width, box[3]*p.rect.height)
        return p.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False).tobytes('png')


def figure_image(paths, snapshot_id, index):
    with database(paths) as db:
        row = db.execute('SELECT payload,source_hash FROM snapshots WHERE id=?', (snapshot_id,)).fetchone()
        if not row:
            raise KeyError('图片来源尚未入库')
        assets = json.loads(row['payload'])['figures']
        if not 0 <= index < len(assets):
            raise ValueError('无效图片编号')
        asset = assets[index]
    return page_image(paths, row['source_hash'], asset['page'], asset['bbox'])
