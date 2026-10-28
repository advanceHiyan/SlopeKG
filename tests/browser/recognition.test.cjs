const assert = require('node:assert/strict');
const {readFile} = require('node:fs/promises');
const path = require('node:path');
const {before,after,test} = require('node:test');
const {chromium} = require('playwright');
let browser;
before(async()=> {browser=await chromium.launch({headless:true,channel:process.env.SLOPEKG_BROWSER_CHANNEL || (process.platform==='win32'?'msedge':undefined)});});
after(async()=> {await browser?.close();});

async function workspace(t) {
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[], requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  const state={status:'none'};
  await page.route('**/*',async route=>{
    const request=route.request(), url=new URL(request.url()), pathname=url.pathname;
    if ((state.failCatalog && pathname.startsWith('/api/recognition/')) ||
        (state.failResults && pathname==='/api/recognition')) {
      return route.fulfill({status:503,json:{error:'temporary unavailable'}});
    }
    if(pathname==='/api/pipeline/jobs' && request.method()==='POST') {
      const options=request.postDataJSON();requests.push(options);state.status=options.prepare_only?'prepared':'partial';
      return route.fulfill({status:202,json:{id:'new',status:state.status,progress:100,options,
        result:{recognizer:'multimodal',summary:{facts:2,verified_model:1,pending:1}},message:'done'}});
    }
    const payloads={
      '/api/status':{parsed:{}},'/api/documents':{rows:[],count:0},
      '/api/pipeline/jobs/latest':{status:'none'},
      '/api/recognition/profiles':{profiles:[{id:'provider',name:'国内模型',model:'vision',endpoint:'https://example.invalid/v1',credential_configured:true}]},
      '/api/recognition/documents':{documents:[{path:'new/匿名资料.pdf',name:'匿名资料.pdf',size_bytes:1024}]},
      '/api/recognition':{processing_status:'partial',summary:{facts:2,verified_model:1,pending:1,rejected:0},
        metric_note:'自动核验状态不是人工准确率或召回率',count:2,rows:[
          {id:'a',file_name:'匿名资料.pdf',source_path:'new/匿名资料.pdf',page:3,object:'材料X',scope:'同表同一行',field:'单价',value:'72.6',unit:'元',unit_basis:'kg',role:'calculation',status:'verified_model',quote:'<script>source</script> 72.6 元/kg'},
          {id:'b',file_name:'匿名资料.pdf',page:4,object:'墙体',field:'高度',value:'8.7',unit:'m',status:'pending',quote:'原文'}]},
    };
    if (pathname==='/api/recognition' && url.searchParams.get('status')==='pending') {
      const payload=payloads[pathname];return route.fulfill({json:{...payload,count:1,rows:[payload.rows[1]]}});
    }
    if(payloads[pathname])return route.fulfill({json:payloads[pathname]});
    if(pathname.startsWith('/web/')){
      const file=path.basename(pathname);
      return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:await readFile(path.join(__dirname,'../../web',file))});
    }
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto('http://slopekg.test/web/pdf-ingestion.html');
  await page.locator('#recognitionFiles input').waitFor();
  return {page,requests,state};
}

test('local prepare sends explicit files and no permission to transmit',async t=>{
  const {page,requests}=await workspace(t);
  assert.equal(await page.locator('#recognitionRun').isDisabled(),true);
  await page.locator('#recognitionFiles input').check();
  assert.equal(await page.locator('#recognitionPrepare').isEnabled(),true);
  assert.equal(await page.locator('#recognitionRun').isDisabled(),true);
  await page.locator('#recognitionPrepare').click();
  await page.waitForFunction(()=>document.querySelector('#jobStatus').textContent==='本地准备完成');
  assert.equal(requests.length,1);assert.equal(requests[0].prepare_only,true);assert.equal(requests[0].allow_external,false);
  assert.deepEqual(requests[0].input_pdfs,['new/匿名资料.pdf']);
  assert.equal(await page.locator('#deepRunBtn').count(),0);
  assert.equal(await page.locator('#rerunBtn').isEnabled(),true);
});

test('external recognition uses the chosen model and bounded settings',async t=>{
  const {page,requests}=await workspace(t);
  await page.locator('#recognitionFiles input').check();
  await page.locator('#recognitionAllowExternal').check();
  await page.locator('#recognitionMaxCalls').fill('37');await page.locator('#recognitionConcurrency').fill('3');
  await page.locator('#recognitionRun').click();
  await page.waitForFunction(()=>document.querySelector('#jobStatus').textContent==='部分完成');
  assert.equal(requests[0].model_profile,'provider');assert.equal(requests[0].allow_external,true);
  assert.deepEqual(requests[0].visual_settings,{max_calls:37,model_concurrency:3});
  assert.doesNotMatch(await page.locator('#jobDetails').textContent(),/基础解析（不调用大模型）/);
});

test('candidate results preserve units, page links and untrusted source text',async t=>{
  const {page}=await workspace(t);
  const row=page.locator('#recognitionRows tr').first();await row.waitFor();
  assert.match(await row.textContent(),/自动核验通过 · 待人工确认/);
  assert.match(await row.textContent(),/元\/kg/);
  assert.match(await row.locator('a').getAttribute('href'),/#page=3$/);
  assert.equal(await row.locator('script').count(),0);
  await row.locator('summary').click();assert.match(await row.textContent(),/<script>source<\/script>/);
  await page.locator('#recognitionStatusFilter').selectOption('pending');
  await page.waitForFunction(()=>document.querySelector('#recognitionRows').textContent.includes('墙体') && !document.querySelector('#recognitionRows').textContent.includes('材料X'));
  assert.match(await page.locator('#recognitionResultNote').textContent(),/不是人工准确率/);
});

test('workbench stays usable at narrow widths and exports a visual QA screenshot',async t=>{
  const {page}=await workspace(t);
  if(process.env.SLOPEKG_RECOGNITION_SCREENSHOT) await page.screenshot({path:process.env.SLOPEKG_RECOGNITION_SCREENSHOT,fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
  await page.locator('#recognitionFiles input').check();
  assert.equal(await page.locator('#recognitionPrepare').isEnabled(),true);
});

test('failed refresh clears stale results and disables actions until recovery',async t=>{
  const {page,state}=await workspace(t);
  await page.locator('#recognitionFiles input').check();
  await page.locator('#recognitionRows tr').first().waitFor();
  state.failCatalog=true;state.failResults=true;
  await page.locator('#refreshBtn').click();
  await page.waitForFunction(()=>document.querySelector('#recognitionPageInfo').textContent==='暂不可读');
  await page.waitForFunction(()=>document.querySelector('#recognitionEndpoint').textContent.includes('暂不可读'));
  assert.equal(await page.locator('#recognitionPrepare').isDisabled(),true);
  assert.equal(await page.locator('#recognitionRun').isDisabled(),true);
  assert.doesNotMatch(await page.locator('#recognitionRows').textContent(),/材料X/);
  assert.equal(await page.locator('#recognitionMetrics').textContent(),'');
  assert.equal(await page.locator('#recognitionNext').isDisabled(),true);
  state.failCatalog=false;state.failResults=false;
  await page.locator('#refreshBtn').click();
  await page.locator('#recognitionFiles input').waitFor();
  assert.equal(await page.locator('#recognitionFiles input').isChecked(),true);
  await page.waitForFunction(()=>document.querySelector('#recognitionRows').textContent.includes('材料X'));
  assert.equal(await page.locator('#recognitionPrepare').isEnabled(),true);
});
