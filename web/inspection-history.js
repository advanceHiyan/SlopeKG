(function () {
  'use strict';
  const api = window.SlopeKGApi, esc = api.escapeHtml, $ = id => document.getElementById(id);
  const size = 20;
  let offset = 0, revision = 0, catalog;
  function message(text) { $('historyError').textContent = text || ''; $('historyError').hidden = !text; }
  async function load() {
    const current = ++revision;
    message(''); $('historyCount').textContent = '';
    $('historyRows').replaceChildren(); $('historyPrevious').disabled = $('historyNext').disabled = true;
    $('historySearch').disabled = true;
    try {
      const start = $('historyStart').value, end = $('historyEnd').value;
      if (start && end && start > end) throw new Error('开始日期不能晚于结束日期');
      const query = new URLSearchParams({asset_type: $('historyType').value, q: $('historyQuery').value, start, end, include_undated: String($('historyUndated').checked), limit: String(size), offset: String(offset)});
      const data = await api.getJson('/api/inspections/history?' + query);
      if (current !== revision) return;
      $('historyRows').innerHTML = data.rows.map(row => {
        const extras = Object.entries(row.raw_fields).filter(([key]) => !['时间','起止桩号','方向及位置','存在问题'].includes(key)).map(([key, value]) => `${esc(key)}：${esc(value == null ? '未提供' : value)}`).join('<br>');
        return `<tr><td>${esc(String(row.raw_time || '日期未提供').replace('T',' '))}<br>${esc(row.asset_type)}</td><td>${esc(row.station_text || '未提供')}<br>${esc(row.direction || '未提供')}</td><td>${esc(row.problem || '未提供')}</td><td>${esc(row.source.sheet)} · 第${esc(row.source.row)}行<br>问题原文 ${esc(row.source.cells['存在问题'])}<details><summary>其他原始字段</summary>${extras}</details></td></tr>`;
      }).join('') || '<tr><td colspan="4">该条件没有历史记录</td></tr>';
      $('historyCount').textContent = `共 ${data.total} 条，本页 ${data.rows.length} 条`;
      $('historyPrevious').disabled = offset === 0; $('historyNext').disabled = offset + data.rows.length >= data.total;
    } catch (error) { if (current === revision) message('读取失败：' + error.message); }
    finally { if (current === revision) $('historySearch').disabled = false; }
  }
  async function main() {
    try {
      catalog = await api.getJson('/api/inspections/history/catalog');
      if (!catalog.count) throw new Error('历史巡查尚未导入，请运行历史巡查导入脚本');
      $('historyBoundary').textContent = catalog.boundary;
      $('historySummary').textContent = `${catalog.start} 至 ${catalog.end} · 边坡 ${catalog.counts_by_type['边坡'] || 0} 条 · 桥梁 ${catalog.counts_by_type['桥梁'] || 0} 条；日期缺失/待解析 ${catalog.invalid_date_rows || 0} 条。记录以原表工作表和行号区分，桩号范围不自动当作单个边坡。`;
      $('historyStart').value = catalog.start; $('historyEnd').value = catalog.end;
      $('historySource').href = '/api/inspections/history/source'; $('historySource').hidden = false;
      $('historyFilters').addEventListener('submit', event => { event.preventDefault(); offset = 0; load(); });
      $('historyPrevious').addEventListener('click', () => { offset = Math.max(0, offset - size); load(); });
      $('historyNext').addEventListener('click', () => { offset += size; load(); });
      await load();
    } catch (error) { message(error.message); $('historySearch').disabled = true; }
  }
  main();
})();
