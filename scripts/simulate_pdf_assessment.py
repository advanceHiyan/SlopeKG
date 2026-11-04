"""Isolated synthetic PDF -> existing Knowledge -> standard service experiment."""
from pathlib import Path
import argparse, json, hashlib, sys, re, html
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
OUT=ROOT/'outputs/20261031_模拟PDF国标流程'
WORK=ROOT/'.private/reviews/20261031_synthetic_pdf_runtime'
PDF=OUT/'隔离档案_虚构滑坡测试原件.pdf'
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def load(p):return json.loads(p.read_text('utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def prepare():
 from reportlab.pdfgen import canvas
 from reportlab.pdfbase import pdfmetrics
 from reportlab.pdfbase.cidfonts import UnicodeCIDFont
 OUT.mkdir(parents=True,exist_ok=True);WORK.mkdir(parents=True,exist_ok=True)
 # Freeze expectations before any PDF recognition; never pass this file to a model.
 expected={'A':{'development':'中等发育','people':120,'harm':'危害大','grade':'危险性大'},
           'B':{'development':'弱发育','people':100,'harm':'危害中等','grade':'危险性中等'},
           'C':{'development':None,'people':9,'harm':'危害小','grade':None}}
 save(OUT/'识别前锁定的预期.json',{'synthetic':True,'cases':expected,'table_refs':['表15','表17'],'model_receives_expectations':False})
 pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
 c=canvas.Canvas(str(PDF),pagesize=(595,842))
 for key,x in expected.items():
  lines=['虚构软件测试资料 - 隔离档案，不是工程报告',f'SIM-PDF-{key}滑坡现状调查记录',
    '本页只描述一个虚构滑坡。全部内容专用于流程测试。',
    '评估依据：GB/T 40112-2021；评估时点：2026-10-01。',
    '调查对象为整个滑坡体；本次采用天然工况，评价口径为险情。',
    f'受威胁人数：{x["people"]}人。',
    '地质环境：土质滑坡，滑体位于斜坡中部。',
    '诱发因素调查：降雨入渗。',
    '潜在经济损失：未统计，未知，不作为零。']
  lines.insert(5,('现场专业判断（按本国标形成的模拟已给结论）：发育程度为'+x['development']+'。') if x['development'] else '发育程度：尚未调查，未给出等级；缺少变形观察资料。')
  c.setFont('STSong-Light',18);c.drawString(42,785,lines[1]);c.setFont('STSong-Light',12)
  for i,line in enumerate([lines[0]]+lines[2:]):c.drawString(42,735-i*46,line)
  c.setFont('STSong-Light',10);c.drawString(42,45,'隔离标识：SIMULATED / DO NOT IMPORT AS REAL DATA');c.showPage()
 c.save();save(OUT/'预冻结清单.json',{'pdf_sha256':sha(PDF),'expectations_sha256':sha(OUT/'识别前锁定的预期.json'),'source_pdf':str(PDF.relative_to(ROOT)),'synthetic':True})
 print('prepared',PDF,flush=True)
def recognize():
 from slopekg.knowledge_pipeline import KnowledgePipeline
 from slopekg.knowledge_settings import KnowledgeSettings
 from slopekg.model_client import CompatibleVisionClient,ModelProfile
 profile=ModelProfile.load(ROOT/'config/models/deepseek-knowledge.json')
 assert profile.max_output_tokens==393216
 pipeline=KnowledgePipeline(CompatibleVisionClient(profile,ROOT),WORK/'knowledge',
   settings=KnowledgeSettings(max_calls=16,model_concurrency=2,review_mode='none',max_review_calls=0,record_model_inputs=True,max_transient_retries=0),
   root=ROOT,parsed_dir=WORK/'parsed',progress_callback=lambda p,s,m:print(p,s,m,flush=True))
 run=pipeline.run([PDF]);print(json.dumps(run['summary'],ensure_ascii=False),flush=True)
 save(OUT/'识别运行回执.json',{'summary':run['summary'],'receipts':run['receipts'],'model':run['model'],'synthetic':True})
def assess():
 from slopekg.config import PATHS
 from slopekg.standard_assessment import StandardAssessmentService
 from slopekg.standard_assessment.contract import CONTRACT_VERSION,enumeration,number,fact_record,identity
 run=load(WORK/'knowledge/run.json');expected=load(OUT/'识别前锁定的预期.json')['cases']
 service=StandardAssessmentService(replace(PATHS,output_dir=WORK))
 results=[];bridge=[]
 for key in ['A','B','C']:
  facts=[];contexts=[];sources=[]
  scope={'subject':{'source_object_ref':'SIM-PDF-'+key},'time':{'kind':'instant','raw':'2026-10-01','start':'2026-10-01','end':'2026-10-01'},'scenario':{'code':'natural','raw':'天然工况','basis_refs':[]}}
  for doc in run['documents']:
   names={e['id']:e['name'] for e in doc['entities']}
   for f in doc['facts']:
    owner=str(f.get('object',''))+' '+names.get(f.get('entity_id'),'')
    if not re.search(r'SIM[-－ ]?PDF[-－ ]?'+key,owner,re.I):continue
    field=f.get('field','');value=str(f.get('value',''));quotes=''.join(e.get('quote','') for e in f.get('evidence',[]))
    if f.get('status')=='rejected' or not quotes:continue
    variable=None;v=None
    if '发育' in field and value in ['强发育','中等发育','弱发育'] and value in quotes:
     variable='development';v=enumeration(variable,value)
    elif ('人数' in field or '人口' in field) and '威胁' in field:
     if re.fullmatch(r'\s*\d+(?:\.0+)?\s*(?:人)?\s*',value):
      n=int(float(re.sub('人','',value).strip()))
      if str(n) in quotes:variable='threatened_people';v=number(n,'person')
    if not variable:continue
    src={'record_ref_id':identity('simsource_',f),'source_path':str(PDF.relative_to(ROOT)),'snapshot_sha256':sha(PDF),'collection':'knowledge.documents.facts','record_id':f['id'],'original_record':f}
    ctx={'context_id':identity('simcontext_',f['evidence']),'source_path':str(PDF.relative_to(ROOT)),'source_sha256':sha(PDF),
         'spans':[{'quote':e['quote'],'file_page_1_based':e.get('page')} for e in f['evidence']],
         'table_context':{},'synthetic':True}
    typed=fact_record(variable,v,scope,source_refs=[src['record_ref_id']],evidence_refs=[ctx['context_id']],origin='knowledge',
      semantic='reported_judgment' if variable=='development' else 'observation',role='observation',support='literal_matched',source_standard='GB/T 40112-2021')
    typed['synthetic']=True
    facts.append(typed);sources.append(src);contexts.append(ctx)
    bridge.append({'case':key,'source_fact_id':f['id'],'field':field,'value':f['value'],'typed_variable':variable,'source_role':f.get('role'),
                   'test_role_policy':'模拟资料仅在隔离测试库解释为测试观察；未修改生产仿真角色门控。','quotes':quotes})
  case={'case_id':'SIM-PDF-'+key,'target_ref':'SIM-PDF-'+key,'contract_version':CONTRACT_VERSION,'scope':scope,
        'resolution_version':1,'fact_ids':[f['fact_id'] for f in facts],'context_ids':[c['context_id'] for c in contexts],
        'source_hashes':{str(PDF.relative_to(ROOT)):sha(PDF)},'source_coverage':'SYNTHETIC_PDF_TEST_ONLY','hazard_type_supported':True,'selections':{},'synthetic':True}
  with service.store.transaction() as db:
   for table,rows in [('source_record',sources),('evidence_context',contexts),('fact_assertion',facts),('assessment_case',[case])]:
    for row in rows:service.store.put(table,row,db)
  req={'contract_version':CONTRACT_VERSION,'case_id':case['case_id'],'expected_resolution_version':1}
  r=service.evaluate_assessment(req)
  # Check recognized inputs independently; a missing-data case must not pass by accident.
  actual={f['variable_code']:f['value'] for f in facts}
  assert any(f['variable_code']=='threatened_people' for f in facts),(key,'people missing')
  assert service.store.get('fact_assertion',r['resolved_variables']['threatened_people']['selected_fact_id'])['value']['value']==expected[key]['people']
  if expected[key]['development']:assert any(f['variable_code']=='development' for f in facts),(key,'development missing')
  assert r['partials']['table15']['harm']==expected[key]['harm'],(key,r)
  assert r['table17_result']['grade']==expected[key]['grade'],(key,r)
  assert r['review_status']=='pending'
  note=service.review_assessment({**req,'decision':'note','target_id':r['result_id'],'actor_kind':'assistant','reviewer_ref':'Codex/虚构软件流程核对',
    'reason':'仅核对模拟输入与预冻结预期一致，非实际工程专业签认。'})
  r2=service.evaluate_assessment(dict(req,expected_resolution_version=2))
  reopened=StandardAssessmentService(replace(PATHS,output_dir=WORK)).get_case(case['case_id'])
  assert any(not x['is_current'] for x in reopened['results'])
  assert not note['review_decision']['professional_acceptance']
  results.append({'case':key,'recognized':actual,'expected':expected[key],'result':r2,'database_readback':True,'old_version_preserved':True,'professional_acceptance':False})
 save(OUT/'隔离归档_字段适配回执.json',{'synthetic':True,'bridge_kind':'test_only_generic_field_mapping_not_production_auto_import','rows':bridge})
 save(OUT/'隔离归档_评估运行回执.json',{'synthetic':True,'results':results,'database_counts':{k:len(v) for k,v in service.store.export().items()}})
 print('all three frozen cases passed',flush=True)
def report():
 data=load(OUT/'隔离归档_评估运行回执.json');rec=load(OUT/'识别运行回执.json');e=lambda x:html.escape(str(x))
 body='<h1>虚构PDF到国标评估：隔离流程验证</h1><p class="warn">所有案例均为编造的软件测试资料，不是实际工程资料，不是风险识别准确率验收。</p>'
 body+='<p><a href="隔离档案_虚构滑坡测试原件.pdf">查看模拟原始PDF（隔离档案）</a></p><h2>先定输入和预期，再运行</h2><table><tr><th>案例</th><th>PDF中的输入</th><th>预先期待</th><th>实际结果</th></tr>'
 for x in data['results']:
  body+='<tr><td>'+e(x['case'])+'</td><td>'+e(x['expected']['development'] or '未调查发育程度')+'；受威胁'+str(x['expected']['people'])+'人</td><td>'+e(x['expected']['harm'])+' → '+e(x['expected']['grade'] or '停止定级')+'</td><td>'+e(x['result']['table17_result']['grade'] or '缺发育依据，等级为空')+'；待人工复核</td></tr>'
 body+='</table><h2>实际执行的数据流</h2><ol><li>生成3页带隔离标记的PDF，并锁定预期及文件哈希。</li><li>调用项目现有知联Knowledge识别路线，完成PDF文字解析、对象发现、归属和事实识别；答案文件未传给模型。</li><li>用测试专用字段适配器，把识别得到的发育程度与受威胁人数映射成国标类型化事实，并核对原句、对象和单位。此适配桥接不是现有生产自动导入已完善的证明。</li><li>写入独立SQLite事实库；由项目StandardAssessmentService执行口径推断、表15与表17。</li><li>保存代理复核提示，生成第2版结果；重新打开数据库验证旧版保留。没有伪造真实人工专业签认。</li><li>移除隔离运行库与识别缓存，保留本页、原始PDF和带隔离标签的审计档案；用正式数据文件哈希核对没有污染。</li></ol>'
 body+='<p>现有识别路线回执：'+e(rec['summary'])+'</p><p><a href="识别前锁定的预期.json">预先冻结的测试答案</a> · <a href="隔离归档_字段适配回执.json">识别到类型化字段</a> · <a href="隔离归档_评估运行回执.json">数据库评估与版本回执</a> · <a href="清理核验.json">清理和生产数据不变核验</a></p>'
 (OUT/'流程报告.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>body{max-width:1100px;margin:auto;padding:32px;font:16px/1.8 sans-serif;color:#203548}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:12px;text-align:left}.warn{padding:18px;background:#fff0cc}</style>'+body+'</html>',encoding='utf-8')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['prepare','recognize','assess','report']);a=p.parse_args();globals()[a.phase]()
