"""Plan first; execute only an explicitly authorized incremental diagnostic.

Visual continuation targets wholly unread pages, retaining previous readings.
Neither route automatically resends uncertain/failed historical transactions.
"""
import argparse,asyncio,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from slopekg.storage import read_json,write_json,write_jsonl
from slopekg.model_client import ModelProfile,CompatibleVisionClient
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.visual_pipeline import VisualPipeline,VisualSettings,consolidate,resolve_conflicts
from slopekg.pipeline_lock import pipeline_run_guard
from slopekg.provenance import runtime_identity
from slopekg.knowledge_render import render_knowledge

def transactions(folder):
    return sum(max(1,len(read_json(path).get('attempts',[]))) for path in folder.glob('*.json'))

def unread_pages(run):
    return [p['page'] for d in run['documents'] for p in d['pages']
            if not any(r.get('reading') for r in p['regions'])]

def merge_visual(old,new):
    a,b=old['documents'][0],new['documents'][0]
    digest=lambda d:d.get('sha256',d.get('source_sha256'))
    if not digest(a) or digest(a)!=digest(b) or a['id']!=b['id'] or old['model']!=new['model']:
        raise ValueError('Visual continuation requires identical source bytes and model')
    selected={p['page'] for p in b['pages']}
    if not selected<=set(unread_pages(old)):
        raise ValueError('Continuation cannot silently reread previously read pages')
    b['pages']=sorted([*[p for p in a['pages'] if p['page'] not in selected],*b['pages']],key=lambda p:p['page'])
    combined={f['id']:f for f in [*a['facts'],*b['facts']]}
    b['facts']=consolidate(list(combined.values()));resolve_conflicts(b['facts'])
    b['coverage']={'total_pages':len(b['pages']),'completed_pages':sum(p['status']=='completed' for p in b['pages']),
        'region_note':'Preserved old readings plus explicitly selected previously unread pages; not measured recall.'}
    b['status']='completed' if b['coverage']['completed_pages']==len(b['pages']) else 'partial'
    new['processing_status']=b['status']
    new['summary'].update(pages=len(b['pages']),facts=len(b['facts']),
        **{s:sum(f['status']==s for f in b['facts']) for s in ('verified_model','pending','rejected')})
    new['incremental_scope']={'new_target_pages':sorted(selected),'prior_target_pages':[p['page'] for p in a['pages']],
        'prior_facts_retained':len(a['facts']),'prior_producer':old.get('producer')}
    return new

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--route',choices=['knowledge','multimodal'],required=True)
    parser.add_argument('--max-calls',type=int)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--ownership-only',action='store_true',help='Recheck existing topic facts against explicit document scopes')
    args=parser.parse_args();out=args.output.resolve();protocol=read_json(out/'protocol.json')
    source=Path(protocol['source']).resolve(strict=True)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=protocol['source_sha256']:raise ValueError('Source changed')
    current=read_json(out/args.route/'run.json')
    spent=transactions(out/args.route/'cache')+(transactions(out/'knowledge-v1-initial/cache') if args.route=='knowledge' else 0)
    pages=unread_pages(current) if args.route=='multimodal' else []
    plan={'route':args.route,'spent':spent,'unread_visual_targets':pages,'source_sha256':protocol['source_sha256']}
    if not args.execute:print(json.dumps(plan,ensure_ascii=False));return
    auth=read_json(out/'remaining_request_proposal.json',{})
    if auth.get('status')!='authorized':raise ValueError('Additional requests require explicit user approval')
    ceiling=auth[args.route]['proposed_total_limit'];remaining=ceiling-spent
    limit=args.max_calls if args.max_calls is not None else remaining
    if not 1<=limit<=remaining:raise ValueError('Additional call allowance exceeded')
    model=CompatibleVisionClient(ModelProfile.load(ROOT/'config/models/deepseek-knowledge.json'),ROOT)
    def progress(percent,stage,message):print(json.dumps({'percent':percent,'stage':stage,'message':message},ensure_ascii=False),flush=True)
    if args.route=='knowledge':
        pipeline=KnowledgePipeline(model,out/'knowledge',settings=KnowledgeSettings(max_calls=limit,model_concurrency=3),progress_callback=progress)
        if args.ownership_only:
            with pipeline_run_guard(out/'knowledge/.knowledge.lock'):
                write_json(out/f'knowledge-before-ownership-{spent}.json',current)
                pipeline.runtime.reset();result=current;result['producer']=runtime_identity()
                for doc in result['documents']:
                    units={u['id']:u for u in doc['units']};known={e['id']:e for e in doc['entities']}
                    asyncio.run(pipeline._reconcile_owners(doc,units,known))
                    pipeline._conflicts(doc)
                    for e in doc['entities']:e['fact_ids']=[f['id'] for f in doc['facts'] if f['entity_id']==e['id']]
                    pipeline._finish_document(doc,False)
                result['processing_status']='completed' if all(d['status']=='completed' for d in result['documents']) else 'partial'
                result['ownership_reconciliation']='Explicit heading scopes; every ownership change separately reviewed'
                pipeline._save(result);render_knowledge(result,out/'knowledge/index.html')
        else:
            result=pipeline.run([source])
    else:
        if not pages or not set(pages)<=set(protocol['visual_pages']):raise ValueError('No new target pages in frozen scope')
        write_json(out/f'multimodal-before-continuation-{spent}.json',current)
        result=VisualPipeline(model,out/'multimodal',settings=VisualSettings(max_calls=limit,model_concurrency=3),progress_callback=progress).run([source],page_selections={str(source):pages})
        result=merge_visual(current,result)
        write_json(out/'multimodal/run.json',result)
        write_jsonl(out/'multimodal/facts.jsonl',[f for d in result['documents'] for f in d['facts']])
    final_spent=transactions(out/args.route/'cache')+(transactions(out/'knowledge-v1-initial/cache') if args.route=='knowledge' else 0)
    if final_spent>ceiling:raise RuntimeError('Cumulative allowance integrity failure')
    write_json(out/(args.route+'_continuation_receipt.json'),{**plan,'additional_limit':limit,'cumulative_spent':final_spent,'summary':result['summary']})
    print(json.dumps({'cumulative_spent':final_spent,'summary':result['summary']},ensure_ascii=False))

if __name__=='__main__':main()
