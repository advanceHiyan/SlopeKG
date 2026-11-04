"""知联 Knowledge: discovery -> many-to-many attribution -> evidence-bound knowledge.

All available text, table and OCR chunks are visited. Discovery and attribution are
separate from fact extraction; non-adjacent evidence is reunited before reading
facts. Results never publish into the basic graph or the visual result store.
"""
from __future__ import annotations
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import re
from pathlib import Path
import unicodedata

from .knowledge_document import prepare_document, batch_chunks, wire_chunks, wire_source
from .knowledge_settings import VERSION, KnowledgeSettings
from . import knowledge_prompts as prompts
from .model_client import content_hash, ModelError
from .pipeline_lock import pipeline_run_guard
from .provenance import runtime_identity
from .storage import read_json, write_json, write_jsonl
from .visual_contract import ContractError, ROLES, normalize_qualifier_verdict, bbox as checked_bbox
from .visual_evidence import source_contains, literal_supported
from .visual_runtime import VisualRuntime
from .visual_batching import compact
from .knowledge_ownership import owner_proposals
from .knowledge_evidence import table_headers, table_unit_quotes, unit_supported, value_supported, source_quote
from .knowledge_claims import partition_attribution
from .knowledge_recovery import recovery_package
from .knowledge_roles import canonical_role
from .knowledge_directory import request_directory
from .knowledge_batching import (fit_groups, pack_chunks, split_chunk, replace_chunks, prompt_chars,
                                 RequestCapacityError)
from .knowledge_attribution import source_local, source_pages
from .knowledge_composition import (prepare_bindings, composition_jobs, pack_jobs,
                                    gate_ownership, concrete_subject, object_pages, source_accounting)


def checked_list(answer, key):
    rows = answer.get(key)
    if key == 'relations' and key in answer and rows is None:
        return []  # Explicit JSON null means no relation claim, never a fact.
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ContractError('Invalid knowledge list: ' + key)
    return rows


def partition_composition(answer, expected):
    """Keep unique valid requested objects; quarantine only the bad partitions."""
    rows = answer.get('objects')
    if not isinstance(rows, list):
        raise ContractError('Invalid composition objects')
    groups = {}
    quarantined = list(answer.get('quarantined', [])) if isinstance(answer.get('quarantined'), list) else []
    prior_failures = answer.get('object_failures', {})
    for row in rows:
        key = row.get('entity_id') if isinstance(row, dict) else None
        if not isinstance(key, str) or key not in expected:
            quarantined.append({'reason': 'unrequested_or_invalid_object', 'output': row})
        else:
            groups.setdefault(key, []).append(row)
    accepted, failures = [], {}
    for key in sorted(expected):
        candidates = groups.get(key, [])
        if len(candidates) != 1:
            reason = ('missing_object' if not candidates else 'duplicate_object')
            if not candidates and isinstance(prior_failures, dict):
                reason = prior_failures.get(key, reason)
            failures[key] = reason
            quarantined.extend({'reason': reason, 'output': r} for r in candidates)
            continue
        row = candidates[0]
        try:
            facts, relations = checked_list(row, 'facts'), checked_list(row, 'relations')
            if not isinstance(row.get('unresolved', []), list):
                raise ContractError('Invalid unresolved composition')
        except ContractError as exc:
            failures[key] = str(exc)
            quarantined.append({'reason': str(exc), 'output': row})
            continue
        accepted.append({**row, 'facts': facts, 'relations': relations})
    return {'objects': accepted, 'object_failures': failures, 'quarantined': quarantined}


def canonical_name(name):
    return ''.join(unicodedata.normalize('NFC', name).casefold().split())


def wire_units(units, ids):
    return [wire_source(units[key]) for key in ids]


def resolve_source_handle(value, units, allowed):
    """An omitted offset is valid only for one whole, actually provided block.

    Never resolve by fuzzy spelling, proximity or a preferred first fragment.
    Split blocks remain ambiguous even when only one fragment is in this batch.
    """
    if isinstance(value, str) and value in allowed:
        return value
    if isinstance(value, str) and re.fullmatch(r'p\d+b\d+', value):
        matches = [key for key in units if key.startswith(value + 't')]
        if len(matches) == 1 and matches[0] in allowed:
            return matches[0]
    raise ContractError('Invalid or unprovided source identifier')


class KnowledgePipeline:
    def __init__(self, model, output_dir, *, settings=None, review_model=None, progress_callback=None,
                 parsed_dir=None, root=None):
        self.model, self.review_model = model, review_model or model
        self.output_dir = Path(output_dir).resolve()
        self.settings = settings or KnowledgeSettings()
        self.progress_callback = progress_callback
        self.parsed_dir, self.root = parsed_dir, root
        self.runtime = VisualRuntime(model, self.review_model, self.output_dir, self.settings,
            version=VERSION, system=prompts.SYSTEM, review_stages=('knowledge_verify','knowledge_owner_verify'), recover_invalid_answers=True)

    def run(self, pdfs, *, prepare_only=False, page_selections=None):
        return asyncio.run(self.arun(pdfs, prepare_only=prepare_only, page_selections=page_selections))

    def progress(self, percent, stage, message):
        if self.progress_callback:
            self.progress_callback(percent, stage, message)

    async def arun(self, pdfs, *, prepare_only=False, page_selections=None):
        sources = [Path(p).resolve(strict=True) for p in pdfs]
        if not sources or len(set(sources)) != len(sources) or any(p.suffix.lower() != '.pdf' for p in sources):
            raise ValueError('Explicit, unique PDF inputs required')
        if any(self.output_dir == p.parent or self.output_dir in p.parents for p in sources):
            raise ValueError('Output directory must not contain source PDFs')
        selections = {str(Path(p).resolve()): pages for p, pages in (page_selections or {}).items()}
        if page_selections is not None and set(selections) != {str(p) for p in sources}:
            raise ValueError('Page selections must cover exactly the explicit source PDFs')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with pipeline_run_guard(self.output_dir / '.knowledge.lock'):
            self.runtime.reset()
            self._review_batches_used = 0
            previous = read_json(self.output_dir / 'run.json', {})
            if previous and (previous.get('schema_version') != VERSION
                    or previous.get('model') != self.model.identity()
                    or previous.get('review_model') != self.review_model.identity()
                    or prepare_only and not previous.get('prepare_only')):
                write_json(self.output_dir / 'history' / (content_hash(previous)[:24] + '.json'), previous)
            self._previous = previous if (previous.get('schema_version') == VERSION
                and previous.get('model') == self.model.identity()
                and previous.get('review_model') == self.review_model.identity()) else {}
            result = {'schema_version': VERSION, 'recognizer': 'knowledge', 'producer': runtime_identity(),
                'started_at': datetime.now(timezone.utc).isoformat(), 'model': self.model.identity(),
                'review_model': self.review_model.identity(), 'settings': asdict(self.settings),
                'prepare_only': prepare_only, 'documents': [], 'processing_status': 'running',
                'precision': None, 'recall': None,
                'metric_note': '来源对齐和模型核验不等于人工准确率；图形页缺口、待审与未处理项单独保留。'}
            for source in sources:
                self.progress(2, 'knowledge_source', '正在建立原文、页码和分块清单')
                doc = prepare_document(source, self.settings, parsed_dir=self.parsed_dir, root=self.root,
                    assets_dir=self.output_dir / 'assets', run_ocr=not prepare_only,
                    ocr_cache_dir=self.output_dir / 'source-cache', selected_pages=selections.get(str(source)))
                count = len(list(batch_chunks(doc['chunks'], {u['id']: u for u in doc['units']}, self.settings.batch_chars)))
                doc['call_plan'] = {'discovery_batches': count, 'attribution_batches': count,
                    'synthesis_batches': None, 'review_limit': self.settings.max_review_calls,
                    'hard_run_limit': self.settings.max_calls, 'automatic_budget_increase': False}
                result['documents'].append(doc)
                self.runtime.document_id = doc['id']
                self._save(result)
                if not prepare_only:
                    try:
                        await self._recognize(doc, result)
                    except Exception as exc:
                        doc['errors'].append(str(exc) if isinstance(exc, (ModelError, ContractError)) else type(exc).__name__)
                        if isinstance(exc, RequestCapacityError):
                            visited = {key for c in doc['chunks'] if c['attribution_status'] == 'completed'
                                       for key in c['unit_ids']}
                            doc['capacity_failure'] = {'error': str(exc), 'request_sent': False,
                                'unprocessed_source_ids': [u['id'] for u in doc['units'] if u['id'] not in visited]}
                self._finish_document(doc, prepare_only)
                self._save(result)
            states = [d['status'] for d in result['documents']]
            result['processing_status'] = ('prepared' if prepare_only else 'completed' if all(s == 'completed' for s in states)
                else 'failed' if all(s == 'failed' for s in states) else 'partial')
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            result['processing_message'] = ('全文知识本地准备完成，未调用模型' if prepare_only else
                '全文知识识别结束；请查看对象页、来源和覆盖缺口，候选未写入正式图谱')
            self._save(result)
            from .knowledge_render import render_knowledge
            render_knowledge(result, self.output_dir / 'index.html')
            self.progress(100, result['processing_status'], result['processing_message'])
            return result

    def _save(self, result):
        facts = [f for d in result['documents'] for f in d['facts']]
        relations = [f for d in result['documents'] for f in d['relations']]
        result['summary'] = {'documents': len(result['documents']),
            'pages': sum(len(d['pages']) for d in result['documents']),
            'source_pages': sum(d['source_page_count'] for d in result['documents']),
            'chunks': sum(len(d['chunks']) for d in result['documents']),
            'entities': sum(len(d['entities']) for d in result['documents']), 'facts': len(facts),
            'references': sum(len(d['references']) for d in result['documents']),
            'carried_forward_facts': sum(bool(f.get('carried_forward')) for f in facts),
            'relations': len(relations),
            **{status: sum(f['status'] == status for f in facts) for status in ('source_aligned', 'verified_model', 'pending', 'rejected')},
            'calls': self.runtime.calls, 'cache_hits': self.runtime.cache_hits,
            'transient_retries': self.runtime.transient_retries,
            'peak_concurrency': self.runtime.peak_concurrency}
        result['receipts'] = list(self.runtime.receipts)
        result['environment_variables_used'] = sorted({v for r in self.runtime.receipts for v in r.get('environment_variables_used', [])})
        write_json(self.output_dir / 'run.json', result)
        write_jsonl(self.output_dir / 'facts.jsonl', facts)
        write_jsonl(self.output_dir / 'relations.jsonl', relations)

    @staticmethod
    def _registry(doc, include_catalog=False):
        rows = [*doc['entities'], *[e for e in doc['references'] if include_catalog and e['salience'] == 'catalog']]
        return [{**{k: e[k] for k in ('id', 'name', 'kind', 'context', 'aliases', 'salience','description')},
                 'source_pages':sorted({m['page'] for m in e.get('mentions',[]) if m.get('page') is not None}),
                 'source_anchors':[{k:m[k] for k in ('span_id','page','bbox') if k in m}
                                   for m in e.get('mentions',[])[:8]]} for e in rows]

    def _request_context(self, doc, context):
        # Reserve most input capacity for original evidence. Optional directory
        # detail must not displace it; every object's identity stays available.
        result, receipt = request_directory(context, self.settings.max_prompt_chars // 3)
        if receipt:
            previous = doc.get('directory_delivery', {})
            if receipt['full_metadata_chars'] >= previous.get('full_metadata_chars', 0):
                doc['directory_delivery'] = receipt
        return result

    @staticmethod
    def _evidence(rows, units, allowed):
        valid, errors = [], []
        if not isinstance(rows, list) or not rows:
            return [], ['missing_source_evidence']
        for row in rows:
            if not isinstance(row, dict):
                errors.append('invalid_evidence')
                continue
            key, quote = row.get('span_id'), row.get('quote')
            try:
                key = resolve_source_handle(key, units, allowed)
            except ContractError:
                errors.append('unprovided_source_span')
                continue
            if not isinstance(key, str) or key not in allowed or not isinstance(quote, str) or not quote.strip():
                errors.append('unprovided_source_span')
            elif source_quote(quote, units[key]['text']) is None:
                errors.append('quote_not_in_source')
            else:
                box = units[key].get('bbox')
                try:
                    if box is not None:
                        checked_bbox(box)
                except ContractError:
                    errors.append('invalid_source_geometry')
                    box = None
                if units[key].get('geometry_issue'):
                    errors.append('invalid_source_geometry')
                valid.append({'span_id': key, 'quote': source_quote(quote,units[key]['text']), 'page': units[key]['page'], 'bbox': box,
                              'source': units[key].get('source', 'pdf_native')})
        return valid, list(dict.fromkeys(errors))

    async def _recognize(self, doc, result):
        units = {u['id']: u for u in doc['units']}
        discovery_context = lambda batch: self._request_context(doc, {
            'registry': self._registry(doc, include_catalog=True), 'chunks': wire_chunks(batch, units)})
        # Whole-source structural preflight before the first paid call. Registry
        # growth is checked again, lazily, before every discovery dispatch.
        preflight = list(pack_chunks(doc['chunks'], units, discovery_context, prompts.DISCOVER,
                                     self.settings.max_prompt_chars, source_budget=self.settings.batch_chars))
        doc['request_preflight'] = {'source_units': len(units), 'initial_discovery_batches': len(preflight),
            'max_prompt_chars': max((prompt_chars(prompts.DISCOVER, discovery_context(b)) for b in preflight), default=0),
            'basis': 'complete serialized request including instructions and source metadata',
            'registry_growth_rechecked': True}
        batches = pack_chunks(list(doc['chunks']), units, discovery_context, prompts.DISCOVER,
                              self.settings.max_prompt_chars, source_budget=self.settings.batch_chars)
        for index, batch in enumerate(batches):
            replace_chunks(doc, batch)
            self.progress(5 + min(19, int(20 * index / max(1, len(preflight)))), 'knowledge_discover',
                          f'全文对象发现第 {index + 1} 批')
            context = discovery_context(batch)
            entry = {'chunks': [c['id'] for c in batch], 'status': 'pending',
                     'prompt_chars': prompt_chars(prompts.DISCOVER, context)}
            doc['discovery_batches'].append(entry)
            known = {e['id']: e for e in [*doc['entities'], *doc['references']]}
            allowed = {key for c in batch for key in c['unit_ids']}
            try:
                answer = await self.runtime.ask_async('knowledge_discover', prompts.DISCOVER,
                    context, [],
                    lambda a: {'entities': checked_list(a, 'entities'),'document_context':a.get('document_context')})
                entry['status'] = 'completed'
                context=answer.get('document_context')
                if isinstance(context,dict) and isinstance(context.get('purpose'),str) and context['purpose'].strip():
                    support,issues=self._evidence(context.get('evidence'),units,allowed)
                    if support and not issues:
                        candidate={'purpose':context['purpose'],'evidence':support,
                                   'status':'model_proposed_document_context'}
                        if candidate not in doc.setdefault('document_context',[]):doc['document_context'].append(candidate)
                    else:
                        doc.setdefault('context_diagnostics',[]).append({'batch':index,'issues':issues,
                            'reason':'document_context_without_valid_source'})
                for raw in answer['entities']:
                    name, kind = raw.get('name'), raw.get('kind')
                    if not isinstance(name, str) or not name.strip() or kind not in {'entity', 'concept'}:
                        raise ContractError('Invalid discovered identity')
                    evidence, errors = self._evidence(raw.get('mentions'), units, allowed)
                    if errors:
                        doc['errors'].extend(errors)
                        entry['status'] = 'partial'
                        continue
                    supplied_id = raw.get('id', '')
                    if not isinstance(supplied_id, str) or (supplied_id and supplied_id not in known):
                        raise ContractError('Discovery cited unknown registry identity')
                    context = raw.get('context', '')
                    salience = raw.get('salience', 'focus')
                    if salience not in {'focus', 'catalog', 'support'}:
                        raise ContractError('Invalid object salience')
                    if not isinstance(context, str):
                        raise ContractError('Invalid identity context')
                    existing = known.get(supplied_id) or next((e for e in [*doc['entities'], *doc['references']]
                        if e['kind'] == kind and canonical_name(e['name']) == canonical_name(name)
                        and canonical_name(e['context']) == canonical_name(context)), None)
                    aliases = raw.get('aliases', [])
                    if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
                        raise ContractError('Invalid aliases')
                    if existing:
                        if existing['kind'] != kind:
                            raise ContractError('Identity type disagreement')
                        if salience == 'focus' and existing['salience'] != 'focus':
                            if len(doc['entities']) >= self.settings.max_entities:
                                entry['status'] = 'partial'
                                doc['errors'].append('entity_budget_exhausted')
                            else:
                                doc['references'].remove(existing)
                                doc['entities'].append(existing)
                                aliases = [existing['name'], *aliases]
                                existing.update(name=name, context=context, salience='focus', description=str(raw.get('description', '')))
                        if name != existing['name']:
                            aliases = [name, *aliases]
                        existing['mentions'].extend(evidence)
                        existing['aliases'] = list(dict.fromkeys([*existing['aliases'], *aliases]))
                    elif salience == 'focus' and len(doc['entities']) >= self.settings.max_entities:
                        entry['status'] = 'partial'
                        doc['errors'].append('entity_budget_exhausted')
                        doc['references'].append({'id': 'e_' + content_hash([doc['id'], kind, canonical_name(name), canonical_name(context)])[:16],
                            'name': name, 'kind': kind, 'context': context, 'salience': 'support',
                            'aliases': aliases, 'description': str(raw.get('description', '')), 'mentions': evidence,
                            'status': 'focus_budget_deferred', 'chunk_ids': [], 'unit_ids': []})
                    else:
                        entity = {'id': 'e_' + content_hash([doc['id'], kind, canonical_name(name), canonical_name(context)])[:16],
                            'name': name, 'kind': kind, 'context': context, 'salience': salience, 'aliases': list(dict.fromkeys(aliases)),
                            'description': str(raw.get('description', '')), 'mentions': evidence,
                            'chunk_ids': [], 'unit_ids': [], 'status': 'candidate'}
                        doc['entities' if salience == 'focus' else 'references'].append(entity)
                        known[entity['id']] = entity
            except (ModelError, ContractError) as exc:
                entry.update(status='failed', error=str(exc))
            self._save(result)
        for entity in doc['entities']:entity['source_anchor_pages']=source_pages(entity,units)
        registry = self._registry(doc)
        known = {e['id']: e for e in doc['entities']}
        attribution_context = lambda batch: self._request_context(doc, {'registry': registry,
            'document_context': doc.get('document_context', []), 'chunks': wire_chunks(batch, units)})
        batches = list(pack_chunks(doc['chunks'], units, attribution_context, prompts.ATTRIBUTE,
                                   self.settings.max_prompt_chars, source_budget=self.settings.batch_chars))
        for batch in batches:
            replace_chunks(doc, batch)
        doc['call_plan'].update(discovery_batches=len(doc['discovery_batches']), attribution_batches=len(batches))

        async def attribute(index, batch):
            context = attribution_context(batch)
            entry = {'chunks': [c['id'] for c in batch], 'status': 'pending',
                     'prompt_chars': prompt_chars(prompts.ATTRIBUTE, context)}
            doc['attribution_batches'].append(entry)
            by_id = {c['id']: c for c in batch}
            def validate(answer):
                return partition_attribution(answer,batch,units,known,resolve_source_handle)
            try:
                answer = await self.runtime.ask_async('knowledge_attribute', prompts.ATTRIBUTE,
                    context, [], validate)
                doc.setdefault('attributed_claims',[]).extend(answer.get('claims',[]))
                if answer.get('quarantined'):
                    doc.setdefault('attribution_diagnostics',[]).append({'batch':index,
                        'reason':'invalid_bindings_quarantined','items':answer['quarantined']})
                    entry['error']='; '.join(dict.fromkeys(str(row.get('reason','invalid_binding')) for row in answer['quarantined']))
                for row in answer['assignments']:
                    c = by_id[row['chunk_id']]
                    c.update(attribution_status=row['attribution_status'], assignments=row['entities'], unresolved=row.get('unresolved', []))
                    for binding in row['entities']:
                        e = known[binding['entity_id']]
                        e['chunk_ids'] = list(dict.fromkeys([*e['chunk_ids'], c['id']]))
                        e['unit_ids'] = list(dict.fromkeys([*e['unit_ids'], *binding['span_ids']]))
                entry['status'] = 'completed' if all(r['attribution_status'] == 'completed' for r in answer['assignments']) else 'partial'
                entry['source_chunk_repairs'] = answer['source_chunk_repairs']
            except (ModelError, ContractError) as exc:
                entry.update(status='failed', error=str(exc))
            self.progress(25 + int(20 * (index + 1) / max(1, len(batches))), 'knowledge_attribute',
                          f'跨分块归属 {index + 1}/{len(batches)} 批')
            self._save(result)

        if registry:
            await asyncio.gather(*(attribute(i, b) for i, b in enumerate(batches)))
        prepare_bindings(doc)
        self._retain_attributed(doc,units,known)
        doc['synthesis_jobs'] = composition_jobs(doc, self.settings)
        groups = list(fit_groups(pack_jobs(doc['synthesis_jobs'], units, self.settings),
            lambda g: self._composition_request(doc, g, units, registry)[1],
            lambda g: self._composition_request(doc, g, units, registry)[0],
            self.settings.max_prompt_chars, lambda j: self._split_job(j, units)))
        doc['synthesis_jobs'] = [job for group in groups for job in group]
        doc['call_plan']['synthesis_batches'] = len(groups)
        doc['call_plan']['planned_before_optional_reviews'] = len(doc['discovery_batches']) + len(batches) + len(groups)
        slots = asyncio.Semaphore(self.settings.model_concurrency)
        async def synthesize(index, group):
            async with slots:
                try:
                    instruction, context = self._composition_request(doc, group, units, registry)
                    subjects = context.get('subjects', [context.get('subject')])
                    for job in group:
                        job['prompt_chars'] = prompt_chars(instruction, context)
                    if len(group) == 1:
                        answer = await self.runtime.ask_async('knowledge_synthesize', prompts.SYNTHESIZE,
                            context, [],
                            lambda a: {'facts': checked_list(a, 'facts'), 'relations': checked_list(a, 'relations')})
                        outputs = {subjects[0]['id']: answer}
                    else:
                        expected = {s['id'] for s in subjects}
                        def validate(answer):
                            return partition_composition(answer, expected)
                        answer = await self.runtime.ask_async('knowledge_synthesize', prompts.RECOMPOSE,
                            context, [], validate)
                        outputs = {r['entity_id']: r for r in answer['objects']}
                        if answer['quarantined'] or answer['object_failures']:
                            doc.setdefault('composition_diagnostics', []).append({'batch': index,
                                'object_failures': answer['object_failures'], 'quarantined': answer['quarantined']})
                    for job in group:
                        if job['entity_id'] not in outputs:
                            job.update(status='failed', error=answer.get('object_failures', {}).get(
                                job['entity_id'], 'missing_object'))
                            continue
                        subject, output = known[job['entity_id']], outputs[job['entity_id']]
                        items = []
                        rejected_items = []
                        for kind, rows in (('fact', output['facts']), ('relation', output['relations'])):
                            for raw in rows:
                                target = concrete_subject(doc, subject, raw, known) if kind == 'fact' else subject
                                allowed = set(job['unit_ids'])
                                if target['id'] != subject['id']:
                                    allowed.intersection_update(target['unit_ids'])
                                try:
                                    item = self._item(doc, target, kind, raw, units, allowed, known)
                                except ContractError as exc:
                                    rejected_items.append({'kind': kind, 'reason': str(exc), 'output': raw})
                                    continue
                                if target['id'] != subject['id']:
                                    item['topic_entity_ids'] = [subject['id']]
                                    doc.setdefault('topic_examples', []).append({'topic_id': subject['id'],
                                        'fact_id': item['id'], 'basis': 'explicit concrete scope and consistent source row; no review transferred'})
                                items.append(item)
                        for item in self._deduplicate_items(items):
                            gate_ownership(doc, item, units)
                            doc['facts' if item['kind'] == 'fact' else 'relations'].append(item)
                        job['unresolved'] = output.get('unresolved', [])
                        if rejected_items:
                            job['unresolved'] = [*job['unresolved'], 'invalid_items_quarantined']
                            job['quarantined_items'] = rejected_items
                        job['status'] = 'partial' if job['unresolved'] else 'completed'
                except (ModelError, ContractError) as exc:
                    for job in group:
                        job.update(status='failed', error=str(exc))
                self.progress(45 + int(42 * (index + 1) / max(1, len(groups))), 'knowledge_synthesize',
                              f'对象知识重组 {index + 1}/{len(groups)} 批')
                self._save(result)
        await asyncio.gather(*(synthesize(i, group) for i, group in enumerate(groups)))
        self._retain_previous(doc, units, known)
        for key in ('facts', 'relations'):
            doc[key] = self._deduplicate_items(doc[key])
            for item in doc[key]:
                gate_ownership(doc, item, units)
        await self._selective_review(doc, units, known)
        await self._recover_unassigned(doc,result,units,known)
        self._conflicts(doc)
        for entity in doc['entities']:
            entity['fact_ids'] = [f['id'] for f in doc['facts'] if f['entity_id'] == entity['id']]
            jobs = [j for j in doc['synthesis_jobs'] if j['entity_id'] == entity['id']]
            entity['status'] = 'organized' if jobs and all(j['status'] == 'completed' for j in jobs) else 'partial'
        object_pages(doc)

    @staticmethod
    def _split_job(job, units):
        parts = split_chunk({'id': 'part', 'unit_ids': job['unit_ids']}, units)
        return [{**job, 'unit_ids': part['unit_ids'], 'part': str(job['part']) + '.' + str(i + 1),
                 'status': 'pending'} for i, part in enumerate(parts)]

    def _composition_request(self, doc, group, units, registry):
        """One builder is used by planning and dispatch, including optional hints."""
        known = {e['id']: e for e in doc['entities']}
        subjects = [{**{k: known[j['entity_id']][k] for k in
            ('id', 'name', 'kind', 'context', 'aliases', 'description')},
            'allowed_span_ids': j['unit_ids']} for j in group]
        keys = {key for j in group for key in j['unit_ids']}
        instruction = prompts.SYNTHESIZE if len(group) == 1 else prompts.RECOMPOSE
        context = {'registry': registry, 'document_context': doc.get('document_context', []),
            'source_spans': wire_units(units, [u['id'] for u in doc['units'] if u['id'] in keys]),
            'source_sha256': doc['source_sha256'], 'extracted_facts': []}
        context['subject' if len(group) == 1 else 'subjects'] = subjects[0] if len(group) == 1 else subjects
        context = self._request_context(doc, context)
        omitted = 0
        for fact in doc['facts']:
            if (fact['entity_id'] not in {s['id'] for s in subjects}
                    or not all(e['span_id'] in keys for e in fact['evidence'])):
                continue
            hint = {k: fact.get(k, '') for k in ('entity_id', 'field', 'value', 'unit', 'scope', 'role')}
            hint['span_ids'] = [e['span_id'] for e in fact['evidence']]
            proposed = {**context, 'extracted_facts': [*context['extracted_facts'], hint],
                        'extracted_fact_hints_omitted': len(doc['facts'])}
            if prompt_chars(instruction, proposed) <= self.settings.max_prompt_chars:
                context['extracted_facts'].append(hint)
            else:
                omitted += 1
        if omitted:
            context['extracted_fact_hints_omitted'] = omitted
        return instruction, context

    async def _selective_review(self, doc, units, known):
        """No paid repair of local-invalid candidates; bounded ambiguity review."""
        doc['review_jobs'] = []
        if self.settings.review_mode == 'none':
            return
        def priority(subject):
            concrete_fact = any(i['entity_id'] == subject['id'] and i.get('review_required')
                and not i['quality_issues'] and i['status'] != 'verified_model' for i in doc['facts'])
            return (not concrete_fact, subject['kind'] != 'entity')
        for subject in sorted(known.values(), key=priority):
            items = [i for i in [*doc['facts'], *doc['relations']] if i['entity_id'] == subject['id']
                and not i['quality_issues'] and i['status'] != 'verified_model'
                and (self.settings.review_mode == 'all' or i.get('review_required'))]
            for batch in self._review_batches(subject, items, units):
                if self._review_batches_used >= self.settings.max_review_calls:
                    for item in batch:
                        item.update(status='pending', reason='optional_review_budget_exhausted')
                    continue
                job = {'entity_id': subject['id'], 'items': [i['id'] for i in batch]}
                doc['review_jobs'].append(job)
                self._review_batches_used += 1
                for item in batch:
                    item.update(status='pending', reason='awaiting_optional_semantic_review')
                ok = await self._verify(subject, batch, units)
                job['status'] = 'completed' if ok else 'partial'

    async def _reconcile_owners(self, doc, units, known):
        """Explicit compatibility review helper; v3 does not auto-run this stage."""
        existing={f['id']:f for f in doc['facts']};groups={};old_by_new={};created={}
        doc['ownership_jobs']=[];doc.setdefault('topic_examples',[])
        for proposal in owner_proposals(doc,literal_supported):
            original=proposal['original'];subject=known[proposal['owner_id']]
            header=units[proposal['header_span_id']]
            evidence={ (e['span_id'],e['quote']):e for e in [*original['evidence'],{'span_id':header['id'],'quote':header['text']}] }
            raw={**original,'evidence':list(evidence.values())}
            allowed={e['span_id'] for e in raw['evidence']}
            item=self._item(doc,subject,'fact',raw,units,allowed,known)
            if item['id'] in existing:
                if existing[item['id']]['status']=='verified_model':
                    if original in doc['facts']:doc['facts'].remove(original)
                    doc['topic_examples'].append({'topic_id':original['entity_id'],'fact_id':item['id'],
                        'previous_fact_id':original['id'],'basis':'identical concrete assertion already reviewed'})
                continue
            if item['id'] in old_by_new:
                old_by_new[item['id']].append(original)
                if proposal['topic_id'] not in created[item['id']]['topic_entity_ids']:
                    created[item['id']]['topic_entity_ids'].append(proposal['topic_id'])
                continue
            item['topic_entity_ids']=[proposal['topic_id']]
            item['ownership_context']={'header_span_id':header['id'],'end_span_id':proposal['end_span_id'],
                'basis':'explicit numbered heading and real source interval; semantic review required'}
            old_by_new[item['id']]=[original]
            created[item['id']]=item
            groups.setdefault(subject['id'],[]).append(item)
        for subject_id in [e['id'] for e in doc['entities'] if e['id'] in groups]:
            items=groups[subject_id]
            subject=known[subject_id]
            for batch in self._review_batches(subject,items,units,instruction=prompts.OWNERSHIP_VERIFY):
                job={'entity_id':subject_id,'items':[i['id'] for i in batch],'status':'pending'}
                doc['ownership_jobs'].append(job)
                ok=await self._verify(subject,batch,units,stage='knowledge_owner_verify',instruction=prompts.OWNERSHIP_VERIFY)
                job['status']='completed' if ok else 'partial'
                for item in batch:
                    originals=old_by_new[item['id']]
                    if item['status']=='verified_model':
                        for original in originals:
                            if original in doc['facts']:doc['facts'].remove(original)
                            doc['topic_examples'].append({'topic_id':original['entity_id'],'fact_id':item['id'],'previous_fact_id':original['id']})
                        doc['facts'].append(item)
                    elif item['status']=='pending':
                        for original in originals:
                            original.update(status='pending',reason='concrete_owner_requires_review',owner_candidate_id=subject_id)
                        doc['facts'].append(item)

    def _retain_previous(self, doc, units, known):
        """Growing context must not erase independently sourced prior readings.

        Reuse only this route, identical file bytes, identical model profiles,
        identical assertion identity, and evidence still assigned to the same
        object. Re-run literal guards; never turn an old pending item into a pass.
        Unread new context stays an unfinished job even when prior facts survive.
        """
        prior = next((d for d in self._previous.get('documents', [])
            if d['id'] == doc['id'] and d['source_sha256'] == doc['source_sha256']), None)
        if not prior:
            return
        for collection, kind in (('facts', 'fact'), ('relations', 'relation')):
            present = {f['id'] for f in doc[collection]}
            for old in prior.get(collection, []):
                subject = known.get(old.get('entity_id'))
                if old['id'] in present or not subject or old.get('source_sha256') != doc['source_sha256']:
                    continue
                try:
                    item = self._item(doc, subject, kind, old, units, set(subject['unit_ids']), known)
                except ContractError:
                    continue
                if item['id'] != old['id'] or not item['evidence'] or len(item['evidence']) != len(old.get('evidence', [])):
                    continue
                review = old.get('review', {})
                checks = ('source_supported', 'identity_supported', 'meaning_supported', 'scope_supported', 'qualifiers_supported')
                item.update(carried_forward=True, prior_reason=old.get('reason'), review=review)
                if (old['status'] == 'verified_model' and not item['quality_issues'] and review.get('id') == item['id']
                        and review.get('decision') == 'supported' and all(review.get(k) is True for k in checks)):
                    item.update(status='verified_model', reason='prior_identical_assertion_and_source_revalidated')
                elif old['status'] == 'rejected':
                    item.update(status='rejected', reason=old.get('reason', 'prior_model_rejected'))
                elif old['status'] == 'source_aligned' and not item['quality_issues']:
                    item.update(status='source_aligned', reason='prior_literal_source_revalidated_not_independently_reviewed')
                else:
                    item.update(status='pending', reason='prior_candidate_retained_pending_review')
                doc[collection].append(item)
                present.add(item['id'])

    def _item(self, doc, subject, kind, raw, units, allowed, known):
        evidence, errors = self._evidence(table_headers(raw.get('evidence'),units,allowed), units, allowed)
        keys = ('field', 'value', 'unit', 'unit_basis', 'qualifier', 'scope', 'role', 'condition', 'time') if kind == 'fact' else ('target_id', 'predicate', 'scope')
        if any(not isinstance(raw.get(key, ''), str) for key in keys):
            raise ContractError('Knowledge fields must be strings')
        item = {key: raw.get(key, '') for key in keys}
        original_role=item.get('role')
        if kind=='fact':item['role']=canonical_role(item['role'])
        quotes = [e['quote'] for e in evidence]
        if kind == 'fact':
            if not item['field'] or not item['value'] or item['role'] not in ROLES or item['role'] == 'unknown':
                errors.append('incomplete_fact_semantics')
            if not value_supported(item['value'],evidence,units):
                errors.append('value_not_in_cited_source')
            for key in ('unit', 'unit_basis'):
                unit_quotes=table_unit_quotes(item['value'],evidence,units) if key=='unit' else None
                if item[key] and not unit_supported(item[key],quotes if unit_quotes is None else unit_quotes):
                    errors.append(key + '_not_in_cited_source')
        elif item['target_id'] not in known or item['target_id'] == subject['id'] or not item['predicate']:
            errors.append('invalid_relation_identity')
        if kind == 'relation':
            item['target_name'] = known.get(item['target_id'], {}).get('name', '')
            item['target_context'] = known.get(item['target_id'], {}).get('context', '')
            item['target_aliases'] = known.get(item['target_id'], {}).get('aliases', [])
        if not item['scope']:
            errors.append('missing_scope')
        item.update(id='k_' + content_hash([doc['id'], subject['id'], kind, item])[:22],
            kind=kind, document_id=doc['id'], entity_id=subject['id'], object=subject['name'],
            source_sha256=doc['source_sha256'], evidence=evidence,
            page=evidence[0]['page'] if evidence else None, status='pending',
            quality_issues=list(dict.fromkeys(errors)), reason='awaiting_semantic_review')
        item['review_required'] = kind == 'relation' or raw.get('review_required') is True
        if kind=='fact' and original_role!=item['role']:item['role_original']=original_role
        if not item['quality_issues']:
            item.update(status='source_aligned', reason='literal_source_aligned_not_independently_reviewed')
        return item

    def _retain_attributed(self,doc,units,known):
        """Recomposition may add knowledge; omissions cannot erase source claims.

        Every candidate still passes the same literal, unit and ownership gates.
        The origin marker is provenance, never an independent review badge.
        """
        for claim in doc.get('attributed_claims',[]):
            subject=known.get(claim.get('entity_id'))
            if not subject:continue
            raw=claim.get('fact');allowed=set(claim.get('provided_span_ids',[]))
            try:
                item=self._item(doc,subject,'fact',raw,units,allowed,known)
            except (ContractError,TypeError,AttributeError) as exc:
                doc.setdefault('attribution_diagnostics',[]).append({'entity_id':subject['id'],
                    'reason':'invalid_atomic_claim','error':str(exc),'output':raw})
                continue
            item.update(origin_stage='knowledge_attribute',retained_from_attribution=True)
            gate_ownership(doc,item,units)
            doc['facts'].append(item)

    async def _recover_unassigned(self,doc,result,units,known):
        """One source batch at most, under the same total request cap.

        Discovery salience is a navigation choice, not permission to lose a
        section's factual body. Recovered candidates use the ordinary gates.
        This runs after optional review; it never restarts previous requests.
        """
        recovery_context = self._request_context(doc, {'registry':self._registry(doc,include_catalog=True)+[
            {k:e[k] for k in ('id','name','kind','context','description','salience')}
            for e in doc['references'] if e['salience']!='catalog'],
            'document_context':doc.get('document_context',[])})
        package=recovery_package(doc,self.settings,context=recovery_context)
        if not package:return
        job={'status':'pending','source_ids':package['unassigned_span_ids'],
             'remaining_source_ids':package['remaining_span_ids']}
        doc.setdefault('recovery_jobs',[]).append(job)
        allowed={s['id'] for s in package['source_spans']}
        context = {**recovery_context, **{k: v for k, v in package.items() if k != 'remaining_span_ids'}}
        job['prompt_chars'] = prompt_chars(prompts.RECOVER, context)
        if not package['unassigned_span_ids']:
            job.update(status='failed', error='Recovery source/context cannot fit; request not sent; remaining sources retained')
            return
        try:
            answer=await self.runtime.ask_async('knowledge_recover',prompts.RECOVER,
                context,[],
                lambda a:{'objects':checked_list(a,'objects'),'unresolved':a.get('unresolved',[])})
            targets={e['id']:e for e in [*doc['entities'],*doc['references']]}
            failures=[];recovered=[]
            for raw in answer['objects']:
                try:
                    supplied=raw.get('id','')
                    if not isinstance(supplied,str) or supplied and supplied not in targets:
                        raise ContractError('Recovery cited unknown identity')
                    subject=targets.get(supplied)
                    if subject is None:
                        name,kind,context=raw.get('name'),raw.get('kind'),raw.get('context','')
                        if not isinstance(name,str) or not name.strip() or kind not in {'entity','concept'} or not isinstance(context,str):
                            raise ContractError('Invalid recovered identity')
                        mentions,errors=self._evidence(raw.get('mentions'),units,allowed)
                        if errors:raise ContractError('Recovered identity lacks valid source')
                        subject=next((e for e in targets.values() if e['kind']==kind
                            and canonical_name(e['name'])==canonical_name(name)
                            and canonical_name(e['context'])==canonical_name(context)),None)
                        if subject is None:
                            subject={'id':'e_'+content_hash([doc['id'],kind,canonical_name(name),canonical_name(context)])[:16],
                                'name':name,'kind':kind,'context':context,'aliases':[],
                                'description':str(raw.get('description','')),'mentions':mentions,
                                'salience':'support','unit_ids':[],'chunk_ids':[],'status':'candidate'}
                    items=[]
                    facts=raw.get('facts')
                    if not isinstance(facts,list):raise ContractError('Invalid recovered facts')
                    for fact in facts:
                        if not isinstance(fact,dict):
                            failures.append({'reason':'invalid_recovery_fact','output':fact});continue
                        try:
                            item=self._item(doc,subject,'fact',fact,units,allowed,{**known,subject['id']:subject})
                            if item['evidence']:
                                item.update(origin_stage='knowledge_recover',recovered_from_unassigned=True)
                                items.append(item)
                            else:failures.append({'reason':'recovery_fact_without_valid_source','output':fact})
                        except ContractError as exc:failures.append({'reason':str(exc),'output':fact})
                    if not items:continue
                    if subject['id'] not in known:
                        if len(doc['entities'])>=self.settings.max_entities:
                            failures.append({'reason':'entity_budget_exhausted','output':raw});continue
                        if subject in doc['references']:doc['references'].remove(subject)
                        subject.update(salience='focus',source_anchor_pages=source_pages(subject,units))
                        doc['entities'].append(subject);known[subject['id']]=subject;targets[subject['id']]=subject
                    source_ids={e['span_id'] for item in items for e in item['evidence']}
                    union=set(subject['unit_ids'])|source_ids
                    subject['unit_ids']=[u['id'] for u in doc['units'] if u['id'] in union]
                    subject['chunk_ids']=[c['id'] for c in doc['chunks'] if source_ids.intersection(c['unit_ids'])
                                          or c['id'] in subject['chunk_ids']]
                    for item in items:
                        gate_ownership(doc,item,units);doc['facts'].append(item);recovered.append(item['id'])
                    doc['synthesis_jobs'].append({'entity_id':subject['id'],'part':0,'stage':'knowledge_recover',
                        'unit_ids':sorted(source_ids),'status':'partial' if raw.get('unresolved') else 'completed',
                        'unresolved':raw.get('unresolved',[])})
                except (ContractError,KeyError,TypeError) as exc:
                    failures.append({'reason':str(exc),'output':raw})
            doc['facts']=self._deduplicate_items(doc['facts'])
            job.update(status='partial' if failures or answer['unresolved'] or package['remaining_span_ids'] else 'completed',
                fact_ids=recovered,unresolved=answer['unresolved'],quarantined=failures)
        except (ContractError,ModelError) as exc:job.update(status='failed',error=str(exc))
        self._save(result)

    def _review_context(self, subject, items, units):
        keys = {e['span_id'] for item in items for e in item['evidence']}
        for item in items:
            context=item.get('ownership_context',{})
            keys.update(key for key in (context.get('header_span_id'),context.get('end_span_id')) if key in units)
        context = {'subject': {k: subject[k] for k in ('id', 'name', 'kind', 'context', 'aliases', 'description')},
                   'items': items, 'source_spans': wire_units(units, sorted(keys))}
        return request_directory(context, self.settings.max_prompt_chars // 3)[0]

    def _review_batches(self, subject, items, units, *, instruction=prompts.VERIFY):
        batch = []
        for item in items:
            proposed = [*batch, item]
            size = prompt_chars(instruction, self._review_context(subject, proposed, units))
            if batch and (len(proposed) > 64 or size > self.settings.max_prompt_chars):
                yield batch
                batch = []
            batch.append(item)
            if prompt_chars(instruction, self._review_context(subject, batch, units)) > self.settings.max_prompt_chars:
                raise RequestCapacityError('Review item/context exceeds capacity; item retained pending; request was not sent')
        if batch:
            yield batch

    async def _verify(self, subject, items, units, *, stage='knowledge_verify', instruction=prompts.VERIFY):
        for item in items:
            item.update(status='pending', reason='awaiting_semantic_review')
        expected = {item['id'] for item in items}
        def validate(answer):
            rows = checked_list(answer, 'verdicts')
            if len(rows) != len(expected) or {r.get('id') for r in rows} != expected:
                raise ContractError('Verification omitted or duplicated candidates')
            for row in rows:normalize_qualifier_verdict(row)
            return answer
        try:
            answer = await self.runtime.ask_async(stage, instruction,
                self._review_context(subject, items, units), [], validate)
            verdicts = {v['id']: v for v in answer['verdicts']}
            for item in items:
                v = verdicts[item['id']]
                checks = ('source_supported', 'identity_supported', 'meaning_supported', 'scope_supported', 'qualifiers_supported')
                item['review'] = v
                if v.get('decision') == 'rejected':
                    item.update(status='rejected', reason=str(v.get('reason', 'model_rejected')))
                elif not item['quality_issues'] and v.get('decision') == 'supported' and all(v.get(k) is True for k in checks):
                    item.update(status='verified_model', reason='source_aligned_and_model_supported')
                else:
                    item['reason'] = str(v.get('reason', 'insufficient_semantic_support'))
            return True
        except (ModelError, ContractError) as exc:
            for item in items:
                item['reason'] = str(exc)
            return False

    @staticmethod
    def _deduplicate_items(items):
        """Only identical assertions merge. Preserve evidence and disagreements.

        Identity includes every semantic field, so different scopes, conditions,
        units or roles are never collapsed just because their values match.
        """
        unique = {}
        for item in items:
            if item['id'] not in unique:
                unique[item['id']] = item
                continue
            first = unique[item['id']]
            first['occurrences'] = first.get('occurrences', 1) + item.get('occurrences', 1)
            evidence = {(e['span_id'], e['quote']): e for e in [*first['evidence'], *item['evidence']]}
            first['evidence'] = list(evidence.values())
            first['quality_issues'] = list(dict.fromkeys([*first['quality_issues'], *item['quality_issues']]))
            if 'review' in item:
                if 'reviews' not in first:
                    first['reviews'] = [first['review']] if 'review' in first else []
                first['reviews'].append(item['review'])
            if first['status'] != item['status'] or first['quality_issues']:
                first.update(status='pending', reason='merged_assertion_requires_review')
        return list(unique.values())

    @staticmethod
    def _conflicts(doc):
        groups = {}
        for fact in doc['facts']:
            key = content_hash([fact.get(k) for k in ('entity_id', 'field', 'scope', 'role', 'condition', 'time')])
            groups.setdefault(key, []).append(fact)
        for key, rows in groups.items():
            values = {(f['value'], f['unit'], f['unit_basis'], f['qualifier']) for f in rows
                      if f['status'] in {'verified_model', 'source_aligned'}}
            if len(values) > 1:
                for fact in rows:
                    if fact['status'] in {'verified_model', 'source_aligned'}:
                        fact.update(status='pending', reason='conflicting_source_values', conflict_id=key)

    @staticmethod
    def _finish_document(doc, prepared):
        doc['source_accounting']=source_accounting(doc)
        sparse = [p['page'] for p in doc['pages'] if p.get('graphics_unread') or p['status'] == 'unread_graphics']
        complete = bool(doc['chunks'] and doc['entities']) and all(
            entry['status'] == 'completed' for stage in ('discovery_batches', 'attribution_batches', 'synthesis_jobs') for entry in doc[stage])
        complete = complete and all(c['attribution_status'] == 'completed' for c in doc['chunks']) and not doc['errors']
        complete = complete and all(e.get('status') == 'organized' for e in doc['entities'])
        complete = complete and all(j['status']=='completed' for j in doc.get('ownership_jobs',[]))
        complete = complete and all(j['status']=='completed' for j in doc.get('review_jobs',[]))
        complete = complete and all(j['status']=='completed' for j in doc.get('recovery_jobs',[]))
        complete = complete and not doc['source_accounting']['pages_without_attribution']
        doc['coverage'] = {'total_pages': doc['source_page_count'],
            'selected_pages': doc.get('selection', {}).get('selected_pages', []),
            'unselected_pages': doc.get('selection', {}).get('unselected_pages', []),
            'scope': doc.get('selection', {}).get('scope', 'whole_document'),
            'native_text_pages': sum(p['status'] == 'native_text' for p in doc['pages']),
            'ocr_text_pages': sum(p['status'] == 'ocr_text' for p in doc['pages']),
            'ocr_pending_pages': [p['page'] for p in doc['pages'] if p.get('ocr_pending')],
            'full_page_ocr_pending_pages':[p['page'] for p in doc['pages'] if p.get('full_page_ocr_pending')],
            'pages_without_object_attribution':doc['source_accounting']['pages_without_attribution'],
            'unread_graphics_pages': sparse, 'text_processing_status': 'prepared' if prepared else 'completed' if complete else 'partial',
            'attributed_chunks': sum(c['attribution_status'] == 'completed' for c in doc['chunks']),
            'total_chunks': len(doc['chunks']), 'note': '原文处理覆盖不是人工事实召回率；图形页未走本路线读取。'}
        doc['coverage']['references_not_independently_organized'] = len(doc['references'])
        if hashlib.sha256(Path(doc['path']).read_bytes()).hexdigest() != doc['source_sha256']:
            doc['errors'].append('source_changed')
            for item in [*doc['facts'], *doc['relations']]:
                item.update(status='pending', reason='source_changed')
            doc['status'] = 'failed'
        else:
            doc['status'] = 'prepared' if prepared else 'completed' if complete and not sparse and not doc['coverage']['unselected_pages'] else 'partial'
