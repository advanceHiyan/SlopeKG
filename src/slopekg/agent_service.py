"""Fifth-route integration and allowlisted, read-only run evidence."""
from pathlib import Path
import json
import re
from .agent_gateway import dump, utc_now
from .agent_trace import trace_snapshot
from .agent_pipeline import AgentPipeline, sha
from .agent_settings import AgentSettings
from .pipeline_lock import pipeline_run_guard
from .recognition import visual_job_options
from .storage import read_json


def run_agent_recognition(paths, *, input_pdfs, model_profile, agent_settings=None,
                          prepare_only=False, progress_callback=None, resume_from=None, page_selections=None):
    if not input_pdfs or model_profile is None:
        raise ValueError('自主识别需要明确PDF和项目模型配置')
    with pipeline_run_guard(paths.output_dir/'.pipeline.lock'):
        return AgentPipeline(paths, model_profile, settings=AgentSettings(**(agent_settings or {})),
            progress_callback=progress_callback).run(input_pdfs, prepare_only=prepare_only,
                resume_from=resume_from, page_selections=page_selections)


def can_resume(run):
    status = run.get('processing_status')
    assessment = run.get('assessment') or {}
    return status in {'failed', 'partial', 'cancelled'} or (
        status == 'completed' and assessment.get('status') == 'failed'
        and assessment.get('retry_mode') != 'intake' and not run.get('assessment_preview'))


def resume_inputs(paths, run):
    if not can_resume(run) or not run.get('documents'):
        raise ValueError('这个任务没有可以接续的选页草稿')
    root = run_root(paths, run)
    manifest = read_json(root/'workspace/manifest.json', {})
    saved = {d['document_id']: d for d in manifest.get('documents', [])}
    raw = paths.raw_dir.resolve()
    files, pages = [], {}
    for doc in run['documents']:
        source = Path(doc.get('path') or '').resolve()
        entry = saved.get(doc['id'], {})
        selected = doc.get('selection', {}).get('selected_pages')
        if (not source.is_relative_to(raw) or source.suffix.lower() != '.pdf' or not source.is_file()
                or not selected or entry.get('selected_physical_pages') != selected
                or entry.get('original_source_sha256') != doc.get('source_sha256')
                or sha(source) != doc.get('source_sha256')):
            raise ValueError('原 PDF 或所选页与旧任务不一致：'+doc['name']+'。请先恢复原资料，再接续。')
        files.append(source.relative_to(raw).as_posix())
        pages[str(source)] = selected
    return files, pages


def agent_job_options(paths, body):
    if any(k in body for k in ('visual_settings','knowledge_settings','document_settings','review_profile')):
        raise ValueError('自主 Agent 使用独立设置，不接受其他路线或复核模型设置')
    resume = body.get('resume_run')
    if 'resume_run' in body:
        if not isinstance(resume, str) or not resume or {'input_pdfs', 'agent_settings'} & body.keys():
            raise ValueError('接续任务必须使用已保存的原资料、选页和识别设置')
        previous = select_run(paths, resume)
        files, pages = resume_inputs(paths, previous)
        body = {**body, 'input_pdfs': files}
    options = visual_job_options(paths, {**body,'model_profile':body.get('model_profile','deepseek-knowledge-max'),'visual_settings':{}})
    options.pop('visual_settings'); options.pop('review_profile')
    limits = body.get('agent_settings', {})
    if not isinstance(limits,dict) or set(limits)-{'max_calls','timeout_seconds','record_model_inputs','task_instruction'}:
        raise ValueError('无效自主识别设置')
    if resume:
        from .model_client import ModelProfile
        old = previous.get('model', {})
        current = ModelProfile.load(Path(options['model_profile'])).identity()
        if any(old.get(k) != current[k] for k in ('model', 'base_url')):
            raise ValueError('接续须使用原任务的模型及发送地址')
        limits = {key: value for key, value in previous.get('settings', {}).items()
                  if key in AgentSettings.__dataclass_fields__}
        options.update(resume_from=resume, page_selections=pages)
    AgentSettings(**limits)
    return {**options,'recognizer':'agent','agent_settings':limits}


def run_root(paths, run):
    root = Path(run.get('run_directory','')).resolve()
    standard = root.is_relative_to((paths.output_dir/'agent-recognition/runs').resolve())
    published = (root.is_relative_to((paths.root/'outputs').resolve()) and root.parent.name=='runs'
        and root.name==run.get('run_id') and root.name.startswith('agent-'))
    if not (standard or published):
        raise ValueError('Invalid Agent run directory')
    return root


def select_run(paths, run_id=''):
    latest=read_json(paths.output_dir/'agent-recognition/run.json', {})
    if not run_id or run_id==latest.get('run_id'):return latest
    if not re.fullmatch(r'agent-[A-Za-z0-9-]+',run_id):raise ValueError('无效任务编号')
    base=(paths.output_dir/'agent-recognition/runs').resolve()
    file=(base/run_id/'run.json').resolve()
    if not file.is_relative_to(base):raise ValueError('无效任务路径')
    run=read_json(file,{})
    if not run:run=read_json(paths.output_dir/'agent-recognition/history'/f'{run_id}.json',{})
    if run.get('run_id')!=run_id:raise ValueError('任务不存在')
    run_root(paths,run)
    return run


def agent_runs(paths):
    latest=select_run(paths)
    found={latest.get('run_id'):latest} if latest else {}
    files=[*(paths.output_dir/'agent-recognition/runs').glob('agent-*/run.json'),
           *(paths.output_dir/'agent-recognition/history').glob('agent-*.json')]
    for file in files:
        try:
            run=read_json(file,{});run_root(paths,run)
            if run.get('run_id') in {file.parent.name,file.stem}:found[run['run_id']]=run
        except (OSError,ValueError):continue
    rows=[]
    for run in sorted(found.values(),key=lambda r:r.get('started_at',''),reverse=True):
        rows.append({k:run.get(k) for k in ('run_id','started_at','finished_at','processing_status','summary','elapsed_seconds','resumed_from')} |
            {'title':'、'.join(d.get('name',d.get('id','')) for d in run.get('documents',[])) or '准备资料中'})
    return {'runs':rows,'latest_id':latest.get('run_id')}


def stop_agent(paths, run_id):
    run=select_run(paths,run_id)
    if not run_id or run.get('run_id')!=run_id:raise ValueError('必须指定正在执行的任务')
    if run.get('processing_status') not in {'preparing','running','stopping'}:raise ValueError('此任务已结束')
    dump(run_root(paths,run)/'control/stop.request.json',{'requested_at':utc_now(),'source':'user_stop'})
    return {'run_id':run_id,'status':'stopping','message':'正在停止，已生成文件会保留'}


def intake_assessments(paths, run_id):
    """Retryable data handoff; never restarts the SDK or changes its outputs."""
    from .standard_assessment.agent_bridge import submit
    run=select_run(paths,run_id)
    if not run_id or run.get('processing_status')!='completed':raise ValueError('请选择已完成的Agent任务')
    receipt={'run_id':run_id,'status':'imported','cases':[],'new_model_requests':0,'errors':[]}
    for doc in run.get('documents',[]):
        try:
            result=submit(paths,{'route':'agent','run_id':run_id,'document':doc['id']})
            receipt['cases'].extend(result['cases'])
        except (OSError,ValueError,KeyError,TypeError) as exc:
            receipt['errors'].append({'document_id':doc['id'],'error':str(exc)})
    if receipt['errors']:
        receipt['status']='partial' if receipt['cases'] else 'failed'
        receipt['error']='；'.join(r['error'] for r in receipt['errors'])
    dump(run_root(paths,run)/'control/assessment-receipt.json',receipt)
    return receipt


def agent_artifact(paths, name, run_id=''):
    run = select_run(paths,run_id)
    root = run_root(paths,run)
    target = (root/name).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('Run artifact does not exist')
    rel = target.relative_to(root).as_posix()
    allowed = rel in {'run.json','control/protocol.json','control/compatibility.json',
        'control/tool_calls.jsonl','control/events.jsonl','control/sdk_result.json','workspace/manifest.json',
        'workspace/assessment-contract.json','control/assessment-receipt.json','control/final-assessment-preview.json',
        'control/delivery-check.json','control/delivery-repair.json','control/sdk_repair_result.json'}
    allowed |= rel.startswith(('workspace/inputs/','workspace/outputs/','workspace/scratch/','control/exchanges/',
        'control/resume-drafts/','control/delivery-repair-drafts/','control/assessment-previews/'))
    if not allowed or target.suffix.lower() not in {'.json','.jsonl','.sse','.txt','.md','.png','.jpg','.jpeg','.webp'}:
        raise ValueError('Artifact is outside the read-only evidence catalog')
    return target


def agent_payload(paths, *, document='', stage='facts', offset=0, limit=50, run_id=''):
    if stage not in {'facts','objects','coverage','calls','tools','errors'} or offset<0 or not 1<=limit<=100:
        raise ValueError('无效自主识别筛选')
    run = select_run(paths,run_id)
    if run:
        receipt=read_json(run_root(paths,run)/'control/assessment-receipt.json',None)
        if receipt:run={**run,'assessment':receipt}
    docs = run.get('documents',[])
    if document and document not in {d['id'] for d in docs}: raise ValueError('文档不存在')
    rows = []
    if stage in {'facts','objects'}:
        rows=[{'document_id':d['id'],'record':r} for d in docs if not document or d['id']==document for r in d.get(stage,[])]
        if stage=='facts' and run:
            names={o['id']:o.get('name',o['id']) for d in docs for o in d.get('objects',[])}
            rows=[{**row,'record':{**row['record'],'object_name':names.get(row['record'].get('object'),row['record'].get('object'))}} for row in rows]
            manifest=read_json(run_root(paths,run)/'workspace/manifest.json',{})
            images={(d['document_id'],f['physical_page']):'workspace/'+f['path']
                for d in manifest.get('documents',[]) for f in d['files'] if f['kind']=='source_page_image'}
            for row in rows:
                evidence=row['record'].get('evidence',[])
                evidence=evidence if isinstance(evidence,list) else [evidence]
                keys = [(e.get('document_id', row['document_id']), e.get('physical_page'))
                        for e in evidence if isinstance(e, dict)]
                row['artifacts'] = list(dict.fromkeys(images[key] for key in keys if key in images))
    elif stage == 'coverage':
        rows=[{'document_id':p['document_id'],'record':p} for p in run.get('coverage',{}).get('pages',[]) if not document or p['document_id']==document]
    elif run:
        root = run_root(paths,run)
        if stage=='calls':
            rows=[{'document_id':'','record':r,'artifacts':['control/exchanges/'+r[k] for k in ('request_file','response_file') if r.get(k)]}
                  for r in read_json(root/'control/exchanges/receipts.json',[])]
        elif stage=='tools':
            log=root/'control/tool_calls.jsonl'
            if log.exists():
                for line in log.read_text(encoding='utf-8').splitlines():
                    try: rows.append({'document_id':'','record':json.loads(line)})
                    except ValueError: continue  # Last record may still be writing.
        else: rows=[{'document_id':'','record':{'error':e}} for e in run.get('errors',[])]
    artifacts=[]
    if run:
        root=run_root(paths,run)
        artifacts=[p for p in ['run.json','control/protocol.json','control/compatibility.json','control/events.jsonl',
            'control/tool_calls.jsonl','workspace/manifest.json','workspace/outputs/report.md',
            'control/delivery-check.json','control/delivery-repair.json'] if (root/p).exists()]
        artifacts += [p.relative_to(root).as_posix() for p in sorted((root/'control/resume-drafts').glob('*')) if p.is_file()]
        artifacts += [p.relative_to(root).as_posix() for p in sorted((root/'control/delivery-repair-drafts').glob('*')) if p.is_file()]
    return {k:run.get(k) for k in ('summary','usage','elapsed_seconds','metric_note','run_id','started_at','finished_at',
        'settings','model','run_directory','assessment','resumed_from','delivery_repair_attempted')} | {
        'processing_status':run.get('processing_status','not_generated'),
        'can_resume':bool(docs) and can_resume(run),
        'documents':[{k:d.get(k) for k in ('id','name','status','selection')} for d in docs],
        'rows':rows[offset:offset+limit],'count':len(rows),'artifacts':artifacts}


def agent_workspace(paths, *, run_id='', offset=-1, limit=40, event=None):
    if offset < -1 or not 1<=limit<=100:raise ValueError('无效轨迹分页')
    run=select_run(paths,run_id)
    if not run:return {'rows':[],'count':0,'offset':0,'files':[],'pages':[]}
    root=run_root(paths,run)
    trace=trace_snapshot(root,offset=offset,limit=limit,event=event)
    if event is not None:return trace
    manifest=read_json(root/'workspace/manifest.json',{})
    pages=[{'document_id':d['document_id'],'name':d.get('original_filename',d['document_id']),
            'page':f['physical_page'],'path':'workspace/'+f['path']}
        for d in manifest.get('documents',[]) for f in d.get('files',[]) if f['kind']=='source_page_image']
    files=[]
    for folder in ['workspace/outputs','workspace/scratch','control/assessment-previews']:
        for p in sorted((root/folder).rglob('*')):
            if not p.is_file():continue
            try:target=agent_artifact(paths,p.relative_to(root).as_posix(),run.get('run_id',''))
            except ValueError:continue
            files.append({'path':p.relative_to(root).as_posix(),'name':p.name,'bytes':target.stat().st_size})
    return trace | {'run_id':run.get('run_id'),'files':files,'pages':pages,'errors':run.get('errors',[]),
        'storage':{'root':str(root),'inputs':str(root/'workspace/inputs'),'outputs':str(root/'workspace/outputs'),
                   'trace':str(root/'control/events.jsonl'),'exchanges':str(root/'control/exchanges')}}
