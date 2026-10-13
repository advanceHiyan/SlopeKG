const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { before, after, test } = require('node:test');
const { chromium } = require('playwright');
let browser;
before(async () => { browser = await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined}); });
after(async () => { await browser?.close(); });

async function sample(t) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors,[]); });
  const state = {fail:false, calls:[]};
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/catalog')) return route.fulfill({json:{count:2,start:'2022-01-04',end:'2022-08-12',counts_by_type:{'边坡':1,'桥梁':1},boundary:'历史资料，不代表近期状态'}});
    if (url.pathname==='/api/inspections/history') {
      state.calls.push(Object.fromEntries(url.searchParams));
      if (state.fail) return route.fulfill({status:409,json:{error:'数据损坏'}});
      const bridge = url.searchParams.get('asset_type')==='桥梁';
      return route.fulfill({json:{total:1,rows:[{raw_time:'2022-04-25',asset_type:bridge?'桥梁':'边坡',station_text:'K340+540',direction:'下行',problem:bridge?'桥头水毁':'<img src=x onerror=alert(1)>',raw_fields:{'长度m':0},source:{sheet:bridge?'桥梁':'边坡',row:2,cells:{'存在问题':'F2'}}}]}});
    }
    if (url.pathname.startsWith('/web/')) {
      const name = path.basename(url.pathname);
      return route.fulfill({contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html',body:await readFile(path.join(__dirname,'../../web',name))});
    }
    return route.fulfill({status:404,body:'not found'});
  });
  await page.goto('http://slopekg.test/web/inspection-history.html');
  await page.waitForFunction(() => document.querySelector('#historyCount').textContent.includes('共 1 条'));
  return {page,state};
}

test('history filters send the selected type and dates, preserve zero, and escape source text', async t => {
  const {page,state}=await sample(t);
  assert.equal(await page.locator('#historyRows img').count(),0);
  await page.locator('#historyRows summary').click();
  assert.match(await page.locator('#historyRows').textContent(),/长度m：0/);
  assert.match(await page.locator('#historyRows').textContent(),/F2/);
  await page.selectOption('#historyType','桥梁');
  await page.fill('#historyQuery','水毁');
  await page.click('#historySearch');
  await page.waitForFunction(() => document.querySelector('#historyRows').textContent.includes('桥头水毁'));
  assert.equal(state.calls.at(-1).asset_type,'桥梁');
  assert.equal(state.calls.at(-1).q,'水毁');
  assert.equal(state.calls.at(-1).start,'2022-01-04');
});

test('failed history read clears old rows and retry restores the chosen type', async t => {
  const {page,state}=await sample(t);
  state.fail=true;
  await page.click('#historySearch');
  await page.waitForFunction(() => !document.querySelector('#historyError').hidden);
  assert.equal(await page.locator('#historyRows').textContent(),'');
  assert.equal(await page.locator('#historyCount').textContent(),'');
  state.fail=false;
  await page.selectOption('#historyType','桥梁');
  await page.click('#historySearch');
  await page.waitForFunction(() => document.querySelector('#historyRows').textContent.includes('桥头水毁'));
  assert.equal(await page.locator('#historyError').isVisible(),false);
});
