const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { before, after, test } = require('node:test');
const { chromium } = require('playwright');
let browser;
before(async () => { browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined }); });
after(async () => { await browser?.close(); });

async function portalPage(t, payloads) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  await page.route('**/*', async route => {
    const pathname = new URL(route.request().url()).pathname;
    const payload = payloads[pathname];
    if (payload) return route.fulfill({ json: typeof payload === 'function' ? payload(route.request()) : payload });
    if (pathname.startsWith('/web/')) {
      const file = path.basename(pathname);
      return route.fulfill({ contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html',
        body: await readFile(path.join(__dirname, '../../web', file)) });
    }
    return route.fulfill({ status: 404, body: 'Not found' });
  });
  return page;
}

test('dossier shows data coverage and refreshes after manual entry', async t => {
  const slope = { id: 's1', label: 'G344 梅子沟', props: { slope_id: 'G344-1' } };
  const fields = [
    { code: 'start_coordinate', label_zh: '起点坐标', status: 'missing', requirement: 'risk_required' },
    { code: 'slope_height_m', label_zh: '坡高', status: 'missing', requirement: 'risk_required' },
    { code: 'deformation_observation', label_zh: '近期变形巡检', status: 'interface_reserved', requirement: 'risk_required' },
    { code: 'risk_rules', label_zh: '已发布风险规则', status: 'interface_reserved', requirement: 'risk_required' },
  ];
  const report = { slope_id: slope.id, fields, risk_data_completeness: 0 };
  let record = { fields: {} };
  const page = await portalPage(t, {
    '/api/slopes': { rows: [slope] },
    '/api/completeness': { reports: [report] },
    '/api/slopes/s1': () => ({ slope, completeness: report }),
    '/api/manual/schema': { slope_fields: [
      { code: 'slope_height_m', label: '坡高（m）', type: 'number' },
      { code: 'deformation_observation', label: '近期变形巡检', type: 'text', dynamic: true },
    ] },
    '/api/manual/slopes/s1': request => {
      if (request.method() === 'GET') return record;
      record = request.postDataJSON();
      slope.props.slope_height_raw = '30m';
      fields[1].status = 'available';
      fields[2].status = 'available';
      return { status: 'saved', record, completeness: report };
    },
  });
  await page.goto('http://slopekg.test/web/slopes.html?slope_id=s1');
  const row = page.locator('#slopeRows tr');
  const detail = page.locator('#slopeDetail');
  await detail.getByText('静态资料 · 已有 0/2').waitFor();
  assert.equal(await row.getByRole('button', { name: 'G344 梅子沟' }).count(), 1);
  assert.equal(await row.getByText('系统编号 G344-1').count(), 1);
  assert.equal(await detail.getByRole('heading', { name: 'G344 梅子沟' }).count(), 1);
  assert.deepEqual(await row.locator('td').allTextContents().then(cells => cells.slice(-2)), ['0/2', '0/1']);
  assert.equal(await page.getByText('风险规则发布状态').count(), 0);
  assert.doesNotMatch(await row.textContent(), /%|未就绪/);
  await detail.getByRole('button', { name: '人工录入' }).click();
  await detail.getByLabel('坡高（m）').fill('30');
  await detail.getByLabel('近期变形巡检').fill('坡脚渗水');
  await detail.getByLabel('观测日期').fill('2026-11-10');
  await detail.getByRole('button', { name: '保存并更新结果' }).click();
  await detail.getByText('已保存人工记录，档案已更新。').waitFor();
  assert.deepEqual(record.fields, { slope_height_m: 30, deformation_observation: '坡脚渗水' });
  assert.equal(record.observed_at, '2026-11-10');
  assert.deepEqual(await row.locator('td').allTextContents().then(cells => cells.slice(-2)), ['1/2', '1/1']);
  assert.match(await detail.textContent(), /坡高：30m/);
});

test('workbench distinguishes unknown conditions and keeps excluded original evidence visible', async t => {
  const scope = {
    time: { kind: 'report_period', start: '2021-06-16', end: '2021-07-05', raw: '报告共同外业时段' },
    scenario: { code: 'unspecified', raw: '报告现状资料；各计算工况另列' },
  };
  const slope = { id: 's1', label: 'G344 梅子沟', kind: 'graph' };
  const source = { case_id: 'old', label: 'G344 勘察报告', workspace: 'main', scope };
  const result = { result_id: 'r1', is_current: true, workflow_status: 'needs_evidence', review_status: 'pending' };
  const reference = { source_case_id: source.case_id, variable_code: 'observations', scope,
    status: 'excluded', reasons: ['scenario_unresolved'], evidence_refs: ['e1'] };
  const page = await portalPage(t, {
    '/api/slope-assessment/catalog': { slopes: [slope] },
    '/api/slope-assessment/slope': { slope, sources: [source, { ...source, case_id: 'old2' }], cases: [] },
    '/api/slope-assessment/case': {
      case: { case_id: 'c1', workspace_slope_id: slope.id, scope, resolution_version: 1, workspace_sources: [source],
        workspace_fact_trace: [reference] },
      facts: [], results: [result], contexts: [{
        context_id: 'e1', source_name: 'G344 勘察报告.pdf', usable_for_evidence: false,
        spans: [{ file_page_1_based: 4, quote: '原文中的历史观测记录', page_url: '/api/slope-assessment/evidence?page=4' }],
      }],
    },
  });
  await page.goto('http://slopekg.test/web/slope-assessment.html?slope_id=s1&case_id=c1');
  const detail = page.locator('#wsCase');
  await detail.getByText('缺少可采用的证据').waitFor();
  assert.match(await detail.textContent(), /2021-06-16 至 2021-07-05.*工况未明确/s);
  assert.doesNotMatch(await detail.textContent(), /时期未明确/);
  assert.match(await detail.textContent(), /已有来源记录 1 条 · 通过组证检查 0 条 · 待核 0 条 · 未纳入 1 条/);
  assert.equal(await detail.getByRole('button', { name: '补充证据' }).isDisabled(), true);
  assert.equal(await detail.getByRole('link', { name: '查看或关联资料' }).getAttribute('href'), '#wsSources');
  assert.doesNotMatch(await page.locator('#wsSources').textContent(), /时期或工况不同/);
  await page.getByText('未纳入本次评估的来源记录 · 1 条').click();
  await page.getByText('查看来源原文').click();
  await page.getByText('原文中的历史观测记录').waitFor();
  assert.equal(await page.getByRole('link', { name: '查看原页' }).getAttribute('href'), '/api/slope-assessment/evidence?page=4');
});

test('overview shows the two graph metrics and keeps completeness on the data page', async t => {
  const page = await portalPage(t, {
    '/api/status': { graph: { nodes: 1451, node_type_counts: { Slope: 63 } } },
    '/api/monitoring/catalog': { series: [] },
    '/api/completeness': { summary: { slopes: 63, average_risk_data_completeness: 0.356 }, reports: [] },
    '/api/interfaces': { interfaces: Array.from({ length: 4 }, (_, i) => ({ path: '/api/pending-' + i, status: 'reserved_not_connected' })) },
    '/api/evaluation': {},
  });
  await page.goto('http://slopekg.test/web/index.html');
  const overview = page.locator('#dashboardMetrics');
  await overview.getByText('候选边坡').waitFor();
  assert.deepEqual(await overview.locator('.metric-card span').allTextContents(), ['候选边坡', '图谱节点']);
  assert.deepEqual(await overview.locator('.metric-card strong').allTextContents(), ['63', '1451']);

  await page.locator('.app-more summary').click();
  assert.equal(await page.locator('.app-more').getByRole('link', { name: '智能识别' }).count(), 0);
  await page.locator('.app-more').getByRole('link', { name: '数据与接口' }).click();
  const detail = page.locator('#statusMetrics');
  await detail.getByText('平均完整度').waitFor();
  assert.match(await detail.textContent(), /36%平均完整度/);
  assert.match(await detail.textContent(), /4待接入\/待确认接口/);
});

test('all five recognition methods share names, selection and the fifth route task return', async t => {
  const page = await portalPage(t, {
    '/api/status': {},
    '/api/documents': { rows: [] },
    '/api/pipeline/jobs/latest': { status: 'none' },
    '/api/recognition/documents': { documents: [] },
    '/api/recognition/profiles': { profiles: [] },
    '/api/recognition': { rows: [] },
    '/api/knowledge-recognition': { rows: [] },
    '/api/document-recognition': { rows: [] },
    '/api/agent-recognition/runs': { latest_id: 'other-task', runs: [] },
    '/api/agent-recognition': { run_id: 'saved-task', processing_status: 'completed', documents: [{ name: '已保存资料.pdf' }], rows: [] },
    '/api/agent-recognition/workspace': {},
  });
  await page.goto('http://slopekg.test/web/index.html');
  await page.locator('.app-nav').getByRole('link', { name: '识别方法', exact: true }).click();
  const names = ['基读 Basic', '知联 Knowledge（暂时归档）', '视读 Visual（暂时归档）', '整读 Document', '智能识别'];
  const methods = page.locator('.method-nav');
  const method = name => methods.getByRole('link', { name: names.find(label => label.startsWith(name)), exact: true });
  assert.equal(await method('基读 Basic').getAttribute('aria-current'), 'page');
  await page.evaluate(() => sessionStorage.setItem('slopekg-agent-context', JSON.stringify({
    url: '/web/pdf-ingestion.html?run=saved-task&tab=results',
  })));
  for (const name of ['基读 Basic', '知联 Knowledge', '整读 Document', '智能识别']) {
    await method(name).click();
    assert.deepEqual(await methods.locator('a').allTextContents(), names);
    assert.equal(await method(name).getAttribute('aria-current'), 'page');
  }
  await page.getByRole('heading', { name: '已保存资料' }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get('run'), 'saved-task');
  assert.equal(await page.locator('.app-nav').getByRole('link', { name: '识别方法', exact: true }).getAttribute('aria-current'), 'location');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

  await method('视读 Visual').click();
  assert.equal(await method('视读 Visual').getAttribute('aria-current'), 'page');
  assert.equal(new URL(page.url()).searchParams.get('method'), 'visual');
  const returnTo = new URL(new URL(page.url()).searchParams.get('return_to'), page.url());
  assert.equal(returnTo.searchParams.get('run'), 'saved-task');
  await method('智能识别').click();
  await page.getByRole('heading', { name: '已保存资料' }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get('run'), 'saved-task');
});

for (const [name, counts, label, unfinished] of [
  ['completed queue', { done: 410, failed: 0, pending: 0 }, '已完成', 0],
  ['pending queue', { done: 2, failed: 0, pending: 3 }, '待处理', 3],
  ['failed queue', { done: 2, failed: 1, pending: 0 }, '有失败任务', 0],
  ['missing engine', { done: 0, engine_missing: 2 }, '待处理', 2],
  ['no OCR tasks', {}, '无OCR任务', 0],
]) {
  test('OCR execution status accurately describes ' + name, async t => {
    const page = await portalPage(t, {
      '/api/status': { parsed: { ocr_status_counts: counts } },
      '/api/completeness': { summary: {}, reports: [] },
      '/api/interfaces': { interfaces: [] },
      '/api/evaluation': {},
    });
    await page.goto('http://slopekg.test/web/data-status.html');
    const row = page.locator('#parserStrategyRows .detail-row').last();
    await row.waitFor();
    assert.equal(await row.locator('.badge').textContent(), label);
    assert.match(await row.textContent(), new RegExp('待执行 ' + unfinished));
    assert.doesNotMatch(await row.textContent(), /待审核/);
  });
}
