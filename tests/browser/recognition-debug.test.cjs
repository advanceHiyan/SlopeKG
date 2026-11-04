const assert=require('node:assert/strict');
const {readFile,mkdir,readdir}=require('node:fs/promises');
const path=require('node:path');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});});
after(async()=>{await browser?.close();});
const key='a'.repeat(64),full='完整输入\n<script>unsafe()</script>\n'+'长文本'.repeat(10000);
const stages=[['input','输入与参数'],['pages','逐页覆盖'],['ocr','OCR识别原文'],['units','原文与表格'],['chunks','分块与归属'],['entities','对象目录'],['calls','大模型输入输出'],['gates','校验与拒绝'],['facts','最终候选'],['errors','错误与缺口']].map(([id,label])=>({id,label,help:'查看实际保存记录'}));
async function workspace(t){
  const page=await browser.newPage({viewport:{width:1600,height:1050}}),errors=[],requests=[];
  const state={fail:false,stale:false,changed:false,records:[],reviews:[],recordRows:null,pageFailure:false,slowPage:0};
  const dataset={id:'ds_'+'b'.repeat(24),name:'匿名开放数据集 <img src=x>',dataset_hash:'dataset-v1',note:'仅开放开发点',source:{route:'knowledge',run_id:'live',document:'doc',source_name:'anonymous.pdf',input_pages:2},points:[{id:'p1',label:'高度12m',pages:[1],expected:{value:'12'},checks:[{field:'value',op:'equals',value:'12'}]},{id:'p2',label:'另一个检查点',pages:[2],expected:{value:'9'},checks:[]}]};
  const details=new Map();
  function createRecord(body,snapshot=false){
    const id='h_'+String(state.records.length+1).padStart(24,'0'),points=snapshot?[]:dataset.points.filter(p=>body.point_ids.includes(p.id));
    const record={id,label:body.label,route:'knowledge',mode:snapshot?'snapshot':body.mode,status:'completed',created_at:'2026-11-02T09:00:00Z',snapshot_ready:true,document_id:'doc',source_name:'anonymous.pdf',selected_points:points.length,progress:100,processing_status:'partial'};
    const rows=points.map(p=>({point_id:p.id,label:p.label,rule_status:p.id==='p1'?'matched':'needs_review',original_pages:p.pages,input_pages:p.pages,candidate_fact_ids:['f1'],pipeline_admitted_fact_ids:[],note:'显式规则与语义复核分开',historical_review_bound:false}));
    const detail={record,definition:{dataset,dataset_hash:'dataset-v1',points},results:{rows,summary:{selected:rows.length,rule_matched:rows.filter(p=>p.rule_status==='matched').length,rule_unmatched:0,needs_review:rows.filter(p=>p.rule_status==='needs_review').length,execution_unavailable:0},metric_note:'不是准确率'},reviews:[]};
    state.records.unshift(record);details.set(id,detail);return record;
  }
  page.on('pageerror',e=>errors.push(e.message));
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url()),p=url.pathname;
    if(p.startsWith('/api/')&&state.fail)return route.fulfill({status:503,json:{error:'temporarily unavailable'}});
    if(p.startsWith('/api/recognition-debug')&&state.stale)return route.fulfill({status:404,json:{error:'not found'}});
    if(p==='/api/recognition-debug/datasets'){
      if(req.method()==='POST'){const body=req.postDataJSON();requests.push(body);return route.fulfill({status:201,json:{...dataset,name:body.name}});}
      return route.fulfill({json:{datasets:[dataset]}});
    }
    if(p==='/api/recognition-debug/tests'){const body=req.postDataJSON();requests.push(body);return route.fulfill({status:202,json:createRecord(body)});}
    if(p==='/api/recognition-debug/history'){
      if(req.method()==='POST'){const body=req.postDataJSON();requests.push(body);return route.fulfill({status:201,json:createRecord(body,true)});}
      return route.fulfill({json:{count:state.records.length,records:state.records,offset:0,limit:15}});
    }
    if(p==='/api/recognition-debug/history/record'){if(state.detailFail)return route.fulfill({status:503,json:{error:'detail unavailable'}});return route.fulfill({json:details.get(url.searchParams.get('id'))});}
    if(p==='/api/recognition-debug/history/review'){
      const body=req.postDataJSON();requests.push(body);if(state.slowReview)await new Promise(resolve=>setTimeout(resolve,350));const data=details.get(body.id);data.record.label=body.label;data.record.tags=body.tags;
      data.reviews.push({at:'2026-11-02T10:00:00Z',point_id:body.point_id,verdict:body.verdict,note:body.note});return route.fulfill({json:data});
    }
    if(p==='/api/recognition-debug/history/compare')return route.fulfill({json:state.snapshotComparison?{same_dataset_version:false,has_test_results:false,changed_points:0,note:'过程快照未定义测试点',rows:[]}:{same_dataset_version:true,has_test_results:true,changed_points:1,note:'定义版本一致',rows:[{point_id:'p1',changed:true}]}});
    if(p==='/api/recognition-debug/history/export')return route.fulfill({contentType:'application/zip',headers:{'Content-Disposition':'attachment; filename="test-record.zip"'},body:Buffer.from('ZIP test fixture')});
    if(p==='/api/recognition-debug/catalog')return route.fulfill({json:{route:url.searchParams.get('route'),run_id:'live',runs:[{id:'live',label:'当前生产记录'},{id:'open-g359',label:'开放施工图'},...state.records.map(r=>({id:'history-'+r.id,label:r.label}))],documents:[{id:'doc',name:'匿名资料.pdf',pages:3},{id:'doc2',name:'another-document.pdf',pages:3}],stages:url.searchParams.get('route')==='basic'?stages.filter(s=>!['calls','chunks','entities','gates'].includes(s.id)):stages,status:'partial',implementation_status:'current'}});
    if(p==='/api/recognition-debug'){
      requests.push(url.searchParams.toString());
      if(state.slowRecordsDoc===url.searchParams.get('document'))await new Promise(resolve=>setTimeout(resolve,350));
      const customRows=state.recordRowsByDocument?.[url.searchParams.get('document')]||state.recordRows;
      if(customRows){const start=Number(url.searchParams.get('offset'));return route.fulfill({json:{rows:customRows.slice(start,start+25),count:customRows.length,offset:start,note:'匿名分页记录',snapshot:'fixed'}});}
      const stage=url.searchParams.get('stage');
      const rows=stage==='calls'?[{id:key,stage:'knowledge_synthesize',status:'failed',prompt_chars:full.length,input_saved:true,raw_exchange_saved:true,pages:[2]}]:stage==='ocr'?[{id:'ocr1',source:'ocr',page:2,bbox:[.1,.2,.6,.3],text:state.changed?'OCR changed':'OCR原文 <img src=x onerror=unsafe()>',status:'pending'}]:[{id:'input',page:1,text:'输入与设置',status:'prepared'}];
      return route.fulfill({json:{rows,count:rows.length,offset:0,note:'完整记录正文不截断',snapshot:'fixed'}});
    }
    if(p==='/api/recognition-debug/call'&&state.requestOnly)return route.fulfill({json:{cache_key:key,input:{system:'fixture',prompt:'fixture'},cache:{status:'failed',error:'No response'},exchange:{attempts:[{request:{model:'fixture'},response:null}]},notes:['已保存请求正文，但未收到或未保存供应商回复。']}});
    if(p==='/api/recognition-debug/call')return route.fulfill({json:{cache_key:key,input:{system:'完整system',prompt:full,model:{model:'fixture'}},cache:{status:'failed',error:'invalid JSON',invalid_answer:{bad:'candidate'}},exchange:{attempts:[{request:{model:'fixture',max_tokens:393216,messages:[{role:'user',content:full}]},response:{choices:[{finish_reason:'stop',message:{content:'原始回复 {bad JSON <script>unsafe()</script>'}}]}}]},notes:[]}});
    if(p==='/api/recognition-debug/page.png'){
      requests.push('PDF:'+url.searchParams.get('page'));
      (state.imageRequests??=[]).push(Object.fromEntries(url.searchParams));
      if(state.pageFailure)return route.fulfill({status:400,json:{error:'页码超出原件范围（匿名测试）'}});
      if(Number(url.searchParams.get('page'))===state.slowPage)await new Promise(resolve=>setTimeout(resolve,350));
      return route.fulfill({contentType:'image/png',body:Buffer.from(url.searchParams.get('document')==='doc2'?'iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAADUlEQVR4nGNgYPgPRAAFAgH/wSuWnwAAAABJRU5ErkJggg==':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=','base64')});
    }
    if(p==='/api/recognition/profiles')return route.fulfill({json:{profiles:['fixture','deepseek-knowledge','deepseek'].map(id=>({id,name:'匿名测试模型',model:'fixture',endpoint:'https://example.invalid'}))}});
    if(p==='/api/recognition/documents')return route.fulfill({json:{documents:[{path:'anonymous.pdf'}]}});
    if(p==='/api/pipeline/jobs'&&req.method()==='POST'){requests.push(req.postDataJSON());return route.fulfill({status:202,json:{id:'job'}});}
    if(p==='/api/pipeline/jobs/latest')return route.fulfill({json:{status:'completed',progress:100,message:'本地准备完成'}});
    if(p.startsWith('/web/')){const f=path.basename(p);return route.fulfill({contentType:f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':'text/html',body:f.endsWith('.js')&&!['api.js','recognition-debug.js','recognition-debug-tests.js'].includes(f)?'':await readFile(path.join(__dirname,'../../web',f))});}
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto('http://slopekg.test/web/recognition-debug.html');await page.locator('.record-card').waitFor();
  return {page,requests,state};
}
test('OCR stage, source locations, search and safe rendering work on desktop and mobile',async t=>{
  const {page,requests}=await workspace(t);
  await page.locator('[data-stage=ocr]').click();await page.locator('.record-preview').getByText('OCR原文 <img',{exact:false}).waitFor();
  assert.equal(await page.locator('#records img,#records script').count(),0);
  await page.locator('[data-locate]').click();await page.locator('#sourceImage').waitFor();
  assert.equal(await page.locator('#sourceOverlay rect').getAttribute('x'),'0.1');
  await page.locator('#closePage').click();
  await page.locator('#search').fill('OCR');await page.waitForFunction(()=>document.querySelector('#recordNote').textContent.includes('不截断'));
  await page.locator('#page').fill('2');await page.locator('#filter').click();
  await page.waitForTimeout(400);assert(requests.some(r=>typeof r==='string'&&r.includes('page=2')&&r.includes('q=OCR')));
  const out=path.join(__dirname,'../../outputs/20261102_识别调试工作台验证');await mkdir(out,{recursive:true});
  await page.screenshot({path:path.join(out,'调试页_桌面.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'调试页_手机.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
});
test('full model prompt, exact request and invalid raw reply are inspectable and downloadable',async t=>{
  const {page}=await workspace(t);await page.locator('[data-stage=calls]').click();await page.locator('[data-call]').click();
  await page.locator('#callBody').getByText('模型原始回复正文',{exact:false}).waitFor();
  const contents=await page.locator('#callBody pre').allTextContents();assert(contents.includes(full));
  assert(contents.some(s=>s.includes('393216')));assert(contents.some(s=>s.includes('原始回复 {bad JSON <script>')));
  assert.equal(await page.locator('#callBody script,#callBody img').count(),0);
  const download=page.waitForEvent('download');await page.locator('#downloadCall').click();assert.match((await download).suggestedFilename(),/model-exchange/);
  const out=path.join(__dirname,'../../outputs/20261102_识别调试工作台验证');await page.screenshot({path:path.join(out,'模型完整输入输出.png')});
  await page.locator('#closeCall').click();assert.equal(await page.locator('#callDialog').isVisible(),false);
});
test('new task requires external consent and carries complete input capture flag',async t=>{
  const {page,requests}=await workspace(t);await page.locator('#launchPanel summary').click();
  assert.equal(await page.locator('#recordInputs').isChecked(),true);assert.equal(await page.locator('#start').isDisabled(),true);
  await page.locator('#prepare').click();await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  let posts=requests.filter(r=>typeof r==='object');assert.equal(posts[0].allow_external,false);assert.equal(posts[0].prepare_only,true);assert.equal(posts[0].knowledge_settings.record_model_inputs,true);
  await page.locator('#allow').check();await page.locator('#start').click();await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  posts=requests.filter(r=>typeof r==='object');assert.equal(posts[1].allow_external,true);
  await page.locator('#route').selectOption('visual');await page.locator('#allow').check();await page.locator('#start').click();await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  posts=requests.filter(r=>typeof r==='object');assert.equal(posts[2].visual_settings.record_model_inputs,true);assert.equal('ocr_pages' in posts[2].visual_settings,false);
});
test('pinned comparison detects changed records and failed refresh clears stale contents',async t=>{
  const {page,state}=await workspace(t);await page.locator('[data-stage=ocr]').click();await page.locator('[data-locate]').waitFor();
  await page.locator('#pin').click();state.changed=true;await page.locator('#filter').click();await page.getByText('OCR changed',{exact:true}).waitFor();await page.locator('#compare').click();
  assert.match(await page.locator('#comparison').textContent(),/1项差异/);
  state.fail=true;await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#runStatus').textContent==='暂不可读');
  assert.doesNotMatch(await page.locator('#records').textContent(),/OCR changed/);assert.equal(await page.locator('#downloadStage').isDisabled(),true);
});

test('selected dataset checkpoints create a durable record with a separate result view',async t=>{
  const {page,requests}=await workspace(t);await page.locator('[data-view=tests]').click();await page.locator('.point-check').first().waitFor();
  assert.equal(await page.locator('#testPoints img,#testPoints script').count(),0);
  await page.locator('#clearPoints').click();await page.locator('.point-check[value=p1]').check();await page.locator('#testLabel').fill('只测高度');
  await page.locator('#runTest').click();await page.locator('#historyDialog').waitFor();await page.waitForFunction(()=>document.querySelector('#historyTitle').textContent==='只测高度');
  const body=requests.find(r=>typeof r==='object'&&r.dataset_id);assert.deepEqual(body.point_ids,['p1']);assert.equal(body.mode,'saved');assert.equal('expected' in body,false);
  assert.match(await page.locator('#historyResults').textContent(),/所选1点/);assert.match(await page.locator('#historyResults').textContent(),/显式规则匹配/);
  assert.equal(await page.locator('.test-result').count(),1);
  const download=page.waitForEvent('download');await page.locator('#exportHistory').click();assert.match((await download).suggestedFilename(),/\.zip$/);
  await page.screenshot({path:path.join(__dirname,'../../outputs/20261102_测试点与历史记录验证/测试点结果_桌面.png')});
  await page.locator('#reuseTest').click();await page.waitForFunction(()=>document.querySelector('#testSource').value==='current');
  assert.equal(await page.locator('#testMode').inputValue(),'saved');assert.equal(await page.locator('.point-check:checked').count(),1);
});

test('history keeps local review notes and can reopen one full process independently',async t=>{
  const {page,requests}=await workspace(t);await page.locator('#snapshotLabel').fill('归属问题快照');await page.locator('#saveSnapshot').click();
  await page.waitForFunction(()=>document.querySelector('#historyTitle').textContent==='归属问题快照');
  await page.locator('#historyRename').fill('归属问题 · 已复核');await page.locator('#historyTags').fill('归属, 待改');await page.locator('#reviewNote').fill('来源正确，重组未返回');await page.locator('#saveReview').click();
  await page.getByText('来源正确，重组未返回',{exact:true}).waitFor();assert(requests.some(r=>typeof r==='object'&&r.note==='来源正确，重组未返回'));
  await page.locator('#openHistoryProcess').click();await page.waitForFunction(()=>document.querySelector('#run').value.startsWith('history-h_'));
  await page.waitForURL(/run=history-h_/);assert.equal(await page.locator('#processView').isVisible(),true);
  await page.locator('[data-view=history]').click();await page.locator('[data-detail]').waitFor();assert.match(await page.locator('#historyList').textContent(),/归属问题 · 已复核/);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(__dirname,'../../outputs/20261102_测试点与历史记录验证/历史记录_手机.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
});

test('fresh dataset recognition requires consent and sends no expected answers in the request',async t=>{
  const {page,requests}=await workspace(t);await page.locator('[data-view=tests]').click();await page.locator('.point-check').first().waitFor();await page.locator('#testMode').selectOption('recognize');
  assert.equal(await page.locator('#runTest').isDisabled(),true);await page.locator('#testAllowExternal').check();await page.locator('#runTest').click();
  await page.waitForFunction(()=>document.querySelector('#historyTitle').textContent.includes('匿名开放'));
  const body=requests.find(r=>typeof r==='object'&&r.mode==='recognize');assert.equal(body.allow_external,true);assert.equal(body.scope,'whole_input');assert.deepEqual(body.point_ids,['p1','p2']);
  assert.equal('expected' in body,false);assert.deepEqual(body.settings,{max_calls:64,model_concurrency:6,ocr_pages:0});
});

test('two saved tests can be selected for checkpoint comparison',async t=>{
  const {page}=await workspace(t);await page.locator('[data-view=tests]').click();await page.locator('.point-check').first().waitFor();
  await page.locator('#testLabel').fill('改前');await page.locator('#runTest').click();await page.waitForFunction(()=>document.querySelector('#historyTitle').textContent==='改前');await page.locator('#closeHistory').click();
  await page.locator('#testLabel').fill('改后');await page.locator('#runTest').click();await page.waitForFunction(()=>document.querySelector('#historyTitle').textContent==='改后');await page.locator('#closeHistory').click();
  await page.locator('[data-view=history]').click();await page.locator('.history-card').first().waitFor();
  await page.locator('.history-card').nth(1).locator('[data-compare-side=left]').click();await page.locator('.history-card').first().locator('[data-compare-side=right]').click();await page.locator('#compareHistory').click();
  await page.getByText('数据集版本一致；1个测试点记录有变化。',{exact:true}).waitFor();
});

test('old backend 404 identifies a service-version problem and recovers after refresh',async t=>{
  const {page,state}=await workspace(t);state.stale=true;await page.locator('#refresh').click();
  await page.getByText(/后端未加载识别调试接口/).waitFor();assert.match(await page.locator('#error').textContent(),/SlopeKG桌面入口/);
  state.stale=false;await page.locator('#refresh').click();await page.locator('.record-card').waitFor();
  assert.equal(await page.locator('#error').isHidden(),true);
});


test('three methods switch steps and launch scope without mixing local and model options',async t=>{
  const {page,requests}=await workspace(t);
  await page.locator('#launchPanel summary').click();
  await page.locator('[data-route=visual]').click();
  assert.equal(await page.locator('#model').inputValue(),'deepseek');
  assert.equal(await page.locator('#ocrLabel').isVisible(),false);
  assert.match(await page.locator('#allowText').textContent(),/页面图像/);
  await page.locator('[data-route=basic]').click();
  await page.waitForFunction(()=>!document.querySelector('[data-stage=calls]'));
  assert.equal(await page.locator('#modelLaunch').isVisible(),false);
  assert.equal(await page.locator('#basicLaunch').isVisible(),true);
  assert.equal(await page.locator('#start').isDisabled(),false);
  await page.locator('#start').click();
  await page.waitForFunction(()=>!document.querySelector('#start').disabled);
  const body=requests.filter(r=>typeof r==='object').at(-1);
  assert.deepEqual(body,{recognizer:'basic',ocr_pages:0,ocr_device:'auto',reuse_parsed:true});
  await page.locator('[data-route=knowledge]').click();
  assert.equal(await page.locator('#model').inputValue(),'deepseek-knowledge');
  assert.equal(await page.locator('#allow').isChecked(),false);
  assert.equal(await page.locator('#ocrLabel').isVisible(),true);
  assert.match(await page.locator('#allowText').textContent(),/不发送原页图像/);
});

test('leaving, browser back and reentering debug restore method, source and step',async t=>{
  const {page}=await workspace(t);
  await page.locator('#route').selectOption('visual');
  await page.locator('#run').selectOption('open-g359');
  await page.locator('[data-stage=ocr]').click();
  await page.locator('.record-card').waitFor();
  await page.locator('.site-nav a[href="./graph.html"]').click();
  assert.equal(await page.locator('.site-nav .nav-debug').count(),1);
  await page.goBack();await page.locator('[data-stage=ocr][aria-current=step]').waitFor();
  assert.equal(await page.locator('#route').inputValue(),'visual');
  assert.equal(await page.locator('#run').inputValue(),'open-g359');
  await page.locator('.site-nav a[href="./graph.html"]').click();
  await page.locator('.nav-debug').click();
  await page.locator('[data-stage=ocr][aria-current=step]').waitFor();
  assert.equal(await page.locator('#route').inputValue(),'visual');
  assert.equal(await page.locator('#run').inputValue(),'open-g359');
});

test('every main page has one visible debug entrance in the same position, including after scroll on mobile',async t=>{
  const page=await browser.newPage();t.after(()=>page.close());
  const root=path.join(__dirname,'../../web');let count=0;
  await page.route('**/*',async route=>{
    const f=path.basename(new URL(route.request().url()).pathname);
    return route.fulfill({contentType:f.endsWith('.css')?'text/css':f.endsWith('.js')?'text/javascript':'text/html',body:f.endsWith('.js')?'':await readFile(path.join(root,f))});
  });
  for(const f of await readdir(root)){
    if(!f.endsWith('.html'))continue;
    if(!(await readFile(path.join(root,f),'utf8')).includes('<nav class="site-nav"'))continue;
    await page.goto('http://slopekg.test/web/'+f);count++;
    assert.equal(await page.locator('.site-nav .nav-debug').count(),1,f);
    assert.equal(await page.locator('.site-nav a').first().textContent(),'识别调试',f);
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));
    const box=await page.locator('.nav-debug').boundingBox();
    assert(box.x>=0&&box.x+box.width<=390&&box.y>=0&&box.y+box.height<=844,f);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,f);
    await page.setViewportSize({width:1600,height:1050});
  }
  assert(count>=15,"all existing main pages must be checked");
});


test('View opens a decoded large PDF page with real page navigation, zoom and bounds',async t=>{
  const {page,requests}=await workspace(t);
  await page.locator('#showPage').click();
  await page.waitForFunction(()=>document.querySelector('#pdfStatus').textContent.startsWith('已加载PDF物理第1页'));
  assert.equal(await page.locator('#pageDialog').isVisible(),true);
  assert.equal(await page.locator('#pageDialogImage').evaluate(e=>e.complete&&e.naturalWidth>0),true);
  assert.equal(await page.locator('#pdfPrev').isDisabled(),true);
  await page.locator('#pdfNext').click();
  await page.waitForFunction(()=>document.querySelector('#pdfStatus').textContent.startsWith('已加载PDF物理第2页'));
  assert.equal(await page.locator('#previewPage').inputValue(),'2');
  assert.match(await page.locator('#pageOriginalPdf').getAttribute('href'),/#page=2$/);
  await page.locator('#pageZoom').selectOption('200');
  assert.equal(await page.locator('#pageDialogCanvas').evaluate(e=>e.style.width),'2px');
  await page.locator('#pageZoom').selectOption('width');
  await page.locator('#dialogPage').fill('3');await page.locator('#pdfGo').click();
  await page.waitForFunction(()=>document.querySelector('#pdfStatus').textContent.startsWith('已加载PDF物理第3页'));
  assert.equal(await page.locator('#pdfNext').isDisabled(),true);
  assert(requests.includes('PDF:1')&&requests.includes('PDF:2')&&requests.includes('PDF:3'));
  const download=page.waitForEvent('download');await page.locator('#downloadPage').click();assert.match((await download).suggestedFilename(),/第3页/);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  const box=await page.locator('#pageDialog').boundingBox();assert(box.x>=0&&box.x+box.width<=391);
});

test('PDF errors stay visible and rapid page changes cannot restore a stale image',async t=>{
  const {page,requests,state}=await workspace(t);
  state.pageFailure=true;await page.locator('#showPage').click();
  await page.locator('#pdfError').waitFor();assert.match(await page.locator('#pdfError').textContent(),/页码超出原件范围/);
  assert.equal(await page.locator('#pageDialogCanvas').isVisible(),false);
  state.pageFailure=false;await page.locator('#pdfGo').click();
  await page.waitForFunction(()=>document.querySelector('#pdfStatus').textContent.startsWith('已加载PDF物理第1页'));
  state.slowPage=2;await page.locator('#pdfNext').click();await page.locator('#pdfNext').click();
  await page.waitForFunction(()=>document.querySelector('#pdfStatus').textContent.startsWith('已加载PDF物理第3页'));
  await page.waitForTimeout(450);
  assert.equal(await page.locator('#previewPage').inputValue(),'3');
  assert.equal(await page.locator('#sourceOverlay rect').count(),0);
  assert.match(await page.locator('#pageDialogTitle').textContent(),/第3页/);
  const count=requests.filter(r=>typeof r==='string'&&r.startsWith('PDF:')).length;
  await page.locator('#dialogPage').fill('0');await page.locator('#pdfGo').click();
  assert.match(await page.locator('#pdfError').textContent(),/1～3/);
  assert.equal(requests.filter(r=>typeof r==='string'&&r.startsWith('PDF:')).length,count);
});

test('record batch navigation changes actual records and a single batch is explained separately from PDF pages',async t=>{
  const {page,state,requests}=await workspace(t);
  state.recordRows=Array.from({length:53},(_,i)=>({id:'record-'+(i+1),text:'匿名记录'+(i+1)}));
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#pageInfo').textContent.includes('第1/3批'));
  const imageCountBeforeBatches=requests.filter(r=>typeof r==='string'&&r.startsWith('PDF:')).length;
  await page.locator('#next').click();await page.waitForFunction(()=>document.querySelector('#pageInfo').textContent.includes('第2/3批'));
  assert.match(await page.locator('.record-card > summary').first().textContent(),/record-26/);
  await page.reload();await page.waitForFunction(()=>document.querySelector('#pageInfo').textContent.includes('第2/3批'));
  await page.locator('#next').click();await page.waitForFunction(()=>document.querySelector('#pageInfo').textContent.includes('第3/3批'));
  assert.equal(await page.locator('.record-card').count(),3);assert.equal(await page.locator('#next').isDisabled(),true);
  await page.locator('#prev').click();await page.waitForFunction(()=>document.querySelector('#pageInfo').textContent.includes('第2/3批'));
  assert.equal(requests.filter(r=>typeof r==='string'&&r.startsWith('PDF:')).length,imageCountBeforeBatches);
  state.recordRows=state.recordRows.slice(0,23);await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#pageInfo').textContent.includes('第1/1批'));
  assert.equal(await page.locator('#prev').isDisabled(),true);assert.equal(await page.locator('#next').isDisabled(),true);
  assert.match(await page.locator('#next').getAttribute('title'),/当前只有一批记录/);
  assert.equal(await page.locator('#sourceNext').isDisabled(),false);
});


test('selected record automatically binds its physical page; manual browsing warns and can return',async t=>{
  const {page,state}=await workspace(t);
  state.recordRows=[{id:'ocr-page-two',page:2,text:'page two OCR',bbox:[.1,.2,.6,.3]},{id:'ocr-page-three',page:3,text:'page three OCR'}];
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#sourceBinding').textContent.includes('ocr-page-two')&&document.querySelector('#sourceBinding').textContent.includes('页码一致'));
  assert.equal(await page.locator('#previewPage').inputValue(),'2');
  assert.equal(await page.locator('#pageDialog').isVisible(),false);
  assert.equal(await page.locator('.selected-record').getAttribute('data-record-index'),'0');
  await page.locator('[data-record-index="1"] > summary').click();
  await page.waitForFunction(()=>document.querySelector('#sourceBinding').textContent.includes('ocr-page-three')&&document.querySelector('#sourceBinding').textContent.includes('页码一致'));
  assert.equal(await page.locator('#previewPage').inputValue(),'3');
  await page.locator('#previewPage').fill('1');await page.locator('#showPage').click();
  await page.waitForFunction(()=>document.querySelector('#pdfStatus').textContent.startsWith('已加载PDF物理第1页'));
  assert.equal(await page.locator('#followRecord').isChecked(),false);
  assert.match(await page.locator('#sourceBinding').textContent(),/手动浏览第1页.*第3页.*不同页/);
  assert.equal(await page.locator('#sourceBinding').evaluate(e=>e.classList.contains('source-mismatch')),true);
  await page.locator('#pdfReturnToRecord').click();
  await page.waitForFunction(()=>document.querySelector('#pdfBinding').textContent.includes('第3页（页码一致）'));
  assert.equal(await page.locator('#followRecord').isChecked(),true);
  assert.equal(await page.locator('#dialogPage').inputValue(),'3');
  assert.equal(await page.locator('#sourceBinding').evaluate(e=>e.classList.contains('source-mismatch')),false);
});

test('changing document while a preview is pending cannot commit the previous document image',async t=>{
  const {page,state}=await workspace(t);
  state.recordRowsByDocument={doc:[{id:'record-A',page:2,text:'from A'}],doc2:[{id:'record-B',page:3,text:'from B'}]};
  state.slowPage=2;await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#sourceStatus').textContent.includes('正在加载PDF物理第2页'));
  await page.locator('#document').selectOption('doc2');
  await page.waitForFunction(()=>document.querySelector('#sourceBinding').textContent.includes('record-B')&&document.querySelector('#sourceBinding').textContent.includes('页码一致'));
  await page.waitForTimeout(450);
  assert.equal(await page.locator('#sourceImage').evaluate(e=>e.naturalWidth),2);
  assert.equal(await page.locator('#previewPage').inputValue(),'3');
  assert.equal(await page.locator('#sourceDocumentName').textContent(),'another-document.pdf');
  assert.match(await page.locator('#sourceBinding').textContent(),/record-B.*第3页/);
  assert(state.imageRequests.some(r=>r.document==='doc2'&&r.page==='3'));
});


test('audit: old record actions disappear immediately while a different document is loading',async t=>{
  const {page,state}=await workspace(t);
  state.recordRowsByDocument={doc:[{id:'record-A',page:2,text:'A evidence'}],doc2:[{id:'record-B',page:3,text:'B evidence'}]};
  await page.locator('#refresh').click();await page.getByText('A evidence',{exact:true}).waitFor();
  state.slowRecordsDoc='doc2';await page.locator('#document').selectOption('doc2');
  assert.equal(await page.locator('[data-locate]').count(),0,'A record must not remain actionable under B document');
  await page.waitForFunction(()=>document.querySelector('#sourceBinding').textContent.includes('record-B')&&document.querySelector('#sourceBinding').textContent.includes('页码一致'));
  assert.equal(state.imageRequests.some(r=>r.document==='doc2'&&r.page==='2'),false);
});

test('audit: changing the test model invalidates previous external-call consent',async t=>{
  const {page}=await workspace(t);await page.locator('[data-view=tests]').click();
  await page.locator('.test-point').first().waitFor();await page.locator('#testMode').selectOption('recognize');
  await page.locator('#testAllowExternal').check();assert.equal(await page.locator('#runTest').isDisabled(),false);
  await page.locator('#testModel').selectOption('deepseek');
  assert.equal(await page.locator('#testAllowExternal').isChecked(),false);
  assert.equal(await page.locator('#runTest').isDisabled(),true);
});

test('audit: a failed history detail read cannot retain another record export or raw data',async t=>{
  const {page,state}=await workspace(t);
  await page.locator('#saveSnapshot').click();await page.waitForFunction(()=>document.querySelector('#historyRaw').textContent.includes('h_'));
  await page.locator('#closeHistory').click();await page.locator('#saveSnapshot').click();
  await page.waitForFunction(id=>document.querySelector('#historyRaw').textContent.includes(id),state.records[0].id);await page.locator('#closeHistory').click();
  await page.locator('[data-view=history]').click();state.detailFail=true;
  await page.locator('[data-detail="'+state.records[1].id+'"]').click();
  await page.locator('#historyTitle').filter({hasText:'读取失败'}).waitFor();
  assert.equal(await page.locator('#exportHistory').getAttribute('href'),null);
  assert.equal(await page.locator('#historyRaw').textContent(),'');
  assert.equal(await page.locator('#historyRename').inputValue(),'');
});

test('audit: an exchange-file indicator does not claim the supplier reply exists',async t=>{
  const {page,state}=await workspace(t);state.requestOnly=true;await page.locator('[data-stage=calls]').click();
  await page.locator('[data-call]').waitFor();await page.locator('[data-call]').click();
  await page.locator('#callNotes').filter({hasText:'未收到或未保存供应商回复'}).waitFor();
  await page.locator('#closeCall').click();const text=await page.locator('.record-body').textContent();
  assert.match(text,/原始交互文件/);assert.doesNotMatch(text,/供应商原始响应：已保存/);
});

test('audit: comparing process snapshots cannot report zero test changes as equivalence',async t=>{
  const {page,state}=await workspace(t);await page.locator('#saveSnapshot').click();
  await page.waitForFunction(()=>document.querySelector('#historyRaw').textContent.includes('h_'));await page.locator('#closeHistory').click();
  await page.locator('[data-view=history]').click();state.snapshotComparison=true;
  await page.locator('[data-compare-side=left]').first().click();await page.locator('[data-compare-side=right]').first().click();
  await page.locator('#compareHistory').click();await page.locator('#historyComparison').filter({hasText:'过程快照'}).waitFor();
  assert.doesNotMatch(await page.locator('#historyComparison').textContent(),/0个测试点记录有变化/);
});


test('audit: late review-save response cannot replace another open history detail',async t=>{
  const {page,state}=await workspace(t);
  await page.locator('#saveSnapshot').click();await page.waitForFunction(()=>document.querySelector('#historyRaw').textContent.includes('h_'));await page.locator('#closeHistory').click();
  await page.locator('#saveSnapshot').click();await page.waitForFunction(id=>document.querySelector('#historyRaw').textContent.includes(id),state.records[0].id);
  const savedId=state.records[0].id,otherId=state.records[1].id;
  state.slowReview=true;await page.locator('#reviewNote').fill('A review');await page.locator('#saveReview').click();
  await page.locator('#closeHistory').click();await page.locator('[data-view=history]').click();
  await page.locator('[data-detail="'+otherId+'"]').click();await page.waitForFunction(id=>document.querySelector('#historyRaw').textContent.includes(id),otherId);
  await page.waitForTimeout(450);
  assert.match(await page.locator('#historyRecordInfo').textContent(),new RegExp(otherId));
  assert.doesNotMatch(await page.locator('#historyRecordInfo').textContent(),new RegExp(savedId));
});
