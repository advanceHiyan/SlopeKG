(function () {
  'use strict';

  const api = window.SlopeKGApi;
  const e = api.escapeHtml;
  const base = '/api/slope-assessment/';
  const $ = id => document.getElementById(id);
  const list = value => Array.isArray(value) ? value : [];
  const query = new URLSearchParams(location.search);
  const state = {
    catalog: null,
    slopeId: '',
    dossier: null,
    bundle: null,
    prior: null,
    resultId: '',
    anchor: '',
    scenario: '',
    selected: new Set(),
    busy: false,
    action: null,
    sourceHint: query.get('source_case') || '',
    sourceWorkspace: query.get('source_workspace') || 'debug',
  };
  const fields = {
    development: { label: '发育程度', vocabulary: 'gb-landslide-development@2021', options: ['强发育', '中等发育', '弱发育'] },
    harm: { label: '危害程度', vocabulary: 'gb-harm@2021', options: ['危害大', '危害中等', '危害小'] },
    assessment_basis: { label: '灾情或险情', vocabulary: 'assessment-basis@2021', options: ['灾情', '险情'] },
    threatened_people: { label: '受威胁人数', unit: 'person', step: '1' },
    deaths: { label: '死亡人数', unit: 'person', step: '1' },
    potential_loss_wan: { label: '可能直接经济损失', unit: '10k_CNY' },
    direct_loss_wan: { label: '直接经济损失', unit: '10k_CNY' },
    body_mean_angle: { label: '滑体平均坡度', unit: 'deg' },
    fs: { label: '稳定系数 Fs', unit: '1' },
    fst: { label: '允许稳定系数 Fst', unit: '1' },
    observations: { label: '观察记录' },
    geological_context: { label: '地质环境' },
  };
  const unitLabels = { person: '人', '10k_CNY': '万元', deg: '°', '1': '' };
  const workflowLabels = {
    needs_evidence: '缺少可采用的证据',
    needs_resolution: '待处理冲突',
    ready_for_review: '待人工复核',
    reviewed: '已复核',
    invalid_request: '不适用',
  };
  const factLabels = { ...Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.label])),
    hazard_type: '灾害类型', hazard_level: '危险性', stability_axis: '稳定状态', reference: '原报告参考结论' };
  const sourceLabels = {
    ...window.SlopeKGNav.methodLabels,
    manual: '人工录入', manual_transcription: '原文转录', llm: '模型建议', rule: '规则推导',
  };

  function label(key) {
    return factLabels[key] || key || '资料';
  }

  function shownValue(value) {
    if (!value) return '未知';
    if (value.kind === 'enum') return value.label;
    const unit = unitLabels[value.unit] ?? value.unit ?? '';
    if (value.kind === 'number') return `${value.qualifier === 'approximate' ? '约' : ''}${value.value}${unit ? ` ${unit}` : ''}`;
    if (value.kind === 'interval') return `${value.lower_inclusive ? '[' : '('}${value.lower ?? '−∞'}，${value.upper ?? '+∞'}${value.upper_inclusive ? ']' : ')'} ${unit}`.trim();
    if (value.kind === 'boolean') return value.value ? '是' : '否';
    return value.text || '未知';
  }

  function scopeLabels(scope) {
    const time = scope?.time;
    const scenario = scope?.scenario;
    const period = scopePeriod(scope);
    const condition = scopeCondition(scope);
    const dates = period && (time.start === time.end ? time.start : `${time.start} 至 ${time.end}`);
    return [period ? (time.raw?.includes(time.start) && time.raw.includes(time.end) ? time.raw : dates) : '时期未明确',
      condition ? scenario.raw || scenario.label ||
        state.catalog?.scenario_options?.[condition] || condition : '评估条件待确定'];
  }

  function scopeTags(scope) {
    return `<span class="workbench-source-meta">${scopeLabels(scope).map(value => `<span class="badge">${e(value)}</span>`).join('')}</span>`;
  }

  function localUrl(value, prefix) {
    if (!value) return '';
    try {
      const url = new URL(value, location.href);
      return url.origin === location.origin && url.pathname.startsWith(prefix) ? url.pathname + url.search + url.hash : '';
    } catch (_) {
      return '';
    }
  }

  function pageLink(value, title, className = 'button ghost small') {
    const path = localUrl(value, '/web/');
    if (!path) return '';
    return `<a class="${className}" href="${e(window.SlopeKGNav?.withReturn(path) || path)}">${e(title)}</a>`;
  }

  function sourceKey(source) {
    return `${source.workspace}:${source.case_id}`;
  }

  function sourceName(source) {
    const documents = list(source.document_names).filter(Boolean);
    return documents.length ? documents.join('、') : source.document || source.label || '原有边坡档案';
  }

  function usableSource(source) {
    return source.supported !== false && !source.archived;
  }

  function scopePeriod(scope) {
    const { kind, start, end } = scope?.time || {};
    if (!['instant', 'report_period', 'event_period'].includes(kind) || !start || !end || start > end ||
      (kind === 'instant' && start !== end)) return null;
    for (const value of [start, end]) {
      const time = Date.parse(value);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(time) || new Date(time).toISOString().slice(0, 10) !== value) return null;
    }
    return [kind, start, end];
  }

  function scopeCondition(scope) {
    const code = scope?.scenario?.code;
    if (typeof code !== 'string') return '';
    const scenario = code.trim();
    return ['', 'unknown', 'unspecified', 'not_applicable', '未知', '未明确'].includes(scenario.toLowerCase()) ? '' : scenario;
  }

  function scopeKey(scope) {
    const period = scopePeriod(scope);
    const scenario = scopeCondition(scope);
    return period && scenario ? JSON.stringify([...period, scenario]) : '';
  }

  function scopeGaps(scope) {
    return [!scopePeriod(scope) && '时期未明确', !scopeCondition(scope) && '工况未明确'].filter(Boolean);
  }

  function matchingScope(left, right) {
    const key = scopeKey(left?.scope);
    return !!key && key === scopeKey(right?.scope);
  }

  function testOnly() {
    return !!(state.catalog?.test_only || state.dossier?.test_only || state.bundle?.test_only || state.bundle?.case.test_only);
  }

  function demoMode() {
    return testOnly() && query.get('demo') === '1';
  }

  function scenarioOptions(selected) {
    return Object.entries(state.catalog?.scenario_options || {}).map(([code, name]) =>
      `<option value="${e(code)}" ${selected === code ? 'selected' : ''}>${e(name)}</option>`).join('');
  }

  function status(message, error = false) {
    const notice = $('wsNotice');
    notice.hidden = !message;
    notice.classList.toggle('error', error);
    notice.classList.toggle('info', !error);
    notice.textContent = message || '';
  }

  function caseUrl() {
    const url = new URL(location.href);
    for (const name of ['slope_id', 'case_id', 'history_case', 'source_case', 'source_workspace']) url.searchParams.delete(name);
    if (state.slopeId) url.searchParams.set('slope_id', state.slopeId);
    if (state.bundle) url.searchParams.set('case_id', state.bundle.case.case_id);
    if (state.prior) url.searchParams.set('history_case', state.prior.case.case_id);
    if (state.sourceHint) {
      url.searchParams.set('source_case', state.sourceHint);
      url.searchParams.set('source_workspace', state.sourceWorkspace);
    }
    return url.pathname + url.search + url.hash;
  }

  function updateUrl() {
    history.replaceState(null, '', caseUrl());
    $('wsIngest').href = window.SlopeKGNav?.withReturn('/web/pdf-ingestion.html', caseUrl()) || './pdf-ingestion.html';
  }

  function sourceHint() {
    return list(state.catalog?.source_cases).find(source => source.case_id === state.sourceHint && source.workspace === state.sourceWorkspace);
  }

  function renderPicker() {
    const search = $('wsSlopeSearch').value.trim().toLowerCase();
    const all = list(state.catalog?.slopes);
    const matching = all.filter(slope => slope.id === state.slopeId ||
      [slope.label, slope.id, slope.location].some(value => String(value || '').toLowerCase().includes(search)));
    const select = $('wsSlopeSelect');
    select.innerHTML = `<option value="">${matching.length ? '选择边坡' : '没有匹配的边坡'}</option>${matching.map(slope =>
      `<option value="${e(slope.id)}">${e(slope.label)}${slope.source_count ? ` · ${e(slope.source_count)} 条资料` : ''}</option>`).join('')}`;
    select.value = state.slopeId;
    select.disabled = state.busy || !matching.length;
    $('wsSlopeCount').textContent = `共 ${all.length} 处边坡${search ? `，显示 ${matching.length} 处` : ''}`;
  }

  function renderHint() {
    const element = $('wsSourceHint');
    const source = sourceHint();
    element.hidden = !state.sourceHint;
    if (!state.sourceHint) return;
    if (!source) {
      element.textContent = '未找到这条识别资料。可返回识别任务查看接收状态。';
      return;
    }
    const action = source.archived ? '这是已归档的历史资料。' : source.supported === false ? '该资料对象不适用于滑坡现状评估。' :
      source.slope_id ? '已关联到边坡。' : state.slopeId ? '核对资料描述的是同一滑坡体后，可以关联。' : '先选择或新建对应的边坡。';
    element.innerHTML = `<strong>${e(source.label || sourceName(source))}</strong><br>${e(sourceName(source))} · ${e(action)}`;
  }

  function updateSelection() {
    const sources = list(state.dossier?.sources).filter(usableSource);
    const anchor = sources.find(source => sourceKey(source) === state.anchor) ||
      sources.find(source => scopeKey(source.scope)) || sources[0];
    const key = anchor ? sourceKey(anchor) : '';
    if (key !== state.anchor) state.scenario = '';
    state.anchor = key;
    state.selected = new Set(sources.filter(source => sourceKey(source) === state.anchor ||
      state.selected.has(sourceKey(source)) && matchingScope(anchor, source)).map(sourceKey));
  }

  function availableLinks() {
    return list(state.catalog?.source_cases).filter(source => !source.slope_id && usableSource(source));
  }

  function sourcePanel() {
    const { slope, sources } = state.dossier;
    const usable = list(sources).filter(usableSource);
    const anchor = usable.find(source => sourceKey(source) === state.anchor);
    const condition = scopeCondition(anchor?.scope);
    const fromGraph = !anchor && slope.kind === 'graph' && slope.supported !== false;
    const canStart = !!anchor || fromGraph;
    const conditionOptions = scenarioOptions(state.scenario);
    const anchorGaps = anchor ? scopeGaps(anchor.scope) : [];
    const rows = list(sources).map(source => {
      const key = sourceKey(source);
      const isAnchor = key === state.anchor;
      const allowed = usableSource(source) && (isAnchor || matchingScope(anchor, source));
      const gaps = scopeGaps(source.scope);
      const reason = source.archived ? '历史档案' : source.supported === false ? '不适用' :
        isAnchor ? (gaps.length ? '所选来源（范围待确认）' : '本次范围依据') : allowed ? '' : gaps.length ? '该来源的范围待确认' :
        anchorGaps.length ? '本次范围待确认，暂不能合用' : '时期或工况不同，可单独评估';
      return `<li class="workbench-row">
        <label class="workbench-source-choice"><input type="checkbox" data-source-key="${e(key)}"
          ${state.selected.has(key) ? 'checked' : ''} ${!allowed || isAnchor ? 'disabled' : ''}/>
          <span><strong>${e(source.label || sourceName(source))}</strong>
          <small class="workbench-muted">${e(sourceName(source))}${reason ? ` · ${e(reason)}` : ''}</small>${scopeTags(source.scope)}</span></label>
        <div class="workbench-actions">${pageLink(source.detail_url, '查看资料')}
        ${source.link?.action === 'link' && !source.archived ? `<button type="button" class="button ghost small" data-action="unlink" data-key="${e(key)}">解除关联</button>` : ''}</div>
      </li>`;
    });
    const scopes = anchor ? `<div class="workbench-options"><label for="wsAnchor">本次用哪份资料</label>
      <select id="wsAnchor">${usable.map(source => `<option value="${e(sourceKey(source))}" ${state.anchor === sourceKey(source) ? 'selected' : ''}>
      ${e(sourceName(source))} · ${e(scopeLabels(source.scope).join(' · '))}</option>`).join('')}</select></div>` : '';
    const conditionField = condition ? `<p id="wsConditionSource">评估条件 <strong>${e(scopeLabels(anchor.scope)[1])}</strong> <span class="badge available">从资料带入</span></p>` :
      canStart && conditionOptions ? `<div class="workbench-options"><label for="wsScenario">这次按什么条件评？</label>
        <select id="wsScenario"><option value="">暂不确定</option>${conditionOptions}</select>
        <p class="workbench-muted">资料里的条件还不明确。选定条件后，可以为本次评估补充已核实的证据。</p></div>` : '';
    const draft = anchor && (!scopePeriod(anchor.scope) || (!condition && !state.scenario));
    return `<section class="card workbench-result" id="wsSources"><div class="workbench-bar"><h3>准备评估</h3>
      <button type="button" class="button small secondary" id="wsLinkSource" data-action="link">关联识别资料</button></div>
      ${scopes}${conditionField}
      ${anchor && !scopePeriod(anchor.scope) ? '<p class="workbench-muted">这份资料还缺调查日期。可先保存，核对日期后再补证。</p>' : ''}
      ${canStart ? `<div class="workbench-actions">
        <button type="button" class="button" id="wsCreate" data-action="create">${draft ? '保存待补资料的评估' : fromGraph ? '用已有档案开始评估' : '开始评估'}</button>
        ${anchor ? pageLink(anchor.detail_url, '查看资料') : ''}</div>` : '<p>先关联同一处滑坡的识别资料，再开始评估。</p>'}
      ${rows.length ? `<details class="workbench-expand"><summary>资料列表和合用设置 <span class="workbench-muted" id="wsSelectedCount">· 已选 ${state.selected.size} 条</span></summary>
        <p class="workbench-muted">同一处滑坡、同一时期、同一条件下的资料可以合用。</p><ul class="workbench-list">${rows.join('')}</ul></details>` : ''}</section>`;
  }

  function badgeFor(result) {
    if (!result) return '<span class="badge">待计算</span>';
    const message = result.review_status === 'accepted' && testOnly() ? '已复核（模拟）' :
      workflowLabels[result.workflow_status] || (result.review_status === 'accepted' ? '已复核' : '待复核');
    const style = result.review_status === 'accepted' ? 'available' : 'pending';
    return `<span class="badge ${style}">${e(message)}</span>`;
  }

  function resultTitle(result) {
    return result?.table17_result?.grade || (result ? '危险性等级待确定' : '尚无计算结果');
  }

  function priorPanel() {
    const bundle = state.prior;
    const results = list(bundle.results);
    const result = results.findLast(item => item.is_current) || results.at(-1);
    const facts = new Map([...list(bundle.facts), ...list(bundle.derived_facts)].map(fact => [fact.fact_id, fact]));
    const contexts = new Map(list(bundle.contexts).map(context => [context.context_id, context]));
    const selected = Object.entries(result?.resolved_variables || {}).flatMap(([variable, row]) => {
      const fact = facts.get(row.selected_fact_id);
      if (!fact) return [];
      const proof = list(fact.evidence_refs).map(id => contexts.get(id)).filter(Boolean)
        .flatMap(context => list(context.spans).map(span => citation(context, span)));
      return [`<div class="workbench-proof"><p><strong>${e(label(variable))} · ${e(shownValue(fact.value))}</strong>
        ${fact.origin_kind === 'llm' ? '<span class="badge">模型判断</span>' : ''}</p>
        ${proof.length ? `<details><summary>查看依据</summary>${proof.join('')}</details>` : ''}</div>`];
    });
    const missing = list(result?.minimal_missing).map(item => label(item.variable_or_feature)).filter(Boolean);
    return `<div class="workbench-prior" id="wsPrior" tabindex="-1">
      <div class="workbench-bar"><h4>原评估 · 版本 ${e(result?.resolution_version ?? bundle.case.resolution_version)}</h4>${badgeFor(result)}</div>
      <p><strong>${e(resultTitle(result))}</strong>${result?.review_status === 'pending' ? ' · 待人工复核' : ''}</p>
      ${result?.partials?.table15?.harm ? `<p>危害程度 · ${e(result.partials.table15.harm)}</p>` : ''}
      ${selected.length ? selected.join('') : '<p class="workbench-muted">这版未采用具体评估输入。</p>'}
      ${missing.length ? `<p class="workbench-muted">仍缺 ${e([...new Set(missing)].join('、'))} 依据。</p>` : ''}
    </div>`;
  }

  function historyPanel() {
    const rows = list(state.dossier?.cases).map(item => {
      const isOpen = item.case_id === state.bundle?.case.case_id;
      const isLegacy = item.workspace === 'main' || item.workspace === 'debug';
      const showingPrior = isLegacy && state.prior?.case.case_id === item.case_id;
      const action = isLegacy ? `<button type="button" class="button small ghost" data-action="open-prior" data-id="${e(item.case_id)}">打开原评估</button>` :
        `<button type="button" class="button small ${isOpen ? 'secondary' : 'ghost'}" data-action="open-case" data-id="${e(item.case_id)}">${isOpen ? '查看当前案例' : '查看评估'}</button>`;
      return `<li class="workbench-row${isOpen ? ' is-current' : ''}">
        <div><strong>${e(item.label || resultTitle(item.result))}</strong>
        <p>${e(resultTitle(item.result))} ${badgeFor(item.result)} ${isLegacy ? '<span class="badge">原有案例</span>' : ''}</p>
        ${scopeTags(item.scope)}<small class="workbench-muted">版本 ${e(item.resolution_version ?? '—')}${item.created_at ? ` · ${e(item.created_at)}` : ''}</small></div>
        <div class="workbench-actions">${action}</div>${showingPrior ? priorPanel() : ''}</li>`;
    });
    return rows.length ? `<details class="card workbench-history" ${!state.bundle || state.prior ? 'open' : ''}>
      <summary>历次评估 <span class="badge">${rows.length} 次</span></summary><ul class="workbench-list">${rows.join('')}</ul></details>` : '';
  }

  function currentResult() {
    const results = list(state.bundle?.results);
    return results.find(result => result.is_current) || (results.every(result => result.is_current === undefined) ?
      results.filter(result => result.resolution_version === state.bundle?.case.resolution_version).at(-1) : undefined);
  }

  function displayedResult() {
    return list(state.bundle?.results).find(result => result.result_id === state.resultId) || currentResult();
  }

  function isCurrentView() {
    return !state.resultId || state.resultId === currentResult()?.result_id;
  }

  function canEditCase() {
    return isCurrentView() && !state.bundle?.source_links_changed;
  }

  function factResolution(result, factId) {
    return Object.values(result?.resolved_variables || {}).find(item => list(item.candidate_fact_ids).includes(factId));
  }

  function contextName(context) {
    return context.source_name || context.document_name || String(context.source_path || '').split(/[\\/]/).pop() || '已绑定证据';
  }

  function pageLabel(span) {
    return Number.isInteger(span?.file_page_1_based) ? `第 ${span.file_page_1_based} 页` : '原文片段';
  }

  function contextLink(context, span) {
    const path = localUrl(span?.page_url || context?.document_url, '/api/');
    return path ? `<a href="${e(path)}" target="_blank" rel="noopener" class="button ghost small">查看原页</a>` : '';
  }

  function citation(context, span) {
    return `<div class="workbench-proof"><span class="workbench-muted">${e(contextName(context))} · ${e(pageLabel(span))}</span>
      ${span?.quote ? `<blockquote class="workbench-quote">${e(span.quote)}</blockquote>` : ''}
      <div>${contextLink(context, span)}</div></div>`;
  }

  function factTable(result) {
    const { facts, derived_facts, history_facts, contexts, case: record } = state.bundle;
    const lookup = new Map(list(contexts).map(context => [context.context_id, context]));
    const ids = new Set(Object.values(result?.resolved_variables || {}).flatMap(item => [item.selected_fact_id, ...list(item.candidate_fact_ids)]));
    const shown = isCurrentView() ? [...list(facts), ...list(derived_facts)] : list(history_facts || facts).filter(fact => ids.has(fact.fact_id));
    if (!shown.length) return `<p class="workbench-muted">此版本暂无结构化输入。${list(record.workspace_fact_trace).length ? '可在下方核对已有来源和未采用原因。' : ''}</p>`;
    const rows = shown.map(fact => {
      const field = fields[fact.variable_code];
      const resolution = factResolution(result, fact.fact_id);
      const check = resolution?.applicability_by_fact?.[fact.fact_id];
      const chosen = resolution?.selected_fact_id === fact.fact_id;
      const explicitlyChosen = Object.values(record.selections || {}).includes(fact.fact_id);
      const canSelect = canEditCase() && resolution && check?.status !== 'incompatible' &&
        check?.dimensions?.support === 'compatible' && list(resolution.candidate_fact_ids).length > 1;
      const correctable = canEditCase() && scopeKey(fact.scope) && scopeKey(fact.scope) === scopeKey(record.scope) && field &&
        !fact.derivation_id && fact.value?.kind !== 'interval' && fact.value?.kind !== 'boolean';
      const evidence = list(fact.evidence_refs).map(id => lookup.get(id)).filter(Boolean)
        .flatMap(context => list(context.spans).map(span => citation(context, span)));
      const stateLabel = chosen ? '已采用' : check?.status === 'incompatible' ? '范围或资料不适用' :
        fact.derivation_id ? '规则推导' : '保留待核';
      return `<tr><td><strong>${e(label(fact.variable_code))}</strong><p>${e(shownValue(fact.value))}</p></td>
        <td>${e(sourceLabels[fact.origin_kind] || '来源记录')}<br><span class="badge ${chosen ? 'available' : ''}">${e(stateLabel)}</span></td>
        <td>${evidence.length ? `<details><summary>${evidence.length} 条原文证据</summary>${evidence.join('')}</details>` : '<span class="workbench-muted">无原文页</span>'}</td>
        <td><div class="workbench-actions">
        ${correctable ? `<button class="button small ghost" type="button" data-action="correct" data-id="${e(fact.fact_id)}">纠错</button>` : ''}
        ${canSelect && !explicitlyChosen ? `<button class="button small secondary" type="button" data-action="select" data-id="${e(fact.fact_id)}">采用此来源</button>` : ''}
        ${canEditCase() && explicitlyChosen ? `<button class="button small ghost" type="button" data-action="clear" data-id="${e(fact.fact_id)}">撤销来源选择</button>` : ''}</div></td></tr>`;
    });
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>输入</th><th>采用情况</th><th>证据</th><th>操作</th></tr></thead>
      <tbody>${rows.join('')}</tbody></table></div>`;
  }

  function reviewHistory() {
    const names = { confirm_result: '确认评估结果', select_branch: '选择来源', clear_selection: '撤销来源选择',
      correct: '纠正识别值', supplement: '补充证据', add_assertion: '补充资料', note: '复核记录' };
    const facts = new Map([...list(state.bundle.history_facts), ...list(state.bundle.facts)].map(fact => [fact.fact_id, fact]));
    const rows = [...list(state.bundle.reviews)].reverse().map(item => {
      const old = facts.get(item.target_id);
      const replacement = facts.get(item.replacement_fact_id);
      const correction = item.decision === 'correct' ?
        `<p>${e(label(replacement?.variable_code || item.variable))} · ${e(shownValue(old?.value || item.previous_value))} → ${e(shownValue(replacement?.value || item.replacement_value))}</p>` : '';
      return `<li class="workbench-row"><div><strong>${e(names[item.decision] || '复核记录')}</strong>
        <small class="workbench-muted">${e(item.reviewer_ref || item.actor_ref || '操作者未记录')} · ${e(item.reviewed_at || '')}</small>
        <p>${e(item.reason || '')}</p>${correction}</div></li>`;
    });
    return `<details class="workbench-expand"><summary>补证、纠错与复核记录 · ${rows.length} 条</summary>
      ${rows.length ? `<ul class="workbench-list">${rows.join('')}</ul>` : '<p class="workbench-muted">暂无人工操作记录。</p>'}</details>`;
  }

  function scopeTrace(record) {
    const trace = list(record.workspace_fact_trace);
    if (!trace.length) return '';
    const source = new Map(list(record.workspace_sources).map(item => [item.case_id, item]));
    const contexts = new Map(list(state.bundle.contexts).map(context => [context.context_id, context]));
    const reasons = { different_or_unresolved_object: '对象或范围不一致', period_unresolved: '时期未明确', different_period: '时期不同',
      scenario_unresolved: '工况未明确', different_scenario: '工况不同', reference_role: '仅作参考', prior_derivation: '原案例推导结果',
      source_record_missing: '原始记录不完整', source_support_unresolved: '原文支持未核', source_evidence_unverified: '原页证据待核',
      standard_unconfirmed: '适用标准未核定', value_unknown: '数值未知', value_type_unresolved: '数值或单位待核',
      invalid_stability_threshold: '稳定阈值不适用', ambiguous_evidence_scope: '引文涉及多个范围' };
    const pending = trace.filter(item => item.status === 'requires_review');
    const excluded = trace.filter(item => item.status !== 'included' && item.status !== 'requires_review');
    const renderRow = item => {
      const original = source.get(item.source_case_id);
      const proof = list(item.evidence_refs).map(id => contexts.get(id)).filter(Boolean)
        .flatMap(context => list(context.spans).map(span => citation(context, span)));
      return `<li class="workbench-row"><div><strong>${e(label(item.variable_code))} · ${e(shownValue(item.value))}</strong>
        <p class="workbench-muted">${e(original?.label || sourceName(original || {}))} · ${e(scopeLabels(item.scope).join(' · '))}</p>
        <p class="workbench-muted">${e(list(item.reasons).map(reason => reasons[reason] || '依据待核').join('、'))}</p>
        ${proof.length ? `<details><summary>查看来源原文</summary>${proof.join('')}</details>` : ''}
        </div></li>`;
    };
    const section = (title, items) => items.length ? `<details class="workbench-expand"><summary>${title} · ${items.length} 条</summary>
      <ul class="workbench-list">${items.map(renderRow).join('')}</ul></details>` : '';
    return `<p class="workbench-muted">已有来源记录 ${trace.length} 条 · 通过组证检查 ${trace.length - pending.length - excluded.length} 条 · 待核 ${pending.length} 条 · 未纳入 ${excluded.length} 条</p>
      ${section('待核的来源记录', pending)}${section('未纳入本次评估的来源记录', excluded)}`;
  }

  function casePanel() {
    if (!state.bundle) return '';
    const record = state.bundle.case;
    const result = displayedResult();
    const active = canEditCase();
    const hasPeriod = !!scopePeriod(record.scope);
    const hasCondition = !!scopeCondition(record.scope);
    const canSupplement = hasPeriod && hasCondition;
    const grade = result?.table17_result?.grade;
    const canReview = active && grade && result.review_status !== 'accepted' && !list(result.conflicts).some(item => list(item.blocks).length);
    const knownFacts = new Map([...list(state.bundle.history_facts), ...list(state.bundle.facts), ...list(state.bundle.derived_facts)].map(fact => [fact.fact_id, fact]));
    const dev = knownFacts.get(result?.resolved_variables?.development?.selected_fact_id);
    const harm = result?.partials?.table15?.harm || shownValue(knownFacts.get(result?.resolved_variables?.harm?.selected_fact_id)?.value);
    const missing = list(result?.minimal_missing).map(item => `<li><strong>${e(label(item.variable_or_feature === 'triggers_and_geological_context_review' ? 'geological_context' : item.variable_or_feature))}</strong>
      ${e(item.why_this_resolves_target || item.reason)}</li>`);
    const conflicts = list(result?.conflicts).map(item => `<li>${e(item.variable ? `${label(item.variable)}有不同来源值` :
      ({ table15_indicator_conflict: '表 15 的指标指向不同危害等级', reported_vs_derived_harm: '原报告危害程度与指标查表结果不同' })[item.kind] || '需要核对不同来源或适用范围')}</li>`);
    const versions = [...list(state.bundle.results)].reverse();
    const canSetCondition = hasPeriod && !hasCondition && !!conditionSource(record) && !!scenarioOptions();
    const next = canSetCondition ? `<div class="notice"><strong>选定本次条件，继续补证</strong>
      <p>选定后会打开新的评估。原资料里条件不明的数值仍需核对。</p>
      <div class="workbench-actions workbench-condition"><label for="wsCaseScenario">这次按什么条件评？</label>
        <select id="wsCaseScenario"><option value="">请选择</option>${scenarioOptions()}</select>
        <button class="button" type="button" id="wsCaseContinue" data-action="set-case-condition" disabled>继续评估</button></div></div>` :
      `<div class="notice"><strong>下一步</strong> ${hasPeriod ? '在准备评估中选择可用资料和本次条件。' : '先核对资料里的调查日期。'}
      <a href="#wsSources">回到准备评估</a></div>`;
    return `<section class="card workbench-result" id="wsCase" aria-labelledby="wsCaseTitle">
      <div class="workbench-bar"><h3 id="wsCaseTitle">本次评估</h3><div class="workbench-actions">${testOnly() ? '<span class="badge pending">模拟</span>' : ''}${badgeFor(result)}</div></div>
      <div class="workbench-grade">${e(resultTitle(result))}${grade && result.basis_status === 'conditional' ? '<small>条件结果</small>' : ''}</div>
      ${scopeTags(result?.assessment_scope || record.scope)}
      ${record.scope?.scenario?.selected_by_operator ? '<span class="badge">本次条件由操作者选择</span>' : ''}
      ${!grade && list(result?.table17_result?.possible_grades).length > 1 ? `<p class="workbench-muted">需进一步区分 ${e(result.table17_result.possible_grades.join('、'))}</p>` : ''}
      <dl class="workbench-summary">
        <div><dt>发育程度</dt><dd>${e(shownValue(dev?.value))}</dd></div>
        <div><dt>危害程度</dt><dd>${e(harm)}</dd></div>
      </dl>
      ${active && !canSupplement ? next :
        missing.length || conflicts.length ? `<div class="notice"><strong>下一步 · 核对以下证据</strong><ul>${[...conflicts, ...missing].join('')}</ul></div>` : ''}
      ${state.bundle.source_links_changed ? '<p class="notice">资料归属已调整。此案例保留为历史记录，请用目前已确认的资料开始新的评估。</p>' : ''}
      ${active ? `<div class="workbench-actions">
        <button type="button" class="button secondary" id="wsSupplement" data-action="supplement" ${canSupplement ? '' : 'hidden'}>补充证据</button>
        <button type="button" class="button" id="wsReview" data-action="review" ${canReview ? '' : 'disabled hidden'}>复核本次结果</button></div>` :
        (!isCurrentView() ? '<p class="workbench-muted">正在查看保存的旧版本。切回当前版本可查看当前可用操作。</p>' : '')}
      <details class="workbench-expand" ${grade ? 'open' : ''}><summary>评估输入和原文证据</summary>${factTable(result)}</details>
      <details class="workbench-expand" ${state.resultId ? 'open' : ''}><summary>资料采用明细与版本</summary>
        ${versions.length > 1 ? `<label class="workbench-version" for="wsVersion">结果版本<select id="wsVersion">
          <option value="" ${!state.resultId ? 'selected' : ''}>当前案例 · 版本 ${e(record.resolution_version)}</option>
          ${versions.filter(item => item.result_id !== currentResult()?.result_id).map(item => `<option value="${e(item.result_id)}" ${state.resultId === item.result_id ? 'selected' : ''}>版本 ${e(item.resolution_version)} · ${e(resultTitle(item))}</option>`).join('')}</select></label>` : ''}
        ${scopeTrace(record)}${reviewHistory()}
        ${active && canSupplement ? '<button type="button" class="button ghost small" data-action="evaluate">重新计算</button>' : ''}</details></section>`;
  }

  function render() {
    $('wsTestBanner').hidden = !testOnly();
    document.body.classList.toggle('risk-demo', demoMode());
    if (demoMode()) $('wsTestBanner').textContent = '模拟演示。A 展示从资料自动带入条件、查看原页和复核。B 展示资料条件缺失时怎么继续。';
    renderPicker();
    renderHint();
    if (!state.dossier) {
      $('wsDossier').innerHTML = '<section class="card detail-empty"><h2>选择一个边坡开始</h2><p>查看已关联的资料和评估历史。尚未建档的对象可新建边坡。</p></section>';
      return;
    }
    const slope = state.dossier.slope;
    $('wsDossier').innerHTML = `<section class="card"><div class="workbench-bar"><h2>${e(slope.label)}</h2>
      <span class="badge">${slope.kind === 'graph' ? '已有档案' : '新建档案'}</span></div>
      <p class="workbench-muted">${slope.location ? `${e(slope.location)} · ` : ''}${list(state.dossier.sources).length} 条关联资料 · ${list(state.dossier.cases).length} 次评估</p></section>
      ${sourcePanel()}${casePanel()}${historyPanel()}`;
  }

  async function readCatalog() {
    state.catalog = await api.requestJson(`${base}catalog`);
  }

  async function readSlope(id) {
    if (!id) {
      state.slopeId = '';
      state.dossier = null;
      state.bundle = null;
      state.prior = null;
      state.anchor = '';
      state.scenario = '';
      state.selected.clear();
      return;
    }
    const data = await api.requestJson(`${base}slope?${new URLSearchParams({ slope_id: id })}`);
    if (state.slopeId !== id) {
      state.anchor = '';
      state.scenario = '';
      state.selected.clear();
      state.bundle = null;
      state.prior = null;
      state.resultId = '';
    }
    state.slopeId = id;
    state.dossier = data;
    updateSelection();
  }

  function setBundle(bundle) {
    state.bundle = bundle;
    state.prior = null;
    state.resultId = '';
  }

  async function readPrior(caseId) {
    const item = list(state.dossier?.cases).find(row => row.case_id === caseId &&
      ['main', 'debug'].includes(row.workspace));
    if (!item) throw new Error('这条原评估不在所选边坡的历史中。');
    state.prior = await api.requestJson('/api/standard-assessment/case?' +
      new URLSearchParams({ case_id: caseId, workspace: item.workspace }));
  }

  function focusPrior() {
    $('wsPrior')?.focus({ preventScroll: true });
    $('wsPrior')?.scrollIntoView({ block: 'start' });
  }

  function focusCase() {
    $('wsCase')?.scrollIntoView({ block: 'start' });
  }

  function busy(value) {
    state.busy = value;
    $('wsDialogSubmit').disabled = value || $('wsDialogSubmit').dataset.invalid === 'true';
    $('wsSlopeSelect').disabled = value || !$('wsSlopeSelect').querySelector('option[value]:not([value=""])');
    $('wsDossier').setAttribute('aria-busy', String(value));
  }

  async function perform(operation, { dialog = false } = {}) {
    if (state.busy) return;
    busy(true);
    if (dialog) dialogError('');
    try {
      await operation();
    } catch (error) {
      if (dialog && $('wsDialog').open) {
        dialogError(error.message || '操作未完成', error.status === 409 && !!state.action?.caseId);
      } else {
        status(error.message || '操作未完成', true);
      }
    } finally {
      busy(false);
    }
  }

  function dialogError(message, canRefresh = false) {
    $('wsDialogError').textContent = message;
    $('wsDialogError').hidden = !message;
    $('wsDialogRefresh').hidden = !canRefresh;
  }

  function openDialog(action, title, body, submit) {
    state.action = action;
    $('wsDialogTitle').textContent = title;
    $('wsDialogBody').innerHTML = body;
    $('wsDialogSubmit').textContent = submit;
    delete $('wsDialogSubmit').dataset.invalid;
    $('wsDialogSubmit').disabled = false;
    dialogError('');
    $('wsDialog').showModal();
  }

  function identityFields(reasonLabel = '依据或理由') {
    const demo = demoMode();
    return `<label>实际操作者<input name="actor" required maxlength="100" autocomplete="off" ${demo ? 'value="模拟演示"' : ''}/></label>
      <label>${e(reasonLabel)}<textarea name="reason" required maxlength="500">${demo ? '现场展示模拟案例' : ''}</textarea></label>`;
  }

  function conditionSource(record) {
    const old = list(record?.workspace_sources).find(source => source.case_id === record?.workspace_anchor_case_id);
    return old && list(state.dossier?.sources).find(source => sourceKey(source) === sourceKey(old) &&
      usableSource(source) && scopePeriod(source.scope) && !scopeCondition(source.scope));
  }

  function openCreate(selected, anchor, scenario) {
    const draft = anchor && (!scopePeriod(anchor.scope) || (!scopeCondition(anchor.scope) && !scenario));
    const chosenCondition = state.catalog?.scenario_options?.[scenario];
    openDialog({ operation: 'create', selected, anchor, scenario }, draft ? '保存待补资料的评估' : '开始评估',
      `<p><strong>${e(state.dossier.slope.label)}</strong></p>${anchor ? e(scopeLabels(anchor.scope)[0]) : ''}
       ${selected.length ? `<ul>${selected.map(source => `<li>${e(sourceName(source))}</li>`).join('')}</ul>` : '<p>依据当前保存的边坡档案。</p>'}
       ${chosenCondition ? `<p>本次选择 <strong>${e(chosenCondition)}</strong>。资料里条件不明的记录仍会列在未纳入明细。</p>` :
         scopeCondition(anchor?.scope) ? `<p>资料带入 <strong>${e(scopeLabels(anchor.scope)[1])}</strong>。</p>` : ''}
       ${draft ? '<p>保存后可以查看资料，待补齐条件和证据后再定级。</p>' : ''}
       ${identityFields(scenario ? '为什么选这个评估条件' : '本次选用资料的依据')}`, draft ? '保存案例' : '生成评估');
  }

  function renderLinkOptions(search = '', selected = '') {
    const filter = search.trim().toLowerCase();
    const matches = state.action.choices.filter(source => sourceKey(source) === selected ||
      [source.label, sourceName(source), ...scopeLabels(source.scope)].some(value => String(value || '').toLowerCase().includes(filter)));
    const picker = $('wsLinkPicker');
    picker.innerHTML = `<option value="">${matches.length ? '请选择资料内的对象' : '未找到匹配资料'}</option>${matches.map(source =>
      `<option value="${e(sourceKey(source))}">${e(source.label || sourceName(source))} · ${e(sourceName(source))} · ${e(scopeLabels(source.scope).join(' · '))}</option>`).join('')}`;
    picker.value = selected;
    renderLinkPreview();
  }

  function renderLinkPreview() {
    const source = state.action.choices.find(item => sourceKey(item) === $('wsLinkPicker').value);
    const detail = localUrl(source?.detail_url, '/web/');
    $('wsLinkPreview').innerHTML = source ? `${scopeTags(source.scope)}${detail ?
      `<p><a href="${e(detail)}" target="_blank" rel="noopener" class="button small ghost">查看来源案例</a></p>` : ''}` : '';
  }

  function openLink() {
    const choices = availableLinks();
    if (!choices.length) {
      status('没有可关联的新资料。可返回智能识别，上传 PDF 或查看识别任务。');
      return;
    }
    const hint = sourceHint();
    const selected = hint && choices.some(item => sourceKey(item) === sourceKey(hint)) ? sourceKey(hint) : '';
    openDialog({ operation: 'link', choices }, '关联识别资料',
      `<label>搜索识别资料<input type="search" id="wsLinkSearch" placeholder="输入文件名、对象名或时期"/></label>
      <label>识别资料<select name="source" id="wsLinkPicker" required></select></label>
      <div id="wsLinkPreview"></div>
      <p class="workbench-muted">关联到 ${e(state.dossier.slope.label)}。根据原报告的名称、位置和范围核对对象。</p>
      <label class="workbench-check"><input type="checkbox" name="same_object_confirmed" required/>我已核对，资料描述的是同一处完整滑坡体</label>
      ${identityFields('确认同一滑坡体的依据')}`, '保存关联');
    renderLinkOptions('', selected);
  }

  function activeCaseAction(operation, extra = {}) {
    return { operation, caseId: state.bundle.case.case_id, version: state.bundle.case.resolution_version, ...extra };
  }

  function valueControl(field, value) {
    if (field.options) return `<label>${e(field.label)}<select name="value" required><option value="">请选择</option>
      ${field.options.map(item => `<option ${value?.label === item ? 'selected' : ''}>${e(item)}</option>`).join('')}</select></label>`;
    if (field.unit) return `<label>${e(field.label)}${unitLabels[field.unit] ? `（${e(unitLabels[field.unit])}）` : ''}
      <input name="value" type="number" step="${e(field.step || 'any')}" min="0" ${field.unit === 'deg' ? 'max="90"' : ''} required value="${e(value?.kind === 'number' ? value.value : '')}"/></label>
      <label>数值精度<select name="qualifier"><option value="exact_as_reported">原文精确值</option>
      <option value="approximate" ${value?.qualifier === 'approximate' ? 'selected' : ''}>约数</option></select></label>`;
    return `<label>${e(field.label)}<textarea name="value" required maxlength="3000">${e(value?.kind === 'text' ? value.text : '')}</textarea></label>`;
  }

  function evidenceChoices(variable) {
    const allowed = state.bundle.case.workspace_evidence || {};
    return list(state.bundle.contexts).filter(context => {
      const metadata = allowed[context.context_id];
      return context.usable_for_evidence ?? !!metadata;
    }).flatMap(context => list(context.spans).map((span, index) => ({ context, span, index,
      related: list(context.evidence_variables || allowed[context.context_id]?.variables).includes(variable) })))
      .filter(item => item.span.quote && Number.isInteger(item.span.file_page_1_based))
      .sort((left, right) => Number(right.related) - Number(left.related));
  }

  function updateProof() {
    const picker = $('wsEvidence');
    const item = picker.value === '' ? null : state.action.evidence[Number(picker.value)];
    const proof = $('wsEvidenceProof');
    const check = $('wsPageReviewed');
    proof.innerHTML = item ? citation(item.context, item.span) : '';
    const image = item && localUrl(item.span.page_url, '/api/');
    if (image) {
      proof.insertAdjacentHTML('beforeend', `<details><summary>在此处展开原页</summary>
        <img loading="lazy" src="${e(image)}" alt="${e(`${contextName(item.context)} ${pageLabel(item.span)}`)}"/></details>`);
    }
    check.checked = false;
    check.disabled = !item;
  }

  function updateEvidenceForm() {
    const { target } = state.action;
    const form = $('wsActionForm');
    const fromPage = target || form.elements.mode.value === 'source_quote';
    if (!target) {
      const picker = form.elements.variable;
      const previous = picker.value;
      const options = Object.entries(fields).filter(([key]) => fromPage || !['development', 'harm', 'fs', 'fst'].includes(key));
      picker.innerHTML = `<option value="">请选择</option>${options.map(([key, field]) => `<option value="${e(key)}">${e(field.label)}</option>`).join('')}`;
      picker.value = options.some(([key]) => key === previous) ? previous : '';
    }
    const variable = target?.variable_code || form.elements.variable.value;
    const field = fields[variable];
    $('wsEvidenceValue').innerHTML = field ? valueControl(field, target?.value) : '';
    $('wsEvidenceStandard').hidden = !['development', 'harm'].includes(variable);
    const evidence = state.action.evidence = field && fromPage ? evidenceChoices(variable) : [];
    $('wsEvidencePicker').hidden = !fromPage || !field;
    const select = $('wsEvidence');
    select.required = !!(fromPage && field);
    select.disabled = !(fromPage && field);
    select.innerHTML = `<option value="">${evidence.length ? '选择经核对的原文片段' : '本次案例没有该资料类型的可用原页'}</option>${evidence.map(({ context, span }, index) =>
      `<option value="${index}">${e(contextName(context))} · ${e(pageLabel(span))} · ${e(span.quote.replace(/\s+/g, ' ').slice(0, 64))}</option>`).join('')}`;
    const sameSource = target && evidence.findIndex(item => list(target.evidence_refs).includes(item.context.context_id));
    if (sameSource !== undefined && sameSource >= 0) select.value = String(sameSource);
    $('wsPageReviewed').required = !!(fromPage && field);
    updateProof();
    $('wsDialogSubmit').dataset.invalid = String(!field || !!(fromPage && !evidence.length));
    $('wsDialogSubmit').disabled = $('wsDialogSubmit').dataset.invalid === 'true';
  }

  function openEvidence(operation, target) {
    const correct = operation === 'correct';
    const canQuote = correct || evidenceChoices().length > 0;
    openDialog(activeCaseAction(operation, { target }), correct ? '纠正识别值' : '补充证据',
      `${correct ? `<p>当前记录 · <strong>${e(label(target.variable_code))} ${e(shownValue(target.value))}</strong></p>` :
        `<label>补充方式<select name="mode"><option value="source_quote" ${canQuote ? '' : 'disabled'}>根据案例中的原文补证</option>
         <option value="field_observation">录入本人核实的现场资料</option></select></label>
         ${canQuote ? '' : '<p class="workbench-muted">本次条件下暂无可引用的原页。可以录入已核实的现场资料。</p>'}
         <label>资料类型<select name="variable" required></select></label>`}
      <p class="workbench-muted">${e(scopeLabels(state.bundle.case.scope).join(' · '))}</p>
      <div id="wsEvidenceValue" class="workbench-form"></div>
      <p id="wsEvidenceStandard" class="workbench-muted" hidden>发育程度与危害程度需由原页支持所用国标版本，才能参与查表。</p>
      <div id="wsEvidencePicker" class="workbench-form" hidden>
        <label>原文证据<select id="wsEvidence" name="evidence"></select></label>
        <div id="wsEvidenceProof" class="workbench-proof"></div>
        <label class="workbench-check"><input type="checkbox" id="wsPageReviewed" name="source_page_reviewed"/>我已核对原页、资料含义和适用范围</label>
      </div>${identityFields(correct ? '纠错依据' : '补证依据')}`, correct ? '保存纠错并重新计算' : '保存证据并重新计算');
    updateEvidenceForm();
  }

  function typedValue(variable, data) {
    const field = fields[variable];
    const raw = String(data.get('value') ?? '').trim();
    if (!field || !raw) throw new Error('请选择资料类型并填写实际值。');
    if (field.options) return { kind: 'enum', label: raw, code: raw, vocabulary: field.vocabulary };
    if (field.unit) {
      const value = Number(raw);
      if (!Number.isFinite(value)) throw new Error('请输入有效数值。');
      return { kind: 'number', value, unit: field.unit, qualifier: data.get('qualifier') || 'exact_as_reported' };
    }
    return { kind: 'text', text: raw };
  }

  function post(operation, body) {
    return api.requestJson(`${base}${operation}`, { method: 'POST', body });
  }

  async function saveDialog(data) {
    const { operation, caseId, version, target, resultId, choices, source, selected, anchor, scenario } = state.action;
    const actor = String(data.get('actor') || '').trim();
    const reason = String(data.get('reason') || '').trim();
    if (!actor || !reason) throw new Error('请填写实际操作者和依据。');
    const body = { actor, reason };
    if (caseId) Object.assign(body, { case_id: caseId, expected_resolution_version: version });
    if (operation === 'slopes') {
      body.label = String(data.get('label') || '').trim();
      body.location = String(data.get('location') || '').trim();
    } else if (operation === 'link' || operation === 'unlink') {
      const item = source || choices.find(item => sourceKey(item) === data.get('source'));
      if (!item) throw new Error('请选择资料。');
      Object.assign(body, { slope_id: state.slopeId, workspace: item.workspace, case_id: item.case_id });
      if (operation === 'link') body.same_object_confirmed = data.get('same_object_confirmed') === 'on';
    } else if (operation === 'create') {
      Object.assign(body, { slope_id: state.slopeId, sources: selected.map(({ workspace, case_id, resolution_version }) => ({ workspace, case_id, resolution_version })) });
      if (anchor) body.anchor_case_id = anchor.case_id;
      if (scenario) body.scenario = scenario;
    } else if (operation === 'supplement' || operation === 'correct') {
      const variable = target?.variable_code || data.get('variable');
      const mode = target ? 'source_quote' : data.get('mode');
      Object.assign(body, { variable, mode, value: typedValue(variable, data) });
      if (target) body.fact_id = target.fact_id;
      if (mode === 'source_quote') {
        const selection = data.get('evidence');
        const item = selection === '' || selection === null ? null : state.action.evidence[Number(selection)];
        if (!item || data.get('source_page_reviewed') !== 'on') throw new Error('请选择证据并核对原页。');
        Object.assign(body, { evidence_id: item.context.context_id, evidence_span_index: item.index, source_page_reviewed: true });
      }
    } else if (operation === 'select') {
      Object.assign(body, { fact_id: target.fact_id, clear: state.action.clear });
    } else if (operation === 'review') {
      Object.assign(body, { result_id: resultId, context_reviewed: data.get('context_reviewed') === 'on' });
    }
    const response = await post(operation, body);
    $('wsDialog').close();
    if (operation === 'link' && state.sourceHint === body.case_id && state.sourceWorkspace === body.workspace) state.sourceHint = '';
    const id = response.slope?.id || state.slopeId;
    if (response.case) setBundle(response);
    status(({ slopes: '已保存边坡档案。', link: '资料已关联。', unlink: '已解除关联。原始资料和评估记录已保留。',
      create: '评估结果已保存。', supplement: '已保存证据并重新计算。', correct: '已记录原值和纠正值，评估已重新计算。',
      select: '来源选择已记录。', review: '已保存本次人工复核。' })[operation] || '已保存。');
    try {
      await readCatalog();
      await readSlope(id);
    } catch (error) {
      status(`操作已保存，列表刷新失败。${error.message}`, true);
    }
    render();
    updateUrl();
    if (response.case) focusCase();
  }

  $('wsActionForm').addEventListener('submit', event => {
    event.preventDefault();
    if (!state.action) return;
    const data = new FormData(event.currentTarget);
    perform(() => saveDialog(data), { dialog: true });
  });
  $('wsDialog').addEventListener('cancel', event => {
    if (state.busy) event.preventDefault();
  });

  $('wsSlopeSearch').addEventListener('input', renderPicker);
  $('wsDialog').addEventListener('input', event => {
    if (event.target.id === 'wsLinkSearch') renderLinkOptions(event.target.value, $('wsLinkPicker').value);
  });
  $('wsSlopeSelect').addEventListener('change', event => {
    const id = event.target.value;
    perform(async () => {
      await readSlope(id);
      status('');
      render();
      updateUrl();
    });
  });

  document.addEventListener('change', event => {
    const input = event.target;
    if (state.busy) return;
    if (input.id === 'wsAnchor') {
      state.anchor = input.value;
      state.scenario = '';
      updateSelection();
      render();
      $('wsAnchor')?.focus({ preventScroll: true });
    } else if (input.id === 'wsScenario') {
      state.scenario = input.value;
      render();
      $('wsScenario')?.focus({ preventScroll: true });
    } else if (input.id === 'wsCaseScenario') {
      $('wsCaseContinue').disabled = !input.value;
    } else if (input.dataset.sourceKey) {
      if (input.checked) state.selected.add(input.dataset.sourceKey);
      else state.selected.delete(input.dataset.sourceKey);
      $('wsSelectedCount').textContent = `· 已选 ${state.selected.size} 条`;
    } else if (input.id === 'wsVersion') {
      state.resultId = input.value;
      render();
      $('wsVersion')?.focus({ preventScroll: true });
    } else if (input.id === 'wsLinkPicker') renderLinkPreview();
    else if (input.id === 'wsEvidence') updateProof();
    else if ($('wsDialog').open && ['mode', 'variable'].includes(input.name)) updateEvidenceForm();
  });

  document.addEventListener('click', event => {
    const button = event.target.closest('button[data-action]');
    if (!button || state.busy) return;
    const { action, id, key } = button.dataset;
    if (action === 'close-dialog') {
      $('wsDialog').close();
    } else if (action === 'new-slope') {
      openDialog({ operation: 'slopes' }, '新建边坡',
        `<label>边坡名称<input name="label" required maxlength="140" placeholder="可包含路线、桩号和侧别"/></label>
         <label>位置（可选）<input name="location" maxlength="240"/></label>${identityFields('建档依据')}`, '保存边坡');
    } else if (action === 'link') {
      openLink();
    } else if (action === 'unlink') {
      const source = list(state.dossier?.sources).find(item => sourceKey(item) === key);
      if (source) openDialog({ operation: 'unlink', source }, '解除资料关联',
        `<p>${e(source.label || sourceName(source))} 将从本边坡的资料列表移除。历史评估保留。</p>${identityFields('解除原因')}`, '解除关联');
    } else if (action === 'create') {
      const selected = list(state.dossier?.sources).filter(item => state.selected.has(sourceKey(item)));
      const anchor = selected.find(item => sourceKey(item) === state.anchor);
      openCreate(selected, anchor, scopeCondition(anchor?.scope) ? '' : state.scenario);
    } else if (action === 'set-case-condition') {
      const anchor = conditionSource(state.bundle?.case);
      const scenario = $('wsCaseScenario')?.value;
      if (anchor && scenario) openCreate([anchor], anchor, scenario);
    } else if (action === 'open-prior') {
      perform(async () => {
        await readPrior(id);
        render();
        updateUrl();
        focusPrior();
        status('');
      });
    } else if (action === 'open-case' || action === 'refresh-case') {
      const caseId = id || state.bundle?.case.case_id;
      if (!caseId) return;
      perform(async () => {
        const bundle = await api.requestJson(`${base}case?${new URLSearchParams({ case_id: caseId })}`);
        if ($('wsDialog').open) $('wsDialog').close();
        setBundle(bundle);
        render();
        updateUrl();
        focusCase();
        status('');
      });
    } else if (action === 'supplement') {
      openEvidence(action);
    } else if (action === 'correct' || action === 'select' || action === 'clear') {
      const fact = list(state.bundle?.facts).find(item => item.fact_id === id);
      if (!fact) return;
      if (action === 'correct') openEvidence(action, fact);
      else openDialog(activeCaseAction('select', { target: fact, clear: action === 'clear' }), button.textContent,
        `<p>${e(label(fact.variable_code))} · <strong>${e(shownValue(fact.value))}</strong></p>${identityFields()}`, '保存并重新计算');
    } else if (action === 'review') {
      const result = currentResult();
      if (!result?.table17_result?.grade || !canEditCase()) return;
      openDialog(activeCaseAction(action, { resultId: result.result_id }), '复核本次结果',
        `<p><strong>${e(resultTitle(result))}</strong> · 版本 ${e(result.resolution_version)}</p>${scopeTags(state.bundle.case.scope)}
         ${identityFields('复核意见与依据')}
         <label class="workbench-check"><input type="checkbox" name="context_reviewed" required/>我已核对对象、时期、工况及地质环境、诱发因素</label>`, '确认并保存复核');
    } else if (action === 'evaluate') {
      perform(async () => {
        const { case_id, resolution_version } = state.bundle.case;
        setBundle(await post('evaluate', { case_id, expected_resolution_version: resolution_version }));
        await readSlope(state.slopeId);
        render();
        status('已重新计算。');
      });
    }
  });

  perform(async () => {
    await readCatalog();
    const caseId = query.get('case_id');
    let slopeId = query.get('slope_id') || sourceHint()?.slope_id || '';
    if (caseId) {
      const bundle = await api.requestJson(`${base}case?${new URLSearchParams({ case_id: caseId })}`);
      slopeId = bundle.case.workspace_slope_id || slopeId;
      await readSlope(slopeId);
      setBundle(bundle);
    } else {
      await readSlope(slopeId);
    }
    const priorId = query.get('history_case');
    render();
    if (priorId) {
      await readPrior(priorId);
      render();
      focusPrior();
    }
    updateUrl();
  });
})();
