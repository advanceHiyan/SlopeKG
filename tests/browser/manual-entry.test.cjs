const assert = require('node:assert/strict');
const {readFile} = require('node:fs/promises');
const path = require('node:path');
const {before,after,test} = require('node:test');
const {chromium} = require('playwright');
let browser;
before(async()=>{browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});});
after(async()=>{await browser?.close();});

async function workbench(t, name='risk', options={}) {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const state={...options}, requests=[], errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  const schema={slope_fields:[
    {code:'slope_height_m',label:'坡高（m）',type:'number',min:0,max:2000},
    {code:'material_nature',label:'材料类型',type:'enum',options:['岩质','土质']},
    {code:'rainfall',label:'近期降雨',type:'text',dynamic:true}],
    slope_field_groups:[{title:'工程信息',description:'核对几何与材料',fields:['slope_height_m','material_nature']},
      {title:'近期观测',description:'记录实际观测日期',fields:['rainfall']}],
    rule_fields:[{code:'slope_height_m',label:'坡高',type:'number'}],
    rule_operators:[{code:'gte',label:'大于等于'},{code:'exists',label:'已有值'}]};
  const row={slope_id:'s1',slope_label:'G111 测试边坡',business_id:'K1',screening_priority_code:'P3',
    screening_priority:'常规复核',confidence:'待核',basis:[],critical_data_gaps:[],limitations:[]};
  let manualRule;
  await page.route('**/*',async route=>{
    const req=route.request(), pathname=new URL(req.url()).pathname;
    if(pathname==='/api/manual/schema') return route.fulfill({status:state.failSchema?503:200,json:state.failSchema?{error:'offline'}:schema});
    if(pathname==='/api/manual/slopes/s1' && req.method()==='GET') return route.fulfill({status:state.failRead?503:200,
      json:state.failRead?{error:'offline'}:{fields:{slope_height_m:7},updated_at:'2026-10-20',note:'<b>原始记录</b>'}});
    if(pathname.startsWith('/api/manual/') && req.method()!=='GET') {
      const body=req.postDataJSON();requests.push({method:req.method(),body,path:pathname});
      if(state.holdWrite) await new Promise(resolve=>{state.release=resolve;});
      if(state.failWrite) return route.fulfill({status:400,json:{error:'<b>请核对日期</b>'}});
      if(req.method()==='POST' && pathname.endsWith('/rules')) manualRule={...body,id:'manual_rule_test',extraction_method:'manual_input',approval_status:'draft'};
      return route.fulfill({json:{status:'saved',rule:manualRule,message:'规则已保存，未启用'}});
    }
    const payloads={
      '/api/status':{}, '/api/risk/screening':{summary:{priority_counts:{P3:1}},assessments:[row],boundary:{}},
      '/api/risk/evaluation':{}, '/api/slopes/s1':{slope:{props:{slope_height_m:8}}},
      '/api/rules/library':{rules:manualRule?[manualRule]:[],documents:[],stats:{},publication_status:'candidate_not_approved'},
    };
    if(payloads[pathname])return route.fulfill({json:payloads[pathname]});
    if(pathname.startsWith('/web/')) {
      const file=path.basename(pathname);
      return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',
        body:await readFile(path.join(__dirname,'../../web',file))});
    }
    return route.fulfill({status:404,json:{error:'not found'}});
  });
  await page.goto(`http://slopekg.test/web/${name}.html`);
  await page.locator(name==='risk'?'.manual-edit-toggle':'#toggleRuleForm').waitFor();
  return {page,state,requests};
}

test('manual entry has a compact toggle, grouped fields and accessible collapse',async t=>{
  const {page}=await workbench(t);
  const toggle=page.locator('.manual-edit-toggle');
  assert.equal(await toggle.getAttribute('aria-expanded'),'false');
  assert.equal(await toggle.locator('svg').count(),1);
  await toggle.click();await page.locator('.manual-slope-form').waitFor();
  assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  assert.equal(await page.locator('.manual-form-section legend').count(),2);
  assert.equal(await page.locator('[name=slope_height_m]').inputValue(),'7');
  assert.equal(await page.locator('[name=note]').inputValue(),'<b>原始记录</b>');
  assert.equal(await page.locator('.manual-editor b').count(),0);
  await toggle.click();assert.equal(await page.locator('.manual-editor').isHidden(),true);
  await toggle.click();assert.equal(await page.locator('[name=slope_height_m]').inputValue(),'7');
});

test('unread saved records cannot become an empty overwrite and can be retried',async t=>{
  const {page,state,requests}=await workbench(t,'risk',{failRead:true});
  await page.locator('.manual-edit-toggle').click();await page.locator('.retry-manual').waitFor();
  assert.equal(await page.locator('.manual-slope-form').count(),0);assert.equal(requests.length,0);
  state.failRead=false;await page.locator('.retry-manual').click();
  await page.locator('.manual-slope-form').waitFor();assert.equal(await page.locator('[name=slope_height_m]').inputValue(),'7');
});

test('slope save preserves zero, freezes duplicate submits and retains entered values on error',async t=>{
  const {page,state,requests}=await workbench(t,'risk',{holdWrite:true,failWrite:true});
  await page.locator('.manual-edit-toggle').click();await page.locator('[name=slope_height_m]').fill('0');
  await page.locator('.manual-slope-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('.manual-slope-form').dataset.busy==='true');
  assert.equal(await page.locator('.reset-manual').isDisabled(),true);
  await page.locator('.manual-slope-form').evaluate(form=>form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(requests.length,1);assert.equal(requests[0].body.fields.slope_height_m,0);
  state.release();await page.waitForFunction(()=>document.querySelector('.manual-save-status').dataset.state==='error');
  assert.equal(await page.locator('[name=slope_height_m]').inputValue(),'0');
  assert.equal(await page.locator('.manual-save-status b').count(),0);
  assert.equal(await page.locator('.manual-slope-form [type=submit]').isEnabled(),true);
});

test('slope success refreshes results without a page reload or losing search',async t=>{
  const {page,requests}=await workbench(t);
  let navigation=0;page.on('framenavigated',()=>navigation++);
  await page.locator('#riskSearch').fill('G111');await page.locator('.manual-edit-toggle').click();
  await page.locator('[name=rainfall]').fill('未见积水');
  assert.equal(await page.locator('[name=observed_at]').getAttribute('required'),'');
  await page.locator('[name=observed_at]').fill('2026-10-20');
  await page.locator('.manual-slope-form [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('.manual-editor').hidden);
  assert.equal(requests[0].body.observed_at,'2026-10-20');
  assert.equal(await page.locator('#riskSearch').inputValue(),'G111');assert.equal(navigation,0);
});

test('rule entry handles exists, prevents removing the final condition and collapses after save',async t=>{
  const {page,requests}=await workbench(t,'rules');
  await page.locator('#toggleRuleForm').click();
  assert.equal(await page.locator('.remove-condition').isDisabled(),true);
  await page.locator('[name=title]').fill('现场核对');
  await page.locator('.condition-operator').selectOption('exists');
  assert.equal(await page.locator('.condition-value').isDisabled(),true);
  await page.locator('#addRuleCondition').click();assert.equal(await page.locator('.manual-condition-row').count(),2);
  await page.locator('.remove-condition').last().click();
  await page.locator('#manualRuleForm [type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#manualRuleForm').hidden);
  assert.equal(requests[0].body.conditions[0].operator,'exists');assert.equal(requests[0].body.approval_status,'draft');
  assert.match(await page.locator('#ruleDetail').textContent(),/现场核对/);
  assert.equal(await page.locator('#toggleRuleForm').getAttribute('aria-expanded'),'false');
});

test('schema failure leaves rule data visible and explains why entry is disabled',async t=>{
  const {page}=await workbench(t,'rules',{failSchema:true});
  await page.waitForFunction(()=>document.querySelector('#manualRuleStatus').textContent.includes('配置读取失败'));
  assert.equal(await page.locator('#manualRuleStatus').isVisible(),true);
  assert.equal(await page.locator('#toggleRuleForm').isDisabled(),true);
});

test('failed rule deletion keeps the record visible and allows retry',async t=>{
  const {page,state,requests}=await workbench(t,'rules');
  await page.locator('#toggleRuleForm').click();await page.locator('[name=title]').fill('保留这条规则');
  await page.locator('#manualRuleForm [type=submit]').click();await page.locator('.delete-manual-rule').waitFor();
  state.failWrite=true;page.on('dialog',dialog=>dialog.accept());
  await page.locator('.delete-manual-rule').click();
  await page.waitForFunction(()=>document.querySelector('#ruleDetail .manual-save-status').textContent.includes('删除失败'));
  assert.match(await page.locator('#ruleDetail').textContent(),/保留这条规则/);
  assert.equal(await page.locator('.delete-manual-rule').isEnabled(),true);
  assert.equal(requests[1].method,'DELETE');
  assert.equal(await page.locator('#ruleDetail .manual-save-status b').count(),0);
});

test('expanded editors fit mobile screens and provide desktop screenshots',async t=>{
  for(const name of ['risk','rules']) {
    const {page}=await workbench(t,name);
    await page.locator(name==='risk'?'.manual-edit-toggle':'#toggleRuleForm').click();
    await page.locator(name==='risk'?'.manual-slope-form':'#manualRuleForm').waitFor();
    if(process.env.SLOPEKG_MANUAL_SCREENSHOTS) await page.screenshot({path:path.join(process.env.SLOPEKG_MANUAL_SCREENSHOTS,`${name}-desktop.png`),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,name);
    if(process.env.SLOPEKG_MANUAL_SCREENSHOTS) await page.screenshot({path:path.join(process.env.SLOPEKG_MANUAL_SCREENSHOTS,`${name}-mobile.png`),fullPage:true});
  }
});
