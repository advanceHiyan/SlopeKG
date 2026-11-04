const assert=require('node:assert/strict');
const {readFile}=require('node:fs/promises');
const path=require('node:path');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});});
after(async()=>{await browser?.close();});
test('fourth route preserves records, supports stages, and sends only explicitly selected PDFs',async t=>{
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],sent=[];
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const req=route.request(),u=new URL(req.url()),p=u.pathname;
    if(p==='/api/pipeline/jobs'&&req.method()==='POST'){sent.push(req.postDataJSON());return route.fulfill({status:202,json:{id:'fixture'}});}
    const routes={
      '/api/recognition/profiles':{profiles:[{id:'deepseek-knowledge',name:'Fixture',model:'fixture',endpoint:'https://example.invalid'}]},
      '/api/recognition/documents':{documents:[{path:'nested/anonymous.pdf'}]},
      '/api/pipeline/jobs/latest':{status:'none'},
      '/api/document-recognition':{processing_status:'partial',summary:{blocks:2,groups:2,facts:1,pending:1},
        documents:[{id:'doc',name:'anonymous.pdf',status:'partial'}],count:1,
        rows:[{document_id:'doc',record:u.searchParams.get('stage')==='blocks'?{id:'b1',text:'完整段落 <script>bad()</script>'}:{subject:'Pump A',field:'flow',value:'12',unit:'L',status:'pending',evidence:[{quote:'Pump A | 12'}]}}]}
    };
    if(routes[p])return route.fulfill({json:routes[p]});
    if(p.startsWith('/web/')){const f=path.basename(p);return route.fulfill({contentType:f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':'text/html',body:await readFile(path.join(__dirname,'../../web',f))});}
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto('http://slopekg.test/web/document-recognition.html');
  await page.locator('#files input').waitFor();await page.locator('#records article').waitFor();
  await page.locator('#files input').check();assert.equal(await page.locator('#run').isDisabled(),true);
  await page.locator('#prepare').click();await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  assert.equal(sent[0].recognizer,'document');assert.equal(sent[0].prepare_only,true);assert.equal(sent[0].allow_external,false);
  assert.deepEqual(sent[0].input_pdfs,['nested/anonymous.pdf']);
  await page.locator('#allow').check();await page.locator('#run').click();await page.waitForFunction(()=>!document.querySelector('#prepare').disabled);
  assert.equal(sent[1].allow_external,true);assert.deepEqual(sent[1].document_settings,{max_calls:24,record_model_inputs:true});
  assert.equal('knowledge_settings' in sent[1],false);
  await page.locator('#stage').selectOption('blocks');await page.locator('#records .source').waitFor();
  assert.match(await page.locator('#records').innerText(),/完整段落 <script>bad\(\)<\/script>/);
  assert.equal(await page.locator('#records script').count(),0);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
});
