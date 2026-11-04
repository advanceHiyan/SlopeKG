"""Run the isolated pilot on local REAL records, with no synthetic fallback."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import html
import json
from pathlib import Path
import sqlite3
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from slopekg.config import PATHS  # noqa: E402
from slopekg.standard_assessment import StandardAssessmentService  # noqa: E402
from slopekg.standard_assessment.adapters import read_json, sha  # noqa: E402
from slopekg.standard_assessment.contract import CONTRACT_VERSION, VersionConflict, encoded  # noqa: E402


def monitoring_audit():
    catalog_path = PATHS.monitoring_dir/'catalog.json'
    catalog = read_json(catalog_path)
    database = (catalog_path.parent/catalog['database']).resolve()
    database.relative_to(PATHS.monitoring_dir.resolve())
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        count = db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
        columns = [r[1] for r in db.execute('PRAGMA table_info(observations)')]
    return {'path':str(database.relative_to(ROOT)),'sha256':sha(database),'actual_rows':count,'columns':columns,
            'site_slope_id':catalog.get('site',{}).get('slope_id'),
            'connected_to_case':False,'reason':'已有监测库可读，但测点/边坡归属、单位、分量和基准待核；本次不把该站记录接到G351/G344。'}


def make_table(headers,rows):
    e=lambda x:html.escape(str(x))
    return '<div class="scroll"><table><tr>'+''.join('<th>'+e(h)+'</th>' for h in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+e(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</table></div>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir',type=Path,default=ROOT/'outputs/20261031_国标小范围贯通')
    parser.add_argument('--database',type=Path,default=PATHS.output_dir/'assessment-pilot/assessment.sqlite')
    parser.add_argument('--skip-http',action='store_true',help='Report explicitly that real socket verification was skipped')
    args = parser.parse_args()
    out = args.report_dir.resolve()
    out.mkdir(parents=True,exist_ok=True)
    curated = ROOT/'.private/knowledge/GBT40112-2021/assessment_v1/real_case_evidence.json'
    required = [PATHS.graph_json,curated,PATHS.output_dir/'knowledge-recognition/run.json',PATHS.monitoring_dir/'catalog.json']
    if any(not p.is_file() for p in required):
        raise SystemExit('Required real source snapshot missing; no synthetic fallback: '+str([str(p) for p in required if not p.is_file()]))
    source_manifest = {str(p.relative_to(ROOT)):sha(p) for p in required}
    for r in read_json(curated)['source_manifest']:
        source_manifest[r['path']] = sha(ROOT/r['path'])
    service = StandardAssessmentService(PATHS,database=args.database,curated_evidence=curated)
    receipts = []
    http_server = None
    def call_direct(op,payload):
        return getattr(service,op)(payload)
    def call_http(op,payload=None):
        url = 'http://127.0.0.1:'+str(http_server.server_port)+'/api/standard-assessment/'+op
        request = Request(url,data=encoded(payload).encode() if payload is not None else None,
                          headers={'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=30) as response:
                value, status = json.loads(response.read()),response.status
        except HTTPError as exc:
            value,status = json.loads(exc.read()),exc.code
        receipts.append({'operation':op,'status':status,'request':payload,'response':value})
        return status,value
    try:
        if not args.skip_http:
            from http.server import ThreadingHTTPServer
            import slopekg.server as server_module
            # The production handler and real requests, with only the pilot DB
            # location varied; graph/parsed sources still point at actual files.
            original_paths = server_module.PATHS
            server_module.PATHS = replace(PATHS,output_dir=args.database.parent.parent)
            expected = server_module.PATHS.output_dir/'assessment-pilot/assessment.sqlite'
            if expected.resolve()!=args.database.resolve():
                raise ValueError('--database for socket check must end in assessment-pilot/assessment.sqlite')
            class QuietHandler(server_module.SlopeKGHandler):
                def log_message(self,*_):
                    pass
            http_server = ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
            thread = threading.Thread(target=http_server.serve_forever,daemon=True)
            thread.start()
            status, prepared = call_http('prepare',{'contract_version':CONTRACT_VERSION,'target_ref':'G351-AUTO-038','task':'landslide_current'})
            assert status==200
            case351 = prepared['case']
        else:
            case351 = call_direct('prepare_assessment',{'contract_version':CONTRACT_VERSION,'target_ref':'G351-AUTO-038'})['case']
        evaluate=lambda c:service.evaluate_assessment({'contract_version':CONTRACT_VERSION,'case_id':c['case_id'],'expected_resolution_version':c['resolution_version']})
        before = evaluate(case351)
        assert before['table17_result']['grade'] is None
        assert len(before['partials']['source_adoptions'])==2
        assert len(before['partials']['stability_axes'])==2
        data351 = service.get_case(case351['case_id'])
        fs = next(f for f in data351['facts'] if f['variable_code']=='fs')
        context_request = {'contract_version':CONTRACT_VERSION,'case_id':case351['case_id'],'fact_ids':[fs['fact_id']]}
        if http_server:
            status,context = call_http('context',context_request)
            assert status==200
        else:
            context = service.get_fact_context(context_request)
        assert '安全系数' in context['contexts'][0]['table_context']['raw_table_text']

        case344 = service.prepare_assessment({'contract_version':CONTRACT_VERSION,'target_ref':'G344-AUTO-027'})['case']
        result344 = evaluate(case344)
        assert len(result344['partials']['normalizations'])==1
        historical = service.prepare_assessment({'contract_version':CONTRACT_VERSION,'target_ref':'G351-AUTO-038','time_selector':{'kind':'event_period','raw':'2016年雨季'}})['case']
        result_history = evaluate(historical)
        assert result_history['partials']['table15']['harm']=='危害小'
        assert not result_history['partials']['stability_axes']
        assert before['partials']['table15'] is None
        conflict_case = service.prepare_assessment({'contract_version':CONTRACT_VERSION,'target_ref':'G351-AUTO-045'})['case']
        conflict_result = evaluate(conflict_case)
        assert conflict_result['workflow_status']=='invalid_request'
        assert conflict_result['conflicts']

        bad = next(f for f in data351['facts'] if f['origin_kind']=='basic' and f['value']=={'kind':'text','text':'拉张裂缝宽约1.8m'})
        original = service.store.get('evidence_context',bad['evidence_refs'][0])
        assert '拉张裂缝' not in original['spans'][0]['quote']
        repair = next(c for c in service.store.rows('evidence_context') if c['context_id'] in case351['context_ids'] and c['spans'][0]['kind']=='page_text' and '拉张裂缝宽约1.8m' in c['spans'][0]['quote'])
        correction_request = {'contract_version':CONTRACT_VERSION,'case_id':case351['case_id'],'expected_resolution_version':1,
            'target_id':bad['fact_id'],'decision':'correct','evidence_refs':[repair['context_id']],
            'actor_kind':'assistant','reviewer_ref':'Codex/原件文字核对','reason':'原引用只有报告页眉；原件第41文件页正文明确含同一裂缝描述。只纠正证据绑定，不冒充人工专业确认。'}
        if http_server:
            status,correction = call_http('review',correction_request)
            assert status==200
            status,after = call_http('evaluate',{'contract_version':CONTRACT_VERSION,'case_id':case351['case_id'],'expected_resolution_version':2})
            assert status==200
            status,stale = call_http('evaluate',{'contract_version':CONTRACT_VERSION,'case_id':case351['case_id'],'expected_resolution_version':1})
            assert status==409 and stale['code']=='version_conflict'
            status,readback = call_http('case?case_id='+case351['case_id'])
            assert status==200
        else:
            correction = service.review_assessment(correction_request)
            after = evaluate(correction['case'])
            readback = service.get_case(case351['case_id'])
        assert set(before['trace_derivation_ids'])<=set(after['reused_derivation_ids'])
        assert not correction['review_decision']['professional_acceptance']
        assert any(not r['is_current'] for r in readback['results'])
        reopened = StandardAssessmentService(PATHS,database=args.database,curated_evidence=curated)
        assert reopened.get_case(case351['case_id'])['case']['resolution_version']==2

        knowledge = service.import_knowledge(['k_804fc537cfed34d841acc3','k_18cbf1d11f9edf3c31d89b','k_0ff19442eb227339f22413','k_88533f01ce566604009294','k_a8b9b42c69a7e365667c39','k_5a5f13b1cf28229a782d42'])
        assert len(knowledge['rows'])==6
        monitor = monitoring_audit()
        source_manifest[monitor['path']] = monitor['sha256']
        assert all(sha(ROOT/p)==h for p,h in source_manifest.items())
        results = {'G351_report_2021_before':before,'G351_report_2021_after':after,'G344_report_2021':result344,'G351_event_2016':result_history,'G351_conflict_non_landslide':conflict_result}
        assert all(r['table17_result']['grade'] is None for r in results.values())
        matrix = [
          {'scenario':'直接采用','status':'partial_real_verified','actual':'2项真实Fs原值直接采用，未重新求解。','not_verified':'缺少适用的既有development/harm双等级，未验证直接采用后完整查表成功。'},
          {'scenario':'推导','status':'partial_real_verified','actual':'两工况系数列对照；G344裂缝10–20cm→0.1–0.2m；2016死亡0按表15得到单项危害小。','not_verified':'未自动综合判断发育程度；没有真实完整表17等级。'},
          {'scenario':'缺失','status':'real_verified','actual':'2021评估缺发育程度和同期危害资料时保留部分结果，最终等级为空；2016数据未填入2021险情。','not_verified':'只检索选定来源，不声称整份报告或全库都没有。'},
          {'scenario':'冲突','status':'partial_real_verified','actual':'G351-AUTO-045既有识别高度46m/37m候选都入库；保留来源分歧且识别为崩塌，停止套滑坡流程。','not_verified':'属于原识别潜在对象/范围冲突，未确认物理事实冲突；没有真实表15指标跨级冲突样本。'},
          {'scenario':'人工纠正','status':'assistant_operation_verified_human_review_missing','actual':'通过同一纠正接口把真实裂缝描述从页眉引用改到正文，保留旧事实/旧结果；版本1请求被拒绝，未变的系数推导复用。','not_verified':'本次操作者是Codex，实际人工专业复核0；不能算人工签认通过。'}
        ]
        code_paths = [Path(__file__),ROOT/'src/slopekg/server.py',ROOT/'src/slopekg/config.py']+list((ROOT/'src/slopekg/standard_assessment').glob('*.py'))+[ROOT/'data/standards/GBT40112-2021/landslide_current.review.json']
        summary = {'contract_version':CONTRACT_VERSION,'run_id':case351['case_id'],'runtime_executed':True,'synthetic_case_count':0,
            'distinct_existing_slope_records':3,'assessment_cases':4,'real_table17_grade_count':0,'human_expert_reviews':0,'llm_calls':0,
            'case_ids_in_this_run':[case351['case_id'],case344['case_id'],historical['case_id'],conflict_case['case_id']],
            'database_total_cases_including_prior_runs':len(service.store.rows('assessment_case')),
            'http_socket_verified':not args.skip_http,'http_receipt_count':len(receipts),'database':str(args.database.resolve()),'database_sha256':sha(args.database),
            'source_files_unchanged':True,'sources':source_manifest,'code_snapshot':{str(p.relative_to(ROOT)):sha(p) for p in code_paths},
            'scenario_matrix':matrix,'source_adapters':{'basic_graph':'selected_records_connected','knowledge':'six_real_candidates_persisted_and_quarantined','visual':'not_connected_this_pilot','monitoring':monitor},
            'real_case_boundary':'历史资料回放和接口贯通，不是现场真值、正式评估报告或模型准确率。',
            'remaining': ['M-D-JUDGMENT仅形成真实上下文任务，未调用LLM/专业人员。','未找到同对象时期的可靠发育/危害双等级；不能演示真实完整表17成功。','人工复核与表15冲突的实际决定尚缺；不造样本补齐。','新手工事实入口、视读适配、专业稳定求解器及空间暴露统计未实现。']}
        for name,value in [('运行摘要.json',summary),('真实案例运行结果.json',results),('接口调用回执.json',receipts),('试点数据库导出.json',service.store.export())]:
            (out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        e=lambda t:html.escape(str(t))
        body = '<p>2026-10-31 · SlopeKG · 统一接口小范围运行</p><h1>数据接口已接通；真实完整危险性等级仍未跑通</h1><p class="lead">3条现有边坡记录、4个评估案例，均使用保存的真实识别与原件。最终表17等级为0个，真实人工专业签认为0；本报告没有用合成样例充当成功。</p>'
        body += '<div class="flow">现有主图谱 / 选定知联事实 / 已整理原件引用<br>↓ 适配：对象、时期、工况、原义、单位与证据<br>独立试点 SQLite：原始断言、推导、结果、纠正记录<br>↓ 国标表4系数列 / 表15 / 表17<br>部分结果 + 缺失 / 冲突 + 原文上下文<br>↓ 人工复核接口（本轮仅代理操作，未专业签认）</div>'
        body += '<h2>五类情况分别验证到哪里</h2>'+make_table(['情况','真实运行做到了什么','还没有验证什么'],[(r['scenario'],r['actual'],r['not_verified']) for r in matrix])
        body += '<h2>真实结果</h2>'+make_table(['案例','部分结果','最终危险性'],[
          ('G351 K1573+520–560，2021报告状态','Fs=1.071/Fst=1.15；Fs=1.011/Fst=1.10，系数列均为欠稳定；不能据此直接定发育程度','空：缺两项核心等级'),
          ('G344新滑坡，2021报告状态','Ⅰ区滑塌、Ⅱ区明显变形未滑塌分开保存；共述裂缝宽10–20cm归一为0.1–0.2m','空：缺可采用发育程度与危害输入'),
          ('G351，2016历史灾情','报告转述无人员伤亡→死亡0；表15该历史单项危害小；损失未知','空：无同口径发育程度；不能迁入2021险情'),
          ('G351 K1577+140–200，冲突入口','既有高度46/37m分歧保留；灾种为崩塌','停止套滑坡表')])
        body += '<h2>纠正记录可追溯</h2><p>旧引用只有“G351…勘察报告”页眉。纠正后引用实际第41文件页的“拉张裂缝宽约1.8m”正文。旧断言仍在库中，原始图谱未改。结果版本从1变为2；旧版请求返回409；两个未受影响的系数推导复用。</p>'
        body += '<h2>现有数据库和其他识别路线</h2><p>主图谱的选定记录已读入试点库。知联6条真实候选保留原角色、原文和模型核对状态，但“规范阈值人数”“节理/岩溶发育”没有被冒充滑坡实测输入。视读本轮未接。监测SQLite实读 '+e(monitor['actual_rows'])+' 行；站点与边坡未对应，未跨接到本次工点。</p>'
        body += '<h2>接口与下一步</h2><p>已增加 prepare、context、evaluate、review、case 接口，持久化到 <code>'+e(args.database)+'</code>。原P1–P4筛查接口继续独立。下一步实际缺的是表4/5定性判断及同范围后果资料，不需要把22项变量全部交给用户填写。</p>'
        body += '<p>原始识别与PDF哈希未变；本轮LLM调用0。软件单元测试中的匿名数值不进入这份真实案例报告。网络接口实测：'+e(not args.skip_http)+'。</p>'
        body += '<ul><li><a href="运行摘要.json">逐项状态、来源与程序快照</a></li><li><a href="真实案例运行结果.json">真实案例完整输入解析、推导与缺口</a></li><li><a href="接口调用回执.json">实际接口请求/响应回执</a></li><li><a href="试点数据库导出.json">数据库事实、原件上下文及纠正记录</a></li></ul>'
        css = 'body{max-width:1120px;margin:auto;padding:32px 24px 64px;font:16px/1.75 "Microsoft YaHei",sans-serif;color:#213047;background:#f6f8fc}h1{font-size:29px}h2{margin-top:32px}.lead{font-size:19px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;background:#fff}th,td{border:1px solid #d5dfe9;padding:12px;vertical-align:top;text-align:left}th{background:#e9eff6}.flow{text-align:center;background:#e3f1ee;padding:22px;margin:24px 0;border-radius:8px;font-weight:600}a{color:#12647d}'
        (out/'小范围贯通结果.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>国标小范围贯通结果</title><style>'+css+'</style></head><body>'+body+'</body></html>',encoding='utf-8')
        print(json.dumps({k:summary[k] for k in ['run_id','assessment_cases','real_table17_grade_count','human_expert_reviews','http_socket_verified','http_receipt_count','source_files_unchanged']},ensure_ascii=False))
    finally:
        if http_server:
            http_server.shutdown(); http_server.server_close(); thread.join(timeout=5)
            server_module.PATHS = original_paths


if __name__=='__main__':
    main()
