"""Application service for visual recognition; shared by CLI, HTTP and jobs.

Only named, local model profiles are selectable over HTTP. Credentials and raw
provider receipts never cross the public read API. Results remain separate from
the legacy graph, and model approval is explicitly not human approval.
"""
from pathlib import Path
import re

from .model_client import CompatibleVisionClient, ModelProfile
from .storage import read_json, discover_pdf_files
from .visual_document import ExistingOcrEvidence
from .visual_pipeline import VisualPipeline
from .visual_settings import VisualSettings
from .pipeline_lock import pipeline_run_guard


def run_visual_recognition(paths, *, input_pdfs, model_profile, review_profile=None,
                           visual_settings=None, prepare_only=False, progress_callback=None):
    if not input_pdfs or model_profile is None:
        raise ValueError("Multimodal recognition requires explicit input_pdfs and model_profile")
    settings = VisualSettings(**(visual_settings or {}))
    profile = ModelProfile.load(Path(model_profile))
    reviewer = CompatibleVisionClient(ModelProfile.load(Path(review_profile)), paths.root) if review_profile else None
    pipeline = VisualPipeline(CompatibleVisionClient(profile, paths.root), paths.output_dir / 'recognition',
        settings=settings, ocr_evidence=ExistingOcrEvidence(paths.parsed_dir) if settings.use_existing_ocr else None,
        review_model=reviewer, progress_callback=progress_callback)
    with pipeline_run_guard(paths.output_dir / '.pipeline.lock'):
        result = pipeline.run([Path(p) for p in input_pdfs], prepare_only=prepare_only)
    result['processing_message'] = ('本地准备完成，未调用模型' if prepare_only else
        '多模态识别结束；自动核验通过不等于人工确认，未自动写入图谱')
    return result


def profile_path(paths, name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        raise ValueError('请选择已配置的模型')
    path = (paths.root / 'config/models' / (name + '.json')).resolve()
    if not path.is_relative_to((paths.root / 'config/models').resolve()) or not path.is_file():
        raise ValueError('模型配置不存在')
    return path


def profile_catalog(paths):
    result=[]
    for path in sorted((paths.root / 'config/models').glob('*.json')):
        try:
            profile = ModelProfile.load(profile_path(paths, path.stem))
        except (ValueError, TypeError, OSError):
            continue
        # Presence only: listing models never reads or discloses their secrets.
        key_file = (paths.root / profile.api_key_file).resolve() if profile.api_key_file else None
        result.append({'id':path.stem, 'name':profile.name, 'model':profile.model,
            'endpoint':profile.base_url, 'credential_configured':bool(profile.api_key_env or
                (key_file and key_file.is_relative_to((paths.root / '.secrets').resolve()) and key_file.is_file()))})
    return {'profiles':result}


def recognition_documents(paths):
    return {'documents':[{'path':p.relative_to(paths.raw_dir).as_posix(),
                           'name':p.name,'size_bytes':p.stat().st_size}
                          for p in discover_pdf_files(paths.raw_dir,recursive=True)
                          if p.resolve().is_relative_to(paths.raw_dir.resolve())]}


def visual_job_options(paths, body):
    names=body.get('input_pdfs')
    if not isinstance(names,list) or not 1 <= len(names) <= 100 or any(not isinstance(v,str) for v in names):
        raise ValueError('请选择1～100份目录中的PDF')
    files=[]
    for name in names:
        source=(paths.raw_dir / name).resolve()
        if (not source.is_relative_to(paths.raw_dir.resolve()) or source.suffix.lower()!='.pdf'
                or not source.is_file()):
            raise ValueError('只能处理原始资料目录中存在的PDF')
        files.append(str(source))
    if len(set(files)) != len(files):
        raise ValueError('不能重复选择同一份PDF')
    for name in ('prepare_only','allow_external'):
        if name in body and type(body[name]) is not bool:
            raise ValueError(name+'必须为布尔值')
    prepared=body.get('prepare_only',True)
    if not prepared and body.get('allow_external') is not True:
        raise ValueError('请确认允许将所选PDF发送到所选模型接口')
    if body.get('parse_mode','basic')!='basic' or body.get('reuse_parsed') or body.get('use_llm'):
        raise ValueError('多模态与旧basic/deep选项不能混用')
    profile=profile_path(paths,body.get('model_profile','deepseek'))
    reviewer=profile_path(paths,body['review_profile']) if body.get('review_profile') else None
    # Browser cannot inject endpoint, key path, retry_failed, or arbitrary model options.
    limits=body.get('visual_settings',{})
    if not isinstance(limits,dict) or set(limits)-{'max_calls','model_concurrency'}:
        raise ValueError('网页仅接受调用上限和模型并发设置')
    if any(type(v) is not int for v in limits.values()):
        raise ValueError('调用上限和并发必须是整数')
    VisualSettings(**limits)
    return {'recognizer':'multimodal','input_pdfs':files,'model_profile':str(profile),
        'review_profile':str(reviewer) if reviewer else None,'visual_settings':limits,'prepare_only':prepared}


def recognition_payload(paths, *, status='', offset=0, limit=50):
    if status not in {'','pending','verified_model','rejected'}:
        raise ValueError('无效候选状态')
    if offset<0 or not 1<=limit<=200:
        raise ValueError('无效分页参数')
    run=read_json(paths.output_dir/'recognition/run.json',{})
    facts=[f for d in run.get('documents',[]) for f in d.get('facts',[])]
    selected=[f for f in facts if not status or f.get('status')==status]
    fields=('id','page','object','field','value','unit','unit_basis','scope','role','condition','time','qualifier',
            'status','reason','quote','support_quotes','admission_checks','quality_issues')
    rows=[]
    documents=run.get('documents',[])
    by_id={d.get('id'):Path(d.get('path','')).name for d in documents}
    source_paths={d.get('id'):Path(d['path']).resolve().relative_to(paths.raw_dir.resolve()).as_posix()
                  for d in documents if d.get('path') and Path(d['path']).resolve().is_relative_to(paths.raw_dir.resolve())}
    for fact in selected[offset:offset+limit]:
        rows.append({**{k:fact.get(k) for k in fields},'file_name':by_id.get(fact.get('document_id'),''),
                     'source_path':source_paths.get(fact.get('document_id'))})
    return {'processing_status':run.get('processing_status','not_generated'),
        'summary':run.get('summary',{}),'precision':None,'recall':None,
        'metric_note':'自动核验状态不是人工准确率或召回率；候选未自动写入主图谱。',
        'model':{k:run.get('model',{}).get(k) for k in ('name','model','base_url')},
        'documents':[{'file_name':Path(d.get('path','')).name,'status':d.get('status'),
                      'source_page_count':d.get('source_page_count'),'coverage':d.get('coverage'),
                      'pages':[{'page':p['page'],'status':p['status'],'errors':p.get('errors',[])} for p in d.get('pages',[])]}
                     for d in documents],
        'count':len(selected),'offset':offset,'limit':limit,'rows':rows}
