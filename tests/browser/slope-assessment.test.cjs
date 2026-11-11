const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { setTimeout: sleep } = require('node:timers/promises');
const { test } = require('node:test');
const { JSDOM, VirtualConsole } = require('jsdom');

const web = join(__dirname, '../../web');
const read = file => readFileSync(join(web, file), 'utf8');
const page = '/web/slope-assessment.html';
const scope = (date = '2026-11-09', code = 'natural') => ({
  subject: { source_object_ref: 's1', object_kind: 'landslide_body' },
  time: { kind: 'instant', start: date, end: date, raw: date },
  scenario: { code, raw: code === 'natural' ? '天然工况' : '暴雨工况' },
});

const number = value => ({ kind: 'number', value, unit: 'person', qualifier: 'exact_as_reported' });
const source = (id, options = {}) => ({
  case_id: id, label: `原文对象 ${id}`, workspace: 'debug', supported: true, slope_id: null,
  scope: scope(), document_names: [`${id}.pdf`], resolution_version: 1,
  detail_url: `/web/assessment-debug.html?case_id=${id}`, ...options,
});
const slope = { id: 's1', label: '模拟滑坡 A', kind: 'registered', supported: true, source_count: 0, case_count: 0 };
const operator = { actor: '模拟经办人', reason: '原件桩号、侧别和调查日期一致' };

async function waitFor(fn) {
  for (let index = 0; index < 150; index++) {
    if (fn()) return;
    await sleep(8);
  }
  throw new Error('未观察到预期的页面状态');
}

function ui(t, suffix, handler) {
  const faults = [];
  const console = new VirtualConsole();
  console.on('jsdomError', error => faults.push(error.message));
  const dom = new JSDOM(read('slope-assessment.html'), {
    runScripts: 'outside-only', url: `http://slopekg.test${page}${suffix}`, virtualConsole: console,
  });
  const { window } = dom;
  const { document } = window;
  const requests = [];
  window.HTMLElement.prototype.scrollIntoView = () => {};
  if (!window.HTMLDialogElement.prototype.showModal) {
    window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    window.HTMLDialogElement.prototype.close = function () { this.open = false; };
  }
  window.fetch = async (url, options) => {
    const target = new URL(url, window.location.href);
    const operation = target.pathname.split('/').at(-1);
    const method = options.method;
    const body = options.body && JSON.parse(options.body);
    requests.push({ method, operation, query: Object.fromEntries(target.searchParams), body });
    const result = handler(requests.at(-1));
    return { ok: (result?.status || 200) < 400, status: result?.status || 200,
      json: async () => structuredClone(result?.payload ?? result) };
  };
  const find = selector => document.querySelector(selector);
  const set = (selector, value) => {
    const input = find(selector);
    assert.ok(input, selector);
    input.value = value;
    input.dispatchEvent(new window.Event('change', { bubbles: true }));
  };
  function click(selector) {
    const button = find(selector);
    assert.ok(button, selector);
    button.click();
  }
  function submit() {
    set('[name=actor]', operator.actor);
    set('[name=reason]', operator.reason);
    click('#wsDialogSubmit');
  }
  const settled = () => waitFor(() => find('#wsDossier').getAttribute('aria-busy') === 'false');
  for (const file of ['navigation.js', 'api.js', 'slope-assessment.js']) window.eval(read(file));
  t.after(() => { window.close(); assert.deepEqual(faults, []); });
  return { window, find, set, click, submit, requests, settled };
}

test('同一档案关联两份同期资料，只合用本期同工况的已选来源', async t => {
  const origin = [source('地质'), source('巡查'), source('旧资料', { slope_id: 's1', scope: scope('2026-10-09') }),
    source('已归档模拟', { archived: true })];
  const unified = { case_id: 'composed', workspace: 'workspace', label: slope.label, scope: scope(), resolution_version: 1 };
  let created = false;
  const panel = ui(t, '?source_case=地质&source_workspace=debug', ({ operation, body }) => {
    const sources = origin.filter(item => item.slope_id === slope.id);
    if (operation === 'catalog') return { slopes: [slope], source_cases: origin, test_only: true };
    if (operation === 'slope') return { slope, sources, cases: created ? [unified, ...sources] : sources, test_only: true };
    if (operation === 'link') {
      const linked = origin.find(item => item.case_id === body.case_id);
      Object.assign(linked, { slope_id: slope.id, link: { action: 'link', link_id: `link_${body.case_id}` } });
      return linked.link;
    }
    if (operation === 'create') {
      created = true;
      return { case: { ...unified, workspace_slope_id: slope.id }, facts: [], results: [], contexts: [], reviews: [], test_only: true };
    }
    throw new Error(`Unexpected request ${operation}`);
  });
  await panel.settled();
  assert.equal(panel.find('#wsTestBanner').hidden, false);
  panel.set('#wsSlopeSelect', slope.id);
  await panel.settled();
  panel.click('#wsLinkSource');
  assert.equal(panel.find('#wsLinkPicker').value, 'debug:地质');
  assert.doesNotMatch(panel.find('#wsLinkPicker').textContent, /已归档模拟/);
  assert.equal(panel.find('[name=same_object_confirmed]').checked, false);
  panel.submit();
  assert.equal(panel.requests.filter(row => row.operation === 'link').length, 0);
  panel.click('[name=same_object_confirmed]');
  panel.submit();
  await panel.settled();
  assert.equal(panel.requests.find(row => row.operation === 'link').body.same_object_confirmed, true);

  panel.click('#wsLinkSource');
  panel.set('#wsLinkPicker', 'debug:巡查');
  panel.click('[name=same_object_confirmed]');
  panel.submit();
  await panel.settled();
  panel.set('#wsAnchor', 'debug:地质');
  assert.equal(panel.find('[data-source-key="debug:旧资料"]').disabled, true);
  assert.equal(panel.find('[data-source-key="debug:巡查"]').disabled, false);
  panel.click('[data-source-key="debug:巡查"]');
  panel.click('#wsCreate');
  panel.submit();
  await panel.settled();
  const request = panel.requests.find(row => row.operation === 'create').body;
  assert.equal(request.anchor_case_id, '地质');
  assert.deepEqual(request.sources, ['地质', '巡查'].map(case_id => ({ workspace: 'debug', case_id, resolution_version: 1 })));
  assert.match(panel.window.location.search, /case_id=composed/);
  assert.match(panel.find('#wsDossier').textContent, /历次评估/);
  assert.match(panel.find('#wsDossier').textContent, /打开原评估/);
});

function caseFixture() {
  const context = { context_id: 'ctx', source_name: '巡查.pdf', usable_for_evidence: true,
    evidence_variables: ['observations'], spans: [
      { file_page_1_based: 1, quote: '巡查对象为模拟滑坡 A。', page_url: '/api/slope-assessment/evidence?page=1' },
      { file_page_1_based: 2, quote: '本期受威胁人数为20人。<script>错误标签</script>', page_url: '/api/slope-assessment/evidence?page=2' },
    ] };
  const result = (version, factId, review) => ({ result_id: `r${version}`, resolution_version: version, is_current: true,
    table17_result: { grade: factId ? '危险性中等' : null }, review_status: review ? 'accepted' : 'pending',
    workflow_status: review ? 'reviewed' : factId ? 'ready_for_review' : 'needs_evidence', conflicts: [], minimal_missing: [],
    resolved_variables: factId ? { threatened_people: { selected_fact_id: factId, candidate_fact_ids: [factId] } } : {},
  });
  return { case: { case_id: 'c1', workspace_slope_id: slope.id, scope: scope(), resolution_version: 1 },
    results: [result(1)], facts: [], contexts: [context], reviews: [], history_facts: [], test_only: true, result };
}

test('补证、纠错和复核提交原页与版本，历史保留原值', async t => {
  const fixture = caseFixture();
  const makeFact = (id, value) => ({ fact_id: id, scope: scope(), variable_code: 'threatened_people',
    value, origin_kind: 'manual_transcription', evidence_refs: ['ctx'] });
  const panel = ui(t, '?slope_id=s1&case_id=c1', ({ operation, body }) => {
    if (operation === 'catalog') return { slopes: [slope], source_cases: [] };
    if (operation === 'slope') return { slope, sources: [], cases: [{ ...fixture.case, result: fixture.results.at(-1) }] };
    if (operation === 'case') {
      const { result, ...bundle } = fixture;
      return bundle;
    }
    if (['supplement', 'correct', 'review'].includes(operation)) {
      assert.equal(body.expected_resolution_version, fixture.case.resolution_version);
      const version = ++fixture.case.resolution_version;
      fixture.results.at(-1).is_current = false;
      let id = fixture.facts[0]?.fact_id;
      if (operation !== 'review') {
        id = `f${version}`;
        fixture.facts = [makeFact(id, body.value)];
        fixture.history_facts.push(...fixture.facts);
      }
      fixture.reviews.push({ decision: operation === 'review' ? 'confirm_result' : operation,
        reviewer_ref: body.actor, reason: body.reason, target_id: body.fact_id, replacement_fact_id: id });
      fixture.results.push(fixture.result(version, id, operation === 'review'));
      const { result, ...bundle } = fixture;
      return bundle;
    }
    throw new Error(`Unexpected request ${operation}`);
  });
  await panel.settled();
  assert.equal(panel.find('#wsReview').disabled, true);
  panel.click('#wsSupplement');
  panel.set('[name=variable]', 'threatened_people');
  panel.set('#wsEvidence', '1');
  panel.set('[name=value]', '18');
  assert.match(panel.find('#wsEvidenceProof').textContent, /第 2 页/);
  assert.equal(panel.find('#wsEvidenceProof script'), null);
  panel.submit();
  assert.equal(panel.requests.some(row => row.operation === 'supplement'), false);
  panel.click('#wsPageReviewed');
  panel.submit();
  await panel.settled();
  const supplemented = panel.requests.find(row => row.operation === 'supplement').body;
  assert.deepEqual(supplemented.value, number(18));
  assert.equal(supplemented.evidence_id, 'ctx');
  assert.equal(supplemented.evidence_span_index, 1);
  assert.equal(supplemented.source_page_reviewed, true);

  panel.click('[data-action=correct]');
  panel.set('#wsEvidence', '1');
  panel.set('[name=value]', '20');
  panel.click('#wsPageReviewed');
  panel.submit();
  await panel.settled();
  const corrected = panel.requests.find(row => row.operation === 'correct').body;
  assert.equal(corrected.fact_id, 'f2');
  assert.deepEqual(corrected.value, number(20));
  assert.match(panel.find('#wsCase').textContent, /18 人 → 20 人/);

  panel.set('#wsVersion', 'r2');
  assert.equal(panel.find('#wsSupplement'), null);
  assert.match(panel.find('#wsCase').textContent, /正在查看保存的旧版本/);
  panel.set('#wsVersion', '');
  panel.click('#wsReview');
  assert.equal(panel.find('[name=actor]').value, '');
  assert.equal(panel.find('[name=context_reviewed]').checked, false);
  panel.click('[name=context_reviewed]');
  panel.submit();
  await panel.settled();
  assert.equal(panel.requests.find(row => row.operation === 'review').body.result_id, 'r3');
  assert.match(panel.find('#wsCase').textContent, /已复核（模拟）/);
});

test('版本冲突保留表单，刷新后来源归属已变更的案例只读', async t => {
  const { result, ...fixture } = caseFixture();
  let stale = false;
  const panel = ui(t, '?case_id=c1', ({ operation }) => {
    if (operation === 'catalog') return { slopes: [slope], source_cases: [] };
    if (operation === 'slope') return { slope, sources: [], cases: [] };
    if (operation === 'case') return { ...fixture, source_links_changed: stale };
    if (operation === 'supplement') {
      stale = true;
      return { status: 409, payload: { error: '案例版本已变化，请刷新' } };
    }
    throw new Error(`Unexpected request ${operation}`);
  });
  await panel.settled();
  panel.click('#wsSupplement');
  panel.set('[name=mode]', 'field_observation');
  assert.equal(panel.find('[name=variable] option[value=fs]'), null);
  panel.set('[name=variable]', 'threatened_people');
  panel.set('[name=value]', '12');
  panel.submit();
  await panel.settled();
  assert.match(panel.find('#wsDialogError').textContent, /版本已变化/);
  assert.equal(panel.find('[name=value]').value, '12');
  assert.equal(panel.find('#wsDialogRefresh').hidden, false);
  panel.click('#wsDialogRefresh');
  await panel.settled();
  assert.equal(panel.find('#wsSupplement'), null);
  assert.match(panel.find('#wsCase').textContent, /资料归属已调整/);
});

test('已有图谱档案没有已评估资料时仍能从工作台发起', async t => {
  const graph = { ...slope, kind: 'graph' };
  const { result, ...fixture } = caseFixture();
  const panel = ui(t, '?slope_id=s1', ({ operation, body }) => {
    if (operation === 'catalog') return { slopes: [graph], source_cases: [] };
    if (operation === 'slope') return { slope: graph, sources: [], cases: [] };
    if (operation === 'create') {
      assert.deepEqual(body.sources, []);
      return fixture;
    }
    throw new Error(`Unexpected request ${operation}`);
  });
  await panel.settled();
  assert.match(panel.find('#wsCreate').textContent, /已有档案/);
  panel.click('#wsCreate');
  panel.submit();
  await panel.settled();
  assert.equal(panel.find('#wsCaseTitle').textContent, '本次评估');
});
