(() => {
  const $ = id => document.getElementById(id), esc = window.SlopeKGApi.escapeHtml;
  const offline = window.BASELINE_OFFLINE;
  let report = null, options = [], revision = 0, selectedKey = null;
  const number = v => v == null ? '无定义' : Number(v).toLocaleString('zh-CN', { maximumSignificantDigits: 7 });
  function clear() {
    report = null; options = [];
    for (const id of ['baselineComponent', 'baselineProtocol', 'baselineMetrics', 'baselineCoverage', 'baselineRows', 'baselineChart']) $(id).textContent = '';
    $('baselineComponent').disabled = $('baselineSplit').disabled = true;
    $('baselineSource').hidden = true;
  }
  function chart(rows) {
    if (!rows.length) return '<p>本段没有满足共同计分条件的目标。</p>';
    const start = Date.parse(rows[0].target_day), end = Date.parse(rows.at(-1).target_day);
    const values = rows.flatMap(r => [r.actual, ...Object.values(r.predictions)]);
    const min = Math.min(...values), max = Math.max(...values), margin = max === min ? Math.max(1, Math.abs(min) * .05) : (max - min) * .08;
    const lo = min - margin, hi = max + margin;
    const x = d => 60 + (Date.parse(d) - start) * 720 / Math.max(86400000, end - start);
    const y = v => 20 + (hi - v) * 220 / (hi - lo);
    const lines = [['actual', '#265c99'], ['persistence', '#18785d'], ['recent_drift', '#b86d1f']].map(([key, color]) => {
      let d = '', previous = null, dots = '';
      rows.forEach((r, i) => {
        const stamp = Date.parse(r.target_day), value = key === 'actual' ? r.actual : r.predictions[key];
        d += `${previous != null && stamp - previous === 86400000 ? 'L' : 'M'}${x(r.target_day)},${y(value)} `;
        const next = rows[i + 1] ? Date.parse(rows[i + 1].target_day) : null;
        if ((previous == null || stamp - previous !== 86400000) && (next == null || next - stamp !== 86400000)) dots += `<circle cx="${x(r.target_day)}" cy="${y(value)}" r="3" fill="${color}"/>`;
        previous = stamp;
      });
      return `<path data-method="${key}" d="${d}" stroke="${color}" fill="none" stroke-width="1.7"/>${dots}`;
    }).join('');
    return `<svg viewBox="0 0 810 280" role="img" aria-label="历史实际原值和两种一步预测对照" style="width:100%"><text x="0" y="24" font-size="12">${esc(number(hi))}</text><text x="0" y="240" font-size="12">${esc(number(lo))}</text>${lines}<text x="60" y="268" font-size="12">${esc(rows[0].target_day)}</text><text x="780" y="268" text-anchor="end" font-size="12">${esc(rows.at(-1).target_day)}</text></svg>`;
  }
  function render() {
    if (!report || !options.length) return;
    const selected = options[Number($('baselineComponent').value)], { series, component } = selected;
    selectedKey = `${series.series_id}:${component.component}`;
    const split = $('baselineSplit').value, score = component.metrics[split];
    const rows = component.predictions.filter(r => r.split === split);
    const source = report.sources.find(s => s.id === series.series_id);
    const warnings = (component.flags || []).map(f => f.includes('all_zero') ? '训练段全零，不能证明传感器正常。' : '训练段恒定，缩放误差无定义。');
    $('baselineProtocol').innerHTML = [
      ['对象', `${series.file_name} · 分量${component.component} · ${series.unit || '原值单位待核'}；分量含义待核`],
      ['时间划分', component.split ? `训练截至 ${component.split.train_end}，验证截至 ${component.split.validation_end}，末段截至 ${component.split.end}` : '资料不足，未建立时间划分'],
      ['预测规则', '用目标日期前一日的原值，以及该日之前连续7日记录。只有两种方法都可计算的同一目标才计分。'],
      ['未做的处理', '不填补、平滑、删零或删跳变。重复日期和不合法记录保留来源，但不计分。'],
      ['实验有效性', '这些历史原值已用于开发和查看，不是独立盲测。实际发布时间未知，不能证明在线可用提前量。'],
      ['版本', `实验 ${report.run_id}；源表 SHA-256 ${source.sha256}`],
      ['特别说明', warnings.join(' ') || '累计/重置基准及跳变原因未知，结果仅代表原值序列。'],
    ].map(([k, v]) => `<div class="detail-row"><small>${esc(k)}</small>${esc(v)}</div>`).join('');
    $('baselineSource').hidden = !!offline;
    if (!offline) $('baselineSource').href = '/api/monitoring/source?series_id=' + encodeURIComponent(series.series_id);
    if (!score) {
      $('baselineMetrics').innerHTML = '<tr><td colspan="6">可用连续记录不足，不生成评分。</td></tr>';
      $('baselineCoverage').textContent = ''; $('baselineRows').textContent = ''; $('baselineChart').innerHTML = chart([]); return;
    }
    $('baselineMetrics').innerHTML = [['persistence', '保持上次值'], ['recent_drift', '延续最近7日平均变化']].map(([key, name]) => {
      const m = score.methods[key];
      return `<tr><td>${name}</td><td>${esc(m.count)}</td><td>${esc(number(m.mae))}</td><td>${esc(number(m.rmse))}</td><td>${esc(number(m.bias))}</td><td>${esc(number(m.mase))}</td></tr>`;
    }).join('');
    const c = score.coverage, improvement = score.drift_mae_improvement_fraction;
    $('baselineCoverage').textContent = `本段 ${c.calendar_days || 0} 个日历日，${c.valid_target_records || 0} 个有效目标，${c.persistence_evaluable || 0} 个保持值可计算目标，两方法共同计分 ${c.common_evaluable || 0} 个。7日变化相对保持值的MAE改善：${improvement == null ? '无定义（没有目标或保持值误差为零）' : number(improvement * 100) + '%'}。未计分目标不算预测正确。`;
    $('baselineChart').innerHTML = chart(rows);
    $('baselineRows').innerHTML = rows.slice(-20).map(r => {
      const ref = report.source_references[r.target_id].source;
      return `<tr><td>${esc(r.origin_day)} → ${esc(r.target_day)}</td><td>${esc(number(r.actual))}</td><td>${esc(number(r.predictions.persistence))}</td><td>${esc(number(r.predictions.recent_drift))}</td><td>${esc(ref.sheet)} · 第${esc(ref.row)}行<br/>${esc(Object.entries(ref.cells).map(([k, v]) => k + ' ' + v).join('；'))}</td></tr>`;
    }).join('') || '<tr><td colspan="5">本段没有共同计分目标。</td></tr>';
  }
  async function load() {
    const priorKey = selectedKey;
    const current = ++revision;
    clear(); $('baselineError').hidden = true; $('baselineStatus').textContent = '正在读取离线实验…';
    try {
      let payload = offline;
      if (!payload) {
        const response = await fetch('/api/monitoring/baseline', { cache: 'no-store' });
        payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      }
      if (current !== revision) return;
      if (!payload.series?.length) { $('baselineStatus').textContent = '实验尚未生成。运行 scripts/evaluate_huichang_baseline.py 后再读取；没有实验结果不代表风险低。'; return; }
      if (payload.risk_eligible !== false || payload.deployment_ready !== false) throw new Error('实验边界不一致，停止显示');
      report = payload;
      options = report.series.flatMap(series => series.components.map(component => ({ series, component })));
      $('baselineComponent').innerHTML = options.map((o, i) => `<option value="${i}">${esc(o.series.category)} · 分量${o.component.component}</option>`).join('');
      const selected = options.findIndex(o => `${o.series.series_id}:${o.component.component}` === priorKey);
      $('baselineComponent').value = String(Math.max(0, selected));
      $('baselineComponent').disabled = $('baselineSplit').disabled = false;
      $('baselineBoundary').textContent = report.boundary;
      $('baselineStatus').textContent = `${offline ? '离线报告' : '已发布实验'} · ${report.series.length}份已接入原表，${options.length}个独立数值分量；没有加入新资料。`;
      render();
    } catch (e) { if (current === revision) { clear(); $('baselineStatus').textContent = '实验暂不可读，旧评分已清除。'; $('baselineError').hidden = false; $('baselineError').textContent = e.message; } }
  }
  $('baselineComponent').addEventListener('change', render); $('baselineSplit').addEventListener('change', render);
  $('baselineReload').addEventListener('click', load);
  if (offline) { document.querySelector('.site-header').hidden = true; $('baselineReload').hidden = true; }
  load();
})();
