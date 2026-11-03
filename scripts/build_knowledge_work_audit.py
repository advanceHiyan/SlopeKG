"""Read-only audit of named open real/synthetic runs. Never runs recognition.

The synthetic archive stays archive-only and is not imported into a database.
Historical cache attempts, stage requests and frozen local replays are distinct.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/知联调试'
ASSETS=OUT/'assets/work_audit'
SYN=ROOT/'outputs/20261101_复杂PDF国标流程'
FULL=ROOT/'outputs/20261028_新知识路线对照/knowledge'
SIX=ROOT/'outputs/20261029_知联v3选页实测/run'
DENIED={'c325fc949c6ed19e380321693b8f2d7f109948a893fbfef6f3f7ae869cb227d4',
        'b10a9b843c1c02975009a7c48404fc60b073bc4985cb359896d9f3933c8ba9c7'}
REAL='a6b13df9f177cd53c2cd7b3f587b9c27832c1da6b38ca8879b273476c7a860a7'
READS={}


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    p=Path(p).resolve();low=p.as_posix().lower()
    assert p.is_relative_to(ROOT) and '/.private/evaluation/' not in low
    assert not re.search(r'/(?:r0[123]|sealed_holdout)/',low)
    READS[p.relative_to(ROOT).as_posix()]=sha(p)
    return json.loads(p.read_text(encoding='utf-8-sig'))


def clean(x):
    if isinstance(x,dict):return {k:clean(v) for k,v in x.items() if k.lower() not in {
        'api_key','api_key_file','api_key_env','authorization','password','secret','access_token'}}
    if isinstance(x,list):return [clean(v) for v in x]
    return x


def dump(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(clean(x),ensure_ascii=False,separators=(',',':'))
    assert not any(h in text for h in DENIED)
    p.write_text(text,encoding='utf-8')


def js(p,assignment,x):
    dump(p.with_suffix('.json'),x)
    p.write_text(assignment+json.dumps(clean(x),ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')+';',encoding='utf-8')


def context(inp):
    try:return json.loads(inp.get('prompt','').split('\n输入：',1)[1])
    except (ValueError,IndexError):return {}


def walk(x):
    if isinstance(x,dict):
        yield x
        for value in x.values():yield from walk(value)
    elif isinstance(x,list):
        for value in x:yield from walk(value)


def candidate_counts(answer):
    counts=Counter()
    for obj in walk(answer):
        for key in ['entities','assignments','facts','relations','verdicts','objects','unresolved']:
            if isinstance(obj.get(key),list):counts[key]+=len(obj[key])
    return dict(counts)


def prepare_calls(key,doc,cache,inputs,receipts):
    """Use receipts for run membership; cache-only legacy attempts stay labelled."""
    units={u['id']:u for u in doc.get('units',[])}
    refs={r.get('cache_key'):r for r in receipts}
    order={r.get('cache_key'):i+1 for i,r in enumerate(receipts)}
    keys=list(refs) if refs else list(cache)
    rows=[]
    for ck in keys:
        saved=cache.get(ck,{})
        inp=inputs.get(ck)
        ctx=context(inp or {})
        spans=[o for o in walk(ctx) if o.get('id') in units and isinstance(o.get('text'),str)]
        for span in spans:
            assert span['text']==units[span['id']]['text'],'Wrong document/source binding'
        receipt=saved.get('receipt') or refs.get(ck,{})
        stage=saved.get('stage',refs.get(ck,{}).get('stage','unknown'))
        raw=saved.get('answer',saved.get('invalid_answer',{}))
        subjects=ctx.get('subjects') or ([ctx['subject']] if ctx.get('subject') else [])
        if not subjects and isinstance(raw,dict):
            subjects=[{'id':o['entity_id']} for o in raw.get('objects',[]) if isinstance(o,dict) and o.get('entity_id')]
        subject_ids=list(dict.fromkeys(s.get('id',s.get('entity_id')) for s in subjects if s.get('id',s.get('entity_id'))))
        details={'input':inp,'cache':saved,'receipt_in_run':refs.get(ck),
            'context':ctx if inp else None,
            'evidence_note':'input is the saved full system/prompt; cache answer is parsed/validated output, not provider HTTP body. Missing input remains missing.'}
        callpath=ASSETS/key/'calls'/(ck+'.js')
        callpath.parent.mkdir(parents=True,exist_ok=True)
        detail_id=key+':'+ck
        js(callpath,'window.WORK_AUDIT_CALLS['+json.dumps(detail_id)+']=',details)
        attempts=saved.get('attempts',[])
        rows.append({'key':ck,'detail_id':detail_id,'ordinal':order.get(ck),'ordering':'receipt sequence' if refs else 'cache-key order; not request chronology',
            'stage':stage,'status':saved.get('status','not_saved'),'input_saved':bool(inp),'raw_http_saved':False,
            'attempts':len(attempts),'attempt_states':dict(Counter(a.get('status','unknown') for a in attempts)),
            'usage':receipt.get('usage'), 'finish_reason':receipt.get('finish_reason'),
            'requested_max_output_tokens':receipt.get('requested_max_output_tokens'),
            'cache_hit':bool(refs.get(ck,{}).get('cache_hit')),
            'prompt_chars':len(inp.get('system',''))+len(inp.get('prompt','')) if inp else None,
            'registry_entries':len(ctx.get('registry',[])) if inp else None,
            'registry_chars':len(json.dumps(ctx.get('registry',[]),ensure_ascii=False,separators=(',',':'))) if inp else None,
            'span_ids':[s['id'] for s in spans], 'source_chars':sum(len(s['text']) for s in spans) if inp else None,
            'subject_ids':subject_ids, 'output_counts':candidate_counts(raw),
            'detail_url':callpath.relative_to(OUT).as_posix(),'raw_url':callpath.with_suffix('.json').relative_to(OUT).as_posix()})
    return sorted(rows,key=lambda r:(r['ordinal'] is None,r['ordinal'] or 0,r['key']))


def dataset(key,label,kind,run,doc,cache,inputs,receipts,source,extra=None):
    assert doc['source_sha256'] not in DENIED and sha(source)==doc['source_sha256']
    ASSETS.joinpath(key).mkdir(parents=True,exist_ok=True)
    # The audit copy never becomes an ingestion source or a production run.
    copied=ASSETS/key/'source.pdf'
    if copied.exists():assert sha(copied)==doc['source_sha256'],'Preserve an existing different source copy'
    else:shutil.copy2(source,copied)
    calls=prepare_calls(key,doc,cache,inputs,receipts)
    owners={e['id']:e for e in doc.get('entities',[])}
    units={u['id']:u for u in doc.get('units',[])}
    repeated=Counter(s for c in calls for s in c['span_ids'])
    repeated_chars=sum(len(units[s]['text'])*n for s,n in repeated.items())
    unique_chars=sum(len(units[s]['text']) for s in repeated)
    texts=Counter(re.sub(r'\s+','',u['text']) for u in units.values())
    signatures=Counter((f.get('entity_id'),f.get('field'),f.get('value'),f.get('unit'),f.get('scope'),f.get('role'),f.get('condition'),f.get('time')) for f in doc.get('facts',[]))
    metrics={'source_pages':doc.get('source_page_count'), 'processed_pages':len(doc.get('pages',[])),
        'units':len(units),'source_chars':sum(len(u['text']) for u in units.values()),'chunks':len(doc.get('chunks',[])),
        'chunk_spans':[{'id':c['id'],'pages':c.get('pages'), 'units':len(c['unit_ids']),
            'characters':sum(len(units[s]['text']) for s in c['unit_ids'])} for c in doc.get('chunks',[])],
        'entities':len(owners),'entity_kinds':dict(Counter(e['kind'] for e in owners.values())),
        'facts':len(doc.get('facts',[])), 'relations':len(doc.get('relations',[])),
        'fact_owner_kinds':dict(Counter(owners.get(f.get('entity_id'),{}).get('kind','unknown') for f in doc.get('facts',[]))),
        'fact_states':dict(Counter(f.get('status','unknown') for f in doc.get('facts',[]))),
        'quality_issues':dict(Counter(q for f in doc.get('facts',[]) for q in f.get('quality_issues',[]))),
        'literal_duplicate_unit_occurrences':sum(n-1 for n in texts.values()),
        'exact_fact_signature_extra_records':sum(n-1 for n in signatures.values()),
        'saved_call_records':len(calls),'saved_inputs':sum(c['input_saved'] for c in calls),
        'stages':dict(Counter(c['stage'] for c in calls)),
        'repeated_input_source_occurrences':sum(repeated.values()),'unique_input_source_units':len(repeated),
        'all_input_source_chars':repeated_chars,'unique_input_source_chars':unique_chars,
        'repeated_input_source_chars':repeated_chars-unique_chars,
        'source_repetition_factor':round(repeated_chars/unique_chars,3) if unique_chars else None,
        'prompt_chars':sum(c['prompt_chars'] or 0 for c in calls),
        'registry_chars':sum(c['registry_chars'] or 0 for c in calls),
        'total_tokens_in_saved_receipts':sum((c['usage'] or {}).get('total_tokens',0) for c in calls),
        'current_replay_calls':run.get('summary',{}).get('calls'),
        'warning':'Repeated text and concepts are diagnostic counts, not automatically useless calls. No counterfactual correctness/savings evaluation.'}
    pages=[]
    import fitz
    with fitz.open(source) as pdf:
        numbers=[p['page'] for p in doc.get('pages',[])]
        for number in numbers:
            target=ASSETS/'pages'/doc['source_sha256']/(str(number)+'.png')
            if not target.is_file():
                target.parent.mkdir(parents=True,exist_ok=True)
                p=pdf[number-1];scale=1400/max(p.rect.width,p.rect.height)
                p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).save(target)
            pages.append({'page':number,'url':target.relative_to(OUT).as_posix()})
    payload={'key':key,'label':label,'kind':kind,'run_metadata':{k:v for k,v in run.items() if k not in ['documents','receipts']},
        'document':doc,'calls':calls,'metrics':metrics,'source_pdf':copied.relative_to(OUT).as_posix(),
        'page_images':pages,'extra':extra or {}}
    js(ASSETS/key/'data.js',f'window.WORK_AUDIT_DATASETS[{json.dumps(key)}]=',payload)
    return {'key':key,'label':label,'kind':kind,'data_url':(ASSETS/key/'data.js').relative_to(OUT).as_posix(),
        'json_url':(ASSETS/key/'data.json').relative_to(OUT).as_posix(),'metrics':metrics}


def folder_cache(p):return {f.stem:read(f) for f in sorted((p/'cache').glob('*.json'))}
def folder_inputs(p):return {f.stem:read(f) for f in sorted((p/'inputs').glob('*.json'))}


def build():
    OUT.mkdir(exist_ok=True);ASSETS.mkdir(parents=True,exist_ok=True)
    allsets=[]
    full=read(FULL/'run.json');assert len(full['documents'])==1 and full['documents'][0]['source_sha256']==REAL
    audit=read(FULL.parent/'API调用与用量审计.json')
    allsets.append(dataset('g209-full','G209 127页全文 · 旧路线开发累计','real_whole_document',full,full['documents'][0],
        folder_cache(FULL),folder_inputs(FULL),full.get('receipts',[]),Path(full['documents'][0]['path']),
        {'historical_cost':audit['routes']['knowledge'],'historical_stages':{k:v for k,v in audit['stages'].items() if k.startswith('knowledge')},
         'cost_note':'658 is historical multi-run development attempts, not one frozen production run. This final snapshot is a local replay with 0 calls and 0 receipts. Legacy saved cache records are incomplete relative to all historical receipts.'}))
    print('Built full 127-page snapshot',flush=True)
    six=read(SIX/'run.json');assert six['documents'][0]['source_sha256']==REAL
    allsets.append(dataset('g209-six','G209 知联v3 · 6/127页真实实测','real_selected_pages',six,six['documents'][0],
        folder_cache(SIX),folder_inputs(SIX),six['receipts'],Path(six['documents'][0]['path']),
        {'execution':read(SIX.parent/'execution.json'),'scope_note':'Only six pages; do not divide 658 by 9 to claim savings.'}))
    corpus=read(SYN/'资料目录.json');assert corpus['synthetic'] and corpus['development_only']
    archive=read(SYN/'归档隔离标识.json');assert archive['archive_only'] and not archive['automatic_ingestion_allowed']
    syn=read(SYN/'隔离归档_识别全量审计.json')['recognition_run']
    downstream=read(SYN/'隔离归档_after_release_字段与评估.json')
    with zipfile.ZipFile(SYN/'隔离归档_模型请求与原回复.zip') as z:
        READS[str((SYN/'隔离归档_模型请求与原回复.zip').relative_to(ROOT))]=sha(SYN/'隔离归档_模型请求与原回复.zip')
        caches={Path(n).stem:json.loads(z.read(n)) for n in z.namelist() if re.fullmatch(r'cache/[a-f0-9]{64}\.json',n)}
        inputs={Path(n).stem:json.loads(z.read(n)) for n in z.namelist() if re.fullmatch(r'inputs/[a-f0-9]{64}\.json',n)}
    # Sequential document processing in this frozen run: one discover starts
    # each document. Validate source text, names and synth source SHA below.
    groups=[]
    for receipt in syn['receipts']:
        if receipt['stage']=='knowledge_discover':groups.append([])
        assert groups
        groups[-1].append(receipt)
    assert len(groups)==len(syn['documents'])==len(corpus['documents'])==8
    for desc,doc,receipts in zip(corpus['documents'],syn['documents'],groups):
        assert Path(doc['path']).name==Path(desc['path']).name
        for receipt in receipts:
            ctx=context(inputs[receipt['cache_key']])
            if receipt['stage']=='knowledge_synthesize':assert ctx['source_sha256']==doc['source_sha256']
        cases=set(desc['cases']);mapping=[m for m in downstream['mapping'] if m['case'] in cases]
        extra={'synthetic':True,'archive_only':True,'automatic_ingestion_allowed':False,
            'evaluation_cases':desc['cases'],'downstream_mapping':mapping,
            'downstream_results':[r for r in downstream['results'] if r.get('case') in cases],
            'request_document_binding':'Frozen receipt sequence split at discover; document order, every source text and synth SHA checked.',
            'stage_note':'Risk bridge after_* stages reuse one frozen prediction. They are not repeated model runs.'}
        allsets.append(dataset(desc['id'],desc['id']+' '+desc['name']+' · '+str(desc['pages'])+'页虚构隔离',
            'synthetic_open_archive',syn,doc,caches,inputs,receipts,SYN/desc['path'],extra))
        print('Built '+desc['id'],flush=True)
    syn_receipts=syn['receipts']
    summary={'historical_attempts_127':audit['routes']['knowledge'],
        'full_stages':{k:v for k,v in audit['stages'].items() if k.startswith('knowledge')},
        'synthetic':{'documents':8,'pages':sum(d['source_page_count'] for d in syn['documents']),
            'calls':len(syn_receipts),'tokens':sum(r['usage']['total_tokens'] for r in syn_receipts),
            'entities':sum(len(d['entities']) for d in syn['documents']),
            'entity_kinds':dict(Counter(e['kind'] for d in syn['documents'] for e in d['entities'])),
            'facts':sum(len(d['facts']) for d in syn['documents']),
            'fact_owner_kinds':dict(sum((Counter(s['metrics']['fact_owner_kinds']) for s in allsets if s['kind']=='synthetic_open_archive'),Counter())),
            'mapping_model_rows':sum(m['source_origin']=='knowledge_model_fact' for m in downstream['mapping']),
            'mapping_local_retrieval_rows':sum(m['source_origin']=='explicit_source_statement_retrieval' for m in downstream['mapping']),
            'mapping_model_unique_facts':len({m['fact_id'] for m in downstream['mapping'] if m['source_origin']=='knowledge_model_fact'}),
            'local_retrieval':[{'case':m['case'],'field':m['field'],'raw_value':m.get('raw_value'),'fact_id':m['fact_id']} for m in downstream['mapping'] if m['source_origin']=='explicit_source_statement_retrieval']},
        'new_model_requests':0,'hidden_cases_included':0,'production_ingestion':False}
    manifest={'generated_at':datetime.now().astimezone().isoformat(),'datasets':allsets,'summary':summary,
        'reading_note':'All request/answer details are preserved artifacts. Missing transport responses are never relabelled raw HTTP. Counts are not independent accuracy or invoiced cost.'}
    js(ASSETS/'manifest.js','window.WORK_AUDIT_MANIFEST=',manifest)
    dump(OUT/'work_audit_receipt.json',{'generated_at':manifest['generated_at'],'summary':summary,
        'datasets':[{'key':s['key'],'metrics':s['metrics']} for s in allsets], 'read_files':READS,
        'html':'多页与大文档调用审计.html','source_files_unchanged':True})
    from knowledge_work_audit_layout import write_html
    write_html(OUT,manifest)
    # The main view follows one piece of information through actual stages.
    # Aggregate costs and the general record browser remain a secondary appendix.
    from knowledge_process_stories import build_stories
    from knowledge_process_story_layout import write_html as write_stories
    write_stories(OUT,build_stories())
    print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    # Even an accidental imported helper cannot connect to a model or network.
    from unittest.mock import patch
    with patch('socket.socket.connect',side_effect=AssertionError('Network disabled for saved-artifact audit')):
        build()
