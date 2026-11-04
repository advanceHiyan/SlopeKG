"""Independent fourth-route service, with explicit model-send authorization over HTTP."""
from pathlib import Path
from .document_pipeline import DocumentPipeline
from .document_settings import DocumentSettings
from .model_client import ModelProfile,CompatibleVisionClient
from .pipeline_lock import pipeline_run_guard
from .recognition import visual_job_options
from .storage import read_json
from .provenance import version_status


def run_document_recognition(paths,*,input_pdfs,model_profile,review_profile=None,document_settings=None,
                             prepare_only=False,progress_callback=None):
    if not input_pdfs or model_profile is None:raise ValueError('整读需要明确PDF和模型配置')
    model=CompatibleVisionClient(ModelProfile.load(Path(model_profile)),paths.root)
    reviewer=CompatibleVisionClient(ModelProfile.load(Path(review_profile)),paths.root) if review_profile else None
    with pipeline_run_guard(paths.output_dir/'.pipeline.lock'):
        return DocumentPipeline(model,paths.output_dir/'document-recognition',
            settings=DocumentSettings(**(document_settings or {})),review_model=reviewer,
            progress_callback=progress_callback).run(input_pdfs,prepare_only=prepare_only)


def document_job_options(paths,body):
    if any(k in body for k in ('visual_settings','knowledge_settings')):raise ValueError('整读不能混用其他路线设置')
    options=visual_job_options(paths,{**body,'model_profile':body.get('model_profile','deepseek-knowledge'),'visual_settings':{}})
    options.pop('visual_settings')
    limits=body.get('document_settings',{})
    if not isinstance(limits,dict) or set(limits)-{'max_calls','model_concurrency','record_model_inputs'}:
        raise ValueError('网页仅接受调用上限、并发及记录开关')
    DocumentSettings(**limits)
    return {**options,'recognizer':'document','document_settings':limits}


def document_payload(paths,*,document='',stage='facts',offset=0,limit=50):
    if stage not in {'pages','blocks','groups','claims','facts','errors','stages'} or offset<0 or not 1<=limit<=100:
        raise ValueError('无效整读记录筛选')
    run=read_json(paths.output_dir/'document-recognition/run.json',{})
    docs=run.get('documents',[])
    if document and document not in {d['id'] for d in docs}:raise ValueError('文档不存在')
    selected=[d for d in docs if not document or d['id']==document]
    rows=[{'document_id':d['id'],'record':row} for d in selected for row in d.get(stage,[])]
    return {'processing_status':run.get('processing_status','not_generated'),'summary':run.get('summary',{}),
        'metric_note':run.get('metric_note'),'implementation_status':version_status(run.get('producer')),
        'documents':[{'id':d['id'],'name':Path(d['path']).name,'status':d['status'],'selection':d['selection']} for d in docs],
        'stage':stage,'count':len(rows),'offset':offset,'limit':limit,'rows':rows[offset:offset+limit]}
