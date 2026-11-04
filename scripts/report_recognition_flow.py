"""Audit old recognition inputs against current flow mechanisms; zero API calls."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from slopekg import knowledge_prompts, visual_prompts
from slopekg.knowledge_composition import source_accounting
from slopekg.knowledge_document import wire_chunks
from slopekg.knowledge_settings import VERSION as KNOWLEDGE_VERSION
from slopekg.model_client import ModelProfile
from slopekg.provenance import runtime_identity
from slopekg.visual_batching import verification_batches, compact
from slopekg.visual_contract import FACT_KEYS, VERSION as VISUAL_VERSION
from slopekg.visual_planning import plan_window
from slopekg.visual_sources import fact_quality_issues, content_coverage

PRIOR=ROOT/'outputs/20261031_三路修复复测'
OUT=ROOT/'outputs/20261031_识别流程复核'
read=lambda path:json.loads(path.read_text(encoding='utf-8-sig'))
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    before=read(OUT/'before_flow_analysis.json')
    for route,info in before['source_prediction_hashes'].items():
        assert sha(ROOT/info['path'])==info['sha256'],route
    knowledge=read(PRIOR/'knowledge/run.json')['documents'][0]
    visual=read(PRIOR/'visual/run.json')['documents'][0]
    accounting=source_accounting(knowledge)
    after=[]
    for old in before['visual']['verification_plan']:
        facts=[f for f in visual['facts'] if f['page']==old['page'] and f['region_id']==old['region']]
        wire=[{**{key:f[key] for key in (*FACT_KEYS,'id','quote','bbox')},'unit_basis':f.get('unit_basis',''),
            **({'source_bindings':f['source_bindings']} if f.get('source_bindings') else {}),
            **({'independent_comparison':f['independent_comparison']}
               if f.get('independent_comparison',{}).get('differences') else {}),
            'support_quotes':f.get('support_quotes',[]),'field_evidence':f.get('field_evidence',{})} for f in facts]
        batches=verification_batches(wire,12000,3,shared_region=True)
        assert [f for batch in batches for f in batch]==wire
        after.append({**old,'new_planned_verify_batches':[len(b) for b in batches],
                      'all_candidates_preserved':True})
    old_batches=sum(len(r['planned_verify_batches']) for r in after)
    new_batches=sum(len(r['new_planned_verify_batches']) for r in after)
    prepared=read(OUT/'source_preparation_local.json')['document']
    units={u['id']:u for u in prepared['units']}
    source_wire=wire_chunks(prepared['chunks'],units)
    assert all('bbox' in u for chunk in source_wire for u in chunk['spans'])
    plan=plan_window(deepcopy(visual['pages']),22,independent_review=True)
    newly_visible_issues=[{'id':f['id'],'page':f['page'],'old_status':f['status'],
        'issues':fact_quality_issues(f,[f['quote'],*f.get('support_quotes',[])])}
        for f in visual['facts'] if any(b.get('kind')=='dimension' for b in f.get('source_bindings',[]))]
    profiles={name:ModelProfile.load(ROOT/'config/models'/name).identity()
              for name in ['deepseek-knowledge.json','deepseek.json','qwen.json','kimi.json']}
    summary={'created_at':datetime.now(timezone.utc).isoformat(),'status':'local_mechanisms_repaired; real_quality_not_retested',
        'new_provider_requests':0,'new_OCR_inference':0,'versions':{'knowledge':KNOWLEDGE_VERSION,'visual':VISUAL_VERSION},
        'producer':runtime_identity(),'historical_predictions_unchanged':True,
        'fixed_core_scope_note':'Prior four checkpoints depend on graphical dimensions or image-table interpretation; they do not measure general text knowledge extraction.',
        'knowledge':{'historical_candidates':len(knowledge['facts']),'source_accounting':accounting,
            'full_page_ocr_pending_pages':[p['page'] for p in prepared['pages'] if p['full_page_ocr_pending']],
            'geometry_kept_in_wire':True,'wire_chars_with_geometry':len(compact(source_wire))},
        'visual':{'cached_read_regions':len(after),'same_candidates_planned_verify_batches_before':old_batches,
            'same_candidates_planned_verify_batches_after':new_batches,'batch_details':after,'window_plan':plan,
            'new_measurement_requirements_diagnostic':newly_visible_issues,
            'warning':'No new prediction or measured call saving. Coalesced images and measurement output need frozen real-model validation.'},
        'prompt_chars_before':before['prompt_chars'],
        'prompt_chars_after':{'knowledge_discover':len(knowledge_prompts.DISCOVER),
            'knowledge_attribute':len(knowledge_prompts.ATTRIBUTE),'visual_read':len(visual_prompts.READ),
            'visual_verify':len(visual_prompts.VERIFY)},
        'model_profiles':profiles,
        'local_tests':{'unique_passed':66,'new_tests':7,'provider_requests':0,
            'runs':[{'modules':['tests.test_recognition_flow','tests.test_recognition_repairs',
                'tests.test_knowledge_v3.KnowledgeSourceReuseTests','tests.test_visual_pipeline.ModelTransportTests'],
                'passed':27,'elapsed_seconds':1.154},
                {'modules':['tests.test_visual_pipeline.VisualWorkflowTests','tests.test_knowledge_v3.KnowledgeV3Tests'],
                 'passed':39,'elapsed_seconds':43.136}]},
        'remaining_work':['Execute bounded local full-page OCR and verify its actual coverage.',
            'Test object discovery, source ownership and graphical measurement meaning on newly frozen inputs.',
            'Reduce repeated full structural extraction during blind reading; retain independent source verification.',
            'Verify dense page-wide reading still preserves small text and source relationships.']}
    (OUT/'flow_review_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'same_candidate_batch_plan.json').write_text(json.dumps(after,ensure_ascii=False,indent=2),encoding='utf-8')
    snapshot=[]
    paths=[*sorted((ROOT/'src/slopekg').glob('*.py')),
        *(ROOT/'config/models'/name for name in profiles),
        ROOT/'tests/test_recognition_flow.py',ROOT/'tests/test_knowledge_v3.py',Path(__file__)]
    for source in paths:
        rel=source.relative_to(ROOT);target=OUT/'code_snapshot'/rel
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        snapshot.append({'path':str(rel),'sha256':sha(source)})
    (OUT/'code_snapshot_manifest.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
    ledger=''
    if (OUT/'ledger_receipt.json').exists():ledger=escape(json.dumps(read(OUT/'ledger_receipt.json')['ids'],ensure_ascii=False))
    rows=''.join(f'<tr><td>p{r["page"]} {escape(r["region"])}</td><td>{r["facts"]}</td><td>{len(r["planned_verify_batches"])}</td><td>{len(r["new_planned_verify_batches"])}</td></tr>' for r in after)
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>知联与视读：代码流程复核</title><style>body{{margin:0;background:#f1f4f7;color:#183047;font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1100px;margin:auto;padding:28px 22px 65px}}h1{{font-size:28px;line-height:1.4}}h2{{font-size:22px;margin-top:32px}}section{{background:white;padding:21px;border-radius:12px}}.notice{{background:#fff0e7;border-left:5px solid #b36137;padding:18px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:23px 0}}.card{{background:white;border-radius:10px;padding:17px}}.card b{{font-size:26px;display:block}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #dce5ed;min-width:85px}}th{{background:#e8eef4}}p,li,small,code{{overflow-wrap:anywhere}}small{{display:block;color:#53697b}}code{{font-size:13px}}a{{color:#146794}}@media(max-width:650px){{main{{padding:18px 12px}}h1{{font-size:23px}}.cards{{grid-template-columns:repeat(2,1fr)}}td{{min-width:140px}}}}</style>
<main><small>2026-10-31 · 知联 {KNOWLEDGE_VERSION} · 视读 {VISUAL_VERSION}</small><h1>主要卡在流程；局部机制已改，尚未证明识别恢复</h1>
<p>两路有明确的改进空间。当前证据不能据此判定模型能力不行；也不能仅因本地测试通过就称路线已经救活。本轮把真实缓存、源码分支和请求拆分对齐，没有新增API。</p>
<div class="notice"><strong>先纠正上一轮结论的口径：</strong>4项核心检查都依赖图形尺寸或图像表格，知联的0/4不等于它没有文本理解能力。它实际产生33条候选，包括材料、施工间距和排水要求；这些也尚非专家确认。真正严重的问题是：第8页38个来源单元一个都没进入对象重组，流程却没有把这个缺口清楚列出来。</div>
<div class="cards"><div class="card"><b>0次API</b>本轮只有代码、匿名测试和旧缓存诊断</div><div class="card"><b>66项</b>定向离线检查通过，非真实准确率</div><div class="card"><b>{old_batches} → {new_batches}</b>相同候选的核验计划批数，非实发节省</div><div class="card"><b>38 / 38</b>旧第8页原文全部未归入对象</div></div>
<h2>知联为什么会丢掉一整页</h2><section><p><strong>原流程：</strong>原生解析/标题区OCR → 按字符切分 → 发现focus对象 → 只给这些对象分配证据 → 只整理分配到的来源 → 字面校验和选择性复核。</p>
<p>发现、归属、重组是串联的。前面没发现对象、没分配来源，后面的重组就没有机会补回。第8页图名成为catalog线索，图内数字没有进入两个focus对象的证据；发现和归属请求返回成功，不代表所有原文已经整理。</p>
<p>程序还主动在三个模型阶段删除bbox，把可定位的OCR排成文字串。数字虽被传入，端点、方向、表格行列关系却没有随文字保留。原生文本为0时又复用了title_block已完成任务，没有补规划全页OCR。图形测量需要视觉关系；纯OCR数字无法可靠推导被测对象和尺寸含义。</p>
<p><strong>已改：</strong>保留来源框与置信度；标题区任务完成后仍规划全页OCR，继续受ocr_pages上限约束；同文同位置去重，不合并不同位置的相同值。新增逐页来源记账：本缓存第8页38条、第9页13条未归属均可查，不猜配给附近对象，不把整页旁路当完成。</p>
<p><strong>尚未验证：</strong>本轮只验证全页OCR任务规划和匿名注入式执行，没有实际运行这两页新OCR，也没有新模型预测。补了文字和坐标能否改善发现/重组仍待实测；图形量义应交给视读核验。</p></section>
<h2>视读为什么费钱却没有把尺寸说清楚</h2><section><p><strong>原流程：</strong>两页概览切成9区 → 区域细读 → 有候选的区域再盲读一遍完整结构 → 逐条语义核验 → 覆盖查漏 → 疑点放大。真实运行在24次请求后仍未完成。</p>
<p>一张表被拆成110条候选，独立复读又生成110条。核验已经共享同一组源图，代码仍按12条再次硬拆；在12K字符容量下，实际缓存需要11个核验批次，而不是一个完整表单元。另一段25条候选也被拆成3批。它们挤占后续核验和查漏预算。</p>
<p>另一个断点在表示和验收：“尺寸标注=某个数字”即使得到supported，也只支持这个笼统命题。旧dimensions只有text和bbox，没有要求输出测量量义、被测对象和端点/方向依据；转录关联成功便被算成已覆盖。当前4个检查值都被读到，仍有3个没有完成测量含义。</p>
<p><strong>已改：</strong>共享源图按文本容量分批，取消12条二次硬拆；读取前规划完整周期，预算紧时合并同页源范围并保留原区域，实体身份不合并，核验要求不降低。尺寸结构增加measurement，对象、quantity和测量依据不完整时只能保留转录；fact.field须对应量义，不能用笼统数字命题替代。</p>
<p><strong>尚未验证：</strong>新的页面合并阅读是否损失小字分辨率、新结构能否让模型正确解释端点与方向，都没有真实成绩。盲读仍重复生成完整事实结构，这一消耗尚未改成更轻的独立源表示。</p></section>
<h2>相同缓存候选，分批机制改变了多少</h2><div class="scroll"><table id="batch-plan"><thead><tr><th>源区域</th><th>候选数</th><th>原计划核验批次</th><th>新计划核验批次</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>合计{old_batches}→{new_batches}，所有原候选均保留。这是对相同候选JSON的零API分批重算；实际重读后的候选、质量、请求数和tokens可能变化，不能直接声称上轮28次已降到某个次数。</p>
<h2>参数、提示词与JSON失败</h2><section><p>所有已配置模型的最大输出设置保持，DeepSeek实发上限仍为393216；未降低思考模式。知联发现/归属提示词均未增加。视读READ由{before['prompt_chars']['visual_read']}字符变为{len(visual_prompts.READ)}字符：增加通用测量结构，同时删除重复的角色定义，没有加入文件名、桩号或题目答案。</p>
<p>公共客户端现在能在非法JSON回执中保存内容类型、长度、哈希、JSON错误位置及顶层类型，不保存源文或推理内容。空内容、非法JSON、数组顶层和缺失字段可分开诊断；旧失败回复的原文仍不可恢复，不会为找回它重发API。</p></section>
<h2>已经证明与还没证明</h2><section><p>匿名离线测试证明：局部丢弃修复仍在、四种旋转坐标仍合法、同图批次不再受12条限制、预算不会突破上限、全页OCR复用有界、不同位置同数字不被合并、数值转录不能通过完整测量门控、JSON诊断不泄露正文。合计66项检查通过。</p>
<p>没有证明：这份原件的真实语义恢复、整份固定100项回归、跨资料通用质量、专家签认或风险评估能力。基读上层规则保持停止扩充，底层工具继续复用。下一轮应先验证来源覆盖和通用测量关系，再小范围冻结实测；不靠增加提示词答案示例或扩大调用预算换结果。</p>
<p><a href="flow_review_summary.json">完整流程与机制摘要</a> · <a href="before_flow_analysis.json">改造前缓存分析</a> · <a href="same_candidate_batch_plan.json">逐批对账</a> · <a href="source_preparation_local.json">本地源准备</a> · <a href="cached_source_accounting.json">未归属来源</a> · <a href="code_snapshot_manifest.json">当前代码冻结</a> · <a href="../20261031_三路修复复测/通用修复与两页核心回归.html">上一轮真实预测与用量</a></p>
<small>当前代码指纹：{summary['producer']['code_revision']}<br>唯一待核事项仍维护在.private项目台账；本页是流程复核快照。{ledger}</small></section></main></html>'''
    (OUT/'知联与视读_代码流程复核.html').write_text(html,encoding='utf-8')
    print(json.dumps({'new_API_requests':0,'local_tests':66,'planned_verify_batches':[old_batches,new_batches],
        'knowledge_unassigned_units':accounting['unassigned_units'],'versions':summary['versions'],
        'prompt_chars':summary['prompt_chars_after'],'quality_retested':False},ensure_ascii=False))


if __name__=='__main__':main()
