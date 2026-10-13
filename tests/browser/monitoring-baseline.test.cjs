const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { before, after, test } = require('node:test');
const { chromium } = require('playwright');
let browser;
before(async () => { browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined }); });
after(async () => { await browser?.close(); });
const clone = obj => JSON.parse(JSON.stringify(obj));
function fixture() {
  const predictions = [11, 14].map(day => ({ target_day: `2026-01-${day}`, origin_day: `2026-01-${day - 1}`, split: 'test', target_id: 'o' + day,
    actual: 0, predictions: { persistence: 0, recent_drift: 0 } }));
  const methods = { persistence: { count: 2, mae: 0, rmse: 0, bias: 0, mase: null }, recent_drift: { count: 2, mae: 0, rmse: 0, bias: 0, mase: null } };
  const component = { component: 1, flags: ['all_zero_training_values_not_proof_of_sensor_health'], split: { train_end: '2026-01-06', validation_end: '2026-01-10', end: '2026-01-15' },
    predictions, metrics: { test: { methods, coverage: { calendar_days: 5, valid_target_records: 2, common_evaluable: 2, persistence_evaluable: 2 }, drift_mae_improvement_fraction: null } } };
  return { risk_eligible: false, deployment_ready: false, boundary: '原值离线实验，不是风险预警', run_id: 'sample', sources: [{ id: 'series1', sha256: 'abc' }],
    series: [{ series_id: 'series1', file_name: '<img src=x onerror=alert(1)>', category: '地表位移', unit: null, components: [component, { ...clone(component), component: 2 }] }],
    source_references: { o11: { source: { sheet: '<img src=x>', row: 12, cells: { 原值: 'D12' } } }, o14: { source: { sheet: '记录', row: 15, cells: { 原值: 'D15' } } } } };
}
async function open(t, state = {}, offline = false) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  state.payload ||= fixture(); state.calls = 0;
  if (offline) await page.addInitScript(payload => { window.BASELINE_OFFLINE = payload; }, state.payload);
  await page.route('**/*', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/monitoring/baseline') {
      state.calls++;
      return route.fulfill({ status: state.fail ? 409 : 200, json: state.fail ? { error: '所选来源已变化，请重跑' } : state.payload });
    }
    if (pathname.startsWith('/web/')) {
      const file = path.basename(pathname);
      return route.fulfill({ contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html', body: await readFile(path.join(__dirname, '../../web', file)) });
    }
    return route.fulfill({ status: 404, body: 'Not found' });
  });
  await page.goto('http://slopekg.test/web/monitoring-baseline.html');
  return { page, state };
}

test('raw zero metrics stay zero and untrusted source labels are escaped', async t => {
  const { page } = await open(t);
  await page.waitForFunction(() => document.querySelectorAll('#baselineMetrics tr').length === 2);
  assert.equal(await page.locator('#baselineMetrics tr').first().locator('td').nth(2).textContent(), '0');
  assert.equal(await page.locator('#baselineMetrics tr').first().locator('td').nth(5).textContent(), '无定义');
  assert.equal(await page.locator('#baselineProtocol img, #baselineRows img').count(), 0);
  assert.match(await page.locator('#baselineProtocol').textContent(), /不能证明传感器正常/);
});
test('a calendar gap breaks all three curves instead of compressing missing dates', async t => {
  const { page } = await open(t);
  await page.locator('#baselineChart svg').waitFor();
  const paths = await page.locator('#baselineChart path').evaluateAll(rows => rows.map(r => r.getAttribute('d')));
  assert.equal(paths.length, 3);
  for (const d of paths) { assert.equal((d.match(/M/g) || []).length, 2); assert.equal((d.match(/L/g) || []).length, 0); }
  assert.equal(await page.locator('#baselineChart circle').count(), 6);
});
test('failed reload clears old scores and source link, retry preserves the chosen component', async t => {
  const { page, state } = await open(t);
  await page.locator('#baselineChart svg').waitFor();
  await page.locator('#baselineComponent').selectOption('1');
  state.fail = true;
  await page.locator('#baselineReload').click();
  await page.waitForFunction(() => !document.querySelector('#baselineError').hidden);
  assert.match(await page.locator('#baselineError').textContent(), /来源已变化/);
  assert.equal(await page.locator('#baselineMetrics tr').count(), 0);
  assert.equal(await page.locator('#baselineChart svg').count(), 0);
  assert.equal(await page.locator('#baselineSource').isVisible(), false);
  state.fail = false;
  await page.locator('#baselineReload').click();
  await page.locator('#baselineChart svg').waitFor();
  assert.equal(await page.locator('#baselineComponent').inputValue(), '1');
  assert.equal(await page.locator('#baselineError').isVisible(), false);
});
test('missing experiment and inappropriate risk claims never display successful metrics', async t => {
  const state = { payload: { status: 'not_generated', series: [] } };
  const { page } = await open(t, state);
  await page.waitForFunction(() => document.querySelector('#baselineStatus').textContent.includes('尚未生成'));
  assert.equal(await page.locator('#baselineComponent').isDisabled(), true);
  state.payload = fixture(); state.payload.deployment_ready = true;
  await page.locator('#baselineReload').click();
  await page.waitForFunction(() => !document.querySelector('#baselineError').hidden);
  assert.equal(await page.locator('#baselineMetrics tr').count(), 0);
  assert.match(await page.locator('#baselineError').textContent(), /边界不一致/);
});
test('offline mode performs no API request and hides network-only navigation and download', async t => {
  const { page, state } = await open(t, {}, true);
  await page.locator('#baselineChart svg').waitFor();
  assert.equal(state.calls, 0);
  assert.equal(await page.locator('.site-header').isVisible(), false);
  assert.equal(await page.locator('#baselineSource').isVisible(), false);
  assert.equal(await page.locator('#baselineReload').isVisible(), false);
});
test('full source checksums remain inside the narrow viewport', async t => {
  const payload = fixture(); payload.sources[0].sha256 = 'a'.repeat(64);
  const { page } = await open(t, { payload });
  await page.locator('#baselineChart svg').waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
});
