"""Immutable native PDF evidence, source-preserving chunks, and page accounting.

No domain dictionary, station pattern, filename interpretation or cached legacy
extraction is used. Textless graphical pages are explicitly outside text coverage.
"""
import hashlib
import re
from pathlib import Path
import fitz


def pieces(text, limit):
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            candidates = [text.rfind(mark, start + limit // 2, end) for mark in ('\n', '。', '. ', '; ', '；')]
            boundary = max(candidates)
            if boundary >= 0:
                end = boundary + 1
        yield start, end, text[start:end]
        start = end


def prepare_document(path: Path, settings):
    path = path.resolve(strict=True)
    stamp = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document_id = 'pdf_' + digest[:20]
    doc = {'id': document_id, 'path': str(path), 'source_sha256': digest,
           'source_size_bytes': stamp.st_size, 'source_mtime_ns': stamp.st_mtime_ns,
           'chunker': 'source-block-sentence-overlap-v1',
           'pages': [], 'units': [], 'chunks': [], 'entities': [], 'references': [], 'facts': [], 'relations': [],
           'errors': [], 'discovery_batches': [], 'attribution_batches': [], 'synthesis_jobs': []}
    with fitz.open(path) as pdf:
        doc['source_page_count'] = len(pdf)
        for number, page in enumerate(pdf, 1):
            blocks = [b for b in page.get_text('blocks', sort=False) if len(b) > 6 and b[6] == 0 and b[4].strip()]
            chars = sum(len(re.sub(r'\s+', '', b[4])) for b in blocks)
            state = 'native_text' if chars >= settings.min_page_chars else ('unread_graphics' if page.get_contents() else 'blank')
            doc['pages'].append({'page': number, 'status': state, 'native_chars': chars,
                                 'width': page.rect.width, 'height': page.rect.height})
            if state != 'native_text':
                continue
            for block_index, block in enumerate(blocks):
                for start, end, text in pieces(block[4], max(200, settings.chunk_chars // 2)):
                    if not text.strip():
                        continue
                    # Short, document-scoped handles are easy for models to copy;
                    # the immutable document digest remains the evidence identity.
                    identifier = f'p{number}b{block_index}t{start}'
                    doc['units'].append({'id': identifier, 'page': number, 'text': text,
                        'bbox': [round(block[0] / page.rect.width, 6), round(block[1] / page.rect.height, 6),
                                 round(block[2] / page.rect.width, 6), round(block[3] / page.rect.height, 6)],
                        'block': block_index, 'start': start, 'end': end, 'source': 'pdf_native'})
    units = doc['units']
    start = 0
    while start < len(units):
        end, size = start, 0
        while end < len(units) and size + len(units[end]['text']) <= settings.chunk_chars:
            size += len(units[end]['text'])
            end += 1
        end = max(start + 1, end)
        selected = units[start:end]
        doc['chunks'].append({'id': 'c' + str(len(doc['chunks']) + 1).zfill(4),
            'unit_ids': [u['id'] for u in selected], 'pages': sorted({u['page'] for u in selected}),
            'assignments': [], 'attribution_status': 'pending'})
        start = max(start + 1, end - settings.overlap_units) if end < len(units) else end
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
    return [{'id': c['id'], 'spans': [{k: units[key][k] for k in ('id', 'page', 'text')} for key in c['unit_ids']]}
            for c in chunks]
