(function(){
  'use strict';
  const {getJson,requestJson,escapeHtml:esc}=window.SlopeKGApi,$=id=>document.getElementById(id);
  const stateNames={preparing:'准备资料',running:'正在识别',stopping:'正在停止',cancelled:'已停止',prepared:'资料已准备',ready:'预检通过',completed:'已完成',partial:'部分完成',failed:'失败',not_generated:'新任务'};
  const toolNames={list_files:'查看工作区文件',read_text:'读取资料文字',search_text:'搜索证据',view_image:'查看原图或裁图',write_text:'写入识别报告',write_json:'保存结构化结果',check_outputs:'检查交付文件'};
  const query=new URLSearchParams(location.search);
  if(!query.size){try{const saved=JSON.parse(sessionStorage.getItem('slopekg-agent-context')||'{}');if(saved.url){const u=new URL(saved.url,location.href);for(const [k,v] of u.searchParams)query.set(k,v);}}catch(_){}}
  const selected=new Set(query.getAll('file'));let profiles=[],files=[],runs=[],runId=query.get('run')||'',draft=query.get('new')==='1',tab=['chat','trace','results','calls','assessment'].includes(query.get('tab'))?query.get('tab'):'auto',resultStage=['facts','objects','coverage','errors'].includes(query.get('stage'))?query.get('stage'):'facts',documentId=query.get('document')||'',offset=Math.max(0,Number(query.get('offset'))||0),traceOffset=-1;
  let sourceToRestore=query.get('source')||'',pageToRestore=Number(query.get('page'))||0,restoreScroll=true;
  function taskUrl(){const q=new URLSearchParams({run:runId,tab,stage:resultStage,document:documentId,offset:String(offset)});if($('pageSelect').value)q.set('source',$('pageSelect').value);if(!document.querySelector('.workspace').classList.contains('evidence-hidden'))q.set('evidence','1');return '/web/pdf-ingestion.html?'+q;}
  function remember() {
    if (draft || !runId) return;
    const url = taskUrl();
    const links = document.querySelectorAll('.app-nav a[aria-current="location"], .method-nav a:not(.active), .sidebar-bottom a[href*="recognition-debug.html"]');
    for (const a of links) a.href = window.SlopeKGNav.withReturn(a.href, url);
    historyReplace(url);
    try { sessionStorage.setItem('slopekg-agent-context', JSON.stringify({ url })); } catch (_) {}
  }
  function assessmentUrl(url){return window.SlopeKGNav.withReturn(url,taskUrl());}
  function saveScroll(){if(!runId)return;try{sessionStorage.setItem('slopekg-agent-scroll:'+runId+':'+tab,String($('feed').scrollTop));}catch(_){}}
  let current={},workspace={},busy=false,sending=false,revision=0,detailRevision=0,timer,signature='',loadedImage='',globalJob={};
  let pendingResume=null,resumeError='';
  const active=s=>['preparing','running','stopping'].includes(s);
  const number=v=>Number(v||0).toLocaleString('zh-CN');
  const shortDate=v=>v?new Date(v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'';
  const artifact=(p,id=runId)=>'/api/agent-recognition/artifact?'+new URLSearchParams({run:id,path:p});
  const label=p=>p.split(/[\\/]/).pop();
  function error(message=''){ $('error').textContent=message;$('error').hidden=!message; }
  function rich(text){return esc(text).replace(/^#{1,6} (.+)$/gm,'<h3>$1</h3>').replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>');}
  function updateActions(){
    const blocked=busy||sending,hasModel=profiles.some(p=>p.id===$('model').value);
    $('run').disabled=blocked||!selected.size||!hasModel||!$('allow').checked;
    $('prepare').disabled=blocked||!selected.size||!hasModel;
    $('instruction').disabled=$('model').disabled=$('chooseFiles').disabled=blocked;
    $('selectedCount').textContent=selected.size?`(${selected.size})`:'';$('dialogCount').textContent=`已选 ${selected.size} 份`;
    $('selectedChips').innerHTML=[...selected].map(p=>`<span class="chip" title="${esc(p)}">▤ ${esc(label(p))}</span>`).join('');
    const running=active(current.processing_status)&&!draft;$('runningBar').hidden=!running;
    $('stop').disabled=sending||current.processing_status==='stopping';
  }
  function renderFiles(){const q=$('searchFiles').value.toLowerCase();$('files').innerHTML=files.filter(f=>f.path.toLowerCase().includes(q)).map(f=>`<label><input type="checkbox" value="${esc(f.path)}" ${selected.has(f.path)?'checked':''}><span>${esc(f.path)}</span></label>`).join('')||'<p class="muted">没有匹配的 PDF，可上传资料。</p>';updateActions();}
  function endpoint(){const p=profiles.find(p=>p.id===$('model').value);$('endpoint').textContent=p?`${p.model} · ${p.endpoint}`:'模型列表暂不可读';updateActions();}
  async function catalog(){const [a,b]=await Promise.all([getJson('/api/recognition/profiles'),getJson('/api/recognition/documents')]);profiles=(a.profiles||[]).filter(p=>p.model==='deepseek-flash'&&p.endpoint==='https://api.deepseek.com');files=b.documents||[];const old=$('model').value;for(const p of selected)if(!files.some(f=>f.path===p))selected.delete(p);$('model').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name||p.id)} · ${esc(p.model)}</option>`).join('');$('model').value=profiles.some(p=>p.id===old)?old:profiles.some(p=>p.id==='deepseek-knowledge-max')?'deepseek-knowledge-max':profiles[0]?.id||'';endpoint();renderFiles();}
  let historySignature='';
  function history(){ const html=runs.map(r=>`<button data-run="${esc(r.run_id)}" class="${!draft&&r.run_id===runId?'active':''}" title="${esc(r.title)}"><strong>${esc(r.title)}</strong><small><span>${shortDate(r.started_at)}</span><span>${esc(stateNames[r.processing_status]||r.processing_status)}</span></small></button>`).join('')||'<p class="muted small">暂无任务，添加资料开始。</p>';if(html!==historySignature){$('history').innerHTML=html;historySignature=html;} }
  function welcome(){if(selected.size)return `<div class="welcome"><div class="agent-avatar">▤</div><h2>已选 ${selected.size} 份资料</h2><p>在下方填写识别重点，确认资料发送范围后开始。<br>结果会按对象和原文证据保存。</p><button class="primary" data-choose>核对资料与发送范围</button></div>`;return '<div class="welcome"><div class="agent-avatar">✦</div><h2>让资料里的信息，变得清楚。</h2><p>添加 PDF，告诉 Agent 识别重点。<br>它会自行阅读、查找、放大原图，整理对象与证据。</p><button class="primary" data-choose>选择资料</button></div>';}
  function dossierLink() {
    if (!['completed', 'partial'].includes(current.processing_status) || !current.summary?.facts) return '';
    const params = new URLSearchParams({ recognition_run: runId });
    if (documentId) params.set('recognition_document', documentId);
    return `<p><a class="button secondary" href="${esc(assessmentUrl('./slopes.html?' + params))}">关联识别资料到边坡档案</a></p>`;
  }
  function recoveryAction() {
    if (!current.can_resume) return '';
    const waiting = pendingResume?.source === runId;
    return `<p>${current.delivery_repair_attempted ? 'Agent 已尝试修正，仍有问题需要核对。' : '已保存原页、候选和本次校验问题。'}
      可从这些草稿继续处理，通过后自动交接风险评估。</p>
      <button type="button" class="button secondary" data-resume ${waiting || busy || sending ? 'disabled' : ''}>${waiting ? '正在启动接续任务…' : '继续修正并交接'}</button>
      ${resumeError ? `<p role="alert">${esc(resumeError)}</p>` : ''}`;
  }
  function errorLabel(error) {
    return String(error).replace(/^Fact\/object document mismatch:/, '事实和对象属于不同 PDF：')
      .replace(/^Fact\/evidence document mismatch:/, '原文引用属于另一份 PDF：')
      .replace(/^Unknown fact role:/, '事实类别未通过校验：');
  }
  function assessmentHTML(){
    const status = current.processing_status;
    if (status !== 'completed') {
      const errors = workspace.errors || [];
      const coverageBlocked = errors.some(message => /^(Declared incomplete page|Coverage must|Original page images)/.test(message));
      const otherErrors = errors.filter(message => !message.startsWith('Declared incomplete page:'));
      const facts = current.summary?.facts || 0;
      const resultLink = stage => './pdf-ingestion.html?' + new URLSearchParams({run:runId, tab:'results', stage});
      const message = active(status) ? current.delivery_repair_attempted ?
        'Agent 正在核对交接问题，修正后会重新校验。' : '识别完成并通过校验后，将自动交接风险评估。' :
        '识别结果还未通过交接校验。';
      return `<section class="assessment-handoff"><h3>${active(status) ? '等待识别完成' : '暂未进入风险评估'}</h3><p>${message}</p>
        ${coverageBlocked ? '<p>有页面未通过覆盖检查。可以在逐页记录里核对尚未读清的内容。</p>' : ''}
        ${otherErrors.length ? `<details><summary>查看校验问题</summary><ul>${otherErrors.map(error => `<li>${esc(errorLabel(error))}</li>`).join('')}</ul></details>` : ''}
        ${facts ? `<div class="record-links"><a href="${esc(resultLink('facts'))}">查看已保存的 ${number(facts)} 条候选和原页</a>
          ${coverageBlocked ? `<a href="${esc(resultLink('coverage'))}">查看哪些页面还待核对</a>` : ''}</div>` : ''}
        ${recoveryAction()}${!active(status)&&!current.can_resume ?
          '<p>当前任务没有可接续的识别草稿。可以重新选择资料创建任务。</p><button type="button" class="button secondary" data-new-task>重新选择资料</button>' : ''}
        ${dossierLink()}</section>`;
    }
    const assessment=current.assessment||{},cases=assessment.cases||[];
    const caseLinks = cases.map(c => {
      const dossier = './slope-assessment.html?' + new URLSearchParams({source_workspace:'debug',source_case:c.case_id});
      return `<p><a class="button secondary" href="${esc(assessmentUrl(c.url))}">${esc(c.label)} · 查看预评估 →</a>
        <a href="${esc(assessmentUrl(dossier))}">关联到评估工作台</a></p>`;
    }).join('');
    const retryIntake=assessment.status&&assessment.status!=='imported'&&!current.can_resume;
    return `<section class="assessment-handoff"><h3>风险预评估</h3><p class="muted small">Agent 自主整理对象、时期、工况与依据。把同一滑坡的资料归入同一个档案后，可选择同期资料一起评估。</p>${dossierLink()}${caseLinks}${assessment.error?`<p>评估数据尚未接入：${esc(assessment.error)}</p>`:''}${recoveryAction()}${retryIntake?'<button data-intake>重试接收 Agent 提交（不重新识别）</button>':''}<div class="record-links">${(current.documents||[]).map(d=>`<a href="${esc(assessmentUrl('./assessment-debug.html?'+new URLSearchParams({route:'agent',run:runId,document:d.id})))}">${esc(d.name)} · 选择对象 / 查看来源</a>`).join('')}</div><p class="muted small">${cases.length?'原始提交和来源快照已保存。候选结果待复核。':'已有识别事实可以接收。旧任务若未提交评估语义，会明确显示缺口。'}</p></section>`;
  }
  function runErrors(){return (workspace.errors||[]).map(e=>current.assessment?.status==='imported'&&String(e).startsWith('Agent评估数据尚未接入：')?`<details class="small"><summary>评估数据已重新接收；查看首次交接记录</summary><p>${esc(e)}</p></details>`:`<p class="small">${esc(e)}</p>`).join('');}
  function summary(){const s=current.summary||{};return `<div class="run-summary compact-summary"><div class="stats">${[['pages','资料页'],['facts','候选事实'],['objects','识别对象']].map(([k,l])=>`<div><b>${number(s[k])}</b><small>${l}</small></div>`).join('')}</div><div class="summary-context"><p>${active(current.processing_status)?'正在处理资料，结果持续保存':'历史运行结果 · 候选内容待复核'}</p><details><summary>运行记录 · ${shortDate(current.started_at)}</summary><p>${esc(current.model?.model||'')} · ${current.elapsed_seconds!=null?`${(current.elapsed_seconds/60).toFixed(1)} 分钟`:''}</p>${runErrors()}${current.resumed_from?`<p>从中断任务接续。<a href="?run=${encodeURIComponent(current.resumed_from)}">查看原任务与用量</a></p>`:''}</details></div></div>`;}
  function traceHTML(){let lastStep;const rows=workspace.rows||[];
    if(tab==='chat'){
      const messages=rows.filter(r=>r.kind==='assistant');
      const tools=rows.filter(r=>r.kind==='tool');
      const latestTools=(active(current.processing_status)?tools.slice(-6):tools).map(r=>`<div class="tool-row"><span class="tool-icon">${r.status==='complete'?'✓':'↳'}</span><div class="tool-main"><button data-event="${r.detail_seq}">${esc(toolNames[r.tool]||r.tool)}</button><code>${esc(r.path||r.tool)}</code></div><span class="tool-status ${esc(r.status)}">${r.status==='complete'?'完成':r.status==='error'?'失败':'执行中'}</span></div>`).join('');
      return (active(current.processing_status)?latestTools:'')+messages.map(r=>`<article class="message assistant"><div class="message-label">✦ Agent${r.streaming?' · 输出中':''}</div><div class="message-body">${rich(r.text)}</div></article>`).join('')+(!messages.length?'<p class="muted small">Agent 正在阅读和整理，操作记录可在轨迹中查看。</p>':'')+(!active(current.processing_status)?`<details><summary class="muted small">展开最近的工具操作</summary>${latestTools}</details>`:'')+`<button class="load-more" data-tab="trace">查看完整操作轨迹 · ${workspace.count||0} 条消息与操作</button>`;
    }
    let html=traceOffset===-1&&workspace.offset>0?`<button class="load-more" data-earlier="${Math.max(0,workspace.offset-40)}">查看更早步骤 · 共有 ${workspace.count} 条记录</button>`:'';
    for(const r of rows){
      if(r.kind==='tool'){
        if(r.step!==lastStep){html+=`<div class="group-heading">步骤 ${r.step??'—'}</div>`;lastStep=r.step;}
        html+=`<div class="tool-row"><span class="tool-icon">${r.tool==='view_image'?'▧':r.tool?.startsWith('write')?'↳':'⌕'}</span><div class="tool-main"><button data-event="${r.detail_seq}">${esc(toolNames[r.tool]||r.tool)} ↗</button><code title="${esc(r.path)}">${esc(r.path||r.tool)}</code></div><span class="tool-status ${esc(r.status)}">${r.status==='complete'?'✓ 完成':r.status==='error'?'失败':'执行中'}</span></div>`;
      }else if(tab!=='trace'||r.kind==='assistant'){
        const body=rich(r.text);html+=`<article class="message ${esc(r.kind)}"><div class="message-label">${r.kind==='user'?'任务要求':'✦ Agent'}${r.streaming?' · 输出中':''}</div>${r.kind==='user'?`<details><summary>查看完整任务要求</summary><div class="message-body">${body}</div></details>`:`<div class="message-body">${body}</div>`}</article>`;
      }
    }
    if(!rows.length)html+='<p class="muted">等待 Agent 产生消息和操作记录…</p>';
    if(traceOffset!==-1)html+=`<div class="pagination"><button data-earlier="${Math.max(0,workspace.offset-40)}" ${workspace.offset===0?'disabled':''}>更早</button><span>${workspace.offset+1}–${Math.min(workspace.offset+40,workspace.count)} / ${workspace.count}</span><button data-earlier="${workspace.offset+40<workspace.count?workspace.offset+40:-1}">较新 / 最新</button></div>`;
    return html;
  }
  function resultsHTML(){
    const controls=tab==='results'?`<div class="result-toolbar"><select id="resultStage" aria-label="结果类型">${[['facts','候选事实'],['objects','对象与关系'],['coverage','逐页覆盖'],['errors','缺口与错误']].map(([k,l])=>`<option value="${k}" ${k===resultStage?'selected':''}>${l}</option>`).join('')}</select><select id="resultDocument" aria-label="筛选结果文档"><option value="">全部文档</option>${(current.documents||[]).map(d=>`<option value="${esc(d.id)}" ${d.id===documentId?'selected':''}>${esc(d.name)}</option>`).join('')}</select></div>`:'<p class="muted small">逐次模型请求、原始响应和实际用量。大文件可直接下载完整内容。</p>';
    const records=(current.rows||[]).map(({document_id,record:r,artifacts:links})=>{
      if (tab === 'results' && resultStage === 'coverage') {
        const document = (current.documents || []).find(item => item.id === document_id);
        const source = (workspace.pages || []).find(item => item.document_id === document_id && item.page === r.physical_page);
        const remaining = Array.isArray(r.remaining) ? r.remaining : [];
        const status = r.status === 'complete' && !remaining.length ? '覆盖检查通过' :
          r.status === 'unread' ? '未读' : r.status === 'partial' ? '有内容未读完' : '覆盖记录待核对';
        return `<article class="record-card"><h3>${esc(document?.name || document_id)} · 第 ${esc(r.physical_page)} 页</h3><p>${status}</p>
          ${remaining.length ? `<ul>${remaining.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : ''}
          ${source ? `<div class="record-links"><button data-source="${esc(source.path)}">核对原文 · 第 ${esc(r.physical_page)} 页</button></div>` : ''}</article>`;
      }
      if(tab==='calls')return `<article class="record-card"><div class="call-header"><b class="call-number">#${String(r.number).padStart(3,'0')}</b><span class="status">${r.status==='complete'?'完整响应':esc(r.status)}</span></div><p class="muted small">${shortDate(r.started_at)} · ${r.elapsed_seconds??'—'} 秒</p><p>输入 ${number(r.usage?.prompt_tokens)} · 输出 ${number(r.usage?.completion_tokens)}</p><div class="record-links">${(links||[]).map(p=>`<button data-file="${esc(p)}">${p.endsWith('request.json')?'查看完整请求':'查看原始响应'}</button><a href="${artifact(p)}" target="_blank" rel="noopener" download>下载</a>`).join('')}</div><details><summary>调用回执</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details></article>`;
      const evidence=Array.isArray(r.evidence)?r.evidence:[r.evidence].filter(Boolean),quote=evidence.map(v=>v.quote).filter(Boolean).join('\n');
      const object=(current.documents||[]).flatMap(d=>d.objects||[]).find(o=>o.id===r.object);
      const title=[r.object_name||object?.name||r.name||r.object||r.id||document_id,r.field,typeof r.value==='object'?JSON.stringify(r.value):r.value,r.unit].filter(v=>v!==undefined&&v!==null&&v!=='').join(' · ');
      const sourceButtons=(links||[]).map(p=>{
        const page=(workspace.pages||[]).find(x=>x.path===p);
        if(!page)return '';
        const excerpts=evidence.filter(v=>v?.physical_page===page.page &&
          (v.document_id||document_id)===page.document_id).map(v=>v.quote).filter(Boolean).join('\n');
        return `<button data-source="${esc(p)}" data-quote="${esc(excerpts)}">核对原文 · ${esc(page.name)} 第 ${esc(page.page)} 页</button>`;
      }).join('');
      return `<article class="record-card"><h3>${esc(title)}</h3><p class="muted">${esc(r.condition||r.error||'')} ${r.status==='pending'?'· 待核候选':''}</p>${quote?`<blockquote class="fact-quote">${esc(quote)}</blockquote>`:''}<details><summary>完整字段、归属与证据</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details><div class="record-links">${sourceButtons}</div></article>`;
    }).join('')||'<p class="muted">当前没有记录。运行中的产物可以先在右侧生成文件查看。</p>';
    return controls+(tab==='results'?dossierLink():'')+records+`<div class="pagination"><button data-page="${Math.max(0,offset-30)}" ${offset===0?'disabled':''}>上一页</button><span>${current.count?offset+1:0}–${Math.min(offset+30,current.count||0)} / ${current.count||0}</span><button data-page="${offset+30}" ${offset+30>=(current.count||0)?'disabled':''}>下一页</button></div>`;
  }
  function render(){
    $('taskTitle').textContent=draft?'新建智能识别任务':(current.documents||[]).map(d=>d.name.replace(/\.pdf$/i,'')).join('、')||'选择资料，开始识别';$('taskTitle').title=$('taskTitle').textContent;
    $('runStatus').textContent=draft?'新任务':stateNames[current.processing_status]||'新任务';$('runStatus').className='status '+(current.processing_status||'');
    document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));document.querySelector('.task-more>summary').textContent=tab==='trace'?'执行轨迹 ▾':tab==='calls'?'模型调用 ▾':'过程详情';
    const recovery=tab!=='assessment'&&current.can_resume?`<section class="assessment-handoff"><h3>评估交接待处理</h3>${recoveryAction()}</section>`:'';
    const html=draft||!current.run_id?welcome():summary()+recovery+(tab==='assessment'?assessmentHTML():tab==='chat'||tab==='trace'?traceHTML():resultsHTML());
    if(signature!==html){const feed=$('feed'),wasBottom=feed.scrollHeight-feed.scrollTop-feed.clientHeight<70,oldTop=feed.scrollTop;const open=[...feed.querySelectorAll('details[open]')].map((d)=>d.parentElement?.querySelector('h3')?.textContent);feed.innerHTML=html;signature=html;for(const d of feed.querySelectorAll('details'))if(open.includes(d.parentElement?.querySelector('h3')?.textContent)&&d.parentElement?.querySelector('h3'))d.open=true;feed.scrollTop=active(current.processing_status)&&wasBottom?feed.scrollHeight:oldTop;}
    const u=current.usage||{};$('usage').textContent=current.run_id&&!draft?`输入 ${number(u.prompt_tokens)} · 输出 ${number(u.completion_tokens)}`:'过程与结果自动保存';$('usage').title=`缓存命中 ${number(u.prompt_cache_hit_tokens)} · 新输入 ${number(u.prompt_cache_miss_tokens)}`;
    $('activity').textContent=globalJob.stage==='agent_repair'?'Agent 正在修正交接问题':workspace.active?`正在执行第 ${workspace.active.step} 步 · 已请求 ${current.summary?.calls||0} 次`:`Agent 自主处理中 · 已请求 ${current.summary?.calls||0} 次`;
    history();updateActions();$('taskComposer').hidden=!draft&&!!current.run_id&&!active(current.processing_status);$('finishedActions').hidden=draft||!current.run_id||active(current.processing_status);document.querySelector('.tabs').hidden=draft||!current.run_id;remember();if(restoreScroll&&current.run_id){restoreScroll=false;try{$('feed').scrollTop=Number(sessionStorage.getItem('slopekg-agent-scroll:'+runId+':'+tab))||0;}catch(_){}}
  }
  function side(){
    const pages=workspace.pages||[],previous=$('pageSelect').value;
    $('pageSelect').innerHTML=pages.length?pages.map(p=>`<option value="${esc(p.path)}">${esc(p.name)} · 物理 ${p.page} 页</option>`).join(''):'<option value="">暂无原页</option>';
    if(pages.some(p=>p.path===previous))$('pageSelect').value=previous;
    if(sourceToRestore&&pages.some(p=>p.path===sourceToRestore)){$('pageSelect').value=sourceToRestore;sourceToRestore='';}
    if(pageToRestore){const match=pages.find(p=>p.page===pageToRestore&&(!documentId||p.path.includes(documentId)));if(match){$('pageSelect').value=match.path;pageToRestore=0;document.querySelector('.workspace').classList.remove('evidence-hidden');}}
    $('pageCount').textContent=pages.length?`${pages.length} 页`:'';image();
    $('outputFiles').innerHTML=(workspace.files||[]).map(f=>`<button data-file="${esc(f.path)}"><i class="file-icon">▤</i><span>${esc(f.name)}<small>${(f.bytes/1024).toFixed(1)} KB · ${f.path.includes('/scratch/')?'中间文件':'交付文件'}</small></span></button>`).join('')||'<p class="muted small">Agent 生成的报告、事实和裁图会显示在这里。</p>';
    const names={root:'本次任务目录',inputs:'原页与辅助文字',outputs:'识别结果',trace:'Agent 消息与操作',exchanges:'模型完整输入输出'};
    $('storagePaths').innerHTML=Object.entries(workspace.storage||{}).map(([k,v])=>`<dt>${names[k]||esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
    $('auditLinks').innerHTML=(current.artifacts||[]).map(p=>`<a href="${artifact(p)}" target="_blank" rel="noopener">${esc(label(p))} ↗</a>`).join('');
  }
  function image(){const p=$('pageSelect').value,items=workspace.pages||[],i=items.findIndex(x=>x.path===p),key=runId+':'+p;$('pagePosition').textContent=i<0?'—':`${i+1} / ${items.length}`;$('pagePrev').disabled=i<=0;$('pageNext').disabled=i<0||i>=items.length-1;if(key===loadedImage)return;loadedImage=key;$('fullImage').hidden=!p;$('fullImage').href=p?artifact(p):'#';$('imageBox').innerHTML=p?`<img src="${artifact(p)}" alt="${esc(items[i]?.name)} 物理第${items[i]?.page}页" data-file="${esc(p)}">`:'<p>选择任务后查看原图</p>';$('imageBox').querySelector('img')?.addEventListener('error',()=>{$('imageBox').innerHTML='<p>原页加载失败，请刷新或打开完整原图。</p>';});}
  async function refresh(){clearTimeout(timer);const seq=++revision;try{
    const [list,job]=await Promise.all([getJson('/api/agent-recognition/runs'),getJson('/api/pipeline/jobs/latest')]);if(seq!==revision)return;runs=list.runs||[];globalJob=job;busy=['running','queued'].includes(job.status)||runs.some(r=>active(r.processing_status));
    if(pendingResume){
      const next=runs.find(r=>r.resumed_from===pendingResume.source&&!pendingResume.known.has(r.run_id));
      if(next){
        if(runId===pendingResume.source){runId=next.run_id;tab='assessment';offset=0;traceOffset=-1;documentId='';}
        pendingResume=null;
      }else if(job.id===pendingResume.jobId&&!['queued','running'].includes(job.status)){
        if(runId===pendingResume.source)resumeError=job.error||'接续未能启动，请检查原资料后再试。';
        pendingResume=null;
      }
    }
    if(!draft&&!runId)runId=list.latest_id||'';
    if(!draft&&runId){const query=new URLSearchParams({run:runId,stage:tab==='calls'?'calls':resultStage,document:documentId,offset:String(offset),limit:'30'});const [data,trace]=await Promise.all([getJson('/api/agent-recognition?'+query),getJson('/api/agent-recognition/workspace?'+new URLSearchParams({run:runId,offset:String(traceOffset),limit:'40'}))]);if(seq!==revision)return;current=data;workspace=trace;if(tab==='auto')tab=active(current.processing_status)?'chat':'results';side();}
    render();error();
  }catch(e){if(seq===revision)error('读取失败：'+e.message);}finally{if(seq===revision)timer=setTimeout(refresh,busy?1600:6000);}}
  function choose(){if(!$('filesDialog').open)$('filesDialog').showModal();}
  function pickRun(id){saveScroll();$('sourceQuote').hidden=true;$('feed').textContent='正在读取任务…';sourceToRestore='';pageToRestore=0;draft=false;tab='auto';restoreScroll=true;runId=id;offset=0;traceOffset=-1;documentId='';workspace={};current={};loadedImage='';signature='';$('feed').scrollTop=0;error();document.querySelector('.workspace').classList.remove('show-tasks');const url=new URL(location.href);url.searchParams.set('run',id);historyReplace(url);refresh();}
  function historyReplace(url) {
    window.history.replaceState(null, '', url);
    document.querySelector('.method-nav a.active').href = location.href;
  }
  async function start(prepareOnly){if(busy||sending)return;sending=true;updateActions();error();try{const j=await requestJson('/api/pipeline/jobs',{method:'POST',body:{recognizer:'agent',input_pdfs:[...selected],model_profile:$('model').value,prepare_only:prepareOnly,allow_external:!prepareOnly&&$('allow').checked,agent_settings:{record_model_inputs:true,task_instruction:$('instruction').value}}});draft=false;tab='chat';runId='';offset=0;traceOffset=-1;documentId='';signature='';busy=true;current={processing_status:'preparing'};$('feed').innerHTML='<p class="muted">任务已启动，正在准备资料…</p>';await refresh();}catch(e){error(e.status===409?'已有任务运行，完成或停止后再开始。':e.message);}finally{sending=false;updateActions();}}
  async function resume(){
    if(busy||sending||pendingResume||!current.can_resume)return;
    const source=runId,known=new Set(runs.map(r=>r.run_id));
    const names=(current.documents||[]).map(d=>{
      const pages=d.selection?.selected_pages||[];
      return d.name+(pages.length ? `（原选定 ${pages.length} 页）` : '');
    }).join('\n');
    if(!window.confirm(`继续核对旧任务的草稿，将把原选定页面及草稿再次发送到 ${current.model?.base_url||'https://api.deepseek.com'}。\n\n${names}\n\n确认后开始接续？`))return;
    sending=true;resumeError='';render();
    try{
      const job=await requestJson('/api/pipeline/jobs',{method:'POST',
        body:{recognizer:'agent',resume_run:source,prepare_only:false,allow_external:true}});
      pendingResume={source,known,jobId:job.id};
      await refresh();
    }catch(e){
      if(runId===source)resumeError=e.status===409?'已有任务正在处理，请完成或停止后再接续。':e.message;
    }finally{sending=false;render();}
  }
  async function detail(title,loader){const seq=++detailRevision;$('detailTitle').textContent=title;$('detailBody').textContent='正在读取…';if(!$('detailDialog').open)$('detailDialog').showModal();try{const html=await loader();if(seq===detailRevision)$('detailBody').innerHTML=html;}catch(e){if(seq===detailRevision)$('detailBody').textContent='读取失败：'+e.message;}}
  function previewFile(path){const id=runId,url=artifact(path,id);detail(label(path),async()=>{if(/\.(png|jpg|jpeg|webp)$/i.test(path))return `<img src="${url}" alt="${esc(label(path))}">`;const response=await fetch(url);if(!response.ok)throw Error('HTTP '+response.status);const size=Number(response.headers.get('Content-Length'));if(size>2*1024*1024){await response.body?.cancel();return `<p>完整文件 ${(size/1024/1024).toFixed(1)} MB，包含全部原始内容。</p><p><a href="${url}" target="_blank" rel="noopener" download>下载完整文件</a> · <a href="${url}" target="_blank" rel="noopener">在新页打开完整内容</a></p>`;}const text=await response.text();return `<p><a href="${url}" target="_blank" rel="noopener" download>下载完整文件</a></p>${path.endsWith('.md')?`<div class="message-body">${rich(text)}</div>`:`<pre>${esc(text)}</pre>`}`;});}
  document.addEventListener('click',e=>{const b=e.target.closest('button,a,img');if(!b)return;if(b.dataset.run)pickRun(b.dataset.run);if(b.hasAttribute('data-choose'))choose();if(b.hasAttribute('data-new-task'))$('newTask').click();if(b.dataset.tab){saveScroll();tab=b.dataset.tab;restoreScroll=false;document.querySelector('.task-more').open=false;offset=0;signature='';$('feed').scrollTop=0;refresh();}if(b.hasAttribute('data-earlier')){traceOffset=Number(b.dataset.earlier);signature='';refresh();}if(b.hasAttribute('data-page')){offset=Number(b.dataset.page);signature='';$('feed').scrollTop=0;refresh();}if(b.dataset.file)previewFile(b.dataset.file);if(b.dataset.event){const id=runId;detail('Agent 工具操作 · 完整输入与返回',async()=>`<pre>${esc(JSON.stringify((await getJson('/api/agent-recognition/workspace?'+new URLSearchParams({run:id,event:b.dataset.event}))).event,null,2))}</pre>`);}if(b.dataset.source){$('pageSelect').value=b.dataset.source;$('sourceQuote').textContent=b.dataset.quote||'';$('sourceQuote').hidden=!b.dataset.quote;showSide(false);document.querySelector('.workspace').classList.remove('evidence-hidden');document.querySelector('.workspace').classList.add('show-evidence');image();remember();}});
  $('feed').addEventListener('click',async e=>{const b=e.target.closest('[data-intake]');if(!b)return;b.disabled=true;try{await requestJson('/api/agent-recognition/assessment',{method:'POST',body:{run_id:runId}});await refresh();}catch(err){error(err.message);}finally{b.disabled=false;}});
  $('feed').addEventListener('click',e=>{if(e.target.closest('[data-resume]'))resume();});
  $('feed').addEventListener('change',e=>{if(e.target.id==='resultStage'){resultStage=e.target.value;offset=0;refresh();}if(e.target.id==='resultDocument'){documentId=e.target.value;offset=0;refresh();}});
  $('files').addEventListener('change',e=>{if(e.target.type==='checkbox'){if(e.target.checked)selected.add(e.target.value);else selected.delete(e.target.value);$('allow').checked=false;updateActions();}});
  $('chooseFiles').onclick=choose;$('confirmFiles').onclick=()=>{$('filesDialog').close();updateActions();};$('searchFiles').oninput=renderFiles;$('allow').onchange=updateActions;$('model').onchange=()=>{$('allow').checked=false;endpoint();};
  $('newTask').onclick=()=>{saveScroll();$('sourceQuote').hidden=true;draft=true;restoreScroll=false;clearTimeout(timer);revision++;tab='chat';runId='';current={};workspace={};selected.clear();$('allow').checked=false;$('instruction').value='';signature='';document.querySelector('.workspace').classList.remove('show-tasks');const url=new URL(location.href);url.search='?new=1';historyReplace(url);side();render();choose();};
  $('refresh').onclick=()=>{catalog().catch(e=>error(e.message));refresh();};$('run').onclick=()=>start(false);$('prepare').onclick=()=>start(true);
  $('stop').onclick=async()=>{sending=true;updateActions();try{await requestJson('/api/agent-recognition/stop',{method:'POST',body:{run_id:runId}});await refresh();}catch(e){error(e.message);}finally{sending=false;updateActions();}};
  $('pageSelect').onchange=()=>{$('sourceQuote').hidden=true;image();remember();};$('pagePrev').onclick=()=>{$('pageSelect').selectedIndex--;$('sourceQuote').hidden=true;image();remember();};$('pageNext').onclick=()=>{$('pageSelect').selectedIndex++;$('sourceQuote').hidden=true;image();remember();};
  function showSide(outputs){$('sourcePanel').hidden=outputs;$('outputPanel').hidden=!outputs;$('showSources').classList.toggle('active',!outputs);$('showOutputs').classList.toggle('active',outputs);}
  $('showSources').onclick=()=>showSide(false);$('showOutputs').onclick=()=>showSide(true);$('toggleEvidence').onclick=()=>{const w=document.querySelector('.workspace');if(innerWidth<=1100)w.classList.toggle('show-evidence');else w.classList.toggle('evidence-hidden');};
  $('detailDialog').addEventListener('close',()=>{detailRevision++;});
  $('toggleTasks').onclick=()=>document.querySelector('.workspace').classList.toggle('show-tasks');
  $('closeEvidence').onclick=()=>{const w=document.querySelector('.workspace');w.classList.remove('show-evidence');w.classList.add('evidence-hidden');remember();};
  $('upload').onclick=()=>$('uploadInput').click();$('uploadInput').onchange=async()=>{const chosen=[...$('uploadInput').files];if(!chosen.length)return;const data=new FormData();chosen.forEach(f=>data.append('files',f,f.name));$('upload').disabled=true;$('uploadStatus').textContent='正在上传并建立本地资料索引…';try{const response=await fetch('/api/documents/upload',{method:'POST',body:data}),result=await response.json();if(!response.ok)throw Error(result.error||response.status);await catalog();for(const f of result.files||[])selected.add(f.file_name);$('allow').checked=false;renderFiles();$('uploadStatus').textContent=result.message;refresh();}catch(e){$('uploadStatus').textContent=e.message;}finally{$('upload').disabled=false;$('uploadInput').value='';}};
  if(query.get('evidence')==='1'){document.querySelector('.workspace').classList.remove('evidence-hidden');document.querySelector('.workspace').classList.add('show-evidence');}
  $('feed').addEventListener('scroll',saveScroll,{passive:true});window.addEventListener('pagehide',saveScroll);
  catalog().then(()=>{if(draft){render();}}).catch(e=>error(e.message));refresh();
})();
