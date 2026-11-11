(function(){
  'use strict';
  const {getJson,requestJson,escapeHtml:esc}=window.SlopeKGApi,$=id=>document.getElementById(id),q=new URLSearchParams(location.search);
  const routeNames={basic:'基读',knowledge:'知联（归档）',visual:'视读（归档）',document:'整读',agent:'智能识别'};
  const statusNames={pending:'历史身份待核对',confirmed:'身份已登记',legacy:'既有边坡身份'};
  const fields={namespace:'编号所属范围（单位 / 项目 / 编码体系）',external_id:'甲方或外部边坡编号',route_code:'路线编号',side:'左右侧',start_station_m:'起点桩号',end_station_m:'终点桩号'};
  let catalog={slopes:[],unassigned:[],snapshots:[]},allSlopes=[],current=null,selection='',binding=null,generation=0,evidence=[],identityPreview=null,masterIdentity=null,previewGeneration=0;
  const value=v=>typeof v==='object'?JSON.stringify(v,null,2):String(v??'');
  const date=v=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'未记录';
  function note(text){$('message').textContent=text;}
  function station(v){if(v===undefined||v===null||v==='')return '';const n=Number(v),text=String(v);if(!Number.isFinite(n)||n<0||text.includes('e'))return text;const whole=Math.trunc(n),fraction=text.includes('.')?'.'+text.split('.')[1]:'';return `K${Math.floor(whole/1000)}+${String(whole%1000).padStart(3,'0')}${fraction}`;}
  function stationMeters(v){const match=/^K(\d+)\+(\d{1,3}(?:\.\d+)?)$/i.exec(v);return match?String(Number(match[1])*1000+Number(match[2])):v;}
  function attributesHTML(attrs={}){return '<dl class="identity-attributes">'+Object.entries(fields).map(([k,label])=>`<div><dt>${esc(label)}</dt><dd>${esc(k.endsWith('_m')?(station(attrs[k])||'未提供'):(attrs[k]??'未提供'))}</dd></div>`).join('')+'</dl>';}
  function identityInputs(host,prefix,attrs={}){
    $(host).innerHTML=Object.entries(fields).map(([k,label])=>`<label>${esc(label)}${k==='side'?`<select id="${prefix}-${k}"><option value="">未提供</option><option>左侧</option><option>右侧</option></select>`:`<input id="${prefix}-${k}" maxlength="200" ${k.endsWith('_m')?'placeholder="如 K2402+458，也可填米数"':''}>`}</label>`).join('');
    for(const key of Object.keys(fields))$(prefix+'-'+key).value=key.endsWith('_m')?station(attrs[key]):(attrs[key]??'');
  }
  function readAttributes(prefix){return Object.fromEntries(Object.keys(fields).map(k=>[k,k.endsWith('_m')?stationMeters($(prefix+'-'+k).value.trim()):$(prefix+'-'+k).value.trim()]).filter(([,v])=>v!==''));}
  function renderList(){
    const text=$('search').value.trim().toLowerCase(),filter=$('filter').value;
    let rows=filter==='unassigned'?catalog.unassigned.map(r=>({...r,name:r.label,binding:true})):catalog.slopes.filter(r=>filter!=='pending'||r.identity_status==='pending');
    rows=rows.filter(r=>[r.name,r.id,...Object.values(r.identity?.attributes||{})].join(' ').toLowerCase().includes(text));
    $('rows').innerHTML=rows.map(r=>`<button class="library-row ${selection===r.id?'selected':''}" data-id="${esc(r.id)}" data-binding="${!!r.binding}"><strong>${esc(r.name)}</strong><small>${r.binding?`${r.source_objects||1} 个来源对象 · 身份待核对`:`${r.sources} 个已关联来源对象${r.legacy_fields?' · 有既有字段':''}`}</small></button>`).join('')||'<p class="library-empty">当前没有符合条件的记录。</p>';
    $('counts').innerHTML=`<span><b>${catalog.slopes.length}</b>边坡主档</span><button id="showQueue" class="button secondary"><b>${catalog.unassigned.length}</b> 组待核对资料</button><span><b>${catalog.snapshots.length}</b>已保存识别记录</span>`;
    $('showQueue').onclick=()=>{$('filter').value='unassigned';renderList();if(catalog.unassigned.length)select(catalog.unassigned[0].id,true);};
    $('libraryState').textContent=catalog.unassigned.length?'资料已安全保存。请在待核对区关联已有边坡或确认新建；未核对来源不会自动增加边坡主档。':!catalog.snapshots.length?'当前展示既有边坡字段，尚未归档识别原件。可从已有识别任务进入本页“保存 / 重试入库”，无需重新调用模型。':'不同批次按永久边坡 ID 归集，原文、候选值和识别版本分别保留。';
  }
  async function refresh(){
    const params=new URLSearchParams();for(const key of ['snapshot','run','route'])if(q.get(key))params.set(key,q.get(key));
    catalog=await getJson('/api/slope-library?'+params);allSlopes=(params.size?await getJson('/api/slope-library'):catalog).slopes;renderList();
  }
  function factsHTML(facts){
    if(!facts.length)return '<p class="muted">这个对象尚无可展示的识别候选。</p>';
    return '<div class="table-wrap"><table class="library-facts"><thead><tr><th>原文属性</th><th>候选值</th><th>状态 / 限定</th><th>原页证据</th></tr></thead><tbody>'+facts.map(f=>{
      const raw=f.raw,links=f.evidence.map(e=>{if(!e.image_url)return '<small>原页定位待核对</small>';const i=evidence.push(e)-1;return `<button data-evidence="${i}">第 ${Number(e.physical_page||e.page)} 页 ↗</button>`;}).join('');
      return `<tr><td>${esc(f.field_label||raw.field||raw.property||'未标属性')}<details><summary>记录详情</summary><small>${esc(raw.id||'')} · ${esc(raw.role||'')} · ${esc(raw.model_status||raw.status||'')}</small></details></td><td>${esc(value(raw.value))} ${esc(raw.unit||'')}</td><td><span class="library-badge ${f.issues.length?'pending':''}">${f.issues.length?'暂不采用':'识别候选，待核对'}</span><small>${esc([raw.condition,raw.qualifier,raw.scope,raw.time].filter(Boolean).join('；'))}</small>${f.issues.map(i=>`<small>${esc(i)}</small>`).join('')}</td><td>${links}<small>${esc(f.evidence.map(e=>e.quote||'').join('；'))}</small></td></tr>`;
    }).join('')+'</tbody></table></div>';
  }
  function versionHTML(v){
    const sourceLink=v.route==='agent'?'./pdf-ingestion.html?'+new URLSearchParams({run:v.run_id,tab:'results',stage:'facts',document:v.document_id}):'./recognition-debug.html?'+new URLSearchParams({route:v.route,run:'live',document:v.document_id});
    return `<p class="small muted">${esc(v.document)} · ${esc(routeNames[v.route]||v.route)}<br>资料日期：${esc(v.document_date||'未记录')} · 识别时间：${esc(date(v.recognized_at))} · 入库时间：${esc(date(v.created_at))}<br><a target="_blank" rel="noopener" href="${esc(v.pdf_url)}">原 PDF ↗</a> · <a href="${esc(window.SlopeKGNav.withReturn(sourceLink))}">回到识别任务核对</a></p>${v.errors.length?`<details><summary>本次识别有 ${v.errors.length} 项待处理，已保存内容仍可查阅</summary>${v.errors.map(e=>`<p class="small">${esc(value(e))}</p>`).join('')}</details>`:''}${factsHTML(v.object.facts)}${v.figures.length?`<details><summary>原文图片附件 · ${v.figures.length} 处（归属待核对）</summary>${v.figures.map(f=>`<a target="_blank" rel="noopener" href="${esc(f.image_url)}"><img loading="lazy" class="library-figure" src="${esc(f.image_url)}" alt="归档图片，第 ${esc(f.page)} 页"><p>第 ${esc(f.page)} 页 · ${esc(f.caption||'图片')} · 只归档</p></a>`).join('')}</details>`:''}`;
  }
  function renderDetail(){
    evidence=[];const slope=current.slope,identity=current.identity;
    let html=`<h2>${esc(slope?.name||current.sources[0]?.label||'待核对资料')}</h2><p><span class="library-badge">${slope?(statusNames[slope.identity_status]||'身份待核对'):'资料已保存 · 身份待核对'}</span> <span class="muted small">归属核对不等于确认字段值。</span></p>`;
    if(slope)html+=`<section class="identity-box"><div class="identity-title"><strong>永久系统 ID：<code>${esc(slope.id)}</code></strong><button id="editIdentity" class="button secondary">维护唯一身份</button></div>${attributesHTML(identity?.attributes)}<p class="small muted">${identity?.reviewed?'身份字段已人工登记':'身份字段来自既有资料，尚未人工登记'}；名称、编号或材料批次变化不会改变系统 ID。</p>${identity?.history?.length?`<details><summary>身份维护历史（${identity.history.length}）</summary>${identity.history.map(h=>`<p>${esc(date(h.at))} · ${esc(h.actor)}：${esc(h.reason)}</p>`).join('')}</details>`:''}</section>`;
    if(current.legacy_fields?.length)html+=`<section><h3>既有字段资料</h3><p class="small muted">既有资料中的历史提取值，尚需原文核对。<a href="./slopes.html?slope_id=${encodeURIComponent(slope.id)}">查看旧档案及来源详情 ↗</a></p><dl class="legacy-fields">${current.legacy_fields.map(f=>`<div><dt>${esc(f.label)}</dt><dd>${typeof f.value==='object'?`<details><summary>展开原有记录</summary><pre>${esc(value(f.value))}</pre></details>`:esc(value(f.value))}</dd></div>`).join('')}</dl></section>`;
    if(!current.sources.length)html+='<p class="library-warning">此边坡尚未关联新的识别原件。已有字段可在上方查看；新材料保存后将在待核对区确认归属。</p>';
    html+=current.sources.map((s,i)=>`<section class="library-source"><header><div><h3>${esc(s.label)}</h3><span class="small muted">${s.versions.length} 份识别记录 · ${s.identity_ready?'归属已核对':'归属待核对'}</span></div><button class="button secondary" data-bind="${i}">${s.slope_id?'核对 / 更正归属':'核对身份 / 关联边坡'}</button></header>${s.identity_issues?.length?`<p class="library-warning">${esc(s.identity_issues.join('；'))}${s.identity_ready?'（已记录人工处理依据）':''}</p>`:''}${s.versions.length?versionHTML(s.versions[0]):''}${s.versions.length>1?`<details><summary>其他识别版本（${s.versions.length-1}）</summary>${s.versions.slice(1).map(versionHTML).join('')}</details>`:''}<details><summary>归属处理记录（${s.history.length}）</summary>${s.history.map(h=>`<p>${esc(date(h.at))} · ${esc(h.actor)}：${esc(h.reason)}<br><small>${esc(h.from_slope||'待核对')} → ${esc(h.to_slope||'待核对')}</small></p>`).join('')||'<p>尚无人工归属记录。</p>'}</details></section>`).join('');
    $('detail').innerHTML=html;
  }
  async function select(id,isBinding=false){
    const n=++generation;selection=id;renderList();$('detail').innerHTML='<p>正在读取档案…</p>';
    try{const d=await getJson('/api/slope-library?'+new URLSearchParams({[isBinding?'binding_id':'slope_id']:id}));if(n!==generation)return;current=d;renderDetail();const p=new URLSearchParams(q);p.delete('slope_id');p.delete('binding_id');p.set(isBinding?'binding_id':'slope_id',id);history.replaceState(null,'','?'+p);}catch(e){if(n===generation)$('detail').textContent=e.message;}
  }
  function targetSummary(){
    const target=$('bindTarget').value,newIdentity=target==='__new',release=target==='__unassigned';
    $('newIdentityFields').hidden=!newIdentity;$('conflictControls').hidden=true;$('identityDifferences').innerHTML='';
    const candidate=identityPreview?.candidates.find(c=>c.slope_id===target);
    if(candidate){
      const conflicts=[...identityPreview.source.conflicts,...candidate.conflicts];
      $('identityDifferences').innerHTML=`<div class="identity-box"><strong>目标：${esc(candidate.name)}</strong><small class="block">${esc(candidate.slope_id)}</small>${attributesHTML(candidate.attributes)}<p>${esc(candidate.reasons.join('；')||'没有足够的自动匹配依据，请对照原页核对。')}</p>${conflicts.length?`<p class="library-warning">${esc(conflicts.join('；'))}</p>`:''}</div>`;
      $('conflictControls').hidden=!conflicts.length;
    }else if(newIdentity){
      const attrs=readAttributes('new'),source=identityPreview?.source,conflicts=[...(source?.conflicts||[])];
      for(const k of ['namespace','route_code','side'])if(attrs[k]&&source?.attributes[k]&&attrs[k].replace(/\s/g,'').toUpperCase()!==String(source.attributes[k]).replace(/\s/g,'').toUpperCase())conflicts.push(fields[k]+'与来源不同');
      if(attrs.start_station_m!==undefined&&attrs.end_station_m!==undefined&&source?.attributes.start_station_m!==undefined&&source?.attributes.end_station_m!==undefined&&Math.max(Number(attrs.start_station_m),Number(source.attributes.start_station_m))>Math.min(Number(attrs.end_station_m),Number(source.attributes.end_station_m)))conflicts.push('桩号范围与来源不重合');
      $('conflictControls').hidden=!conflicts.length;
      if(conflicts.length)$('identityDifferences').innerHTML=`<p class="library-warning">${esc(conflicts.join('；'))}</p>`;
    }else if(release)$('identityDifferences').textContent='解除本次来源归属，资料退回待核对区。原件、候选和历史记录均保留。';
    $('saveBinding').disabled=!identityPreview||!target;
  }
  async function loadPreview(){
    const n=++previewGeneration;$('saveBinding').disabled=true;
    try{
      const target=$('bindTarget').value,params=new URLSearchParams({binding_id:binding.id});if(target&&!target.startsWith('__'))params.set('slope_id',target);
      const result=await getJson('/api/slope-library/identity?'+params);if(n!==previewGeneration)return;
      identityPreview=result;targetSummary();
    }catch(e){$('bindError').textContent=e.message;}
  }
  async function openBinding(index){
    binding=current.sources[index];identityPreview=null;$('bindSource').textContent='来源对象：'+binding.label;
    $('bindTarget').innerHTML='<option value="">请选择已有边坡或确认新建</option>'+allSlopes.map(s=>`<option value="${esc(s.id)}">${esc(s.name)} · ${esc(s.identity?.attributes?.external_id||s.id)}</option>`).join('')+'<option value="__new">确认是新边坡，创建永久身份</option>'+(binding.slope_id?'<option value="__unassigned">解除归属，退回待核对区</option>':'');
    $('bindTarget').value=binding.slope_id||'';$('newName').value=binding.label;$('confirm').checked=false;$('includeRelated').checked=true;$('reason').value='';$('bindError').textContent='';$('conflictConfirmed').checked=false;$('conflictReason').value='';$('distinctConfirmed').checked=false;$('distinctReason').value='';$('identitySource').textContent='正在提取身份线索…';$('identityMatches').innerHTML='';identityInputs('newIdentityInputs','new');targetSummary();$('bindDialog').showModal();
    await loadPreview();if(!identityPreview)return;
    const source=identityPreview.source;identityInputs('newIdentityInputs','new',source.attributes);
    const original=binding.versions[0];
    $('identitySource').innerHTML=`<strong>本来源的身份线索（尚未核对）</strong>${attributesHTML(source.attributes)}${source.conflicts.length?`<p class="library-warning">${esc(source.conflicts.join('；'))}</p>`:''}${original?`<a target="_blank" rel="noopener" href="${esc(original.pdf_url)}">打开来源原件 ↗</a>`:''}<p class="small">资料及其候选已经保存，关闭窗口不会丢失。以下建议仅辅助查找，需对照原件确认。</p>`;
    $('identityMatches').innerHTML='<h3>可能对应的已有边坡</h3>'+identityPreview.candidates.slice(0,8).map(c=>`<button type="button" class="identity-match" data-target="${esc(c.slope_id)}"><strong>${esc(c.name)}</strong><small>${esc(c.reasons.join('；'))}</small>${c.conflicts.length?`<small class="identity-conflict">${esc(c.conflicts.join('；'))}</small>`:''}</button>`).join('')+(identityPreview.candidates.length?'':'<p class="muted">没有找到足够的匹配线索；可从列表查找或核对后新建。</p>');
  }
  $('rows').onclick=e=>{const b=e.target.closest('[data-id]');if(b)select(b.dataset.id,b.dataset.binding==='true');};
  $('search').oninput=renderList;$('filter').onchange=renderList;
  $('detail').onclick=async e=>{
    const b=e.target.closest('[data-bind]'),p=e.target.closest('[data-evidence]');
    if(p){const item=evidence[Number(p.dataset.evidence)];$('evidenceImage').src=item.image_url;$('fullEvidence').href=item.image_url;$('evidenceQuote').textContent=item.quote||'';$('evidenceDialog').showModal();}
    if(b)await openBinding(Number(b.dataset.bind));
    if(e.target.closest('#editIdentity')){
      try{masterIdentity=await getJson('/api/slope-library/identity?'+new URLSearchParams({slope_id:current.slope.id}));$('identityId').textContent='永久系统 ID：'+masterIdentity.slope_id;$('identityName').value=masterIdentity.name;identityInputs('masterIdentityInputs','master',masterIdentity.attributes);$('identityConfirmed').checked=false;$('identityReason').value='';$('identityError').textContent='';$('identityDialog').showModal();}catch(err){note(err.message);}
    }
  };
  $('identityMatches').onclick=e=>{const b=e.target.closest('[data-target]');if(b){$('bindTarget').value=b.dataset.target;loadPreview();}};
  $('bindTarget').onchange=()=>{targetSummary();loadPreview();};
  $('newIdentityFields').oninput=targetSummary;
  $('closeEvidence').onclick=()=>$('evidenceDialog').close();$('cancel').onclick=()=>{++previewGeneration;$('bindDialog').close();};$('cancelIdentity').onclick=()=>$('identityDialog').close();
  $('bindForm').onsubmit=async e=>{
    e.preventDefault();if(!identityPreview||$('saveBinding').disabled)return;const submit=$('saveBinding');submit.disabled=true;
    try{
      const target=$('bindTarget').value,candidate=identityPreview.candidates.find(c=>c.slope_id===target);
      const result=await requestJson('/api/slope-library',{method:'POST',body:{operation:'bind',binding_id:binding.id,revision:identityPreview.binding.revision,slope_id:target.startsWith('__')?'':target,release:target==='__unassigned',target_revision:candidate?.revision,name:$('newName').value,identity:readAttributes('new'),actor:$('actor').value,reason:$('reason').value,confirmed:$('confirm').checked,include_related:$('includeRelated').checked,related_revisions:Object.fromEntries(identityPreview.related.map(s=>[s.id,s.revision])),conflict_confirmed:$('conflictConfirmed').checked,conflict_reason:$('conflictReason').value,distinct_confirmed:$('distinctConfirmed').checked,distinct_reason:$('distinctReason').value}});
      await refresh();if(result.slope){$('filter').value='all';await select(result.slope.id);}else{$('filter').value='unassigned';await select(binding.id,true);}$('bindDialog').close();note('身份处理已保存。不同材料的原文、候选值和处理记录分别保留。');
    }catch(err){$('bindError').textContent=err.message;}finally{submit.disabled=false;}
  };
  $('identityForm').onsubmit=async e=>{
    e.preventDefault();const submit=e.submitter;submit.disabled=true;
    try{await requestJson('/api/slope-library/identity',{method:'POST',body:{slope_id:masterIdentity.slope_id,revision:masterIdentity.revision,name:$('identityName').value,attributes:readAttributes('master'),actor:$('identityActor').value,reason:$('identityReason').value,confirmed:$('identityConfirmed').checked}});await refresh();await select(masterIdentity.slope_id);$('identityDialog').close();note('边坡身份已更新，永久系统 ID 保持不变。');}catch(err){$('identityError').textContent=err.message;}finally{submit.disabled=false;}
  };
  async function intake(){
    const host=$('intake');host.hidden=false;host.innerHTML='<h2>本次识别资料</h2><p>先保存原件和候选，再核对边坡身份。部分完成的任务也可保存已有结果，不必重新识别。</p><button class="button" id="saveRun">保存 / 重试入库</button><p id="intakeResult" role="status"></p>';
    if(catalog.receipts?.length){const r=catalog.receipts[0];$('intakeResult').textContent=r.message+(r.errors?.length?'；'+r.errors.map(e=>e.document+'：'+e.reason).join('；'):'');}
    $('saveRun').onclick=async()=>{$('saveRun').disabled=true;try{const result=await requestJson('/api/slope-library',{method:'POST',body:{operation:'ingest',route:q.get('route')||'agent',run:q.get('run')||'live'}});$('intakeResult').textContent=result.message+(result.errors?.length?'；'+result.errors.map(e=>e.document+'：'+e.reason).join('；'):'');await refresh();if(catalog.unassigned.length){$('filter').value='unassigned';await select(catalog.unassigned[0].id,true);}}catch(e){$('intakeResult').textContent=e.message;}finally{$('saveRun').disabled=false;}};
  }
  (async()=>{try{await refresh();if(q.get('run')||q.get('route'))intake();if(q.get('slope_id'))await select(q.get('slope_id'));else if(q.get('binding_id')){$('filter').value='unassigned';await select(q.get('binding_id'),true);}else if(catalog.unassigned.length){$('filter').value='unassigned';await select(catalog.unassigned[0].id,true);}else if(catalog.slopes.length)await select(catalog.slopes[0].id);}catch(e){note('资料库读取失败：'+e.message);}})();
})();
