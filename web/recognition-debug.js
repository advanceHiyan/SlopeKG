(function () {
  'use strict';
  const {getJson, requestJson, escapeHtml: esc} = window.SlopeKGApi;
  const el = id => document.getElementById(id);
  const labels = {completed:'完成',prepared:'仅本地准备',partial:'部分完成',failed:'失败',pending:'待审',rejected:'拒绝',
    running:'执行中',started:'已发起',queued:'排队中',source_aligned:'原文对齐 · 未独立复核',verified_model:'模型核验通过',
    not_generated:'尚未生成',native_text:'原生文字',ocr_text:'OCR文字',unread_graphics:'图形待读',blank:'空白/无可用文字'};
  let catalog = null, stage = 'input', rows = [], offset = 0, total = 0, generation = 0, catalogBusy=false;
  let activeCall = null, pinned = null, busy = false, profiles = [], files = [], pollTimer, refreshTimer, searchTimer, callGeneration=0;
  let pageRequest=0,pageController=null,pageBlobUrl=null,currentPreviewPage=1,previewBox=null,recordsBusy=false;
  let selectedRecord=null,loadedPreview=null,previewMode='record';
  let recordsContext=null;
  const routeLabels={basic:'基读 Basic',knowledge:'知联 Knowledge',visual:'视读 Visual'};
  const methodModels={knowledge:'deepseek-knowledge',visual:'deepseek'};
  const query = new URLSearchParams(location.search);
  // Plain navigation links restore the last workspace in this tab. Explicit links win.
  if(!query.size){try{const saved=JSON.parse(sessionStorage.getItem('slopekg-debug-context')||'{}');for(const [k,v] of Object.entries(saved))query.set(k,v);}catch(_){}}
  if (['knowledge','visual','basic'].includes(query.get('route'))) el('route').value = query.get('route');
  if(query.get('run')&&query.get('run')!=='live')el('run').add(new Option(query.get('run'),query.get('run'),true,true));
  stage=query.get('stage')||'input';
  if(query.has('q'))el('search').value=query.get('q');
  if(/^\d+$/.test(query.get('page')||''))el('page').value=query.get('page');
  if(/^\d+$/.test(query.get('offset')||'')&&Number(query.get('offset'))%25===0)offset=Number(query.get('offset'));
  const header=document.querySelector('.site-header');
  new ResizeObserver(()=>document.documentElement.style.setProperty('--header-height',`${header.getBoundingClientRect().height}px`)).observe(header);
  function rememberContext(){
    const value={route:el('route').value,run:el('run').value||'live',document:el('document').value,stage,q:el('search').value,page:el('page').value,offset:String(offset),
      tab:document.querySelector('[data-view].active')?.dataset.view||query.get('tab')||'process'};
    try{sessionStorage.setItem('slopekg-debug-context',JSON.stringify(value));}catch(_){}
    history.replaceState(null,'',`?${new URLSearchParams(value)}`);
  }
  function methodUI({chooseModel=false}={}){
    const route=el('route').value,basic=route==='basic';
    for(const b of document.querySelectorAll('[data-route]')){b.setAttribute('aria-pressed',String(b.dataset.route===route));b.disabled=busy;}
    el('modelLaunch').hidden=basic;el('basicLaunch').hidden=!basic;el('ocrLabel').hidden=route!=='knowledge';
    el('prepare').hidden=basic;el('start').textContent=basic?'运行基读 · 更新基础图谱':'运行并记录';
    el('launchPanel').querySelector('summary').textContent=`新建${routeLabels[route]}任务 · ${basic?'本地解析与规则抽取':'保存完整模型输入输出'}`;
    el('allowText').textContent=route==='visual'?'允许把上述资料原文和页面图像发送到所选模型接口':'允许把上述资料原文发送到所选模型接口（不发送原页图像）';
    if(chooseModel&&profiles.some(p=>p.id===methodModels[route]))el('model').value=methodModels[route];
    endpoint();
  }
  function changeRoute(){
    el('run').innerHTML='<option value="live">当前生产记录</option>';el('allow').checked=false;offset=0;stage='input';
    methodUI({chooseModel:true});loadCatalog({reset:true});
  }
  function error(message='') {el('error').textContent=message;el('error').hidden=!message;}
  function describeError(e) {return e.status===404?`当前地址（${location.host}）的后端未加载识别调试接口（404）。请重新打开SlopeKG桌面入口后刷新页面。`:e.message;}
  function toast(message) {el('toast').textContent=message;el('toast').hidden=false;setTimeout(()=>{el('toast').hidden=true;},3500);}
  function params(extra={}) {return new URLSearchParams({route:el('route').value,run:el('run').value||'live',document:el('document').value,...extra});}
  function api(suffix='', extra={}) {return `/api/recognition-debug${suffix}?${params(extra)}`;}
  function pretty(value) {return typeof value==='string'?value:JSON.stringify(value,null,2);}
  function panel(title,value,open=false) {return `<details class="json-panel" ${open?'open':''}><summary>${esc(title)}</summary><pre>${esc(value===undefined||value===null?'未保存':pretty(value))}</pre></details>`;}
  function state(value) {const bad=['failed','pending','rejected'].includes(value),good=['completed','source_aligned','verified_model'].includes(value);return `<span class="state-label ${bad?'bad':good?'good':''}">${esc(labels[value]||value||'已保存')}</span>`;}
  function download(name,data) {const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function copy(value) {try{await navigator.clipboard.writeText(pretty(value));toast('已复制完整内容');}catch(_){toast('浏览器未允许复制，请使用下载');}}
  function sourceLink() {return api('/source.pdf');}
  function sourceContext(){return [el('route').value,el('run').value||'live',el('document').value].join('|');}
  function recordViewContext(){return JSON.stringify([sourceContext(),stage,offset,el('page').value,el('search').value,el('failures').checked]);}
  function recordsReady(){return !catalogBusy&&!recordsBusy&&recordsContext===recordViewContext();}
  function recordKey(row){return row.id?[row.id,row.record_type,row.source].join('|'):JSON.stringify(row);}
  function updatePreviewBinding(){
    const selected=selectedRecord?.context===sourceContext()?selectedRecord:null;
    const loaded=loadedPreview?.context===sourceContext()?loadedPreview:null;
    const name=catalog?.documents.find(d=>d.id===el('document').value)?.name||'';
    el('sourceDocumentName').textContent=name;
    let message,warning=false;
    if(selected){
      if(loaded){
        const mismatch=loaded.page!==selected.page;
        message=mismatch?`手动浏览第${loaded.page}页；所选记录 ${selected.row.id||selected.index+1} 在第${selected.page}页。当前不同页，请返回所选记录。`:`${previewMode==='manual'?'手动浏览，同页记录':'跟随记录'} ${selected.row.id||selected.index+1} → PDF物理第${loaded.page}页（页码一致）。`;
        warning=mismatch;
      }else message=`所选记录 ${selected.row.id||selected.index+1} 来自物理第${selected.page}页。${el('followRecord').checked?'正在准备对应原页。':'尚未显示对应原页，可点击返回所选记录。'}`;
    }else message=loaded?`手动浏览PDF物理第${loaded.page}页；当前没有选中可定位的记录。`:'当前没有选中可定位的记录，可手动浏览PDF。';
    for(const id of ['sourceBinding','pdfBinding']){el(id).textContent=message;el(id).className=warning?'notice source-mismatch':'notice info';}
    for(const id of ['returnToRecord','pdfReturnToRecord']){el(id).hidden=!selected||previewMode==='record'&&(!loaded||loaded.page===selected.page);el(id).textContent=selected?`返回所选记录 · 第${selected.page}页`:'返回所选记录原页';}
    for(const card of el('records').querySelectorAll('[data-record-index]')){
      const active=selected&&Number(card.dataset.recordIndex)===selected.index;card.classList.toggle('selected-record',!!active);
      if(active)card.querySelector('summary').setAttribute('aria-current','true');else card.querySelector('summary').removeAttribute('aria-current');
    }
  }
  function selectRecord(index,{force=false,open=false}={}){
    if(!recordsReady())return;
    const row=rows[index];if(!row)return;
    const page=firstPage(row);selectedRecord=page?{row,index,page,context:sourceContext(),stage}:null;
    if(force)el('followRecord').checked=true;
    if(selectedRecord&&el('followRecord').checked){
      const box=boxOf(row)||null;
      if(loadedPreview?.context===sourceContext()&&loadedPreview.page===page&&JSON.stringify(previewBox)===JSON.stringify(box)&&previewMode==='record'){
        updatePreviewBinding();if(open){if(!el('pageDialog').open)el('pageDialog').showModal();resizePage();}
      }else showPage(page,box,{mode:'record',open});
    }else updatePreviewBinding();
  }
  function syncRecordPreview(){
    const previous=selectedRecord;
    let index=previous?.context===sourceContext()&&previous.stage===stage?rows.findIndex(row=>recordKey(row)===recordKey(previous.row)):-1;
    if(index<0)index=rows.findIndex(row=>firstPage(row)>0);
    if(index>=0)selectRecord(index);
    else{selectedRecord=null;if(el('followRecord').checked)clearSource();updatePreviewBinding();}
  }
  function clearSource() {
    pageRequest++;pageController?.abort();if(pageBlobUrl)URL.revokeObjectURL(pageBlobUrl);pageBlobUrl=null;currentPreviewPage=1;previewBox=null;
    selectedRecord=null;loadedPreview=null;previewMode='record';
    if(el('pageDialog').open)el('pageDialog').close();
    for(const id of ['sourceImage','pageDialogImage'])el(id).removeAttribute('src');
    for(const id of ['sourceCanvas','pageDialogCanvas','sourceError','pdfError','downloadPage'])el(id).hidden=true;
    for(const id of ['sourceOverlay','pageDialogOverlay'])el(id).innerHTML='';
    for(const id of ['sourcePrev','sourceNext','pdfPrev','pdfNext'])el(id).disabled=true;
    for(const id of ['sourceError','pdfError','sourceStatus','pdfStatus','sourcePageInfo','pdfPageInfo'])el(id).textContent='';
    el('previewPage').value=el('dialogPage').value='1';el('openPdf').hidden=true;
    el('sourceNote').textContent='选择记录后定位到对应原页；手动浏览时会单独标记。';updateSourcePager();updatePreviewBinding();
  }
  function clearRecords(message='选择一份资料，开始查看识别过程。') {
    recordsBusy=false;
    recordsContext=null;
    selectedRecord=null;if(el('followRecord').checked)clearSource();else updatePreviewBinding();
    el('traceCoverage').hidden=true;
    rows=[];total=0;el('records').innerHTML=`<p class="empty-state">${esc(message)}</p>`;el('recordCount').textContent='0项';el('pageInfo').textContent='';
    el('prev').disabled=el('next').disabled=el('downloadStage').disabled=el('pin').disabled=true;
  }
  function renderStages() {
    el('stages').innerHTML=(catalog?.stages||[]).map((s,i)=>`<button type="button" data-stage="${esc(s.id)}" ${s.id===stage?'aria-current="step"':''}><span>${i+1}</span>${esc(s.label)}</button>`).join('');
    const selected=catalog?.stages.find(s=>s.id===stage);el('stageTitle').textContent=selected?.label||'处理步骤';el('stageHelp').textContent=selected?.help||'';
  }
  async function loadCatalog({reset=false}={}) {
    const current=++generation;catalogBusy=true;el('document').disabled=el('showPage').disabled=true;error();clearRecords('正在读取记录…');clearSource();
    try {
      const requestedRun=el('run').value||'live',requestedDoc=reset?'':el('document').value||query.get('document');
      const data=await getJson(api('/catalog'));
      if(current!==generation)return;
      catalogBusy=false;el('document').disabled=false;catalog=data;el('run').innerHTML=data.runs.map(r=>`<option value="${esc(r.id)}">${esc(r.label)}</option>`).join('');
      el('run').value=data.runs.some(r=>r.id===requestedRun)?requestedRun:'live';
      el('document').innerHTML=data.documents.map(d=>`<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')||'<option value="">当前路线没有保存的识别记录</option>';
      if(data.documents.some(d=>d.id===requestedDoc))el('document').value=requestedDoc;
      if(!data.stages.some(s=>s.id===stage))stage='input';renderStages();
      el('runStatus').textContent=labels[data.status]||data.status;
      el('version').textContent={current:'代码版本与保存记录一致',different_implementation:'历史记录 · 代码已变化',unversioned:'历史记录未保存代码版本'}[data.implementation_status]||'';
      el('showPage').disabled=!el('document').value;
      updateSourcePager();
      el('routeSummary').textContent=`当前：${routeLabels[el('route').value]} · 本次记录${data.documents.length}份资料 · ${data.stages.length}个处理步骤 · 可打开${data.runs.filter(r=>r.id.startsWith('history-')).length}条独立历史。${el('route').value==='basic'?'本方法没有大模型调用。':'模型输入输出请进入左侧对应步骤；旧记录未保存的请求不会补造。'}`;
      rememberContext();
      window.dispatchEvent(new Event('debugcontextchange'));
      if(el('document').value){el('openPdf').href=sourceLink();el('openPdf').hidden=false;await loadRecords();}
      else clearRecords('当前没有记录。可新建调试任务，或从“记录来源”选择已开放样例。');
    }catch(e){if(current!==generation)return;catalogBusy=false;catalog=null;el('document').innerHTML='<option value="">记录读取失败</option>';el('runStatus').textContent='暂不可读';el('version').textContent='';el('showPage').disabled=true;renderStages();clearRecords('记录读取失败，请刷新。');error(describeError(e));}
  }
  function firstPage(row) {
    if(Number.isInteger(row.page))return row.page;
    if(Array.isArray(row.pages)&&Number.isInteger(row.pages[0]))return row.pages[0];
    const evidence=(row.evidence||[]).find(e=>Number.isInteger(e.page));
    return evidence?.page||0;
  }
  function boxOf(row) {
    if(Array.isArray(row.debug_bbox))return row.debug_bbox;
    if(Array.isArray(row.bbox)&&row.bbox.length===4&&row.bbox.every(x=>Number.isFinite(x)&&x>=0&&x<=1))return row.bbox;
    return (row.evidence||[]).find(e=>e.page===firstPage(row)&&Array.isArray(e.bbox))?.bbox;
  }
  function titleOf(row,index) {return row.object&&row.field?`${row.object} · ${row.field} = ${row.value??''} ${row.unit||''}`:row.name||row.stage||row.id||row.record_type||`记录 ${offset+index+1}`;}
  function previewOf(row) {return row.text??row.quote??row.error??row.reason??(row.cells?row.cells.join(' | '):'');}
  function renderRecords() {
    el('records').innerHTML=rows.map((r,i)=>{
      const page=firstPage(r),preview=previewOf(r),meta=[r.id,page?`物理页 ${page}`:'整份资料 / 无独立页码',r.source,r.record_type,r.cache_hit?'本次命中缓存':'',r.duration_seconds!=null?`${r.duration_seconds} 秒`:'',r.prompt_chars!=null?`提示词 ${r.prompt_chars} 字符 · ${r.image_count} 张图`:''].filter(Boolean).join(' · ');
      return `<details class="record-card" data-record-index="${i}" ${rows.length<=3?'open':''}><summary title="选择此记录；自动跟随开启时显示对应原页"><div class="record-head"><strong>${esc(titleOf(r,i))}</strong>${state(r.status||r.attribution_status)}</div><div class="record-meta">${esc(meta)}</div></summary><div class="record-body">${preview?`<pre class="record-preview">${esc(preview)}</pre>`:''}${stage==='calls'?`<p class="muted">输入：${r.input_saved?'已保存':'历史缺失'} · 原始交互文件：${r.raw_exchange_saved?'已保存（可能仅有请求，回复是否收到见详情）':'未保存'} · 解析/校验结果可查看</p>`:''}<div class="record-actions">${page?`<button class="button ghost" data-locate="${i}">定位原页 ${page}</button>`:''}${stage==='calls'?`<button class="button" data-call="${esc(r.id)}">查看完整输入输出</button>`:''}<button class="button ghost" data-copy="${i}">复制此项</button><button class="button ghost" data-download="${i}">下载此项</button></div>${panel('完整中间记录 · 全部字段',r)}</div></details>`;
    }).join('')||'<p class="empty-state">当前筛选下没有已保存记录。请调整页码、搜索或异常筛选。<br>未保存的历史过程不会自动补造。</p>';
    const batches=Math.ceil(total/25),batch=total?Math.floor(offset/25)+1:0;
    el('recordCount').textContent=`${total}项 · 第${batch}/${batches}批`;el('pageInfo').textContent=total?`第${batch}/${batches}批 · ${offset+1}～${Math.min(offset+25,total)} / 共${total}条`:'0条记录';
    el('prev').title=offset===0?(batches<=1?'当前只有一批记录；PDF翻页请使用原页窗口':'已是第一批记录'):'查看前25条记录';
    el('next').title=offset+25>=total?(batches<=1?'当前只有一批记录；PDF翻页请使用原页窗口':'已是最后一批记录'):'查看后25条记录';
    el('prev').disabled=offset===0;el('next').disabled=offset+25>=total;el('downloadStage').disabled=!total;el('pin').disabled=!rows.length;
  }
  function filters(extra={}) {return {stage,page:el('page').value||'0',q:el('search').value,failures:el('failures').checked?'1':'0',offset:String(offset),limit:'25',...extra};}
  async function loadRecords({reveal=false}={}) {
    if(catalogBusy||!el('document').value)return;
    const previousSelection=selectedRecord,viewContext=recordViewContext();
    if(reveal||selectedRecord&&(selectedRecord.stage!==stage||selectedRecord.context!==sourceContext())){
      selectedRecord=null;if(el('followRecord').checked)clearSource();else updatePreviewBinding();
    }
    const current=++generation;recordsBusy=true;recordsContext=null;
    rows=[];selectedRecord=null;updatePreviewBinding();
    el('prev').disabled=el('next').disabled=el('downloadStage').disabled=el('pin').disabled=el('saveSnapshot').disabled=true;
    el('recordNote').textContent='正在读取完整记录…';el('records').innerHTML='<p class="empty-state">正在读取当前资料的记录；旧记录操作已清空…</p>';
    window.dispatchEvent(new Event('debugrecordstatechange'));
    try {
      const data=await getJson(api('',filters()));if(current!==generation||viewContext!==recordViewContext())return;
      if(offset>0&&offset>=(data.count||0)){offset=Math.floor(Math.max(0,(data.count||0)-1)/25)*25;return loadRecords({reveal});}
      rows=data.rows||[];total=data.count||0;rememberContext();el('recordNote').textContent=`${data.note} 页码0表示全部。`;
      const trace=data.trace_coverage;el('traceCoverage').hidden=!trace;
      if(trace)el('traceCoverage').textContent=trace.calls?`整份资料保存了${trace.calls}次调用记录；输入文件 ${trace.saved_inputs}/${trace.calls}，原始交互文件 ${trace.saved_raw_exchanges}/${trace.calls}（文件可能仅有请求，是否收到响应见详情）。${trace.saved_raw_exchanges<trace.calls?'部分历史仅有解析/校验后的缓存结果，不能当作模型原话；缺失原始响应无法补回。':'可打开每次调用检查完整请求和原始响应。'}`:'未发现可归属到此资料的已保存调用；请结合处理状态检查是否仅完成本地准备。';
      recordsBusy=false;recordsContext=viewContext;selectedRecord=previousSelection;renderRecords();syncRecordPreview();error();
      window.dispatchEvent(new Event('debugrecordstatechange'));
      if(reveal){el('records').scrollIntoView({block:'start'});toast(`已切换到第${Math.floor(offset/25)+1}/${Math.ceil(total/25)}批记录`);}
    }catch(e){if(current!==generation)return;recordsBusy=false;clearRecords('中间记录暂不可读');el('recordNote').textContent='';error(e.message);window.dispatchEvent(new Event('debugrecordstatechange'));}
  }
  function previewTotal(){const value=catalog?.documents.find(d=>d.id===el('document').value)?.pages;return Number.isInteger(value)&&value>0?value:null;}
  function updateSourcePager(number=Number(el('previewPage').value)||1){
    const total=previewTotal(),available=!catalogBusy&&!!el('document').value;
    for(const id of ['sourcePrev','pdfPrev'])el(id).disabled=!available||number<=1;
    for(const id of ['sourceNext','pdfNext'])el(id).disabled=!available||!!total&&number>=total;
    for(const id of ['previewPage','dialogPage']){if(total)el(id).max=String(total);else el(id).removeAttribute('max');}
    el('sourcePageInfo').textContent=el('pdfPageInfo').textContent=total?`${number} / ${total}页`:`物理第${number}页`;
  }
  function resizePage(){
    const image=el('pageDialogImage'),canvas=el('pageDialogCanvas'),area=el('pageReaderScroll'),zoom=el('pageZoom').value;
    if(!image.naturalWidth)return;
    canvas.style.width=zoom==='width'?'100%':zoom==='page'?`${Math.max(1,Math.min(area.clientWidth-24,(area.clientHeight-24)*image.naturalWidth/image.naturalHeight))}px`:`${image.naturalWidth*Number(zoom)/100}px`;
  }
  async function showPage(number,box=null,{mode,open=true}={}) {
    if(catalogBusy||!el('document').value)return;
    mode=mode||(el('followRecord').checked&&selectedRecord?.page===number?'record':'manual');
    if(mode==='manual')el('followRecord').checked=false;
    previewMode=mode;loadedPreview=null;
    const request=++pageRequest;pageController?.abort();pageController=new AbortController();
    if(open&&!el('pageDialog').open)el('pageDialog').showModal();
    el('pageDocumentName').textContent=catalog?.documents.find(d=>d.id===el('document').value)?.name||'';
    for(const id of ['sourceError','pdfError','sourceCanvas','pageDialogCanvas','downloadPage'])el(id).hidden=true;
    el('sourceStatus').textContent=el('pdfStatus').textContent=`正在加载PDF物理第${number}页…`;
    updatePreviewBinding();
    const context=sourceContext();
    try{
      const total=previewTotal();
      if(!Number.isInteger(number)||number<1||total&&number>total)throw Error(`请输入${total?'1～'+total:'大于0的整数'}范围内的PDF物理页码`);
      currentPreviewPage=number;previewBox=box;el('previewPage').value=el('dialogPage').value=String(number);updateSourcePager(number);
      el('pageDialogTitle').textContent=`PDF原页 · 物理第${number}页`;
      el('openPdf').href=el('pageOriginalPdf').href=`${sourceLink()}#page=${number}`;el('openPdf').hidden=false;
      const response=await fetch(api('/page.png',{page:String(number)}),{cache:'no-store',signal:pageController.signal});
      if(!response.ok){let message=`HTTP ${response.status}`;try{message=(await response.json()).error||message;}catch(_){}throw Error(message);}
      if(!/^image\/png(?:;|$)/i.test(response.headers.get('Content-Type')||''))throw Error('原页接口没有返回PNG图像，请检查后端版本');
      const url=URL.createObjectURL(await response.blob()),probe=new Image();probe.src=url;
      try{await probe.decode();}catch(_){URL.revokeObjectURL(url);throw Error('返回的原页图像无法解码，请重试');}
      if(request!==pageRequest){URL.revokeObjectURL(url);return;}
      const previous=pageBlobUrl;pageBlobUrl=url;
      el('sourceImage').src=el('pageDialogImage').src=url;
      await el('pageDialogImage').decode();if(request!==pageRequest)return;
      if(previous)URL.revokeObjectURL(previous);
      const overlay=box?`<rect x="${Number(box[0])}" y="${Number(box[1])}" width="${Math.max(0,box[2]-box[0])}" height="${Math.max(0,box[3]-box[1])}"/>`:'';
      el('sourceOverlay').innerHTML=el('pageDialogOverlay').innerHTML=overlay;
      el('sourceCanvas').hidden=el('pageDialogCanvas').hidden=false;resizePage();el('pageReaderScroll').scrollTop=el('pageReaderScroll').scrollLeft=0;
      loadedPreview={context,page:number};updatePreviewBinding();
      el('sourceStatus').textContent=`已加载PDF物理第${number}页${total?'，共'+total+'页':''}。`;
      el('pdfStatus').textContent=el('sourceStatus').textContent+'可缩放阅读；这里的页码以本次输入PDF为准。';
      el('sourceNote').textContent=box?`物理页 ${number} · 橙色框是已保存记录的来源坐标`:`物理页 ${number} · 此记录未提供可用的页面归一化坐标`;
      el('downloadPage').href=url;el('downloadPage').download=`PDF物理第${number}页.png`;el('downloadPage').hidden=false;
    }catch(e){
      if(request!==pageRequest||e.name==='AbortError')return;
      el('sourceStatus').textContent=el('pdfStatus').textContent='原页加载失败';
      for(const id of ['sourceError','pdfError']){el(id).textContent=`无法显示原页：${e.message}`;el(id).hidden=false;}
    }
  }
  async function showCall(key) {
    const currentCall=++callGeneration;
    activeCall=null;el('callBody').textContent='正在读取…';el('callNotes').textContent='';el('downloadCall').disabled=el('copyCall').disabled=true;el('callDialog').showModal();
    const path=api('/call',{key});
    try {
      const data=await getJson(path);if(!el('callDialog').open||currentCall!==callGeneration)return;activeCall=data;
      el('callNotes').textContent=(data.notes||[]).join('\n')||'供应商响应在JSON解析与校验之前保存。请求正文不含鉴权请求头。';
      const inp=data.input,cache=data.cache;
      el('callBody').innerHTML=`<p class="muted">调用标识 ${esc(key)}</p><div class="call-columns"><section><h3>模型输入</h3>${panel('系统提示词 · system',inp?.system,true)}${panel('完整用户提示词 · prompt',inp?.prompt,true)}${panel('模型配置与图片清单',inp?{model:inp.model,images:inp.images,input_capture:inp.input_capture}:null)}</section><section><h3>模型输出</h3>${panel('校验前解析对象 · raw_answer / invalid_answer',cache?.raw_answer??cache?.invalid_answer,true)}${panel('校验后的结果 · answer',cache?.answer,true)}${panel('调用回执、用量和失败原因',cache?{status:cache.status,error:cache.error,receipt:cache.receipt,attempts:cache.attempts}:null)}</section></div>`;
      for(const img of data.image_previews||[])el('callBody').insertAdjacentHTML('beforeend',`<details class="json-panel"><summary>已保存输入图像 ${img.index+1} · ${esc(img.label)}</summary>${img.available?`<img class="model-image" alt="已保存请求图像 ${img.index+1}" src="${esc(img.url)}">`:'历史未保存该图像文件'}</details>`);
      for(const [i,attempt] of (data.exchange?.attempts||[]).entries()){
        const response=attempt.response,choices=response?.choices||[],message=choices[0]?.message;
        el('callBody').insertAdjacentHTML('beforeend',`<section class="exchange-attempt"><h3>请求与原始响应 · 尝试 ${i+1}</h3><div class="call-columns"><section>${panel('请求正文 · 含精确参数和图片数据，发送状态见回执',attempt.request,true)}</section><section>${panel('模型原始回复正文 · content',message?.content,true)}${message?.reasoning_content?panel('模型返回的 reasoning_content',message.reasoning_content):''}${attempt.raw_response_body!==undefined?panel('供应商原始HTTP响应正文 · 原字串',attempt.raw_response_body):''}${panel('供应商完整JSON响应 · 解析前',response,true)}</section></div></section>`);
        const images=(attempt.request?.messages||[]).flatMap(m=>Array.isArray(m.content)?m.content:[]).filter(c=>c.type==='image_url');
        images.forEach((item,n)=>{const url=item.image_url?.url;if(/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(url||''))el('callBody').insertAdjacentHTML('beforeend',`<details class="json-panel"><summary>请求中的图片 ${n+1}</summary><img class="model-image" alt="请求中的图片 ${n+1}" src="${esc(url)}"></details>`);});
      }
      if(!data.exchange)el('callBody').insertAdjacentHTML('beforeend',panel('完整历史缓存（含保留的失败对象）',cache));
      el('downloadCall').disabled=el('copyCall').disabled=false;
    }catch(e){if(currentCall===callGeneration)el('callBody').textContent=`读取失败：${e.message}`;}
  }
  async function downloadStage() {
    const base=api('',filters({offset:'0',limit:'100'})),name=`${el('route').value}-${el('document').value}-${stage}.json`;
    el('downloadStage').disabled=true;
    try {
      const first=await getJson(base),all=[...first.rows];
      for(let next=100;next<first.count;next+=100){const url=new URL(base,location.origin);url.searchParams.set('offset',String(next));const data=await getJson(url.pathname+url.search);if(data.snapshot!==first.snapshot||data.count!==first.count)throw Error('任务正在变化，请结束后重新下载，以免混合不同快照');all.push(...data.rows);}
      download(name,{...first,rows:all,offset:0,limit:all.length});toast(`已下载 ${all.length} 条完整记录`);
    }catch(e){error(e.message);}finally{el('downloadStage').disabled=!total;}
  }
  function compare() {
    if(!pinned)return;
    const key=(r,i)=>String(r.id??`${r.record_type||r.page||'record'}:${i}`),before=new Map(pinned.rows.map((r,i)=>[key(r,i),r])),after=new Map(rows.map((r,i)=>[key(r,i),r]));
    const changes=[];for(const [id,r] of after){if(!before.has(id))changes.push({id,change:'新增',after:r});else if(pretty(before.get(id))!==pretty(r))changes.push({id,change:'变化',before:before.get(id),after:r});}
    for(const [id,r] of before)if(!after.has(id))changes.push({id,change:'当前页未出现',before:r});
    el('comparison').hidden=false;el('comparison').innerHTML=`固定：${esc(pinned.label)}<br>当前：${esc(el('route').value+' / '+el('run').value+' / '+stage)}<br>仅比较所显示这一页记录：${changes.length}项差异。不同筛选或翻页会影响此对照。${panel('查看完整差异',changes)}`;
  }
  function launchActions() {const basic=el('route').value==='basic',valid=!busy&&(basic||!!el('inputFile').value&&!!el('model').value);el('prepare').disabled=!valid;el('start').disabled=!valid||!basic&&!el('allow').checked;for(const id of ['route','inputFile','model','maxCalls','concurrency','ocrPages','recordInputs','allow','basicOcr','basicDevice','basicReuse'])el(id).disabled=busy;for(const b of document.querySelectorAll('[data-route]'))b.disabled=busy;el('basicOcr').disabled=busy||el('basicReuse').checked;el('basicDevice').disabled=busy||el('basicReuse').checked;}
  function endpoint() {const p=profiles.find(p=>p.id===el('model').value);el('endpoint').textContent=p?`${p.model} · ${p.endpoint}`:'暂无可用模型';launchActions();}
  async function loadLaunch() {
    const results=await Promise.allSettled([getJson('/api/recognition/profiles'),getJson('/api/recognition/documents')]);
    profiles=results[0].status==='fulfilled'?results[0].value.profiles||[]:[];files=results[1].status==='fulfilled'?results[1].value.documents||[]:[];
    el('model').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');
    el('inputFile').innerHTML=files.map(f=>`<option value="${esc(f.path)}">${esc(f.path)}</option>`).join('');methodUI({chooseModel:true});
  }
  async function pollJob() {
    try{const job=await getJson('/api/pipeline/jobs/latest');busy=['queued','running'].includes(job.status);el('job').textContent=job.status==='none'?'':`${job.progress||0}% · ${job.message||labels[job.status]||job.status}`;if(job.debug_history_ids?.length){el('job').textContent+=` · 已保存${job.debug_history_ids.length}条独立历史`;window.dispatchEvent(new Event('debughistorychange'));}if(job.debug_history_errors?.length)error('识别任务结束，但历史快照保存失败：'+job.debug_history_errors.join('；'));launchActions();if(busy)pollTimer=setTimeout(pollJob,1500);else if(el('run').value==='live')await loadCatalog();}
    catch(e){busy=false;launchActions();error(`任务状态读取失败：${e.message}`);}
  }
  async function start(prepared) {
    if(busy)return;error();busy=true;launchActions();
    try{
      const integer=(id,min,max)=>{const n=Number(el(id).value);if(!Number.isInteger(n)||n<min||n>max)throw Error('调用上限、并发或OCR页数超出允许范围');return n;};
      const route=el('route').value;let body;
      if(route==='basic')body={recognizer:route,ocr_pages:integer('basicOcr',0,1000),ocr_device:el('basicDevice').value,reuse_parsed:el('basicReuse').checked};
      else{const settings={max_calls:integer('maxCalls',1,10000),model_concurrency:integer('concurrency',1,16),record_model_inputs:el('recordInputs').checked};
        if(route==='knowledge')settings.ocr_pages=integer('ocrPages',0,1000);
        body={recognizer:route,input_pdfs:[el('inputFile').value],model_profile:el('model').value,prepare_only:prepared,allow_external:!prepared&&el('allow').checked,[route==='knowledge'?'knowledge_settings':'visual_settings']:settings};}
      await requestJson('/api/pipeline/jobs',{method:'POST',body});
      el('run').value='live';clearTimeout(pollTimer);await pollJob();
    }catch(e){busy=false;launchActions();if(e.status===409){toast('已有任务运行中，正在跟踪该任务');await pollJob();}else error(e.message);}
  }
  el('stages').onclick=e=>{const b=e.target.closest('[data-stage]');if(b){stage=b.dataset.stage;offset=0;renderStages();rememberContext();loadRecords();}};
  el('records').onclick=e=>{
    if(!recordsReady())return;
    const b=e.target.closest('button');
    if(!b){const summary=e.target.closest('.record-card > summary');if(summary)selectRecord(Number(summary.parentElement.dataset.recordIndex));return;}
    if(b.dataset.call)showCall(b.dataset.call);
    if(b.dataset.locate!==undefined)selectRecord(Number(b.dataset.locate),{force:true,open:true});
    if(b.dataset.copy!==undefined)copy(rows[Number(b.dataset.copy)]);if(b.dataset.download!==undefined)download('recognition-record.json',rows[Number(b.dataset.download)]);
  };
  el('route').onchange=changeRoute;
  el('methodCards').onclick=e=>{const b=e.target.closest('[data-route]');if(b&&!busy&&el('route').value!==b.dataset.route){el('route').value=b.dataset.route;changeRoute();}};
  el('run').onchange=()=>{offset=0;loadCatalog({reset:true});};el('document').onchange=()=>{offset=0;clearSource();updateSourcePager();el('openPdf').href=sourceLink();el('openPdf').hidden=false;rememberContext();loadRecords();};
  el('refresh').onclick=()=>loadCatalog();el('filter').onclick=()=>{offset=0;loadRecords();};el('search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{offset=0;loadRecords();},300);};el('page').onchange=el('failures').onchange=()=>{offset=0;loadRecords();};
  function flipRecords(delta){if(recordsBusy||catalogBusy)return;const next=Math.max(0,Math.min(Math.floor(Math.max(0,total-1)/25)*25,offset+delta));if(next===offset)return;offset=next;loadRecords({reveal:true});}
  el('prev').onclick=()=>flipRecords(-25);el('next').onclick=()=>flipRecords(25);
  el('showPage').onclick=()=>showPage(Number(el('previewPage').value));el('downloadStage').onclick=downloadStage;
  el('sourcePrev').onclick=el('pdfPrev').onclick=()=>showPage(currentPreviewPage-1,null,{mode:'manual'});
  el('sourceNext').onclick=el('pdfNext').onclick=()=>showPage(currentPreviewPage+1,null,{mode:'manual'});
  el('pdfGo').onclick=()=>showPage(Number(el('dialogPage').value));
  for(const id of ['dialogPage','previewPage'])el(id).onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();showPage(Number(el(id).value));}};
  el('closePage').onclick=()=>el('pageDialog').close();el('sourceImage').onclick=()=>showPage(currentPreviewPage,previewBox,{mode:previewMode});
  el('followRecord').onchange=()=>{if(el('followRecord').checked&&selectedRecord)selectRecord(selectedRecord.index,{force:true});else{previewMode='manual';updatePreviewBinding();}};
  el('returnToRecord').onclick=()=>selectedRecord&&selectRecord(selectedRecord.index,{force:true});
  el('pdfReturnToRecord').onclick=()=>selectedRecord&&selectRecord(selectedRecord.index,{force:true,open:true});
  el('pageZoom').onchange=resizePage;window.addEventListener('resize',()=>{if(el('pageDialog').open)resizePage();});
  el('pin').onclick=()=>{pinned={rows:structuredClone(rows),label:`${el('route').value} / ${el('run').value} / ${stage} / 第${offset+1}项起`};el('compare').disabled=false;toast('已固定当前页记录；刷新或换记录后可比较');};el('compare').onclick=compare;
  el('autoRefresh').onchange=()=>{clearInterval(refreshTimer);if(el('autoRefresh').checked)refreshTimer=setInterval(()=>{if(!el('callDialog').open&&!el('pageDialog').open&&!recordsBusy)loadRecords();},3000);};
  el('closeCall').onclick=()=>el('callDialog').close();el('callDialog').addEventListener('close',()=>{callGeneration++;});el('downloadCall').onclick=()=>activeCall&&download(`model-exchange-${activeCall.cache_key}.json`,activeCall);el('copyCall').onclick=()=>activeCall&&copy(activeCall);
  el('prepare').onclick=()=>start(true);el('start').onclick=()=>start(false);el('model').onchange=()=>{el('allow').checked=false;endpoint();};el('allow').onchange=launchActions;
  el('basicReuse').onchange=launchActions;
  window.addEventListener('beforeunload',()=>{clearTimeout(pollTimer);clearInterval(refreshTimer);pageController?.abort();if(pageBlobUrl)URL.revokeObjectURL(pageBlobUrl);});
  window.SlopeKGDebug={context:()=>({route:el('route').value,run_id:el('run').value||'live',document:recordsReady()?el('document').value:''}),profiles:()=>profiles,
    toast,error,panel,describeError,refresh:loadCatalog,rememberContext,routeLabel:r=>routeLabels[r]||r,initialView:query.get('tab'),
    openSource:async(source,{targetStage='input',q='',page=0}={})=>{
      el('route').value=source.route;el('allow').checked=false;methodUI({chooseModel:true});el('run').innerHTML='';el('run').add(new Option(source.run_id,source.run_id,true,true));stage=targetStage;offset=0;el('search').value=q;el('page').value=String(page);el('failures').checked=false;
      await loadCatalog({reset:true});if(source.document&&catalog?.documents.some(d=>d.id===source.document)){el('document').value=source.document;await loadRecords();}
      rememberContext();
    }};
  methodUI();loadCatalog();loadLaunch();
})();
