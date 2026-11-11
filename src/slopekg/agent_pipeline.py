"""自主 Agent: isolated DSH SDK sessions, source tools and retained receipts."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from .agent_gateway import AgentGateway, dump, utc_now
from .agent_prompts import DELIVERY_REPAIR, SYSTEM, TASK
from .agent_settings import AgentSettings, VERSION
from .model_client import ModelProfile, ModelError


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def validate_outputs(workspace, manifest, tool_calls=()):
    """Structural admission only. Model claims remain pending source review."""
    workspace = Path(workspace)
    errors = []
    try:
        facts = [json.loads(line) for line in (workspace/'outputs/facts.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        obj = load(workspace/'outputs/objects.json')
        objects = obj['objects'] if isinstance(obj, dict) else obj
        coverage = load(workspace/'outputs/coverage.json')
        report = (workspace/'outputs/report.md').read_text(encoding='utf-8-sig')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'errors': ['Missing or malformed deliverable: '+str(exc)], 'facts': [], 'objects': [], 'coverage': {}}
    if (not isinstance(objects, list) or not isinstance(coverage, dict) or not report.strip()
            or not all(isinstance(v,dict) for v in objects+facts)
            or not isinstance(coverage.get('pages'),list)
            or not all(isinstance(v,dict) for v in coverage['pages'])):
        return {'errors': ['Invalid objects/coverage/report root'], 'facts': [], 'objects': [], 'coverage': {}}
    expected = {(d['document_id'], p) for d in manifest['documents'] for p in d['selected_physical_pages']}
    ids = [o.get('id') for o in objects if isinstance(o, dict)]
    fact_ids = [f.get('id') for f in facts if isinstance(f, dict)]
    library_workflow = manifest.get('workflow') == 'recognition_to_library'
    if (not objects and not library_workflow) or len(ids) != len(objects) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        errors.append('Missing or duplicate object IDs')
    if (not facts and not library_workflow) or len(fact_ids) != len(facts) or any(not isinstance(i, str) or not i for i in fact_ids) or len(set(fact_ids)) != len(fact_ids):
        errors.append('Missing or duplicate fact IDs')
    if errors:
        return {'errors':errors,'facts':[],'objects':[],'coverage':coverage}
    known = set(ids)
    by_id = {o['id']:o for o in objects}
    docs = {d['document_id'] for d in manifest['documents']}
    valid_ref = lambda ref: isinstance(ref,str) and ref in known
    required = {'id','document_id','object','parent_object','field','value','unit','condition','role','evidence','status','uncertainty'}
    for obj in objects:
        if not isinstance(obj, dict): continue
        if not isinstance(obj.get('document_id'),str) or obj['document_id'] not in docs or (obj.get('parent_object') and not valid_ref(obj['parent_object'])):
            errors.append('Invalid object document/parent: '+str(obj.get('id')))
        relations = obj.get('relations', [])
        if not isinstance(relations,list):
            errors.append('Invalid relation list: '+obj['id']);relations=[]
        for relation in relations:
            target = relation.get('target') if isinstance(relation, dict) else None
            if not valid_ref(target): errors.append('Invalid relation target: '+str(obj.get('id')))
    for f in facts:
        if not isinstance(f, dict): continue
        if required-set(f): errors.append('Missing fact fields: '+str(f.get('id')))
        if not valid_ref(f.get('object')) or (f.get('parent_object') and not valid_ref(f['parent_object'])):
            errors.append('Unknown fact object: '+str(f.get('id')))
        elif by_id[f['object']].get('document_id') != f.get('document_id'):
            errors.append('Fact/object document mismatch: '+f['id'])
        evidence = f.get('evidence')
        evidence = evidence if isinstance(evidence, list) else [evidence]
        if not evidence or any(not isinstance(e, dict) or not isinstance(f.get('document_id'),str)
                or type(e.get('physical_page')) is not int or (f.get('document_id'), e.get('physical_page')) not in expected
                or not e.get('quote') or not e.get('locator') for e in evidence):
            errors.append('Invalid evidence: '+str(f.get('id')))
        elif any(e.get('document_id', f['document_id']) != f['document_id'] for e in evidence):
            errors.append('Fact/evidence document mismatch: '+f['id'])
        if f.get('status') not in ('ok','conflict','unclear','partial','inferred'):
            errors.append('Unknown model status: '+str(f.get('id')))
        if f.get('role') not in ('observed','design','computed','judgement','citation','method','figure','meta'):
            errors.append('Unknown fact role: '+f['id'])
        elif library_workflow and f.get('role') == 'figure':
            errors.append('Figure content must be archived, not extracted as facts: '+f['id'])
    if any(not isinstance(p.get('document_id'),str) or type(p.get('physical_page')) is not int for p in coverage['pages']):
        return {'errors':errors+['Invalid coverage page identity'],'facts':facts,'objects':objects,'coverage':coverage}
    seen = {(p.get('document_id'), p.get('physical_page')) for p in coverage.get('pages', []) if isinstance(p, dict)}
    if seen != expected or len(coverage.get('pages', [])) != len(expected):
        errors.append('Coverage must list every selected page exactly once')
    viewed = {(workspace/r.get('args', {}).get('path', '')).resolve() for r in tool_calls
              if r.get('tool') == 'view_image' and r.get('status') == 'ok'}
    actual_images = {(d['document_id'], f['physical_page']) for d in manifest['documents'] for f in d['files']
                     if f['kind'] == 'source_page_image' and (workspace/f['path']).resolve() in viewed}
    missing_images = expected-actual_images
    if missing_images: errors.append('Original page images not actually viewed: '+str(sorted(missing_images)))
    for p in coverage.get('pages', []):
        if p.get('status') != 'complete' or p.get('remaining'): errors.append('Declared incomplete page: '+str((p.get('document_id'),p.get('physical_page'))))
    figures = []
    if library_workflow:
        from .recognition_scope import figure
        try:
            archive = load(workspace/'outputs/figures.json')
            if not isinstance(archive, dict) or not isinstance(archive.get('figures'), list):
                raise ValueError('Expected figures array')
            for item in archive['figures']:
                if (item.get('document_id'), item.get('page')) not in expected:
                    raise ValueError('Figure outside selected source pages')
                figures.append({**figure(item['page'], item['bbox'], caption=item.get('caption', ''),
                                         kind=item.get('kind', 'figure')), 'document_id': item['document_id']})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append('Invalid figure archive: '+str(exc))
    return {'errors': errors, 'facts': facts, 'objects': objects, 'coverage': coverage, 'figures': figures,
            'actual_image_pages': len(actual_images), 'report': report}


class AgentPipeline:
    def __init__(self, paths, model_profile, *, settings=None, output_dir=None, progress_callback=None):
        self.paths = paths
        self.profile = ModelProfile.load(Path(model_profile))
        self.settings = settings or AgentSettings()
        self.output_dir = Path(output_dir or paths.output_dir/'agent-recognition').resolve()
        self.progress_callback = progress_callback
        if self.profile.base_url.rstrip('/') != 'https://api.deepseek.com' or self.profile.model != 'deepseek-flash':
            raise ValueError('Agent v0.1 currently validates the project official DeepSeek Flash profile only')

    def progress(self, stage, message):
        if self.progress_callback: self.progress_callback(0, stage, message)

    def _prepare(self, workspace, *, input_pack=None, input_pdfs=None, page_selections=None):
        if bool(input_pack) == bool(input_pdfs): raise ValueError('Provide exactly one PDF list or open task pack')
        manifest = {'documents': [], 'purpose': 'Independent autonomous document recognition; no answers or previous predictions'}
        if input_pack:
            pack = Path(input_pack).resolve(strict=True)
            original = load(pack/'manifest.json')
            for doc in original['documents']:
                safe = {k: doc[k] for k in ('document_id','original_filename','original_source_sha256','original_page_count','selected_physical_pages')}
                safe['files'] = []
                for f in doc['files']:
                    if f['kind'] not in {'source_page_image','native_text_auxiliary_not_ground_truth'}: continue
                    source = (pack/f['path']).resolve(strict=True)
                    target = (workspace/f['path']).resolve()
                    if not source.is_relative_to(pack/'inputs') or not target.is_relative_to(workspace/'inputs'):
                        raise ValueError('Task pack file escapes its inputs directory')
                    if sha(source) != f['sha256']: raise ValueError('Task pack input hash mismatch')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    safe['files'].append({k:f[k] for k in ('path','physical_page','kind','bytes','sha256')})
                manifest['documents'].append(safe)
        else:
            import fitz
            for item in input_pdfs:
                source = Path(item).resolve(strict=True)
                if source.suffix.lower() != '.pdf': raise ValueError('Explicit PDF files required')
                source_hash = sha(source)
                doc_id = 'pdf_'+source_hash[:20]
                with fitz.open(source) as pdf:
                    pages = list((page_selections or {}).get(str(source), range(1,len(pdf)+1)))
                    if not pages or len(set(pages)) != len(pages) or any(type(p) is not int or p<1 or p>len(pdf) for p in pages):
                        raise ValueError('Invalid explicit physical-page selection')
                    doc = {'document_id':doc_id,'original_filename':source.name,'original_source_sha256':source_hash,
                           'source_path':str(source),
                           'original_page_count':len(pdf),'selected_physical_pages':pages,'files':[]}
                    folder = workspace/'inputs'/doc_id
                    folder.mkdir(parents=True,exist_ok=True)
                    for number in pages:
                        page=pdf[number-1]
                        scale=self.settings.page_edge/max(page.rect.width,page.rect.height)
                        image=folder/f'p{number}.png';text=folder/f'p{number}.txt'
                        page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).save(image)
                        text.write_text(page.get_text('text'),encoding='utf-8')
                        for p,kind in [(image,'source_page_image'),(text,'native_text_auxiliary_not_ground_truth')]:
                            doc['files'].append({'path':p.relative_to(workspace).as_posix(),'physical_page':number,
                                'kind':kind,'bytes':p.stat().st_size,'sha256':sha(p)})
                if sha(source) != source_hash: raise ValueError('Source PDF changed during preparation')
                manifest['documents'].append(doc)
        if len({d['document_id'] for d in manifest['documents']}) != len(manifest['documents']):
            raise ValueError('Duplicate input document')
        from .recognition_scope import IMAGE_POLICY
        manifest.update(workflow='recognition_to_library', image_policy=IMAGE_POLICY)
        dump(workspace/'manifest.json',manifest)
        for name in ('outputs','scratch'): (workspace/name).mkdir(exist_ok=True)
        return manifest

    def run(self, input_pdfs=None, *, input_pack=None, prepare_only=False, preflight_only=False, page_selections=None, resume_from=None):
        run_id='agent-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        run_dir=self.output_dir/'runs'/run_id
        workspace=run_dir/'workspace';control=run_dir/'control'
        workspace.mkdir(parents=True);control.mkdir()
        result={'recognizer':'agent','version':VERSION,'run_id':run_id,'started_at':utc_now(),
            'processing_status':'preparing','model':self.profile.identity(),'settings':asdict(self.settings),
            'documents':[],'summary':{},'precision':None,'recall':None,
            'metric_note':'Agent原文识别候选；结构检查和模型自查不等于准确率或专业签认。',
            'run_directory':str(run_dir),'isolation':'独立DSH会话目录；仅受控资料工具可读任务目录；无通用shell/任意代码工具',
            'errors':[]}
        def save():
            index=self.output_dir/'run.json'
            if index.exists():
                previous=load(index)
                previous_id=previous.get('run_id','')
                if previous_id!=run_id and previous_id.startswith('agent-') and all(c.isalnum() or c=='-' for c in previous_id):
                    dump(self.output_dir/'history'/f'{previous_id}.json',previous)
            dump(run_dir/'run.json',result);dump(self.output_dir/'run.json',result)
        started=time.monotonic()
        task_prompt=TASK+ ('\n\n用户本次补充要求：\n'+self.settings.task_instruction.strip() if self.settings.task_instruction.strip() else '')
        save()
        try:
            self.progress('agent_prepare','准备本次原页图像与辅助文字')
            manifest=self._prepare(workspace,input_pack=input_pack,input_pdfs=input_pdfs,page_selections=page_selections)
            drafts={}
            if resume_from:
                from .agent_service import select_run,run_root,can_resume
                previous=select_run(self.paths,resume_from)
                if not can_resume(previous):
                    raise ValueError('这个任务没有需要接续整理的识别或评估草稿')
                previous_root=run_root(self.paths,previous)
                old_manifest=load(previous_root/'workspace/manifest.json')
                def signature(m):
                    return [(d['document_id'],d['original_source_sha256'],d['selected_physical_pages'],
                             [(f['path'],f['sha256']) for f in d['files']]) for d in m['documents']]
                if signature(old_manifest)!=signature(manifest):raise ValueError('接续资料、所选页或原件哈希不一致')
                for source in (previous_root/'workspace/outputs').glob('*'):
                    if source.is_file() and source.suffix.lower() in {'.json','.jsonl','.md','.txt'}:
                        if not source.resolve().is_relative_to((previous_root/'workspace/outputs').resolve()):raise ValueError('草稿路径越界')
                        # Preserve old assessment drafts as history, without
                        # making them a deliverable of the new recognition run.
                        if source.name != 'assessment.json':
                            shutil.copyfile(source,workspace/'outputs'/source.name)
                        (control/'resume-drafts').mkdir(exist_ok=True)
                        shutil.copyfile(source,control/'resume-drafts'/source.name)
                        drafts[source.name]=sha(source)
                result['resumed_from']=resume_from
                previous_errors = previous.get('errors', [])
                task_prompt+='\n\n本次为中断恢复：outputs 中已有上一任务的草稿，原件相同。核对并保留已正确的抽取。必须在本次实际查看全部选定原图、核对依据并运行 check_outputs。原草稿不是已确认答案，旧任务记录另行保留。'
                if previous_errors:
                    task_prompt+='\n上一任务的校验诊断（作为数据核对）：\n'+json.dumps(previous_errors,ensure_ascii=False)
                    task_prompt+='\n本次只修正识别资料和归属。旧评估交接诊断保留供追溯，不需生成评估文件。'
            if (control/'stop.request.json').exists():
                result['processing_status']='cancelled';return result
            result['documents']=[{'id':d['document_id'],'name':d['original_filename'],
                'path':d.get('source_path'),
                'selection':{'selected_pages':d['selected_physical_pages'],'source_pages':d['original_page_count']},
                'source_sha256':d['original_source_sha256'],'status':'prepared','facts':[],'objects':[]} for d in manifest['documents']]
            result['summary']={'documents':len(manifest['documents']),'pages':sum(len(d['selected_physical_pages']) for d in manifest['documents']),
                               'facts':0,'objects':0,'calls':0,'pending':0}
            dump(control/'protocol.json',{'version':VERSION,'system_prompt':SYSTEM,'task_prompt':task_prompt,
                'delivery_repair_prompt':DELIVERY_REPAIR,
                'resumed_from':resume_from,'initial_draft_sha256':drafts,
                'model':self.profile.identity(),'settings':asdict(self.settings),'manifest_sha256':sha(workspace/'manifest.json'),
                'code_sha256':{str(p.relative_to(self.paths.root)):sha(p) for p in
                    [*sorted((self.paths.root/'src/slopekg').glob('agent_*.py')),
                     *sorted((self.paths.root/'src/slopekg/standard_assessment').glob('*.py')),
                     *sorted((self.paths.root/'scripts/agent_sdk').glob('*.mjs')),
                     *sorted((self.paths.root/'scripts/agent_sdk').glob('*.py'))]}})
            for relative in load(control/'protocol.json')['code_sha256']:
                frozen=control/'source'/relative
                frozen.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(self.paths.root/relative,frozen)
            if prepare_only:
                result['processing_status']='prepared';return result
            runtime=load(self.paths.root/'.private/agent-runtime/runtime.json')
            node=Path(runtime['node']).resolve(strict=True);modules=Path(runtime['modules']).resolve(strict=True)
            spec={'workspace':str(workspace),'control_dir':str(control),'dsh_home':str(control/'dsh-home'),
                  'user_home':str(control/'user-home'),'modules':str(modules),'model':self.profile.model,
                  'max_tokens':self.profile.max_output_tokens,'reasoning_effort':self.settings.reasoning_effort,
                  'system_prompt':SYSTEM,'task_prompt':task_prompt,'delivery_repair_prompt':DELIVERY_REPAIR,
                  'session_id':run_id,'timeout_seconds':self.settings.timeout_seconds,
                  'preflight_only':preflight_only,
                  'assessment_python':sys.executable,
                  'assessment_preview_script':str(self.paths.root/'scripts/agent_sdk/preview_assessment.py')}
            spec['expected_version']=runtime.get('expected_version')
            self.progress('agent_start','启动独立DSH SDK会话并检查能力')
            with AgentGateway(self.profile,self.paths.root,control/'exchanges',max_calls=self.settings.max_calls) as gateway:
                spec['gateway_url']=gateway.url;dump(control/'launch.json',spec)
                env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT',
                    'SYSTEMDRIVE','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','HTTPS_PROXY','HTTP_PROXY','NO_PROXY','SSL_CERT_FILE','SSL_CERT_DIR'}}
                env['SLOPEKG_GATEWAY_TOKEN']=gateway.secret
                result['processing_status']='running';save()
                with (control/'stdout.txt').open('w',encoding='utf-8') as stdout,(control/'stderr.txt').open('w',encoding='utf-8') as stderr:
                    proc=subprocess.Popen([str(node),str(self.paths.root/'scripts/agent_sdk/run.mjs'),str(control/'launch.json')],
                        cwd=workspace,env=env,stdout=stdout,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    try:
                        stop_started=None
                        while proc.poll() is None:
                            if (control/'stop.request.json').exists():
                                stop_started=stop_started or time.monotonic()
                                result['processing_status']='stopping'
                                if time.monotonic()-stop_started>20:proc.terminate()
                            if self.settings.timeout_seconds is not None and time.monotonic()-started>self.settings.timeout_seconds+90:
                                proc.terminate();raise TimeoutError('Agent wall-clock limit exceeded; outputs retained incomplete')
                            repairing=(control/'delivery-repair.json').is_file()
                            if repairing: result['delivery_repair_attempted']=True
                            result['usage']=gateway.usage();result['summary']['calls']=len(gateway.receipts);save()
                            self.progress('agent_repair' if repairing else 'agent_reading',
                                f"{'Agent 正在核对交接问题' if repairing else 'Agent 自主识别中'}，已发 {len(gateway.receipts)} 次模型请求")
                            time.sleep(2)
                    finally:
                        if proc.poll() is None:proc.terminate()
                        proc.wait(timeout=15)
                result['usage']=gateway.usage();result['summary']['calls']=len(gateway.receipts)
                if (control/'delivery-repair.json').is_file(): result['delivery_repair_attempted']=True
                if (control/'stop.request.json').exists():
                    result['processing_status']='cancelled'
                    result['errors'].append('用户主动停止；已生成文件保留，未完成最终核验')
                    return result
                if proc.returncode:result['errors'].append('SDK process failed; see control/sdk_error.txt or stderr.txt')
                if result['usage']['incomplete_requests']:result['errors'].append('Some model responses were incomplete; inspect retained receipts')
            if preflight_only:
                result['processing_status']='ready' if not result['errors'] else 'failed';return result
            log=control/'tool_calls.jsonl'
            tool_calls=[json.loads(s) for s in log.read_text(encoding='utf-8').splitlines()] if log.exists() else []
            checked=validate_outputs(workspace,manifest,tool_calls)
            result['errors'].extend(checked['errors'])
            for doc in result['documents']:
                doc['facts']=[{**f,'model_status':f['status'],'status':'pending'} for f in checked['facts'] if f.get('document_id')==doc['id']]
                doc['objects']=[o for o in checked['objects'] if o.get('document_id')==doc['id']]
                doc['archived_figures']=[f for f in checked.get('figures',[]) if f['document_id']==doc['id']]
                doc['status']='partial' if result['errors'] else 'completed'
            result['coverage']=checked['coverage']
            result['summary'].update(facts=len(checked['facts']),objects=len(checked['objects']),pending=len(checked['facts']),
                                     actual_image_pages=checked.get('actual_image_pages',0))
            # Verify immutable inputs even when the model reports success.
            for d in manifest['documents']:
                for f in d['files']:
                    if sha(workspace/f['path']) != f['sha256']:result['errors'].append('Input changed: '+f['path'])
            result['processing_status']='partial' if result['errors'] else 'completed'
            result['workflow'] = manifest.get('workflow')
            result['image_policy'] = manifest.get('image_policy')
            if result['processing_status']=='completed' and manifest.get('workflow') != 'recognition_to_library':
                from .standard_assessment.agent_bridge import validate_submission
                try:
                    submission=load(workspace/'outputs/assessment.json')
                    validate_submission(submission,result)
                    from .standard_assessment.agent_preview import preview_submission
                    preview=preview_submission(self.paths,workspace)
                    dump(control/'final-assessment-preview.json',preview)
                    result['assessment_preview']={'cases':len(preview['cases']),
                        'input_sha256':preview['input_sha256'],'new_model_requests':0}
                    result['assessment_submission']=submission
                    result['assessment']={'status':'ready','cases':[],'new_model_requests':0}
                    save()
                    if self.output_dir == (self.paths.output_dir/'agent-recognition').resolve():
                        from .agent_service import intake_assessments
                        result['assessment']=intake_assessments(self.paths,run_id)
                        if result['assessment'].get('error'):result['errors'].append(result['assessment']['error'])
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    result['assessment']={'status':'failed','error':str(exc),'cases':result.get('assessment',{}).get('cases',[]),
                                          'retry_mode':'intake' if result.get('assessment_preview') else 'resume'}
                    dump(control/'assessment-receipt.json',result['assessment'])
                    result['errors'].append('Agent评估数据尚未接入：'+str(exc))
        except (OSError,ValueError,KeyError,TypeError,TimeoutError,ModelError,subprocess.SubprocessError) as exc:
            result['processing_status']='failed';result['errors'].append(str(exc))
        finally:
            result.update(finished_at=utc_now(),elapsed_seconds=round(time.monotonic()-started,3))
            save()
        return result
