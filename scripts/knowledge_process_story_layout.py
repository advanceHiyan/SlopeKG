"""Case-first, static walkthrough: actual per-stage content is the main view."""
from html import escape as esc
import json
from pathlib import Path

CSS='''
:root{--ink:#23384d;--muted:#64788a;--line:#d9e2e9;--blue:#175c80;--red:#a64125;--bg:#edf2f6}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:var(--bg);font:15px/1.8 system-ui,"Microsoft YaHei",sans-serif}header{background:#16354c;color:white;padding:28px 4vw}header h1{font-size:29px;margin:8px 0}header a{color:#d8efff}main{max-width:1600px;margin:auto;padding:24px}h2{font-size:24px;line-height:1.5}h3{font-size:19px;margin:0 0 12px}h4{font-size:16px;margin:12px 0}p{margin:9px 0}a{color:var(--blue)}button{font:inherit;color:var(--blue);border:1px solid #afc4d4;background:white;border-radius:5px;padding:5px 9px;cursor:pointer}nav{display:flex;gap:10px;flex-wrap:wrap}.case-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:20px 0}.case-link{display:block;padding:17px 20px;background:white;border:1px solid var(--line);border-radius:7px;text-decoration:none}.case-link b{display:block}.case-link small{color:var(--muted)}.case{background:#fff;border:1px solid var(--line);border-radius:10px;padding:24px;margin:25px 0 40px;scroll-margin-top:15px}.case-head{border-bottom:2px solid #bdd2df;padding-bottom:18px}.case-head h2{margin:0}.scope{color:var(--muted);font-size:13px}.conclusion{padding:13px 16px;background:#fff5e9;border-left:4px solid #b76d30}.layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(0,1fr);gap:22px;align-items:start;margin-top:20px}.stages{min-width:0}.step{padding:22px 0;border-bottom:1px solid var(--line);scroll-margin-top:15px}.number{display:inline-block;padding:2px 8px;background:#e5f0f7;color:var(--blue);border-radius:5px;margin-right:7px}.lost h3,.misrouted h3,.absent h3{color:var(--red)}.lost,.misrouted{border-left:4px solid #c67143;padding-left:18px}.delta{background:#edf5fa;padding:13px 16px;margin:12px 0}.source-table{width:100%;border-collapse:collapse;font-size:13px}.scroll{overflow:auto}.source-table th,.source-table td{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:9px;overflow-wrap:anywhere}.source-table th{background:#edf4f8}.source-table td:first-child{min-width:110px}.source{border:0;padding:0;text-decoration:underline;overflow-wrap:anywhere;text-align:left;font-size:12px}.facts td{min-width:80px}.raw{white-space:pre-wrap;overflow-wrap:anywhere;max-height:560px;overflow:auto;background:#152b3c;color:#e7f1f7;padding:16px;font:12px/1.65 Consolas,monospace}.beforeafter{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.beforeafter>*{min-width:0}.box{border:1px solid var(--line);border-radius:6px;padding:12px;background:#f8fafc}.box h4{margin-top:0}.full{border:1px solid var(--line);padding:10px 13px;margin:10px 0;border-radius:5px}.full summary{cursor:pointer;font-weight:600}.small{font-size:13px;color:var(--muted)}.api-ref{font-size:13px;padding:7px 10px;background:#eff4f8;border-radius:4px;margin:8px 0}.pdf-aside{position:sticky;top:12px;border:1px solid var(--line);border-radius:7px;background:#f8fafc;padding:14px;max-height:96vh;overflow:auto}.pdf-aside h3{font-size:17px}.page-controls{display:flex;gap:7px;align-items:center;flex-wrap:wrap}select,input{font:inherit;max-width:100%;padding:5px;border:1px solid #b2c6d5;border-radius:4px}.pdf-frame{position:relative;line-height:0;margin-top:10px}.pdf-frame img{width:100%;height:auto}.pdf-frame svg{position:absolute;inset:0;width:100%;height:100%}.pdf-frame rect{fill:#ffb24822;stroke:#e28c20;stroke-width:.0025}.selected-source{padding:10px;background:#eaf2f8;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.warning{color:var(--red);padding:8px;background:#fff3e7}.empty{padding:10px;color:var(--red)}.checks{font-size:13px;background:#f0f5f8;padding:10px;overflow-wrap:anywhere}.request{margin:20px 0;padding:20px;border:1px solid var(--line);background:white;border-radius:8px;scroll-margin-top:12px}code{overflow-wrap:anywhere}.chain{padding:10px;background:#eaf2f8;overflow-wrap:anywhere}.totals-note{background:white;padding:20px;border:1px solid var(--line);border-radius:6px}dialog{width:95vw;max-width:1500px;height:94vh;border:1px solid #aebfcd;border-radius:8px;padding:12px}dialog::backdrop{background:#122635bb}.large-scroll{height:calc(100% - 70px);overflow:auto;background:#e9eef2}.large-img{display:block;max-width:none;height:auto}#largeText{margin:4px;font-size:13px}@media(max-width:1000px){main{padding:12px}.layout{grid-template-columns:1fr}.pdf-aside{position:static;max-height:none}.case{padding:16px}.beforeafter{grid-template-columns:1fr}}@media(max-width:650px){.case-links{grid-template-columns:1fr}header h1{font-size:24px}.source-table{min-width:490px}.step{padding:18px 0}}@media print{.pdf-aside,dialog,button{display:none}.layout{display:block}.raw{max-height:none;color:black;background:#f3f5f7}.case{break-before:page}.full{break-inside:auto}}
'''


def pretty(x):return json.dumps(x,ensure_ascii=False,indent=2)
def raw(x):return '<pre class="raw">'+esc(x if isinstance(x,str) else pretty(x))+'</pre>'
def details(label,x):return '<details class="full"><summary>'+esc(label)+'</summary>'+raw(x)+'</details>'


def source_link(story,key):
    return '<button class="source" data-story="'+esc(story['id'])+'" data-source="'+esc(key)+'">'+esc(key)+'</button>'


def sources_table(story,units):
    return '<div class="scroll"><table class="source-table"><tr><th>来源/页码</th><th>当时实际入链的文字</th><th>类型/置信度</th></tr>'+''.join('<tr><td>'+source_link(story,u['id'])+'<br>物理第'+str(u['page'])+'页</td><td>'+esc(u['text'])+'</td><td>'+esc(u.get('source',''))+'<br>'+esc(str(u.get('confidence','—')))+'</td></tr>' for u in units)+'</table></div>'


def facts_table(story,facts):
    if not facts:return '<p class="empty">本阶段保存结果中没有对应候选。</p>'
    return '<div class="scroll"><table class="source-table facts"><tr><th>对象/范围</th><th>字段和值</th><th>角色/限定</th><th>来源/状态</th></tr>'+''.join('<tr><td>'+esc(str(f.get('object',f.get('entity_id',''))))+'<br>'+esc(f.get('scope',''))+'</td><td><b>'+esc(f.get('field',''))+' = '+esc(str(f.get('value','')))+'</b> '+esc(f.get('unit',''))+'<br>unit_basis='+esc(f.get('unit_basis',''))+'</td><td>'+esc(f.get('role',''))+'<br>'+esc(f.get('qualifier',''))+'</td><td>'+', '.join(source_link(story,e['span_id']) for e in f.get('evidence',[]))+'<br>'+esc(f.get('status','模型返回时尚无本地状态'))+'<br>'+esc(', '.join(f.get('quality_issues',[])))+'</td></tr>' for f in facts)+'</table></div>'


def walk(x):
    if isinstance(x,dict):
        yield x
        for v in x.values():yield from walk(v)
    elif isinstance(x,list):
        for v in x:yield from walk(v)


def excerpt(story,value):
    """Readable selections, with the unchanged complete selection in details."""
    names=story['entity_names'];tracked=set(story['tracked_source_ids'])
    if isinstance(value,list) and value and all(isinstance(v,dict) and 'text' in v and 'page' in v for v in value):
        return sources_table(story,value)
    if isinstance(value,list) and value and all(isinstance(v,dict) and 'field' in v and 'value' in v and 'fact_id' not in v for v in value):
        return facts_table(story,value)
    if isinstance(value,list) and value and all(isinstance(v,dict) and 'fact_id' in v for v in value):
        return '<div class="scroll"><table class="source-table"><tr><th>绑定对象</th><th>采用的值</th><th>实际来源</th></tr>'+''.join('<tr><td>'+esc(v.get('case',''))+'</td><td>'+esc(v.get('field',''))+' = '+esc(str(v.get('raw_value','')))+'</td><td>'+esc(v.get('source_origin',''))+'<br>'+esc(v['fact_id'])+'<br>'+esc(v.get('binding_basis',''))+'</td></tr>' for v in value)+'</table></div>'
    entity_results=[o for o in walk(value) if isinstance(o.get('mentions'),list) and o.get('name') and o.get('salience')]
    if entity_results:
        return ''.join('<div class="box"><b>'+esc(e['name'])+'</b> · '+esc(e.get('kind',''))+'<p>'+esc(e.get('description',''))+'</p><p>'+''.join(source_link(story,m['span_id'])+'：'+esc(m.get('quote',''))+'<br>' for m in e['mentions'])+'</p></div>' for e in entity_results)
    bindings=[o for o in walk(value) if o.get('entity_id') and isinstance(o.get('span_ids'),list)]
    if bindings:
        related=[b for b in bindings if b['entity_id'] in story['entity_ids']]
        return '<p class="small">下面选出本例所追踪对象的实际绑定；完整返回仍保留在本阶段输出中。目标对象未列出，表示该返回没有它的绑定。</p><div class="scroll"><table class="source-table"><tr><th>实际绑定给谁</th><th>返回的来源ID（点击看文字）</th></tr>'+''.join('<tr><td>'+esc(names.get(v['entity_id'],v['entity_id']))+'</td><td>'+', '.join(source_link(story,k) for k in v['span_ids'])+'</td></tr>' for v in related)+'</table></div>'
    subjects=[o for o in walk(value) if o.get('id') in names and isinstance(o.get('allowed_span_ids'),list)]
    if subjects:
        return '<div class="scroll"><table class="source-table"><tr><th>实际发送的对象资料包</th><th>关键来源有无进入</th><th>完整允许来源</th></tr>'+''.join('<tr><td>'+esc(v.get('name',names[v['id']]))+'</td><td>'+''.join(source_link(story,k)+'：<b>'+('有' if k in v['allowed_span_ids'] else '没有')+'</b><br>' for k in tracked)+'</td><td><details><summary>展开'+str(len(v['allowed_span_ids']))+'条允许来源</summary>'+', '.join(source_link(story,k) for k in v['allowed_span_ids'])+'</details></td></tr>' for v in subjects)+'</table></div>'
    responses=[o for o in walk(value) if o.get('entity_id') and isinstance(o.get('facts'),list)]
    if responses:
        parts=[]
        for obj in responses:
            facts=obj['facts'];matched=[f for f in facts if any(e.get('span_id') in tracked for e in f.get('evidence',[]))]
            shown=matched if len(facts)>8 else facts
            parts.append('<div class="box"><b>'+esc(names.get(obj['entity_id'],obj['entity_id']))+'</b><p class="small">保存响应共'+str(len(facts))+'条事实；其中引用本例关键来源的有'+str(len(matched))+'条。不是“有返回”就算本例信息被保留。</p>'+facts_table(story,shown))
            if obj.get('unresolved'):parts.append('<p><b>响应中的unresolved原文：</b></p><ul>'+''.join('<li>'+esc(str(x))+'</li>' for x in obj['unresolved'])+'</ul>')
            parts.append('</div>')
        return ''.join(parts)
    if isinstance(value,dict) and value.get('id','').startswith('c') and isinstance(value.get('unit_ids'),list):
        ids=value['unit_ids'];lookup={u['id']:u for u in story['units']};positions=set()
        for k in tracked:
            if k in ids:
                pos=ids.index(k);positions.update(range(max(0,pos-2),min(len(ids),pos+3)))
        return '<p class="chain">实际分块 '+esc(value['id'])+'，包含物理页 '+esc(str(value.get('pages')))+'；共'+str(len(ids))+'条来源。</p><p>关键来源在块内的前后相邻内容：</p>'+sources_table(story,[lookup[ids[i]] for i in sorted(positions)])+'<details class="full"><summary>展开本块完整来源序列</summary><p>'+', '.join(source_link(story,k) for k in ids)+'</p></details>'
    if value==[]:return '<p class="empty">保存结果中没有对应记录；这不是程序返回了一条“空值事实”。</p>'
    # General small structures are still exact data, never invented outputs.
    return raw(value) if len(pretty(value))<2200 else '<p class="small">该结构较长，完整相关结构在下方展开；阶段结论与关键原文已在上方列出。</p>'


def api_id(story,call):return 'api-'+story['dataset_key']+'-'+call['key']


def step_html(story,step,index):
    ident='step-'+story['id']+'-'+str(index)
    refs=''.join('<div class="api-ref">本步骤关联的保存请求：#'+str(c['ordinal'])+' '+esc(c['stage'])+' · '+str(c['prompt_chars'])+'字符 · '+str((c.get('usage') or {}).get('total_tokens','?'))+' tokens · <a href="#'+api_id(story,c)+'">查看当时完整输入和处理后响应</a></div>' for c in step['calls'])
    result=excerpt(story,step['after'])
    checks='<div class="checks"><b>本页依据保存材料核对：</b>'+esc(pretty(step['check']))+'</div>' if step['check'] else ''
    return '<section id="'+ident+'" class="step '+esc(step['verdict'])+'"><h3><span class="number">'+str(index)+'</span>'+esc(step['name'])+'：'+esc(step['title'])+'</h3><p class="delta">'+esc(step['explanation'])+'</p><p class="small">追踪的原始来源（对照用；是否实际进入本阶段，以保存请求与下方结果为准）：</p>'+sources_table(story,step['source_units'])+'<h4>这一阶段实际得到的相关结果</h4>'+result+checks+refs+details('展开相关输入/前态（来源记录或请求节选）',step['before'])+details('展开相关输出/后态（保存结果节选，字段不截断）',step['after'])+'</section>'


def aside_html(story):
    first=story['steps'][0]['source_units'];key=next((u for u in first if u['id'] in story['tracked_source_ids']),first[0]);page=key['page']
    image=next(p for p in story['page_images'] if p['page']==page)
    box=key.get('bbox');svg=''
    if box:svg=f'<rect x="{box[0]}" y="{box[1]}" width="{box[2]-box[0]}" height="{box[3]-box[1]}"/>'
    return '<aside class="pdf-aside" id="preview-'+story['id']+'"><h3>原件与当前来源</h3><div class="page-controls"><label>原件物理页 <select data-page-story="'+story['id']+'">'+''.join('<option value="'+str(p['page'])+'" '+('selected' if p['page']==page else '')+'>'+str(p['page'])+'</option>' for p in story['page_images'])+'</select></label><button data-large="'+story['id']+'">放大原页</button><a class="pdf-link" href="'+story['source_pdf']+'#page='+str(page)+'">打开PDF</a></div><p class="small page-status">正在看第'+str(page)+'页；共'+str(story['source_pages'])+'页。点击来源ID切换到对应页和框。</p><p class="selected-source">'+esc(key['id']+'：'+key['text'])+'</p><div class="pdf-frame"><img src="'+image['url']+'" alt="原始PDF物理第'+str(page)+'页"><svg viewBox="0 0 1 1" preserveAspectRatio="none">'+svg+'</svg></div><p class="small">框是保存的来源位置；表格行有时共用整张表框，不能据此声称行级定位精度。</p></aside>'


JS=r'''
'use strict';
const P=window.KNOWLEDGE_PROCESS_STORIES.stories,byId=new Map(P.map(s=>[s.id,s]));
const previewState=new Map(),gen=new Map();
function preview(id,page,source){const s=byId.get(id),host=document.getElementById('preview-'+id),p=s.page_images.find(p=>p.page===Number(page));if(!p)return;const n=(gen.get(id)||0)+1;gen.set(id,n);const img=host.querySelector('img'),status=host.querySelector('.page-status');img.hidden=true;status.textContent='正在加载物理第'+page+'页…';host.querySelector('svg').innerHTML='';host.querySelector('select').value=String(page);host.querySelector('.pdf-link').href=s.source_pdf+'#page='+page;host.querySelector('.selected-source').textContent=source?source.id+'：'+source.text:'手动翻到第'+page+'页；此时未指定该页来源框。';img.onload=()=>{if(gen.get(id)!==n)return;img.hidden=false;img.alt='原始PDF物理第'+page+'页';status.textContent='已加载物理第'+page+'页，共'+s.source_pages+'页。';const b=source?.bbox;if(b&&b.length===4&&b.every(Number.isFinite)&&b[2]>b[0]&&b[3]>b[1])host.querySelector('svg').innerHTML=`<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}"/>`;else if(source)status.textContent+='该来源未提供可用的框。'};img.onerror=()=>{if(gen.get(id)===n)status.textContent='原页加载失败；可用打开PDF核对。'};img.src=p.url;previewState.set(id,{page,source,url:p.url});}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.source){const s=byId.get(b.dataset.story),u=s.units.find(u=>u.id===b.dataset.source);if(u)preview(s.id,u.page,u);}if(b.dataset.large){const id=b.dataset.large,s=byId.get(id),state=previewState.get(id)||{page:s.steps[0].source_units[0].page};const p=s.page_images.find(p=>p.page===Number(state.page));const img=document.getElementById('largeImage');img.src=p.url;document.getElementById('largeText').textContent=s.title+' · 物理第'+state.page+'页';document.getElementById('largeZoom').value='100';img.style.width='100%';document.getElementById('pageDialog').showModal();}if(b.id==='closeLarge')document.getElementById('pageDialog').close();});
document.querySelectorAll('[data-page-story]').forEach(el=>el.addEventListener('change',()=>preview(el.dataset.pageStory,Number(el.value),null)));
document.getElementById('largeZoom').addEventListener('input',e=>{document.getElementById('largeImage').style.width=e.target.value+'%'});
P.forEach(s=>{const u=s.steps[0].source_units.find(u=>s.tracked_source_ids.includes(u.id))||s.steps[0].source_units[0];previewState.set(s.id,{page:u.page,source:u});});
'''


def reading_units_html(out,stories):
    """Explain actual source granularity using the preserved open request."""
    story=next(s for s in stories if s['id']=='g209-height')
    call=story['steps'][4]['calls'][0]
    saved=json.loads((out/call['raw_url']).read_text(encoding='utf-8'))
    chunk=next(c for c in saved['context']['chunks'] if any(u['id']=='p21b8t0' for u in c['spans']))
    lookup={u['id']:u for u in story['units']}
    keys={'p21b8t0','p21b14t0','p21b10t0','p21b12t0','p21b13t0','p21b15t0'}
    spans=[u for u in chunk['spans'] if u['id'] in keys]
    assert len(spans)==len(keys)
    for span in spans:assert span['text']==lookup[span['id']]['text']
    rows=''.join('<tr><td>'+source_link(story,u['id'])+'</td><td>'+('右侧危岩介绍' if lookup[u['id']]['bbox'][0]>.5 else '左侧锚杆说明')+'</td><td>'+esc(u['text'])+'</td></tr>' for u in spans)
    return ('<section class="case" id="readingUnits"><h2>文字为什么一行一行：来源单元、段落与chunk不是一回事</h2>'
        '<p>PDF原生文字块可能只有一行，OCR通常按检测框保留短行，表格按行保留单元格和表头。展示每条来源单独占一行，方便原页定位；模型一包通常读取许多来源，不是每行单独识别。</p>'
        '<p>当前默认单元长文本约1400字符才切分，初始chunk按2800字符累加、重叠1个单元；没有先完整恢复栏目和段落。当前合并来源后按全页y/x排序，双栏可能交错。应保留原始来源ID，同时补足上层阅读结构。</p>'
        '<p>下面直接按保存的G209归属请求顺序取出6条来源：'+esc(chunk['id'])+'实际包含'+str(len(chunk['spans']))+'条来源。物理第21页（印刷页13）左侧锚杆公式插入右侧危岩段落；这是当时模型输入里的顺序，不是展示临时拆出来的。</p>'
        '<div class="scroll"><table class="source-table"><tr><th>来源</th><th>原页区域</th><th>实际请求中的文字，保持相对顺序</th></tr>'+rows+'</table></div>'
        '<p class="small">该历史请求没有bbox；当前wire_source已附带bbox，需区分版本。此处是历史真实阅读顺序证据，没有重新运行识别，也不能据此证明28m遗漏只有这一个原因。</p>'
        '<p><a href="#'+api_id(story,call)+'">查看这次完整保存请求</a> · <a href="#case-g209-height">继续追踪28m在哪一步丢失</a> · <a href="../../docs/知联调试交接.md">交接说明与修复边界</a></p></section>')


def write_html(out,payload):
    stories=payload['stories'];links=''.join('<a class="case-link" href="#case-'+s['id']+'"><b>'+esc(s['title'])+'</b><span>'+esc(s['question'])+'</span><br><small>'+esc(s['conclusion'])+'</small></a>' for s in stories)
    articles=[];calls={}
    for s in stories:
        nav='<nav>'+''.join('<a href="#step-'+s['id']+'-'+str(i)+'">'+str(i)+' '+esc(t['name'])+'</a>' for i,t in enumerate(s['steps'],1))+'</nav>'
        meta=('虚构开放隔离资料' if s['kind']=='synthetic_open_archive' else '真实G209，同源选6页历史实测')+'；原件'+str(s['source_pages'])+'页，实际读取物理页'+str(s['processed_pages'])
        articles.append('<article class="case" id="case-'+s['id']+'"><div class="case-head"><h2>'+esc(s['title'])+'</h2><p><b>要查的问题：</b>'+esc(s['question'])+'</p><p class="scope">'+esc(meta)+'</p><p class="conclusion">'+esc(s['conclusion'])+'</p>'+nav+'</div><div class="layout"><div class="stages">'+''.join(step_html(s,t,i) for i,t in enumerate(s['steps'],1))+'</div>'+aside_html(s)+'</div>'+details('这一例的原件哈希、代码、模型参数和目标对象身份',{k:s[k] for k in ['source_sha256','producer','settings','entity_ids']})+'</article>')
        for step in s['steps']:
            for c in step['calls']:calls.setdefault(api_id(s,c),(s,c))
    request_html=[]
    for ident,(s,c) in calls.items():
        saved=json.loads((out/c['raw_url']).read_text(encoding='utf-8'))
        request_html.append('<section class="request" id="'+ident+'"><h3>'+esc(s['title'])+' · 保存回执#'+str(c['ordinal'])+' '+esc(c['stage'])+'</h3><p class="small">这是当时保存的输入和缓存解析/校验后响应。编号按保存回执序列，并发时不等于发送先后。供应商HTTP原始正文未保存；不把cache.answer冒充原文回包。<a href="'+c['raw_url']+'">下载该条完整JSON</a></p>'+details('本条实际参数、用量与回执',c)+details('完整保存输入对象（含当时模型配置及请求选项）',saved['input'])+details('完整system',saved['input']['system'])+details('完整prompt（包含当时发送的全部输入JSON）',saved['input']['prompt'])+details('完整解析/校验后响应（answer、attempts、receipt均保留）',saved['cache'])+'</section>')
    body='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>知联具体案例：逐步检查中间结果</title><style>'''+CSS+'''</style></head><body><header><div>SlopeKG · 知联 Knowledge · 具体案例流程检查</div><h1>一条具体信息，在哪一步保留、错归或丢失？</h1><p>每个例子按原件、解析、分块、发现、归属、资料包、重组、校验、最终结果逐步展开实际内容。API参数和用量附在关联步骤旁边。</p><nav><a href="知联流程详解.html">固定流程详解</a><a href="知联失败样例.html">固定失败入口</a><a href="附录_工作量与完整数据.html">辅助附录：全量记录与调用统计</a></nav></header><main><div class="case-links">'''+links+'''</div><p class="totals-note">这里是保存的历史过程，不是重新识别。正文中的解释与核对结论单独标出；相关输入/输出取自保存材料，完整字段可展开。隐藏和曾作为隐藏集的原件排除，本次模型调用0次。</p>'''+''.join(articles)+'''<section id="requestAppendix"><h2>按步骤引用的完整请求与响应材料</h2><p>前面的案例使用可读节选检查过程；这里保留对应完整保存内容，供你逐项debug。数据统计和全部其他资料转入辅助附录。</p>'''+''.join(request_html)+'''</section></main><dialog id="pageDialog"><div class="page-controls"><button id="closeLarge">关闭大图</button><label>缩放 <input id="largeZoom" type="range" min="100" max="250" value="100" step="25"></label><span id="largeText"></span></div><div class="large-scroll"><img id="largeImage" class="large-img" alt="原始PDF物理页大图"></div></dialog><script src="assets/process_stories/data.js"></script><script>'''+JS+'''</script></body></html>'''
    body=body.replace('<main><div class="case-links">','<main>'+reading_units_html(out,stories)+'<div class="case-links">',1)
    old=out/'多页与大文档调用审计.html'
    if old.is_file() and 'id="globalStats"' in old.read_text(encoding='utf-8'):
        (out/'附录_工作量与完整数据.html').write_text(old.read_text(encoding='utf-8'),encoding='utf-8')
    old.write_text(body,encoding='utf-8')
    (out/'具体案例全流程详解.html').write_text(body,encoding='utf-8')
