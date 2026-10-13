const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { before, after, test } = require('node:test');
const { chromium } = require('playwright');
let browser;
before(async () => { browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined }); });
after(async () => { await browser?.close(); });

async function samplePage(t) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
  const dates = ['2026-05-25', '2026-05-26', '2026-05-27'];
  const meta = { id: 's1', category: '雨量', file_name: 'source.xlsx', source_file: 'source.xlsx', sha256: 'sample', device_names: ['test'], unit: null,
    component_names: ['分量1'], row_count: 2, first_time: dates[0], last_time: dates[2], max_interval_seconds: 172800, component_stats: [], invalid_time_rows: 0, invalid_value_rows: 0, duplicate_time_rows: 0 };
  const observation = day => ({ raw_time: day, raw_values: { '单日雨量值': day === dates[0] ? 4 : 0 }, components: [day === dates[0] ? 4 : 0], quality_flags: [],
    source: { sheet: '记录', row: day === dates[0] ? 3 : 2, cells: { '时间': 'C3', '单日雨量值': 'D3' } } });
  const catalog = { site: { label: '会昌', route_candidate: 'G357（候选）' }, series: [meta], files: 1, observations: 2, common_days: 2, boundary: '原始监测 · 字段待核', default_start: dates[0], default_end: dates[2] };
  const state = { failReplay: false, failRaw: false, requests: [] };
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname === '/api/monitoring/catalog') return route.fulfill({ json: catalog });
    if (pathname === '/api/monitoring/graph') return route.fulfill({ json: { nodes: [], edges: [] } });
    if (pathname === '/api/monitoring/replay') {
      if (state.failReplay) return route.fulfill({ status: 500, body: 'failure' });
      const included = dates.filter(d => d >= url.searchParams.get('start') && d <= url.searchParams.get('end'));
      return route.fulfill({ json: { dates: included, start: included[0], end: included.at(-1), aggregation: '每日期最后观测，不插值', series: [{ meta, record_count: 2, points: included.map(day => ({ day, count: day === dates[1] ? 0 : 1, components: day === dates[1] ? null : observation(day).components, last_record: day === dates[1] ? null : observation(day) })) }] } });
    }
    if (pathname === '/api/monitoring/observations') {
      state.requests.push(url.searchParams.get('end'));
      if (state.failRaw) return route.fulfill({ status: 500, body: 'failure' });
      const rows = [dates[0], dates[2]].filter(d => d <= url.searchParams.get('end')).map(observation);
      return route.fulfill({ json: { total: rows.length, rows } });
    }
    if (pathname === '/api/export/monitoring') return route.fulfill({ status: 201, json: { file_name: 'report.html', download_url: '/output/demo/share/report.html' } });
    if (pathname.startsWith('/web/')) {
      const file = path.basename(pathname);
      return route.fulfill({ contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html', body: await readFile(path.join(__dirname, '../../web', file)) });
    }
    return route.fulfill({ status: 404, body: 'not found' });
  });
  await page.goto('http://slopekg.test/web/monitoring.html');
  await page.waitForFunction(() => document.querySelector('#rawCount').textContent.includes('共 2 条'));
  return { page, state };
}

test('calendar gap breaks the SVG curve and a real zero remains plotted', async t => {
  const { page } = await samplePage(t);
  const curve = await page.locator('path[data-component="1"]').getAttribute('d');
  assert.equal((curve.match(/M/g) || []).length, 2);
  assert.equal((curve.match(/L/g) || []).length, 0);
  // A path consisting only of move commands renders no visible data points.
  assert.equal(await page.locator('circle[data-point-component="1"]').count(), 2);
  assert.match(await page.locator('#monitorCharts').textContent(), /期间无记录日：1/);
  assert.match(await page.locator('#monitorCharts').textContent(), /原值 0/);
  assert.match(await page.locator('#monitorRawRows').textContent(), /D3/);
  assert.match(await page.locator('#monitorQualityRows').textContent(), /单位待确认/);
});

test('replay cutoff applies to charts and the server observation request', async t => {
  const { page, state } = await samplePage(t);
  await page.locator('#replaySlider').evaluate(el => { el.value = '0'; el.dispatchEvent(new Event('input')); });
  await page.waitForFunction(() => document.querySelector('#rawCount').textContent.includes('共 1 条'));
  assert.equal(state.requests.at(-1), '2026-05-25');
  assert.equal(await page.locator('#replayDate').textContent(), '2026-05-25');
  assert.doesNotMatch(await page.locator('#monitorCharts').textContent(), /2026-05-27/);
  assert.doesNotMatch(await page.locator('#monitorRawRows').textContent(), /2026-05-27/);
});

test('failed replay clears old data and disables export until recovery', async t => {
  const { page, state } = await samplePage(t);
  state.failReplay = true;
  await page.click('#loadReplay');
  await page.waitForFunction(() => document.querySelector('#monitorError').textContent.includes('读取失败'));
  assert.equal(await page.locator('#monitorCharts').textContent(), '');
  assert.equal(await page.locator('#exportMonitoring').isDisabled(), true);
  state.failReplay = false;
  await page.click('#loadReplay');
  await page.waitForFunction(() => document.querySelector('#rawCount').textContent.includes('共 2 条'));
  assert.equal(await page.locator('#monitorError').isVisible(), false);
});

test('report export returns an explicit downloadable artifact', async t => {
  const { page } = await samplePage(t);
  await page.click('#exportMonitoring');
  await page.waitForSelector('#exportMessage a');
  assert.equal(await page.locator('#exportMessage a').getAttribute('href'), '/output/demo/share/report.html');
});

test('source strings render as text without injecting markup', async t => {
  const { page } = await samplePage(t);
  // Exercise the shared encoder used for every raw cell and filename.
  const encoded = await page.evaluate(() => window.SlopeKGApi.escapeHtml('<img src=x onerror=alert(1)>'));
  assert.equal(encoded, '&lt;img src=x onerror=alert(1)&gt;');
});
