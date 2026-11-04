"""Bind immutable recognition history to one auditable assessment workflow.

The default action only normalizes evidence and executes deterministic tables.
No OCR, recognition inference, model request, production graph update or
professional acceptance is performed here.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlencode
import zipfile

from ..debug_history import artifact_paths, get_record, save_snapshot
from ..recognition_debug import _document, _records, _clean, _safe_location, DENIED_HASHES, debug_source
from .contract import CONTRACT_VERSION, applicability, encoded, fact_record, identity
from .knowledge_bridge import candidate, compact, explicit_judgment_candidates, source_dates
from .service import StandardAssessmentService
from ..provenance import source_revision

STAGES=[('source','PDF与识别快照'),('raw','识别原始候选'),('mapping','字段转换与未采用项'),
        ('scope','对象、时期与工况校核'),('inputs','有效输入与来源选择'),('judgment','专业判断与模型输入输出'),
        ('table15','表15：危害程度'),('table17','表17：危险性'),('gaps','缺口、冲突与不确定结果'),
        ('versions','评估版本与人工复核')]
BRIDGE_REVISION=source_revision(Path(__file__).parent)


def service(paths,workspace='debug'):
    if workspace not in {'debug','main'}:raise ValueError('无效评估工作区')
    return StandardAssessmentService(paths,database=paths.output_dir/'assessment-debug/assessment.sqlite' if workspace=='debug' else None)


def source_data(paths,request):
    route=request.get('route','knowledge');run_id=request.get('run_id','live');document=request.get('document','')
    run,folder,doc=_document(paths,route,run_id,document)
    source=debug_source(paths,route=route,run_id=run_id,document=document)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if digest!=doc.get('source_sha256'):raise ValueError('PDF与识别快照哈希不一致，请重新识别或选择保存的历史记录')
    local=artifact_paths(paths,run,folder)
    raw=_records(local,run,folder,doc,route,'facts')
    units=copy.deepcopy(doc.get('units',[]))
    if not units:
        source_rows=_records(local,run,folder,doc,route,'units') if route!='visual' else _records(local,run,folder,doc,route,'ocr')
        for i,row in enumerate(source_rows):
            if row.get('text') and row.get('page'):
                units.append({'id':row.get('id',f'source_{i}'),'text':row['text'],'page':row['page'],'source':'saved_source_text'})
    entities=doc.get('entities',[])
    subjects=[{'id':str(e['id']),'label':e.get('name',e['id']),'aliases':list(dict.fromkeys([e.get('name',e['id']),*e.get('aliases',[])])),
               'supported':'滑坡' in e.get('name',''),'kind':e.get('kind','entity')}
              for e in entities if e.get('kind','entity')!='concept']
    if not subjects:
        labels=list(dict.fromkeys(r.get('object') or r.get('subject_name') for r in raw if isinstance(r.get('object') or r.get('subject_name'),str)
                                   and (r.get('object') or r.get('subject_name')) not in {'unknown','未明确',''}))
        subjects=[{'id':identity('source_subject_',label),'label':label,'aliases':[label],
                   'supported':'滑坡' in label,'kind':'source_fact_object_label'} for label in labels]
    # Basic records do not always contain an object directory. Preserve an
    # explicitly unbound document entry instead of inventing a slope identity.
    if not subjects:subjects=[{'id':'document_scope','label':'文档范围（尚无可绑定滑坡对象）','aliases':[], 'supported':False,'kind':'document'}]
    page_texts={p:'\n'.join(u['text'] for u in units if u.get('page')==p) for p in sorted({u['page'] for u in units if u.get('page')})}
    for p in doc.get('pages',[]) if isinstance(doc.get('pages'),list) else []:
        if isinstance(p,dict) and p.get('page'):page_texts.setdefault(p['page'],'')
    dates=list(dict.fromkeys(d for text in [*page_texts.values(),*(str(f.get('time','')) for f in raw)] for d in source_dates(text)))
    return {'run':run,'folder':folder,'doc':doc,'source':source,'sha256':digest,'raw':raw,'units':units,
            'subjects':subjects,'page_texts':page_texts,'dates':dates,'route':route,'run_id':run_id,
            'document':document,'run_hash':hashlib.sha256(encoded(run).encode()).hexdigest()}


def source_context(data):
    return {'route':data['route'],'run_id':data['run_id'],'document':data['document']}


def catalog(paths,request):
    data=source_data(paths,request)
    rows=[];db=paths.output_dir/'assessment-debug/assessment.sqlite'
    if db.is_file():
        for case in service(paths).store.rows('assessment_case'):
            lineage=case.get('recognition_lineage',{})
            if lineage.get('source_sha256')==data['sha256']:
                rows.append({'case_id':case['case_id'],'subject':case['target_ref'],'scope':case['scope'],
                    'subject_label':lineage.get('subject_label',case['target_ref']),
                    'resolution_version':case['resolution_version'],'source_context':lineage.get('source_context'),
                    'same_recognition_snapshot':lineage.get('recognition_run_hash')==data['run_hash'] or
                        lineage.get('original_context')==source_context(data) and lineage.get('original_recognition_run_hash')==data['run_hash']})
    rows.sort(key=lambda r:(not r['same_recognition_snapshot'],r['subject_label'],r['case_id']))
    return {'source_context':source_context(data),'source_name':Path(data['doc'].get('original_source_path',data['source'])).name,
            'source_sha256':data['sha256'],'recognition_run_hash':data['run_hash'],'subjects':data['subjects'],
            'dates':data['dates'],'scenarios':[{'id':'natural','label':'天然工况'},{'id':'rainstorm','label':'暴雨工况'},
                                           {'id':'unspecified','label':'工况尚未明确'}],
            'linked_cases':rows,'new_model_requests':0,'automatic_assessment':'仅确定性预评估；对象范围未确定或关键依据不足时停止并展示原因'}


def prepare(paths,request):
    data=source_data(paths,request)
    requested_hash=request.get('expected_recognition_hash')
    if requested_hash and requested_hash!=data['run_hash']:raise ValueError('识别结果已变化，请刷新后衔接，不能混合两次结果')
    subject=next((s for s in data['subjects'] if s['id']==request.get('subject_id')),None)
    if subject is None:raise ValueError('请选择该识别记录中实际存在的对象')
    date=request.get('assessment_date','');scenario=request.get('scenario','unspecified')
    if date and date not in data['dates']:raise ValueError('请选择来源记录中已有的日期；缺少时期时保留未知，不补造日期')
    if scenario not in {'natural','rainstorm','unspecified'}:raise ValueError('无效工况')
    original_context=source_context(data)
    original_run_hash=data['run_hash']
    # All downstream records bind to a frozen recognition history, never live
    # mutable output files. Existing histories are reused without rewriting.
    if not data['run_id'].startswith('history-'):
        meta=save_snapshot(paths,route=data['route'],run_id=data['run_id'],document=data['document'],
                           label='国标评估来源 · '+Path(data['doc']['path']).name)
        data=source_data(paths,{'route':data['route'],'run_id':'history-'+meta['id'],'document':meta['document_id']})
    frozen_context=source_context(data)
    meta=get_record(paths,data['run_id'].removeprefix('history-'))
    target_ref='source-object:'+data['doc']['id']+':'+subject['id']
    scope={'subject':{'source_object_ref':target_ref,'object_kind':'landslide_body' if subject['supported'] else 'unconfirmed_object'},
           'time':{'kind':'instant' if date else 'unknown','raw':date or '来源时期尚未明确','start':date or None,'end':date or None},
           'scenario':{'code':scenario,'raw':{'natural':'天然工况','rainstorm':'暴雨工况','unspecified':'未明确'}[scenario],'basis_refs':[]}}
    api=service(paths)
    bridge_revision=BRIDGE_REVISION
    case_id=identity('debugcase_',{'source':frozen_context,'manifest':meta['manifest_sha256'],'scope':scope,'subject':subject['id'],'bridge_revision':bridge_revision})
    try:
        existing=api.get_case(case_id)
        return {'case':existing['case'],'reused':True,'new_model_requests':0}
    except KeyError:pass
    raw_rows=[];mapping=[];facts=[];contexts=[];sources=[];bindings=[]
    names={e['id']:e.get('name',e['id']) for e in data['doc'].get('entities',[])}
    page_targets={p:{s['id'] for s in data['subjects'] if any(compact(a) in compact(text) for a in s['aliases'])}
                  for p,text in data['page_texts'].items()}
    source_path=str(data['source'].relative_to(paths.root))
    input_rows=[(r,'recognition') for r in data['raw']]+[(r,'explicit_source_statement_retrieval') for r in explicit_judgment_candidates(data['units'])]
    for index,(original,origin) in enumerate(input_rows):
        raw=copy.deepcopy(original)
        raw.setdefault('id','saved_'+identity('',{'index':index,'row':raw}))
        raw.setdefault('object',names.get(raw.get('entity_id'),'') or raw.get('subject_name',''))
        raw.setdefault('field',raw.get('property_name') or raw.get('property') or '')
        if not isinstance(raw.get('value'),str) and raw.get('value') is not None:raw['value']=str(raw['value'])
        evidence=raw.get('evidence',[])
        if isinstance(evidence,dict):evidence=[evidence]
        raw['evidence']=[{'quote':e.get('quote',e.get('text','')),'page':e.get('page',raw.get('page')),
                         **{k:v for k,v in e.items() if k not in {'quote','page'}}} for e in evidence if isinstance(e,dict)]
        raw_rows.append({'id':raw['id'],'record_type':origin,'original_record':original})
        mapped=candidate(raw,case_scope=scope,target_key=subject['id'],aliases=subject['aliases'],
                         page_texts=data['page_texts'],page_targets=page_targets)
        if not mapped['accepted'] and mapped.get('reason')=='not_an_assessment_input' and re.search(r'裂缝|位移|变形|前缘|后缘|滑体.*坡度',raw.get('field','')):
            observation={**raw,'field':'地质环境'}
            mapped=candidate(observation,case_scope=scope,target_key=subject['id'],aliases=subject['aliases'],
                             page_texts=data['page_texts'],page_targets=page_targets)
            if mapped['accepted']:mapped['variable']='observations'
        record={'id':raw['id'],'source_origin':origin,'field':raw.get('field'),'raw_value':raw.get('value'),
                'source_role':raw.get('role'),'source_object':raw.get('object'),'source_scope':raw.get('scope'),
                'evidence':raw['evidence'],'accepted':mapped['accepted'],'reason':mapped.get('reason')}
        if not mapped['accepted']:mapping.append(record);continue
        src={'record_ref_id':identity('debugsource_',{'lineage':frozen_context,'index':index,'row':original}),
             'source_path':source_path,'snapshot_sha256':data['sha256'],'collection':'recognition_history.'+origin,
             'record_id':raw['id'],'original_record':original,'recognition_context':frozen_context}
        ctx={'context_id':identity('debugctx_',{'record':src['record_ref_id'],'quotes':raw['evidence']}),
             'source_path':source_path,'source_sha256':data['sha256'],
             'spans':[{'quote':e['quote'],'file_page_1_based':e.get('page'),'source_span_id':e.get('span_id')} for e in raw['evidence']],
             'table_context':{},'recognition_context':frozen_context}
        binding={'binding_id':identity('debugbinding_',{'source':src['record_ref_id'],'scope':scope}),
                 'source_object_ref':raw.get('object') or raw.get('scope'),'candidate_target_refs':[target_ref],
                 'selected_target_ref':target_ref,'mapping_status':'source_evidence_candidate','relation':'assertion_about',
                 'basis_refs':[ctx['context_id']],'scope_limit':'仅此PDF识别快照内的候选归属；未确认跨资料物理体身份',
                 'binding_basis':mapped['binding_basis'],'version':'recognition-assessment-debug-1'}
        fact=fact_record(mapped['variable'],mapped['value'],mapped['scope'],source_refs=[src['record_ref_id']],
            evidence_refs=[ctx['context_id']],origin=data['route'] if origin=='recognition' else 'source_retrieval',
            semantic='reported_judgment' if mapped['variable']=='development' else 'observation',role=mapped['role'],
            support='literal_matched',source_standard=mapped['source_standard'],
            feature='recognized_context' if mapped['variable']=='geological_context' else 'recognized_observation' if mapped['variable']=='observations' else None)
        fact['basis_status']=mapped['basis_status']
        if data['route']=='visual' and not any(data['page_texts'].get(p) for p in mapped['source_pages']):
            fact['source_check']['content']='model_supported'
            fact['basis_status']='conditional'
            mapped['basis_status']='conditional'
            record['source_support_note']='视读模型的原页转录候选，未当成独立原文文字对齐；需查看原页复核。'
        fact['recognition_ref']={'context':frozen_context,'record_id':raw['id'],'source_origin':origin}
        fact['normalization_receipt']={'transformations':mapped['transformations'],'source_role':mapped['source_role']}
        fact['fact_id']=identity('debugfact_',fact)
        record.update(typed_fact_id=fact['fact_id'],typed_variable=mapped['variable'],typed_value=mapped['value'],
                      scope=mapped['scope'],typed_role=mapped['role'],transformations=mapped['transformations'],
                      binding_basis=mapped['binding_basis'],basis_status=mapped['basis_status'])
        mapping.append(record);facts.append(fact);sources.append(src);contexts.append(ctx);bindings.append(binding)
    lineage={'source_context':frozen_context,'original_context':original_context,'source_sha256':data['sha256'],
             'recognition_run_hash':data['run_hash'],'original_recognition_run_hash':original_run_hash,'history_id':meta['id'],'history_manifest_sha256':meta['manifest_sha256'],
             'source_name':Path(data['doc'].get('original_source_path',data['source'])).name,
             'subject_id':subject['id'],'subject_label':subject['label'],'binding_status':'source_local_candidate',
             'bridge_revision':bridge_revision,
             'created_at':datetime.now(timezone.utc).isoformat(),'new_model_requests':0}
    case={'case_id':case_id,'contract_version':CONTRACT_VERSION,'target_ref':target_ref,'scope':scope,
          'resolution_version':1,'fact_ids':[f['fact_id'] for f in facts],'context_ids':[c['context_id'] for c in contexts],
          'source_hashes':{source_path:data['sha256']},'selections':{},'hazard_type_supported':subject['supported'],
          'source_coverage':'frozen_recognition_history_candidate_mapping_not_full_source_validation',
          'recognition_lineage':lineage,'debug_raw_records':raw_rows,'debug_mapping':mapping,
          'debug_rule_snapshot':copy.deepcopy(api.rules),'debug_workspace':True,'created_at':lineage['created_at']}
    with api.store.transaction() as db:
        for table,rows in [('source_record',sources),('evidence_context',contexts),('identity_binding',bindings),('fact_assertion',facts),('assessment_case',[case])]:
            for row in rows:api.store.put(table,row,db)
    result=api.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':case_id,'expected_resolution_version':1})
    return {'case':case,'result':result,'reused':False,'new_model_requests':0}


def flow(paths,case_id,result_id=None,workspace='debug'):
    api=service(paths,workspace);data=api.get_case(case_id);case=data['case']
    for source_path,digest in case.get('source_hashes',{}).items():
        if digest in DENIED_HASHES:raise ValueError('该案例引用非开放资料，不在本调试展示范围')
        _safe_location(source_path,paths.root)
    for ctx in data['contexts']:
        if ctx.get('source_sha256') in DENIED_HASHES:raise ValueError('该案例引用非开放资料，不在本调试展示范围')
        _safe_location(ctx['source_path'],paths.root)
    results=data['results'];result=next((r for r in results if r['result_id']==result_id),None) if result_id else next((r for r in reversed(results) if r['is_current']),None)
    if result_id and not result:raise ValueError('结果版本不属于此案例')
    lineage=case.get('recognition_lineage',{})
    # Revalidate the immutable history on every view, including its manifest.
    if lineage:
        frozen=source_data(paths,lineage['source_context'])
        if frozen['sha256']!=lineage['source_sha256'] or frozen['run_hash']!=lineage['recognition_run_hash']:raise ValueError('绑定的识别快照校验失败')
    initial_ids=set(case['fact_ids'])
    if result:
        initial_ids={f['typed_fact_id'] for f in case.get('debug_mapping',[]) if f.get('accepted')} if 'debug_mapping' in case else (set(case['fact_ids']) if result.get('is_current') else set())
        for resolution in result.get('resolved_variables',{}).values():
            initial_ids.update(resolution.get('candidate_fact_ids',[]))
            if resolution.get('selected_fact_id'):initial_ids.add(resolution['selected_fact_id'])
    judgment_id=(result or {}).get('partials',{}).get('professional_context_task',{}).get('derivation_id')
    derivations=[d for d in data['derivations'] if not result or d['derivation_id'] in result['trace_derivation_ids'] or d['derivation_id']==judgment_id]
    for d in derivations:initial_ids.update(d['input_fact_ids']);initial_ids.update(d['output_fact_ids'])
    facts=[api.store.get('fact_assertion',fid) for fid in sorted(initial_ids)]
    scope=result.get('assessment_scope',case['scope']) if result else case['scope']
    scope_rows=[{'record_type':'assessment_scope','assessment_scope':scope,'hazard_type_supported':case.get('hazard_type_supported',False),
                 'reason':'已绑定来源滑坡候选；时期、工况和逐条事实适用性仍按下列记录核查。' if case.get('hazard_type_supported') else
                          '来源对象尚未确认为本流程适用的滑坡体；本次停止套用滑坡表，不将危岩体、工程或普通边坡自动当作滑坡。'},
                *[{'fact_id':f['fact_id'],'variable':f['variable_code'],'value':f['value'],'source_role':f['role'],
                 'source_scope':f['scope'],'assessment_scope':scope,**applicability(f,{'scope':scope})} for f in facts]
                ]
    r=result or {};partials=r.get('partials',{})
    rule_snapshot=case.get('debug_rule_snapshots',{}).get(r.get('rule_snapshot_hash'),case.get('debug_rule_snapshot') or (api.rules if r.get('rule_snapshot_hash')==api.rules_hash else {'status':'该历史规则快照未保存，不用当前规则冒充旧版'}))
    old_sources=[]
    if not case.get('debug_raw_records'):
        for sid in sorted({sid for f in facts for sid in f['source_record_refs']}):
            try:old_sources.append(api.store.get('source_record',sid))
            except KeyError:old_sources.append({'record_ref_id':sid,'status':'not_saved','reason':'该历史来源实体未保存，不按引用ID补造原文。'})
    records={'source':[{'lineage':lineage,'case_id':case_id,'scope':case['scope'],'rules_version':r.get('rule_version'),
                        'rule_snapshot_hash':r.get('rule_snapshot_hash'),'note':'仅本地确定性预评估；模型辅助与专业确认分别执行'}],
             'raw':case.get('debug_raw_records') or old_sources,
             'mapping':case.get('debug_mapping') or [{'fact_id':f['fact_id'],'variable':f['variable_code'],'origin_kind':f['origin_kind'],'value':f['value'],'source_record_refs':f['source_record_refs'],'note':'既有评估输入；未保存新版识别字段转换日志，不补造日志。'} for f in facts],
             'scope':scope_rows,
             'inputs':[{'variable':k,**v} for k,v in r.get('resolved_variables',{}).items()],
             'judgment':[d for d in derivations if d.get('method_kind')=='llm'] or [{'execution_status':'not_run','reason':'尚未发送专业判断模型请求；已有结论可直接查表，缺依据则保留缺口。'}],
             'table15':[{'rule':rule_snapshot.get('table15'),'calculation':partials.get('table15'),
                         'derivations':[d for d in derivations if d.get('method_id')=='M-T15']}],
             'table17':[{'result':r.get('table17_result'),'branches':r.get('table17_branches'),
                         'rule':rule_snapshot.get('table17'),'basis_status':r.get('basis_status'),
                         'derivations':[d for d in derivations if d.get('method_id')=='M-T17'],
                         'workflow_status':r.get('workflow_status'),'formal_assessment_enabled':False}],
             'gaps':[*({'record_type':'missing',**m} for m in r.get('minimal_missing',[])),
                     *({'record_type':'conflict',**c} for c in r.get('conflicts',[]))],
             'versions':[{'record_type':'result',**v} for v in results]+[{'record_type':'review',**v} for v in data['reviews']]}
    if r.get('table17_result',{}).get('grade') is None and len(r.get('table17_result',{}).get('possible_grades',[]))>1:
        records['gaps'].append({'record_type':'multiple_outcomes','possible_grades':r['table17_result']['possible_grades'],'reason':'输入范围已给，保留可能结果，不取均值或补造单值。'})
    if case['scope']['time']['kind']=='unknown' or case['scope']['scenario']['code']=='unspecified':
        records['gaps'].append({'record_type':'scope_unresolved','scope':case['scope'],
            'reason':'本次对象时期或工况仍未明确；即使存在查表格值，也仅可作带此待核前提的候选，不视为已具备可靠适用输入。'})
    link='./recognition-debug.html?'+urlencode({**lineage.get('source_context',{}),'run':lineage.get('source_context',{}).get('run_id',''),'stage':'facts','assessment_case':case_id}) if lineage else None
    if not lineage:records['source'].append({'record_type':'recognition_history_unbound','reason':'此既有案例尚未关联识别历史；现有评估输入、规则及复核可读，上游识别过程不补造。'})
    return _clean({'case':case,'workspace':workspace,'selected_result':result,'results':results,'facts':facts,'contexts':data['contexts'],
                   'stages':[{'id':key,'label':label,'count':len(records[key])} for key,label in STAGES],
                   'records':records,'recognition_link':link,'new_model_requests':0})


def export(paths,case_id,workspace='debug'):
    from ..debug_history import export_record
    data=flow(paths,case_id,workspace=workspace);buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('assessment-flow.json',json.dumps(data,ensure_ascii=False,indent=2))
        history=data['case'].get('recognition_lineage',{}).get('history_id')
        if history:z.writestr('recognition-history.zip',export_record(paths,history))
    return buffer.getvalue()
