"""Bound request navigation metadata while preserving every object identity.

Full descriptions, mention geometry and intent evidence stay in the document
record. They are navigation aids, never substitutes for provided source spans.
Identity strings and actual source evidence are never sliced or top-k filtered.
"""
from .visual_batching import compact

IDENTITY_KEYS = ('id', 'name', 'kind', 'context', 'aliases', 'salience', 'source_pages')


def request_directory(context, budget):
    registry = context.get('registry', [])
    intents = context.get('document_context', [])
    subject_keys = [key for key in ('subject', 'subjects') if key in context]
    metadata = {'registry': registry, 'document_context': intents,
                **{key: context[key] for key in subject_keys}}
    if len(compact(metadata)) <= budget:
        return context, None
    minimal = [{key: row[key] for key in IDENTITY_KEYS if key in row} for row in registry]
    assert len(minimal) == len(registry)
    # Keep all uses/purposes; their complete provenance remains in doc context.
    purposes = [{key: value for key, value in row.items() if key != 'evidence'}
                for row in intents]
    bounded = {**context, 'registry': minimal}
    if 'document_context' in context:
        bounded['document_context'] = purposes
    for key in subject_keys:
        def identity(row):
            return {k: v for k, v in row.items() if k != 'description'}
        bounded[key] = ([identity(row) for row in context[key]] if key == 'subjects'
                        else identity(context[key]))
    return bounded, {'mode': 'complete_identity_index', 'objects': len(minimal),
        'full_metadata_chars': len(compact(metadata)),
        'forwarded_metadata_chars': len(compact({k: bounded[k] for k in metadata if k in bounded})),
        'identity_strings_truncated': False, 'objects_omitted': 0,
        'full_details_retained_in_document': True}
