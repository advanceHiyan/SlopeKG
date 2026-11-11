"""Data-only intake of autonomous Agent proposals, with immutable provenance.

No field-name classifier, object-name guessing, or additional model call. The
Agent owns semantic decisions; the host validates references/types and runs the
existing review-only standard tables. Arbitrary additional fields are retained.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import threading

from .contract import CONTRACT_VERSION, UNITS, VOCABS, encoded, fact_record, identity
from ..storage import read_json, write_json

SUBMISSION_VERSION = 'slopekg.agent-assessment/1'
_SUBMIT_LOCK = threading.RLock()


def normalized_value(variable, raw):
    """Only equivalent data representations, never field/meaning inference."""
    if raw is None: return None, 'canonical', []
    if not isinstance(raw,dict): raise ValueError('value必须为带kind的对象或null')
    value=copy.deepcopy(raw);changes=[];normalization='canonical'
    if value.get('kind')=='null':
        if value.get('value') is not None:raise ValueError('未知值不能携带数值')
        return None,'canonical',['explicit_null_object']
    if value.get('kind') in {'number','interval'} and variable in UNITS:
        expected=UNITS[variable];unit=value.get('unit');scale=1
        aliases={'person':{'人','person','people'},'1':{'1','无量纲','dimensionless'},
                 'deg':{'deg','°','度','degree'},'10k_CNY':{'10k_CNY','万元','万元人民币'}}
        if unit in aliases[expected]:
            value['unit']=expected
        elif expected=='10k_CNY' and unit in {'元','CNY','人民币元'}:
            scale=0.0001;value['unit']=expected
        else:
            # Preserve unfamiliar/incompatible units as an unresolved candidate;
            # the existing evaluator does not select raw numeric inputs.
            normalization='raw';changes.append('unresolved_unit:'+str(unit))
        if value.get('unit')!=unit:changes.append({'unit_from':unit,'unit_to':expected,'scale':scale})
        if scale!=1:
            for key in ('value',) if value['kind']=='number' else ('lower','upper'):
                if value.get(key) is not None:
                    if type(value[key]) not in {int,float}:raise ValueError('有限JSON数值必需')
                    value[key]*=scale
    return value,normalization,changes


def intake_contract():
    return {
        'schema_version': SUBMISSION_VERSION,
        'purpose': '由Agent自主决定对象、范围、适用性、事实映射、缺口及冲突；接口接收数据，不按中文字段名猜测量义。',
        'output': 'outputs/assessment.json',
        'shape': {'schema_version': SUBMISSION_VERSION, 'cases': [{
            'id': '本次提交唯一案例ID', 'document_id': 'manifest中的document_id', 'object_id': 'objects.json中的对象ID',
            'label': '可读的案例名称',
            'applicability': {'landslide': True, 'reason': '为何适用或不适用滑坡表', 'basis_fact_ids': ['事实ID']},
            'time': {'kind': 'instant/report_period/event_period/unknown', 'start': 'YYYY-MM-DD或null', 'end': 'YYYY-MM-DD或null', 'raw': '原文时期', 'basis_fact_ids': ['事实ID']},
            'scenario': {'code': 'natural/rainstorm/unspecified/自定义', 'raw': '原文工况'},
            'inputs': [{'id': '案例内唯一输入ID', 'variable': '标准变量或自定义字段',
                'value': {'kind': 'number', 'value': 50, 'unit': 'person'},
                'fact_ids': ['支持该输入的原始事实ID；明确缺失的null可为空'], 'role': 'observation/calculation/judgment/reference/design/recommendation/prediction/simulation',
                'semantic': 'observation/reported_judgment/model_judgment', 'source_standard': 'unknown或明确标准',
                'status': 'proposed/conflict/unknown', 'reason': '归属、量义和推导依据',
                'scope': {'object_id': '采用该输入进行评估的对象ID；同本案例可省略此键',
                          'time': {'kind': 'instant/report_period/event_period/unknown', 'start': 'YYYY-MM-DD或null', 'end': 'YYYY-MM-DD或null', 'basis_fact_ids': ['时期依据事实ID']},
                          'scenario': {'code': 'natural/rainstorm/unspecified/自定义', 'raw': '原文工况'},
                          'description': '原文范围限定；对象、时期、工况相同时可以省略整个scope或填null'}}],
            'gaps': [], 'conflicts': [], 'analysis': '自主综合分析，可附任意额外字段'}], 'limitations': []},
        'numeric_variables': UNITS,
        'enum_variables': {k: {'vocabulary': v[0], 'labels': v[1]} for k, v in VOCABS.items()},
        'other_variables': ['observations', 'geological_context', 'reference', '任意扩展字段，保留原样但不自动纳入现有查表'],
        'value_types': ['number: kind/value/unit（接受人、度、万元、元等明确单位并保留转换记录）', 'enum: kind/code/label/vocabulary', 'text: kind/text',
                        'interval: kind/lower/upper/lower_inclusive/upper_inclusive/unit', 'null或{"kind":"null"}: 明确未知；可以无事实引用'],
        'rules': [
            '自主选取适用案例；文献、规范概念或危岩体可以不适用，说明原因。cases可为空，但应说明limitations。',
            'fact_ids和basis_fact_ids只能引用同文档的实际识别事实；跨对象使用需在reason中说明，接口保留完整关系及原始归属。',
            '已知时期必须有日期与basis_fact_ids；没有日期用unknown，不用本次运行日期填充。各时期及工况分别建案例。',
            '原述等级与自主推断分别标reported_judgment和model_judgment；推断需给理由和事实依据。',
            'role表示在本案例中的用途：judgment表示作为预评估候选采用的判断；reference表示只作参考、明确不参与查表。是否原文报告值由semantic记录，不必把所有原判都标reference。由你自主决定采用或不采用并说明理由。',
            '已明确的单条输入scope可以直接引用该输入的fact_ids支持日期；不必重复basis_fact_ids。文档未提及的未知值可无引用，不补造来源。',
            'scope是用于计算的对象，不要用一句说明文字代替结构。说明放在scope.description或reason；真正无法确定的文字scope仍可保留待核。只有证据确实同对象、同期、同工况时才可省略scope继承案例，不能通过删掉范围来消除真实不一致。',
            '区分原始记录对象和评估对象：若资料明确某统计区覆盖该滑坡的影响区，受威胁人数可用于该滑坡，scope.object_id指向该滑坡，reason和fact_ids保留覆盖依据。局部点位、局部统计或范围不明不能直接当作整体。原始facts中的归属不改写。',
            '同对象、同期、同口径的同一指标存在不同来源值时，在同一案例内保留所有备选及conflicts；不得拆成互不相关案例后假装冲突已解决。真正不同时期或工况才分别建案例。',
            'check_outputs会执行现有国标程序的只读试算，逐条返回输入采用状态、对象/时期/工况/角色/单位/来源支持及冲突。按证据修正表达后再检查；真实缺口与冲突可以保留。试算不是真值答案，不能为了得到等级改值、隐去冲突或补造依据。',
            'Fs不等于发育程度；险情和灾情、设计值和现状、历史事件和当前巡查不可混用。',
            '冲突备选分别保留；未知不填0。标准来源不明用unknown。缺口、冲突、自主建议全部保留。',
            '模型提交一律待复核、条件性输入；不会授权专业签认或发布正式等级。',
        ],
        'http': {'method': 'POST', 'path': '/api/assessment-debug/agent-submit',
                 'body': 'route=agent, run_id, document, expected_recognition_hash, submission=本结构；提交后返回案例及全过程链接'},
    }


def recognition_store(paths, run_id):
    from ..agent_service import select_run, run_root
    from ..recognition_debug import _safe_location, DENIED_HASHES
    run = copy.deepcopy(select_run(paths, '' if run_id == 'live' else run_id))
    root = run_root(paths, run)
    if run.get('processing_status') != 'completed':
        raise ValueError('请等待第五路完成并通过结构核验后再衔接预评估')
    manifest = read_json(root/'workspace/manifest.json', {})
    if (root/'workspace/outputs/assessment.json').is_file():
        run['assessment_submission'] = read_json(root/'workspace/outputs/assessment.json')
    for doc in run.get('documents', []):
        if doc['source_sha256'] in DENIED_HASHES: raise ValueError('该资料不在开放调试范围')
        source = Path(doc.get('path') or '')
        if not source.is_file():
            # Legacy runs stored the original filename/hash, not its path.
            # Search only the project source directory, never evaluation files.
            choices = list(paths.raw_dir.rglob(doc['name']))
            source = next((p for p in choices if hashlib.sha256(p.read_bytes()).hexdigest() == doc['source_sha256']), source)
        source = _safe_location(source, paths.root)
        if not source.is_file(): raise ValueError('未找到与第五路哈希匹配的原PDF：'+doc['name'])
        doc['path'] = str(source)
        doc['units'] = []
        entry = next((d for d in manifest.get('documents', []) if d['document_id'] == doc['id']), {})
        for f in entry.get('files', []):
            if f['kind'] != 'native_text_auxiliary_not_ground_truth': continue
            p = _safe_location(root/'workspace'/f['path'], paths.root)
            if not p.is_relative_to((root/'workspace/inputs').resolve()): raise ValueError('辅助文字路径越界')
            if hashlib.sha256(p.read_bytes()).hexdigest() != f['sha256']: raise ValueError('第五路输入文字发生变化')
            doc['units'].append({'id': f['path'], 'page': f['physical_page'], 'text': p.read_text(encoding='utf-8-sig')})
        doc['pages'] = [{'page': p} for p in doc['selection']['selected_pages']]
    validate_submission(run.get('assessment_submission'), run)
    return run, root


def _time(value, fallback_refs=()):
    if not isinstance(value, dict) or value.get('kind') not in {'instant', 'report_period', 'event_period', 'unknown'}:
        raise ValueError('时期必须明确类型；缺少日期用unknown')
    for key in ('start', 'end'):
        if value.get(key) is not None: date.fromisoformat(value[key])
    if value['kind'] != 'unknown' and (not value.get('start') or not value.get('end') or not (value.get('basis_fact_ids') or fallback_refs)):
        raise ValueError('已知时期需要起止日期和支持事实引用')
    if value.get('start') and value.get('end') and value['start'] > value['end']: raise ValueError('时期起止倒置')
    if value['kind'] == 'unknown' and (value.get('start') or value.get('end')): raise ValueError('未知时期不能同时声明确定日期')


def validate_submission(submission, run):
    if submission is None: return
    if not isinstance(submission, dict) or submission.get('schema_version') != SUBMISSION_VERSION or not isinstance(submission.get('cases'), list):
        raise ValueError('无效Agent评估提交版本或cases')
    ids = set()
    docs = {d['id']: d for d in run['documents']}
    for case in submission['cases']:
        if not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id'] or case['id'] in ids:
            raise ValueError('案例ID缺失或重复')
        ids.add(case['id'])
        doc = docs.get(case.get('document_id'))
        if not doc: raise ValueError('案例引用不存在的文档')
        objects = {o['id'] for o in doc.get('objects', [])}
        facts = {f['id']: f for f in doc.get('facts', [])}
        if case.get('object_id') not in objects: raise ValueError('案例对象不属于该文档')
        def refs(value, required=False):
            if not isinstance(value, list) or (required and not value) or any(not isinstance(v, str) or v not in facts for v in value):
                raise ValueError('证据引用必须是本次同文档事实ID')
            for fact_id in value:
                evidence = facts[fact_id].get('evidence')
                for span in evidence if isinstance(evidence, list) else [evidence]:
                    if isinstance(span, dict) and span.get('document_id', doc['id']) != doc['id']:
                        raise ValueError(f'事实 {fact_id} 引用了另一份 PDF 的证据；请在证据所属文档单独提交来源案例')
        app = case.get('applicability', {})
        if not isinstance(app,dict) or type(app.get('landslide')) is not bool or not app.get('reason'): raise ValueError('需要Agent明确适用性及理由')
        if any(not isinstance(case.get(k,[]),list) for k in ('gaps','conflicts')): raise ValueError('gaps/conflicts必须为数组，内容可自由扩展')
        refs(app.get('basis_fact_ids', []), app['landslide'])
        _time(case.get('time')); refs(case['time'].get('basis_fact_ids', []))
        if not isinstance(case.get('scenario'), dict) or not case['scenario'].get('code'): raise ValueError('需要工况；不明确填unspecified')
        if not isinstance(case.get('inputs'), list): raise ValueError('inputs必须为数组')
        seen = set()
        for row in case['inputs']:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in seen: raise ValueError('输入ID缺失或重复')
            seen.add(row['id'])
            if not isinstance(row.get('variable'), str) or not row['variable']: raise ValueError('需要变量代码')
            value,normalization,_=normalized_value(row['variable'],row.get('value'))
            refs(row.get('fact_ids',[]), value is not None)
            if row.get('status', 'proposed') not in {'proposed', 'conflict', 'unknown'}: raise ValueError('模型不能提交已确认状态')
            if row.get('status') == 'unknown' and value is not None: raise ValueError('未知输入必须保留null，不能填数值')
            if row.get('role', 'observation') not in {'observation','calculation','judgment','reference','design','recommendation','prediction','simulation'}: raise ValueError('无效数据角色')
            scope = row.get('scope')
            if scope is None or isinstance(scope, str): scope = {}
            if not isinstance(scope, dict): raise ValueError('scope必须为对象、文字描述或null')
            if scope.get('object_id', case['object_id']) not in objects: raise ValueError('输入对象不存在')
            if 'time' in scope: _time(scope['time'],row.get('fact_ids',[])); refs(scope['time'].get('basis_fact_ids', []))
            if 'scenario' in scope and (not isinstance(scope['scenario'], dict) or not scope['scenario'].get('code')): raise ValueError('输入工况无效')
            fact_record(row['variable'], value, {'subject': {}, 'time': {}, 'scenario': {}},
                        source_refs=['submission-schema-check'], evidence_refs=[], origin='agent',feature=row.get('feature'),normalization=normalization)
    encoded(submission)  # Reject NaN/Infinity and non-JSON values, retain extensions.


def proposals(data):
    submission = data['run'].get('assessment_submission')
    if submission is not None:
        return [c for c in submission['cases'] if c['document_id'] == data['document']]
    return []


def subjects(data):
    rows = proposals(data)
    if rows:
        return [{'id': c['id'], 'label': c.get('label') or c['object_id'], 'supported': c['applicability']['landslide'],
                 'kind': 'agent_proposal', 'aliases': [], 'time': c['time'], 'scenario': c['scenario']} for c in rows]
    return [{'id': o['id'], 'label': o.get('name', o['id']), 'supported': False, 'kind': 'agent_raw_object', 'aliases': []}
            for o in data['doc'].get('objects', [])]


def prepare(data, request, paths, meta, original_context, original_run_hash, *, api=None):
    from . import debug_flow
    selected = request['subject_id']
    proposal = next((c for c in proposals(data) if c['id'] == selected), None)
    if proposal is None:
        obj = next((o for o in data['doc']['objects'] if o['id'] == selected), None)
        if obj is None: raise ValueError('不存在该Agent案例或对象')
        proposal = {'id': selected, 'document_id': data['document'], 'object_id': selected, 'label': obj.get('name', selected),
            'applicability': {'landslide': False, 'reason': '该旧记录尚无Agent自主评估提交；识别事实完整保留，未猜测适用灾种。', 'basis_fact_ids': []},
            'time': {'kind':'unknown','start':None,'end':None,'raw':'来源时期待Agent整理'},
            'scenario': {'code':'unspecified','raw':'来源工况待Agent整理'}, 'inputs': [],
            'gaps':['需要Agent基于原资料提交assessment.json，或通过agent-submit接收其评估数据。']}
    context = debug_flow.source_context(data)
    intake_origin=data['run'].get('agent_intake_origin',{})
    original_context=intake_origin.get('context',original_context)
    original_run_hash=intake_origin.get('run_hash',original_run_hash)
    def scope(row):
        override = row.get('scope') or {}
        if isinstance(override, str):
            return {'subject': {'source_object_ref': None, 'object_kind': 'unconfirmed_object'},
                    'time': {'kind': 'unknown', 'start': None, 'end': None},
                    'scenario': {'code': 'unspecified'}, 'agent_scope_text': override}
        oid = override.get('object_id', proposal['object_id'])
        obj=next(o for o in data['doc']['objects'] if o['id']==oid)
        return {'subject': {'source_object_ref': 'source-object:'+data['document']+':'+oid,
                            'object_kind':obj.get('type','unconfirmed_object')},
                'time': copy.deepcopy(override.get('time', proposal['time'])),
                'scenario': copy.deepcopy(override.get('scenario', proposal['scenario']))}
    case_scope = scope({})
    case_id = identity('agentcase_', {'source':context,'proposal':proposal,'bridge':debug_flow.BRIDGE_REVISION})
    api = api if api is not None else debug_flow.service(paths)
    try:
        existing = api.get_case(case_id)
        return {'case':existing['case'],'reused':True,'new_model_requests':0}
    except KeyError: pass
    originals = {r['id']: r for r in data['raw']}
    source_path = str(data['source'].relative_to(paths.root))
    source_rows, contexts, facts, mapping, bindings = [], [], [], [], []
    for row in proposal['inputs']:
        related = [originals[i] for i in row.get('fact_ids',[])]
        src = {'record_ref_id':identity('agentsource_',{'context':context,'input':row}), 'source_path':source_path,
               'snapshot_sha256':data['sha256'],'collection':'agent_assessment_submission','record_id':row['id'],
               'original_record':copy.deepcopy(row),'original_facts':related,'recognition_context':context}
        spans = []
        for f in related:
            evidence = f.get('evidence', [])
            for e in evidence if isinstance(evidence, list) else [evidence]:
                spans.append({'quote':e['quote'],'file_page_1_based':e['physical_page'],'locator':e['locator'],'source_fact_id':f['id']})
        ctx = {'context_id':identity('agentctx_',src),'source_path':source_path,'source_sha256':data['sha256'],
               'spans':spans,'table_context':{},'recognition_context':context}
        conflicted = row.get('status') == 'conflict' or any(f.get('model_status', f.get('status')) in {'conflict','unclear','partial'} for f in related)
        unresolved_scope = isinstance(row.get('scope'), str) and bool(row['scope'])
        value,normalization,transformations=normalized_value(row['variable'],row.get('value'))
        fact = fact_record(row['variable'], value, scope(row), source_refs=[src['record_ref_id']],
            evidence_refs=[ctx['context_id']],origin='agent',semantic=row.get('semantic','observation'),
            role=row.get('role','observation'),source_standard=row.get('source_standard','unknown'),
            support='unchecked' if conflicted or unresolved_scope else 'model_supported',feature=row.get('feature'),normalization=normalization)
        # Recognition and semantic interpretation are model proposals, never an
        # independently verified quote or professional endorsement.
        fact.update(basis_status='conditional',agent_status=row.get('status','proposed'),agent_reason=row.get('reason',''),
                    recognition_ref={'context':context,'record_id':row['id'],'fact_ids':row.get('fact_ids',[])},
                    normalization_receipt={'original_value':row.get('value'),'transformations':transformations})
        fact['fact_id'] = identity('agentfact_',fact)
        mapping.append({'id':row['id'],'accepted':True,'reason':'agent_submitted_data', 'typed_fact_id':fact['fact_id'],
            'typed_variable':row['variable'],'typed_value':value,'scope':fact['scope'],'basis_status':'conditional',
            'normalization_status':normalization,'transformations':transformations,
            'source_role':row.get('role'),'original_proposal':row,'source_fact_ids':row.get('fact_ids',[]),
            'source_support_note':'Agent自主提交；引用已定位，语义及专业适用性仍待复核。'+('存在源冲突，未自动采用。' if conflicted else '')+('范围为文字描述，原样接收；对象、时期与工况待结构化，未自动采用。' if unresolved_scope else '')})
        source_rows.append(src);contexts.append(ctx);facts.append(fact)
        bindings.append({'binding_id':identity('agentbinding_',{'source':src,'scope':case_scope}),'source_object_ref':proposal['object_id'],
                         'selected_target_ref':case_scope['subject']['source_object_ref'],'mapping_status':'agent_proposed',
                         'basis_refs':[ctx['context_id']],'original_object_relations':data['doc'].get('objects',[])})
    lineage = {'source_context':context,'original_context':original_context,'source_sha256':data['sha256'],
        'recognition_run_hash':data['run_hash'],'original_recognition_run_hash':original_run_hash,'history_id':meta['id'],
        'history_manifest_sha256':meta['manifest_sha256'],'source_name':data['doc'].get('name',data['source'].name),
        'subject_id':selected,'subject_label':proposal.get('label',proposal['object_id']),'binding_status':'agent_proposed',
        'bridge_revision':debug_flow.BRIDGE_REVISION,'created_at':datetime.now(timezone.utc).isoformat(),'new_model_requests':0,
        'agent_run_id':data['run'].get('run_id')}
    case = {'case_id':case_id,'contract_version':CONTRACT_VERSION,'target_ref':case_scope['subject']['source_object_ref'],
        'scope':case_scope,'resolution_version':1,'fact_ids':[f['fact_id'] for f in facts],
        'context_ids':[c['context_id'] for c in contexts],'source_hashes':{source_path:data['sha256']},'selections':{},
        'hazard_type_supported':proposal['applicability']['landslide'],'recognition_lineage':lineage,
        'source_coverage':'agent_submitted_selected_pages_pending_review',
        'agent_proposal':proposal,'agent_submission':data['run'].get('assessment_submission'),
        'debug_raw_records':[{'id':r['id'],'record_type':'agent_recognition','original_record':r} for r in data['raw']],
        'debug_mapping':mapping,'debug_rule_snapshot':copy.deepcopy(api.rules),'debug_workspace':True,'created_at':lineage['created_at']}
    with api.store.transaction() as db:
        for table, rows in [('source_record',source_rows),('evidence_context',contexts),('identity_binding',bindings),('fact_assertion',facts),('assessment_case',[case])]:
            for row in rows: api.store.put(table,row,db)
    result = api.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':case_id,'expected_resolution_version':1})
    return {'case':case,'result':result,'reused':False,'new_model_requests':0}


def submit(paths, request):
    with _SUBMIT_LOCK:
        return _submit(paths, request)


def _submit(paths, request):
    """Explicit HTTP/file intake. Persist one immutable history per document."""
    from . import debug_flow
    from ..debug_history import save_snapshot
    data = debug_flow.source_data(paths, {**request, 'route':'agent'})
    if request.get('expected_recognition_hash') and request['expected_recognition_hash'] != data['run_hash']:
        raise ValueError('识别结果已变化，请刷新后提交')
    submission = copy.deepcopy(request.get('submission', data['run'].get('assessment_submission')))
    if submission is None: raise ValueError('Agent尚未提交assessment.json；可先接收旧识别事实，等待自主评估数据')
    validate_submission(submission, data['run'])
    cases = [c for c in submission['cases'] if c['document_id'] == data['document']]
    key=identity('intake_',{'run_id':data['run'].get('run_id'),'document':data['doc'],
                          'submission':submission,'source':data['sha256'],'bridge':debug_flow.BRIDGE_REVISION})
    receipt_path=paths.output_dir/'assessment-debug/intake'/f'{key}.json'
    cached=read_json(receipt_path)
    if cached:
        from ..debug_history import history_store
        history_store(paths,'agent','history-'+cached['history_id'])
        for row in cached['cases']: debug_flow.flow(paths,row['case_id'])
        return {**cached,'reused':True}
    meta = save_snapshot(paths, route='agent',run_id=data['run_id'],document=data['document'],
                         label='Agent国标提交 · '+data['doc'].get('name',data['document']),agent_submission=submission,
                         agent_intake_origin={'context':debug_flow.source_context(data),'run_hash':data['run_hash']})
    frozen = {**request,'route':'agent','run_id':'history-'+meta['id'],'document':meta['document_id']}
    results = []
    for proposal in cases:
        made = debug_flow.prepare(paths, {**frozen,'subject_id':proposal['id'],'expected_recognition_hash':None})
        results.append({'case_id':made['case']['case_id'],'proposal_id':proposal['id'],'label':proposal.get('label',proposal['object_id']),
                        'document_id':data['document'],'url':'/web/assessment-debug.html?case='+made['case']['case_id'],
                        'workflow_status':made.get('result',{}).get('workflow_status')})
    receipt={'cases':results,'history_id':meta['id'],'new_model_requests':0,'formal_assessment_enabled':False}
    write_json(receipt_path,receipt)
    return receipt
