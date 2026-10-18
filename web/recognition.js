(function () {
  'use strict';
  const {getJson, escapeHtml: esc} = window.SlopeKGApi;
  const byId = id => document.getElementById(id);
  const model = byId('recognitionModel');
  const filesBox = byId('recognitionFiles');
  const errorBox = byId('recognitionError');
  const run = byId('recognitionRun');
  const prepare = byId('recognitionPrepare');
  const allow = byId('recognitionAllowExternal');
  const selected = new Set();
  let profiles = [], files = [], busy = false, sending = false, offset = 0, catalogGeneration = 0, resultGeneration = 0, lastFinished = '';
  const limit = 30;
  const statuses = {verified_model:'自动核验通过 · 待人工确认', pending:'待定', rejected:'已拒绝'};
  const runStatuses = {not_generated:'尚未生成', running:'执行中', prepared:'仅本地准备', completed:'流程已完成', partial:'部分完成', failed:'执行失败', stopped_by_user:'已停止'};

  function showError(message) {
    errorBox.textContent = message || '';
    errorBox.hidden = !message;
  }

  function updateActions() {
    const exists = profiles.some(p => p.id === model.value);
    const blocked = busy || sending || !selected.size || !exists;
    prepare.disabled = blocked;
    run.disabled = blocked || !allow.checked;
    byId('recognitionSelectedCount').textContent = `${selected.size}份`;
    for (const input of filesBox.querySelectorAll('input')) input.disabled = busy || sending;
    model.disabled = busy || sending;
    allow.disabled = busy || sending;
  }

  function renderFiles() {
    const query = byId('recognitionFileSearch').value.toLowerCase();
    const visible = files.filter(f => f.path.toLowerCase().includes(query));
    filesBox.innerHTML = visible.length ? visible.map(f => `<label class="recognition-file"><input type="checkbox" value="${esc(f.path)}" ${selected.has(f.path) ? 'checked' : ''} /><span>${esc(f.path)}</span><small>${(f.size_bytes / 1024 ** 2).toFixed(1)} MB</small></label>`).join('') : '<p class="detail-empty">没有匹配的PDF；可先上传资料。</p>';
    updateActions();
  }

  function renderEndpoint() {
    const chosen = profiles.find(p => p.id === model.value);
    byId('recognitionEndpoint').textContent = chosen ? `${chosen.model} · ${chosen.endpoint}${chosen.credential_configured ? '' : ' · 尚未配置凭据，仍可本地准备'}` : '没有可用的本地模型配置。';
    updateActions();
  }

  async function loadCatalog() {
    const generation = ++catalogGeneration;
    try {
      const [catalog, inventory] = await Promise.all([getJson('/api/recognition/profiles'), getJson('/api/recognition/documents')]);
      if (generation !== catalogGeneration) return;
      const previous = model.value;
      profiles = catalog.profiles || [];
      files = inventory.documents || [];
      const paths = new Set(files.map(f => f.path));
      for (const path of selected) if (!paths.has(path)) selected.delete(path);
      model.innerHTML = profiles.map(p => `<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');
      if (profiles.some(p => p.id === previous)) model.value = previous;
      else if (profiles.some(p => p.id === 'deepseek')) model.value = 'deepseek';
      renderEndpoint(); renderFiles();
    } catch (error) {
      if (generation !== catalogGeneration) return;
      profiles = []; files = []; model.innerHTML = '';
      filesBox.textContent = `资料加载失败：${error.message}`;
      byId('recognitionEndpoint').textContent = '模型配置暂不可读，请刷新重试。';
      updateActions();
    }
  }

  function integer(id, min, max) {
    const value = Number(byId(id).value);
    if (!Number.isInteger(value) || value < min || value > max) throw new Error(`请输入${min}～${max}的整数`);
    return value;
  }

  async function start(prepared) {
    if (busy || sending) return;
    sending = true; updateActions(); showError('');
    try {
      const options = {recognizer:'multimodal', input_pdfs:[...selected], model_profile:model.value,
        prepare_only:prepared, allow_external:!prepared && allow.checked,
        visual_settings:{max_calls:integer('recognitionMaxCalls',1,100000), model_concurrency:integer('recognitionConcurrency',1,32)}};
      const response = await fetch('/api/pipeline/jobs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(options)});
      const job = await response.json();
      if (!response.ok && response.status !== 409) throw new Error(job.error || `HTTP ${response.status}`);
      window.dispatchEvent(new CustomEvent('pipeline:job', {detail:job}));
      if (response.status === 409) showError('已有任务正在执行，已显示该任务进度；本次没有重复启动。');
    } catch (error) { showError(error.message); }
    finally { sending = false; updateActions(); }
  }

  async function loadResults() {
    const generation = ++resultGeneration;
    try {
      const status = byId('recognitionStatusFilter').value;
      const payload = await getJson(`/api/recognition?status=${encodeURIComponent(status)}&offset=${offset}&limit=${limit}`);
      if (generation !== resultGeneration) return;
      const summary = payload.summary || {};
      byId('recognitionResultNote').textContent = `${runStatuses[payload.processing_status] || payload.processing_status}。${payload.metric_note || '自动通过不是人工准确率。'}`;
      byId('recognitionMetrics').innerHTML = [['facts','候选'],['verified_model','自动通过'],['pending','待定'],['rejected','拒绝']].map(([key,label]) => `<div class="metric-card"><strong>${esc(summary[key] ?? 0)}</strong><span>${label}</span></div>`).join('');
      byId('recognitionRows').innerHTML = payload.rows?.length ? payload.rows.map(row => {
        const physicalPage = Number(row.page);
        const href = row.source_path && Number.isInteger(physicalPage) && physicalPage > 0 ? `/data/rawPDF/${row.source_path.split('/').map(encodeURIComponent).join('/')}#page=${physicalPage}` : null;
        const source = href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(row.file_name)} · 第${physicalPage}页</a>` : `${esc(row.file_name)} · 第${esc(row.page)}页`;
        const unit = `${row.unit || ''}${row.unit_basis ? '/' + row.unit_basis : ''}`;
        return `<tr><td>${source}</td><td><strong>${esc(row.object)}</strong><small class="table-subtext">${esc(row.scope)} · ${esc(row.role)}</small></td><td>${esc(row.field)}<br><strong>${esc(row.value)} ${esc(unit)}</strong><small class="table-subtext">${esc([row.qualifier,row.condition,row.time].filter(Boolean).join('；'))}</small></td><td><span class="badge ${row.status === 'rejected' ? 'missing' : 'pending'}">${esc(statuses[row.status] || row.status)}</span><details><summary>原文与检查依据</summary><p>${esc(row.quote)}</p><p>${esc((row.support_quotes || []).join('；'))}</p><small>${esc([row.reason,...(row.quality_issues || [])].filter(Boolean).join('；'))}</small></details></td></tr>`;
      }).join('') : '<tr><td colspan="4" class="detail-empty">当前筛选下没有候选。仅本地准备不会生成识别事实。</td></tr>';
      byId('recognitionPageInfo').textContent = `${payload.count ? offset + 1 : 0}～${Math.min(offset + limit,payload.count || 0)} / ${payload.count || 0}条`;
      byId('recognitionPrev').disabled = offset === 0;
      byId('recognitionNext').disabled = offset + limit >= (payload.count || 0);
    } catch (error) {
      if (generation !== resultGeneration) return;
      byId('recognitionMetrics').textContent = '';
      byId('recognitionRows').innerHTML = '<tr><td colspan="4" class="detail-empty">结果暂不可读，请刷新重试。</td></tr>';
      byId('recognitionPrev').disabled = true;
      byId('recognitionNext').disabled = true;
      byId('recognitionPageInfo').textContent = '暂不可读';
      byId('recognitionResultNote').textContent = `结果读取失败：${error.message}`;
    }
  }

  filesBox.addEventListener('change', event => {
    if (event.target.type !== 'checkbox') return;
    if (event.target.checked) selected.add(event.target.value); else selected.delete(event.target.value);
    updateActions();
  });
  byId('recognitionFileSearch').addEventListener('input',renderFiles);
  model.addEventListener('change',() => { allow.checked = false; renderEndpoint(); });
  allow.addEventListener('change',updateActions);
  prepare.addEventListener('click',() => start(true));
  run.addEventListener('click',() => start(false));
  byId('recognitionStatusFilter').addEventListener('change',() => { offset = 0; loadResults(); });
  byId('recognitionPrev').addEventListener('click',() => { offset = Math.max(0,offset-limit); loadResults(); });
  byId('recognitionNext').addEventListener('click',() => { offset += limit; loadResults(); });
  byId('refreshBtn').addEventListener('click',() => { loadCatalog(); loadResults(); });
  window.addEventListener('pipeline:busy',event => { busy = event.detail; updateActions(); });
  window.addEventListener('pipeline:finished',event => {
    const key = `${event.detail.id}:${event.detail.updated_at}:${event.detail.status}`;
    if (key !== lastFinished) { lastFinished = key; loadCatalog(); loadResults(); }
  });
  loadCatalog(); loadResults();
})();
