const assert=require('node:assert/strict');
const {readFile}=require('node:fs/promises');
const path=require('node:path');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});});
after(async()=>{await browser?.close();});
const key='c'.repeat(64),fact={id:'f1',subject:'Object A <script>unsafe()</script>',field:'height',value:'12',status:'pending',evidence:[{page:2,block_id:'b1',quote:'12',bbox:[.1,.2,.8,.3]}],parent_evidence:[],quality_issues:['ownership']};
async function fixture(t,initial=''){
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],posts=[],state={fail:false};
  page.on('pageerror',e=>errors.push(e.message));t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  await page.route('**/*',async r=>{
    const u=new URL(r.request().url()),p=u.pathname;
    if(r.request().method()==='POST'){posts.push(r.request().postDataJSON());return r.fulfill({status:202,json:{id:'fixture'}});}
    if(p==='/api/recognition-debug/catalog')return r.fulfill({json:{schema_version:'document-v0.2',route:'document',runs:[{id:'live',label:'当前生产记录'}],documents:[{id:'doc',name:'anonymous.pdf',pages:3,status:'partial',workflow_complete:true,selection:{scope:'selected_pages',selected_pages:[2]}}],stages:['input','native','blocks','groups','claims','reviews','audit','calls','facts','errors'].map(id=>({id,label:id,help:id})),status:'partial',summary:{facts:1,pending:1,calls:0,cache_hits:1}}});
    if(p==='/api/recognition-debug')return r.fulfill({json:{rows:[fact],count:1,note:'complete',snapshot:'fixed'}});
    if(p==='/api/recognition-debug/document-trace'){
      if(state.fail)return r.fulfill({status:400,json:{error:'候选不属于当前资料记录'}});
      return r.fulfill({json:{claim_id:'f1',document:'doc',final:fact,claims:[fact],source_pages:[{page:2,native_sources:[{id:'n1',text:'12'}]}],blocks:[{id:'b1',kind:'table',text:'12',page:2}],groups:[],links:[],repair_queue:[],notes:['按保存ID关联'],calls:[{id:key,stage:'document_extract',association:'exact_claim_id',cache_hit:true,input_saved:true,raw_exchange_saved:true,raw_detail_origin:'provider_response_content',input_claims:[],raw_claims:[{subject:'Object A',value:'12'}],validated_claims:[fact],raw_reviews:[],validated_reviews:[],audit_flags:[],context_available:true,request_block_count:1,request_block_ids:['b1'],group_coverage:[{group_id:'g1',name:'Object A',group_in_request:true,included_block_ids:['b1'],absent_block_ids:['title']}]}]}});
    }
    if(p==='/api/recognition-debug/call')return r.fulfill({json:{cache_key:key,input:{system:'complete system',prompt:'complete input'},cache:{answer:{_document_validated:{claims:[fact]}}},exchange:{attempts:[{request:{model:'fixture',max_tokens:65536},response:{choices:[{message:{content:'{"claims":[]}'}}]}}]},notes:[]}});
    if(p==='/api/recognition-debug/page.png')return r.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=','base64')});
    if(p==='/api/recognition/profiles')return r.fulfill({json:{profiles:[{id:'deepseek-knowledge',name:'Fixture',model:'fixture',endpoint:'https://example.invalid'}]}});
    if(p==='/api/recognition/documents')return r.fulfill({json:{documents:[{path:'anonymous.pdf'}]}});
    if(p==='/api/pipeline/jobs/latest')return r.fulfill({json:{status:'completed'}});
    if(p==='/api/assessment-debug/catalog')return r.fulfill({json:{linked_cases:[]}});
    if(p.startsWith('/web/')){const f=path.basename(p);return r.fulfill({contentType:f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':'text/html',body:await readFile(path.join(__dirname,'../../web',f))});}
    return r.fulfill({json:{records:[],datasets:[]}});
  });
  await page.goto('http://slopekg.test/web/recognition-debug.html?route=document&stage=facts'+initial);
  await page.locator('[data-document-trace]').waitFor();return {page,posts,state};
}
test('document trace joins PDF, parsed lines, extraction context, original exchange and final result without POST',async t=>{
  const {page,posts,state}=await fixture(t,'&claim=f1');
  await page.locator('[data-trace-tab="extract"]').waitFor();
  assert.match(await page.locator('#documentOverview').innerText(),/物理页 2.*原件共3页/);
  assert.equal(await page.locator('#documentTrace script').count(),0);
  await page.locator('[data-trace-tab="extract"]').click();
  assert.match(await page.locator('[data-trace-panel="extract"]').innerText(),/1 \/ 2/);
  await page.locator('[data-trace-call]').click();await page.locator('#callBody').getByText('complete input',{exact:true}).waitFor();
  assert.match(await page.locator('#callBody').innerText(),/65536/);
  await page.locator('#closeCall').click();await page.locator('[data-trace-tab="source"]').click();
  await page.getByRole('button',{name:'放大原始PDF第2页',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#pageDialogCanvas').hidden);
  assert.equal(await page.locator('#pageDialogOverlay rect').count(),1);await page.locator('#closePage').click();
  await page.locator('[data-trace-tab="final"]').click();assert.match(await page.locator('[data-trace-panel="final"]').innerText(),/ownership/);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await page.locator('#documentTrace').evaluate(e=>e.scrollWidth<=e.clientWidth+1),true);
  await page.locator('#closeDocumentTrace').click();state.fail=true;await page.locator('[data-document-trace]').click();
  await page.getByText('追查读取失败：候选不属于当前资料记录',{exact:true}).waitFor();
  assert.equal(await page.locator('#downloadDocumentTrace').isDisabled(),true);
  assert.equal(await page.locator('[data-trace-panel]').count(),0);assert.deepEqual(posts,[]);
});
test('document launch keeps consent and complete capture without mixing other route settings',async t=>{
  const {page,posts}=await fixture(t);await page.locator('#launchPanel > summary').click();
  assert.equal(await page.locator('#start').isDisabled(),true);
  assert.equal(await page.locator('#recordInputs').isChecked(),true);assert.equal(await page.locator('#recordInputs').isDisabled(),true);
  assert.match(await page.locator('#allowText').innerText(),/原文和页面图像/);
  await page.locator('#prepare').click();await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  assert.equal(posts[0].recognizer,'document');assert.equal(posts[0].allow_external,false);
  assert.deepEqual(posts[0].document_settings,{max_calls:64,model_concurrency:6,record_model_inputs:true});
  assert.equal('visual_settings' in posts[0],false);assert.equal('knowledge_settings' in posts[0],false);
  await page.locator('#allow').check();await page.locator('#start').click();await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  assert.equal(posts[1].allow_external,true);assert.equal(posts[1].prepare_only,false);
});
