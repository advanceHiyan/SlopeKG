"""Fourth route: source reconstruction -> overlapping dossiers -> claims -> review.

Reuses transport/rendering only. It never invokes Knowledge discovery/attribution
or writes candidates into the production graph. Full-source grouping currently
requires the reconstructed document to fit its explicit request capacity.
"""
from dataclasses import asdict, replace
from copy import deepcopy
import time
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

from . import document_prompts as prompts
from .document_settings import DocumentSettings, VERSION
from .document_contract import reconstruct, zone_plan, extract_claims, reconcile, consolidate
from .model_client import ModelError, content_hash
from .pipeline_lock import pipeline_run_guard
from .provenance import runtime_identity
from .storage import write_json, write_jsonl, read_json
from .visual_contract import ContractError
from .visual_document import VisualDocument
from .visual_runtime import VisualRuntime
from .document_workflow import wire_claim, repair_source_links, apply_review, audit_index, validate_audit


def wire_block(block):
    return {k:block[k] for k in ('id','page','kind','text','bbox','source_ids','source_type','heading_path') if k in block}


def fits(instruction,context,settings):
    return ((instruction not in {prompts.EXTRACT,prompts.RECONCILE} or len({b['page'] for b in context.get('blocks',[])})<=settings.max_images)
        and len(prompts.SYSTEM)+len(instruction)+len('\n输入：')+len(json.dumps(context,ensure_ascii=False,separators=(',',':'))) <= settings.max_prompt_chars)


def extraction_batches(groups,blocks,settings,links=()):
    """Prefer whole overlapping groups, deduplicate sources in each request.

    Only transport overflow divides a dossier, at existing block boundaries.
    A block too large to fit is an explicit failure, never character truncation.
    """
    index={b['id']:b for b in blocks}
    def context(batch):
        ids={i for g in batch for i in g['source_block_ids']}
        return {'groups':batch,'blocks':[wire_block(b) for b in blocks if b['id'] in ids],
                'links':[link for link in links if link['from'] in ids and link['to'] in ids]}
    pieces=[]
    for group in groups:
        if fits(prompts.EXTRACT,context([group]),settings):pieces.append(group);continue
        current=[];part=0
        for key in group['source_block_ids']:
            proposed={**group,'source_block_ids':current+[key],'transport_part':part}
            if current and not fits(prompts.EXTRACT,context([proposed]),settings):
                pieces.append({**group,'source_block_ids':current,'transport_part':part})
                current=[];part+=1
            current.append(key)
            if not fits(prompts.EXTRACT,context([{**group,'source_block_ids':current,'transport_part':part}]),settings):
                raise ContractError('Whole reconstructed block exceeds request capacity; retained unread: '+index[key]['id'])
        if current:pieces.append({**group,'source_block_ids':current,'transport_part':part})
    batch=[]
    for group in pieces:
        if batch and (any(g['id']==group['id'] for g in batch) or not fits(prompts.EXTRACT,context(batch+[group]),settings)):
            yield context(batch);batch=[]
        batch.append(group)
    if batch:yield context(batch)


class DocumentPipeline:
    def __init__(self,model,output_dir,*,settings=None,review_model=None,progress_callback=None):
        self.model=model;self.review_model=review_model or model;self.output_dir=Path(output_dir).resolve()
        self.settings=settings or DocumentSettings();self.progress_callback=progress_callback
        self.runtime=VisualRuntime(model,self.review_model,self.output_dir,self.settings,version=VERSION,
            system=prompts.SYSTEM,review_stages=('document_zone_review','document_reconcile','document_cross_audit'),recover_invalid_answers=True)

    def progress(self,stage,message):
        if self.progress_callback:self.progress_callback(0,stage,message)

    def _save(self,result):
        result['receipts']=list(self.runtime.receipts)
        result['environment_variables_used']=sorted({v for r in result['receipts'] for v in r.get('environment_variables_used',[])})
        docs=result['documents'];facts=[f for d in docs for f in d['facts']]
        result['summary']={'documents':len(docs),'pages':sum(len(d['pages']) for d in docs),
            'blocks':sum(len(d['blocks']) for d in docs),'groups':sum(len(d['groups']) for d in docs),
            'claims':sum(len(d['claims']) for d in docs),'facts':len(facts),
            'source_aligned':sum(f['status']=='source_aligned' for f in facts),
            'model_supported':sum(f['status']=='model_supported' for f in facts),
            'pending':sum(f['status']=='pending' for f in facts),'rejected':sum(f['status']=='rejected' for f in facts),
            'calls':self.runtime.calls,'cache_hits':self.runtime.cache_hits,'peak_concurrency':self.runtime.peak_concurrency}
        write_json(self.output_dir/'run.json',result)

    def _ask(self,result,doc,stage,instruction,context,images,validate):
        started=time.monotonic()
        job={'stage':stage,'status':'started','image_pages':[i.label for i in images],
             'prompt_chars':len(prompts.SYSTEM)+len(instruction)+len('\n输入：')+len(json.dumps(context,ensure_ascii=False,separators=(',',':')))}
        doc['stages'].append(job);self._save(result);self.progress(stage,stage+' · '+str(len(doc['stages'])))
        try:
            def checked(value):
                raw=value.get('_document_raw',value)
                return {'_document_raw':raw,'_document_validated':validate(raw)}
            # Reconstruction's exact protocol is unchanged; old raw responses
            # are revalidated. Changed prompts/images still produce new keys.
            self.runtime.version='document-v0.1' if stage=='document_reconstruct' else VERSION
            answer=self.runtime.ask(stage,instruction,context,images,checked)
            job['status']='completed';return answer['_document_validated']
        except (ModelError,ContractError) as exc:
            job.update(status='failed',error=str(exc));raise
        finally:
            self.runtime.version=VERSION
            job['elapsed_seconds']=round(time.monotonic()-started,3);self._save(result)

    def run(self,pdfs,*,prepare_only=False,page_selections=None):
        sources=[Path(p).resolve(strict=True) for p in pdfs]
        if not sources or len(set(sources))!=len(sources) or any(p.suffix.lower()!='.pdf' for p in sources):
            raise ValueError('Explicit unique PDFs required')
        if any(self.output_dir==p.parent or self.output_dir in p.parents for p in sources):
            raise ValueError('Output must not contain input PDFs')
        selections={str(Path(k).resolve()):v for k,v in (page_selections or {}).items()}
        if page_selections is not None and set(selections)!={str(p) for p in sources}:raise ValueError('Selections must cover exactly the input PDFs')
        self.output_dir.mkdir(parents=True,exist_ok=True)
        with pipeline_run_guard(self.output_dir/'.document.lock'):
            self.runtime.reset()
            previous=read_json(self.output_dir/'run.json',{})
            if previous:write_json(self.output_dir/'history'/(content_hash(previous)+'.json'),previous)
            result={'schema_version':VERSION,'recognizer':'document','producer':runtime_identity(),
                'model':self.model.identity(),'review_model':self.review_model.identity(),'settings':asdict(self.settings),
                'started_at':datetime.now(timezone.utc).isoformat(),'processing_status':'running','documents':[],
                'precision':None,'recall':None,'prepare_only':prepare_only,
                'metric_note':'实验第四路；结构覆盖、引文匹配及同模型核对不等于准确率、召回率或工程签认。'}
            for source in sources:
                document=VisualDocument(source,self.output_dir/'assets'/hashlib.sha256(source.read_bytes()).hexdigest()[:20])
                try:
                    numbers=selections.get(str(source),list(range(1,len(document.pdf)+1)))
                    if not isinstance(numbers,list) or not numbers or any(type(n) is not int or not 1<=n<=len(document.pdf) for n in numbers):
                        raise ValueError('Invalid physical page selection')
                    numbers=sorted(set(numbers))
                    doc={'id':document.id,'path':str(source),'source_sha256':document.sha256,'source_page_count':len(document.pdf),
                        'selection':{'scope':'whole_document' if page_selections is None else 'selected_pages','selected_pages':numbers},
                        'pages':[],'blocks':[],'groups':[],'claims':[],'facts':[],'relations':[],'stages':[],
                        'errors':[],'unresolved':[],'status':'prepared' if prepare_only else 'running'}
                    result['documents'].append(doc);self.runtime.document_id=doc['id']
                    images={}
                    for number in numbers:
                        image=document.render(number,edge=self.settings.page_edge,max_pixels=self.settings.max_pixels_per_image)
                        images[number]=image
                        geometry=document.geometric_evidence(number)
                        native=[a for a in geometry if a['source']=='pdf_native_line']
                        doc['pages'].append({**document.page_info(number),'native_sources':native,
                            'image':str(image.path.relative_to(self.output_dir)).replace('\\','/'),
                            'image_sha256':hashlib.sha256(image.path.read_bytes()).hexdigest(),
                            'has_visible_content':bool(geometry),'status':'prepared'})
                    self._save(result)
                    if not prepare_only:
                        try:self._read(result,doc,images)
                        except (ModelError,ContractError) as exc:
                            doc['errors'].append(str(exc));doc['status']='partial' if doc['blocks'] or doc['claims'] else 'failed'
                        # Retain all successfully emitted candidates even if a later stage failed.
                        if doc['claims'] and not doc['facts']:doc['facts']=consolidate(doc['claims'])
                    if document._hash()!=document.sha256:raise ValueError('Source changed during run')
                finally:document.close()
                self._save(result)
            states=[d['status'] for d in result['documents']]
            result['processing_status']='prepared' if prepare_only else ('completed' if all(s=='completed' for s in states) else 'failed' if all(s=='failed' for s in states) else 'partial')
            result['finished_at']=datetime.now(timezone.utc).isoformat();self._save(result)
            write_jsonl(self.output_dir/'facts.jsonl',[f for d in result['documents'] for f in d['facts']])
            from .document_render import render_report
            render_report(result,self.output_dir)
            return result

    def _read(self,result,doc,images):
        for start in range(0,len(doc['pages']),self.settings.window_pages):
            pages=doc['pages'][start:start+self.settings.window_pages]
            context={'pages':[{k:p[k] for k in ('page','width','height','native_sources','has_visible_content')} for p in pages]}
            answer=self._ask(result,doc,'document_reconstruct',prompts.RECONSTRUCT,context,
                [images[p['page']] for p in pages],lambda a:reconstruct(a,pages))
            for page in answer['pages']:
                state=next(p for p in doc['pages'] if p['page']==page['page']);state.update(page)
                doc['blocks'].extend(page['blocks'])
            self._save(result)
        doc['source_repairs']=repair_source_links(doc['pages'])
        doc['repair_queue']=[]
        repair_pages=[p for p in doc['pages'] if p.get('issues')]
        for page in repair_pages[:self.settings.max_structure_repairs]:
            before=deepcopy(page)
            context={'pages':[{k:page[k] for k in ('page','width','height','native_sources','has_visible_content')}],
                     'repair_reason':page['issues']}
            try:
                fixed=self._ask(result,doc,'document_repair_structure',prompts.RECONSTRUCT,context,
                    [images[page['page']]],lambda a:reconstruct(a,[page]))['pages'][0]
                page.update(fixed)
                doc['source_repairs'].append({'action':'reconstruct_page_once','before':before,'after':deepcopy(fixed)})
                doc['source_repairs'].extend(repair_source_links([page]))
            except (ModelError,ContractError) as exc:
                doc['repair_queue'].append({'stage':'reconstruction','page':page['page'],'reason':str(exc)})
        for page in doc['pages']:
            if page.get('issues'):
                doc['repair_queue'].append({'stage':'reconstruction','page':page['page'],'reason':page['issues']})
        doc['blocks']=[b for page in doc['pages'] for b in page['blocks']]
        blocks=doc['blocks'];context={'blocks':[wire_block(b) for b in blocks]}
        if not fits(prompts.ZONE,context,replace(self.settings,max_images=12)):
            raise ContractError('Full document zoning exceeds capacity; no source truncation')
        proposed=self._ask(result,doc,'document_zone',prompts.ZONE,context,[],lambda a:zone_plan(a,blocks))
        doc['proposed_plan']=proposed;doc['reviewed_plan']=proposed
        doc['zone_review_mode']='integrated_with_dossier_source_review'
        doc['groups']=proposed['groups'];doc['structure_links']=proposed['links'];doc['coverage']=proposed['coverage']
        self._save(result)
        planning=replace(self.settings,max_prompt_chars=max(12000,min(self.settings.dossier_chars,self.settings.max_prompt_chars)))
        batches=list(extraction_batches(doc['groups'],blocks,planning,doc['structure_links']))
        doc['dossier_plan']={'batches':len(batches),'minimum_remaining_calls':2*len(batches)+1,
            'note':'Each extraction is followed by source review; actual review overflow is split with full context.'}
        doc['dossier_reviews']=[];doc['completed_batches']=[]
        for number,batch in enumerate(batches):
            ids={b['id'] for b in batch['blocks']};provided=[b for b in blocks if b['id'] in ids]
            try:
                # Reserve a review before spending a new extraction request.
                self.runtime.reserve_calls(1 if self.runtime.calls<self.settings.max_calls else 0)
                answer=self._ask(result,doc,'document_extract',prompts.EXTRACT,batch,[],
                    lambda a:extract_claims(a,provided,batch['groups'],doc['id']))
                self.runtime.reserve_calls(0)
                doc['claims'].extend(answer['claims']);doc['unresolved'].extend(answer['unresolved']);self._save(result)
                self._review_batch(result,doc,batch,provided,answer['claims'],images)
                doc['completed_batches'].append(number)
            except (ModelError,ContractError) as exc:
                doc['errors'].append('dossier '+str(number)+': '+str(exc))
            finally:self.runtime.reserve_calls(0)
            doc['facts']=consolidate(doc['claims']);self._save(result)
        ctx=audit_index(doc['facts'],doc['groups'],doc['structure_links'])
        try:
            audit=self._ask(result,doc,'document_cross_audit',prompts.CROSS_AUDIT,ctx,[],lambda a:validate_audit(a,doc['facts']))
            doc['cross_group_audit']=audit
            for flag in audit['flags']:
                for c in doc['claims']:
                    if c['id'] in flag['claim_ids'] and c['status']!='rejected':
                        c.update(status='pending',reason='cross_group_review: '+flag['reason'])
                doc['repair_queue'].append({'stage':'ownership','claim_ids':flag['claim_ids'],'reason':flag['reason']})
        except (ModelError,ContractError) as exc:
            doc['errors'].append('cross_group_audit: '+str(exc))
        doc['facts']=consolidate(doc['claims']);doc['relations']=[f for f in doc['facts'] if f['kind']=='relation']
        doc['workflow_complete']=len(doc['completed_batches'])==len(batches) and 'cross_group_audit' in doc
        doc['status']='partial' if (not doc['workflow_complete'] or doc['errors'] or doc['repair_queue'] or doc['unresolved']
            or any(p['status']=='partial' for p in doc['pages']) or proposed['issues']
            or any(f['status']=='pending' for f in doc['facts'])) else 'completed'

    def _review_batch(self,result,doc,batch,blocks,claims,images):
        # A later batch can fail without undoing reviews of earlier batches.
        unique=list({c['id']:c for c in claims}.values())
        context={**batch,'claims':[wire_claim(c) for c in unique]}
        if not fits(prompts.RECONCILE,context,self.settings):
            if len(unique)<2:raise ContractError('Review context cannot fit with complete evidence; candidates pending')
            middle=len(unique)//2
            self._review_batch(result,doc,batch,blocks,unique[:middle],images)
            self._review_batch(result,doc,batch,blocks,unique[middle:],images)
            return
        review=self._ask(result,doc,'document_reconcile',prompts.RECONCILE,context,
            [images[p] for p in sorted({b['page'] for b in blocks})],
            lambda a:reconcile(a,unique,blocks,batch['groups'],doc['id']))
        apply_review(claims,review)
        doc['claims'].extend(review['additions']);doc['dossier_reviews'].append(review)
        doc['unresolved'].extend(review.get('invalid_additions',[]))
        doc['repair_queue'].extend({'stage':'reconstruction','request':r} for r in review['repair_requests'])
        doc['facts']=consolidate(doc['claims']);self._save(result)
