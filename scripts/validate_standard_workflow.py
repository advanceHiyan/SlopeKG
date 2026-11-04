"""Exercise the single standard workflow over actual saved reports and HTTP."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
from http.server import ThreadingHTTPServer
import html
import json
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from slopekg.config import PATHS
from slopekg.standard_assessment.contract import CONTRACT_VERSION, encoded


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir',type=Path,default=ROOT/'.private/reviews/20261031_standard_workflow')
    p.add_argument('--report-dir',type=Path,default=ROOT/'outputs/20261031_国标风险评估贯通')
    p.add_argument('--without-model',action='store_true')
    p.add_argument('--with-model',action='store_true',help='Explicitly send selected source quotes to the configured external model')
    p.add_argument('--port',type=int,default=0)
    p.add_argument('--serve',action='store_true',help='Keep local server for reviewing the resulting cases')
    args=p.parse_args();args.work_dir.mkdir(parents=True,exist_ok=True);args.report_dir.mkdir(parents=True,exist_ok=True)
    import slopekg.server as server_module
    original=server_module.PATHS
    server_module.PATHS=replace(PATHS,output_dir=args.work_dir)
    class Handler(server_module.SlopeKGHandler):
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    receipts=[];cases=[]
    def call(op,body=None):
        req=Request(origin+'/api/standard-assessment/'+op,
                    data=encoded(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=150) as response:status=response.status;result=json.load(response)
        except HTTPError as err:status=err.code;result=json.load(err)
        receipts.append({'operation':op,'status':status,'request':body,'response':result})
        if status!=200:raise RuntimeError(f'{op}: HTTP {status}: '+str(result))
        return result
    try:
        contract=call('contract')
        for label,target,period in [('G351报告调查状态','G351-AUTO-038',None),
                                    ('G344新滑坡报告状态','G344-AUTO-027',None),
                                    ('G351历史2016事件','G351-AUTO-038',{'kind':'event_period','raw':'2016年雨季'})]:
            print('Running '+label,flush=True)
            result=call('run',{'contract_version':CONTRACT_VERSION,'target_ref':target,'task':'landslide_current',
                               'use_model':args.with_model and not args.without_model,**({'time_selector':period} if period else {})})
            case=call('case?case_id='+result['case']['case_id'])
            assert case['results'] and case['case']['resolution_version']==result['case']['resolution_version']
            for f in case['facts']:
                if f['origin_kind']=='llm':assert f['review_status']!='accepted'
            cases.append({'label':label,'case':case,'result':result['result']})
            print(json.dumps({'label':label,'grade':result['result']['table17_result'],
                              'judgment':result['result']['partials']['professional_context_task']['execution_status']},ensure_ascii=False),flush=True)
        # Unsupported hazard must not be turned into a low landslide grade.
        other=call('run',{'contract_version':CONTRACT_VERSION,'target_ref':'G351-AUTO-045','use_model':False})
        assert other['result']['workflow_status']=='invalid_request' and other['result']['table17_result']['grade'] is None
        runs=[d for row in cases for d in row['case']['derivations'] if d['method_id']=='M-D-JUDGMENT']
        model_receipts=[r['receipt'] for r in runs if r.get('receipt')]
        summary={'date':'2026-10-31','contract':contract,'actual_report_cases':len(cases),
                 'external_model_requested':args.with_model and not args.without_model,
                 'actual_table17_candidates':sum(bool(c['result']['table17_result']['grade']) for c in cases),
                 'actual_human_reviews':0,'model_receipts':model_receipts,'model_run_statuses':[r['execution_status'] for r in runs],
                 'http_receipts':len(receipts),'isolated_database':str(args.work_dir/'assessment-pilot/assessment.sqlite'),
                 'case_urls':[origin+'/web/risk.html#case='+c['case']['case']['case_id'] for c in cases],
                 'unsupported_hazard_stopped':True,'synthetic_engineering_successes':0,
                 'boundary':'原报告资料回放；模型候选未经人工专业确认。软件合成输入单独由测试验证，不计真实工程成功。',
                 'code_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in list((ROOT/'src/slopekg/standard_assessment').glob('*.py'))+[ROOT/'web/risk.html',ROOT/'web/standard-assessment.js',ROOT/'data/standards/GBT40112-2021/landslide_current.review.json']}}
        for name,value in [('运行摘要.json',summary),('真实案例结果.json',cases),('HTTP回执.json',receipts)]:
            (args.report_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        esc=lambda x:html.escape(str(x))
        body='<h1>国标滑坡现状评估贯通结果</h1><p>真实资料 → 资料检索 → 国标上下文与模型建议 → 表15/17 → 数据库读回 → 待人工复核</p>'
        body+='<p>真实报告案例 '+str(len(cases))+' 个，表17候选结果 '+str(summary['actual_table17_candidates'])+' 个，实际人工专业确认 0 个。</p>'
        for row in cases:
            r=row['result'];j=r['partials']['professional_context_task']
            body+='<article><h2>'+esc(row['label'])+'</h2><p><strong>'+esc(r['table17_result']['grade'] or '未形成完整危险性等级')+'</strong> · '+esc(r['basis_status'])+' · 待人工复核</p>'
            body+='<p>模型判断：'+esc(j['execution_status'])+'</p><p>'+esc(j.get('detail',{}).get('rationale',''))+'</p>'
            body+='<ul>'+''.join('<li>'+esc(x['why_this_resolves_target'])+'</li>' for x in r['minimal_missing'])+'</ul>'
            body+='<p>案例ID：'+esc(row['case']['case']['case_id'])+'</p><details><summary>依据与结果</summary><pre>'+esc(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></details></article>'
        body+='<p>完整原句、模型请求与使用回执、推导步骤及数据库记录保存在 <a href="真实案例结果.json">真实案例结果</a>；<a href="运行摘要.json">运行摘要</a>；<a href="HTTP回执.json">HTTP回执</a>。</p>'
        (args.report_dir/'贯通结果.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>国标评估贯通</title><style>body{max-width:1050px;margin:auto;padding:30px;font:16px/1.8 "Microsoft YaHei",sans-serif;color:#233849;background:#f5f7fa}article{background:white;padding:22px;margin:20px 0;border:1px solid #dce3e9;border-radius:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}h1{font-size:28px}</style>'+body+'</html>',encoding='utf-8')
        print('REPORT '+str(args.report_dir/'贯通结果.html'),flush=True)
        if args.serve:
            print('LOCAL_SERVER '+origin,flush=True)
            worker.join()
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5);server_module.PATHS=original


if __name__=='__main__':main()
