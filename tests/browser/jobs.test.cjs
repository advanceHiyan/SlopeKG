const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { before, after, test } = require('node:test');
const { chromium } = require('playwright');

let browser;
before(async () => { browser = await chromium.launch({ headless: true, channel: process.env.SLOPEKG_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined) }); });
after(async () => { await browser?.close(); });

async function jobPage(t, kind, failure, initialStatus = 'running') {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  const state = { polls: 0, status: initialStatus };
  const prefix = kind === 'rules' ? '/api/rules/jobs' : '/api/pipeline/jobs';
  const job = () => ({ id: 'sample', status: state.status, progress: state.status === 'completed' ? 100 : 10, message: 'sample', options: {}, result: {}, ...state.extra });
  await page.route('**/*', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === prefix + '/latest') return route.fulfill({ json: job() });
    if (pathname === prefix + '/sample') {
      state.polls++;
      if (state.polls === 1 && failure) return route.fulfill({ status: failure, json: { error: 'sample failure' } });
      state.status = state.nextStatus || 'completed';
      return route.fulfill({ json: job() });
    }
    if (pathname === '/api/rules/library') return route.fulfill({ json: { documents: [], rules: [], formulas: [], stats: {} } });
    if (pathname === '/api/documents') return route.fulfill({ json: { rows: [], count: 0 } });
    if (pathname === '/api/status') return route.fulfill({ json: { parsed: {} } });
    if (pathname.startsWith('/web/')) {
      const file = path.basename(pathname);
      const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      return route.fulfill({ contentType, body: await readFile(path.join(__dirname, '../../web', file)) });
    }
    return route.fulfill({ status: 404, body: 'Not found' });
  });
  await page.goto(`http://slopekg.test/web/${kind === 'rules' ? 'rule-sources' : 'pdf-ingestion'}.html`);
  return { page, state };
}

test('a temporary rules status failure keeps the task busy and retries', async t => {
  const { page, state } = await jobPage(t, 'rules', 500);
  await page.waitForFunction(() => document.querySelector('#ruleJobMessage').textContent.includes('读取'));
  assert.equal(await page.locator('#runRuleExtraction').isDisabled(), true);
  await page.waitForFunction(() => !document.querySelector('#runRuleExtraction').disabled);
  assert.ok(state.polls >= 2);
});

test('a missing rules task explains the lost record and releases the button', async t => {
  const { page, state } = await jobPage(t, 'rules', 404);
  await page.waitForFunction(() => document.querySelector('#ruleJobMessage').textContent.includes('任务记录不存在'));
  assert.ok(state.polls >= 1);
  assert.equal(await page.locator('#runRuleExtraction').isDisabled(), false);
  assert.match(await page.locator('#ruleJobMessage').textContent(), /任务记录不存在/);
});

test('an existing PDF job disables every processing action', async t => {
  const { page } = await jobPage(t, 'pdf', 0);
  await page.waitForFunction(() => document.querySelector('#jobStatus').textContent === '处理中');
  for (const id of ['rerunBtn', 'reextractBtn', 'deepRunBtn']) assert.equal(await page.locator('#' + id).isDisabled(), true);
});

test('a missing PDF task stops polling and allows processing again', async t => {
  const { page } = await jobPage(t, 'pdf', 404);
  await page.waitForFunction(() => document.querySelector('#jobStatus').textContent === '失败', null, { timeout: 5000 });
  assert.match(await page.locator('#jobError').textContent(), /任务记录不存在/);
  for (const id of ['rerunBtn', 'reextractBtn', 'deepRunBtn']) assert.equal(await page.locator('#' + id).isDisabled(), false);
});

test('PDF upload sends the selected settings including zero OCR pages', async t => {
  const { page, state } = await jobPage(t, 'pdf', 0, 'none');
  let selected;
  await page.route('**/api/documents/upload', async route => {
    const body = route.request().postData();
    const part = body.split('name="options"')[1];
    assert.ok(part, 'settings must be present in multipart upload');
    selected = JSON.parse(part.split('\r\n\r\n')[1].split('\r\n')[0]);
    state.status = 'completed';
    return route.fulfill({ status: 201, json: { count: 1, job: { id: 'sample', status: 'completed', progress: 100, options: selected } } });
  });
  await page.locator('#ocrPages').fill('0');
  await page.locator('#ocrDevice').selectOption('cpu');
  await page.locator('#ocrWorkers').fill('2');
  await page.locator('#ocrBatchSize').fill('3');
  await page.locator('#forceOcr').check();
  await page.locator('#pdfInput').setInputFiles({ name: 'sample.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7 sample') });
  await page.locator('#uploadOnlyBtn').click();
  await page.waitForFunction(() => document.querySelector('#jobStatus').textContent === '已完成');
  assert.deepEqual([selected.ocr_pages, selected.ocr_device, selected.ocr_workers, selected.ocr_batch_size, selected.force_ocr], [0, 'cpu', 2, 3, true]);
});

test('partial deep processing stops polling and shows skipped coverage', async t => {
  const { page, state } = await jobPage(t, 'pdf', 0);
  state.nextStatus = 'partial';
  state.extra = { stage: 'semantic_partial', message: '基础结果可用，深度未完成', result: { llm: { run: { candidates: 2, passed: 1, skipped_count: 3 } } } };
  await page.waitForFunction(() => document.querySelector('#jobStatus').textContent === '部分完成');
  assert.match(await page.locator('#jobDetails').textContent(), /3 个对象/);
  assert.equal(await page.locator('#deepRunBtn').isDisabled(), false);
  const polls = state.polls;
  await page.waitForTimeout(1100);
  assert.equal(state.polls, polls);
});

test('PDF evidence links carry only valid physical page numbers', async t => {
  const page = await browser.newPage();
  t.after(() => page.close());
  const source = await readFile(path.join(__dirname, '../../web/app.js'), 'utf8');
  await page.addScriptTag({ content: source.slice(0, source.lastIndexOf('main().catch')) });
  const links = await page.evaluate(() => [49, '50', -1, 1.5, null].map(page => {
    const box = document.createElement('div');
    box.innerHTML = evidenceItem({ source_file: '边坡 设计.pdf', page, kind: 'source', text: '原文' });
    return box.querySelector('a').getAttribute('href');
  }));
  assert.match(links[0], /#page=49$/); assert.match(links[1], /#page=50$/);
  for (const href of links.slice(2)) assert.doesNotMatch(href, /#page=/);
  assert.ok(links[0].includes(encodeURIComponent('边坡 设计.pdf')));
});

test('knowledge pages can be selected per document without changing automatic defaults', async t => {
  const { page } = await jobPage(t, 'rules', 0, 'none');
  await page.route('**/api/rules/library', route => route.fulfill({ json: {
    documents: [{id:'reference',title:'匿名分类资料',pages:5,selected_pages:[],processing_status:'cataloged_only'}],
    rules:[], formulas:[], stats:{}
  }}));
  await page.reload();
  await page.locator('[data-page-selection="reference"]').fill('1-3,5');
  await page.locator('#ruleIncludeUnselected').check();
  let options;
  await page.route('**/api/rules/jobs', async route => {
    options=route.request().postDataJSON();
    return route.fulfill({status:202,json:{id:'selected',status:'completed',progress:100,message:'done'}});
  });
  await page.locator('#runRuleExtraction').click();
  await page.waitForFunction(()=>document.querySelector('#runRuleExtraction').disabled===false);
  assert.deepEqual(options.page_selection,{reference:'1-3,5'});
  assert.equal(options.include_unselected,true);
});

test('legacy artifacts are not presented as matching the running implementation', async t => {
  const { page } = await jobPage(t, 'pdf', 0, 'none');
  await page.route('**/api/status', route => route.fulfill({json:{version_status:'unversioned',updated_at:'2020-01-01',parsed:{}}}));
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('#artifactVersion').textContent.includes('旧成果'));
  assert.match(await page.locator('#artifactVersion').textContent(),/2020-01-01/);
});
