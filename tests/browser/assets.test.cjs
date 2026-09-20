const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
const path = require('node:path');
const { before, after, test } = require('node:test');
const { chromium } = require('playwright');

// Requires Playwright and an installed browser; no running app or project data needed.
let browser;
before(async () => {
  browser = await chromium.launch({
    headless: true,
    channel: process.env.SLOPEKG_BROWSER_CHANNEL || (process.platform === 'win32' ? 'msedge' : undefined),
  });
});
after(async () => { await browser?.close(); });

async function gallery(t) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => {
    await page.close();
    assert.deepEqual(errors, [], 'no browser script errors');
  });
  const state = {
    assets: Array.from({ length: 60 }, (_, index) => ({
      id: `image-${index}`, asset_type: 'VisualAsset', file_name: `image-${index}.png`,
      source_document_id: 'doc-a', source_page: 83, subtype: 'drawing_title_block',
      validation_status: 'valid', slope_review_status: 'pending',
    })).concat([{
      id: 'other', asset_type: 'VisualAsset', file_name: 'other.png',
      source_document_id: 'doc-b', source_page: 2, subtype: 'pdf_full_page',
      validation_status: 'valid', slope_review_status: 'pending',
    }]),
    beforeAssets: async () => {},
    fail: false,
  };
  const documents = [
    { id: 'doc-a', file_name: 'G209施工图.pdf', path: 'data/rawPDF/G209施工图.pdf' },
    { id: 'doc-b', file_name: '调查资料.pdf', path: 'data/rawPDF/调查资料.pdf' },
  ];
  // Every request is intercepted, so fixtures never reach external services.
  await page.route('**/*', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/multimodal/assets') {
      await state.beforeAssets();
      return route.fulfill(state.fail ? { status: 500, body: 'failure' } : { json: { assets: state.assets } });
    }
    if (pathname === '/api/multimodal/quality') return route.fulfill({ json: { issues: [] } });
    if (pathname === '/api/parsed/documents') return route.fulfill({ json: { rows: documents } });
    const file = pathname.slice('/web/'.length);
    if (pathname.startsWith('/web/') && ['assets.html', 'assets.js', 'api.js', 'assets.css', 'pages.css'].includes(file)) {
      const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      return route.fulfill({ contentType, body: await readFile(path.join(__dirname, '../../web', file)) });
    }
    return route.fulfill({ status: 404, body: 'Not found' });
  });
  await page.goto('http://slopekg.test/web/assets.html');
  await page.waitForFunction(() => document.querySelectorAll('.asset-card').length === 24);
  return { page, state };
}

async function refresh(page) {
  await page.locator('#reloadAssets').click();
  await page.waitForFunction(() => !document.querySelector('#reloadAssets').disabled);
}

test('refresh keeps all active filters', async t => {
  const { page } = await gallery(t);
  await page.selectOption('#assetDocument', 'doc-a');
  await page.selectOption('#assetType', 'drawing_title_block');
  await page.fill('#assetPage', '83');
  await page.selectOption('#assetStatus', 'pending');
  await page.fill('#assetSearch', 'image');
  await refresh(page);
  for (const [id, value] of Object.entries({ assetDocument: 'doc-a', assetType: 'drawing_title_block', assetPage: '83', assetStatus: 'pending', assetSearch: 'image' })) {
    assert.equal(await page.inputValue(`#${id}`), value, id);
  }
  assert.match(await page.textContent('#assetCount'), /60/);
});

test('refresh keeps the current page', async t => {
  const { page } = await gallery(t);
  await page.click('#assetNext');
  assert.equal(await page.textContent('#assetPagination'), '2 / 3');
  await refresh(page);
  assert.equal(await page.textContent('#assetPagination'), '2 / 3');
  assert.equal(await page.locator('.asset-file').first().textContent(), 'image-24.png');
});

test('search finds the displayed source document name when asset title is missing', async t => {
  const { page } = await gallery(t);
  assert.equal(await page.locator('.asset-card h2').first().textContent(), 'G209施工图.pdf');
  await page.fill('#assetSearch', ' g209施工图 ');
  assert.match(await page.textContent('#assetCount'), /60/);
  assert.equal(await page.locator('.asset-card').count(), 24);
});

test('removed document/type selections reset to all without a hidden invalid filter', async t => {
  const { page, state } = await gallery(t);
  await page.selectOption('#assetDocument', 'doc-a');
  await page.selectOption('#assetType', 'drawing_title_block');
  state.assets = state.assets.filter(asset => asset.source_document_id === 'doc-b');
  await refresh(page);
  assert.equal(await page.inputValue('#assetDocument'), '');
  assert.equal(await page.inputValue('#assetType'), '');
  assert.equal(await page.textContent('#assetPagination'), '1 / 1');
  assert.equal(await page.locator('.asset-card').count(), 1);
});

test('refresh clamps to the last remaining page when the catalog shrinks', async t => {
  const { page, state } = await gallery(t);
  await page.click('#assetNext');
  await page.click('#assetNext');
  state.assets = state.assets.slice(0, 30);
  await refresh(page);
  assert.equal(await page.textContent('#assetPagination'), '2 / 2');
  assert.equal(await page.locator('.asset-card').count(), 6);
});

test('a filter changed while refreshing is not overwritten by an older selection', async t => {
  const { page, state } = await gallery(t);
  await page.selectOption('#assetDocument', 'doc-a');
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  state.beforeAssets = () => pending;
  try {
    await page.click('#reloadAssets');
    await page.selectOption('#assetDocument', 'doc-b');
  } finally { release(); }
  await page.waitForFunction(() => !document.querySelector('#reloadAssets').disabled);
  assert.equal(await page.inputValue('#assetDocument'), 'doc-b');
  assert.equal(await page.locator('.asset-card').count(), 1);
});

test('a failed refresh clears stale cards and retry restores the selected document', async t => {
  const { page, state } = await gallery(t);
  await page.selectOption('#assetDocument', 'doc-b');
  state.fail = true;
  await refresh(page);
  assert.match(await page.textContent('#assetSummary'), /加载失败/);
  assert.equal(await page.locator('.asset-card').count(), 0);
  state.fail = false;
  await refresh(page);
  assert.equal(await page.inputValue('#assetDocument'), 'doc-b');
  assert.equal(await page.locator('.asset-card').count(), 1);
});
