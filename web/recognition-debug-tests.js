(function(){
  'use strict';
  const {getJson,requestJson,escapeHtml:esc}=window.SlopeKGApi, debug=window.SlopeKGDebug;
  const el=id=>document.getElementById(id), api='/api/recognition-debug/';
  let datasets=[],selected=new Set(),historyOffset=0,historyCount=0,detail=null,testBusy=false,pollTimer,historyGeneration=0,detailGeneration=0;
  let testProfiles=[];
  const states={queued:'排队中',running:'执行中',completed:'检查完成',failed:'失败',interrupted:'已中断',partial:'部分完成',prepared:'仅准备'};
  const ruleStates={matched:'显式规则匹配',unmatched:'显式规则未匹配',needs_review:'待人工复核',execution_unavailable:'识别未执行或不可用'};
  const reviewStates={note:'备注',pass:'本地判为通过',fail:'本地判为不通过',pending:'待复核',not_applicable:'不适用'};
  const selectedDataset=()=>datasets.find(d=>d.id===el('dataset').value);
  const testSource=()=>el('testSource').value==='current'?debug.context():selectedDataset()?.source;
  function showView(name){
    for(const view of ['process','tests','history'])el(view+'View').hidden=view!==name;
    for(const b of document.querySelectorAll('[data-view]'))b.classList.toggle('active',b.dataset.view===name);
    if(name==='tests'&&!datasets.length)loadDatasets();
    if(name==='history')loadHistory();
    debug.rememberContext();
  }
  function controls(){
    const fresh=el('testMode').value==='recognize',source=testSource(),basic=source?.route==='basic';el('testModelControls').hidden=!fresh||basic;
    el('testOcrLabel').hidden=source?.route!=='knowledge';
    el('testSourceNote').textContent=source?`本次检查方法：${debug.routeLabel(source.route)} · 来源：${el('testSource').value==='current'?'当前正在查看的记录':'数据集绑定的历史预测'}。${fresh&&basic?'基读暂不支持独立重跑测试；请先在识别过程运行基读，再检查保存的预测。':'切换顶部方法后，选择“当前正在查看的记录”才会改用该方法进行检查。'}`:'请选择数据集或当前资料。';
    el('runTest').disabled=testBusy||!selected.size||!source?.document||(fresh&&(!el('testAllowExternal').checked||basic));
    el('runTest').textContent=fresh?'重新识别所选测试点并保存':'检查所选测试点并保存';
    el('testSelectionCount').textContent=`已选${selected.size} / ${selectedDataset()?.points.length||0}点`;
    el('saveSnapshot').disabled=!debug.context().document;
  }
  function sourceChanged(){
    el('testAllowExternal').checked=false;const preferred=testSource()?.route==='visual'?'deepseek':'deepseek-knowledge';
    if([...el('testModel').options].some(p=>p.value===preferred))el('testModel').value=preferred;
    testEndpoint();controls();
  }
  function testEndpoint(){const profile=testProfiles.find(p=>p.id===el('testModel').value);el('testEndpoint').textContent=profile?`${profile.model} · ${profile.endpoint}`:'暂无可用模型接口';}
  function renderPoints(){
    const d=selectedDataset(),q=el('pointSearch').value.toLowerCase();
    el('datasetNote').textContent=d?`数据集原预测方法：${debug.routeLabel(d.source.route)} · ${d.source.source_name} · 本次输入${d.source.input_pages||'?'}页 · ${d.points.length}个检查点。${d.note}`:'当前没有开放数据集，可以把正在查看的资料另存为数据集。';
    const rows=(d?.points||[]).filter(p=>!q||JSON.stringify(p).toLowerCase().includes(q));
    el('testPoints').innerHTML=rows.map(p=>`<label class="test-point"><input class="point-check" type="checkbox" value="${esc(p.id)}" ${selected.has(p.id)?'checked':''}><span><strong>${esc(p.label)}</strong><small>${esc(p.id)} · 原件物理页 ${(p.pages||[]).join('、')||'未指定'} · ${p.checks?.length?`${p.checks.length}条显式条件`:'人工复核点'}</small>${p.expected&&Object.keys(p.expected).length?`<span class="point-expected">预期：${esc(JSON.stringify(p.expected))}</span>`:''}<span class="point-description">${esc(p.description||'')}</span></span></label>`).join('')||'<p class="muted">没有匹配测试点。</p>';controls();
  }
  async function loadDatasets(prefer){
    try{
      const data=await getJson(api+'datasets'),old=prefer||el('dataset').value;datasets=data.datasets||[];
      el('dataset').innerHTML=datasets.map(d=>`<option value="${esc(d.id)}">${esc(d.name)} · ${d.points.length}点</option>`).join('');
      if(datasets.some(d=>d.id===old))el('dataset').value=old;
      selected=new Set((selectedDataset()?.points||[]).map(p=>p.id));renderPoints();
      let profiles=debug.profiles();if(!profiles.length)profiles=(await getJson('/api/recognition/profiles')).profiles||[];
      testProfiles=profiles;
      el('testModel').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');
      sourceChanged();
    }catch(e){el('testPoints').textContent='数据集读取失败';debug.error(debug.describeError(e));}
  }
  function integer(id,min,max){const value=Number(el(id).value);if(!Number.isInteger(value)||value<min||value>max)throw Error('调用上限、并发或OCR补页数无效');return value;}
  async function runTest(){
    if(testBusy)return;
    try{
      const dataset=selectedDataset();if(!dataset||!selected.size)throw Error('请选择测试点');
      const source=el('testSource').value==='current'?debug.context():dataset.source;
      const fresh=el('testMode').value==='recognize';
      const body={dataset_id:dataset.id,point_ids:[...selected],label:el('testLabel').value.trim()||dataset.name,
        mode:fresh?'recognize':'saved',scope:el('testScope').value,
        source:{route:source.route,run_id:source.run_id,document:source.document}};
      if(fresh){body.model_profile=el('testModel').value;body.allow_external=el('testAllowExternal').checked;
        body.settings={max_calls:integer('testCalls',1,10000),model_concurrency:integer('testConcurrency',1,16)};
        if(source.route==='knowledge')body.settings.ocr_pages=integer('testOcr',0,1000);
      }
      testBusy=true;controls();debug.error();el('testProgress').textContent='正在创建独立测试记录…';
      const record=await requestJson(api+'tests',{method:'POST',body});
      sessionStorage.setItem('slopekg-debug-active-test',record.id);await pollTest(record.id);
    }catch(e){testBusy=false;controls();debug.error(debug.describeError(e));}
  }
  async function pollTest(id){
    try{
      const data=await getJson(api+'history/record?id='+encodeURIComponent(id)),r=data.record;
      testBusy=['queued','running'].includes(r.status);controls();
      el('testProgress').textContent=`${r.progress||0}% · ${states[r.status]||r.status} · ${r.message||''} · ${r.id}`;
      if(testBusy)pollTimer=setTimeout(()=>pollTest(id),1200);
      else{sessionStorage.removeItem('slopekg-debug-active-test');await loadHistory();await showDetail(id);}
    }catch(e){testBusy=false;controls();debug.error('测试状态读取失败，历史记录仍保留：'+e.message);}
  }
  async function loadHistory(){
    const current=++historyGeneration;
    try{
      const data=await getJson(api+'history?'+new URLSearchParams({q:el('historySearch').value,route:el('historyRoute').value,offset:String(historyOffset),limit:'15'}));
      if(current!==historyGeneration)return;historyCount=data.count;el('historyList').innerHTML=(data.records||[]).map(r=>`<article class="history-card"><div class="record-head"><strong>${esc(r.label)}</strong><span class="state-label ${r.status==='failed'?'bad':''}">${esc(r.mode==='snapshot'&&r.status==='completed'?'快照已保存':states[r.status]||r.status)}</span></div><p class="muted">${esc(new Date(r.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}))} · ${esc(debug.routeLabel(r.route))} · ${esc(r.mode==='saved'?'检查旧预测（0新API）':r.mode==='snapshot'?'完整过程快照':'重新识别测试')} · 识别状态：${esc(states[r.processing_status]||r.processing_status||'未记录')} · ${r.selected_points||0}个检查点<br>${esc(r.id)} · ${esc(r.source_name||r.message||'')}</p>${r.test_summary?`<p class="muted">显式规则匹配 ${r.test_summary.rule_matched}；未匹配 ${r.test_summary.rule_unmatched}；待复核 ${r.test_summary.needs_review}；绑定历史完整结论 ${r.test_summary.historical_complete}</p>`:''}${r.tags?.length?`<p class="muted">标签：${esc(r.tags.join('、'))}</p>`:''}<div class="debug-actions"><button class="button" data-detail="${esc(r.id)}">查看测试记录</button><button class="button ghost" data-open-record="${esc(r.id)}" ${r.snapshot_ready?'':'disabled'}>单独打开完整过程</button><button class="button ghost" data-compare-side="left" data-id="${esc(r.id)}">设为左侧</button><button class="button ghost" data-compare-side="right" data-id="${esc(r.id)}">设为右侧</button><a class="button ghost" href="${api}history/export?id=${encodeURIComponent(r.id)}">导出ZIP</a></div></article>`).join('')||'<p class="empty-state">尚无匹配历史。可以保存当前完整过程，或检查所选测试点。</p>';
      el('historyPage').textContent=`${historyCount?historyOffset+1:0}～${Math.min(historyOffset+15,historyCount)} / ${historyCount}`;el('historyPrev').disabled=!historyOffset;el('historyNext').disabled=historyOffset+15>=historyCount;
    }catch(e){if(current!==historyGeneration)return;el('historyList').textContent='历史记录暂不可读';debug.error(debug.describeError(e));}
  }
  function resultRows(data){
    return (data.results.rows||[]).map(r=>`<details class="test-result"><summary><strong>${esc(r.label)}</strong><span class="state-label">${esc(ruleStates[r.rule_status]||r.rule_status)}${r.historical_review_bound?' · 历史复核：'+(r.historical_review.complete?'完整':'不完整'):''}</span></summary><p class="muted">原件页 ${(r.original_pages||[]).join('、')||'未指定'}；本次输入页 ${(r.input_pages||[]).join('、')||'整份'}。${esc(r.note)}</p>${r.historical_review_bound?`<p>${esc(r.historical_review.reason_zh||r.historical_review.reason||'')}</p>`:''}<p class="muted">候选${r.candidate_fact_ids.length}条；程序接纳${r.pipeline_admitted_fact_ids.length}条。程序接纳与测试点语义正确分别判断。</p><div class="debug-actions"><button class="button ghost" data-evidence-point="${esc(r.point_id)}">查看候选事实/原文</button><button class="button ghost" data-review-point="${esc(r.point_id)}">填写本地复核</button></div>${debug.panel('完整检查结果',r)}</details>`).join('');
  }
  async function showDetail(id){
    const current=++detailGeneration;detail=null;el('historyTitle').textContent='正在读取记录…';el('historyResults').textContent='';
    for(const key of ['historyRaw','historyRecordInfo','reviewHistory','historyReviewStatus'])el(key).textContent='';
    for(const key of ['historyRename','historyTags','reviewNote'])el(key).value='';
    el('reviewPoint').innerHTML='';
    for(const key of ['openHistoryProcess','reuseTest','copyHistoryLink','saveReview'])el(key).disabled=true;
    el('exportHistory').hidden=true;el('exportHistory').removeAttribute('href');
    if(!el('historyDialog').open)el('historyDialog').showModal();
    try{
      const data=await getJson(api+'history/record?id='+encodeURIComponent(id));if(current!==detailGeneration||!el('historyDialog').open)return;detail=data;renderDetail();
    }catch(e){if(current===detailGeneration){el('historyTitle').textContent='读取失败';el('historyResults').textContent=e.message;}}
  }
  function renderDetail(){
    const {record:r,definition:d,results,reviews}=detail;
    el('historyTitle').textContent=r.label;el('historyRecordInfo').textContent=`${r.id} · ${states[r.status]||r.status} · 识别状态：${states[r.processing_status]||r.processing_status||'尚未产生'} · ${r.message||''}${r.error?'；'+r.error:''}`;
    el('openHistoryProcess').disabled=!r.snapshot_ready;el('exportHistory').href=api+'history/export?id='+encodeURIComponent(r.id);el('exportHistory').hidden=false;el('saveReview').disabled=false;
    el('reuseTest').disabled=!d.dataset?.id||!(d.points||[]).length;el('copyHistoryLink').disabled=!r.snapshot_ready;
    el('historyResults').innerHTML=`${results.metric_note?`<p class="notice">${esc(results.metric_note)}</p>`:''}${results.summary?`<p>所选${results.summary.selected}点；显式规则匹配${results.summary.rule_matched}点；未匹配${results.summary.rule_unmatched}点；待人工复核${results.summary.needs_review}点；识别不可用${results.summary.execution_unavailable||0}点。</p>`:''}${resultRows(detail)}`;
    el('historyRaw').textContent=JSON.stringify({record:r,definition:d,results},null,2);el('historyRename').value=r.label;el('historyTags').value=(r.tags||[]).join(', ');
    el('reviewPoint').innerHTML='<option value="">整次记录备注</option>'+(d.points||[]).map(p=>`<option value="${esc(p.id)}">${esc(p.label||p.id)}</option>`).join('');
    el('reviewHistory').innerHTML=(reviews||[]).map(row=>`<blockquote><small>${esc(new Date(row.at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}))} · ${esc(row.point_id||'整次记录')} · ${esc(reviewStates[row.verdict]||row.verdict)}</small><p>${esc(row.note)}</p></blockquote>`).join('')||'<p class="muted">暂无本地备注；追加后会保留全部历史。</p>';
  }
  async function openProcess(record,opts={}){
    if(!record.snapshot_ready)return;
    el('historyDialog').close();showView('process');await debug.openSource({route:record.route,run_id:'history-'+record.id,document:record.document_id},opts);
  }
  async function saveCurrent(){
    el('saveSnapshot').disabled=true;
    try{const source=debug.context();const r=await requestJson(api+'history',{method:'POST',body:{...source,label:el('snapshotLabel').value.trim()||'识别过程快照 · '+new Date().toLocaleString('zh-CN')}});debug.toast('完整历史已保存');await loadHistory();await showDetail(r.id);}
    catch(e){debug.error(debug.describeError(e));}finally{controls();}
  }
  async function saveReview(){
    if(!detail)return;
    const recordId=detail.record.id,current=detailGeneration;
    const sameDetail=()=>current===detailGeneration&&detail?.record.id===recordId&&el('historyDialog').open;
    el('saveReview').disabled=true;
    try{
      const tags=el('historyTags').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean),label=el('historyRename').value.trim();
      const changed=label!==detail.record.label||JSON.stringify(tags)!==JSON.stringify(detail.record.tags||[]);
      const note=el('reviewNote').value.trim()||(changed?'调整记录名称或标签':'');if(!note)throw Error('请填写备注或判定依据');
      const saved=await requestJson(api+'history/review',{method:'POST',body:{id:recordId,point_id:el('reviewPoint').value,verdict:el('reviewVerdict').value,note,label,tags}});
      await loadHistory();
      if(!sameDetail())return;
      detail=saved;el('reviewNote').value='';el('historyReviewStatus').textContent='已追加保存';renderDetail();debug.toast('已追加保存，旧备注与判定仍保留');
    }catch(e){if(sameDetail()){el('historyReviewStatus').textContent=e.message;debug.error(debug.describeError(e));}else debug.toast('历史 '+recordId+' 的备注保存失败：'+e.message);}
    finally{if(sameDetail())el('saveReview').disabled=false;}
  }
  async function saveDataset(){
    el('saveDataset').disabled=true;
    try{const points=JSON.parse(el('datasetJson').value);const d=await requestJson(api+'datasets',{method:'POST',body:{name:el('datasetName').value,source:debug.context(),points,open_development:el('datasetOpen').checked}});el('datasetSaveStatus').textContent='已保存新数据集版本：'+d.id;await loadDatasets(d.id);}
    catch(e){el('datasetSaveStatus').textContent=e.message;debug.error(debug.describeError(e));}finally{el('saveDataset').disabled=false;}
  }
  document.querySelector('.debug-view-switch').onclick=e=>{const b=e.target.closest('[data-view]');if(b)showView(b.dataset.view);};
  el('dataset').onchange=()=>{selected=new Set((selectedDataset()?.points||[]).map(p=>p.id));renderPoints();sourceChanged();};
  el('testSource').onchange=sourceChanged;
  el('testModel').onchange=()=>{el('testAllowExternal').checked=false;testEndpoint();controls();};
  el('testPoints').onchange=e=>{if(e.target.classList.contains('point-check')){if(e.target.checked)selected.add(e.target.value);else selected.delete(e.target.value);controls();}};
  el('pointSearch').oninput=renderPoints;el('selectPoints').onclick=()=>{for(const box of el('testPoints').querySelectorAll('.point-check'))selected.add(box.value);renderPoints();};el('clearPoints').onclick=()=>{selected.clear();renderPoints();};
  el('testMode').onchange=el('testAllowExternal').onchange=controls;el('runTest').onclick=runTest;el('reloadDatasets').onclick=()=>loadDatasets();
  let historyContextRoute=debug.context().route;
  window.addEventListener('debugrecordstatechange',controls);
  el('saveSnapshot').onclick=saveCurrent;window.addEventListener('debugcontextchange',()=>{
    sourceChanged();const route=debug.context().route;
    if(route!==historyContextRoute){historyContextRoute=route;el('historyRoute').value=route;historyOffset=0;if(!el('historyView').hidden)loadHistory();}
  });window.addEventListener('debughistorychange',()=>loadHistory());
  el('historyRoute').onchange=()=>{historyOffset=0;loadHistory();};el('reloadHistory').onclick=loadHistory;el('searchHistory').onclick=()=>{historyOffset=0;loadHistory();};el('historySearch').onkeydown=e=>{if(e.key==='Enter'){historyOffset=0;loadHistory();}};
  el('historyPrev').onclick=()=>{historyOffset=Math.max(0,historyOffset-15);loadHistory();};el('historyNext').onclick=()=>{historyOffset+=15;loadHistory();};
  el('historyList').onclick=async e=>{const b=e.target.closest('button');if(!b)return;try{if(b.dataset.detail)await showDetail(b.dataset.detail);if(b.dataset.openRecord){const data=await getJson(api+'history/record?id='+b.dataset.openRecord);await openProcess(data.record);}if(b.dataset.compareSide)el(b.dataset.compareSide==='left'?'compareLeft':'compareRight').value=b.dataset.id;}catch(error){debug.error(error.message);}};
  el('compareHistory').onclick=async()=>{
    try{
      if(!el('compareLeft').value||!el('compareRight').value)throw Error('请先在历史记录中选定左侧和右侧');
      el('historyComparison').textContent='正在比较所选历史…';
      const data=await getJson(api+'history/compare?'+new URLSearchParams({left:el('compareLeft').value,right:el('compareRight').value}));
      const hasPoints=data.has_test_results??!!data.rows?.length;
      const summary=hasPoints?`${data.same_dataset_version?'数据集版本一致':'数据集版本不同，请核对预期变化'}；${data.changed_points}个测试点记录有变化。`:'这两条是过程快照，没有可比较的测试点结果；请打开完整过程逐步对照。';
      el('historyComparison').innerHTML=`<p>${summary}</p><p class="muted">${esc(data.note)}</p>${debug.panel('完整对照数据与本地复核',data,true)}`;
    }catch(e){el('historyComparison').textContent='比较未完成：'+debug.describeError(e);debug.error(debug.describeError(e));}
  };
  el('closeHistory').onclick=()=>el('historyDialog').close();el('historyDialog').addEventListener('close',()=>{detailGeneration++;});el('openHistoryProcess').onclick=()=>detail&&openProcess(detail.record);
  el('copyHistoryLink').onclick=async()=>{if(!detail)return;const url=new URL(location.href);url.search=new URLSearchParams({route:detail.record.route,run:'history-'+detail.record.id,stage:'input'});try{await navigator.clipboard.writeText(url.href);debug.toast('已复制独立历史链接');}catch(_){debug.error('复制未获浏览器允许，可使用单独打开后的地址');}};
  el('reuseTest').onclick=async()=>{if(!detail)return;const old=detail;el('historyDialog').close();showView('tests');await loadDatasets(old.definition.dataset.id);const current=selectedDataset();if(current?.dataset_hash!==old.definition.dataset_hash){debug.error('数据集定义已变化，请核对当前预期后重新选择；没有自动套用旧选择');return;}let ids=old.definition.points.map(p=>p.id);if(el('reuseScope').value==='pending')ids=(old.results.rows||[]).filter(r=>r.rule_status!=='matched'||r.historical_review_bound&&!r.historical_review.complete).map(r=>r.point_id);if(el('reuseScope').value==='local_fail'){const latest=new Map((old.reviews||[]).filter(r=>r.point_id).map(r=>[r.point_id,r.verdict]));ids=ids.filter(id=>latest.get(id)==='fail');}selected=new Set(ids);if(!ids.length)debug.toast('该范围没有检查点，请重新选择');if(old.definition.settings){el('testCalls').value=old.definition.settings.max_calls;el('testConcurrency').value=old.definition.settings.model_concurrency;el('testOcr').value=old.definition.settings.ocr_pages||0;}if(old.definition.model_profile_id)el('testModel').value=old.definition.model_profile_id;el('testScope').value=old.definition.scope||'whole_input';el('testMode').value='saved';if(old.record.snapshot_ready){await debug.openSource({route:old.record.route,run_id:'history-'+old.record.id,document:old.record.document_id});el('testSource').value='current';}else el('testSource').value='dataset';el('testLabel').value=old.record.label+' · 重测';el('testAllowExternal').checked=false;renderPoints();};
  el('historyResults').onclick=e=>{const b=e.target.closest('button');if(!b||!detail)return;if(b.dataset.reviewPoint){el('reviewPoint').value=b.dataset.reviewPoint;el('reviewVerdict').value='pending';el('reviewNote').focus();}if(b.dataset.evidencePoint){const row=detail.results.rows.find(r=>r.point_id===b.dataset.evidencePoint);openProcess(detail.record,{targetStage:row.candidate_fact_ids.length?'facts':'units',q:row.candidate_fact_ids[0]||'',page:row.input_pages[0]||0});}};
  el('saveReview').onclick=saveReview;el('saveDataset').onclick=saveDataset;el('fillPointTemplate').onclick=()=>{el('datasetJson').value=JSON.stringify([{id:'p001',label:'示例：检查高度字段',pages:[1],expected:{field:'高度',value:'12',unit:'m'},checks:[{field:'field',op:'contains',value:'高度'},{field:'value',op:'equals',value:'12'},{field:'unit',op:'equals',value:'m'}],require_evidence:true}],null,2);};
  window.addEventListener('beforeunload',()=>clearTimeout(pollTimer));
  controls();const tab=debug.initialView;if(['tests','history'].includes(tab))showView(tab);
  const active=sessionStorage.getItem('slopekg-debug-active-test');if(active){testBusy=true;controls();pollTest(active);}
})();
