(function(){
  const api=window.SlopeKGApi,e=api.escapeHtml,base='/api/standard-assessment/';
  const version='slopekg.standard-assessment/0.3.0-review';
  let current=null,busy=false,workspaceMode='main';
  const status=document.querySelector('#assessmentStatus'),workspace=document.querySelector('#assessmentWorkspace');
  const labels={development:'发育程度',harm:'危害程度',assessment_basis:'灾情/险情',deaths:'死亡人数',threatened_people:'受威胁人数',direct_loss_wan:'直接经济损失（万元）',potential_loss_wan:'可能直接经济损失（万元）',observations:'观察记录',geological_context:'地质环境',fs:'稳定系数 Fs',fst:'安全系数 Fst',reference:'原报告参考结论',body_mean_angle:'滑体平均坡度',hazard_type:'灾害类型'};
  const origin={basic:'基读原文记录',knowledge:'知联原文记录',visual:'视读原文记录',manual:'人工提供',manual_transcription:'原件核对转录',llm:'模型建议',rule:'规则推导'};
  const unit=u=>({person:'人','10k_CNY':'万元',deg:'°','1':''})[u]??u;
  const val=v=>v==null?'未知':v.kind==='enum'?v.label:v.kind==='number'?`${v.value} ${unit(v.unit)}`:v.kind==='interval'?`${v.lower??'…'}—${v.upper??'…'} ${unit(v.unit)}`:v.text??JSON.stringify(v);
  const args=()=>({contract_version:version,case_id:current.case.case_id,expected_resolution_version:current.case.resolution_version,...(workspaceMode==='debug'?{workspace:'debug'}:{})});
  async function call(op,payload){return api.requestJson(base+op,{method:'POST',body:payload});}
  async function action(fn){if(busy)return;busy=true;lock(true);status.textContent='正在处理，模型判断可能需要约一分钟…';try{await fn();status.textContent='已保存本次结果。请查看依据与需要复核的判断。';}catch(err){status.textContent=`操作未完成：${err.message}`;}finally{busy=false;lock(false);}}
  function lock(v){document.querySelectorAll('#assessmentStart button,#assessmentWorkspace button').forEach(b=>b.disabled=v||b.dataset.unavailable==='true');}
  async function refresh(){current=await api.requestJson(base+'case?case_id='+encodeURIComponent(current.case.case_id)+(workspaceMode==='debug'?'&workspace=debug':''));render();}
  function latest(){return current.results.filter(r=>r.is_current).at(-1);}
  async function evaluate(){await call('evaluate',args());await refresh();}
  async function review(decision,target_id,extra={}){
    const who=document.querySelector('#assessmentReviewer')?.value.trim(),reason=document.querySelector('#assessmentReason')?.value.trim();
    if(!who||!reason)throw new Error('先填写实际复核人和复核依据，再执行确认或来源选择。');
    await call('review',{...args(),decision,target_id,actor_kind:'human',reviewer_ref:who,reason,...extra});await refresh();await evaluate();
  }
  function render(){
    workspace.hidden=false;const r=latest();if(!r){workspace.innerHTML='<div class="card">尚未计算，请重新开始评估。</div>';return;}
    const allFacts=Array.from(new Map([...current.facts,...(current.derived_facts||[])].map(f=>[f.fact_id,f])).values());
    const facts=allFacts.filter(f=>!allFacts.some(n=>n.supersedes_fact_id===f.fact_id));
    const priority=['development','harm','assessment_basis','threatened_people','potential_loss_wan','deaths','direct_loss_wan','observations','body_mean_angle','fs','fst','reference','hazard_type','geological_context'];
    const samePeriod=f=>JSON.stringify(f.scope.time)===JSON.stringify(current.case.scope.time);
    const rank=f=>(f.source_check.content==='unsupported'?100:0)+(samePeriod(f)?0:50)+priority.indexOf(f.variable_code);
    facts.sort((a,b)=>rank(a)-rank(b));
    const contexts=Object.fromEntries(current.contexts.map(c=>[c.context_id,c]));
    const chosen=Object.fromEntries(Object.entries(r.resolved_variables).map(([k,v])=>[k,v.selected_fact_id]));
    const fact=id=>facts.find(f=>f.fact_id===id),resolved=k=>fact(chosen[k]);
    const trace=current.derivations??[];
    const model=r.partials.professional_context_task;
    const selectedDev=resolved('development');
    const grade=r.table17_result.grade;
    const basis=r.basis_status==='supported'?'输入已有支持':r.basis_status==='conditional'?'基于候选判断的条件结果':'关键依据未齐';
    const time=current.case.scope.time.raw;
    const targetLabel=current.case.recognition_lineage?.subject_label||Array.from(document.querySelector('#assessmentTarget').options).find(o=>o.value===current.case.target_ref)?.textContent||current.case.target_ref;
    const sourceFlow=current.case.recognition_lineage?`<article class="card" style="margin-bottom:18px"><h2>来自同一次识别快照</h2><p>${e(current.case.recognition_lineage.source_name)} · ${e(current.case.recognition_lineage.history_id)} · 独立调试评估工作区</p><a class="button secondary" href="./assessment-debug.html?case=${encodeURIComponent(current.case.case_id)}">查看PDF、识别、规则与复核全过程</a></article>`:`<article class="card" style="margin-bottom:18px"><h2>评估全过程</h2><p>此既有案例尚未绑定识别历史；来源事实、规则推导、模型建议与复核版本均可查看。</p><a class="button secondary" href="./assessment-debug.html?case=${encodeURIComponent(current.case.case_id)}&workspace=${workspaceMode}">查看本案例全部评估中间结果</a></article>`;
    workspace.innerHTML=sourceFlow+`<article class="card"><div class="assessment-result-heading"><div><h2>${e(targetLabel)}</h2><p class="assessment-muted">${e(time)} · 第 ${current.case.resolution_version} 版</p><div class="assessment-grade">${e(grade||'暂未形成唯一危险性等级')}</div><p>${e(basis)} · ${r.review_status==='accepted'?'已人工复核':'待人工复核'}</p></div><button class="button secondary" id="assessmentDownload">下载评估记录</button></div>
      <div class="assessment-flow"><div class="assessment-step">发育程度：${e(selectedDev?val(selectedDev.value):'待判断')}</div><div class="assessment-step">危害程度：${e(resolved('harm')?val(resolved('harm').value):r.partials.table15?.harm||'待确定')}</div><div class="assessment-step">表17：${e(grade||r.table17_result.possible_grades.join(' / ')||'条件不足')}</div></div>
      ${r.minimal_missing.length?`<div class="assessment-warning">${r.minimal_missing.map(m=>`<p><strong>${e(labels[m.variable_or_feature]||m.variable_or_feature==='triggers_and_geological_context_review'&&'环境与诱因复核'||m.variable_or_feature)}</strong>：${e(m.why_this_resolves_target)}</p>`).join('')}</div>`:''}
      ${r.conflicts.length?`<div class="assessment-warning"><strong>保留的冲突或来源分歧</strong>${r.conflicts.map(c=>`<p>${e(labels[c.variable]||c.kind)}：${c.blocks.length?'影响定级，需核对来源':'仅供复核，不直接改变等级'}</p>`).join('')}</div>`:''}
      <p class="assessment-muted">本结果对应上述资料时期。模型建议与实际人工确认分别记录；本页面用于国标现状评估复核。</p></article>
      <div class="assessment-columns"><div><article class="card"><h2>判定依据</h2>
        <details open><summary>国标规则与中间结果</summary><p>表4/5：对照变形、地质和稳定性证据形成发育判断；表15：一项有效危害指标可定级；表17：组合查表。不同来源或指标发生冲突时保留分歧。</p>${r.partials.stability_axes.map(a=>`<p>系数列：${e(a.expression||a.reason)} → ${e(a.axis||'未定')}（${e(a.scope?.scenario?.raw||'')}）</p>`).join('')}
        ${r.partials.table15?`<p>表15：${e(r.partials.table15.harm||r.partials.table15.possible_grades.join(' / '))}，采用 ${r.partials.table15.coverage.provided_indicators} 项指标。</p>`:''}</details>
        <details><summary>地质环境与诱发因素（表16）</summary><p>${e(r.environment_and_triggers?.interpretation||'需结合环境和诱因进行人工综合复核。')}</p>${(r.environment_and_triggers?.matches||[]).map(x=>`<p>${e(x.category)}：${e(x.term)}，见已绑定原文。</p>`).join('')||'<p>尚无可明确归类的诱因依据。</p>'}</details>
        <h3>模型辅助判断</h3><p>${e(({proposed:'已提出候选，需复核',abstained:'证据不足，模型弃答',failed:'调用或输出校验失败',not_run:'尚未调用模型'})[model.execution_status]||model.execution_status)}</p>
        ${(r.review_attention||[]).map(n=>`<div class="assessment-warning"><strong>原文核对提示</strong><p>${e(n.reason)}</p><p class="assessment-muted">${e(n.reviewer_ref)}；此记录未作专业等级签认。</p></div>`).join('')}
        ${model.detail?`<p>${e(model.detail.rationale)}</p>${['support','counterevidence'].map(k=>`<strong>${k==='support'?'支持依据':'反证和限制'}</strong>${model.detail[k].map(x=>`<blockquote>${e(x.quote)}<p>${e(x.reason)}</p></blockquote>`).join('')}`).join('')}<p>${e(model.detail.missing.join('；'))}</p>`:''}
        ${workspaceMode==='debug'?'<label><input type="checkbox" id="assessmentAllowModel"/>允许将本案例证据片段发送到配置的模型服务</label>':''}<button class="button secondary" id="assessmentJudge">${model.execution_status==='not_run'?'用已有证据辅助判断':'查看已有判断 / 重试未完成判断'}</button>
        <h3>原文与来源选择</h3><div class="assessment-fact-list">${facts.map(f=>`<div class="assessment-evidence"><strong>${e(labels[f.variable_code]||f.feature_code||f.variable_code)}</strong>：${e(val(f.value))}<p class="assessment-muted">${e(origin[f.origin_kind]||f.origin_kind)} · ${e(f.scope.time.raw)} · ${e(f.scope.scenario.raw)} · ${f.review_status==='accepted'?'已人工确认':'待复核'}</p>${!samePeriod(f)?'<p class="assessment-muted">历史或其他时期参考，未用于本次定级。</p>':''}${['unchecked','unsupported'].includes(f.source_check.content)?'<p class="assessment-warning">此记录的引用尚未支持原值，保留供纠正，不作已核实依据。</p>':''}${f.evidence_refs.map(id=>contexts[id]?.spans.map(s=>s.quote.length>500?`<details><summary>查看第${e(s.file_page_1_based||'—')}页完整原句 / 表格</summary><p>${e(s.quote)}</p></details>`:`<p>第${e(s.file_page_1_based||'—')}页：${e(s.quote)}</p>`).join('')||'').join('')}
        ${samePeriod(f)&&f.origin_kind!=='rule'&&['development','harm','assessment_basis','deaths','threatened_people','direct_loss_wan','potential_loss_wan'].includes(f.variable_code)?`<div class="assessment-actions"><button data-select="${e(f.fact_id)}" class="button secondary">${chosen[f.variable_code]===f.fact_id?'采用中；核定此来源':'选择此来源'}</button><button data-clear="${e(f.fact_id)}" class="button secondary">撤销来源选择</button>${f.origin_kind==='llm'&&f.review_status!=='accepted'?`<button data-accept="${e(f.fact_id)}" class="button secondary">确认此判断</button>`:''}</div>`:''}</div>`).join('')}</div></article>
        <article class="card" style="margin-top:18px"><h2>已检索资料</h2><p>本案例关联原件的可读文字 · ${current.case.retrieval?.hit_count??0}处命中</p><p class="assessment-muted">命中只是待核对的原文片段，未直接当成当前边坡的人数或等级。</p><details><summary>查看原文命中</summary>${(current.case.retrieval?.hits||[]).slice(0,40).map(h=>`<div class="assessment-hit">${e(h.source_path)} · 第${h.page}页<br>${e(h.quote)}</div>`).join('')}</details></article></div>
      <div><article class="card"><h2>复核与补充</h2><p class="section-subtitle">先核对已有证据。只补充本次真正缺少的观察、人数、损失或专业判断。</p>
      <label>实际复核人<input id="assessmentReviewer" autocomplete="name" placeholder="填写实际操作者"/></label><label>复核依据或理由<textarea id="assessmentReason" placeholder="说明来源、对象和判断依据"></textarea></label>
      <details style="margin-top:18px"><summary>补充一项有依据的资料</summary><form id="assessmentFact"><label>资料类型<select id="assessmentVariable">${['observations','development','harm','assessment_basis','threatened_people','potential_loss_wan','deaths','direct_loss_wan'].map(k=>`<option value="${k}">${labels[k]}</option>`).join('')}</select></label><label>数值或结论<input id="assessmentValue" required placeholder="如：受威胁人数，或中等发育"/></label><label>判断所依据的标准<input id="assessmentStandard" value="GB/T 40112-2021"/></label><button class="button secondary" type="submit">保存资料并重算</button></form></details>
      <div class="assessment-review"><label><input type="checkbox" id="assessmentContextReviewed"/>已核对对象、时期、环境及诱因，确认本次结果的适用范围</label><button class="button" id="assessmentConfirm" data-unavailable="${!grade}" ${!grade?'disabled':''}>确认本次评估结果</button></div><p class="assessment-muted">资料变化后需重新复核。历史结果和本次操作都会保留。</p></article>
      <article class="card" style="margin-top:18px"><h2>推导记录</h2><p>已保存 ${r.trace_derivation_ids.length} 个规则推导步骤。复核状态：${r.review_status==='accepted'?'已确认':'待复核'}。</p><details><summary>查看计算与版本记录</summary><pre class="assessment-json">${e(JSON.stringify({case_id:current.case.case_id,scope:current.case.scope,rule_version:r.rule_version,derivations:trace,review_decisions:current.reviews},null,2))}</pre></details></article></div></div>`;
    document.querySelector('#assessmentJudge').onclick=()=>action(async()=>{if(workspaceMode==='debug'&&!document.querySelector('#assessmentAllowModel').checked)throw new Error('请先允许发送相关证据片段。');await call('judge',{...args(),...(workspaceMode==='debug'?{allow_model:true}:{})});await refresh();await evaluate();});
    workspace.querySelectorAll('[data-select]').forEach(b=>b.onclick=()=>action(()=>review('select_branch',b.dataset.select)));
    workspace.querySelectorAll('[data-clear]').forEach(b=>b.onclick=()=>action(()=>review('clear_selection',b.dataset.clear)));
    workspace.querySelectorAll('[data-accept]').forEach(b=>b.onclick=()=>action(()=>review('accept_fact',b.dataset.accept)));
    document.querySelector('#assessmentConfirm').onclick=()=>action(()=>review('confirm_result',r.result_id,{context_reviewed:document.querySelector('#assessmentContextReviewed').checked}));
    document.querySelector('#assessmentFact').onsubmit=ev=>{ev.preventDefault();action(async()=>{const who=document.querySelector('#assessmentReviewer').value.trim(),reason=document.querySelector('#assessmentReason').value.trim();if(!who||!reason)throw new Error('请填写实际操作者和资料依据。');const variable=document.querySelector('#assessmentVariable').value,raw=document.querySelector('#assessmentValue').value.trim();let value;
      const vocab={development:['gb-landslide-development@2021',['强发育','中等发育','弱发育']],harm:['gb-harm@2021',['危害大','危害中等','危害小']],assessment_basis:['assessment-basis@2021',['灾情','险情']]};
      if(vocab[variable]){if(!vocab[variable][1].includes(raw))throw new Error('请填写：'+vocab[variable][1].join('、'));value={kind:'enum',code:raw,label:raw,vocabulary:vocab[variable][0]};}
      else if(variable==='observations')value={kind:'text',text:raw};else{if(!raw||!Number.isFinite(Number(raw)))throw new Error('请输入有效数值。');value={kind:'number',value:Number(raw),unit:variable.includes('loss')?'10k_CNY':'person',qualifier:'exact_as_reported'};}
      await call('assertion',{...args(),variable,value,actor_kind:'human',actor_ref:who,reason,source_standard:document.querySelector('#assessmentStandard').value.trim()});await refresh();await evaluate();});};
    document.querySelector('#assessmentDownload').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));a.download=current.case.target_ref+'_评估记录.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
    lock(busy);
  }
  document.querySelector('#assessmentStart').onsubmit=ev=>{ev.preventDefault();action(async()=>{workspaceMode='main';const target=document.querySelector('#assessmentTarget').value,period=document.querySelector('#assessmentPeriod').value;const result=await call('run',{contract_version:version,target_ref:target,task:'landslide_current',...(period==='event2016'?{time_selector:{kind:'event_period',raw:'2016年雨季'}}:{})});current={case:result.case};history.replaceState(null,'','#case='+result.case.case_id);await refresh();});};
  async function loadHash(){const p=new URLSearchParams(location.hash.slice(1)),id=p.get('case');workspaceMode=p.get('workspace')==='debug'?'debug':'main';if(id){current={case:{case_id:id}};await refresh();document.querySelector('#assessmentTarget').value=current.case.target_ref;document.querySelector('#assessmentPeriod').value=current.case.scope.time.kind==='event_period'?'event2016':'report';}}
  window.addEventListener('hashchange',()=>{if(!busy)loadHash().catch(err=>{status.textContent='读取案例失败：'+err.message;});});
  (async()=>{try{const data=await api.requestJson(base+'targets');const target=document.querySelector('#assessmentTarget');target.innerHTML=data.targets.map(t=>`<option value="${e(t.id)}" ${t.supported?'':'disabled'}>${e(t.label)}${t.supported?'':'（当前流程仅支持滑坡）'}</option>`).join('');await loadHash();}catch(err){status.textContent='读取评估资料失败：'+err.message;}})();
})();
