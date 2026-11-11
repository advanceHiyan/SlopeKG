(function () {
  'use strict';
  const debug=window.SlopeKGDebug,{requestJson:getJson,escapeHtml:esc}=window.SlopeKGApi;
  const el=id=>document.getElementById(id), panel=debug.panel;
  let catalog=null,trace=null,request=0,context='',initialClaim=new URLSearchParams(location.search).get('claim');
  const labels={pending:'待核',source_aligned:'原文对齐 · 未独立复核',model_supported:'模型支持 · 未独立复核',rejected:'拒绝'};
  const dialog=document.createElement('dialog');dialog.id='documentTrace';dialog.setAttribute('aria-labelledby','documentTraceTitle');
  dialog.innerHTML='<div class="dialog-heading"><div><p class="eyebrow">DOCUMENT · EVIDENCE TRACE</p><h2 id="documentTraceTitle">沿一条候选追查</h2></div><button id="closeDocumentTrace" class="button ghost">关闭追查</button></div><p id="documentTraceMeta" class="muted"></p><div class="debug-actions"><button id="downloadDocumentTrace" class="button ghost" disabled>下载完整追查JSON</button><button id="copyDocumentTraceLink" class="button ghost" disabled>复制这条候选链接</button></div><div id="documentTraceBody" aria-live="polite"></div>';
  document.body.append(dialog);
  const query=(extra={})=>new URLSearchParams({route:'document',run:el('run').value||'live',document:el('document').value,...extra});
  function overview(){
    const host=el('documentOverview');host.hidden=el('route').value!=='document';if(host.hidden)return;
    const d=catalog?.documents.find(d=>d.id===el('document').value),s=catalog?.summary||{},selected=d?.selection?.selected_pages;
    const scope=selected?.length?`本次只处理物理页 ${selected.join('、')}（原件共${d.pages}页）`:`${d?'本次输入范围：'+(d.selection?.scope||'以输入记录为准')+'；原件'+d.pages+'页':'当前生产目录暂无整读结果'}`;
    host.innerHTML=`<div class="debug-title"><h2>第四路 · 从来源到候选</h2><span class="count-pill">${esc(catalog?.schema_version||'尚无运行记录')}</span></div><p>${esc(scope)}。${d?d.workflow_complete===true?'已保存记录标记流程完成。':d.workflow_complete===false?'流程尚未完成。':'历史未保存流程完成标记。':''}</p><div class="document-metrics">${[['恢复块',s.blocks],['重合分区',s.groups],['最终候选',s.facts],['待核',s.pending],['本次新调用',s.calls],['本次缓存命中',s.cache_hits]].map(([n,v])=>`<div><strong>${v??'—'}</strong><span>${n}</span></div>`).join('')}</div><p class="muted">上方数量为本次运行汇总。待核、原文对齐及模型支持均不等于专业确认；页码筛选只影响浏览。追查时可看到原始解析行、内容恢复、分组输入、原始提取、复核修正和最终记录。</p><div class="debug-actions"><a class="button secondary" href="?route=document&run=open-document-survey&stage=facts">打开已保存的开放报告 · 22–27页</a><a class="button ghost" href="./document-recognition.html">整读任务入口</a></div>`;
  }
  const stageLabels={document_reconstruct:'内容恢复',document_repair_structure:'结构修复',document_zone:'分区规划',document_zone_review:'分区审核',document_extract:'提取',document_reconcile:'原页复核',document_cross_audit:'跨组审计'};
  const association={exact_claim_id:'候选ID直接关联',page_context:'来源页面关联',document_plan:'全文分区上下文',group_context_only:'同组上下文 · 未证实产生此候选'};
  function callCard(c){
    const coverage=c.group_coverage||[];
    return `<article class="trace-call"><div class="record-head"><strong>${esc(stageLabels[c.stage]||c.stage)}</strong><span class="count-pill">${esc(association[c.association])}</span></div><p class="muted">${esc(c.id)} · ${c.cache_hit?'本次使用缓存':'本次未标记缓存命中'} · 输入${c.input_saved?'已保存':'未保存'} · 原始交互${c.raw_exchange_saved?'已保存':'未保存'}</p><button class="button secondary" data-trace-call="${esc(c.id)}">完整输入输出 · 提示词 / 请求 / 回复</button>${coverage.length?`<p>该次请求实际带入 ${c.request_block_count} 个内容块。</p><div class="trace-table-wrap"><table><thead><tr><th>最终分组</th><th>组是否在请求中</th><th>带入 / 该组来源块</th><th>未带入块数</th></tr></thead><tbody>${coverage.map(g=>`<tr><td>${esc(g.name)}<small>${esc(g.group_id)}</small></td><td>${g.group_in_request?'是':'否'}</td><td>${g.included_block_ids.length} / ${g.included_block_ids.length+g.absent_block_ids.length}</td><td>${g.absent_block_ids.length}</td></tr>`).join('')}</tbody></table></div>${panel('带入/缺席的全部块ID与分组上下文',{group_coverage:coverage,request_groups:c.request_groups,request_block_ids:c.request_block_ids})}`:''}${c.input_claims.length?panel('该次实际输入的候选或审计索引',c.input_claims,true):''}${c.raw_claims.length?panel(c.raw_detail_origin==='provider_response_content'?'供应商原始提取 / 新增陈述':'历史缓存中的提取对象 · 可能已被校验改写',c.raw_claims,true):''}${c.validated_claims.length?panel('校验后陈述 · 进入后续处理前',c.validated_claims):''}${c.raw_reviews.length?panel(c.raw_detail_origin==='provider_response_content'?'供应商原始复核意见':'历史缓存中的复核对象 · 可能已被校验改写',c.raw_reviews,true):''}${c.validated_reviews.length?panel('校验后复核与修正',c.validated_reviews):''}${c.audit_flags.length?panel('该候选的跨组审计标记',c.audit_flags,true):''}${!c.context_available?'<p class="notice">历史输入上下文缺失或无法解析；请查看完整交互记录。</p>':''}</article>`;
  }
  const callList=(data,stages)=>data.calls.filter(c=>stages.includes(c.stage)).map(callCard).join('')||'<p class="empty-state">未找到此步骤可关联的已保存调用。不能由后续结果补造。</p>';
  function sourceButton(e,label){return e.page?`<button class="button ghost" data-trace-source="${e.page}" data-box="${esc(JSON.stringify(e.bbox||null))}">${esc(label||'查看原页 '+e.page)}</button>`:'';}
  function renderTrace(data){
    const f=data.final, first=f.evidence?.find(e=>e.page)||data.blocks[0], firstPage=first?.page;
    const tabs=[['source','1 原始PDF与解析'],['structure','2 内容恢复与分区'],['extract','3 提取'],['review','4 原页复核'],['audit','5 跨组审计'],['final','6 最终结果']];
    const source=firstPage?`<div class="trace-source-grid"><div><p>原件物理第${firstPage}页 · 点击放大定位</p><button class="trace-image" data-trace-source="${firstPage}" data-box="${esc(JSON.stringify(first.bbox||null))}" aria-label="放大原始PDF第${firstPage}页"><img src="/api/recognition-debug/page.png?${query({page:firstPage})}" alt="原始PDF物理第${firstPage}页"></button></div><div><h3>数值与归属证据</h3>${['evidence','parent_evidence'].map(k=>`<h4>${k==='evidence'?'数值 / 内容来源':'父对象 / 归属来源'}</h4>${(f[k]||[]).map(e=>`<blockquote>${esc(e.quote)}<small>${esc(e.block_id)} · 物理页${e.page||'未保存'}</small>${sourceButton(e)}</blockquote>`).join('')||'<p>未保存</p>'}`).join('')}<p class="muted">下面只列此候选关联内容块引用的原始解析行；整页全部解析行可在左侧“原始解析行”步骤查看。</p>${data.source_pages.map(p=>sourceButton(p)+panel('第'+p.page+'页 · 关联原始解析行',p.native_sources)).join('')}</div></div>`:'<p class="notice">此候选没有可定位的原页证据。保留为来源缺口。</p>';
    const structure=`<h3>实际引用的恢复块</h3>${data.blocks.map(b=>`<article class="trace-block"><strong>${esc(b.id)} · ${esc(b.kind)} · 第${b.page}页</strong><pre class="record-preview">${esc(b.text)}</pre>${sourceButton(b)}${panel('恢复块全部字段',b)}</article>`).join('')||'<p>没有可关联内容块。</p>'}${panel('最终分组 · 正文/上下文/重合范围',data.groups,true)}${panel('相关跨页与标题链接',data.links)}${callList(data,['document_reconstruct','document_repair_structure','document_zone','document_zone_review'])}`;
    const contents={source,structure,extract:callList(data,['document_extract']),review:callList(data,['document_reconcile']),audit:`<p class="notice info">此步骤使用精简候选索引；请检查模型实际收到了什么，不把索引审计当作全文核对。</p>${callList(data,['document_cross_audit'])}${panel('关联未处理项',data.repair_queue,true)}`,final:`<div class="notice info"><strong>${esc(labels[f.status]||f.status)}</strong><p>${esc(f.reason||'未保存原因')}</p><p>质量问题：${esc((f.quality_issues||[]).join('、')||'此记录未标记质量问题')}</p></div>${panel('最终候选 · 原样完整记录',f,true)}${panel('合并前已保存陈述 · 可能已应用复核修正',data.claims)}${panel('去重关联ID',f.duplicate_claim_ids||[])}`};
    el('documentTraceMeta').textContent=`${f.subject||f.object||''} · ${f.field||''} = ${f.value??''} ${f.unit||''} · ${labels[f.status]||f.status} · ${data.claim_id}`;
    el('documentTraceBody').innerHTML=`<nav class="trace-tabs" aria-label="候选追查步骤">${tabs.map(([id,label],i)=>`<button class="button ${i?'ghost':'secondary'}" data-trace-tab="${id}" aria-pressed="${!i}">${label}</button>`).join('')}</nav><p class="muted">${data.notes.map(esc).join(' ')}</p>${tabs.map(([id,label],i)=>`<section data-trace-panel="${id}" ${i?'hidden':''}><h3>${label}</h3>${contents[id]}</section>`).join('')}`;
    el('documentTraceBody').querySelector('.trace-image img')?.addEventListener('error',e=>{e.target.replaceWith(Object.assign(document.createElement('p'),{textContent:'原页加载失败，可点击“查看原页”重试并查看原因。'}));});
  }
  async function showTrace(id){
    if(!debug.context().document)return;
    const n=++request;trace=null;context=JSON.stringify(debug.context());el('documentTraceMeta').textContent=id;
    el('documentTraceBody').textContent='正在读取此候选的完整证据链…';el('downloadDocumentTrace').disabled=el('copyDocumentTraceLink').disabled=true;
    if(!dialog.open)dialog.showModal();
    try{const data=await getJson('/api/recognition-debug/document-trace?'+query({claim:id}));if(n!==request||!dialog.open||context!==JSON.stringify(debug.context()))return;trace=data;renderTrace(data);el('downloadDocumentTrace').disabled=el('copyDocumentTraceLink').disabled=false;}
    catch(e){if(n===request)el('documentTraceBody').textContent='追查读取失败：'+e.message;}
  }
  window.addEventListener('debugcatalogloaded',e=>{catalog=e.detail;overview();});
  window.addEventListener('debugcontextchange',()=>{request++;dialog.close();overview();});
  window.addEventListener('debugrecordsloaded',e=>{
    overview();if(el('route').value!=='document')return;
    if(['facts','claims'].includes(e.detail.stage))e.detail.rows.forEach((r,i)=>{
      const actions=el('records').querySelector(`[data-record-index="${i}"] .record-actions`);
      if(actions&&r.id)actions.insertAdjacentHTML('afterbegin',`<button class="button" data-document-trace="${esc(r.id)}">沿这条候选追查</button>`);
    });
    if(initialClaim){const id=initialClaim;initialClaim=null;showTrace(id);}
  });
  el('records').addEventListener('click',e=>{const b=e.target.closest('[data-document-trace]');if(b)showTrace(b.dataset.documentTrace);});
  el('closeDocumentTrace').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{request++;});
  el('downloadDocumentTrace').onclick=()=>trace&&debug.download('document-trace-'+trace.claim_id+'.json',trace);
  el('copyDocumentTraceLink').onclick=async()=>{if(!trace)return;const url=new URL(location.href);url.search=new URLSearchParams({route:'document',run:el('run').value,document:trace.document,stage:'facts',q:trace.claim_id,claim:trace.claim_id});try{await navigator.clipboard.writeText(url.href);debug.toast('已复制此候选的独立追查链接');}catch(_){debug.toast('复制未获允许，请使用地址栏中的候选筛选链接');}};
  dialog.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.dataset.traceTab){for(const p of dialog.querySelectorAll('[data-trace-panel]'))p.hidden=p.dataset.tracePanel!==b.dataset.traceTab;for(const t of dialog.querySelectorAll('[data-trace-tab]')){const active=t===b;t.setAttribute('aria-pressed',String(active));t.className='button '+(active?'secondary':'ghost');}}
    if(b.dataset.traceCall)debug.showCall(b.dataset.traceCall);
    if(b.dataset.traceSource)debug.showPage(Number(b.dataset.traceSource),JSON.parse(b.dataset.box||'null'),{mode:'manual'});
  });
})();
