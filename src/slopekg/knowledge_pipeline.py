"""Document-wide discovery -> many-to-many attribution -> evidence-bound knowledge.

All native source chunks are visited. Identity discovery and attribution are
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

from .knowledge_document import prepare_document, batch_chunks, wire_chunks
from .knowledge_settings import VERSION, KnowledgeSettings
from . import knowledge_prompts as prompts
from .model_client import content_hash, ModelError
from .pipeline_lock import pipeline_run_guard
from .provenance import runtime_identity
from .storage import read_json, write_json, write_jsonl
from .visual_contract import ContractError, ROLES, normalize_qualifier_verdict
from .visual_evidence import source_contains, literal_supported
from .visual_runtime import VisualRuntime
from .visual_batching import compact
from .knowledge_ownership import owner_proposals


def checked_list(answer, key):
    rows = answer.get(key)
    if key == 'relations' and key in answer and rows is None:
        return []  # Explicit JSON null means no relation claim, never a fact.
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ContractError('Invalid knowledge list: ' + key)
    return rows


def canonical_name(name):
    return ''.join(unicodedata.normalize('NFC', name).casefold().split())


def wire_units(units, ids):
    # Coordinates/offsets stay in the local evidence store. The model only needs
    # stable handles, physical pages and original text; this keeps budgets honest.
    return [{k: units[key][k] for k in ('id', 'page', 'text')} for key in ids]


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
    def __init__(self, model, output_dir, *, settings=None, review_model=None, progress_callback=None):
        self.model, self.review_model = model, review_model or model
        self.output_dir = Path(output_dir).resolve()
        self.settings = settings or KnowledgeSettings()
        self.progress_callback = progress_callback
        self.runtime = VisualRuntime(model, self.review_model, self.output_dir, self.settings,
            version=VERSION, system=prompts.SYSTEM, review_stages=('knowledge_verify','knowledge_owner_verify'), recover_invalid_answers=True)

    def run(self, pdfs, *, prepare_only=False):
        return asyncio.run(self.arun(pdfs, prepare_only=prepare_only))

    def progress(self, percent, stage, message):
        if self.progress_callback:
            self.progress_callback(percent, stage, message)

    async def arun(self, pdfs, *, prepare_only=False):
        sources = [Path(p).resolve(strict=True) for p in pdfs]
        if not sources or len(set(sources)) != len(sources) or any(p.suffix.lower() != '.pdf' for p in sources):
            raise ValueError('Explicit, unique PDF inputs required')
        if any(self.output_dir == p.parent or self.output_dir in p.parents for p in sources):
            raise ValueError('Output directory must not contain source PDFs')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with pipeline_run_guard(self.output_dir / '.knowledge.lock'):
            self.runtime.reset()
            previous = read_json(self.output_dir / 'run.json', {})
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
                doc = prepare_document(source, self.settings)
                result['documents'].append(doc)
                self._save(result)
                if not prepare_only:
                    try:
                        await self._recognize(doc, result)
                    except Exception as exc:
                        doc['errors'].append(str(exc) if isinstance(exc, (ModelError, ContractError)) else type(exc).__name__)
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
            'pages': sum(d['source_page_count'] for d in result['documents']),
            'chunks': sum(len(d['chunks']) for d in result['documents']),
            'entities': sum(len(d['entities']) for d in result['documents']), 'facts': len(facts),
            'references': sum(len(d['references']) for d in result['documents']),
            'carried_forward_facts': sum(bool(f.get('carried_forward')) for f in facts),
            'relations': len(relations),
            **{status: sum(f['status'] == status for f in facts) for status in ('verified_model', 'pending', 'rejected')},
            'calls': self.runtime.calls, 'cache_hits': self.runtime.cache_hits,
            'peak_concurrency': self.runtime.peak_concurrency}
        result['receipts'] = list(self.runtime.receipts)
        result['environment_variables_used'] = sorted({v for r in self.runtime.receipts for v in r.get('environment_variables_used', [])})
        write_json(self.output_dir / 'run.json', result)
        write_jsonl(self.output_dir / 'facts.jsonl', facts)
        write_jsonl(self.output_dir / 'relations.jsonl', relations)

    @staticmethod
    def _registry(doc, include_catalog=False):
        rows = [*doc['entities'], *[e for e in doc['references'] if include_catalog and e['salience'] == 'catalog']]
        return [{k: e[k] for k in ('id', 'name', 'kind', 'context', 'aliases', 'salience')} for e in rows]

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
            elif not source_contains(quote, units[key]['text'], table=True):
                errors.append('quote_not_in_source')
            else:
                valid.append({'span_id': key, 'quote': quote, 'page': units[key]['page'], 'bbox': units[key]['bbox']})
        return valid, list(dict.fromkeys(errors))

    async def _recognize(self, doc, result):
        units = {u['id']: u for u in doc['units']}
        batches = list(batch_chunks(doc['chunks'], units, self.settings.batch_chars))
        for index, batch in enumerate(batches):
            self.progress(5 + int(20 * index / max(1, len(batches))), 'knowledge_discover',
                          f'全文对象发现 {index + 1}/{len(batches)} 批')
            entry = {'chunks': [c['id'] for c in batch], 'status': 'pending'}
            doc['discovery_batches'].append(entry)
            known = {e['id']: e for e in [*doc['entities'], *doc['references']]}
            allowed = {key for c in batch for key in c['unit_ids']}
            try:
                answer = await self.runtime.ask_async('knowledge_discover', prompts.DISCOVER,
                    {'registry': self._registry(doc, include_catalog=True), 'chunks': wire_chunks(batch, units)}, [],
                    lambda a: {'entities': checked_list(a, 'entities')})
                entry['status'] = 'completed'
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
        registry = self._registry(doc)
        known = {e['id']: e for e in doc['entities']}

        async def attribute(index, batch):
            entry = {'chunks': [c['id'] for c in batch], 'status': 'pending'}
            doc['attribution_batches'].append(entry)
            by_id = {c['id']: c for c in batch}
            def validate(answer):
                rows = checked_list(answer, 'assignments')
                # The immutable span handle is authoritative. A model may put a
                # provided span under a neighbouring chunk in the SAME request.
                # Re-index that exact claim; never borrow an unseen source span.
                owners = {}
                for chunk in batch:
                    for key in chunk['unit_ids']:
                        owners.setdefault(key, []).append(chunk['id'])
                source_order = list(units)
                positions = {key: i for i, key in enumerate(source_order)}
                normalized = {key: {'chunk_id': key, 'entities': [], 'unresolved': [], 'attribution_status': 'pending'} for key in by_id}
                bindings, seen, repairs = {}, set(), 0
                for row in rows:
                    label = row.get('chunk_id')
                    if isinstance(label, str) and label in by_id and row.get('attribution_status') != 'pending':
                        seen.add(label)
                        unresolved = row.get('unresolved', [])
                        if isinstance(unresolved, list):
                            normalized[label]['unresolved'].extend(unresolved)
                    selected = row.get('entities')
                    if not isinstance(selected, list):
                        raise ContractError('Invalid chunk identities')
                    for binding in selected:
                        if not isinstance(binding, dict):
                            raise ContractError('Invalid chunk binding')
                        key, ids = binding.get('entity_id'), binding.get('span_ids')
                        ranges = binding.get('span_ranges', [])
                        if not isinstance(ranges, list) or any(not isinstance(r, dict) for r in ranges):
                            raise ContractError('Invalid source ranges')
                        ids = list(ids) if isinstance(ids, list) else ids
                        if not isinstance(ids, list):
                            raise ContractError('Invalid source identifiers')
                        ids = [resolve_source_handle(source_id, units, owners) for source_id in ids]
                        for span_range in ranges:
                            start, end = span_range.get('start_id'), span_range.get('end_id')
                            start = resolve_source_handle(start, units, owners)
                            end = resolve_source_handle(end, units, owners)
                            if not isinstance(start, str) or not isinstance(end, str) or start not in owners or end not in owners or positions[start] > positions[end]:
                                raise ContractError('Invalid or unprovided source range endpoint')
                            expanded = source_order[positions[start]:positions[end] + 1]
                            if any(source_id not in owners for source_id in expanded):
                                raise ContractError('Source range crosses unprovided source')
                            ids.extend(expanded)
                        if not isinstance(key, str) or key not in known or not isinstance(ids, list) or any(not isinstance(v, str) or v not in owners for v in ids):
                            raise ContractError('Attribution cited unknown identity or unprovided source')
                        for source_id in dict.fromkeys(ids):
                            for owner in owners[source_id]:
                                seen.add(owner)
                                repairs += owner != label
                                slot = bindings.setdefault((owner, key), {'entity_id': key, 'span_ids': [], 'reason': []})
                                if source_id not in slot['span_ids']:
                                    slot['span_ids'].append(source_id)
                                reason = binding.get('reason', '')
                                if isinstance(reason, str) and reason and reason not in slot['reason']:
                                    slot['reason'].append(reason)
                for (owner, key), binding in bindings.items():
                    normalized[owner]['entities'].append({**binding, 'reason': '；'.join(binding['reason'])})
                for key in seen:
                    normalized[key]['attribution_status'] = 'completed'
                return {'assignments': list(normalized.values()), 'source_chunk_repairs': repairs}
            try:
                answer = await self.runtime.ask_async('knowledge_attribute', prompts.ATTRIBUTE,
                    {'registry': registry, 'chunks': wire_chunks(batch, units)}, [], validate)
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
        source_order = {u['id']: i for i, u in enumerate(doc['units'])}
        for entity in doc['entities']:
            entity['unit_ids'].sort(key=source_order.__getitem__)
            part, size = [], 0
            for key in entity['unit_ids']:
                cost = len(units[key]['text']) + 80
                if part and size + cost > self.settings.synthesis_chars:
                    doc['synthesis_jobs'].append({'entity_id': entity['id'], 'unit_ids': part, 'status': 'pending'})
                    part, size = [], 0
                part.append(key)
                size += cost
            if part:
                doc['synthesis_jobs'].append({'entity_id': entity['id'], 'unit_ids': part, 'status': 'pending'})
        # Round-robin parts: a long, broad subject cannot consume the whole
        # budget before other independent objects get their first evidence read.
        rounds = {}
        for job in doc['synthesis_jobs']:
            job['part'] = rounds.get(job['entity_id'], 0)
            rounds[job['entity_id']] = job['part'] + 1
        doc['synthesis_jobs'].sort(key=lambda job: job['part'])
        slots = asyncio.Semaphore(self.settings.model_concurrency)
        async def synthesize(index, job):
            async with slots:
                subject = known[job['entity_id']]
                try:
                    answer = await self.runtime.ask_async('knowledge_synthesize', prompts.SYNTHESIZE,
                        {'subject': {k: subject[k] for k in ('id', 'name', 'kind', 'context', 'aliases', 'description')},
                         'registry': registry, 'source_spans': wire_units(units, job['unit_ids'])}, [],
                        lambda a: {'facts': checked_list(a, 'facts'), 'relations': checked_list(a, 'relations')})
                    items = []
                    for kind, rows in (('fact', answer['facts']), ('relation', answer['relations'])):
                        for raw in rows:
                            item = self._item(doc, subject, kind, raw, units, set(job['unit_ids']), known)
                            items.append(item)
                    items = self._deduplicate_items(items)
                    for item in items:
                        doc['facts' if item['kind'] == 'fact' else 'relations'].append(item)
                    job['status'] = 'completed'
                    for review_batch in self._review_batches(subject, items, units):
                        if not await self._verify(subject, review_batch, units):
                            job['status'] = 'partial'
                except (ModelError, ContractError) as exc:
                    job.update(status='failed', error=str(exc))
                self.progress(45 + int(48 * (index + 1) / max(1, len(doc['synthesis_jobs']))), 'knowledge_synthesize',
                              f'知识重组与核验 {index + 1}/{len(doc["synthesis_jobs"])} 组')
                self._save(result)
        await asyncio.gather(*(synthesize(i, j) for i, j in enumerate(doc['synthesis_jobs'])))
        self._retain_previous(doc, units, known)
        await self._reconcile_owners(doc, units, known)
        for key in ('facts', 'relations'):
            doc[key] = self._deduplicate_items(doc[key])
        self._conflicts(doc)
        for entity in doc['entities']:
            entity['fact_ids'] = [f['id'] for f in doc['facts'] if f['entity_id'] == entity['id']]
            jobs = [j for j in doc['synthesis_jobs'] if j['entity_id'] == entity['id']]
            entity['status'] = 'organized' if jobs and all(j['status'] == 'completed' for j in jobs) else 'partial'

    async def _reconcile_owners(self, doc, units, known):
        """Propose an actual subject from explicit scope; never copy a pass."""
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
                else:
                    item['reason'] = 'prior_candidate_retained_pending_review'
                doc[collection].append(item)
                present.add(item['id'])

    def _item(self, doc, subject, kind, raw, units, allowed, known):
        evidence, errors = self._evidence(raw.get('evidence'), units, allowed)
        keys = ('field', 'value', 'unit', 'unit_basis', 'qualifier', 'scope', 'role', 'condition', 'time') if kind == 'fact' else ('target_id', 'predicate', 'scope')
        if any(not isinstance(raw.get(key, ''), str) for key in keys):
            raise ContractError('Knowledge fields must be strings')
        item = {key: raw.get(key, '') for key in keys}
        quotes = [e['quote'] for e in evidence]
        if kind == 'fact':
            if not item['field'] or not item['value'] or item['role'] not in ROLES or item['role'] == 'unknown':
                errors.append('incomplete_fact_semantics')
            if not any(literal_supported(item['value'], q) or source_contains(item['value'], q, table=True) for q in quotes):
                errors.append('value_not_in_cited_source')
            for key in ('unit', 'unit_basis'):
                if item[key] and not any(literal_supported(item[key], q, unit=True) for q in quotes):
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
        return item

    def _review_context(self, subject, items, units):
        keys = {e['span_id'] for item in items for e in item['evidence']}
        for item in items:
            context=item.get('ownership_context',{})
            keys.update(key for key in (context.get('header_span_id'),context.get('end_span_id')) if key in units)
        return {'subject': {k: subject[k] for k in ('id', 'name', 'kind', 'context', 'aliases', 'description')},
                'items': items, 'source_spans': wire_units(units, sorted(keys))}

    def _review_batches(self, subject, items, units, *, instruction=prompts.VERIFY):
        batch = []
        for item in items:
            proposed = [*batch, item]
            size = len(compact(self._review_context(subject, proposed, units))) + len(instruction) + len(prompts.SYSTEM) + 16
            if batch and (len(proposed) > 12 or size > self.settings.max_prompt_chars):
                yield batch
                batch = []
            batch.append(item)
        if batch:
            yield batch

    async def _verify(self, subject, items, units, *, stage='knowledge_verify', instruction=prompts.VERIFY):
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
            values = {(f['value'], f['unit'], f['unit_basis'], f['qualifier']) for f in rows if f['status'] == 'verified_model'}
            if len(values) > 1:
                for fact in rows:
                    if fact['status'] == 'verified_model':
                        fact.update(status='pending', reason='conflicting_source_values', conflict_id=key)

    @staticmethod
    def _finish_document(doc, prepared):
        sparse = [p['page'] for p in doc['pages'] if p['status'] == 'unread_graphics']
        complete = bool(doc['chunks'] and doc['entities']) and all(
            entry['status'] == 'completed' for stage in ('discovery_batches', 'attribution_batches', 'synthesis_jobs') for entry in doc[stage])
        complete = complete and all(c['attribution_status'] == 'completed' for c in doc['chunks']) and not doc['errors']
        complete = complete and all(e.get('status') == 'organized' for e in doc['entities'])
        complete = complete and all(j['status']=='completed' for j in doc.get('ownership_jobs',[]))
        doc['coverage'] = {'total_pages': doc['source_page_count'],
            'native_text_pages': sum(p['status'] == 'native_text' for p in doc['pages']),
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
            doc['status'] = 'prepared' if prepared else 'completed' if complete and not sparse else 'partial'
