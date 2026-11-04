"""Local-only v3 check: exposed source preparation and simulated call counts.

This script has no real model client and never runs OCR inference. It preserves
all historical predictions and must not be used as an independent accuracy eval.
"""
from dataclasses import replace
import copy
import hashlib
from html import escape
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from slopekg.knowledge_pipeline import KnowledgePipeline
from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.knowledge_composition import prepare_bindings, gate_ownership
from slopekg.visual_evidence import source_contains
from slopekg.storage import read_json, write_json
from tests.test_knowledge_v3 import BatchModel
from tests.test_knowledge_pipeline import document


class NoModel:
    def identity(self):
        return {'name': 'local-preparation-no-model', 'model': 'none'}

    def complete(self, **kwargs):
        raise AssertionError('This local validator forbids model requests')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = ROOT / 'outputs/20261029_知联v3本地验证'
    base = ROOT / 'outputs/20261028_新知识路线对照'
    out.mkdir(parents=True, exist_ok=True)
    previous = read_json(base / 'knowledge/run.json')
    source = Path(previous['documents'][0]['path'])
    protected = [base / 'knowledge/run.json', base / 'multimodal/run.json',
                 base / 'basic/extracted.json', base / 'basic/parsed.json', base / 'API调用与用量审计.json']
    before = {str(p): digest(p) for p in protected}
    request_dirs = [base / 'knowledge-v1-initial/cache', base / 'knowledge/cache', base / 'multimodal/cache']
    requests_before = sum(len(list(p.glob('*.json'))) for p in request_dirs)
    print('Preparing byte-matched Basic evidence; model access is forbidden.', flush=True)
    prepared = KnowledgePipeline(NoModel(), out / 'same-source-prepare',
        parsed_dir=base / 'basic/parsed.json', root=ROOT).run([source], prepare_only=True)
    current = prepared['documents'][0]
    print('Source preparation complete.', flush=True)
    # Replay one previously exposed failure through local guards only. This is
    # neither a new discovery nor a new v3 model prediction.
    audit = read_json(ROOT / 'outputs/20261028_三路效果复核/审计明细.json')
    old_case = audit['source_attachment_example']
    replay_doc = copy.deepcopy(current)
    replay_doc['entities'] = copy.deepcopy(previous['documents'][0]['entities'])
    for entity in replay_doc['entities']:
        entity['unit_ids'], entity['chunk_ids'] = [], []
    prepare_bindings(replay_doc)
    mapped, unmapped, ambiguous = [], [], []
    for evidence in old_case['evidence']:
        matches = [u for u in replay_doc['units'] if u['page'] == evidence['page']
                   and source_contains(evidence['quote'], u['text'], table=True)]
        if not matches:
            unmapped.append(evidence)
        if len(matches) > 1:
            ambiguous.append(evidence)
        mapped.extend({'span_id': u['id'], 'quote': evidence['quote']} for u in matches)
    replay_units = {u['id']: u for u in replay_doc['units']}
    known = {e['id']: e for e in replay_doc['entities']}
    local_guard = KnowledgePipeline(NoModel(), out / 'guard-not-a-prediction')
    checked = local_guard._item(replay_doc, known[old_case['entity_id']], 'fact',
        {**old_case, 'evidence': mapped}, replay_units, set(replay_units), known)
    gate_ownership(replay_doc, checked, replay_units)
    guard_replay = {'kind': 'exposed historical candidate through local guards; not a v3 model prediction',
        'historical_id': old_case['id'], 'historical_status': old_case['status'],
        'local_status': checked['status'], 'quality_issues': checked['quality_issues'],
        'unmapped_quotes': len(unmapped), 'ambiguous_quotes': len(ambiguous), 'record': checked}
    assert not unmapped
    assert 'foreign_object_section_evidence' in checked['quality_issues']
    assert checked['status'] == 'pending'
    write_json(out / '历史污染候选_本地门控重放.json', guard_replay)
    simulations = {}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); pdf = root / 'anonymous.pdf'; document(pdf)
        for label, settings in [('v3_default', KnowledgeSettings()), ('explicit_per_object_full_review',
                replace(KnowledgeSettings(), review_mode='all', synthesis_batch_objects=1))]:
            model = BatchModel()
            result = KnowledgePipeline(model, root / label, settings=settings).run([pdf])
            simulations[label] = {'simulated_requests': result['summary']['calls'],
                'facts': result['summary']['facts'], 'source_aligned': result['summary']['source_aligned'],
                'independently_reviewed': result['summary']['verified_model'],
                'semantic_records': [{k: f[k] for k in ('object', 'field', 'value', 'unit', 'scope', 'role', 'condition')}
                                     for f in result['documents'][0]['facts']]}
            write_json(out / (label + '_模拟结果.json'), result)
    assert sorted(simulations['v3_default']['semantic_records'], key=lambda f: f['field']) == sorted(
        simulations['explicit_per_object_full_review']['semantic_records'], key=lambda f: f['field'])
    after = {str(p): digest(p) for p in protected}
    requests_after = sum(len(list(p.glob('*.json'))) for p in request_dirs)
    assert before == after
    assert requests_before == requests_after
    assert prepared['summary']['calls'] == 0
    summary = {'date': '2026-10-29', 'new_real_model_requests': 0, 'real_ocr_inference_runs': 0,
        'scope': 'Known exposed development source preparation + anonymous simulated model mechanics; no new real-model accuracy result.',
        'source_sha256': current['source_sha256'], 'historical_files_before': before, 'historical_files_after': after,
        'historical_cache_records_before': requests_before, 'historical_cache_records_after': requests_after,
        'previous_source': {'schema_version': previous['schema_version'], 'chunks': len(previous['documents'][0]['chunks']),
            'units': len(previous['documents'][0]['units']), 'coverage': previous['documents'][0]['coverage']},
        'v3_source_preparation': {'schema_version': prepared['schema_version'], 'chunks': len(current['chunks']),
            'units': len(current['units']), 'source_inventory': current['source_inventory'],
            'call_plan': current['call_plan'], 'coverage': current['coverage']},
        'simulations': simulations, 'historical_candidate_local_guard_replay': guard_replay}
    write_json(out / '本地验证证据.json', summary)
    inventory = current['source_inventory']
    rows = [('原文单元', len(previous['documents'][0]['units']), len(current['units'])),
            ('模型读取分块', len(previous['documents'][0]['chunks']), len(current['chunks'])),
            ('结构表格接入', '无独立表格行适配', inventory['tables']),
            ('已有OCR文字复用', '无', inventory['ocr_rows_reused']),
            ('重复原生文字抑制', '无表格去重', inventory['duplicate_native_blocks']),
            ('真实模型调用', '保留历史记录', 0)]
    table = '<table><tr><th>同一资料的本地准备</th><th>历史知联v2</th><th>知联v3</th></tr>' + ''.join(
        '<tr>' + ''.join('<td>' + escape(str(c)) + '</td>' for c in row) + '</tr>' for row in rows) + '</table>'
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>知联v3改造与本地验证</title>
<style>body{font:16px/1.8 "Microsoft YaHei",sans-serif;background:#eff3f6;color:#233b4c;margin:0}main{max-width:1100px;margin:28px auto;background:white;padding:30px;border-radius:16px}h1,h2{color:#125569}table{border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #dce5eb;text-align:left}th{background:#e8f2f6}.note{border-left:4px solid #c38737;background:#fff5e7;padding:16px}a{color:#126d8e}code{overflow-wrap:anywhere}@media(max-width:700px){main{margin:0;padding:18px}table{font-size:13px}}</style><main>
<p>2026-10-29 · 知联 Knowledge · knowledge-v3</p><h1>对象先聚齐，知识合批整理</h1>
<p>改造已落地：共享基读解析、表格、已有OCR；按对象聚合证据，多个对象合批整理；原文对齐与独立模型复核分开。默认总调用上限64，可选歧义复核最多4批，不自动提额。</p>
<p class="note">本次真实模型调用0，真实OCR推理0。以下只证明代码机制与同一原件的证据适配；没有产生新版G209真实模型预测，不能宣布识别准确率或召回率提高。</p>
<h2>同一份G209的证据准备</h2>''' + table + '''
<p>结构表格是基读已解析的真实单元格行，保留表头/行关系；61张表不等于61张表已被正确理解。该冻结基读快照没有可复用OCR文字，因此这里如实记0。新OCR接线和缓存通过模拟本地OCR测试，未执行真实模型或下载。</p>
<h2>匿名设备文档的模拟调用对照</h2><p>同一份7页匿名设备资料、相同两个字段：默认合批路径为<b>''' + str(simulations['v3_default']['simulated_requests']) + '''次模拟请求</b>；显式“逐对象＋全面复核”配置为<b>''' + str(simulations['explicit_per_object_full_review']['simulated_requests']) + '''次模拟请求</b>。后者多出独立复核步骤，状态含义不同。这个小例子验证调度节省，不是G209生产调用数预测。</p>
<h2>已知G209引用污染的本地重放</h2><p>将此前已暴露的“坡面清危+挂网锚喷”候选引文重新映射到本次基读证据：8条历史引用均找到对应原文。旧记录是verified_model，新本地门控识别到跨对象章节证据，将其保留为pending。它说明本地门控能拦住这一已知错误，不能据此推导全篇正确率。</p>
<h2>已实现的约束</h2><ul><li>全文逐批发现，晚出现的对象不因前部摘要截断而消失；同名上下文和共享简称分开处理。</li>
<li>表格数据行携带真实表头；不同对象的同值行不能互作证据；每条额外引文也检查归属。</li>
<li>先收集每个对象的非连续来源，再共享原文合批整理档案、子对象和相关主题。</li>
<li>本地引文/数值/单位/主体检查失败的条目直接待审，省去无效的模型复核。普通记录为source_aligned，不借用verified_model标记。</li>
<li>对象漏返、预算耗尽和失败保持partial；旧版本运行记录归档，失败或状态不明请求不自动重发。</li></ul>
<h2>仍然存在的边界</h2><p>扫描文字需要已有且可用的本地OCR结果或明确补页；OCR文字不能代替曲线、图例和工程尺寸理解。语义去重、未明确指名的跨章节归属仍依赖模型，尚需另获授权后的冻结对照验证。此次没有打开备用验收资料。</p>
<p>历史知联、视读、基读产物及调用审计的SHA-256均未变化；历史缓存记录数前后相同：''' + str(requests_after) + '''。</p>
<p><a href="本地验证证据.json">完整本地验证证据</a> · <a href="same-source-prepare/index.html">同资料本地准备结果</a> · <a href="../20261029_WeKnora信息与技术栈对照/识别信息与技术栈对照.html">改造前与WeKnora的对照</a></p></main></html>'''
    (out / '知联v3改造与本地验证.html').write_text(page, encoding='utf-8')
    print(json.dumps({'report': str(out / '知联v3改造与本地验证.html'),
        'source_inventory': inventory, 'chunks': len(current['chunks']), 'units': len(current['units']),
        'simulated_calls': {k: v['simulated_requests'] for k, v in simulations.items()},
        'new_real_model_requests': 0, 'historical_cache_records': requests_after}, ensure_ascii=False))


if __name__ == '__main__':
    main()
