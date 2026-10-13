const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { before, after, test } = require('node:test');
const { chromium } = require('playwright');
let browser;
before(async () => { browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined }); });
after(async () => { await browser?.close(); });

for (const [name, counts, label, unfinished] of [
  ['completed queue', { done: 410, failed: 0, pending: 0 }, '已完成', 0],
  ['pending queue', { done: 2, failed: 0, pending: 3 }, '待处理', 3],
  ['failed queue', { done: 2, failed: 1, pending: 0 }, '有失败任务', 0],
  ['missing engine', { done: 0, engine_missing: 2 }, '待处理', 2],
  ['no OCR tasks', {}, '无OCR任务', 0],
]) {
  test('OCR execution status accurately describes ' + name, async t => {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    t.after(async () => { await page.close(); assert.deepEqual(errors, []); });
    await page.route('**/*', async route => {
      const pathname = new URL(route.request().url()).pathname;
      const payloads = {
        '/api/status': { parsed: { ocr_status_counts: counts } },
        '/api/completeness': { summary: {}, reports: [] },
        '/api/interfaces': { interfaces: [] },
        '/api/evaluation': {},
      };
      if (payloads[pathname]) return route.fulfill({ json: payloads[pathname] });
      if (pathname.startsWith('/web/')) {
        const file = path.basename(pathname);
        return route.fulfill({ contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html',
          body: await readFile(path.join(__dirname, '../../web', file)) });
      }
      return route.fulfill({ status: 404, body: 'Not found' });
    });
    await page.goto('http://slopekg.test/web/data-status.html');
    const row = page.locator('#parserStrategyRows .detail-row').last();
    await row.waitFor();
    assert.equal(await row.locator('.badge').textContent(), label);
    assert.match(await row.textContent(), new RegExp('待执行 ' + unfinished));
    assert.doesNotMatch(await row.textContent(), /待审核/);
  });
}
