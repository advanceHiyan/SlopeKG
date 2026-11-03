"""Build the two stable Knowledge debug HTMLs from source and open run evidence.

Read-only toward recognition data. No model client is constructed. Evaluation
directories and former holdout documents are excluded from the evidence set.
"""
from __future__ import annotations
import ast
from collections import Counter
from datetime import datetime
import hashlib
from html import escape
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/知联调试'
ASSETS=OUT/'assets'
OPEN=ROOT/'outputs/20261031_固定回归与隐藏盲验'
READS=[]
DENIED_HASHES={'c325fc949c6ed19e380321693b8f2d7f109948a893fbfef6f3f7ae869cb227d4',
 'b10a9b843c1c02975009a7c48404fc60b073bc4985cb359896d9f3933c8ba9c7'}
def read_json(path):
    path=Path(path).resolve()
    assert '/.private/evaluation/' not in path.as_posix().lower()
    assert not re.search(r'/(R0[123])/',path.as_posix(),re.I)
    READS.append(str(path.relative_to(ROOT)))
    return json.loads(path.read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def source(path):
    p=ROOT/path;t=p.read_text(encoding='utf-8');tree=ast.parse(t) if p.suffix=='.py' else None
    spans={}
    if tree:
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                spans[node.name]=[node.lineno,node.end_lineno]
            elif isinstance(node,ast.ClassDef):
                spans[node.name]=[node.lineno,node.end_lineno]
                for child in node.body:
                    if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef)):
                        spans[node.name+'.'+child.name]=[child.lineno,child.end_lineno]
    target=ASSETS/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    return {'path':path,'sha256':sha(p),'text':t,'functions':spans,'url':target.relative_to(OUT).as_posix()}
def safe(data):return json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
def persist_raw(case,version,name,data):
    target=ASSETS/'cases'/case/version/name;dump(target,data)
    return target.relative_to(OUT).as_posix()

STAGES=[
('entry','0 入口与参数','PDF路径列表 + 命名模型配置 + KnowledgeSettings','创建知联独立任务；网页提交max_calls/model_concurrency/ocr_pages及可选record_model_inputs，服务端不接收浏览器自选任意端点或密钥。prepare_only只准备本地证据。调试工作台默认开启完整输入输出留痕。','任务锁、路径与页面范围合法；模型/复核模型配置有效。','src/slopekg/knowledge_service.py','run_knowledge_recognition',False),
('parse','1 解析与OCR复用','原件SHA-256、基读解析缓存、已完成OCR','只有完整且哈希和几何版本一致的缓存才复用；否则PdfParser.parse(generic=True)。PyMuPDF提文字，pdfplumber逐页找表。复用同原件OCR；稀疏页补全文OCR任务，ocr_pages>0才执行本地OCR。','原件变动、缓存不全、坐标非法、OCR未完成均可留下缺口。OCR是本地PaddleOCR，不是知联把图片发给大模型。','src/slopekg/knowledge_document.py','prepare_document',False),
('units','2 证据单元与原始分块','文字块 / 表格行 / OCR行','单元保存id、page、text、bbox、source、表格cells/header_ids。默认单元文本约1400字符；再按2800字符拼初始chunk，重叠1个单元。表头可以跨chunk作为结构父证据重复附带。','这里是字符/结构切分，不是模型先做语义切分；重复证据、OCR缺字与表头误识别都可能继续传播。','src/slopekg/knowledge_document.py','pieces',False),
('pack','3 完整请求容量与目录','chunk + 对象目录 + 可能的subject/context','len(SYSTEM)+len(阶段提示+“\\n输入：”+compact(context))≤60000。同时保留batch_chars软界。正文可继续按源单元拆分并带齐表头。导航元数据>20000时用完整身份索引，长描述/重复定位留本地。','身份索引本身或单源单元仍超限会明确失败；不是把max_tokens再加大就能修。初始预检不可能预知未来目录增长后的所有分批。','src/slopekg/knowledge_batching.py','pack_chunks',False),
('discover','4 全文对象发现','当前批chunks + 已发现目录（含catalog）','逐批、串行请求knowledge_discover。模型分entity/concept及focus/catalog/support。有效mentions必须引用本批真实span。相同kind+规范name+context可复用身份；新id由doc+kind+name+context哈希生成。','focus默认最多100；catalog/support不受这个focus上限直接控制。别名支持真实性主要依靠模型提示，代码只检查字符串类型，不等于所有别名已逐条证实。','src/slopekg/knowledge_pipeline.py','KnowledgePipeline._recognize',True),
('attribute','5 跨分块多对多归属','完整focus目录 + 当前batch + 文档用途候选','knowledge_attribute可并发；返回每chunk的对象绑定、span_ids/span_ranges与原子facts。区间只能在已提供来源内展开。坏绑定隔离，合法绑定保留；用真实span恢复chunk归属，表头补进允许证据。','只有focus进入常规归属；source_local会拦截无本页身份锚点的孤立数值。数字可能存在于OCR，却仍不成为某对象的属性。','src/slopekg/knowledge_claims.py','partition_attribution',True),
('bind','6 本地归属补充与原子保留','模型绑定 + 名称、标题、表格行线索 + 原子候选','prepare_bindings补唯一名称和章节范围；再经过主体门控。_retain_attributed先保存通过同一来源检查的原子候选；后面模型摘要漏返，不能把这些已保存候选擦掉。','标题只是线索。抽象概念不能自动承接具体工程事实；同名、子对象和跨页数字可能仍待审。','src/slopekg/knowledge_composition.py','prepare_bindings',False),
('synthesize','7 对象档案重组','对象非连续来源 + 完整身份目录 + 已提原子事实提示','composition_jobs按每来源len(text)+90、24000软预算分段。pack_jobs优先共享原文合批，最多6个不同对象。真实source_spans只传一份；单对象用SYNTHESIZE，多对象用RECOMPOSE。完整包再次计量。','可选原子提示放不下会计入omitted，但完整候选仍在doc.facts。对象响应逐个校验/隔离；模型没返回的量不会凭规则补造。','src/slopekg/knowledge_pipeline.py','KnowledgePipeline._composition_request',True),
('gate','8 引文、量义与主体门控','原子事实 / 重组事实 / 关系','_item检查引文来自允许span、值/单位、非空scope、有效role；数据行补真实表头，单位按数值列检查。gate_ownership核具体对象、章节、同名与OCR置信度。','source_aligned表示字面及规则对齐；不代表专家验证。数值存在不证明字段含义正确。程序仍依赖模型理解field、scope、role。','src/slopekg/knowledge_pipeline.py','KnowledgePipeline._item',False),
('review','9 有限语义复核','本地无质量问题且review_required的事实/关系','默认selective；普通对齐事实不逐条再调模型。最多4个批次，单批≤64条且完整请求能放下。关系强制review_required；可选复核用review_model（未另配时同一模型）。','本地已不合格的条目不会花API去修。复核用同一模型不具备统计上的独立性；verified_model仍不是人工核准。','src/slopekg/knowledge_pipeline.py','KnowledgePipeline._selective_review',True),
('recover','10 一次受限未归属正文查漏','support/catalog标题、未归属正文、对象锚点','review之后才运行。只在有可识别编号/Markdown标题引用时触发；选含至少2个非数字字母/文字的未归属单元，按2×len(text)+160软计量，最多一批。完整请求不足就缩减并保留remaining_source_ids。','不是通用迭代查漏器；没有标题、数字为主的图形、超出一批的正文可能继续漏。排在可选复核后，也可能拿不到剩余调用额度。','src/slopekg/knowledge_recovery.py','recovery_package',True),
('finish','11 冲突、覆盖、对象页与落盘','全部候选、各阶段状态、源哈希','同对象/field/scope/role/工况/时间的不同值会降为pending。对象页由已有事实派生，不再调API。保存run.json、facts.jsonl、relations.jsonl、缓存、可选输入、对象页。','任何阶段完成都不等于全文件完成。图形缺口、未选页、无归属页、错误、未组织对象或未完成复核/查漏可使文档partial。','src/slopekg/knowledge_pipeline.py','KnowledgePipeline._finish_document',False)]

PARAM_NOTES={
'chunk_chars':'初始chunk正文软目标。单元pieces上限max(200,该值//2)，默认1400；表头加入后可超过chunk软值。',
'batch_chars':'发现/归属文本工作量软界：Σ(len(text)+70)。查漏初选另用2×len(text)+160。完整请求仍单独过60000硬门。',
'synthesis_chars':'重组来源软预算Σ(len(text)+90)；并不是完整请求长度。',
'overlap_units':'初始chunk重叠单元数，不是重叠字符数；表头还可额外重复。',
'min_page_chars':'去空白原生文字达到该值走native_text；低于此值且有图/线时追加全页OCR任务。',
'max_entities':'focus对象上限；catalog/support在references，不能据此推断全目录最多100条。',
'max_calls':'一次管线运行的实际新调度上限，重试计入，缓存不占；多PDF共用，非每个文件各64。',
'model_concurrency':'模型网络/缓存任务信号量上限。发现串行；归属/重组可并发；并发不会放大总额度。',
'max_prompt_chars':'系统字符串+阶段提示+JSON完整字符串的Python字符数，不是token、字节或模型上下文上限。',
'allow_model_calls':'False仍可读精确缓存；无缓存则不实发。',
'retry_failed':'旧失败/中断缓存的显式重试开关；不是瞬时429/5xx退避策略。',
'synthesis_batch_objects':'共享来源重组一批最多不同对象数；一个对象可有多个part。',
'review_mode':'selective / all / none；默认仅本地合格且模型要求复核的事实和关系。',
'max_review_calls':'本次运行的可选语义复核批次数硬界；不含正常发现、归属、重组、查漏。',
'ocr_pages':'默认0只复用已完成OCR。正数才执行本地待办；具体选择按OCR任务队列，不是大模型看这些页的图片。',
'max_transient_retries':'单请求允许额外重试的次数，限429/500/502/503明确响应。',
'transient_retry_budget':'全运行额外瞬时重试总数，同时仍占max_calls。',
'retry_backoff_seconds':'指数退避起点；尊重≤30秒Retry-After，共享接口冷却最多30秒。',
'record_model_inputs':'管线默认False；新前端调试任务默认True。开启后保存inputs完整system/prompt与图片清单、exchanges实际请求和供应商原始回复、debug-trace逐次状态；鉴权头不落盘。缓存查找重建输入会标记，历史缺失原文不补造。',
'max_images':'固定0，本路线不传PDF页图给模型。','max_image_pixels':'固定0。','max_image_bytes':'固定0。'}

def collect_source():
    names=['knowledge_pipeline','knowledge_settings','knowledge_document','knowledge_batching','knowledge_directory',
           'knowledge_prompts','knowledge_claims','knowledge_composition','knowledge_attribution','knowledge_evidence',
           'knowledge_ownership','knowledge_roles','knowledge_tables','knowledge_recovery','knowledge_service',
           'visual_runtime','visual_evidence','visual_contract','visual_batching','model_client','model_limits','parsers','ocr','provenance']
    files={n:source('src/slopekg/'+n+'.py') for n in names}
    files.update({
        'assessment_debug_flow':source('src/slopekg/standard_assessment/debug_flow.py'),
        'assessment_debug_http':source('src/slopekg/standard_assessment/debug_http.py'),
        'assessment_knowledge_bridge':source('src/slopekg/standard_assessment/knowledge_bridge.py'),
        'assessment_service':source('src/slopekg/standard_assessment/service.py'),
        'assessment_workflow':source('src/slopekg/standard_assessment/workflow.py'),
    })
    tree=ast.parse(files['knowledge_settings']['text'])
    defaults={}
    for node in ast.walk(tree):
        if isinstance(node,ast.ClassDef) and node.name=='KnowledgeSettings':
            defaults={n.target.id:ast.literal_eval(n.value) for n in node.body if isinstance(n,ast.AnnAssign)}
    bounds={}
    for n in ast.walk(tree):
        if isinstance(n,ast.List):
            try:
                v=ast.literal_eval(n)
                if v and all(isinstance(i,tuple) and len(i)==3 and isinstance(i[0],str) for i in v):bounds.update({i[0]:[i[1],i[2]] for i in v})
            except (ValueError,TypeError):pass
    prompts={}
    def prompt_value(node):
        if isinstance(node,ast.Name):return prompts[node.id]
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):return prompt_value(node.left)+prompt_value(node.right)
        return ast.literal_eval(node)
    for n in ast.parse(files['knowledge_prompts']['text']).body:
        if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name):prompts[n.targets[0].id]=prompt_value(n.value)
    model=read_json(ROOT/'config/models/deepseek-knowledge.json')
    model={k:v for k,v in model.items() if not k.startswith('api_key')}
    packages={}
    for n in ['PyMuPDF','pdfplumber','paddleocr','paddlepaddle','paddlepaddle-gpu']:
        try:packages[n]=importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:packages[n]='未安装此分发名'
    patterns=['G209','G359','G301','R01','R02','74.80','76.5','255']
    hits=[]
    for name,record in files.items():
        if not name.startswith('knowledge_'):continue
        for line,text in enumerate(record['text'].splitlines(),1):
            matches=[p for p in patterns if p in text]
            if matches:hits.append({'file':record['path'],'line':line,'patterns':matches,'text':text})
    digest=hashlib.sha256(''.join(p+v['sha256'] for p,v in sorted(files.items())).encode()).hexdigest()
    return {'files':files,'defaults':defaults,'bounds':bounds,'param_notes':PARAM_NOTES,'model':model,
            'prompts':prompts,'packages':packages,'snapshot_sha256':digest,'fingerprint_scan':{'patterns':patterns,'hits':hits}}

def load_variant(case_id,label,run_path,page_map):
    p=ROOT/run_path;run=read_json(p);assert len(run['documents'])==1
    doc=run['documents'][0];assert doc['source_sha256'] not in DENIED_HASHES
    assert not any(x in doc['path'].lower().replace('\\','/') for x in ['sealed_holdout','/r01/','/r02/','/r03/'])
    original=Path(doc['path']);assert original.exists() and sha(original)==doc['source_sha256']
    key=hashlib.sha256(str(p).encode()).hexdigest()[:10]
    raw_url=persist_raw(case_id,key,'run.json',run)
    shutil.copy2(p,OUT/raw_url)  # Download retains the original run bytes/hash.
    requests=[]
    for ip in sorted((p.parent/'inputs').glob('*.json')):
        inp=read_json(ip);cached=read_json(p.parent/'cache'/ip.name) if (p.parent/'cache'/ip.name).exists() else {}
        assert not inp.get('images'),'Knowledge report must contain text-only evidence'
        requests.append({'stage':inp['stage'],'cache_key':inp.get('cache_key',ip.stem),'input':inp,
            'prompt_chars':len(inp['system'])+len(inp['prompt']),
            'response':cached,'input_sha256':sha(ip),'response_sha256':sha(p.parent/'cache'/ip.name) if cached else None,
            'response_kind':'缓存的解析/规范化对象；不是供应商原始HTTP正文'})
    raw_requests=persist_raw(case_id,key,'requests.json',requests)
    transport_path=next((q for q in [p.parent/'transport/transport.json',p.parent.parent/'transport/transport.json'] if q.exists()),None)
    transport_records=read_json(transport_path) if transport_path else None
    images={}
    import fitz
    pdf=fitz.open(original)
    for page in doc['pages']:
        n=page['page'];target=ASSETS/'pages'/case_id/key/f'p{n}.png';target.parent.mkdir(parents=True,exist_ok=True)
        pg=pdf[n-1];pix=pg.get_pixmap(matrix=fitz.Matrix(1800/max(pg.rect.width,pg.rect.height),1800/max(pg.rect.width,pg.rect.height)),alpha=False)
        if not target.exists():pix.save(target)
        images[str(n)]={'url':target.relative_to(OUT).as_posix(),'original_physical_page':page_map.get(n,n),'run_page':n,
                        'width':pix.width,'height':pix.height}
    pdf.close()
    counts={k:dict(Counter(j.get('status','unknown') for j in doc.get(k,[]))) for k in ['discovery_batches','attribution_batches','synthesis_jobs','review_jobs','recovery_jobs']}
    stage_usage={}
    for receipt in run.get('receipts',[]):
        k=receipt.get('stage','unknown');u=stage_usage.setdefault(k,{'records':0,'cache_hits':0,'usage_tokens':0})
        u['records']+=1;u['cache_hits']+=bool(receipt.get('cache_hit'))
        if not receipt.get('cache_hit') and not receipt.get('replayed_completed_request') and not receipt.get('reused_completed_response'):
            u['usage_tokens']+=(receipt.get('usage') or {}).get('total_tokens',0)
    return {'id':key,'label':label,'path':str(p.relative_to(ROOT)),'run_sha256':sha(p),'run':run,'doc':doc,'requests':requests,
        'images':images,'raw_url':raw_url,'requests_url':raw_requests,'source_pdf_url':original.as_uri(),
        'page_map':page_map,'stage_counts':counts,'stage_usage':stage_usage,
        'transport_records':transport_records,'transport_path':str(transport_path.relative_to(ROOT)) if transport_path else None,
        'source_url_note':'完整输入PDF可打开；识别仅覆盖本次selected_pages，不把未选页视为已测试。'}

def collect_cases():
    protocol=read_json(OPEN/'protocol.json')
    allowed_ids={'pdf_01015fc12c9f8a74','pdf_113cb4d072120fd7','pdf_48c5da9cf568db6a','pdf_add4ffe1054112ea'}
    descriptors={d['id']:d for d in protocol['documents'] if d.get('suite')=='fixed_core_100' and d['id'] in allowed_ids}
    judgments=read_json(OPEN/'baseline_development_judgments.json')
    cases=[]
    definitions=[
      ('G359','pdf_add4ffe1054112ea','施工图：图纸尺寸漏提、表格量义和主体待审',[
        ('v3.8 严格缓存回归（0新API）','outputs/20261101_知联目录容量修复/after/G359/knowledge/run.json',{1:4,2:5,3:7,4:10}),
        ('v3.3 改前真实调用','outputs/20261101_知联修复回归/before/G359/knowledge/run.json',{1:4,2:5,3:7,4:10})]),
      ('G209-report','pdf_48c5da9cf568db6a','勘察报告：255已入正确对象，重组仍漏返',[
        ('v3.2 历史真实调用','outputs/20261031_固定回归与隐藏盲验/baseline/pdf_48c5da9cf568db6a/knowledge/run.json',{})]),
      ('paper','pdf_01015fc12c9f8a74','论文：跨行叙述、公式字形与参数证据',[
        ('v3.2 历史真实调用','outputs/20261031_固定回归与隐藏盲验/baseline/pdf_01015fc12c9f8a74/knowledge/run.json',{})]),
      ('guide','pdf_113cb4d072120fd7','指南：环境失败与恢复后跨行门控',[
        ('v3.2 环境恢复后首轮真实调用','outputs/20261031_固定回归与隐藏盲验/baseline/pdf_113cb4d072120fd7/knowledge_environment_recovery/run.json',{}),
        ('v3.2 本地准备失败（0 API，不当作模型差）','outputs/20261031_固定回归与隐藏盲验/baseline/pdf_113cb4d072120fd7/knowledge/run.json',{})])]
    for key,identifier,title,variants in definitions:
        descriptor=descriptors[identifier];assert descriptor['sha256'] not in DENIED_HASHES
        item={'id':key,'title':title,'source_name':Path(descriptor['source']).name,'original_sha256':descriptor['sha256'],
              'original_pages':descriptor['source_pages'],'tested_original_pages':descriptor['pages'],
              'variants':[load_variant(key,*v) for v in variants],
              'judgments':[r for r in judgments if r['source_sha256']==descriptor['sha256']]}
        if key=='G359':
            item['latest_judgments']=read_json(ROOT/'outputs/20261101_知联目录容量修复/after/G359/prediction.json')
            item['points']=read_json(ROOT/'outputs/20261101_知联修复回归/G359_review_points.json')
            item['latest_review']=read_json(ROOT/'outputs/20261101_知联请求分批修复/G359_development_regression.json')
        if key=='G209-report':
            item['trace']=read_json(OPEN/'development_ownership_trace_255.json')
        cases.append(item)
    return cases

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    data={'generated_at':datetime.now().astimezone().isoformat(),'source':collect_source(),'cases':collect_cases(),
          'policy':{'new_API_calls':0,'hidden_cases_included':0,'open_only':True},'stages':STAGES}
    profiles={}
    for name in ['deepseek-knowledge','deepseek-knowledge-max']:
        profile=read_json(ROOT/f'config/models/{name}.json')
        profiles[name]={k:v for k,v in profile.items() if not k.startswith('api_key')}
    diagnostic=ROOT/'outputs/20261102_最大思考调试/summary.json'
    data['performance_policy']={'profiles':profiles,
        'selection_policy':'各Agent按任务和测试结果选择；简单任务验证后可降低，反复失败可尝试提高，不强制最高档。',
        'compatibility_defaults':['scripts/smoke_knowledge_selected.py','scripts/validate_knowledge_route.py'],
        'diagnostic':read_json(diagnostic) if diagnostic.is_file() else None,
        'diagnostic_url':'../20261102_最大思考调试/调试结论.html',
        'official_thinking_docs':'https://api-docs.deepseek.com/guides/thinking_mode/'}
    # Local value-matching check only, using the same preserved open evidence.
    # Does not run recognition, all-item gating, OCR, or a model client.
    sys.path.insert(0,str(ROOT/'src'))
    from slopekg.knowledge_evidence import value_supported
    guide=next(c for c in data['cases'] if c['id']=='guide')
    old=next(v for v in guide['variants'] if '环境恢复后' in v['label'])
    fact=next(f for f in old['doc']['facts'] if f['id']=='k_f3d6b0d70c80205b76748b')
    data['guide_local_check']={'value_supported':value_supported(fact['value'],fact['evidence'],{u['id']:u for u in old['doc']['units']}),
        'kind':'current local value-matching function only; not a new prediction or complete-fact acceptance',
        'historical_fact_status':fact['status'],'historical_run_sha256':old['run_sha256'],'new_model_requests':0}
    # Final deny-list checks over published data, including exported raw objects.
    encoded=json.dumps(data,ensure_ascii=False)
    assert not any(h in encoded for h in DENIED_HASHES)
    assert not re.search(r'sealed_holdout|[/\\]R0[123][/\\]',encoded,re.I)
    dump(OUT/'debug_data.json',data)
    text='window.KNOWLEDGE_DEBUG='+safe(data)+';'
    (ASSETS/'debug_data.js').write_text(text,encoding='utf-8')
    from knowledge_debug_layout import write_html
    write_html(OUT,data)
    dump(OUT/'build_receipt.json',{'generated_at':data['generated_at'],'snapshot_sha256':data['source']['snapshot_sha256'],
        'source_files':{k:v['sha256'] for k,v in data['source']['files'].items()},'read_json_files':sorted(set(READS)),
        'case_ids':[c['id'] for c in data['cases']],'hidden_cases_included':0,'external_API_requests':0,
        'current_code_changed_by_builder':False,'outputs':['知联流程详解.html','知联失败样例.html']})
    print(json.dumps({'output':str(OUT),'open_cases':len(data['cases']),'variants':sum(len(c['variants']) for c in data['cases']),
        'source_files':len(data['source']['files']),'new_API_requests':0,'hidden_cases':0},ensure_ascii=False))

if __name__=='__main__':main()
