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
  const source=id=>({id,group_id:'b1',label:id==='b1'?'示例边坡':'坡脚',slope_id:'s1',identity_ready:confirmed,revision:confirmed?1:0,history:confirmed?[{at:'2026-11-11T08:00:00Z',actor:'核对人',reason:'范围一致'}]:[],versions:[{
    id:'snapshot',route:'agent',run_id:'run',created_at:'2026-11-11T08:00:00Z',document:'survey.pdf',document_id:'d',
    errors:[],figures:[],pdf_url:'/api/slope-library/source?source=sha',object:{facts:[{
      raw:{id:'f-'+id,field:'坡高',value:'<script>bad()</script>',unit:'m',scope:id==='b1'?'整体':'坡脚局部'},
      evidence:[{page:1,quote:'原文 10m',image_url:'/api/slope-library/page?source=sha&page=1'}],issues:[],admission:'candidate'}]}}]});
  const detail=()=>({slope:slope(),sources:[source('b1'),source('b2')]});
  await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/api/slope-library/identity')return route.fulfill({json:{
      binding:source('b1'),source:{attributes:{},conflicts:[],label:'示例边坡'},related:[source('b1'),source('b2')],
      candidates:[{slope_id:'s1',name:'示例边坡',revision:0,attributes:{},reasons:['名称一致'],conflicts:[]}]}});
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
  await page.reload();await page.locator('#detail').getByText(/归属已核对/).first().waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),true);
});

test('unassigned material is saved before review and a master is created only after explicit confirmation',async t=>{
  const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[],posted=[];
  page.on('pageerror',e=>errors.push(e.message));t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  let created=false,attributes={};
  const source=()=>({id:'b1',group_id:'b1',label:'G209 K1+000-K1+100 左侧边坡',slope_id:created?'permanent-id':null,
    revision:created?1:0,versions:[],history:[],source_objects:1,identity_ready:created});
  const slope=()=>({id:'permanent-id',name:'登记边坡',sources:1,versions:1,identity_status:'confirmed',identity:{attributes}});
  const detail=()=>({slope:created?slope():null,sources:[source()],identity:created?{slope_id:'permanent-id',attributes,revision:1,reviewed:true,history:[]}:null});
  await page.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/api/slope-library/identity')return route.fulfill({json:{binding:source(),source:{attributes:{route_code:'G209',side:'左侧',start_station_m:1000,end_station_m:1100},conflicts:[]},related:[source()],candidates:[]}});
    if(url.pathname==='/api/slope-library'){
      if(req.method()==='POST'){
        const body=req.postDataJSON();posted.push(body);
        if(body.identity.external_id&&!body.identity.namespace)return route.fulfill({status:400,json:{error:'外部编号必须同时填写编号所属范围'}});
        created=true;attributes=body.identity;return route.fulfill({json:detail()});
      }
      return route.fulfill({json:url.searchParams.has('binding_id')||url.searchParams.has('slope_id')?detail():{slopes:created?[slope()]:[],unassigned:created?[]:[source()],snapshots:[{id:'v1'}],receipts:[]}});
    }
    if(url.pathname.startsWith('/web/')){const file=path.basename(url.pathname);return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:await readFile(path.join(__dirname,'../../web',file))});}
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto('http://slopekg.test/web/slope-library.html');
  await page.locator('[data-bind]').click();await page.locator('#identitySource').getByText('本来源的身份线索（尚未核对）').waitFor();
  assert.equal(posted.length,0);assert.equal(await page.locator('#bindTarget').inputValue(),'');
  await page.locator('#bindTarget').selectOption('__new');await page.locator('#saveBinding').waitFor({state:'visible'});
  await page.locator('#newName').fill('登记边坡');await page.locator('#new-external_id').fill('S-01');
  await page.locator('#actor').fill('核对人');await page.locator('#reason').fill('原件编号和位置核对');await page.locator('#confirm').check();
  await page.getByRole('button',{name:'保存归属',exact:true}).click();
  await page.locator('#bindError').getByText('外部编号必须同时填写编号所属范围').waitFor();
  await page.locator('#new-namespace').fill('单位A/G209');await page.getByRole('button',{name:'保存归属',exact:true}).click();
  await page.locator('#bindDialog').waitFor({state:'hidden'});
  await page.locator('#detail').getByText('永久系统 ID：',{exact:false}).waitFor();
  assert.equal(posted.at(-1).slope_id,'');assert.equal(posted.at(-1).identity.namespace,'单位A/G209');
  assert.match(await page.locator('#detail').textContent(),/permanent-id/);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),true);
});
