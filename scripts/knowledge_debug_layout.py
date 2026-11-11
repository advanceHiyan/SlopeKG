"""Offline UI for the stable Knowledge explanation and open failure reports."""
from html import escape
import json

CSS=r'''
:root{--bg:#eef2f6;--ink:#1f3047;--muted:#596b80;--line:#cfd9e4;--blue:#165ba6;--warn:#9c431a;--green:#176752}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.8 system-ui,"Microsoft YaHei",sans-serif}header{background:#172b46;color:white;padding:20px 28px}header h1{margin:4px 0;font-size:28px}header a{color:#d3e7ff}.sub{color:#c0d0e3;font-size:13px}.wrap{max-width:1500px;margin:auto;display:grid;grid-template-columns:210px minmax(0,1fr);gap:22px;padding:24px}aside{align-self:start;position:sticky;top:12px;max-height:95vh;overflow:auto}aside a{display:block;padding:7px 10px;border-left:3px solid transparent;font-size:14px}aside a:hover{border-color:var(--blue);background:white}main{min-width:0}section,.card{background:white;border:1px solid var(--line);border-radius:9px;padding:22px;margin:0 0 20px}h2{font-size:22px;line-height:1.4;margin:0 0 15px}h3{font-size:17px;line-height:1.5;margin:20px 0 10px}p{margin:9px 0}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.muted{font-size:13px;color:var(--muted)}.notice{border-left:5px solid var(--warn);background:#fff8f0}.good{border-left:5px solid var(--green)}.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.scroll{overflow:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #dde4ec;vertical-align:top}th{background:#edf3f9;position:sticky;top:0}code{background:#edf2f7;padding:1px 4px;border-radius:3px;overflow-wrap:anywhere}pre{background:#152438;color:#e4effc;padding:15px;overflow:auto;max-height:560px;font:12px/1.65 Consolas,monospace;tab-size:4;white-space:pre}pre.wrap{white-space:pre-wrap;display:block;overflow-wrap:anywhere;max-width:100%;padding:15px}.badge{display:inline-block;font-size:12px;background:#e9f1fa;border:1px solid #cbdcf0;padding:2px 8px;margin:2px 4px 2px 0;border-radius:15px;line-height:1.7}.bad{color:#9b321a;background:#fff1e7;border-color:#ecc2a7}.green{color:#175d48;background:#eaf6ee}.flow{display:flex;gap:8px;overflow:auto;padding:6px 0 14px}.flow button{min-width:155px;text-align:left;white-space:normal}.flow button.active{background:#1d5a98;color:#fff;border-color:#1d5a98}.stage{border-left:4px solid #4476a8}.meta{display:grid;grid-template-columns:160px minmax(0,1fr);gap:4px 14px;margin:8px 0}.meta dt{font-weight:700}.meta dd{margin:0;overflow-wrap:anywhere}.source-line{display:block}.source-line:target,.source-line.focus{background:#28496c}.ln{color:#90acc9;user-select:none;display:inline-block;min-width:42px}button,select,input,textarea{font:inherit;max-width:100%}button{background:#fff;color:#214969;border:1px solid #a9bed3;border-radius:5px;padding:7px 12px;cursor:pointer}button:hover{background:#eaf2fa}select,input,textarea{border:1px solid #afbed0;border-radius:5px;padding:7px 9px;background:white;color:#1e3047}textarea{width:100%;min-height:115px;resize:vertical}label{display:inline-flex;gap:7px;align-items:center;flex-wrap:wrap}.controls{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:12px 0}.controls input[type=search]{min-width:210px;flex:1}.statgrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.stat{padding:13px;background:#eef4fa;border-radius:6px}.stat b{display:block;font-size:23px}details{border:1px solid #dae2ec;border-radius:5px;padding:10px 13px;margin:10px 0}summary{cursor:pointer;font-weight:600}details pre{margin-bottom:0}.pdfarea{position:relative;background:#e1e5ea;width:100%;line-height:0}.pdfarea img{display:block;width:100%;height:auto}.pdfarea svg{position:absolute;inset:0;width:100%;height:100%}.pdfarea rect{fill:transparent;stroke:#227caf;stroke-width:1.5;cursor:pointer;vector-effect:non-scaling-stroke}.pdfarea rect:hover,.pdfarea rect.sel{fill:#f58c2935;stroke:#dc531e;stroke-width:3}.sourcecols{display:grid;grid-template-columns:minmax(250px,1fr) minmax(330px,1.15fr);gap:16px}.unitlist{max-height:540px;overflow:auto}.unit{padding:10px;border-bottom:1px solid #dae3ed;cursor:pointer}.unit:hover,.unit.sel{background:#e8f2fc}.unit .text{font-size:14px;white-space:pre-wrap}.boxnote{font-size:12px;color:#5c6e82;overflow-wrap:anywhere}.failure{border-left:4px solid #a34923;padding:12px 15px;background:#fff7ef;margin:12px 0}.case-title{margin-top:0}.selected{background:#e8f2fc}.empty{padding:15px;color:#687b8d}.nowrap{white-space:nowrap}.linkbtn{padding:0;border:0;background:none;color:var(--blue)}.four{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.four div{padding:12px;background:#f1f5fa;border-radius:5px}.issue{color:#9f3b17}.stickycase{position:sticky;top:0;z-index:4;padding:9px 12px;background:#e9eff7;border-bottom:1px solid #c5d2e2}.foot{font-size:13px;color:#62748a;padding:18px 0}ul{padding-left:22px}li{margin:6px 0}.hash{font:12px/1.6 Consolas,monospace;word-break:break-all}.pillrow a{display:inline-block;padding:6px 12px;margin:4px;background:#eaf1fa;border-radius:5px}.rawbox{max-height:620px}.question{font-weight:700;color:#8b411c}.printonly{display:none}@media(max-width:900px){.wrap{grid-template-columns:1fr;padding:12px;gap:12px}aside{position:static;max-height:none;display:flex;gap:5px;overflow:auto;white-space:nowrap}aside a{flex:0 0 auto}.grid2,.sourcecols{grid-template-columns:1fr}.statgrid,.four{grid-template-columns:repeat(2,minmax(0,1fr))}header{padding:18px 16px}header h1{font-size:25px}section,.card{padding:18px}.meta{grid-template-columns:115px minmax(0,1fr)}table{min-width:590px}.sourcecols table{min-width:0}pre{font-size:11px}.stickycase{position:static}}@media print{header{background:white;color:black}aside,.controls,button,.stickycase{display:none}.wrap{display:block;max-width:none;padding:0}section,.card{break-inside:avoid;border-radius:0}pre{white-space:pre-wrap;max-height:none;background:#f5f5f5;color:black}.scroll{overflow:visible}.printonly{display:block}.flow{display:block}.sourcecols{display:block}}
'''

CSS += 'p,dd,td,li{overflow-wrap:anywhere}.grid2>div,.sourcecols>div{min-width:0}.pdfviewport{overflow:auto;max-height:720px;border:1px solid #ccd7e3}'

COMMON_JS=r'''
const D=window.KNOWLEDGE_DEBUG;
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=x=>JSON.stringify(x,null,2);
const badge=(x,cls='')=>`<span class="badge ${cls}">${esc(x)}</span>`;
const raw=(title,obj)=>`<details><summary>${esc(title)}</summary><pre class="wrap">${esc(fmt(obj))}</pre></details>`;
function download(name,obj){const blob=new Blob([fmt(obj)],{type:'application/json;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1500);}
function sourcePick(name,line=1){const k=name.split('/').pop().replace('.py','');$('srcPick').value=k;drawSource();location.hash='source';setTimeout(()=>{const e=$('srcL'+line);if(e){e.classList.add('focus');e.scrollIntoView({block:'center'});}},30);}
function drawSource(){if(!$('srcCode'))return;const record=D.source.files[$('srcPick').value];$('srcMeta').innerHTML=`<code>${esc(record.path)}</code> · SHA-256 <span class="hash">${esc(record.sha256)}</span> · <a href="${esc(record.url)}">完整源码文件</a>`;$('srcCode').innerHTML=record.text.split('\n').map((s,i)=>`<span id="srcL${i+1}" class="source-line"><span class="ln">${i+1}</span>${esc(s)}</span>`).join('');}
document.addEventListener('click',e=>{const b=e.target.closest('[data-code]');if(b){e.preventDefault();const [file,func]=b.dataset.code.split('|');const r=D.source.files[file.split('/').pop().replace('.py','')];sourcePick(file,r?.functions?.[func]?.[0]||1);}});
function setupSource(){if(!$('srcPick'))return;$('srcPick').innerHTML=Object.entries(D.source.files).map(([k,r])=>`<option value="${k}">${esc(r.path)}</option>`).join('');$('srcPick').onchange=drawSource;drawSource();$('codeSearch').oninput=()=>{const q=$('codeSearch').value;document.querySelectorAll('.source-line').forEach(e=>e.classList.toggle('focus',q.length>0&&e.textContent.includes(q)));};}
function setupNotes(){if(!$('notes'))return;const key='slopekg-knowledge-debug-'+document.body.dataset.page;try{$('notes').value=localStorage.getItem(key)||'';}catch{}$('notes').oninput=()=>{try{localStorage.setItem(key,$('notes').value);}catch{}};$('exportNotes').onclick=()=>download('知联调试备注.json',{page:document.title,source_snapshot:D.source.snapshot_sha256,notes:$('notes').value});}
setupSource();setupNotes();
'''

FLOW_JS=r'''
function drawStage(n){const s=D.stages[n];document.querySelectorAll('#stageButtons button').forEach((b,i)=>b.classList.toggle('active',i===n));$('stageDetail').innerHTML=`<h3>${esc(s[1])} ${badge(s[7]?'可能调用模型':'本地处理',s[7]?'':'green')}</h3><dl class="meta"><dt>输入</dt><dd>${esc(s[2])}</dd><dt>实际处理</dt><dd>${esc(s[3])}</dd><dt>失败与边界</dt><dd>${esc(s[4])}</dd><dt>源码</dt><dd><button class="linkbtn" data-code="${esc(s[5]+'|'+s[6])}">${esc(s[5]+': '+s[6])}</button></dd></dl>`;}
$('stageButtons').innerHTML=D.stages.map((s,i)=>`<button data-stage="${i}">${esc(s[1])}<br><small>${s[7]?'模型 / 缓存':'本地'}</small></button>`).join('');$('stageButtons').onclick=e=>{const b=e.target.closest('[data-stage]');if(b)drawStage(+b.dataset.stage)};drawStage(0);
$('paramRows').innerHTML=Object.entries(D.source.defaults).map(([k,v])=>`<tr><td><code>${k}</code></td><td>${esc(fmt(v))}</td><td>${esc(D.source.bounds[k]?D.source.bounds[k].join(' … '):(typeof v==='boolean'?'布尔值':k==='review_mode'?'selective/all/none':'固定0'))}</td><td>${esc(D.source.param_notes[k]||'见源码')}</td></tr>`).join('');
$('profile').textContent=fmt(D.source.model);$('packages').textContent=fmt(D.source.packages);
$('prompts').innerHTML=Object.entries(D.source.prompts).map(([key,text])=>`<details><summary>${esc(key)} · ${[...text].length}字符 · 完整原文</summary><pre class="wrap">${esc(text)}</pre></details>`).join('');
$('fingerprints').textContent=fmt(D.source.fingerprint_scan);
const tableKey='knowledge_pipeline';$('snapshot').textContent=D.source.snapshot_sha256;$('generated').textContent=D.generated_at;
function count(){const system=$('systemText').value,prompt=$('promptText').value;const a=[...system].length,b=[...prompt].length,limit=D.source.defaults.max_prompt_chars;$('countResult').innerHTML=`系统 ${a.toLocaleString()} + 提示词/输入 ${b.toLocaleString()} = <b>${(a+b).toLocaleString()}</b> 字符 ${badge(a+b<=limit?'≤60000 可过字符门':'超60000 会在实发前失败',a+b<=limit?'green':'bad')}`;}
$('systemText').value=D.source.prompts.SYSTEM;$('promptText').value=D.source.prompts.ATTRIBUTE+'\n输入：'+JSON.stringify({registry:[],chunks:[]});$('systemText').oninput=count;$('promptText').oninput=count;count();
function estimate(){const nums=['dCalls','aCalls','sCalls','rCalls','uCalls','cacheCalls'].map(id=>Math.max(0,Number($(id).value)||0));const base=nums.slice(0,5).reduce((a,b)=>a+b,0);$('estimateResult').textContent=`计划阶段请求 ${base}，假设其中精确缓存命中 ${nums[5]}，预计新调用 ${Math.max(0,base-nums[5])}；如发生允许的瞬时重试，默认全程还最多追加4次且总数受64硬上限限制。这只是输入数字的算术，不是运行预测。`;}
for(const id of ['dCalls','aCalls','sCalls','rCalls','uCalls','cacheCalls'])$(id).oninput=estimate;estimate();
'''

CASE_JS=r'''
let ci=0,vi=0,selected='',unitPage=1,unitOffset=0,activeStage='units';
const C=()=>D.cases[ci],V=()=>C().variants[vi];
const stageKeys=[['units','输入与证据单元'],['discovery_batches','对象发现'],['attribution_batches','跨块归属'],['synthesis_jobs','知识重组'],['review_jobs','语义复核'],['recovery_jobs','一次查漏'],['facts','最终事实/关系']];
function initCase(){const c=C();$('caseTitle').textContent=c.title;$('caseMeta').innerHTML=`${esc(c.source_name)}<br>原件${c.original_pages}页；历史测试物理页 ${c.tested_original_pages.join('、')}。<br><span class="hash">原件SHA-256 ${esc(c.original_sha256)}</span>`;$('variant').innerHTML=c.variants.map((v,i)=>`<option value="${i}">${esc(v.label)}</option>`).join('');vi=0;initVariant();renderFailures();}
function initVariant(){const c=C(),v=V(),d=v.doc,r=v.run;selected='';unitOffset=0;$('variant').value=vi;
 $('selectedUnit').innerHTML='<p class="muted">点击当前运行的来源框、单元行或事实引用，查看其归属和输出。</p>';
 $('variantInfo').innerHTML=`${badge(r.schema_version)}${badge(r.processing_status,'bad')}${badge('历史/缓存记录，不是本次新测试')}<p><code>${esc(v.path)}</code><br><span class="hash">run SHA-256 ${esc(v.run_sha256)}</span></p><p>运行输入SHA-256 <span class="hash">${esc(d.source_sha256)}</span>；本次选择页 ${d.selection?.selected_pages?.join('、')||d.pages.map(x=>x.page).join('、')} / 输入PDF ${d.source_page_count}页。${v.source_url_note}</p>`;
 $('runStats').innerHTML=[['来源单元',d.units.length],['chunk',d.chunks.length],['focus对象',d.entities.length],['事实',d.facts.length]].map(([a,b])=>`<div class="stat">${esc(a)}<b>${b}</b></div>`).join('');
 $('settings').textContent=fmt({settings:r.settings,model:r.model,review_model:r.review_model,summary:r.summary,source_inventory:d.source_inventory,coverage:d.coverage,call_plan:d.call_plan});
 $('usageTable').innerHTML=`<table><tr><th>模型阶段</th><th>保存的回执记录数</th><th>缓存命中</th><th>新增回执tokens</th></tr>${Object.entries(v.stage_usage).map(([k,u])=>`<tr><td>${esc(k)}</td><td>${u.records}</td><td>${u.cache_hits}</td><td>${u.usage_tokens.toLocaleString()}</td></tr>`).join('')}</table><p class="muted">运行内部calls=${r.summary?.calls??'未保存'}，cache_hits=${r.summary?.cache_hits??'未保存'}。以上按本运行记录计算，不是供应商账单；缓存旧usage不重复计为新增。</p>`;
 $('downloads').innerHTML=`<a href="${esc(v.source_pdf_url)}">打开完整输入PDF</a><a href="${v.raw_url}">下载本运行完整JSON</a><a href="${v.requests_url}">下载全部已保存请求/响应JSON</a>`;
 $('stageTrack').innerHTML=stageKeys.map(([key,name])=>{const items=key==='units'?d.units:d[key]||[];const s=v.stage_counts[key];return `<button data-trace-stage="${key}" class="${key===activeStage?'active':''}">${esc(name)}<br><small>${items.length}项 ${s?esc(Object.entries(s).map(([k,v])=>k+':'+v).join(' / ')):''}</small></button>`}).join('');
 $('pdfPage').innerHTML=d.pages.map(p=>`<option value="${p.page}">运行页${p.page} / 原件物理${v.page_map[p.page]||p.page}页</option>`).join('');unitPage=d.pages[0]?.page||1;$('pdfPage').value=unitPage;drawPdf();drawUnits();renderStage();renderFacts();
 const keys=Object.keys(d).filter(k=>/diagnostic|quarantin|error|failure|accounting|explicit_span_owners|section_owners|directory_delivery/.test(k));$('allDiagnostics').innerHTML=keys.map(k=>raw(k,d[k])).join('')||'<p>该历史版本未保存此类诊断。</p>';
 $('allAssessments').innerHTML=(c.latest_review?raw('开放G359既有逐项复核（含完整/程序接纳分开统计）',c.latest_review):'')+raw('历史开放开发集逐项判定（AI原文核对，非专家签认）',c.judgments);
}
function renderStage(){const v=V(),d=v.doc;const titles=Object.fromEntries(stageKeys);document.querySelectorAll('[data-trace-stage]').forEach(e=>e.classList.toggle('active',e.dataset.traceStage===activeStage));
 const recs=activeStage==='units'?d.units:activeStage==='facts'?{facts:d.facts,relations:d.relations,object_pages:d.object_pages}:d[activeStage]||[];
 const rs=v.requests.filter(x=>({discovery_batches:'knowledge_discover',attribution_batches:'knowledge_attribute',synthesis_jobs:'knowledge_synthesize',review_jobs:'knowledge_verify',recovery_jobs:'knowledge_recover'}[activeStage]===x.stage));
 $('stageResult').innerHTML=`<h3>${esc(titles[activeStage]||activeStage)}</h3><p>这里显示运行文件中真实保存的阶段记录。completed仅代表该阶段契约/处理状态，不代表目标信息完整。</p>`+raw('完整阶段记录 / 输出结构',recs)+rs.map((x,i)=>`<details><summary>保存记录${i+1}（缓存键排序，非调用序号） · ${esc(x.stage)} · 完整输入${x.prompt_chars.toLocaleString()}字符 · ${esc(x.response.status||'未保存响应')}</summary><p class="hash">cache_key ${esc(x.cache_key)}</p><p>${esc(x.response_kind)}</p><p>提示词包含SYSTEM与本阶段指令；以下均可完整展开，未裁剪。</p>${raw('完整System字符串',x.input.system)}${raw('完整Prompt字符串（含全部本批来源）',x.input.prompt)}${raw('保存的响应、隔离项和回执',x.response)}</details>`).join('')+(!rs.length?'<p class="muted">本阶段没有对应已保存请求。可能是本地处理、未启用record_model_inputs、缓存复用未另存输入，或在发送前失败；仅凭此项不能断言未调用。</p>':'');
 $('allUsage').innerHTML=raw('按阶段汇总（记录数、缓存命中、新回执tokens）',v.stage_usage)+raw('本运行全部receipts',v.run.receipts||[])+(v.transport_records?raw('可用的原始transport顺序/状态记录：'+v.transport_path,v.transport_records):'<p class="muted">本版本目录没有单独transport顺序记录；不从缓存文件名伪造请求发生顺序。</p>');
}
function drawPdf(){const v=V(),p=v.images[String(unitPage)];if(!p)return;$('pageImage').src=p.url;$('pageImage').alt=`${C().source_name} 原件物理${p.original_physical_page}页`;
 $('pdfZoomArea').style.width=$('pdfZoom').value+'%';$('zoomLabel').textContent=$('pdfZoom').value+'%';$('fullPageImage').href=p.url;
 const units=v.doc.units.filter(u=>u.page===unitPage&&Array.isArray(u.bbox)&&u.bbox.length===4);
 $('boxes').innerHTML=$('showBoxes').checked?units.map(u=>{const b=u.bbox;return `<rect data-span="${esc(u.id)}" x="${b[0]*1000}" y="${b[1]*1000}" width="${(b[2]-b[0])*1000}" height="${(b[3]-b[1])*1000}" class="${u.id===selected?'sel':''}"><title>${esc(u.id+' '+u.text)}</title></rect>`}).join(''):'';
 $('pdfNote').textContent=`运行页${unitPage}，原件物理${p.original_physical_page}页。蓝框照录该运行保存的bbox；旧坐标误差/越界不会被这里的显示修正。点击框查看绑定和事实。`;
}
function drawUnits(){const q=$('unitSearch').value.toLowerCase(),type=$('sourceType').value;let units=V().doc.units.filter(u=>u.page===unitPage&&(!type||u.source===type)&&(!q||JSON.stringify(u).toLowerCase().includes(q)));
 const pageSize=35,max=Math.max(0,Math.ceil(units.length/pageSize)-1);unitOffset=Math.min(unitOffset,max);$('unitCount').textContent=`本页匹配${units.length}个单元；显示${unitOffset*pageSize+1}—${Math.min(units.length,(unitOffset+1)*pageSize)}（可翻页，未删除其余单元）`;
 $('unitList').innerHTML=units.slice(unitOffset*pageSize,(unitOffset+1)*pageSize).map(u=>`<div class="unit ${u.id===selected?'sel':''}" data-span="${esc(u.id)}"><b>${esc(u.id)}</b> ${badge(u.source)}<div class="text">${esc(u.text)}</div><div class="boxnote">bbox ${esc(fmt(u.bbox))} · confidence ${esc(u.confidence??'无')} ${u.geometry_issue?' · '+esc(u.geometry_issue):''}</div></div>`).join('')||'<p class="empty">本页无匹配单元。查看来源准备/环境失败或修改筛选条件。</p>';
}
function selectSpan(id){selected=id;const d=V().doc,u=d.units.find(u=>u.id===id);if(!u)return;unitPage=u.page;$('pdfPage').value=unitPage;drawPdf();drawUnits();const owners=d.entities.filter(e=>e.unit_ids?.includes(id));const facts=d.facts.filter(f=>f.evidence?.some(e=>e.span_id===id));const chunks=d.chunks.filter(c=>c.unit_ids.includes(id));
 $('selectedUnit').innerHTML=`<h3>${esc(id)}：从输入到输出</h3><p>归属对象${owners.length}个；引用本单元的最终事实${facts.length}条。不存在事实不代表原文不存在。</p>`+raw('完整来源单元',u)+raw('所属chunk及原始归属',chunks)+raw('已接收该来源的对象',owners)+raw('最终引用事实',facts);
}
function renderFacts(){const d=V().doc,q=$('factSearch').value.toLowerCase(),status=$('factStatus').value;const facts=d.facts.filter(f=>(!status||f.status===status)&&(!q||JSON.stringify(f).toLowerCase().includes(q)));$('factCount').textContent=`匹配${facts.length} / 全部${d.facts.length}条，按每个对象/字段/值/范围逐条展示。`;
 $('factRows').innerHTML=facts.map(f=>`<tr id="fact-${esc(f.id)}"><td><code>${esc(f.id)}</code><br>${esc(f.object)}<br><small>${esc(f.scope)}</small></td><td><b>${esc(f.field)}</b><br>${esc(f.value)} ${esc(f.unit)}<br><small>role=${esc(f.role)}；unit_basis=${esc(f.unit_basis)}<br>条件=${esc(f.condition)}；时间=${esc(f.time)}；限定=${esc(f.qualifier)}</small></td><td>${badge(f.status,f.status==='pending'?'bad':'green')}<br><small>${esc(f.reason)}<br>${esc((f.quality_issues||[]).join('；'))}</small></td><td>${(f.evidence||[]).map(e=>`<button class="linkbtn" data-span="${esc(e.span_id)}">${esc(e.span_id)} / 运行页${e.page}</button><br><small>${esc(e.quote)}</small>`).join('<hr>')}${raw('该事实完整JSON',f)}</td></tr>`).join('');
}
function renderFailures(){const c=C();let html='';
 if(c.id==='G359')html+=`<div class="failure"><b>图形漏项：六层厚度没有成为完整“层位—数值—单位”事实。</b><p>原件物理10页的图形已保留OCR原文。只识别到材料名或裸数值不能算完整图表数据点。可在事实搜索依次查“上面层”“基层”“厚度”；再在该页来源查数值，比较对象绑定和knowledge_synthesize响应。最新缓存对照仍6/13完整、5/13程序接纳。</p></div><div class="failure"><b>量义未拆开：数量表里的6cm仍在字段名称，value却是112.20m³。</b><p>这是工程量事实，不自动等同于独立厚度事实；搜索“中粒”查看完整输出。旧版设计量role与单位表头引用问题，可切换v3.3查看。</p></div><div class="failure"><b>花岗岩已返回，具体主体仍待审。</b><p>搜索“下伏基岩”：当前缓存事实保留原文与observation，但属于“工程地质条件”概念，门控显示concrete_case_requires_explicit_owner。不能把待审描述说成正式归属已解决。</p></div>`;
 if(c.id==='G209-report')html+=`<div class="failure"><b>漏在重组：255原文进入正确对象和模型输入，最终没有对应坡向事实。</b><p>这里不是“解析没读到255”的结论。打开专门追踪文件，沿original_value_units → object attribution → synthesis input → cached output → final_facts_with_255检查。历史运行属于v3.2，未据此声称当前v3.8仍以同样方式漏项。</p></div>`+raw('255全链路既有追踪（开放开发资料）',c.trace);
 if(c.id==='paper')html+=`<div class="failure"><b>原文跨行、公式字形与参数身份不等于一个字面子串。</b><p>历史候选包含阶段特征和公式，但部分引文跨行或数学字形与候选不同，程序未接纳；另有α位于未引用的单独span，确实缺证据。不能把所有pending统一当模型不会读，也不能放宽规则一律通过。切换到“最终事实”查看value_not_in_cited_source等具体原因和完整引文。</p></div>`;
 if(c.id==='guide')html+=`<div class="failure"><b>先分清本地环境失败与模型结果。</b><p>初次记录没有可用来源、0次API，不能拿空输出评价识别能力。默认显示环境恢复后的首轮真实调用；另一个版本保留失败现场。恢复后振动相关叙述跨相邻原文行，历史字面门控使候选待审，见逐项判定及事实引用。该案例只测物理12页，不能声称77页整本通过。</p></div>`;
 $('failures').innerHTML=html;
}
$('caseSelect').innerHTML=D.cases.map((c,i)=>`<option value="${i}">${esc(c.title)}</option>`).join('');$('caseSelect').onchange=()=>{ci=+$('caseSelect').value;initCase();};$('variant').onchange=()=>{vi=+$('variant').value;initVariant();};
$('stageTrack').onclick=e=>{const b=e.target.closest('[data-trace-stage]');if(b){activeStage=b.dataset.traceStage;renderStage();}};
document.addEventListener('click',e=>{const a=e.target.closest('[data-span]');if(a){selectSpan(a.dataset.span);if(!a.closest('#pdfPanel'))$('pdfPanel').scrollIntoView({block:'start'});}});
$('pdfPage').onchange=()=>{unitPage=+$('pdfPage').value;unitOffset=0;selected='';$('selectedUnit').innerHTML='<p class="muted">已切换页，请点选当前页来源查看对应记录。</p>';drawPdf();drawUnits();};$('showBoxes').onchange=drawPdf;$('pdfZoom').oninput=drawPdf;$('unitSearch').oninput=()=>{unitOffset=0;drawUnits();};$('sourceType').onchange=()=>{unitOffset=0;drawUnits();};$('unitPrev').onclick=()=>{unitOffset=Math.max(0,unitOffset-1);drawUnits()};$('unitNext').onclick=()=>{unitOffset++;drawUnits()};$('factSearch').oninput=renderFacts;$('factStatus').onchange=renderFacts;initCase();
$('generated').textContent=D.generated_at;
'''

def source_section():
    return '''<section id="source"><h2>源码浏览器：按真实行号定位</h2><p>本页随更新复制的源码快照。报告生成后若工作区再变更，快照不会自动变化；重新运行构建脚本后这两个固定HTML才更新。</p><div class="controls"><select id="srcPick" aria-label="选择源码"></select><input id="codeSearch" type="search" placeholder="在当前文件内高亮变量或函数" aria-label="查找源码"></div><p id="srcMeta" class="muted"></p><pre id="srcCode" class="rawbox"></pre></section>'''

def notes_section():
    return '''<section id="notesSection"><h2>你的debug备注</h2><p class="muted">仅保存在此浏览器本地，不自动回写项目。可导出后发给我；我据此核查并维护这两个HTML。备注不是识别输入。</p><textarea id="notes" aria-label="调试备注" placeholder="写阶段、参数、样例、span_id或fact_id，以及你认为应该如何处理。"></textarea><button id="exportNotes">导出备注JSON</button></section>'''


def guide_walkthrough(data):
    """Explain one preserved open prediction without rerunning recognition."""
    case=next(c for c in data['cases'] if c['id']=='guide')
    variant=next(v for v in case['variants'] if '环境恢复后' in v['label'])
    run,doc=variant['run'],variant['doc']
    target=next(f for f in doc['facts'] if f['id']=='k_f3d6b0d70c80205b76748b')
    entity=next(e for e in doc['entities'] if e['id']==target['entity_id'])
    units={u['id']:u for u in doc['units']}
    quotes=''.join('<tr><td>'+escape(k)+'</td><td>'+escape(units[k]['text'])+'</td><td>'+escape(str(units[k].get('confidence')))+'</td><td>'+escape(str(units[k]['bbox']))+'</td></tr>' for k in ['p12ocr8t0','p12ocr9t0','p12ocr10t0'])
    requests={r['stage']:r for r in variant['requests']}
    synth=requests['knowledge_synthesize']
    context=json.loads(synth['input']['prompt'].split('\n输入：',1)[1])
    returned=next(o for o in synth['response']['answer']['objects'] if o['entity_id']==target['entity_id'])
    raw_fact=next(f for f in returned['facts'] if f['value']==target['value'])
    preview=json.dumps(raw_fact,ensure_ascii=False,indent=2)
    calls=''.join('<tr><td>'+escape(stage)+'</td><td>'+str(len(requests[stage]['input']['system'])+len(requests[stage]['input']['prompt']))+'</td><td>'+str(requests[stage]['response']['receipt']['usage']['total_tokens'])+'</td></tr>' for stage in ['knowledge_discover','knowledge_attribute','knowledge_synthesize','knowledge_verify'])
    names='、'.join(e['name'] for e in doc['entities'])
    local_note='当前源码有相邻证据联合匹配；它与这份v3.2历史结果分开，不会回写旧预测。'
    if 'guide_local_check' in data:
        local_note+='本次把相同两条引用送入当前value_supported函数，返回'+str(data['guide_local_check']['value_supported'])+'。只验证该本地值匹配函数，不代表整条事实通过全部门控或整份资料复测。'
    return f'''<section id="guideWalkthrough" class="notice"><h2>跟着指南“振动监测”走完真实中间过程</h2>
<p>范围：原件77页，历史仅选物理第12页；v3.2保存的旧预测，不是重新调用。<a href="http://127.0.0.1:8766/web/recognition-debug.html?route=knowledge&amp;run=history-h_d9dc32600cf149389402fd36&amp;stage=gates&amp;q=%E6%8C%AF%E5%8A%A8%E7%9B%91%E6%B5%8B&amp;page=12">打开对应门控现场</a>。</p>
<ol><li><b>解析/OCR：</b>该页原生文字0；复用已有OCR的{len(doc['units'])}条文字，连页脚在内共{sum(len(u['text']) for u in doc['units'])}个字符。ocr_receipts为空，本次知联不是重新做了23次OCR；图像请求数也为0。</li>
<li><b>来源单元：</b>每条文字保留ID、页码、置信度和位置。这里的span是证据单元，不是完整段落。<div class="scroll"><table><tr><th>span_id</th><th>OCR原文（不补字）</th><th>置信度</th><th>归一化位置</th></tr>{quotes}</table></div></li>
<li><b>分块：</b>chunk_chars={run['settings']['chunk_chars']}；本页{len(doc['units'])}条、461字符都放在同一个c0001中。没有一行调一次大模型，没有发生跨分块归属验证。</li>
<li><b>对象发现，一次模型请求：</b>{escape(names)}。它们在此指南里都是concept，即监测主题/类别，并非五处真实工程边坡。另有2个support参考概念。</li>
<li><b>归属，一次模型请求：</b>c0001中的p12ocr8t0、p12ocr9t0、p12ocr10t0被一起归给“{escape(entity['name'])}”。这次对象和三条来源没有归错。</li>
<li><b>知识重组，一次模型请求：</b>将{len(context['subjects'])}个对象合批，传{len(context['source_spans'])}条去重来源；目标对象允许引用上述3条。保存的解析/规范化响应已经有下面这条候选，并在unresolved里说明按跨行拼接理解。不是模型漏提。<pre class="wrap">{escape(preview)}</pre></li>
<li><b>本地证据门控：</b>单独找p12ocr9t0，只有“振动监”；单独找p12ocr10t0，只有“测”。历史门控没把两行合成有效的“振动监测”支持，留下value_not_in_cited_source，将候选标为pending。候选仍在最终run里，并未删除。相同对象的“视频监测、微震监控等”在一行中完整出现，因此另一条能达到source_aligned。</li>
<li><b>选择性复核与输出：</b>第4次请求只复核“路堑边坡监测”的2条关系；未包含这条振动候选。因此4次请求没有把这个局部误拒修回来。全页最终{len(doc['facts'])}条事实、{len(doc['relations'])}条关系，事实8条source_aligned、9条pending；整体partial，不能称整页全部正确。</li></ol>
<div class="scroll"><table><tr><th>保存的历史模型步骤</th><th>system+prompt字符</th><th>历史usage tokens</th></tr>{calls}</table></div><p>4次历史请求，输出上限每次393216。缓存answer是解析/校验后的对象，供应商HTTP原始正文当时未保存。本次讲解和构建新增API为0。</p><p><b>当前与历史：</b>{escape(local_note)}</p></section>
<section id="ocrColumns"><h2>OCR行、双栏阅读顺序与当前缺口</h2>
<p><b>OCR不是把整份PDF当成一条长字符串读。</b>当前本地PaddleOCR接收整页或裁剪区域图片，返回多条文字框及文本/坐标/置信度，常接近一行，也可能是短片段。文字框、段落、chunk、API请求是四个不同层级。</p>
<pre class="wrap">普通双栏正文（示意，非真实资料测试）：
左栏                 右栏
左1                  右1
左2                  右2

应先恢复阅读顺序：左1 → 左2 → 右1 → 右2
按 y、x 全页排序： 左1 → 右1 → 左2 → 右2
风险：一个句子的上下文被另一栏插入，可能影响分块、对象发现和归属。</pre>
<p><b>当前源码确有这个缺口：</b>knowledge_document.py的page_units.sort先按框顶部y、再按左侧x排序，未先建立栏目/版面块阅读顺序。即使上游PDF文字块有reading_order，混入OCR和表格后仍被这次排序覆盖。保留bbox和后续相邻框校验，有助于防误拼，不能代替整页阅读顺序恢复。</p>
<p>ocr.py的with_layout_groups只对很窄的一类居中短中文标签尝试合并；knowledge_evidence.py要求同页、非表格、横向重叠至少短框宽度的55%、垂直距离受限、引用完整、无跳过中间行。两者都不是通用双栏/段落分析器。</p>
<p><b>正常双栏不能全部禁止跨栏：</b>左栏结尾接右栏开头可能正是同一段。应先区分通栏标题、正文栏目、表格、图注、页眉页脚等版面块，恢复栏目及段落的阅读顺序，再形成chunk，并保留原始span和坐标。不能简单把左右同行拼起来，也不能只按左右半页硬切。此处说明所需处理顺序，尚未实现新的版面算法，也未把单栏指南算成双栏验收。</p>
<p>官方资料也区分<a href="https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md">通用OCR的文字检测/识别</a>与<a href="https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md">PP-StructureV3的版面分析和多栏阅读顺序恢复</a>。本项目当前调用PaddleOCR，不等于接入了PP-StructureV3的整套能力。</p></section>'''

def performance_policy_section(data):
    policy=data.get('performance_policy')
    if not policy:return ''
    rows=''.join('<tr><td>'+escape(name)+'</td><td><code>'+escape(json.dumps(profile['request_options'],ensure_ascii=False))+
        '</code></td><td>'+str(profile['max_output_tokens'])+'</td></tr>' for name,profile in policy['profiles'].items())
    diagnostic=policy.get('diagnostic')
    if diagnostic:
        evidence=('<p>独立参数调试实际发送'+str(diagnostic['real_model_requests'])+'次匿名短JSON请求；全部通过该短契约检查，'+
            str(diagnostic['length_truncations'])+'次长度截断。max三请求确有思考token。修改前后各'+
            str(diagnostic['after_checks']['tests_run'])+'项离线检查通过，固定开放资料7条缓存命中、195条事实记录保持。</p>'+
            '<p>另做禁止新请求的缓存影响检查：切为max身份后旧缓存命中7→0，不能冒用旧预测。没有max整资料新预测，不能据小请求宣称精度提升或无bug。</p>'+
            '<p>实发期间另有3个非请求组装文件被外部修改，原全源码冻结检查失败已保留；本轮实际请求、提示词和关键调用源码逐项核验不变，仅作为短接口兼容性证据。</p>')
    else:evidence='<p>未读取到参数调试汇总，不在此声称真实兼容性验证完成。</p>'
    return ('<section id="maxPerformance" class="notice"><h2>思考档位按任务选择</h2>'+
        '<p>按用户最新要求，各Agent依据任务和测试自行选择：简单任务验证后可用较低档，反复失败可尝试提高，不强制最高档。'+
        '<code>deepseek-knowledge-max</code>保留为可选配置；两个调试脚本及现有网页、国标评估、旧规则入口保留原默认。</p>'+
        '<div class="scroll"><table><tr><th>配置</th><th>精确请求选项</th><th>最大输出tokens</th></tr>'+rows+'</table></div>'+
        '<p>max配置不发送思考模式下无效的temperature。历史关闭思考的回包仍按其实际配置展示，不将缓存回放标成max实跑。</p>'+
        evidence+'<p><a href="'+escape(policy['diagnostic_url'])+'">参数调试完整记录与采用边界</a> · '+
        '<a href="'+escape(policy['official_thinking_docs'])+'">DeepSeek官方思考参数</a>。本HTML构建新增API仍为0；上面的6次是单独参数调试，不属于4份失败样例的新预测。</p></section>')

def frame(title,page,nav,content,js):
    content = ('<section class="card" id="documentFrontend"><h2>第四路整读前端调试</h2>'
        '<p><a href="http://127.0.0.1:8766/web/recognition-debug.html?route=document&amp;run=open-document-survey&amp;stage=facts">打开整读调试工作台 · 已保存开放报告第22–27页</a>。'
        '点击候选中的“沿这条候选追查”，依次检查原始PDF与解析行、内容恢复与分区、提取、原页复核、跨组审计和最终记录。'
        '每次交互可展开完整提示词、精确请求、图像、供应商原始响应及校验结果；分组覆盖表列出实际带入与缺席的来源块。</p>'
        '<p>浏览与导出均为0新API。选页样例共342条候选、221条待核；流程完成与质量状态分开。'
        '新任务从运行面板启动，读取所选PDF整份文件；页面筛选只用于浏览。可保存历史快照和检查已有预测，第四路测试点面板暂不支持独立重跑。</p></section>') + content
    nav_html=''.join(f'<a href="#{k}">{escape(v)}</a>' for k,v in nav)
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{CSS}</style><body data-page="{page}"><header><div class="sub">SlopeKG · 知联 Knowledge · 固定维护入口</div><h1>{escape(title)}</h1><nav><a href="知联流程详解.html">流程、参数与源码</a>　<a href="知联失败样例.html">开放失败样例全链路</a></nav><div class="sub">更新于 <span id="generated"></span> · 本次不调用模型API</div></header><div class="wrap"><aside>{nav_html}</aside><main>{content}<div class="card"><b>前端测试点与历史入口：</b><code>/web/recognition-debug.html</code> 的“数据集与测试点”可选测开放检查点；“历史记录与对照”可重新打开一次完整快照、导出ZIP、追加复核并选择异常点重测。数据集预期只进入检查器，不送给识别模型。桌面启动器会检查8766默认服务实际加载的版本与调试接口，空闲旧后端自动重载，运行中的任务不会被强制中断。自动字段规则、绑定原预测的历史结论和本地人工判定分开；不等于独立验收或整文档准确率。</div><div class="foot">构建：scripts/build_knowledge_debug_html.py · 本页从当前磁盘源码和允许列出的开放历史产物生成；历史产物、缓存和机制测试分别标注。</div></main></div><script src="assets/debug_data.js"></script><script>{COMMON_JS}\n{js}</script></body></html>'''

def write_html(out,data):
    agent_trial=data.get('agent_trial')
    fifth=''
    if agent_trial:
        fifth=('<section id="agentRoute"><h2>第五路：自主 Agent（DSH SDK）开放试跑</h2>'
            '<p><b>当前前端：</b>智能识别保留第五路专门工作区，与资料管理、其他方法共享导航。已完成任务默认显示结果，原页按需展开；风险预评估返回时保留任务与原页。执行轨迹和模型调用位于过程详情，识别调试位于更多菜单的开发工具。技术来源保留于源码文档。新任务不限调用次数和整任务时长，可主动停止并保留文件。下方18页历史试跑的160次/40分钟设置是当时冻结参数。</p>'
            '<p>由Agent自己安排阅读、裁图、搜索、跨页追踪和分批整理。项目只提供资料工具、交付契约和本地结构核查。独立会话、项目密钥、运行时版本预检。</p>'
            '<p><b>运行时归属：</b>2026-11-09将已验证的DSH 0.1.2-alpha.1、配套SDK和Node独立复制到SlopeKG项目内，程序文件逐项核验；不再从EasyCAE或个人DSH安装目录加载。升级先验证再切换，保留项目内可用版本。此迁移不改变下方历史试跑结果，也不构成新的语义验收。</p>'
            '<p><b>国标接入：</b>新任务由Agent自主提交outputs/assessment.json，决定对象、时期、工况、标准输入、推断依据及缺口；完成后自动形成来源快照及预评估案例。接口校验结构与引用，不按字段关键词重判。原始提交、表15/17、缺口及复核版本可在PDF工作台继续查看。旧任务可接收原始事实，未提交的评估语义明确留缺；全部候选待复核。接口为/api/assessment-debug/agent-submit，契约为/api/assessment-debug/agent-contract。</p>'
            '<p><a href="http://127.0.0.1:8766/web/agent-recognition.html">打开第五路本机试验入口</a> · '
            '<a href="../20261107_第五路Agent/verification.json">完整试跑核验记录</a> · '
            '<a href="../20261107_第五路Agent/试跑结果.html">结果、缺口与升级说明</a> · '
            '<a href="../20261107_第五路Agent/开放检查点复核.json">开放检查点复核</a></p>'
            '<p>本轮只覆盖原开放包4份18页。全部模型请求、原始响应和工具轨迹留存；所有事实作为待核候选，不冒充专业签认或整份PDF准确率。</p>'
            '<details><summary>本轮状态、用量、冻结与核验</summary><pre>'+escape(json.dumps(agent_trial,ensure_ascii=False,indent=2))+'</pre></details></section>')
        if (trial:=data.get('agent_risk_trial')):
            fifth=fifth.replace('</section>', '<div class="card"><b>第五路接边坡风险 · 开放调试</b>'
                '<p>两份工程体例模拟巡查档案按不同资料时点独立实跑，另复用真实W6开放选页。'
                f'实际{trial["actual_model_requests"]}次模型请求；核对后隔离回放，现状候选{trial["standard_matches"]}/{trial["case_count"]}、'
                f'动态筛查{trial["dynamic_matches"]}/{trial["case_count"]}符合预设。该历史测试发现异常漏提示及第五路入口不支持；入口限制已在后续接入改造解除，动态筛查问题仍单独保留。'
                '这是风险逻辑诊断，未验证未来预测。'
                '<a href="../20261107_DSH边坡风险测试/第五路边坡风险测试报告.html">查看证据、失败定位与回放对照</a>。</p></div></section>',1)
        if (direct:=data.get('agent_assessment_direct')):
            fifth=fifth.replace('</section>', '<div class="card"><b>2026-11-09 · 第五路输出直接参与评估</b>'
                '<p>'+escape(direct['conclusion'])+'</p>'
                '<p>check_outputs增加现有接收层和表15/17的真实只读试算，按输入反馈采用或排除原因；'
                'Agent依据原文处理结构表达，保留缺失和冲突。旧结果不改写，临时试算不写正式库。'
                '<a href="../20261109_第五路直接评估/第五路直接评估复测报告.html#photo-comparison">查看原始 PDF 与实际前端结果截图对照</a>。</p></div></section>',1)
    real_comparison=''
    if data.get('real_document_comparison'):
        real_comparison='<section id="realDocumentComparison"><h2>真实单样本对比：归属改善，整读复核仍受阻</h2><p><a href="../20261103_真实资料双路对比/当前单样本对比.html">查看真实报告6页、24个测试点的知联/整读对照</a>。当前只完成这一份选页样本；其余三份未执行，不当作整份75页已验证。</p><p>定向原文核对：知联9项完整、12项部分、3项遗漏；整读19项完整、5项部分。包含待核候选，不是准确率或召回率。整读最终复核输入212,744字符，超过100,000字符，未发送；191条候选均待核，完整流程未通过。</p><p>此前整份解析出现约10GB工作集；已修正选页预处理，332条选页来源单元保持一致。最后收尾新增1次请求，峰值约158.4MiB。累计19次尝试含2次中断，用量缺失部分不算0。当前已停止扩测。</p></section>'
    fourth=''
    if data.get('document_route_experiment'):
        experiment=data['document_route_experiment']
        fourth='<section id="documentRoute"><h2>第四路整读：先恢复内容，再分组与归属</h2><p>原页图像与坐标文字 → 完整段落/表格 → 可重合资料组 → 分区审核 → 提取 → 原页核对及补证据。与下方知联旧流程分别保存。</p><p><a href="../20261103_第四路小测/第四路小测报告.html">查看4页开放合成资料的实际对照、修复和全部输入输出</a>。本轮共 '+str(experiment['new_generation_requests'])+' 次模型请求（含知联基线与修后复核），当前缓存回放0新API；HTML构建本身0 API。</p><p>知联目录已发现SYN-I；整读补回其遗漏事实并保留8～12人区间；复核补齐表头引文。两份文档仍为partial，不能据这次小测声称真实工程准确率或普遍优于知联。长文档分层规划与完整分批复核尚未实现。完整新模块已加入流程页源码选择器。</p></section>'
    if data.get('document_v02'):
        real_comparison=('<section id="documentV02"><h2>当前整读v0.2：流程跑通，尚未证明提速</h2>'
            '<p><a href="../20261103_真实资料双路对比/精简版快速测试.html">查看同一真实6页的最新流程、原文核对、耗时和完整回包</a>。</p>'
            '<p>原页恢复 → 有缺口才有限修复 → 全文可重合分组 → 每批提取后立即原页复核 → 紧凑索引跨组检查。'
            '取消固定的独立分区审核，不再发送巨型全量复核。4批资料均完成复核，最大82,455字符；跨组索引87,100字符。'
            'workflow_complete=true，文档仍partial：342条候选中221条待核、1条被否定。不是准确率或专业确认。</p>'
            '<p>本轮16次尝试含1次中断；最终全缓存回放0次API。92项相关回归及最后16项第四路测试通过。'
            '父对象、范围和计算/要求角色有所改善，但材料名冒充计算主体、共享资料重复提取及泛化复核仍存在。'
            '候选数量增加不算提升，下一步应减少重复提取，不能继续堆固定审核层。当前源码含document_workflow。</p></section>'
            +real_comparison.replace('真实单样本对比：归属改善，整读复核仍受阻','历史v0.1基线：归属改善，整读复核受阻'))
        fourth=fourth.replace('第四路整读：先恢复内容，再分组与归属','历史v0.1合成小测：先恢复内容，再分组与归属')
    if data.get('document_failure_trace'):
        real_comparison=('<section id="documentFailureTrace"><h2>整读失败样例：原始PDF到最终记录</h2>'
            '<p><a href="../20261103_真实资料双路对比/第四路失败样例详解.html">展开4个真实失败样例的完整证据链</a>：'
            '原始75页PDF可直接查看；每例列出相关原生行、内容重建、实际分组与批次上下文、模型原始提取、原页复核、跨组检查及最终候选。'
            '模型原始回答与程序校验结果分别展示，提供完整输入及供应商回包。仅整理已保存的开放结果，新增API为0。</p></section>'+real_comparison)
    assessment_bridge='''<section class="card" id="pdfRiskBridge"><h2>从同一次识别继续到国标风险预评估</h2><p><a href="http://127.0.0.1:8766/web/recognition-debug.html">在识别调试工作台选定记录</a>，点击“衔接当前记录”，进入<a href="http://127.0.0.1:8766/web/assessment-debug.html">PDF到风险全过程</a>。绑定标识包括识别历史ID、PDF哈希、来源对象、时期工况、评估案例及版本；不按文件名或相同桩号猜测同一物理坡体。</p><p>识别候选 → 字段转换/未采用原因 → 对象时期工况校核 → 有效输入/来源选择 → 专业判断及实际模型I/O → 表15 → 表17 → 缺口/冲突/区间 → 人工复核和历史版本。每个规则输入可追溯识别事实及PDF原页，并可导出两段完整记录。</p><p>“衔接并自动预评估”只做本地整理与确定性查表，使用独立调试评估库。模型辅助需要明确允许发送相关片段；不把模型建议当专业签认。旧识别记录与人工修改均保留，读取本页不触发识别或评估。</p><p>有多份资料时，可从预评估页点“归入边坡档案”，到<a href="http://127.0.0.1:8766/web/slope-assessment.html">边坡评估工作台</a>确认同一滑坡体，选择同期同工况资料组证。每次评估保存所用快照、原页与版本。前端可补充原文证据、纠正数值并查看旧值。</p></section>'''
    workbench_report = '20261109_边坡评估前端验收/验收记录.html'
    if (out.parent / workbench_report).is_file():
        assessment_bridge = assessment_bridge.removesuffix('</section>') + (
            f'<p><a href="../{workbench_report}">查看内置浏览器模拟验收</a>。两份模拟 PDF 经第五路实际识别，'
            '在页面完成同坡组证、原页补证、纠错、重算与模拟复核。验收记录保留修复前后的结果和历史版本。</p></section>'
        )
    workspace=''
    receipt=out/'workspace_import_receipt.json'
    if receipt.is_file():
        imported=json.loads(receipt.read_text(encoding='utf-8'))
        links=''.join('<li><a href="'+escape(r['url'],quote=True)+'">'+escape(r['label'])+'</a>：'+escape(r['failure_summary'])+'</li>' for r in imported['records'])
        workspace='<section class="card"><h2>这些失败记录已加载到新调试工作台</h2><p><a href="http://127.0.0.1:8766/web/recognition-debug.html?tab=history">打开历史记录与对照</a>，搜索“失败复盘”。以下链接直接进入对应的失败步骤；可以继续切换输入、分块、归属、请求响应和最终候选。</p><ul>'+links+'</ul><p class="muted">加载保存的历史记录，新增API为0。缺失的历史输入和原始HTTP回包保持缺失。G359输入页1/2/3/4对应原件物理页4/5/7/10；当前界面中的物理页指本次输入PDF页码。</p></section>'
    if (out/'work_audit_receipt.json').is_file():
        workspace+='<section id="workAuditLink" class="notice"><h2>用具体信息逐步检查流程</h2><p><a href="具体案例全流程详解.html">打开四个具体案例的逐阶段中间结果</a>：真实G209的28m坡高为何丢失、扫描SYN-I的弱发育在哪一步脱链、跨页SYN-C的强发育与10人如何归属，以及同表A/B的9人与120人是否串值。</p><p>每例按原件 → 解析 → 分块 → 发现 → 归属 → 对象资料包 → 重组 → 校验 → 最终结果展开实际内容。API输入、响应和用量随步骤查看；统计转入辅助附录。只读已有产物，不恢复数据库，新增API为0。</p></section>'
    workspace+='<section id="readingUnitsLink" class="notice"><h2>文字碎是解析还是展示造成的</h2><p>原生文字块/OCR检测框可能只有一行，展示按来源单元逐条列出；chunk和模型请求则包含许多单元。当前按字符装块，尚缺完整栏目/段落恢复，不能把“在同一包里”当成阅读结构已整理好。</p><p><a href="具体案例全流程详解.html#readingUnits">查看G209真实保存请求：左侧锚杆文字如何插入右侧危岩段落</a>。这不是新预测；历史请求没有bbox，当前源码已附带bbox。<a href="../../docs/知联调试交接.md">查看交接说明和源码定位</a>。</p></section>'
    flow=r'''
<section class="notice" id="overview"><h2>当前算法到底是什么</h2><p><b>知联 v3.8 是“解析/OCR文字证据 → 全文对象目录 → 跨块归属与原子提取 → 对象重组 → 本地门控 → 有限复核/一次查漏”的流水线。</b>它依赖模型理解对象、字段、角色和关系；本地规则负责来源、范围和格式约束。它不会直接把整页图像发给大模型，也没有先得到一个全知的语义图再切分。</p><p>这里解释的是当前磁盘源码。未验证正在运行的服务进程是否已重载。<b>v3.8真实长文档识别提升尚未得到验证；“137项离线通过”和“G359缓存6/13保持”都不是新的独立识别成绩。</b></p><p class="muted">本调试源码集指纹 <span class="hash" id="snapshot"></span></p></section>
<section id="flow"><h2>1. 实际执行顺序（点击阶段）</h2><p>横向从入口走到落盘。蓝色标注可能读精确缓存或发请求；“模型阶段”不代表每次都新增API。</p><div class="flow" id="stageButtons"></div><div id="stageDetail" class="stage card"></div><div class="four"><div><b>三种目录角色</b><p>focus：常规归属/档案对象<br>catalog：只有标题/目录线索<br>support：背景、容器、孤立变量</p></div><div><b>两种对象类型</b><p>entity：具体对象<br>concept：方法/过程/主题<br>同一类型还需name+context区分身份。</p></div><div><b>三种切分</b><p>单元pieces约1400字符<br>初始chunk约2800字符<br>完整请求再按60000字符拆分。</p></div><div><b>三个不能混淆</b><p>OCR数值≠量义<br>候选事实≠程序接纳<br>程序/模型接纳≠专家确认</p></div></div></section>
<section id="params"><h2>2. 精确参数：当前默认、范围与含义</h2><p>下表直接读取KnowledgeSettings，不手填默认值。网页实际默认值为调用64、并发6、OCR补页0；网页只允许改这三项。历史测试经常使用并发3、可选复核2、重试1/全程2、记录输入True；以样例页的run.settings为准。</p><div class="scroll"><table><thead><tr><th>参数</th><th>默认值</th><th>允许范围</th><th>实际作用 / 容易误读之处</th></tr></thead><tbody id="paramRows"></tbody></table></div><p>额外校验：batch_chars和synthesis_chars必须严格小于max_prompt_chars//2；所以默认24000&lt;30000。review_mode仅selective/all/none。知联三项图像预算必须固定0。</p>
<h3>模型请求配置（隐藏凭据字段）</h3><pre id="profile"></pre><p>当前命名配置是DeepSeek兼容接口，max_tokens=393216、temperature=0.2、thinking disabled、timeout=600秒。response_format=json_object、stream=False。该数是项目当前实发输出上限，不是输入字符预算。复核模型可另配；没配则和提取模型相同。</p><p>API密钥由已有配置引用，本报告不读取或展示密钥值。本次构建没有访问密钥或发请求。</p>
<h3>底层解析与OCR参数</h3><ul><li>PyMuPDF page.get_text("blocks", sort=True)，pdfplumber page.find_tables()/table.extract()。generic=True不按文件名选行业文档类型，逐页尝试表格提取。</li><li>知识单元坐标统一为显示页归一化bbox，保留6位小数。非法坐标返回None并记录geometry_issue；不把越界坐标夹紧后冒充正常。</li><li>本地PaddleOCR：lang=ch；首选关闭文档方向分类、文档展平、文本行方向，enable_mkldnn=False；不兼容的初始化参数会逐级回退。实际渲染主路径240dpi，备选Poppler路径由源码记录；解析策略标签里出现render_300dpi不等于当前主渲染真是300dpi。</li><li>OcrRunner默认auto设备、渲染并发4、推理batch_size=0（CPU选1，GPU选4）。知联没有把网页的模型并发当OCR线程数；OCR只处理未完成任务。</li><li>OCR证据confidence缺失或&lt;0.85会触发ocr_source_requires_review。这个阈值是门控规则，未校准为真实准确率。</li></ul><pre id="packages"></pre><p class="muted">上表为本次构建所用slopekg环境的包分发版本；并非每一次历史运行都使用同一环境。源码中的实际调用优先于策略说明文字。</p></section>
<section id="schema"><h2>3. 数据结构：每一步保存什么</h2><div class="grid2"><div><h3>证据单元 unit（示意，不是测试答案）</h3><pre>{"id":"p3table0r2t0","page":3,
"text":"Device A | 23 | C",
"source":"pdf_table","bbox":[0.1,0.2,0.8,0.3],
"table_id":"p3table0","row":2,
"cells":["Device A","23","C"],
"header_ids":["p3table0r0t0"],"is_header":false}</pre><p>wire_source发id/page/text/source/bbox/confidence/table_id/row/cells/header_ids/is_header。源哈希、源记录和偏移仍存本地，不是上述字段都在每个stage重复发送。</p></div><div><h3>候选 fact（示意）</h3><pre>{"entity_id":"e_...","field":"temperature",
"value":"23","unit":"C","unit_basis":"",
"scope":"Device A","role":"observation",
"condition":"","time":"","qualifier":"",
"evidence":[{"span_id":"p3table0r2t0",
"quote":"Device A | 23 | C"}],
"status":"source_aligned","quality_issues":[]}</pre><p>角色是observation/design/recommendation/calculation/prediction/simulation/reference/unknown。unknown或缺field/value/scope继续待审；中文角色只做明确枚举翻译，不替模型判断设计/观测用途。</p></div></div>
<p>对象ID：e_ + hash([doc.id, kind, canonical(name), canonical(context)])前16位。canonical只做NFC、casefold、去空白。别名不参与这个哈希；已有对象可由模型返回id复用，或由相同kind/name/context匹配。不做独立向量同义聚类。</p>
<p>事实ID：k_ + hash([doc.id, subject.id, kind, item字段])前22位；同对象但不同scope/role/工况/时间是不同断言。去重合并证据时不会自动继承某条的通过标记。</p></section>
<section id="identity"><h2>4. 目录、跨块归属和重组：目前的具体做法</h2><ol><li><b>发现时有顺序依赖。</b>前批先建目录，后批带已有目录复用id；因此发现串行，不能直接把model_concurrency=6解释为发现也同时跑6批。</li><li><b>完整目录再次读所有chunk。</b>只把focus对象用于正常归属。返回多对多span集合；范围展开不得跨越本次未提供的来源。规范化修复chunk标签归属时按真实span所在chunk分配，不按最近名字猜。</li><li><b>本地标题和唯一名称仅补来源选择。</b>显式章节范围会结束于同级/上级标题；表格行不会当章节。孤立数字只在对象本页有真实名称锚点时允许，不能凭数字本身创造身份。</li><li><b>原子候选先保留。</b>归属阶段模型返回的facts走与后续相同的本地检查和主体门控；重组阶段可补，但漏返不能抹掉这些候选。</li><li><b>对象证据再打包。</b>每对象按来源预算分part，跨非相邻页聚合；共享来源的不同对象优先同批，原文只发一份。每subject有allowed_span_ids，不能引用别的subject才提供的来源。</li><li><b>主题和具体案例有边界。</b>concrete_subject只在明确scope与同一个具体来源owner一致时换主体；_reconcile_owners虽然在源码里存在，v3主流程并不会自动调用它，不能据函数存在声称有自动二次归属修复。</li></ol>
<h3>v3.8的目录缩减到底省掉什么</h3><p>registry + document_context + subject(s)的JSON导航元数据总长度超过60000//3=20000时，目录保留id/name/kind/context/aliases/salience/source_pages；去掉发送目录里的description/source_anchors。subject(s)去description；document_context去evidence，purpose保留。原文source_spans、表格行与表头不在此处裁剪。</p><p class="issue">这保留了所有对象身份，但不保留发送给模型的全部导航信息。描述和定位减少可能影响消歧能力；“不丢对象”不能证明“识别质量不下降”。最小身份目录仍放不下时，单纯拆正文仍会失败。</p></section>
<section id="gates"><h2>5. 逐条门控与状态：在哪一关会停</h2><div class="scroll"><table><tr><th>关口</th><th>实际检查</th><th>失败后</th></tr><tr><td>来源id/quote</td><td>必须来自该请求给本对象的allowed集合；quote连续原文可忽略排版空白、仅全角宽度等价。</td><td>unprovided_source_span / quote_not_in_source等，保留pending；非法绑定可隔离。</td></tr><tr><td>值与跨行</td><td>先单引文包含；文字可拼相邻、同页、非表格、同列原文。横向重叠≥较窄框55%，行间距-0.15到1.25倍较高行高；不能跳过中间行；句末标点和拼数字有禁止条件。</td><td>value_not_in_cited_source。公式符号、上下标和OCR错字可能误拒，也可能确实缺引文。</td></tr><tr><td>单位</td><td>常见中英长度/面积/体积/质量单位规范表达；表格数值必须找同一数值列的单位表头，不借其他列。</td><td>unit_not_in_cited_source / unit_basis_not_in_cited_source。</td></tr><tr><td>事实语义最低项</td><td>field/value非空、role合法且非unknown、scope非空。不是程序独立判断field语义是否真实。</td><td>incomplete_fact_semantics / missing_scope。</td></tr><tr><td>主体与章节</td><td>具体对象章节、同名歧义、原文数值本页身份锚点、主题下具体案例、OCR置信度。</td><td>foreign_object_section_evidence / concrete_case_requires_explicit_owner / non_unique_class_label_requires_review / numeric_source_has_no_local_object_anchor等。</td></tr><tr><td>独立模型复核</td><td>每项给supported/uncertain/rejected，五项source/identity/meaning/scope/qualifiers均须true。</td><td>支持且无本地问题才verified_model；uncertain保持pending；rejected保留。</td></tr><tr><td>同条件冲突</td><td>相同对象/field/scope/role/condition/time下出现不同value/unit/unit_basis/qualifier。</td><td>已有source_aligned或verified_model降为pending，不自动选一个值。</td></tr></table></div>
<p>四种事实状态：source_aligned＝本地来源规则对齐；verified_model＝来源对齐且模型核验支持；pending＝缺口、歧义、复核不足或冲突；rejected＝模型明确否决。它们均不等于工程专家签认。</p></section>
<section id="budget"><h2>6. 为什么可能调用很多次：调度、缓存、重试</h2><p>无缓存的阶段请求近似 D发现批 + A归属批 + S重组批 + R可选复核批 + U查漏（0或1）。每个大对象可分多个part，很多对象可共用一批。调用数既不是PDF页数，也不是最终事实数；也没有默认每条事实再调用一次核验。</p><div class="controls"><label>D <input id="dCalls" type="number" min="0" value="3" size="3"></label><label>A <input id="aCalls" type="number" min="0" value="3"></label><label>S <input id="sCalls" type="number" min="0" value="5"></label><label>R <input id="rCalls" type="number" min="0" value="2"></label><label>U <input id="uCalls" type="number" min="0" value="1"></label><label>缓存 <input id="cacheCalls" type="number" min="0" value="0"></label></div><p id="estimateResult"></p>
<ul><li>缓存键包括VERSION、stage、model.identity、SYSTEM、完整prompt、图片标签和哈希。知联图片数组为空。设置本身不全部进入缓存键，主要通过提示内容或版本体现；PDF哈希只在部分context显式出现，不能简化成“仅看文件哈希就复用”。</li><li>完成缓存再次验证，不新增请求；有可重新验证invalid_answer的失败缓存可本地恢复；旧failed/started在retry_failed=False时不自动重发。</li><li>仅明确HTTP429/500/502/503可自动退避；默认单请求额外2次、全程额外4次，均占64总额。408/504、超时、未知结果、JSON/契约错误不走自动重试。</li><li>finish_reason必须stop，length会标失败并阻断该profile继续发送；stop但非法JSON仍失败，不等于输出截断。</li><li><b>计数区别：</b>run.summary.calls是runtime发起的调用槽计数；实际外部请求以transport记录、provider回执对照。缓存中的旧usage不可再次当作新增tokens或费用。失败前没发出的批次没有usage回执；有回执也不能代表其余批次都发了。</li><li><b>完整调试留痕（2026-11-02）：</b>前端 /web/recognition-debug.html 可查看三路线各步已保存记录。新调试任务默认开启record_model_inputs，保存完整system/prompt、实际请求参数与图片、供应商HTTP原始正文及完整JSON、校验前raw_answer与校验后answer；非法JSON、length和契约失败也保留已收到的原文。鉴权头不保存。管线默认仍为False；旧记录和这里的历史开放样例通常只有规范化缓存answer，原始HTTP正文没存下就无法事后补回。缓存查找重建的输入明确标记，不冒充当时实发。</li></ul>
<h3>真实字符门计算器（本地，不发请求）</h3><p>输入最终发送的System和Prompt字符串；按Unicode码点计数，与Python len(str)口径一致。这里计算字符，不估token，也不自行重序列化浮点JSON。</p><label>System<textarea id="systemText"></textarea></label><label style="display:block">Prompt<textarea id="promptText"></textarea></label><p id="countResult"></p></section>
<section id="promptsSection"><h2>7. 当前完整提示词（逐字来自源码）</h2><p>每次请求＝SYSTEM + 本阶段指令 + “\n输入：” + 紧凑JSON。这里不是摘要；展开即可完整检查模型到底被要求做什么。</p><div id="prompts"></div></section>
<section id="finish"><h2>8. completed到底要求什么</h2><p>_finish_document同时要求：有chunk和focus对象；发现/归属/重组记录全completed；所有chunk归属completed；没有doc.errors；所有对象organized；owner/review/recovery任务全completed；没有整页来源完全未归属。其后还要求没有图形未读页、没有未选页、原件哈希没变，才会给文档completed。</p><p><b>因此：选择4页的资料可有若干阶段完成但文档仍partial；出现图形缺口仍partial；API全部stop仍可能没有完整输出；所有已输出事实对齐也不意味着没漏项。</b>准备模式为prepared。原件变更会把事实降为pending并标failed；读取API还会把实现或原件变化的旧输出标stale。precision/recall返回null，没有从内部状态自动造准确率。</p><p>对象页面是已有记录的派生展示，不是追加一轮Wiki模型写作；事实不会在这条识别链自动写入正式图谱或自动做正式风险评估。</p></section>
<section id="overfit" class="notice"><h2>9. 过拟合与当前缺陷：哪些已知，哪些没证明</h2><div class="scroll"><table><tr><th>可以核实</th><th>仍不能由此推出</th></tr><tr><td>同一批开放资料反复调试，G359的13点多次使用；最新两轮大量是精确缓存回归。</td><td>不能当成泛化验证；没有新的独立资料成绩就无法排除过拟合。</td></tr><tr><td>角色别名、单位映射、表头规则、标题范围、数值本页锚点都是人工规则；20000导航分界、0.85置信度等为固定参数。</td><td>行业词未写死也可能对版式、数据形态、语言、文档阶段有偏置；阈值未证明跨资料稳健。</td></tr><tr><td>下面有有限文件名/数值指纹扫描，源码和提示词完整可查。</td><td>命中0也不证明无数据泄漏、无调参过拟合、无模型训练集记忆。</td></tr><tr><td>归属和重组做了坏绑定/坏对象隔离；发现循环仍可能因非法name/kind/id/salience等抛异常，中止该批后续对象处理。</td><td>不能笼统声称“整批丢弃全修好了”；这是源码可触发风险，未在此新增付费测试。</td></tr><tr><td>只保留focus走主链；查漏最多一批、需要标题、排在复核之后；孤立数字严格门控。</td><td>全文对象发现和跨块知识完整性没有保证；图纸数字、支持栏目正文、剩余未归属来源仍可能漏。</td></tr><tr><td>v3.8缩小目录导航信息；本地事实检查对field/role业务含义没有独立求解器。</td><td>不能仅凭字符串来源对齐证明量义、设计/实测角色和对象归属正确。</td></tr></table></div><details><summary>有限指纹扫描结果与扫描范围</summary><pre id="fingerprints"></pre></details><p>下一次判断是否泛化，需要冻结最终版本后在未用于开发的完整资料上验收，并只向开发返回汇总。隐藏样例和曾经属于隐藏集的原件均不放进第二份样例报告。</p></section>
'''
    all_stages='<details><summary>展开全部阶段对照（适合连续阅读）</summary><div class="scroll"><table><tr><th>阶段</th><th>输入</th><th>处理</th><th>失败边界</th></tr>'+''.join('<tr>'+''.join('<td>'+escape(str(s[k]))+'</td>' for k in [1,2,3,4])+'</tr>' for s in data['stages'])+'</table></div></details>'
    flow=flow.replace('<div id="stageDetail" class="stage card"></div>','<div id="stageDetail" class="stage card"></div>'+all_stages)
    walkthrough=guide_walkthrough(data)
    flow=fifth+real_comparison+fourth+workspace+assessment_bridge+performance_policy_section(data)+flow+walkthrough+source_section()+notes_section()
    nav=[('overview','当前版本与边界'),('flow','执行顺序'),('params','精确参数与引擎'),('schema','数据结构'),('identity','目录与跨块归属'),('gates','门控与状态'),('budget','调用 / 缓存 / 重试'),('promptsSection','完整提示词'),('finish','完成与未完成'),('overfit','过拟合与缺陷'),('source','源码'),('notesSection','你的备注')]
    nav.insert(0,('maxPerformance','思考档位选择'))
    if fifth:nav.insert(0,('agentRoute','第五路自主试跑'))
    if fourth:nav.insert(0,('documentRoute','第四路小测'))
    if real_comparison:nav.insert(0,('realDocumentComparison','真实单样本对比'))
    nav.insert(-2,('guideWalkthrough','指南真实逐步案例'))
    nav.insert(-2,('ocrColumns','OCR行与双栏问题'))
    (out/'知联流程详解.html').write_text(frame('知联详细流程、精确参数与源码','flow',nav,flow,FLOW_JS),encoding='utf-8')
    cases=r'''
<section id="scope" class="notice"><h2>仅开放开发集：失败现场，不是新的测试成绩</h2><p>收录4份已测试的开放资料：G359施工图、G209勘察报告、变形规律论文、监测指南。<b>不含隐藏集或曾属于隐藏集的原件，也不链接其语义诊断。</b>本次只整理既有输入与产物，新增API为0。</p><p>每个版本显示自己的算法版本、页范围、源哈希、参数和调用记录。G359 v3.8是7个精确输入回包的缓存回归，不能冒充新预测；其余主要是v3.2历史真实调用，不能直接说这些旧缺陷都仍存在于当前v3.8。</p><p>完整成功回包多数只保存了缓存的解析/规范化answer，并未保存供应商HTTP原始正文。下方如实显示已有材料；缺失日志不补造。</p></section>
<section id="choose"><h2>1. 选资料和历史运行</h2><div class="controls"><select id="caseSelect" aria-label="选择失败资料"></select><select id="variant" aria-label="选择历史版本"></select></div><h3 id="caseTitle" class="case-title"></h3><p id="caseMeta"></p><div id="variantInfo"></div><div id="runStats" class="statgrid"></div><div id="usageTable" class="scroll"></div><div id="downloads" class="pillrow"></div><details><summary>本次真实设置、模型、计数、来源清单与覆盖（完整）</summary><pre class="wrap" id="settings"></pre></details></section>
<section id="failuresSection"><h2>2. 已知失败点：按证据定位</h2><div id="failures"></div><p class="muted">“没有最终事实”“候选待审”“契约失败”“发送前失败”“本地环境失败”是不同结果，不能合并成一个低分或简单归咎于模型能力。</p></section>
<section id="trace"><h2>3. 逐阶段输入、中间结果和输出</h2><div class="flow" id="stageTrack"></div><div id="stageResult"></div><details><summary>全部阶段回执与实际用量记录</summary><div id="allUsage"></div></details></section>
<section id="pdfPanel"><h2>4. 原PDF页 → 来源框 → chunk → 对象 → 最终事实</h2><div class="controls"><select id="pdfPage" aria-label="选择原PDF页"></select><label><input type="checkbox" id="showBoxes" checked>显示该运行保存的来源框</label><select id="sourceType" aria-label="筛选来源类型"><option value="">全部来源类型</option><option value="pdf_native">pdf_native</option><option value="pdf_table">pdf_table</option><option value="ocr">ocr</option></select></div><div class="controls"><label>查看缩放<input id="pdfZoom" type="range" min="100" max="400" step="25" value="100"><span id="zoomLabel">100%</span></label><a id="fullPageImage" target="_blank">单独打开高清页图</a></div><div class="sourcecols"><div><div class="pdfviewport"><div class="pdfarea" id="pdfZoomArea"><img id="pageImage" alt="原PDF页"><svg id="boxes" viewBox="0 0 1000 1000" preserveAspectRatio="none"></svg></div></div><p class="muted" id="pdfNote"></p></div><div><div class="controls"><input id="unitSearch" type="search" placeholder="查span_id、文字、值、表头或坐标" aria-label="搜索来源单元"></div><p id="unitCount" class="muted"></p><div id="unitList" class="unitlist"></div><div class="controls"><button id="unitPrev">前35个</button><button id="unitNext">后35个</button></div></div></div><div id="selectedUnit"><p class="muted">点击来源框、单元行或事实引用，展开真实归属和输出。没有关联项会显示空数组，不自动填一个最近对象。</p></div></section>
<section id="facts"><h2>5. 全部最终事实：支持对象、数值、状态和证据搜索</h2><div class="controls"><input id="factSearch" type="search" placeholder="查对象、字段、数值、reason、span_id或fact_id" aria-label="搜索最终事实"><select id="factStatus" aria-label="筛选事实状态"><option value="">全部状态（含pending）</option><option value="source_aligned">source_aligned</option><option value="verified_model">verified_model</option><option value="pending">pending</option><option value="rejected">rejected</option></select></div><p id="factCount"></p><div class="scroll"><table><thead><tr><th>对象 / 范围 / ID</th><th>量义与原值</th><th>状态与失败原因</th><th>原文证据 / 完整JSON</th></tr></thead><tbody id="factRows"></tbody></table></div></section>
<section id="diagnostics"><h2>6. 全部已保存诊断：未归属、隔离、错误、冲突与门控</h2><p>不是只挑成功输出。这里完整展开该版本保存的diagnostics/errors/source_accounting等字段。某字段不存在表示该版本未记录，不补成0。</p><div id="allDiagnostics"></div></section>
<section id="assessment"><h2>7. 开放开发集判定依据</h2><p>以下来自预测冻结后的既有人工式AI原文核对；不是专业签认，不是全文件精确率/召回率。它只用于这几份已开放资料的debug。旧运行结果与新缓存记录分开看。</p><div id="allAssessments"></div></section>
'''
    cases=fifth+real_comparison+fourth+workspace+assessment_bridge+performance_policy_section(data)+cases+walkthrough+notes_section()
    nav=[('scope','资料边界'),('choose','选择资料 / 版本'),('failuresSection','失败点'),('trace','阶段输入输出'),('pdfPanel','原页与证据追踪'),('facts','全部最终事实'),('diagnostics','完整诊断'),('assessment','开放集判定'),('notesSection','你的备注')]
    nav.insert(0,('maxPerformance','思考档位选择'))
    if fifth:nav.insert(0,('agentRoute','第五路自主试跑'))
    if fourth:nav.insert(0,('documentRoute','第四路小测'))
    if real_comparison:nav.insert(0,('realDocumentComparison','真实单样本对比'))
    nav.insert(-1,('guideWalkthrough','指南真实逐步案例'))
    nav.insert(-1,('ocrColumns','OCR行与双栏问题'))
    (out/'知联失败样例.html').write_text(frame('知联开放失败样例：输入、中间结果与输出','cases',nav,cases,CASE_JS),encoding='utf-8')
