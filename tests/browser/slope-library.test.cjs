const assert=require('node:assert/strict');
const {readFile}=require('node:fs/promises');
const path=require('node:path');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});});
after(async()=>{await browser?.close();});

test('source dossiers preserve candidate scope, confirm related objects, and survive reload without assessment',async t=>{
  const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[],posted=[];
  page.on('pageerror',e=>errors.push(e.message));
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  let confirmed=false;
  const slope=()=>({id:'s1',name:'示例边坡',identity_status:confirmed?'confirmed':'pending',sources:2,versions:2});
  const source=id=>({id,group_id:'b1',label:id==='b1'?'示例边坡':'坡脚',slope_id:'s1',revision:confirmed?1:0,history:confirmed?[{at:'2026-11-11T08:00:00Z',actor:'核对人',reason:'范围一致'}]:[],versions:[{
    id:'snapshot',route:'agent',run_id:'run',created_at:'2026-11-11T08:00:00Z',document:'survey.pdf',document_id:'d',
    errors:[],figures:[],pdf_url:'/api/slope-library/source?source=sha',object:{facts:[{
      raw:{id:'f-'+id,field:'坡高',value:'<script>bad()</script>',unit:'m',scope:id==='b1'?'整体':'坡脚局部'},
      evidence:[{page:1,quote:'原文 10m',image_url:'/api/slope-library/page?source=sha&page=1'}],issues:[],admission:'candidate'}]}}]});
  const detail=()=>({slope:slope(),sources:[source('b1'),source('b2')]});
  await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/api/slope-library'){
      if(req.method()==='POST'){posted.push(req.postDataJSON());confirmed=true;return route.fulfill({json:detail()});}
      return route.fulfill({json:url.searchParams.has('slope_id')?detail():{slopes:[slope()],unassigned:[],snapshots:[{id:'snapshot'}],receipts:[]}});
    }
    if(url.pathname.startsWith('/web/')){const file=path.basename(url.pathname);return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:await readFile(path.join(__dirname,'../../web',file))});}
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto('http://slopekg.test/web/slope-library.html');
  await page.locator('[data-bind]').first().waitFor();
  assert.equal(posted.length,0);
  assert.equal(await page.locator('#detail script').count(),0);
  assert.match(await page.locator('#detail').innerText(),/坡脚局部/);
  await page.locator('[data-bind]').first().click();
  await page.locator('#actor').fill('核对人');await page.locator('#reason').fill('范围一致');await page.locator('#confirm').check();
  await page.getByRole('button',{name:'保存归属',exact:true}).click();
  await page.locator('#bindDialog').waitFor({state:'hidden'});
  assert.equal(posted[0].operation,'bind');assert.equal(posted[0].include_related,true);
  assert.deepEqual(posted[0].related_revisions,{b1:0,b2:0});
  await page.reload();await page.locator('#detail').getByText('归属已核对',{exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),true);
});
