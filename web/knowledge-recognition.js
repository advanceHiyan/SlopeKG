(function () {
  'use strict';
  const {getJson, escapeHtml: esc} = window.SlopeKGApi;
  const el = id => document.getElementById(id), selected = new Set();
  let profiles = [], files = [], entities = [], busy = false, offset = 0, generation = 0, pollTimer;
  const states = {verified_model:'模型核验通过 · 待人工确认',pending:'待审',rejected:'已拒绝'};
  const runStates = {prepared:'本地准备完成',completed:'处理结束',partial:'部分完成',failed:'处理失败',stale:'结果已过期',running:'正在识别',not_generated:'尚未生成'};
  function error(message='') {el('error').textContent=message;el('error').hidden=!message;}
  function actions() {
    const disabled=busy || !selected.size || !profiles.some(p=>p.id===el('model').value);
    el('prepare').disabled=disabled;el('run').disabled=disabled || !el('allow').checked;
    el('model').disabled=busy;el('allow').disabled=busy;
    for(const input of el('files').querySelectorAll('input')) input.disabled=busy;
  }
  function renderFiles() {
    const q=el('searchFiles').value.toLowerCase();
    el('files').innerHTML=files.filter(f=>f.path.toLowerCase().includes(q)).map(f=>`<label class="recognition-file"><input type="checkbox" value="${esc(f.path)}" ${selected.has(f.path)?'checked':''}><span>${esc(f.path)}</span></label>`).join('') || '没有匹配的PDF。';actions();
  }
  function endpoint() {const p=profiles.find(p=>p.id===el('model').value);el('endpoint').textContent=p?`${p.model} · ${p.endpoint}`:'暂无可用模型';actions();}
  async function catalog() {
    try {
      const [a,b]=await Promise.all([getJson('/api/recognition/profiles'),getJson('/api/recognition/documents')]);
      profiles=a.profiles||[];files=b.documents||[];const old=el('model').value;
      for(const path of selected) if(!files.some(f=>f.path===path)) selected.delete(path);
      el('model').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');
      if(profiles.some(p=>p.id===old)) el('model').value=old;
      else if(profiles.some(p=>p.id==='deepseek-knowledge')) el('model').value='deepseek-knowledge';
      endpoint();renderFiles();
    } catch(e) {profiles=[];files=[];el('model').innerHTML='';el('endpoint').textContent='模型列表暂不可读';el('files').textContent='资料暂不可读';actions();error(e.message);}
  }
  function source(f,e) {
    const href=f.source_path && Number.isInteger(e.page)?`/data/rawPDF/${f.source_path.split('/').map(encodeURIComponent).join('/')}#page=${e.page}`:'';
    return `<blockquote>${esc(e.quote)}<br>${href?`<a href="${esc(href)}" target="_blank" rel="noopener">${esc(f.file_name)} · 物理页${e.page}</a>`:`物理页${esc(e.page)}`}</blockquote>`;
  }
  async function results() {
    const current=++generation, entity=el('entity').value;
    try {
      const d=await getJson(`/api/knowledge-recognition?entity_id=${encodeURIComponent(entity)}&status=${encodeURIComponent(el('status').value)}&offset=${offset}&limit=30`);
      if(current!==generation)return;entities=d.entities||[];
      el('entity').innerHTML='<option value="">全部对象</option>'+entities.map(e=>`<option value="${esc(e.id)}">${esc(e.name)} · ${e.fact_count}项</option>`).join('');el('entity').value=entity;
      const chosen=entities.find(e=>e.id===entity);el('entityInfo').textContent=chosen?`${chosen.description} 来源分块：${chosen.chunk_ids.join('、')}`:'';
      el('note').textContent=`${runStates[d.processing_status]||d.processing_status}。${d.metric_note}`;
      const s=d.summary||{};el('metrics').innerHTML=[['entities','对象/概念'],['facts','事实'],['verified_model','模型通过'],['pending','待审']].map(([k,label])=>`<div class="metric-card"><strong>${esc(s[k]||0)}</strong><span>${label}</span></div>`).join('');
      el('coverage').textContent=(d.documents||[]).map(v=>v.coverage.total_pages?`${v.file_name}：正文处理 ${runStates[v.coverage.text_processing_status]||v.coverage.text_processing_status}，归属分块 ${v.coverage.attributed_chunks}/${v.coverage.total_chunks}；图形待读页 ${(v.coverage.unread_graphics_pages||[]).length}`:`${v.file_name}：正在建立覆盖清单`).join('；');
      el('rows').innerHTML=(d.rows||[]).map(f=>`<tr><td><strong>${esc(f.object)}</strong><small class="table-subtext">${esc(f.scope)} · ${esc(f.role)}</small></td><td>${esc(f.field)}<br><strong>${esc(f.value)} ${esc(f.unit)}${f.unit_basis?'/'+esc(f.unit_basis):''}</strong><small class="table-subtext">${esc([f.condition,f.time,f.qualifier].filter(Boolean).join('；'))}</small></td><td>${esc(states[f.status]||f.status)}<details><summary>原文与检查</summary><p>${esc(f.reason)}</p>${(f.evidence||[]).map(e=>source(f,e)).join('')}</details></td></tr>`).join('') || '<tr><td colspan="3">暂无当前筛选结果</td></tr>';
      const names=new Map(entities.map(e=>[e.id,e.name]));el('relations').innerHTML=(d.relations||[]).map(r=>`<details><summary>${esc(r.object)} → ${esc(r.predicate)} → ${esc(names.get(r.target_id)||r.target_id)} <small>${esc(states[r.status]||r.status)}</small></summary><p>${esc(r.scope)} · ${esc(r.reason)}</p>${(r.evidence||[]).map(e=>source(r,e)).join('')}</details>`).join('') || '暂无关系候选';
      if(d.relation_count>(d.relation_limit||200))el('relations').insertAdjacentHTML('beforeend',`<p>共 ${Number(d.relation_count)} 条关系，本页显示前 ${Number(d.relation_limit||200)} 条，请按对象缩小范围。</p>`);
      el('pageInfo').textContent=`${d.count?offset+1:0}～${Math.min(offset+30,d.count||0)} / ${d.count||0}`;el('prev').disabled=!offset;el('next').disabled=offset+30>=(d.count||0);
    }catch(e){if(current!==generation)return;el('rows').textContent='结果暂不可读';el('metrics').textContent='';el('coverage').textContent='';el('entityInfo').textContent='';el('relations').textContent='';el('pageInfo').textContent='暂不可读';el('prev').disabled=el('next').disabled=true;error(e.message);}
  }
  async function poll() {
    try {
      const job=await getJson('/api/pipeline/jobs/latest');busy=['queued','running'].includes(job.status);
      el('job').textContent=job.status==='none'?'暂无任务':`${job.progress||0}% · ${job.message||job.status}`;actions();
      if(busy)pollTimer=setTimeout(poll,1200);else await results();
    }catch(e){busy=false;actions();error(e.message);}
  }
  async function start(prepared) {
    if(busy)return;busy=true;actions();error();
    try {
      const max=Number(el('maxCalls').value), concurrency=Number(el('concurrency').value);
      if(!Number.isInteger(max)||max<1||max>10000||!Number.isInteger(concurrency)||concurrency<1||concurrency>16)throw Error('调用上限或并发无效');
      const response=await fetch('/api/pipeline/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recognizer:'knowledge',input_pdfs:[...selected],model_profile:el('model').value,prepare_only:prepared,allow_external:!prepared&&el('allow').checked,knowledge_settings:{max_calls:max,model_concurrency:concurrency}})});
      const body=await response.json();if(!response.ok&&response.status!==409)throw Error(body.error||`HTTP ${response.status}`);clearTimeout(pollTimer);await poll();
    }catch(e){busy=false;actions();error(e.message);}
  }
  el('files').addEventListener('change',e=>{if(e.target.type==='checkbox'){if(e.target.checked)selected.add(e.target.value);else selected.delete(e.target.value);actions();}});
  el('searchFiles').addEventListener('input',renderFiles);el('model').addEventListener('change',()=>{el('allow').checked=false;endpoint();});el('allow').addEventListener('change',actions);
  el('prepare').onclick=()=>start(true);el('run').onclick=()=>start(false);el('entity').onchange=el('status').onchange=()=>{offset=0;results();};
  el('prev').onclick=()=>{offset=Math.max(0,offset-30);results();};el('next').onclick=()=>{offset+=30;results();};
  el('refresh').onclick=()=>{catalog();results();};catalog();results();poll();
})();
