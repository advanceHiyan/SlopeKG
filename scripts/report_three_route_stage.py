"""Report the frozen first-stage experiment; never imports a model client."""
from collections import Counter
from datetime import datetime
import hashlib
from html import escape
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/20261031_三路盲测_阶段1'


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    protocol, frozen, execution = read('protocol.json'), read('predictions_frozen.json'), read('execution.json')
    for route, info in frozen['predictions'].items():
        assert sha(ROOT / info['path']) == info['sha256'], route
    for path, digest in protocol['answer_commitments'].items():
        assert sha(ROOT / path) == digest
    points = read('selected_development_points_after_freeze.json')
    core = read('selected_core_points_after_freeze.json')
    assert len(points) == 27 and not core
    knowledge = read('knowledge/run.json')['documents'][0]
    visual = read('visual/run.json')['documents'][0]
    coordinates, database = read('coordinate_diagnostic.json'), read('database_probe.json')
    known_refs = {
        'angle': 'k_e772df0a4f881b4eabcf82',
        'cover': 'k_482f1ecf135f2fa3e35868',
        'water': 'k_6bf2b6b4192121a7265ab5',
        'ditch': 'k_fbb346c3d1ed56b96fdf3a',
        'stability': 'k_f39cd134e353411361d7b3',
    }
    ids = {f['id'] for f in knowledge['facts']}
    assert set(known_refs.values()) <= ids
    match_groups = {
        'angle': ['pt_e3aab88010c99b66ef20', 'pt_469c69c62f1362fb72c7', 'pt_7b8c7ed477c22e2f1687'],
        'cover': ['pt_b54b4f73701f696fe7e7', 'pt_6df425131192b5e0a6d3', 'pt_84a3d91359f036f67bae'],
        'water': ['pt_942d011ce75fb5fac9b8', 'pt_988314504666748d0df5', 'pt_b91abe1dbdabae02a04f'],
    }
    matches = {pid: group for group, members in match_groups.items() for pid in members}
    partials = {
        'pt_87565443980b578e0482': ('side', '右侧出现在边沟/场地候选scope，未形成独立场地侧别字段，局部桩号另有冲突。'),
        'pt_39643bd1ed2b5df9f260': ('ditch', '0.6×0.6的设计断面已出现，但单位/对象证据门控未通过，保留待审。'),
        'pt_7e6de3c2e466d04830a8': ('identity', '封面项目对象名称含正确桩号范围；未产出独立范围事实。'),
        'pt_1ae08b73753e19f581cc': ('stability', '基本稳定在复合描述中，待审；未拆成独立自然坡体稳定状态。'),
        'pt_840e5b2828f9d40ec6aa': ('ditch', '有断面尺寸，正式候选缺C20，整项不完整。'),
        'pt_83c0fb9fa7f6cf6880bf': ('ditch', '有断面尺寸和design角色，正式候选缺C20，整项不完整。'),
    }
    negative = 'pt_186a6dffd7cda9d1e0e1'
    certificate = 'pt_0c1902b0aec76d7d1ab7'
    judgments = []
    for point in points:
        pid = point['id']
        row = {'point_id': pid, 'expected': point['expected'], 'pages': point['pages'],
               'carrier': point['carrier'], 'granularity': point['granularity'], 'in_fixed_core': False}
        row['basic'] = {'status': 'positive_control_missing' if pid == negative else 'missing',
                        'reason': '本轮结构化实体和属性输出为空；原生文本/OCR读到数字不算结构化通过。'}
        if pid in matches:
            row['knowledge'] = {'status': 'candidate_value_semantics_match', 'fact_id': known_refs[matches[pid]],
                                'reason': '值、语义对象及页码能对应；证据框存在越界，不能视为可自动采用或验收通过。'}
        elif pid == negative:
            row['knowledge'] = {'status': 'negative_with_positive_control', 'fact_id': known_refs['cover'],
                                'reason': '存在覆盖层厚度正例，未把它写成整坡高度；不等于整份资料无误归属。'}
        elif pid in partials:
            group, reason = partials[pid]
            row['knowledge'] = {'status': 'partial', 'fact_id': known_refs.get(group), 'reason': reason}
        else:
            row['knowledge'] = {'status': 'missing', 'reason': '正式冻结输出中无完整对应事实；失败批次的invalid_answer另存，不能冒充已发布结果。'}
        if pid == certificate:
            row['visual'] = {'status': 'candidate_value_semantics_match',
                'fact_id': 'b112e47aa009628b464bc395', 'reason': '第2页公路行业资质编号一致，但候选仍pending。'}
        elif pid == negative:
            row['visual'] = {'status': 'positive_control_missing', 'reason': '缺覆盖层厚度正例，不因没有错误输出而奖励通过。'}
        elif pid == 'pt_87565443980b578e0482':
            row['visual'] = {'status': 'partial', 'fact_id': 'a1745b32872573080874e4af',
                             'reason': '右侧在现状描述scope内，未形成独立字段；局部桩号冲突未裁决。'}
        else:
            row['visual'] = {'status': 'missing', 'reason': '本轮冻结输出无对应完整候选；受测试参数导致的截断和熔断影响，不外推生产配置能力。'}
        judgments.append(row)
    errors = []
    for route in ['knowledge', 'visual']:
        for entry in read(f'{route}/transport/transport.json'):
            receipt = entry.get('receipt') or {}
            if receipt.get('finish_reason') == 'length':
                errors.append({'route': route, 'ordinal': entry['ordinal'], 'provider_response_id': receipt.get('id'),
                    'usage': receipt['usage'], 'classification': 'known_provider_receipt_output_truncated_not_unknown_billing'})
    assert len(errors) == 2
    failed = []
    for path in (OUT / 'knowledge/cache').glob('*.json'):
        cached = json.loads(path.read_text(encoding='utf-8'))
        if cached.get('stage') == 'knowledge_synthesize' and cached.get('invalid_answer'):
            wire = read('knowledge/inputs/' + path.name)
            context = json.loads(wire['prompt'].rsplit('\n输入：', 1)[1])
            raw = cached['invalid_answer']['objects']
            wall = next(o for o in raw if o['entity_id'] == 'e_1564eebe8d1479da')
            failed.append({'cache_file': str(path.relative_to(OUT)), 'requested_objects': len(context['subjects']),
                'returned_objects': len(raw), 'failed_object_jobs': sum(j['status'] == 'failed' for j in knowledge['synthesis_jobs']),
                'wall_raw_fact_count': len(wall['facts']), 'wall_final_fact_count': sum(f['entity_id'] == wall['entity_id'] for f in knowledge['facts']),
                'wall_raw_facts': wall['facts'], 'original_error': cached['error']})
    summary = {'stage': 1, 'decision': 'stopped_no_expansion_no_more_model_calls',
        'actual_model_attempts': execution['total_actual_transport_entries'],
        'usage_receipts': sum(v['usage_receipts'] for v in execution['routes'].values()),
        'usage': {k: sum(v['usage'][k] for v in execution['routes'].values()) for k in ['prompt_tokens', 'completion_tokens', 'total_tokens']},
        'selected_pages': protocol['selected_pages'], 'source': protocol['source'],
        'fixed_core': {'total': 100, 'in_sample': len(core), 'status': 'not_executed_not_passed', 'score': None},
        'development_diagnostic': {'points': len(points), 'all_carriers': dict(Counter(p['carrier'] for p in points)),
            'counts': {route: dict(Counter(j[route]['status'] for j in judgments)) for route in ['basic', 'knowledge', 'visual']},
            'reviewer': 'Codex after prediction freeze; not expert certification', 'precision': None, 'recall': None},
        'truncation_errors': errors, 'composition_failures': failed, 'coordinates': coordinates,
        'database_probe': database, 'all_predictions_unchanged': True,
        'sealed_holdout_read': False, 'test_limitations': [
            'Blind source-prefix selection missed all 100 fixed-core members; no core pass or aggregate score claimed.',
            'Visual used an experimental 16,384 output cap with enabled max reasoning; differs from production 131,072 cap.',
            'Gateway wording calls missing HTTP status unknown; two actual failures have usage receipts and finish_reason=length.',
            'Visual runtime counted 24 local reservations; only 17 entered transport, seven were blocked locally.',
            'Development checkpoint matches contain correlated/overlapping tasks and do not establish whole-file P/R.',
            'Recognition flat-module fingerprint frozen; separately changing risk subpackage tested from its own later code snapshot.']}
    (OUT / 'stage_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / '开发点诊断_非核心回归分数.json').write_text(json.dumps(judgments, ensure_ascii=False, indent=2), encoding='utf-8')
    labels = {'missing': '未产出', 'partial': '部分对应', 'candidate_value_semantics_match': '候选值/语义对应',
              'negative_with_positive_control': '负例配套正例', 'positive_control_missing': '缺正例，不计通过'}
    names = {'basic': '基读 Basic', 'knowledge': '知联 Knowledge', 'visual': '视读 Visual'}
    costs = []
    for route in ['basic', 'knowledge', 'visual']:
        r = execution['routes'].get(route, {})
        count = 0 if route == 'basic' else len((knowledge if route == 'knowledge' else visual)['facts'])
        status = '结构化属性0条；2页本地OCR，90条OCR文本' if route == 'basic' else ('34候选：22字面对应、12待审；6对象作业失败' if route == 'knowledge' else '11候选全待审；2条回复截断；未完成所选4页')
        costs.append(f'<tr><th>{names[route]}</th><td>{r.get("actual_transport_entries",0)}</td><td>{r.get("usage_receipts",0)}</td><td>{r.get("usage",{}).get("total_tokens",0):,}</td><td>{count}</td><td>{status}</td></tr>')
    rows = []
    for j in judgments:
        expected = j['expected']
        desc = f'{expected["object"]} · {expected["field"]} · {expected["value"]} {expected.get("unit") or ""}'
        cols = ''.join(f'<td><strong>{labels[j[r]["status"]]}</strong><small>{escape(j[r]["reason"])}</small></td>' for r in ['basic', 'knowledge', 'visual'])
        rows.append(f'<tr><td><code>{j["point_id"]}</code><small>物理页 {j["pages"]}</small>{escape(desc)}</td>{cols}</tr>')
    opened = read('evaluation_opened.json')
    localtime = lambda v: datetime.fromisoformat(v).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    ledger_note = ''
    if (OUT / 'ledger_receipt.json').exists():
        ledger = read('ledger_receipt.json')
        ledger_note = ('已登记授权' + ledger['request_id'] + '与待修正事项' + '、'.join(ledger['review_ids'])
                       + '，更新P-02并补充RV-009；正式计划A:F和其余工作簿部件保持原样。')
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>三路首批盲测与停测记录</title><style>
body{{margin:0;background:#f2f5f8;color:#183047;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}}main{{max-width:1160px;margin:auto;padding:30px 22px 70px}}h1{{font-size:30px;line-height:1.3}}h2{{font-size:22px;margin-top:34px}}.lead{{font-size:19px}}.stop{{background:#fff0e9;border-left:5px solid #c35630;padding:18px 22px;border-radius:8px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}.card,section{{background:white;padding:20px;border-radius:12px}}.card b{{font-size:28px;display:block}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:13px;text-align:left;vertical-align:top;border-bottom:1px solid #dfe7ee;min-width:70px}}th{{background:#eaf1f6}}small{{display:block;color:#52687a;line-height:1.6;margin-top:5px}}code{{font-size:12px;overflow-wrap:anywhere}}a{{color:#146794}}p,li{{overflow-wrap:anywhere}}.muted{{color:#52687a}}@media(max-width:650px){{main{{padding:20px 12px}}h1{{font-size:25px}}.cards{{grid-template-columns:repeat(2,1fr)}}td{{min-width:180px}}}}
</style><main><p class="muted">2026-10-31 · 原件识别启动诊断 · 三路真实运行</p><h1>首批已停止，未进入扩样</h1>
<p class="lead">知联确实读到了挡墙尺寸，但批次校验把它们整组丢弃；旋转页的证据框还存在越界。视读另因本次测试参数搭配不当，发生两次输出截断。</p>
<div class="stop"><strong>固定100项核心回归：本轮覆盖0项，尚未执行，不能宣称通过。</strong><br>本轮按原件元数据盲选最短施工图的前4页，阅卷后才发现它们不在核心100项内。以下27项来自同一当前开发库，仅作启动诊断。既已触发重大问题停测条件，不再为补核心覆盖追加API。</div>
<div class="cards"><div class="card"><b>1份 / 4页</b>原件21页，仅第1–4页进入识别</div><div class="card"><b>21次</b>实际请求及用量回执各21条</div><div class="card"><b>207,400</b>返回tokens，非账单金额</div><div class="card"><b>0 / 100</b>固定核心覆盖；不算验收分数</div></div>
<h2>三路实际结果与调用</h2><div class="table"><table><thead><tr><th>路线</th><th>实际请求</th><th>回执</th><th>tokens</th><th>结构化事实/候选</th><th>本轮状态</th></tr></thead><tbody>{''.join(costs)}</tbody></table></div>
<p>全程约{execution['elapsed_seconds']:.0f}秒；知联约37秒，视读约256秒。知联实际峰值并发2、视读3。重试均为0。视读内部计数24次，其中7次被本地熔断拦截，真正进入传输层17次，不能把24次写成实际API用量。</p>
<h2>这次查实的问题</h2><section><p><strong>1．知联批次契约处理会丢掉已读出的有效候选。</strong>该批请求6个对象，模型返回了全登记表的11个对象，触发“Composition omitted or duplicated objects”。6个对象作业全部失败。挡墙原始回复含13条候选，包括高3.5m、顶宽1.2m、基础底宽2.55m、现浇C25混凝土，正式冻结输出却为0条挡墙事实。原始回复保存在失败缓存，未回填预测、未补发API。后续应按对象检查并保留合格的局部结果，把多余对象隔离处理；无需按此文档写规则。</p>
<p><strong>2．旋转页原生文字框的坐标有问题。</strong>第4页旋转90°，显示尺寸1191×842，MediaBox为842×1191。158个知联来源单元中25个框越界，25条候选引用了越界框，其中20条仍被标为source_aligned。示例y上界1.313409，超出归一化坐标0–1。字面一致不能证明定位正确，暂不能作为可靠裁图/跳转依据；需要通用旋转坐标转换及边界门控。</p>
<p><strong>3．视读本轮测试参数配套不当。</strong>为控制单次输出，我把输出上限从现有配置131,072调到16,384，却保留thinking enabled、reasoning_effort=max。两次回复的16,384个completion tokens全是reasoning tokens，finish_reason=length。这是我的测试参数错误，不能归因于生产配置固有识别能力。两次均有完整用量回执，属于已知截断，不是计费状态不明；熔断文案把它泛称unknown也不准确。其余流程还有一次布局引用未知锚点。没有追加请求重跑。</p>
<p><strong>4．基读本轮只有解析/OCR，没有结构化工程输出。</strong>2个OCR任务完成，得到90条文本。原生/OCR输入包含3.5、1.2、2.55、C25、C20，但本轮0个Slope、0条工程属性断言。该结论只限前4页，不外推整份文件或全部资料。</p></section>
<h2>与新事实仓、风险接口的实际接入核对</h2><section><p>使用单独冻结的当前风险模块副本和独立SQLite数据库，不调用网络，不修改生产库。知联34条候选已实际写入并重新打开读回，合同校验通过；全部保留reference语义，canonical绑定0、专业确认0，没有提升为已确认风险输入。基读本轮没有Slope目标可提交prepare。风险服务公开导入方法只有import_knowledge，没有import_visual，本轮未伪造视读适配。</p><p>因此，<strong>候选保存链路部分可用，三路到正式风险评估尚未贯通。</strong>本轮未运行风险定级、未获得任何正式等级。已有RV-009的接口边界继续保留，不能把SQLite有记录当成可评估。</p></section>
<h2>27项普通开发点的对应诊断</h2><p>以下是Codex在预测冻结后作的答案对照。知联有9项候选值/语义对应，实际集中于自然坡度、覆盖层厚度、未见地下水渗出这3组重叠语义；另1项负例有正例配套。视读有1项编号候选对应。它们均不是专家签认，也没有因对答案而修改原始状态。知联上述证据框问题、视读截断和待审状态仍保留。重复/关联检查点不算独立事实；不计算整体准确率、精确率或召回率。</p>
<div class="table"><table><thead><tr><th>当前开发检查点</th><th>基读 Basic</th><th>知联 Knowledge</th><th>视读 Visual</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>盲测及冻结证据</h2><section><p>原件：{escape(protocol['source']['path'])}<br>SHA-256：<code>{protocol['source']['sha256']}</code></p><p>三路预测全部冻结：{localtime(frozen['frozen_at'])}；首次评分读取答案：{localtime(opened['opened_at'])}（北京时间）。两份答案文件与事前哈希一致，三路原始预测哈希复查一致。预测进程通过读取拦截禁止访问评测/历史复核/fixtures；未出现被拦截尝试。备用sealed_holdout未读取。此为已暴露开发资料上的本轮答案盲测，不是新资料独立验收。</p><p>识别用的66个顶层模块指纹与事前冻结一致；测试期间单独风险模块有改动，数据库核对使用随后另行冻结的74文件副本，不把两套实现混称同一时点版本。</p>
<p><a href="protocol.json">冻结协议和完整提示词</a> · <a href="execution.json">原始调用回执汇总</a> · <a href="predictions_frozen.json">三路预测哈希</a> · <a href="stage_summary.json">诊断摘要</a> · <a href="开发点诊断_非核心回归分数.json">27项逐项判断</a> · <a href="coordinate_diagnostic.json">坐标复现</a> · <a href="database_probe.json">数据库实际读回证据</a></p></section>
<h2>停止后的下一步</h2><p>先修通用的分批输出容错和旋转坐标问题，并给测试参数增加发送前检查；再准备不含题目和答案的固定核心输入清单，按整份资料分组分阶段复测。当前100项核心回归仍待完成。本轮不再调用API、不扩样；争议和待修复状态集中维护在唯一项目台账。</p><p>{escape(ledger_note)}</p></main></html>'''
    (OUT / '三路首批盲测与停测记录.html').write_text(html, encoding='utf-8')
    print(json.dumps({'report': str(OUT / '三路首批盲测与停测记录.html'),
                      'requests': summary['actual_model_attempts'], 'tokens': summary['usage']['total_tokens'],
                      'fixed_core_points': len(core), 'diagnostic_points': len(points),
                      'decision': summary['decision']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
