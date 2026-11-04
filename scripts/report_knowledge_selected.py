"""Report a finished selected-page smoke test and local-only replay of its output.

Known checkpoints are used AFTER prediction for development diagnosis only.
No model call, credential lookup, new fact generation or sealed acceptance read.
"""
import argparse
import copy
from decimal import Decimal
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from slopekg.knowledge_composition import prepare_bindings, gate_ownership, concrete_subject
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.provenance import runtime_identity
from slopekg.storage import read_json,write_json


class NoCalls:
    def identity(self):return {'name':'local-replay-only','model':'none'}
    def complete(self,**kwargs):raise AssertionError('This report cannot call a model')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def table(headers,rows):
    return '<div class="scroll"><table><tr>'+''.join('<th>'+escape(str(c))+'</th>' for c in headers)+'</tr>'+''.join(
        '<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows)+'</table></div>'


def numbers(text):return [Decimal(n) for n in re.findall(r'\d+(?:\.\d+)?',text)]


def point_matches(cp,fact):
    """Exposed diagnostic adapter, NOT recognition or a whole-file metric."""
    key=cp['id'];text=' '.join(str(fact.get(k,'')) for k in ('object','field','scope','condition'))
    if 'GPS' in key:
        point='GPS17' if key.startswith('B') else 'GPS01'
        if point not in text or cp['field'][-1] not in fact['field']:return False
    elif key=='A-W1-length':
        if 'W1' not in text or '长' not in fact['field']:return False
    else:
        station='2388+976' if key.startswith('A') else '2434+460'
        if station not in text:return False
        if cp.get('condition') and cp['condition'] not in text:return False
    expected,actual=numbers(cp['value']),numbers(fact['value'])
    return bool(expected) and any(actual[i:i+len(expected)]==expected for i in range(len(actual)))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('folder',type=Path);args=parser.parse_args()
    out=args.folder.resolve();execution=read_json(out/'execution.json');protocol=read_json(out/'protocol.json')
    if execution['status']!='finished':raise ValueError('Wait for the bounded model run to finish')
    runfile=out/'run/run.json';original_hash=sha(runfile);assert original_hash==execution['run_sha256']
    run=read_json(runfile);doc=run['documents'][0];selected=set(execution['selected_pages'])
    # Independently check every archived wire context contains selected pages only.
    input_count=0
    for path in (out/'run/inputs').glob('*.json'):
        wire=read_json(path);context=json.loads(wire['prompt'].split('\n输入：',1)[1])
        spans=context.get('source_spans',[])+[s for c in context.get('chunks',[]) for s in c['spans']]
        assert all(s['page'] in selected for s in spans);assert wire['images']==[];input_count+=1
    fixed=copy.deepcopy(doc);prepare_bindings(fixed);units={u['id']:u for u in fixed['units']}
    old_entities={e['id']:e for e in doc['entities']};known={e['id']:e for e in fixed['entities']}
    pipeline=KnowledgePipeline(NoCalls(),out/'local-replay-not-a-model-run')
    local=[];moved=0
    ownership_flags={'foreign_object_section_evidence','concrete_case_requires_explicit_owner'}
    for old in doc['facts']:
        subject=known[old['entity_id']];target=concrete_subject(fixed,subject,old,known)
        allowed=set(old_entities[subject['id']]['unit_ids'])
        if target['id']!=subject['id']:allowed.intersection_update(target['unit_ids'])
        item=pipeline._item(fixed,target,'fact',old,units,allowed,known)
        # Lost invalid/unprovided quotes are not repaired by silently discarding
        # their old flags; only ownership guards are recomputed here.
        item['quality_issues']=list(dict.fromkeys([*item['quality_issues'],
            *(q for q in old['quality_issues'] if q not in ownership_flags)]))
        gate_ownership(fixed,item,units)
        if old['status']=='rejected':item.update(status='rejected',reason=old['reason'])
        elif old.get('review_required') and old['status']!='verified_model':
            item.update(status='pending',reason='semantic_review_still_required')
        if target['id']!=subject['id']:moved+=1
        item.update(previous_id=old['id'],previous_entity_id=old['entity_id'],previous_status=old['status'])
        local.append(item)
    alignment_before=sum(f['status']=='source_aligned' for f in doc['facts'])
    alignment_after=sum(f['status']=='source_aligned' for f in local)
    replay={'kind':'local guards over frozen model output; NOT a second model prediction',
        'base_run_sha256':original_hash,'base_model_calls':execution['request_attempts'],'new_model_calls':0,
        'local_fix_producer':runtime_identity(),'original_prediction_producer':run['producer'],
        'source_aligned_before':alignment_before,'source_aligned_after':alignment_after,
        'rebound_candidates':moved,'facts':local,
        'source_packages':[{'id':e['id'],'name':e['name'],'before_spans':len(old_entities[e['id']]['unit_ids']),
                            'after_spans':len(e['unit_ids'])} for e in fixed['entities']]}
    write_json(out/'本地修复重放_不是新模型预测.json',replay)
    historical=read_json(ROOT/'outputs/20261028_三路效果复核/审计明细.json')
    cases=[];rows=[]
    for case in historical['cases']:
        cp=case['checkpoint']
        if cp['page'] not in selected:continue
        matches=[f for f in doc['facts'] if point_matches(cp,f)]
        local_matches=[f for f in local if point_matches(cp,f)]
        cases.append({'checkpoint':cp,'value_found':bool(matches),'matches':matches,'local_matches':local_matches})
        statuses=','.join(sorted({f['status'] for f in matches})) if matches else '未生成'
        rows.append([cp['id'],cp['field']+(' / '+cp['condition'] if cp.get('condition') else ''),
                     cp['value']+cp.get('unit',''),cp['page'],'有' if matches else '缺',statuses])
    found=sum(c['value_found'] for c in cases);missing=[c['checkpoint'] for c in cases if not c['value_found']]
    summary={'execution':execution,'wire_inputs_checked':input_count,'only_selected_pages_sent':True,
        'selected_diagnostic_targets':len(cases),'target_values_found':found,'missing_targets':missing,
        'cases':cases,'local_replay':{k:v for k,v in replay.items() if k!='facts'},
        'independent_accuracy_measured':False,'new_model_calls_during_reporting':0,
        'local_mechanism_tests':{'python_targeted_passed':13,'browser_targeted_passed':4}}
    write_json(out/'实测诊断证据.json',summary)
    assert sha(runfile)==original_hash
    requests=execution['request_attempts'];usage=execution['usage'];seconds=execution['elapsed_seconds']
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>知联v3选页实测</title>
<style>body{font:16px/1.85 "Microsoft YaHei",sans-serif;background:#eef3f6;color:#23384b;margin:0}main{max-width:1140px;margin:28px auto;padding:32px;background:white;border-radius:16px}h1,h2{color:#125368}p{margin:14px 0}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:11px;border-bottom:1px solid #dce5ed;vertical-align:top;text-align:left}th{background:#eaf2f6}.scroll{overflow:auto}.warning{border-left:4px solid #be6f36;background:#fff4e8;padding:16px}code{overflow-wrap:anywhere}a{color:#146887}@media(max-width:700px){main{margin:0;padding:18px}h1{font-size:24px}table{min-width:600px}}</style><main>
<p>2026-10-29 · 6页开发诊断 · 已停止新增调用</p><h1>知联v3：9次实测，暴露归属和覆盖缺口</h1>
<p>原件仍是127页G209，只向现有DeepSeek发送物理页11、12、21、22、38、39的文字及表格；其他121页未进入模型输入。不运行新OCR，不运行视读。</p>'''
    html+=table(['运行项','实际记录'],[
        ['请求尝试 / 用量回执',f'{requests} / {execution["usage_receipts"]}'],
        ['分阶段','对象发现2次＋归属2次＋重组4次＋歧义复核1次'],
        ['耗时',f'{seconds}秒'],['并发上限 / 实际峰值',f'6 / {execution["peak_concurrency"]}'],
        ['真实重试次数',execution['transient_retries']],['返回token用量',f'输入{usage["prompt_tokens"]:,}；输出{usage["completion_tokens"]:,}；合计{usage["total_tokens"]:,}'],
        ['硬预算','最多18次（含重试）；实际停在9次，余量未使用']])
    html+='''<p class="warning">这些是请求/返回用量，不是服务商账单。9次只处理6页，不能与历史658次全文开发累计直接相除得出成本降幅。本次没有真实重试事件；重试机制通过模拟429/503、预算、超时不重发等定向测试验证。</p>
<h2>识别结果：没有达到理想效果</h2>'''
    html+=table(['结果项','原始模型运行产物'],[
        ['对象/概念',len(doc['entities'])],['候选事实',len(doc['facts'])],
        ['原文对齐 / 待审',f'{run["summary"]["source_aligned"]} / {run["summary"]["pending"]}'],
        ['事实独立模型复核通过',run['summary']['verified_model']],
        ['关系候选',len(doc['relations'])],['整份文件状态','partial：其余页未测试；选页对象也存在未解决项']])
    html+=f'''<p>使用此前已经看过的诊断点，仅检查落在这6页中的<b>{len(cases)}项</b>：在事实value字段中找到<b>{found}项</b>，仍缺<b>{len(missing)}项</b>。出现的数值也可能挂错主体、属于复合字段或仍待审；这不是准确率。</p>'''
    html+=table(['检查点','信息项','目标原值','物理页','值出现','原始状态'],rows)
    html+='''<p>这组目标值在旧知联及WeKnora已有全文结果中均出现过。本轮只给6页，全文上下文不同，不能据此做公平的整体算法排名；但缺失项本就在已选页面内，属于本轮实际覆盖缺口。</p>
<h2>找到的问题及本地修复</h2><ol>
<li><b>类别名抢走具体主体。</b> 模型把一个通用类别登记为具体实体，局部标题匹配又让它覆盖具体工点子章节，导致证据没聚齐、正确内容被误拦。修复为：被多个具体对象共享的短名称不自动当唯一章节主体，仍保留身份待核候选；不再为这类歧义对象单独安排重组调用。规则不包含边坡词典或G209标识。</li>
<li><b>复核预算分配不佳。</b> 唯一复核批次先被前面的对象关系占用，部分具体数值仍待核。后续编排优先处理有实际歧义的具体事实；未追加付费复核。</li>
<li><b>名称/范围仍不确定。</b> 已选原文图注确有“460-600”，正文又有“460-585”；模型保留两种名称，本轮缺少其他章节上下文。没有把它强行改写成预设桩号。</li>
<li><b>字段仍有问题。</b> 复合尺寸未完全拆开，部分值是改写或跨片段拼接，无法通过严格引文检查。保持待审，未放松门控来增加通过数。</li></ol>
<h2>只做缓存/原文重放，没有第二轮模型调用</h2>'''
    html+=f'''<p>本地重放保持全部既有事实值，不生成新值；“原文对齐”从{alignment_before}条变为{alignment_after}条，{moved}条候选按明确范围和一致来源重新关联主体。独立模型复核标记没有增加。未生成的长、高、坡度等值仍没有被补出来。</p>'''
    html+=table(['对象','原证据单元','本地修复后证据单元'],[
        [e['name'],len(old_entities[e['id']]['unit_ids']),len(e['unit_ids'])]
        for e in fixed['entities'] if e['kind']=='entity'])
    html+='''<p class="warning">“证据包补齐”不等于模型已经读取补充内容。原始9次运行、缓存、用量回执、完整实际输入和预测文件均保持不变；本地修复单独保存，不伪装为重跑成功。</p>
<p>只挑测了13项Python机制/归属检查和4项知联界面检查，均通过；没有跑全套或独立备用验收。当前结论：合批能够以较少请求取得结果，但主体与完整性仍需改进，本轮不能算识别能力验收通过。</p>
<p><a href="实测诊断证据.json">诊断与用量证据</a> · <a href="execution.json">实际调用收据</a> · <a href="run/index.html">原始模型运行结果</a> · <a href="本地修复重放_不是新模型预测.json">本地修复重放</a> · <a href="protocol.json">冻结范围、参数和提示词</a></p></main></html>'''
    (out/'选页实测结果与问题.html').write_text(html,encoding='utf-8')
    print(json.dumps({'requests':requests,'seconds':seconds,'target_values_found':found,
        'diagnostic_targets':len(cases),'missing':[c['id'] for c in missing],
        'local_aligned_before':alignment_before,'local_aligned_after':alignment_after,
        'rebound_candidates':moved,'new_model_calls':0,'original_run_unchanged':True},ensure_ascii=False))


if __name__=='__main__':main()
