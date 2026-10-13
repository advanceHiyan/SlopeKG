(function () {
  'use strict';
  const api = window.SlopeKGApi, esc = api.escapeHtml, offline = window.MONITORING_OFFLINE;
  const $ = id => document.getElementById(id);
  if (offline) document.querySelector('.baseline-entry')?.parentElement?.setAttribute('hidden', '');
  let catalog, replay, graph, offset = 0, busy = false, rawRevision = 0;
  const PAGE_SIZE = 20, selectedComponents = new Map();
  const colors = ['#126d79', '#ba6231', '#7353a6', '#56813c'];
  const labels = { contains_zero: '含原始零值', invalid_value: '数值待解析', invalid_time: '时间待解析', duplicate_time: '重复时间', no_record: '无记录', ambiguous_or_invalid_day: '当日记录待核' };
  const number = n => n == null ? '未知' : Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 3 });
  const flags = values => (values || []).map(x => labels[x] || x).join('、') || '无解析问题';
  function error(message) { $('monitorError').hidden = !message; $('monitorError').textContent = message || ''; }
  function currentEnd() { return replay?.dates[Number($('replaySlider').value)] || replay?.end; }
  function setBusy(value) { busy = value; $('loadReplay').disabled = value; $('exportMonitoring').disabled = value || !!offline || !replay; $('replaySlider').disabled = value || !replay; }
  function renderCatalog() {
    $('siteDescription').textContent = `${catalog.site.label} · 路线候选 ${catalog.site.route_candidate} · 测点对应待核`;
    $('monitorBoundary').textContent = catalog.boundary;
    const last = catalog.series.map(s => s.last_time).filter(Boolean).sort().at(-1);
    $('monitorMetrics').innerHTML = [[catalog.files, '源表'], [catalog.observations, '原始记录'], [catalog.common_days, '八表共同记录日（非连续）'], [last?.slice(0, 10) || '未知', '本批记录截止日']].map(([v, label]) => `<div class="card metric-card"><strong>${esc(v)}</strong><span>${esc(label)}</span></div>`).join('');
    $('monitorSeries').innerHTML = catalog.series.map(s => `<option value="${esc(s.id)}">${esc(s.category)} · ${esc(s.device_names.join('/'))}</option>`).join('');
    $('monitorQualityRows').innerHTML = catalog.series.map(s => `<tr><td>${esc(s.category)}<br>${esc(s.device_names.join('/'))}</td><td>${esc(s.unit || '单位待确认')}<br>${esc(s.component_names.join('、'))}（含义待确认）</td><td>${esc(s.first_time)} 至 ${esc(s.last_time)}<br>记录 ${number(s.row_count)} 条；最大间隔 ${number(s.max_interval_seconds / 86400)} 天<br>时间待解析 ${number(s.invalid_time_rows)}；数值待解析 ${number(s.invalid_value_rows)}；重复时间行 ${number(s.duplicate_time_rows)}</td><td>${s.component_stats.map(c => `分量${c.component}：零值 ${number(c.zero_count)}/${number(c.valid_count)}；最大相邻变化 ${number(c.largest_adjacent_change?.change)}`).join('<br>')}</td><td>${esc(s.source_file)}<br><small>SHA-256 ${esc(s.sha256)}</small></td></tr>`).join('');
    $('monitorGraphSummary').textContent = `${graph.nodes.length} 个节点、${graph.edges.length} 条来源关系；保留候选工点、设备、系列、原表和质量摘要。原始采样值在独立观测数据层。`;
    $('monitorGraphRows').innerHTML = catalog.series.map(s => `<tr><td>会昌（归属待核）</td><td>${esc(s.device_names.join('/'))}</td><td>${esc(s.id)}<br>${esc(s.category)}</td><td>${esc(s.file_name)}</td><td>${number(s.row_count)} 条；单位 ${esc(s.unit || '待核')}</td></tr>`).join('');
    if (offline) {
      document.querySelector('.site-nav').hidden = true;
      document.querySelector('.site-brand').removeAttribute('href');
      $('exportMonitoring').textContent = '离线日期报告';
    }
  }
  function chart(item, dates) {
    const w = Math.max(300, $('monitorCharts').clientWidth / (window.innerWidth > 850 ? 2 : 1) - 44), h = 225, left = 56, right = 16, top = 18, bottom = 35;
    const selection = selectedComponents.get(item.meta.id) || 'all';
    const included = index => selection === 'all' || index === Number(selection);
    const values = item.points.flatMap(p => (p.components || []).filter((_, index) => included(index)));
    const min = values.length ? Math.min(...values) : 0, max = values.length ? Math.max(...values) : 1;
    const margin = max === min ? Math.max(1, Math.abs(min) * .05) : (max - min) * .08;
    const lo = min - margin, hi = max + margin;
    const x = i => left + i * (w - left - right) / Math.max(1, dates.length - 1);
    const y = v => top + (hi - v) / (hi - lo) * (h - top - bottom);
    let paths = '';
    item.meta.component_names.forEach((name, component) => {
      if (!included(component)) return;
      let d = '', open = false, isolated = '';
      item.points.forEach((p, index) => {
        const v = p.components?.[component];
        if (v == null || !Number.isFinite(v)) { open = false; return; }
        d += `${open ? 'L' : 'M'}${x(index).toFixed(2)},${y(v).toFixed(2)} `; open = true;
        const previous = item.points[index - 1]?.components?.[component];
        const next = item.points[index + 1]?.components?.[component];
        if ((previous == null || !Number.isFinite(previous)) && (next == null || !Number.isFinite(next))) {
          isolated += `<circle data-point-component="${component + 1}" cx="${x(index).toFixed(2)}" cy="${y(v).toFixed(2)}" r="3" fill="${colors[component % colors.length]}"/>`;
        }
      });
      paths += `<path data-component="${component + 1}" d="${d}" fill="none" stroke="${colors[component % colors.length]}" stroke-width="1.7"/>${isolated}`;
    });
    const svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(item.meta.category)}原始分量曲线"><line x1="${left}" y1="${h - bottom}" x2="${w - right}" y2="${h - bottom}" stroke="#a4afb8"/><text x="2" y="${top + 9}" font-size="12">${esc(number(hi))}</text><text x="2" y="${h - bottom}" font-size="12">${esc(number(lo))}</text>${paths}<text x="${left}" y="${h - 8}" font-size="12">${esc(dates[0] || '')}</text><text x="${w - right}" y="${h - 8}" text-anchor="end" font-size="12">${esc(dates.at(-1) || '')}</text></svg>`;
    const last = [...item.points].reverse().find(p => p.last_record)?.last_record;
    const selector = item.meta.component_names.length > 1 ? `<select class="component-select" data-component-select="${esc(item.meta.id)}" aria-label="${esc(item.meta.category)}显示分量"><option value="all" ${selection === 'all' ? 'selected' : ''}>全部分量（共用纵轴）</option>${item.meta.component_names.map((name, i) => `<option value="${i}" ${selection === String(i) ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select>` : '';
    return `<article class="card monitor-chart" data-series="${esc(item.meta.id)}"><h2>${esc(item.meta.category)} · ${esc(item.meta.device_names.join('/'))}</h2><div class="chart-caption">${esc(item.meta.unit || '单位待确认')} · ${number(item.record_count)} 条原始记录 ${selector}</div>${svg}<div class="chart-legend">${item.meta.component_names.map((name, i) => included(i) ? `<span style="color:${colors[i % colors.length]}">${esc(name)}</span>` : '').join('')}</div><div class="chart-caption">最后已记录：${esc(last?.raw_time || '无记录')} · 原值 ${esc(last ? Object.values(last.raw_values).join(' / ') : '无记录')}<br>期间无记录日：${number(item.points.filter(p => !p.last_record).length)}；未知分量含义保留待核</div></article>`;
  }
  function renderCharts() {
    const length = Number($('replaySlider').value) + 1;
    const dates = replay.dates.slice(0, length);
    $('replayDate').textContent = dates.at(-1) || '';
    $('monitorCharts').innerHTML = replay.series.map(s => chart({ ...s, points: s.points.slice(0, length), record_count: s.points.slice(0, length).reduce((sum, p) => sum + p.count, 0) }, dates)).join('');
  }
  async function rawRows() {
    if (!replay) return;
    const revision = ++rawRevision, seriesId = $('monitorSeries').value;
    error(''); $('rawCount').textContent = '';
    const download = $('sourceDownload');
    download.hidden = true;
    $('rawPrevious').disabled = $('rawNext').disabled = true;
    $('monitorRawRows').innerHTML = '<tr><td colspan="4">读取原始记录…</td></tr>';
    try {
      let payload;
      if (offline) {
        const rows = replay.series.find(s => s.meta.id === seriesId).points.filter(p => p.day <= currentEnd() && p.last_record).map(p => p.last_record);
        payload = { rows: rows.slice(offset, offset + PAGE_SIZE), total: rows.length };
      } else {
        const query = new URLSearchParams({ series_id: seriesId, start: replay.start, end: currentEnd(), limit: String(PAGE_SIZE), offset: String(offset) });
        payload = await api.getJson('/api/monitoring/observations?' + query);
      }
      if (revision !== rawRevision) return;
      $('monitorRawRows').innerHTML = payload.rows.map(o => `<tr><td>${esc(o.raw_time)}</td><td>${Object.entries(o.raw_values).map(([key, value]) => `${esc(key)}：${esc(value)}`).join('<br>')}</td><td>${esc(flags(o.quality_flags))}</td><td>${esc(o.source.sheet)} · 第${esc(o.source.row)}行<br>${esc(Object.entries(o.source.cells).map(([key, value]) => `${key} ${value}`).join('；'))}</td></tr>`).join('') || '<tr><td colspan="4">该范围无已记录观测</td></tr>';
      $('rawCount').textContent = `${offline ? '离线仅含每个日期最后一条代表记录；' : ''}共 ${payload.total} 条，本页 ${payload.rows.length} 条`;
      $('rawPrevious').disabled = offset === 0;
      $('rawNext').disabled = offset + payload.rows.length >= payload.total;
      if (!offline) { download.href = '/api/monitoring/source?' + new URLSearchParams({ series_id: seriesId }); download.hidden = false; }
    } catch (e) { if (revision === rawRevision) { $('monitorRawRows').innerHTML = '<tr><td colspan="4">原始记录读取失败，请重试。</td></tr>'; error(e.message); } }
  }
  async function load() {
    if (busy) return;
    setBusy(true); error('');
    try {
      const start = $('monitorStart').value, end = $('monitorEnd').value;
      if (!start || !end || start > end) throw new Error('请选择有序的开始与结束日期');
      let result;
      if (offline) {
        if (start < offline.replay.start || end > offline.replay.end) throw new Error('离线报告仅包含导出时选定的日期范围');
        result = { ...offline.replay, start, end, dates: offline.replay.dates.filter(d => d >= start && d <= end), series: offline.replay.series.map(s => ({ ...s, points: s.points.filter(p => p.day >= start && p.day <= end) })) };
      } else result = await api.getJson('/api/monitoring/replay?' + new URLSearchParams({ start, end }));
      if (!result.series?.length || !result.dates?.length) throw new Error('尚无已导入的会昌样例，请运行监测导入脚本');
      replay = result; offset = 0;
      $('replaySlider').max = replay.dates.length - 1; $('replaySlider').value = replay.dates.length - 1;
      $('aggregationNote').textContent = replay.aggregation + '。回放按原始记录日期裁切，数据实际发布时间待核。';
      renderCharts(); await rawRows();
    } catch (e) {
      replay = null; rawRevision++;
      $('monitorCharts').replaceChildren(); $('monitorRawRows').replaceChildren();
      $('rawCount').textContent = ''; $('replayDate').textContent = ''; $('aggregationNote').textContent = '';
      $('sourceDownload').hidden = true; $('rawPrevious').disabled = $('rawNext').disabled = true;
      error('读取失败：' + e.message);
    }
    finally { setBusy(false); }
  }
  async function main() {
    try {
      [catalog, graph] = offline ? [offline.catalog, offline.graph] : await Promise.all([api.getJson('/api/monitoring/catalog'), api.getJson('/api/monitoring/graph')]);
      if (!catalog.series?.length) throw new Error('会昌样例尚未导入，请先运行 scripts/import_huichang_monitoring.py');
      renderCatalog();
      $('monitorStart').value = offline?.replay.start || catalog.default_start; $('monitorEnd').value = offline?.replay.end || catalog.default_end;
      $('loadReplay').addEventListener('click', load);
      $('monitorSeries').addEventListener('change', () => { offset = 0; rawRows(); });
      $('rawPrevious').addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); rawRows(); });
      $('rawNext').addEventListener('click', () => { offset += PAGE_SIZE; rawRows(); });
      $('monitorCharts').addEventListener('change', event => { const id = event.target.dataset.componentSelect; if (id) { selectedComponents.set(id, event.target.value); renderCharts(); } });
      $('replaySlider').addEventListener('input', () => { renderCharts(); offset = 0; rawRows(); });
      $('exportMonitoring').addEventListener('click', async () => {
        if (busy || offline) return;
        setBusy(true); $('exportMessage').textContent = '正在生成报告…';
        try {
          const response = await fetch('/api/export/monitoring', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ start: replay.start, end: currentEnd() }) });
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
          const link = document.createElement('a'); link.href = result.download_url; link.download = result.file_name; link.textContent = '下载当前日期范围的离线回放报告';
          $('exportMessage').replaceChildren(link);
        } catch (e) { $('exportMessage').textContent = '报告生成失败：' + e.message; }
        finally { setBusy(false); }
      });
      await load();
    } catch (e) { error(e.message); }
  }
  main();
})();
