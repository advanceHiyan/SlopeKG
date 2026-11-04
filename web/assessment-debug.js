(function(){
  'use strict';
  const {getJson,requestJson,escapeHtml:e}=window.SlopeKGApi,el=id=>document.getElementById(id);
  const query=new URLSearchParams(location.search),base='/api/assessment-debug/',version='slopekg.standard-assessment/0.3.0-review';
  let context={route:query.get('route')||'knowledge',run_id:query.get('run')||'live',document:query.get('document')||''};
  let workspaceMode=query.get('workspace')==='main'?'main':'debug';
  let data=null,catalog=null,stage=query.get('stage')||'source',offset=Math.max(0,Number(query.get('offset'))||0),busy=false,generation=0,pageGeneration=0,caseGeneration=0,pagination=null;
  if(['25','50','100'].includes(query.get('limit')))el('flowLimit').value=query.get('limit');
  const variableLabels={development:'发育程度',harm:'危害程度',assessment_basis:'灾情/险情',threatened_people:'受威胁人数',potential_loss_wan:'潜在经济损失',direct_loss_wan:'直接经济损失',deaths:'死亡人数',observations:'观察记录',geological_context:'地质环境'};
  const states={ready_for_review:'已有候选等级，待人工复核',needs_evidence:'关键依据尚未齐',needs_resolution:'存在冲突或多个可能结果，等待处理',invalid_request:'当前对象不适用滑坡现状评估',reviewed:'已有人工复核记录'};
  function status(text){el('flowStatus').textContent=text;}
  function error(err){el('flowError').hidden=!err;el('flowError').textContent=err?.message||String(err||'');}
  function buttons(){el('flowPrepare').disabled=busy||!catalog;el('flowJudge').disabled=busy||!data?.case.hazard_type_supported||!data?.selected_result?.is_current;if(pagination){el('flowPrevious').disabled=busy||pagination.previous;el('flowNext').disabled=busy||pagination.next;}}
  async function action(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);error();try{await fn();}catch(err){error(err);status('操作未完成，已保存记录不受影响。');}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);buttons();}}
  const pretty=v=>JSON.stringify(v,null,2);
  function value(v){if(v==null)return '未知';if(v.kind==='enum')return v.label;if(v.kind==='number')return v.value+' '+({person:'人','10k_CNY':'万元','1':''}[v.unit]??v.unit);if(v.kind==='interval')return `${v.lower??'…'}～${v.upper??'…'} ${v.unit==='person'?'人':v.unit}`;return v.text??pretty(v);}
  function recognitionLink(targetStage='facts',page){const q=new URLSearchParams({route:context.route,run:context.run_id,document:context.document,stage:targetStage});if(data)q.set('assessment_case',data.case.case_id);if(page)q.set('page',page);return './recognition-debug.html?'+q;}
  async function loadCatalog(){
    if(!context.document)return;
    catalog=await getJson(base+'catalog?'+new URLSearchParams(context));
    el('sourceName').textContent=catalog.source_name+' · '+({knowledge:'知联',basic:'基读',visual:'视读'}[context.route])+' · 识别快照 '+catalog.recognition_run_hash.slice(0,12);
    el('flowSubject').innerHTML=catalog.subjects.map(s=>`<option value="${e(s.id)}">${e(s.label)}${s.supported?'':'（保留资料，不强套滑坡表）'}</option>`).join('');
    el('flowDate').innerHTML='<option value="">时期尚未明确，保留待核</option>'+catalog.dates.map(d=>`<option value="${e(d)}">${e(d)}</option>`).join('');
    if(catalog.dates.length===1)el('flowDate').value=catalog.dates[0];
    el('flowPrepare').disabled=false;
    el('linkedCases').innerHTML=catalog.linked_cases.map(c=>`<a href="?case=${e(c.case_id)}">${e(c.subject_label)} · ${e(c.scope.time.raw)} · ${e(c.scope.scenario.raw)} · 第${c.resolution_version}版${c.same_recognition_snapshot?'':'（同PDF的另一识别快照）'}</a>`).join('');
    el('recognitionBack').href=recognitionLink('facts');
  }
  function args(){return {contract_version:version,...(workspaceMode==='debug'?{workspace:'debug'}:{}),case_id:data.case.case_id,expected_resolution_version:data.case.resolution_version};}
  async function loadCase(id,resultId){
    const caseToken=++caseGeneration;
    const params=new URLSearchParams({case_id:id,workspace:workspaceMode,...(resultId?{result_id:resultId}:{})});
    const previousSource=JSON.stringify(context),next=await getJson(base+'flow?'+params);if(caseToken!==caseGeneration)return;data=next;const lineage=data.case.recognition_lineage;context=lineage?.source_context||{};
    if(previousSource!==JSON.stringify(context)){catalog=null;pageGeneration++;const image=el('flowPageImage');if(image.dataset.blob)URL.revokeObjectURL(image.dataset.blob);image.hidden=true;el('flowPageInRecognition').hidden=true;el('flowPageStatus').textContent='来源快照已变化，请重新定位本次证据原页。';}
    buttons();
    const r=data.selected_result,grade=r?.table17_result?.grade;
    el('flowWorkspace').hidden=false;el('flowGrade').textContent=grade?grade+'（待复核格值）':r?.table17_result?.possible_grades?.length?'保留多个可能等级，尚未唯一确定':'尚未形成危险性等级';
    el('flowSummary').textContent=`${lineage?.subject_label||data.case.target_ref} · ${r?.assessment_scope?.time?.raw||data.case.scope.time.raw} · ${states[r?.workflow_status]||r?.workflow_status||'尚未计算'} · 所看结果第${r?.resolution_version??'…'}版（案例当前第${data.case.resolution_version}版）`;
    el('flowLineage').textContent=pretty({识别历史:lineage||'本既有案例尚未绑定识别历史，未补造上游记录',评估案例:id,工作区:workspaceMode,所看结果:r?.result_id,规则版本:r?.rule_version,输入快照:r?.input_snapshot_hash});
    el('flowRecognition').hidden=!lineage;if(lineage){el('flowRecognition').href=data.recognition_link;el('recognitionBack').href=data.recognition_link;}
    el('flowStart').hidden=!lineage;el('flowOriginalPdf').closest('.flow-original').hidden=!lineage;
    if(!lineage)el('sourceName').textContent='此既有评估尚未绑定识别历史；已保存的评估来源、规则计算、模型建议与复核版本仍可查看。';
    el('flowRisk').href='./risk.html#case='+encodeURIComponent(id)+(workspaceMode==='debug'?'&workspace=debug':'');
    el('flowExport').href=base+'export?case_id='+encodeURIComponent(id)+'&workspace='+workspaceMode;
    if(lineage)el('flowOriginalPdf').href='/api/recognition-debug/source.pdf?'+new URLSearchParams({route:context.route,run:context.run_id,document:context.document});
    el('flowVersion').innerHTML=data.results.map(v=>`<option value="${e(v.result_id)}" ${v.result_id===r?.result_id?'selected':''}>第${v.resolution_version}版 · ${e(v.table17_result.grade||'未定级')} ${v.is_current?'（当前）':'（历史）'}</option>`).join('');
    if(!data.stages.some(s=>s.id===stage))stage='source';
    el('flowSteps').innerHTML=data.stages.map(s=>`<button data-stage="${e(s.id)}" class="${stage===s.id?'active':''}">${e(s.label)} · ${s.count}</button>`).join('');
    el('recognitionSteps').innerHTML=lineage?[['input','PDF输入及参数'],['pages','逐页覆盖'],['ocr','OCR中间结果'],['calls','实际模型输入输出'],['facts','识别最终候选']].map(([s,t])=>`<a href="${e(recognitionLink(s))}">${e(t)}</a>`).join(''):'<p>尚未绑定，不补造上游过程。</p>';
    await loadRows();if(caseToken!==caseGeneration)return;buttons();status('已读取同一识别快照对应的评估全过程；查看记录不调用模型。');
  }
  function pagesOf(row){const pages=new Set();function walk(v){if(Array.isArray(v))v.forEach(walk);else if(v&&typeof v==='object'){for(const [k,x] of Object.entries(v)){if(['page','file_page_1_based'].includes(k)&&Number.isInteger(x)&&x>0)pages.add(x);else if(typeof x==='object')walk(x);}}}walk(row);return [...pages].sort((a,b)=>a-b);}
  function refsOf(row){const refs=new Set();function walk(v){if(Array.isArray(v))v.forEach(walk);else if(v&&typeof v==='object')for(const [k,x] of Object.entries(v)){if(['input_fact_ids','candidate_fact_ids'].includes(k)&&Array.isArray(x))x.forEach(id=>refs.add(id));else if(k==='fact_id'&&typeof x==='string')refs.add(x);else if(typeof x==='object')walk(x);}}walk(row);return [...refs];}
  function record(row,index){
    const variable=row.typed_variable||row.variable||row.variable_code;
    const title=variableLabels[variable]||row.field||(row.record_type==='assessment_scope'?'评估对象与适用范围':row.record_type)||row.method_id||row.id||'已保存记录';
    const v=row.typed_value??row.value;
    const ps=pagesOf(row),sourceRefs=new Map();for(const fid of refsOf(row)){const f=data.facts.find(f=>f.fact_id===fid);if(f?.recognition_ref)sourceRefs.set(f.recognition_ref.record_id,f.recognition_ref);for(const cid of f?.evidence_refs||[]){for(const span of data.contexts.find(c=>c.context_id===cid)?.spans||[]){if(span.file_page_1_based)ps.push(span.file_page_1_based);}}}
    let links=data.case.recognition_lineage?[...new Set(ps)].map(p=>`<button class="button ghost" data-page="${p}">定位PDF第${p}页</button>`).join(''):'';
    if(data.case.recognition_lineage)for(const [id,ref] of sourceRefs){const retrieved=ref.source_origin==='explicit_source_statement_retrieval';links+=`<a href="${e(recognitionLink(retrieved?'units':'facts'))}&q=${encodeURIComponent(retrieved?id.replace('literal_judgment_',''):id)}">追溯识别来源 ${e(id)}</a>`;}
    let summary='';if(variable&&('typed_value'in row||'value'in row))summary+=`<p><b>${e(variableLabels[variable]||variable)}</b>：${e(value(v))}</p>`;
    const reasons={not_an_assessment_input:'不直接参与本次国标查表，原始记录仍保留',ambiguous_or_unbound_source_object:'对象归属尚未明确，不能直接采用',mixed_object_evidence_pages:'证据涉及多个对象，不能自动混用',no_supported_source_quote:'缺少可定位的支持原句',not_an_explicit_development_label:'没有可直接采用的发育等级原判',missing_or_unknown_currency_unit:'经济损失单位尚未明确',not_a_scalar_or_reported_interval:'尚未形成可采用的数值或明确区间'};
    if(row.accepted===false)summary+=`<p>未采用：${e(reasons[row.reason]||row.reason)}</p>`;
    if(row.accepted===true)summary+=`<p>已形成候选输入；${row.basis_status==='conditional'?'保留适用前提、待核':'原句已对应，仍待复核'}</p>`;
    if(row.reason)summary+=`<p>${e(row.reason)}</p>`;
    if(row.execution_status)summary+=`<p>执行状态：${e(row.execution_status)}</p>`;
    if(row.status)summary+=`<p>状态：${e(({compatible:'符合本次适用范围',incompatible:'不适用于本次范围',unresolved:'适用性待核',selected:'已选用候选',conflicted:'存在分歧',missing:'尚无可采用输入',derived:'规则推得'})[row.status]||row.status)}</p>`;
    if(row.result?.grade||row.result?.possible_grades)summary+=`<p>格值：${e(row.result.grade||'未唯一确定')}；可能结果：${e((row.result.possible_grades||[]).join('、'))}</p>`;
    const retrieved=row.source_origin==='explicit_source_statement_retrieval'||row.record_type==='explicit_source_statement_retrieval';
    return `<article class="flow-record"><h3>${offset+index+1}. ${e(title)}</h3>${summary}<div class="flow-links">${links}${row.id&&data.case.recognition_lineage?`<a href="${e(recognitionLink(retrieved?'units':'facts'))}&q=${encodeURIComponent(retrieved?row.id.replace('literal_judgment_',''):row.id)}">查上游识别记录</a>`:''}</div><details><summary>完整输入、输出、依据与状态</summary><pre>${e(pretty(row))}</pre></details></article>`;
  }
  async function loadRows(){
    const token=++generation;if(!data)return;
    el('flowRecords').textContent='正在读取这个步骤的已保存记录…';
    el('flowStageTitle').textContent=data.stages.find(s=>s.id===stage)?.label||stage;
    el('flowStageHelp').textContent=stage==='judgment'?'仅展示实际保存的系统提示词、证据包、模型回复及用量；未调用、失败与弃答均单独保留。':stage==='mapping'?'已采用、未采用和原文补检分别记录。未采用不等于原文不存在。':'展开每条记录可查看完整数据，规则输入可定位到来源事实与PDF原页。';
    const params=new URLSearchParams({case_id:data.case.case_id,workspace:workspaceMode,stage,offset,limit:el('flowLimit').value,...(data.selected_result?{result_id:data.selected_result.result_id}:{})});
    const result=await getJson(base+'records?'+params);if(token!==generation)return;
    el('flowRecords').innerHTML=result.rows.map(record).join('')||'<p class="notice">此步骤尚无已保存记录；不补造处理过程。</p>';
    el('flowCount').textContent=`共${result.count}条；当前${result.count?offset+1:0}～${Math.min(offset+result.rows.length,result.count)}条`;
    const p=new URLSearchParams({case:data.case.case_id,workspace:workspaceMode,stage,offset,limit:el('flowLimit').value,...(data.selected_result?{result_id:data.selected_result.result_id}:{})});history.replaceState(null,'','?'+p);
    pagination={previous:offset===0,next:offset+result.rows.length>=result.count};buttons();
  }
  async function showPage(number){
    if(!data)return;const token=++pageGeneration;el('flowPageImage').hidden=true;el('flowPageStatus').textContent='正在读取同一快照原页…';
    const params=new URLSearchParams({route:context.route,run:context.run_id,document:context.document,page:number});
    try{const response=await fetch('/api/recognition-debug/page.png?'+params);if(!response.ok)throw Error('原页读取失败（'+response.status+'）');const blob=await response.blob();if(token!==pageGeneration)return;const image=el('flowPageImage'),old=image.dataset.blob;image.src=URL.createObjectURL(blob);image.dataset.blob=image.src;if(old)URL.revokeObjectURL(old);image.hidden=false;el('flowPage').value=number;el('flowOriginalPdf').href=el('flowOriginalPdf').href.split('#')[0]+'#page='+number;el('flowPageInRecognition').hidden=false;el('flowPageInRecognition').href=recognitionLink('pages',number);el('flowPageStatus').textContent='PDF物理第'+number+'页 · 来源历史 '+data.case.recognition_lineage.history_id;image.scrollIntoView({block:'nearest'});}catch(err){if(token===pageGeneration){el('flowPageStatus').textContent=err.message;error(err);}}
  }
  async function syncCaseCatalog(){if(data.case.recognition_lineage){await loadCatalog();el('flowSubject').value=data.case.recognition_lineage.subject_id;el('flowDate').value=data.case.scope.time.start||'';el('flowScenario').value=data.case.scope.scenario.code;}}
  el('flowStart').onsubmit=ev=>{ev.preventDefault();action(async()=>{workspaceMode='debug';status('正在保存识别快照、整理输入并执行国标查表…');const result=await requestJson(base+'prepare',{method:'POST',body:{...context,subject_id:el('flowSubject').value,assessment_date:el('flowDate').value,scenario:el('flowScenario').value,expected_recognition_hash:catalog.recognition_run_hash}});await loadCase(result.case.case_id);await syncCaseCatalog();});};
  el('flowJudge').onclick=()=>action(async()=>{if(!data.selected_result?.is_current)throw Error('请先切换到当前评估结果版本。');if(!el('flowAllowModel').checked)throw Error('请先允许发送相关证据片段；只查看过程无需授权。');status('正在运行单模型发育建议，保留支持、反证和弃答结果…');await requestJson('/api/standard-assessment/judge',{method:'POST',body:{...args(),allow_model:true}});await loadCase(data.case.case_id);await requestJson('/api/standard-assessment/evaluate',{method:'POST',body:args()});await loadCase(data.case.case_id);stage='judgment';offset=0;await loadCase(data.case.case_id);});
  el('flowSteps').onclick=ev=>{const b=ev.target.closest('[data-stage]');if(b){stage=b.dataset.stage;offset=0;loadCase(data.case.case_id,data.selected_result?.result_id).catch(error);}};
  el('flowRecords').onclick=ev=>{const b=ev.target.closest('[data-page]');if(b)showPage(Number(b.dataset.page));};
  el('flowVersion').onchange=()=>{offset=0;loadCase(data.case.case_id,el('flowVersion').value).catch(error);};
  el('flowLimit').onchange=()=>{offset=0;loadRows().catch(error);};
  el('flowPrevious').onclick=()=>{offset=Math.max(0,offset-Number(el('flowLimit').value));loadRows().catch(error);};el('flowNext').onclick=()=>{offset+=Number(el('flowLimit').value);loadRows().catch(error);};
  el('flowShowPage').onclick=()=>showPage(Number(el('flowPage').value));
  (async()=>{try{if(query.get('case')){await loadCase(query.get('case'),query.get('result_id'));await syncCaseCatalog();}else if(context.document){await loadCatalog();status('来源已核对。选择已有对象和适用范围后，将自动整理输入并查表。');}}catch(err){error(err);status('读取失败，请检查识别快照是否完整。');}})();
})();
