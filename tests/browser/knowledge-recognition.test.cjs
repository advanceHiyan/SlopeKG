const assert=require('node:assert/strict');
const {readFile}=require('node:fs/promises');
const path=require('node:path');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.env.SLOPEKG_BROWSER_CHANNEL||(process.platform==='win32'?'msedge':undefined)});});
after(async()=>{await browser?.close();});

async function workspace(t){
  const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  const state={fail:false};
  await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url()),p=url.pathname;
    if(state.fail&&p.startsWith('/api/'))return route.fulfill({status:503,json:{error:'temporary unavailable'}});
    if(p==='/api/pipeline/jobs'&&req.method()==='POST'){
      requests.push(req.postDataJSON());return route.fulfill({status:202,json:{id:'fixture'}});
    }
    const d={
      '/api/recognition/profiles':{profiles:[{id:'deepseek-knowledge',name:'测试模型',model:'fixture',endpoint:'https://example.invalid'}]},
      '/api/recognition/documents':{documents:[{path:'nested/匿名资料.pdf',name:'匿名资料.pdf'}]},
      '/api/pipeline/jobs/latest':{status:'none'},
      '/api/knowledge-recognition':{processing_status:'partial',metric_note:'模型核验不是人工准确率',summary:{entities:2,facts:1,verified_model:1},
        entities:[{id:'one',name:'设备<script>bad()</script>',fact_count:1,description:'非连续来源',chunk_ids:['c0001','c0018']}],
        documents:[{file_name:'匿名资料.pdf',coverage:{total_pages:8,text_processing_status:'completed',attributed_chunks:18,total_chunks:18,unread_graphics_pages:[8]}}],
        count:1,rows:[{entity_id:'one',object:'设备<script>bad()</script>',field:'pressure',value:'2.50',unit:'MPa',scope:'局部',role:'observation',condition:'cold',
          status:'verified_model',reason:'Source aligned',file_name:'匿名资料.pdf',source_path:'nested/匿名资料.pdf',evidence:[{page:7,quote:'Original value <img src=x onerror=bad()> 2.50 MPa'}]}],relations:[]}
    };
    if(d[p])return route.fulfill({json:d[p]});
    if(p.startsWith('/web/')){const f=path.basename(p);return route.fulfill({contentType:f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':'text/html',body:await readFile(path.join(__dirname,'../../web',f))});}
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto('http://slopekg.test/web/knowledge-recognition.html');
  await page.locator('#files input').waitFor();await page.locator('#rows tr').first().waitFor();
  return {page,requests,state};
}

test('third route prepares explicit files locally and requires model consent',async t=>{
  const {page,requests}=await workspace(t);
  await page.locator('#files input').check();
  assert.equal(await page.locator('#run').isDisabled(),true);
  await page.locator('#prepare').click();
  await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  assert.equal(requests[0].recognizer,'knowledge');assert.equal(requests[0].prepare_only,true);assert.equal(requests[0].allow_external,false);
  assert.deepEqual(requests[0].input_pdfs,['nested/匿名资料.pdf']);
  await page.locator('#allow').check();await page.locator('#run').click();
  await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  assert.equal(requests[1].recognizer,'knowledge');assert.equal(requests[1].allow_external,true);
  assert.deepEqual(requests[1].knowledge_settings,{max_calls:500,model_concurrency:4});
  assert.equal('visual_settings' in requests[1],false);
});

test('evidence is escaped, source-linked and coverage is distinct from accuracy',async t=>{
  const {page}=await workspace(t);
  assert.match(await page.locator('#note').textContent(),/不是人工准确率/);
  assert.match(await page.locator('#coverage').textContent(),/图形待读页 1/);
  assert.equal(await page.locator('#rows script,#rows img').count(),0);
  await page.locator('#rows details summary').click();
  assert.match(await page.locator('#rows a').getAttribute('href'),/#page=7$/);
  await page.locator('#entity').selectOption('one');
  await page.waitForFunction(()=>document.querySelector('#entityInfo').textContent.includes('c0018'));
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
});

test('failed refresh removes stale data and disables running',async t=>{
  const {page,state}=await workspace(t);
  await page.locator('#files input').check();state.fail=true;await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#pageInfo').textContent==='暂不可读');
  assert.equal(await page.locator('#prepare').isDisabled(),true);
  assert.doesNotMatch(await page.locator('#rows').textContent(),/pressure/);
  assert.equal(await page.locator('#metrics').textContent(),'');
  state.fail=false;await page.locator('#refresh').click();await page.locator('#files input').waitFor();
  await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
});
