"""Frozen repair evidence and staged regression findings; no model calls."""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/20261031_三路修复复测'
read=lambda name:json.loads((OUT/name).read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    protocol,frozen,execution=read('protocol.json'),read('predictions_frozen.json'),read('execution.json')
    for route,info in frozen['predictions'].items():assert sha(ROOT/info['path'])==info['sha256'],route
    for name,h in protocol['answer_commitments'].items():assert sha(ROOT/name)==h
    core=read('core_cases_after_freeze.json');assert len(core)==4
    local=read('local_repairs_validation.json')
    runs={r:read(r+'/run.json') for r in ['basic','knowledge','visual']}
    knowledge,visual=runs['knowledge']['documents'][0],runs['visual']['documents'][0]
    visual_facts={f['id']:f for f in visual['facts']}
    selected={
        'pt_04630cd783e4cef6e452':'33c4bda709849dcda2cbf8f3',
        'pt_9345fa91649a7c536902':'3cf607177d6cdd95b16d2883',
        'pt_407170e4e538481349d5':'679018aa42e6931b0a92573b',
        'pt_0c0854594025b81178ab':'e65b570670cb9995008bdbf7',
    }
    complete='pt_04630cd783e4cef6e452'
    judgments=[]
    coverage=[]
    wire_inputs={p.name:json.loads(p.read_text(encoding='utf-8'))['prompt']
                 for p in (OUT/'knowledge/transport').glob('input-*.json')}
    for point in core:
        fact=visual_facts[selected[point['id']]]
        pattern=r'(?<![\d.])'+re.escape(str(fact['value']))+r'(?![\d.])'
        matching_units=[u for u in knowledge['units'] if u['page'] in point['pages'] and re.search(pattern,u['text'])]
        matching_inputs=sorted(name for name,prompt in wire_inputs.items()
                               if any(u['id'] in prompt for u in matching_units))
        coverage.append({'point_id':point['id'],'pages':point['pages'],
            'numeric_label':fact['value'],'matching_unit_ids':[u['id'] for u in matching_units],
            'actual_model_input_files':matching_inputs,
            'diagnosis':'numeric label supplied but not semantically reconstructed' if matching_inputs else 'target label absent from prepared source input'})
        judgments.append({'point_id':point['id'],'pages':point['pages'],'carrier':point['carrier'],
            'expected':point['expected'],'reviewer':'Codex after freeze and original-page image check; not expert certification',
            'basic':{'value_captured':False,'semantic_complete':False,'status':'missing'},
            'knowledge':{'value_captured':False,'semantic_complete':False,'status':'missing',
                         'numeric_label_present_in_input':bool(matching_inputs),
                         'note':coverage[-1]['diagnosis']+'; both scanned pages use partial title_block OCR rather than full-page content.'},
            'visual':{'value_captured':True,'semantic_complete':point['id']==complete,
                      'status':'complete_candidate' if point['id']==complete else 'partial_value_without_measurement_semantics',
                      'fact_id':fact['id'],'candidate':{k:fact.get(k) for k in ['object','field','value','unit','scope','role','status','reason']},
                      'admitted_by_pipeline':fact['status']=='verified_model',
                      'reason':('Original image confirms H=350 row, graded stone 1.07, literal column unit m³; quantity group says per linear meter. Unit was not rewritten to m³/m.'
                                if point['id']==complete else 'Only the numerical dimension label is represented. Its measurement endpoints, body/total distinction or internal horizontal direction are not expressed.')}})
        for evidence in point['evidence']:
            source=(ROOT/evidence['render']).resolve()
            source.relative_to((ROOT/'.private/evaluation/evidence').resolve())
            assert sha(source)==evidence['render_sha256']
            shutil.copy2(source,OUT/f'source_p{evidence["pdf_physical_page"]:03d}.png')
    attempts=[(route,row) for route in ['knowledge','visual'] for row in read(route+'/transport/transport.json')]
    assert len(attempts)==28
    assert all((r.get('receipt') or {}).get('requested_max_output_tokens')==393216 for _,r in attempts)
    assert all(r['status']!='started' for _,r in attempts)
    truncated=[(route,r['ordinal']) for route,r in attempts if r['receipt'].get('finish_reason')=='length']
    invalid=[{'route':route,**r} for route,r in attempts if r['status']!='finished']
    large=[{'route':route,'ordinal':r['ordinal'],'completion_tokens':r['receipt']['usage'].get('completion_tokens',0),
            'provider_response_id':r['receipt'].get('id')}
           for route,r in attempts if r['status']=='finished' and r['receipt']['usage'].get('completion_tokens',0)>16384]
    stage_usage=defaultdict(lambda:{'attempts':0,'total_tokens':0})
    for route in ['knowledge','visual']:
        names={r.get('id'):r['stage'] for r in runs[route]['receipts'] if not r.get('cache_hit')}
        for r in read(route+'/transport/transport.json'):
            receipt=r.get('receipt') or {};stage=names.get(receipt.get('id'),'unknown')
            stage_usage[route+'/'+stage]['attempts']+=1
            stage_usage[route+'/'+stage]['total_tokens']+=receipt.get('usage',{}).get('total_tokens',0)
    invalid_box=lambda b: bool(b) and not (0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1)
    geometry={'knowledge_units':len(knowledge['units']),
        'invalid_unit_boxes':sum(invalid_box(u.get('bbox')) for u in knowledge['units']),
        'unit_geometry_issues':sum(bool(u.get('geometry_issue')) for u in knowledge['units']),
        'invalid_fact_evidence_boxes':sum(any(invalid_box(e.get('bbox')) for e in f['evidence']) for f in knowledge['facts'])}
    assert geometry['invalid_unit_boxes']==0 and geometry['invalid_fact_evidence_boxes']==0
    basic=read('basic/run.json')
    totals={k:sum(r['usage'][k] for r in execution['routes'].values()) for k in ['prompt_tokens','completion_tokens','total_tokens']}
    assert totals['total_tokens']==552680
    summary={'status':'three_code_repairs_validated; recognition_quality_not_accepted; stopped_no_more_API',
        'source_scope':{'source_sha256':protocol['source']['sha256'],'selected_pages':protocol['selected_pages'],'source_pages':21},
        'code_revision':protocol['signature']['code_revision'],'schema_version':runs['knowledge']['schema_version'],
        'actual_attempts':len(attempts),'usage_receipts':len(attempts),'usage':totals,
        'requested_max_output_tokens':dict(Counter(str(r['receipt']['requested_max_output_tokens']) for _,r in attempts)),
        'truncated_responses':truncated,'invalid_completed_responses':invalid,
        'successful_responses_above_previous_16k_limit':large,'stage_usage':dict(stage_usage),
        'fixed_core':{'exercised':4,'total':100,'unexecuted':96,'scorable':4,'expert_validated':False,
            'carriers':dict(Counter(r['carrier'] for r in core)),
            'complete_candidate_cases':{'basic':0,'knowledge':0,'visual':1},
            'partial_cases':{'basic':0,'knowledge':0,'visual':3},
            'value_capture_cases':{'basic':0,'knowledge':0,'visual':4},
            'pipeline_admitted_core_cases':{'basic':0,'knowledge':0,'visual':0},
            'precision':None,'recall':None,'overall_accuracy':None},
        'geometry':geometry,'local_structural_replay':local['composition_replay'],
        'basic':{'slopes':len(basic['extracted']['slopes']),'assertions':len(basic['extracted']['assertions']),
                 'decision':'Stop extending upper-layer free-text rules; retain parsing/table/OCR utilities.',
                 'ocr_tasks':[{'page':t['page'],'region':t['region'],'status':t['status'],'items':t.get('items')} for t in basic['ocr_tasks']]},
        'local_tests':{'before_paid_run':22,'adjacent_cache_and_transport':16,'unique_total':38,'provider_requests':0},
        'database':read('database_probe.json'),
        'scope_limitations':['This two-page sample differs from the prior four-page sample; no before/after accuracy or cost ratio is claimed.',
            'The structural replay of the identical failed batch is local-only and does not turn historical raw candidates into new model predictions.',
            'Known development documents; metadata-only core membership checked before prediction, answer values assessed after all prediction hashes frozen.',
            'A stop-finish invalid JSON response is not an output-limit failure. Its raw content was not archived; empty/array/malformed/missing content cannot be distinguished retrospectively.',
            'No production database migration, expert sign-off or risk grade was performed.']}
    (OUT/'repair_retest_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'core_case_judgments.json').write_text(json.dumps(judgments,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'input_coverage_diagnostic.json').write_text(json.dumps({
        'status':'post-freeze diagnostic only; not a new prediction or production rule',
        'source_inventory':knowledge['source_inventory'],
        'ocr_tasks':summary['basic']['ocr_tasks'],'core_numeric_input_coverage':coverage},ensure_ascii=False,indent=2),encoding='utf-8')
    ledger_note=''
    if (OUT/'ledger_receipt.json').exists():
        ledger=read('ledger_receipt.json');ledger_note=escape(json.dumps(ledger.get('ids',{}),ensure_ascii=False))
    costs=[]
    for route,label in [('basic','基读 Basic'),('knowledge','知联 Knowledge'),('visual','视读 Visual')]:
        r=execution['routes'].get(route,{})
        count=len(basic['extracted']['assertions']) if route=='basic' else len(runs[route]['documents'][0]['facts'])
        status=('结构化断言0，OCR只覆盖标题区' if route=='basic' else
                '33候选：19字面对应、14待审；本批无对象作业整批丢弃' if route=='knowledge' else
                '161候选：13模型核验、148待审；两页审核未完成')
        costs.append(f'<tr><th>{label}</th><td>{r.get("actual_transport_entries",0)}</td><td>{r.get("usage",{}).get("total_tokens",0):,}</td><td>{count}</td><td>{status}</td></tr>')
    core_rows=[]
    for j in judgments:
        e=j['expected'];f=j['visual']['candidate'];label='候选语义完整，仍待审' if j['visual']['semantic_complete'] else '读到数值，量义不完整'
        core_rows.append(f'<tr><td><code>{j["point_id"]}</code><small>物理页 {j["pages"]}</small>{escape(str(e["field"]))}<br>{escape(str(e["value"]))} {escape(str(e.get("unit") or ""))}</td><td>未产出</td><td>未产出</td><td><strong>{label}</strong><br>{escape(str(f["value"]))} {escape(str(f["unit"]))}<small>字段：{escape(f["field"])}<br>范围：{escape(f["scope"])}</small></td></tr>')
    stage_rows=''.join(f'<tr><td>{escape(name)}</td><td>{value["attempts"]}</td><td>{value["total_tokens"]:,}</td></tr>' for name,value in stage_usage.items())
    localtime=lambda v:datetime.fromisoformat(v).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    opened=read('evaluation_opened.json')
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>通用修复与两页核心回归</title><style>body{{margin:0;background:#f2f5f8;color:#183047;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1160px;margin:auto;padding:30px 22px 65px}}h1{{font-size:29px;line-height:1.4}}h2{{font-size:22px;margin-top:34px}}section,.card{{background:white;padding:21px;border-radius:12px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}.card b{{font-size:28px;display:block}}.notice{{border-left:5px solid #b45b31;background:#fff0e7;padding:18px 22px;border-radius:8px}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:13px;text-align:left;vertical-align:top;border-bottom:1px solid #dce5ed;min-width:75px}}th{{background:#e9f0f5}}p,li,code,small{{overflow-wrap:anywhere}}small{{display:block;color:#53697b;line-height:1.55}}code{{font-size:12px}}a{{color:#146794}}img{{max-width:100%;height:auto}}details{{margin:15px 0}}@media(max-width:650px){{main{{padding:20px 12px}}h1{{font-size:24px}}.cards{{grid-template-columns:repeat(2,1fr)}}td{{min-width:150px}}}}</style>
<main><small>2026-10-31 · 知联 knowledge-v3.1 · 当前固定回归集分阶段检查</small><h1>三项代码问题已修，识别效果仍未达标</h1>
<p>已修复按对象分批验收、旋转坐标归一化和各调用入口的低输出上限。实发请求均为max_tokens=393216，截断为0。新的实测问题在OCR覆盖、尺寸语义重组与核验流程，不能继续归咎于输出上限。</p>
<div class="notice"><strong>本轮固定核心覆盖4/100：视读1项候选语义完整、3项仅数值对应；基读和知联4项均未完整产出。</strong><br>4个目标数值都出现在视读候选中，但其中3个仍只叫“尺寸标注”，没有表达主体高度、含基础总高、内部水平尺寸的区别。4项均未成为自动采用的结果；这不是整体准确率、召回率或专业验收。已停止API，不扩样。</div>
<div class="cards"><div class="card"><b>2页 / 4项</b>同一原件第8、9物理页；96项核心未执行</div><div class="card"><b>28次</b>知联4、视读24；28条用量回执</div><div class="card"><b>0截断</b>出站上限全部393,216</div><div class="card"><b>552,680</b>本轮返回tokens，非账单金额</div></div>
<h2>修复到了哪里</h2><section><p><strong>知联批次容错：</strong>按请求对象ID独立验收。多余对象隔离，缺失/重复/结构错误只影响对应对象；单条事实结构错误也不会拖掉其他事实。对同一历史失败回复的本地重放保留6个目标对象、65条原始候选，其中挡墙13条，隔离5个非本批对象。它们进入后续逐条校验，未伪称为新模型预测或专家事实。真实新两页运行没有发生对象批次整组丢弃。</p>
<p><strong>坐标：</strong>原生文字框先按页面旋转矩阵映射到显示页面，再归一化；OCR从其PDF坐标按相同页面信息转换。旧原生缓存按几何版本刷新，匹配原件哈希的OCR继续复用；非法框阻止source_aligned。匿名0/90/180/270度PDF均有检查。同一旧4页的158个来源单元：25个越界框降为0，复用90条OCR。新两页来源和事实引用越界也为0。旧生产数据未做全量迁移。</p>
<p><strong>最大输出：</strong>公共ModelProfile统一核定上限，调用方即便传4096或16384也无法把已配置模型降帽；风险接口4096和测试脚本16384覆盖已去掉。DeepSeek为393216，Qwen3.8-Max为131072；Kimi K3先用官方计数接口计算输入，再取1048576减输入token数。Qwen/Kimi仅做了离线参数验证；本轮真实调用只用现有DeepSeek。出处：<a href="https://api-docs.deepseek.com/api/create-chat-completion/">DeepSeek</a>、<a href="https://help.aliyun.com/zh/model-studio/qwen3-8-max">Qwen</a>、<a href="https://www.kimi.com/help/kimi-api/api-troubleshooting">Kimi</a>。</p>
<p><strong>提示词：</strong>只替换一句通用说明：仅返回subjects中的对象，registry只供关系引用。没有加入文件名、桩号、某个尺寸或答案清单；主要修复位于容错、坐标与传输代码。合计38项定向本地检查通过，未跑全套数据或全库模型识别。</p></section>
<h2>三路实测结果</h2><div class="table"><table><thead><tr><th>路线</th><th>实际API</th><th>tokens</th><th>结构化断言/候选</th><th>状态</th></tr></thead><tbody>{''.join(costs)}</tbody></table></div>
<p>本轮总耗时约{execution['elapsed_seconds']:.0f}秒，视读约825秒。视读出现1条finish_reason=stop但不符合JSON契约的核验响应：completion_tokens=24762，出站上限393216，有完整用量回执。这不是length截断。当前客户端没有保留其原始content，无法进一步断言是空内容、非法JSON还是顶层类型异常；已记录待补证，没有自动重发。所有在途请求已结束。</p>
<p>有{len(large)}条成功解析的回复超过旧16K上限，最长{max(r['completion_tokens'] for r in large):,}个completion tokens，仍正常结束。总请求数和输出上限分别管理；本轮32次上限实际使用28次，重试0。连同上轮21次，本次两轮合计49次、760,080 tokens；不与历史658次研发累计混合。</p>
<h2>固定4项逐题核对</h2><p>评测侧只检查成员元数据；取页规则先按原件页数计算，未按答案位置挑页。三路全部预测冻结于{localtime(frozen['frozen_at'])}，首次答案评分读取于{localtime(opened['opened_at'])}（北京时间）。答案哈希及预测哈希均核对一致。资料本身是已知开发来源，本轮不是独立新资料验收，备用sealed_holdout未读取。</p>
<div class="table"><table id="core-cases"><thead><tr><th>当前核心检查点</th><th>基读</th><th>知联</th><th>视读</th></tr></thead><tbody>{''.join(core_rows)}</tbody></table></div>
<p><strong>1.07的计量：</strong>原件表头为“每延米数量”，级配碎石列印刷单位是m³。候选保留m³并在scope注明每延米，符合原表，没有把列单位擅改为m³/m。<strong>三个尺寸：</strong>模型review虽支持“某结构上的数字标注”，却未补全其具体测量含义；支持一个笼统命题不等于完成了参考任务。原始候选状态保持pending，没有因对答案而升格。</p>
<details><summary>查看原件第8页：三个数值对应的不同尺寸线</summary><a href="source_p008.png"><img src="source_p008.png" alt="原件第8物理页工程布置剖面图"></a></details>
<details><summary>查看原件第9页：H=350行、级配碎石1.07及原表单位</summary><a href="source_p009.png"><img src="source_p009.png" alt="原件第9物理页结构断面、尺寸数量表和说明"></a></details>
<h2>基读到底差在哪里</h2><section><p>本轮两页原生文字块为0，图像不具备pdfplumber原生表格，复用OCR任务均是title_block：第8页38条、第9页48条。裁剪也带入了部分图内数值和施工说明，但未覆盖全部图和表。<strong>第9页的1.07未进入知联输入，是输入覆盖问题；第8页的3.5、4.51、0.3已在实际模型输入里，却没重组成所问尺寸含义，是语义组织问题。</strong>不能把4项都归因于缺输入，也不能据此判定OCR引擎能力不足。知联另一个0.3来自泄水孔说明，与第8页所问墙内水平0.3不是同一事实，不能凑命中。<a href="input_coverage_diagnostic.json">逐项输入覆盖证据</a></p>
<p>基读上层依赖灾点清单、经佐证的编号章节及有限字段模板；清单/根对象发现还有勘察报告类别门槛，自由施工图说明没有通用对象发现链。这是能力边界，不能靠修一个正则变成通用识别。按用户要求，停止扩充基读自由文本上层规则，保留PDF/表格/OCR工具供知联复用。OCR裁剪覆盖不足是另一个可改的源准备问题，不能把它算成OCR引擎能力结论。</p></section>
<h2>剩余问题与调用去向</h2><div class="table"><table><thead><tr><th>阶段</th><th>请求数</th><th>tokens</th></tr></thead><tbody>{stage_rows}</tbody></table></div>
<p>视读的主要消耗来自逐区域细读、独立复读和核验。多层核验仍未把数字组装成明确量义，两页24次后还未完成审核；后续应优先补OCR正文覆盖、做跨区域对象/尺寸端点与方向重组、合并复核并增强非法回包诊断。保持通用，不把本页的几个数字写成识别规则。当前新问题已达到停止扩样的条件，不再增加调用次数。</p>
<h2>数据库与证据</h2><section><p>独立SQLite实际保存并重新读回33条知联reference候选，合同校验通过；规范对象绑定0、专业确认0、风险定级0。未运行风险模型或迁移生产数据库。视读导入接口尚未提供，本轮不虚称三路到正式风险已打通。</p>
<p><a href="protocol.json">冻结协议/完整提示词/配置</a> · <a href="execution.json">实际用量</a> · <a href="predictions_frozen.json">预测哈希</a> · <a href="evaluation_opened.json">阅卷时间与边界</a> · <a href="core_case_judgments.json">4项逐题判断</a> · <a href="repair_retest_summary.json">完整诊断摘要</a> · <a href="local_repairs_validation.json">零API结构重放与旧页坐标检查</a> · <a href="database_probe.json">数据库读回</a></p><small>冻结识别代码指纹：{protocol['signature']['code_revision']}<br>唯一待核清单仍在.private项目台账；此报告是本次不可变测试快照。{ledger_note}</small></section></main></html>'''
    (OUT/'通用修复与两页核心回归.html').write_text(html,encoding='utf-8')
    print(json.dumps({'report':str(OUT/'通用修复与两页核心回归.html'),'requests':len(attempts),
        'tokens':totals['total_tokens'],'truncations':len(truncated),'core_semantic_complete':summary['fixed_core']['complete_candidate_cases'],
        'visual_partial':3,'source_coordinate_invalid':geometry['invalid_unit_boxes'],'local_tests':38},ensure_ascii=False))


if __name__=='__main__':main()
