"""知联 Knowledge: Basic parsing, tables and byte-matched local OCR evidence.

No engineering extractor, filename semantics or model client is used here.
"""
import hashlib
import math
import re
from pathlib import Path

from .parsers import PdfParser, GEOMETRY_VERSION
from .storage import read_json, read_jsonl, write_json
from .visual_evidence import source_contains


def pieces(text, limit):
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = max(text.rfind(mark, start + limit // 2, end)
                           for mark in ('\n', '。', '. ', '; ', '；'))
            if boundary >= 0:
                end = boundary + 1
        yield start, end, text[start:end]
        start = end


def basic_sources(path, digest, parsed_dir=None, root=None, *, selected_pages=None):
    """Reuse a complete parse only with a matching manifest, never by name."""
    cached = {}
    if parsed_dir:
        location = Path(parsed_dir)
        cached = read_json(location, {}) if location.is_file() else {
            key: read_jsonl(location / (key + '.jsonl')) for key in
            ('documents', 'pages', 'text_blocks', 'tables', 'ocr_tasks', 'ocr_results')}
    manifests = [d for d in cached.get('documents', [])
                 if d.get('source_sha256') == digest and d.get('parse_scope') == 'full']
    if len(manifests) == 1:
        record = manifests[0]
        selected = {key: [r for r in cached.get(key, []) if r.get('document_id') == record['id']]
                    for key in ('pages', 'text_blocks', 'tables', 'ocr_tasks', 'ocr_results')}
        if (record.get('geometry_version') == GEOMETRY_VERSION
                and {p.get('page') for p in selected['pages'] if p.get('text_sampled')} == set(range(1, record['pages'] + 1))):
            return {'documents': [record], **selected}, 'basic_cache'
    parsed = PdfParser(Path(root or path.parent)).parse(path, generic=True,
        **({'selected_pages':selected_pages} if selected_pages is not None else {}))
    fresh = {key: getattr(parsed, key) for key in ('documents', 'pages', 'text_blocks', 'tables', 'ocr_tasks')}
    # Refresh legacy native coordinates without discarding byte-matched OCR.
    tasks = {t['id']: t for t in fresh['ocr_tasks']}
    previous = {t['id']: t for t in cached.get('ocr_tasks', []) if t.get('source_sha256') == digest}
    for task in tasks.values():
        old = previous.get(task['id'], {})
        if old.get('status') == 'done':
            task.update({k: v for k, v in old.items() if k in ('status', 'engine', 'device', 'image_path', 'items')})
    fresh['ocr_results'] = [r for r in cached.get('ocr_results', [])
                            if r.get('task_id') in tasks and r.get('source_sha256') == digest
                            and r.get('document_id') == fresh['documents'][0]['id']]
    return fresh, 'basic_parser'


def normalized_box(row, page):
    box = row.get('bbox')
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or row.get('bbox_coordinate_space', 'pdf_points') != 'pdf_points'
            or not page.get('width') or not page.get('height')):
        return None
    try:
        values = [float(v) for v in box]
        if not all(math.isfinite(v) for v in values):
            return None
        # Existing Basic OCR maps pixels back to unrotated PDF points. Native
        # blocks and pdfplumber tables now explicitly use displayed PDF points.
        if row.get('task_id') and row.get('bbox_coordinate_frame') != 'display_pdf_points':
            matrix = page.get('rotation_matrix')
            if page.get('rotation', 0) and not matrix:
                return None
            if matrix:
                import fitz
                values = list(fitz.Rect(values) * fitz.Matrix(*matrix))
        result = [round(v / (page['width'] if i % 2 == 0 else page['height']), 6)
                  for i, v in enumerate(values)]
        if not (0 <= result[0] < result[2] <= 1 and 0 <= result[1] < result[3] <= 1):
            return None
        return result
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return None


def wire_source(unit):
    """Keep source layout; OCR strings alone do not retain rows or diagrams."""
    return {k:unit[k] for k in ('id','page','text','source','bbox','confidence',
                                'table_id','row','cells','header_ids','is_header') if k in unit}


def ensure_full_page_ocr(parsed, tasks, selected, digest, path, min_chars):
    """A completed title crop is not completed page-text preparation."""
    manifest=parsed['documents'][0]
    for page in parsed['pages']:
        number=page['page']
        if number not in selected:
            continue
        chars=sum(len(re.sub(r'\s+','',b['text'])) for b in parsed['text_blocks'] if b['page']==number)
        if chars>=min_chars or not (page.get('drawing_count',0) or page.get('image_count',0)):
            continue
        existing=[t for t in tasks.values() if t['page']==number]
        if any(t.get('region')=='full_page' for t in existing):
            continue
        template=existing[0] if existing else {'document_id':manifest['id'],
            'page_id':page['id'],'file_name':path.name,'path':str(path),'page':number,
            'source_sha256':digest}
        task={k:v for k,v in template.items() if k not in
              {'image_path','items','engine','device','error'}}
        task.update(id=template['page_id']+'_ocr_full_page',region='full_page',status='pending',
                    priority=100,reason='knowledge_page_text_incomplete',path=str(path),file_name=path.name)
        tasks[task['id']]=task


def prepare_document(path: Path, settings, *, parsed_dir=None, root=None, assets_dir=None,
                     run_ocr=False, ocr_runner=None, ocr_cache_dir=None, selected_pages=None):
    path = path.resolve(strict=True)
    stamp = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parsed, origin = basic_sources(path, digest, parsed_dir, root,selected_pages=selected_pages)
    manifest = parsed['documents'][0]
    selected = set(range(1, manifest['pages'] + 1)) if selected_pages is None else set(selected_pages)
    if (not selected or any(type(p) is not int for p in selected)
            or not selected <= set(range(1, manifest['pages'] + 1))):
        raise ValueError('Selected pages must be explicit physical page numbers inside the source')
    tasks = {t['id']: t for t in parsed.get('ocr_tasks', [])
             if t.get('source_sha256') == digest and t['page'] in selected}
    own_cache = Path(ocr_cache_dir) / (digest + '.json') if ocr_cache_dir else None
    local = read_json(own_cache, {}) if own_cache else {}
    local = local if local.get('source_sha256') == digest else {}
    tasks.update({t['id']: t for t in local.get('tasks', [])
                  if t.get('source_sha256') == digest and t['page'] in selected})
    ensure_full_page_ocr(parsed,tasks,selected,digest,path,settings.min_page_chars)
    ocr = [r for r in [*parsed.get('ocr_results', []), *local.get('rows', [])]
           if r.get('source_sha256') == digest and r.get('task_id') in tasks
           and tasks[r['task_id']].get('status') == 'done']
    ocr_receipts = []
    if run_ocr and settings.ocr_pages and any(t.get('status') != 'done' for t in tasks.values()):
        from .ocr import OcrRunner
        runner = ocr_runner or OcrRunner(Path(root or path.parent), Path(assets_dir or path.parent / 'ocr-assets'))
        fresh_tasks, fresh_rows = runner.run(list(tasks.values()), max_pages=settings.ocr_pages)
        tasks = {t['id']: t for t in fresh_tasks}
        ocr.extend(r for r in fresh_rows if r.get('source_sha256') == digest
                   and tasks.get(r.get('task_id'), {}).get('status') == 'done')
        ocr_receipts = [{'task_id': t['id'], 'page': t['page'], 'status': t['status']}
                        for t in fresh_tasks]
        if own_cache:
            merged_tasks = {t['id']: t for t in [*local.get('tasks', []), *fresh_tasks]}
            merged_rows = {r['id']: r for r in [*local.get('rows', []), *ocr]}
            write_json(own_cache, {'source_sha256': digest, 'tasks': list(merged_tasks.values()),
                                   'rows': list(merged_rows.values())})
    ocr = list({r['id']: r for r in ocr}.values())
    doc = {'id': 'pdf_' + digest[:20], 'path': str(path), 'source_sha256': digest,
           'source_size_bytes': stamp.st_size, 'source_mtime_ns': stamp.st_mtime_ns,
           'source_page_count': manifest['pages'], 'source_adapter': origin,
           'chunker': 'basic-evidence-table-row-v3', 'pages': [], 'units': [], 'chunks': [],
           'entities': [], 'references': [], 'facts': [], 'relations': [], 'errors': [],
           'discovery_batches': [], 'attribution_batches': [], 'synthesis_jobs': [],
           'ocr_receipts': ocr_receipts, 'source_duplicates': [],
           'selection': {'scope': 'whole_document' if selected_pages is None else 'selected_pages',
                         'selected_pages': sorted(selected),
                         'unselected_pages': sorted(set(range(1, manifest['pages'] + 1)) - selected)}}
    by_page = {p['page']: p for p in parsed['pages'] if p['page'] in selected}
    table_count = 0
    for number in sorted(by_page):
        page = by_page[number]
        blocks = sorted((b for b in parsed['text_blocks'] if b['page'] == number),
                        key=lambda b: b.get('reading_order', 0))
        tables = [t for t in parsed['tables'] if t['page'] == number and t.get('rows') and not t.get('error')]
        rows_ocr = [r for r in ocr if r['page'] == number]
        chars = sum(len(re.sub(r'\s+', '', b['text'])) for b in blocks)
        unread = chars < settings.min_page_chars and (page.get('drawing_count', 0) or page.get('image_count', 0))
        state = 'native_text' if chars >= settings.min_page_chars else ('ocr_text' if rows_ocr else 'unread_graphics' if unread else 'blank')
        doc['pages'].append({'page': number, 'status': state, 'native_chars': chars,
            'width': page.get('width'), 'height': page.get('height'),
            'graphics_unread': bool(unread or page.get('page_type') == 'drawing'),
            'ocr_regions_processed':sorted({t.get('region','unknown') for t in tasks.values()
                                           if t['page']==number and t.get('status')=='done'}),
            'full_page_ocr_pending':any(t['page']==number and t.get('region')=='full_page'
                                        and t.get('status')!='done' for t in tasks.values()),
            'ocr_pending': any(t['page'] == number and t.get('status') != 'done' for t in tasks.values())})
        page_units = []

        def add(identifier, text, record, source, **extra):
            box = normalized_box(record, page)
            issue = ('invalid_source_bbox' if record.get('bbox') is not None and box is None
                     and record.get('bbox_coordinate_space', 'pdf_points') == 'pdf_points' else None)
            for start, end, fragment in pieces(text, max(200, settings.chunk_chars // 2)):
                if fragment.strip():
                    page_units.append({'id': identifier + 't' + str(start), 'page': number,
                        'text': fragment, 'bbox': box, 'geometry_issue': issue,
                        'source': source, 'source_record_id': identifier,
                        'source_sha256': digest, 'start': start, 'end': end, **extra})

        for index, block in enumerate(blocks):
            duplicate = next((t for t in tables if normalized_box(block, page) and normalized_box(t, page)
                and t['bbox'][0] <= block['bbox'][0] + 2 and t['bbox'][1] <= block['bbox'][1] + 2
                and t['bbox'][2] >= block['bbox'][2] - 2 and t['bbox'][3] >= block['bbox'][3] - 2
                and any(source_contains(block['text'], ' | '.join(str(c or '') for c in row), table=True)
                        for row in t['rows'])), None)
            if duplicate:
                doc['source_duplicates'].append({'record_id': block['id'], 'table_id': duplicate['id']})
            elif chars >= settings.min_page_chars:
                add(f'p{number}b{index}', block['text'], block, 'pdf_native')
        for index, table in enumerate(tables):
            table_count += 1
            header_ids = []
            from .knowledge_tables import header_rows
            leading_headers = set(header_rows(table['rows']))
            for row_no, cells in enumerate(table['rows']):
                cells = [str(c or '') for c in cells]
                # Explicit row serialization, never invented labels or filled cells.
                is_header = row_no in leading_headers
                before = len(page_units)
                add(f'p{number}table{index}r{row_no}', ' | '.join(cells), table, 'pdf_table',
                    table_id=f'p{number}table{index}', row=row_no, cells=cells,
                    header_ids=list(header_ids), is_header=is_header)
                if is_header:
                    header_ids.extend(u['id'] for u in page_units[before:])
        text_positions={}
        for unit in page_units:
            text_positions.setdefault(re.sub(r'\s+','',unit['text']),[]).append(unit)
        for index, row in enumerate(rows_ocr):
            box=normalized_box(row,page)
            label=re.sub(r'\s+','',row['text'])
            duplicate=next((u for u in text_positions.get(label,[]) if box and u['bbox']
                and min(box[2],u['bbox'][2])>max(box[0],u['bbox'][0])
                and min(box[3],u['bbox'][3])>max(box[1],u['bbox'][1])
                and abs((box[0]+box[2]-u['bbox'][0]-u['bbox'][2])/2)<.005
                and abs((box[1]+box[3]-u['bbox'][1]-u['bbox'][3])/2)<.005),None)
            if duplicate:
                doc['source_duplicates'].append({'record_id':row['id'],'unit_id':duplicate['id'],
                                                'basis':'same_text_same_source_position'})
            else:
                before=len(page_units)
                add(f'p{number}ocr{index}', row['text'], row, 'ocr',
                    confidence=row.get('confidence'), task_id=row['task_id'])
                for unit in page_units[before:]:
                    text_positions.setdefault(re.sub(r'\s+','',unit['text']),[]).append(unit)
        page_units.sort(key=lambda u: ((u['bbox'] or [0, 1, 0, 1])[1],
                                      (u['bbox'] or [0, 1, 0, 1])[0], u.get('row', -1), u['start']))
        doc['units'].extend(page_units)
    doc['source_inventory'] = {'native_blocks': sum(b['page'] in selected for b in parsed['text_blocks']), 'tables': table_count,
        'ocr_rows_reused': len(ocr), 'ocr_tasks_pending': sum(t.get('status') != 'done' for t in tasks.values()),
        'duplicate_native_blocks':sum('table_id' in r for r in doc['source_duplicates']),
        'duplicate_ocr_rows':sum('unit_id' in r for r in doc['source_duplicates'])}
    units = doc['units']
    start = 0
    while start < len(units):
        end, size = start, 0
        while end < len(units) and size + len(units[end]['text']) <= settings.chunk_chars:
            size += len(units[end]['text']); end += 1
        end = max(start + 1, end)
        selected = units[start:end]
        headers = [key for u in selected for key in u.get('header_ids', [])]
        doc['chunks'].append({'id': 'c' + str(len(doc['chunks']) + 1).zfill(4),
            'unit_ids': list(dict.fromkeys([*headers, *(u['id'] for u in selected)])),
            'pages': sorted({u['page'] for u in selected}), 'assignments': [], 'attribution_status': 'pending'})
        start = max(start + 1, end - settings.overlap_units) if end < len(units) else end
    doc['evidence_digest'] = hashlib.sha256(repr(doc['units']).encode('utf-8')).hexdigest()
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Source changed during local preparation; no model request sent')
    return doc


def batch_chunks(chunks, units, budget):
    batch, size = [], 0
    for chunk in chunks:
        cost = sum(len(units[key]['text']) + 70 for key in chunk['unit_ids'])
        if batch and size + cost > budget:
            yield batch
            batch, size = [], 0
        batch.append(chunk)
        size += cost
    if batch:
        yield batch


def wire_chunks(chunks, units):
    return [{'id':c['id'],'spans':[wire_source(units[key]) for key in c['unit_ids']]}
            for c in chunks]
