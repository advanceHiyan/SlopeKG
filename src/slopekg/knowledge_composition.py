"""Object evidence packages, shared-source batching and local ownership gates."""
from .knowledge_ownership import explicit_section_owners, ambiguous_entity_labels
from .visual_evidence import literal_supported
from .knowledge_attribution import numeric_residuals, source_local


def source_accounting(doc):
    """Stage success is not coverage: retain every unassigned source handle."""
    attributed={key for entity in doc['entities'] for key in entity['unit_ids']}
    references={m['span_id'] for entity in doc.get('references',[]) for m in entity.get('mentions',[])}
    cited={e['span_id'] for fact in doc['facts'] for e in fact.get('evidence',[])}
    pages=[]
    for page in doc['pages']:
        units=[u for u in doc['units'] if u['page']==page['page']]
        unassigned=[u['id'] for u in units if u['id'] not in attributed]
        pages.append({'page':page['page'],'source_units':len(units),
            'attributed_units':sum(u['id'] in attributed for u in units),
            'fact_cited_units':sum(u['id'] in cited for u in units),
            'unassigned_unit_ids':unassigned,
            'reference_only_unit_ids':[key for key in unassigned if key in references],
            'needs_visual_context':bool(page.get('graphics_unread') and unassigned),
            'no_object_attribution':bool(units and not any(u['id'] in attributed for u in units)
                                       and any(u['id'] not in references for u in units))})
    return {'pages':pages,'pages_without_attribution':[p['page'] for p in pages if p['no_object_attribution']],
        'unassigned_units':sum(len(p['unassigned_unit_ids']) for p in pages),
        'unrepresented_numeric_sources':numeric_residuals(doc),
        'note':'Unassigned sources require inspection, not forced object ownership. Counts are not fact recall.'}


def prepare_bindings(doc):
    """Unique explicit mentions/section hints select evidence, never prove facts."""
    units = {u['id']: u for u in doc['units']}
    scopes = explicit_section_owners(doc)
    doc['ambiguous_entity_ids'] = sorted(ambiguous_entity_labels(doc))
    doc['section_owners'] = {key: value['owner_id'] for key, value in scopes.items() if value}
    known = {e['id']: e for e in doc['entities']}
    visited = {key for c in doc['chunks'] if c['attribution_status'] == 'completed' for key in c['unit_ids']}
    names = {}
    for entity in known.values():
        if entity['id'] in doc['ambiguous_entity_ids']:
            continue
        for label in [entity['name'], *entity['aliases']]:
            if label.strip():
                names.setdefault(label.casefold(), set()).add(entity['id'])
    doc['explicit_span_owners'] = {}
    for unit in doc['units']:
        if unit['id'] not in visited:
            continue
        owners = {next(iter(ids)) for label, ids in names.items() if len(ids) == 1
                  and literal_supported(label, unit['text'].casefold())}
        concrete = {key for key in owners if known[key]['kind'] == 'entity'}
        if unit.get('source') == 'pdf_table' and not unit.get('is_header') and concrete:
            doc['explicit_span_owners'][unit['id']] = sorted(concrete)
        if unit['id'] in doc['section_owners']:
            owners.add(doc['section_owners'][unit['id']])
        for owner in owners:
            known[owner]['unit_ids'].append(unit['id'])
    for entity in known.values():
        ids = list(dict.fromkeys(entity['unit_ids']))
        ids = set([*ids, *(h for key in ids for h in units[key].get('header_ids', []))])
        rejected=[key for key in ids if not source_local(entity,units[key],units)]
        if rejected:doc.setdefault('attribution_diagnostics',[]).append({'entity_id':entity['id'],
            'source_ids':rejected,'reason':'numeric_source_has_no_local_object_anchor'})
        entity['unit_ids'] = [u['id'] for u in doc['units'] if u['id'] in ids and u['id'] not in rejected]
        ids=set(entity['unit_ids'])
        entity['chunk_ids'] = [c['id'] for c in doc['chunks'] if ids.intersection(c['unit_ids'])]


def composition_jobs(doc, settings):
    units = {u['id']: u for u in doc['units']}
    jobs = []
    for entity in sorted(doc['entities'], key=lambda e: e['kind'] != 'entity'):
        if entity['id'] in doc.get('ambiguous_entity_ids', []):
            entity['status'] = 'identity_requires_review'
            continue
        part, size, number = [], 0, 0
        for key in entity['unit_ids']:
            cost = len(units[key]['text']) + 90
            if part and size + cost > settings.synthesis_chars:
                jobs.append({'entity_id': entity['id'], 'unit_ids': part, 'part': number, 'status': 'pending'})
                part, size, number = [], 0, number + 1
            part.append(key); size += cost
        if part:
            jobs.append({'entity_id': entity['id'], 'unit_ids': part, 'part': number, 'status': 'pending'})
    return sorted(jobs, key=lambda j: j['part'])


def pack_jobs(jobs, units, settings):
    """One source copy for overlapping dossiers; no object is silently dropped."""
    remaining = list(jobs)
    while remaining:
        group = [remaining.pop(0)]
        ids = set(group[0]['unit_ids'])
        while len(group) < settings.synthesis_batch_objects:
            choices = []
            for index, job in enumerate(remaining):
                if job['entity_id'] in {j['entity_id'] for j in group}:
                    continue
                union = ids | set(job['unit_ids'])
                cost = sum(len(units[key]['text']) + 90 for key in union)
                if cost <= settings.synthesis_chars:
                    choices.append((len(ids.intersection(job['unit_ids'])), -index, index))
            if not choices:
                break
            job = remaining.pop(max(choices)[2]); group.append(job); ids.update(job['unit_ids'])
        yield group


def gate_ownership(doc, item, units):
    """Check every citation, including extra quotes attached to a correct value."""
    owners = set()
    for evidence in item['evidence']:
        explicit = doc.get('explicit_span_owners', {}).get(evidence['span_id'], [])
        if len(explicit) == 1:
            owners.update(explicit)
        else:
            owner = doc.get('section_owners', {}).get(evidence['span_id'])
            if owner:
                owners.add(owner)
            if len(explicit) > 1:
                item['review_required'] = True
    subject = next(e for e in doc['entities'] if e['id'] == item['entity_id'])
    if any(not source_local(subject,units[e['span_id']],units) for e in item['evidence']):
        item['quality_issues'].append('numeric_source_has_no_local_object_anchor')
    if subject['id'] in doc.get('ambiguous_entity_ids', []):
        item['quality_issues'].append('non_unique_class_label_requires_review')
    if subject['kind'] == 'entity':
        compatible = {subject['id']}
        compatible.update(e['id'] for e in doc['entities'] if e['kind'] == 'entity'
                          and e['name'] == subject.get('context'))
        if owners - compatible:
            item['quality_issues'].append('foreign_object_section_evidence')
    elif owners:
        item['owner_candidates'] = sorted(owners)
        item['quality_issues'].append('concrete_case_requires_explicit_owner')
    if any(units[e['span_id']].get('source') == 'ocr' and
           (type(units[e['span_id']].get('confidence')) not in (int, float) or units[e['span_id']]['confidence'] < .85)
           for e in item['evidence']):
        item['quality_issues'].append('ocr_source_requires_review')
    item['quality_issues'] = list(dict.fromkeys(item['quality_issues']))
    if item['quality_issues']:
        item.update(status='pending', reason='local_source_or_ownership_issue')


def concrete_subject(doc, subject, raw, known):
    """An explicit concrete scope + consistent source row can replace a topic.

    This changes a candidate's representation, never transfers a review badge.
    Headings alone are insufficient.
    """
    if ((subject['kind'] != 'concept' and subject['id'] not in doc.get('ambiguous_entity_ids', []))
            or not isinstance(raw.get('scope'), str)):
        return subject
    owners = set()
    if not isinstance(raw.get('evidence'), list):
        return subject
    for evidence in raw['evidence']:
        key = evidence.get('span_id') if isinstance(evidence, dict) else None
        explicit = doc.get('explicit_span_owners', {}).get(key, [])
        if explicit:
            owners.update(explicit)
        else:
            owner = doc.get('section_owners', {}).get(key)
            if owner:
                owners.add(owner)
    if len(owners) != 1:
        return subject
    target = known.get(next(iter(owners)))
    if target and any(literal_supported(label.casefold(), raw['scope'].casefold())
                      for label in [target['name'], *target['aliases']] if len(label.strip()) > 1):
        return target
    return subject


def object_pages(doc):
    """Readable dossiers derive from evidence records, without another call."""
    result = []
    parents = {}
    for entity in doc['entities']:
        if entity['kind'] == 'entity':
            for label in [entity['name'], *entity['aliases']]:
                if label.strip():
                    parents.setdefault(label, set()).add(entity['id'])
    for entity in doc['entities']:
        facts = [f for f in doc['facts'] if f['entity_id'] == entity['id']]
        topics = [e['id'] for e in doc['entities'] if e['kind'] == 'concept'
                  and entity['kind'] == 'entity' and set(e['unit_ids']).intersection(entity['unit_ids'])]
        result.append({'entity_id': entity['id'], 'title': entity['name'],
            'description': entity['description'], 'source_unit_ids': entity['unit_ids'],
            'source_chunk_ids': entity['chunk_ids'], 'fact_ids': [f['id'] for f in facts],
            'child_ids': [e['id'] for e in doc['entities'] if e['kind'] == 'entity' and e['id'] != entity['id']
                          and parents.get(e.get('context')) == {entity['id']}],
            'related_topic_ids': topics,
            'unresolved_fact_ids': [f['id'] for f in facts if f['status'] == 'pending'],
            'status': entity['status']})
    doc['object_pages'] = result
