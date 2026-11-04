"""Self-contained local report for reconstructed reading, overlapping groups and claims."""
from html import escape
import json
from pathlib import Path
from .storage import read_json


def render_report(result,out):
    out=Path(out)
    def pretty(value):return escape(json.dumps(value,ensure_ascii=False,indent=2))
    def detail(title,value):return '<details><summary>'+escape(title)+'</summary><pre>'+pretty(value)+'</pre></details>'
    flow=('原页与文字联合恢复 → 有缺口才有限修复 → 全文可重合分组 → 每批提取后原页复核 → 跨组索引检查'
          if result.get('schema_version')=='document-v0.2' else '原页与文字联合恢复 → 可重合大区 → 分区审核 → 区内提取 → 跨区核对')
    body=['<header><h1>第四路 · 整读 Document</h1><p>'+flow+'</p>',
          '<p>这是运行记录。模型转录和同模型审核仍可能错误；执行完成不代表工程事实已确认。</p>',
          '<p>状态：'+escape(result['processing_status'])+' · 生成请求：'+str(result['summary']['calls'])+' · 缓存：'+str(result['summary']['cache_hits'])+'</p>',
          '<a href="run.json">完整运行JSON</a></header>']
    for doc in result['documents']:
        index={b['id']:b for b in doc['blocks']}
        body.append('<section><h2>'+escape(Path(doc['path']).name)+'</h2><p>'+escape(doc['status'])+'</p>')
        body.append(detail('本次输入、模型和范围',{'sha256':doc['source_sha256'],'selection':doc['selection'],'model':result['model'],'settings':result['settings']}))
        body.append('<h3>1 · 原页与恢复后的连续内容</h3>')
        for page in doc['pages']:
            body.append('<div class="reading"><a href="'+escape(page['image'],quote=True)+'" target="_blank"><img loading="lazy" src="'+escape(page['image'],quote=True)+'" alt="原始物理页'+str(page['page'])+'"></a><div><h4>物理页 '+str(page['page'])+'</h4>')
            for b in page.get('blocks',[]):
                body.append('<article id="'+escape(doc['id']+'_'+b['id'],quote=True)+'"><p class="meta">'+escape(b['id']+' · '+b['kind']+' · '+b['source_type'])+'</p><pre class="source">'+escape(b['text'])+'</pre>'+detail('来源与转录状态',{k:v for k,v in b.items() if k!='text'})+'</article>')
            body.append(detail('未读区域及原生文字清单',{k:page.get(k) for k in ('native_sources','excluded_sources','unread_regions','issues')})+'</div></div>')
        body.append('<h3>2 · 大区与重合来源</h3>')
        for group in doc['groups']:
            body.append('<article><h4>'+escape(group['name'])+' <small>'+escape(group['kind'])+'</small></h4><p>'+escape(group.get('reason',''))+'</p><p>')
            for key in group['source_block_ids']:body.append('<a href="#'+escape(doc['id']+'_'+key,quote=True)+'">'+escape(key)+'</a> ')
            body.append('</p>'+detail('完整资料组',group)+'</article>')
        body.append(detail('全文分区与采用方式',{'mode':doc.get('zone_review_mode'),'proposed':doc.get('proposed_plan'),'reviewed':doc.get('reviewed_plan')}))
        body.append(detail('资料组执行、来源修复与跨组审核', {k:doc.get(k) for k in ('workflow_complete','dossier_plan','completed_batches','source_repairs','dossier_reviews','cross_group_audit','repair_queue')}))
        body.append('<h3>3 · 提取结果与证据</h3><table><thead><tr><th>主体 / 范围</th><th>字段</th><th>值 / 单位</th><th>状态</th><th>证据及完整记录</th></tr></thead><tbody>')
        for f in doc['facts']:
            body.append('<tr><td>'+escape(f['subject'] or '主体未定')+'<br><small>'+escape(f['scope'])+'</small></td><td>'+escape(f['field'])+'</td><td>'+escape(f['value']+' '+f['unit'])+'</td><td>'+escape(f['status'])+'</td><td>'+detail('查看原文、归属及门控',f)+'</td></tr>')
        body.append('</tbody></table>'+detail('全部提取候选（含去重前版本）',doc['claims'])+detail('未决项与失败',{'errors':doc['errors'],'unresolved':doc['unresolved'],'review':doc.get('final_review')}))
        body.append('<h3>4 · 每次实际输入输出</h3>')
        for trace in read_json(out/'debug-trace.json',[]):
            if trace.get('document_id')!=doc['id']:continue
            key=trace['cache_key'];record=read_json(out/'cache'/(key+'.json'),{});request=read_json(out/'inputs'/(key+'.json'),{})
            body.append('<details><summary>'+escape(trace['stage']+' · '+trace['status'])+'</summary><p><a href="inputs/'+key+'.json">保存输入</a> · <a href="exchanges/'+key+'.json">原始HTTP请求/响应</a></p>'+detail('完整提示词和图像引用',request)+detail('保存的原始模型JSON与回执',record)+'</details>')
        body.append('</section>')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>第四路整读运行记录</title><style>
body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f2f4f6;color:#21313d}header,section{max-width:1500px;margin:24px auto;padding:28px;background:white;border-radius:12px}h1,h2,h3{color:#164959}.reading{display:grid;grid-template-columns:minmax(280px,42%) 1fr;gap:24px;align-items:start;margin:25px 0}.reading>a{position:sticky;top:16px}.reading img{width:100%;border:1px solid #d8e0e5}article{padding:14px;margin-bottom:14px;background:#f7f9fa;border-left:3px solid #688c95}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,Consolas,monospace;max-height:640px;overflow:auto}.source{font:16px/1.8 system-ui;max-height:none}.meta,small{color:#536675;font-size:13px}details{margin:12px 0}summary{cursor:pointer;color:#165f74}a{color:#126c89}table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #dae2e7;overflow-wrap:anywhere}th{background:#edf3f5}td:last-child,th:last-child{width:36%}@media(max-width:850px){.reading{display:block}.reading>a{position:static}header,section{padding:14px;margin:10px}table{font-size:13px}}</style>'''+''.join(body)+'</html>'
    (out/'report.html').write_text(html,encoding='utf-8')
