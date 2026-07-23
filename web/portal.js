const api = window.SlopeKGApi;
const page = document.body.dataset.page;

function badge(status) {
  return `<span class="badge ${api.escapeHtml(status)}">${api.escapeHtml(api.statusLabel(status))}</span>`;
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function metric(value, label) {
  return `<div class="card metric-card"><strong>${api.escapeHtml(value)}</strong><span>${api.escapeHtml(label)}</span></div>`;
}

async function dashboard() {
  const [status, completeness, interfaces] = await Promise.all([
    api.getJson("/api/status", {}),
    api.getJson("/api/completeness", { summary: {}, reports: [] }),
    api.getJson("/api/interfaces", { interfaces: [], status_counts: {} }),
  ]);
  const graph = status.graph || {};
  const summary = completeness.summary || {};
  const reserved = (interfaces.interfaces || []).filter((item) => item.status !== "implemented").length;
  document.querySelector("#dashboardMetrics").innerHTML = [
    metric(summary.slopes ?? graph.node_type_counts?.Slope ?? 0, "候选边坡"),
    metric(graph.nodes ?? "-", "图谱节点"),
    metric(percent(summary.average_risk_data_completeness), "风险数据平均完整度"),
    metric(reserved, "待接入/待确认接口"),
  ].join("");
  document.querySelector("#dashboardBoundary").innerHTML = [
    ["实例数据", `${status.parsed?.documents ?? 0}份PDF、${status.parsed?.pages ?? 0}页已进入逐页自适应解析`],
    ["实体状态", `${summary.slopes ?? graph.node_type_counts?.Slope ?? 0}个候选边坡，边界和编号可继续现场复核`],
    ["动态数据", "监测、近期气象、巡检和养护接口已预留但未接入"],
    ["风险能力", "仅提供就绪检查，不输出正式风险等级"],
  ].map(([name, value]) => `<div class="detail-row"><small>${name}</small>${value}</div>`).join("");
  setupStandaloneExport();
}

function setupStandaloneExport() {
  const button = document.querySelector("#exportStandaloneBtn");
  if (!button || button.dataset.ready === "true") return;
  button.dataset.ready = "true";
  const fileName = document.querySelector("#exportFileName");
  const graphMode = document.querySelector("#exportGraphMode");
  const status = document.querySelector("#exportStatus");
  const link = document.querySelector("#exportDownloadLink");
  button.addEventListener("click", async () => {
    button.disabled = true;
    link.hidden = true;
    status.textContent = "正在生成离线HTML…";
    try {
      const response = await fetch("/api/export/standalone", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_name: fileName.value.trim(), graph_mode: graphMode.value }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `导出失败：HTTP ${response.status}`);
      link.href = payload.download_url;
      link.download = payload.file_name;
      link.textContent = `再次下载 ${payload.file_name}`;
      link.hidden = false;
      status.textContent = `已生成 ${payload.file_name}，大小 ${(Number(payload.bytes || 0) / 1024).toFixed(1)} KB。`;
      link.click();
    } catch (error) {
      status.textContent = `导出失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

let slopeState = { slopes: [], reports: [], selected: null };

async function slopesPage() {
  const [slopes, completeness] = await Promise.all([
    api.getJson("/api/slopes", { rows: [] }),
    api.getJson("/api/completeness", { reports: [] }),
  ]);
  slopeState.slopes = slopes.rows || [];
  slopeState.reports = completeness.reports || [];
  document.querySelector("#slopeSearch").addEventListener("input", renderSlopeRows);
  document.querySelector("#readinessFilter").addEventListener("change", renderSlopeRows);
  renderSlopeRows();
}

function renderSlopeRows() {
  const q = document.querySelector("#slopeSearch").value.trim().toLowerCase();
  const filter = document.querySelector("#readinessFilter").value;
  const reports = new Map(slopeState.reports.map((row) => [row.slope_id, row]));
  const rows = slopeState.slopes.filter((slope) => {
    const report = reports.get(slope.id) || {};
    const matches = `${slope.label} ${JSON.stringify(slope.props || {})}`.toLowerCase().includes(q);
    return matches && (filter === "all" || report.risk_readiness === filter);
  });
  const body = document.querySelector("#slopeRows");
  body.innerHTML = rows.map((slope) => {
    const props = slope.props || {};
    const report = reports.get(slope.id) || {};
    return `<tr data-slope-id="${api.escapeHtml(slope.id)}"><td><strong>${api.escapeHtml(props.slope_id || slope.label)}</strong><br/><small>${api.escapeHtml(props.source_alias || "")}</small></td><td>${api.escapeHtml(`${props.start_station_raw || "?"}～${props.end_station_raw || "?"} ${props.side || ""}`)}</td><td>${api.escapeHtml(props.slope_length_m ?? "缺失")} m</td><td><div class="progress"><span style="width:${percent(report.risk_data_completeness)}"></span></div><small>${percent(report.risk_data_completeness)}</small></td><td>${badge(report.risk_readiness || "not_ready")}</td></tr>`;
  }).join("") || `<tr><td colspan="5">没有匹配的边坡。</td></tr>`;
  body.querySelectorAll("tr[data-slope-id]").forEach((row) => row.addEventListener("click", () => loadSlopeDetail(row.dataset.slopeId, row)));
}

async function loadSlopeDetail(slopeId, rowElement) {
  document.querySelectorAll("tr[data-slope-id]").forEach((row) => row.classList.toggle("selected", row === rowElement));
  const detail = document.querySelector("#slopeDetail");
  detail.innerHTML = "加载中…";
  const payload = await api.getJson(`/api/slopes/${encodeURIComponent(slopeId)}`, null);
  const slope = payload.slope;
  const props = slope.props || {};
  const report = payload.completeness || { fields: [] };
  const missing = (report.fields || []).filter((item) => item.status !== "available");
  const relatedCounts = (payload.related_nodes || []).reduce((acc, node) => { acc[node.type] = (acc[node.type] || 0) + 1; return acc; }, {});
  detail.className = "";
  detail.innerHTML = `<h3>${api.escapeHtml(props.slope_id || slope.label)}</h3>
    <p>${badge(props.entity_resolution_status)} ${badge(props.review_status)}</p>
    <div class="detail-list">
      <div class="detail-row"><small>原资料名称</small>${api.escapeHtml(props.source_alias || "缺失")}</div>
      <div class="detail-row"><small>坐标</small>${api.escapeHtml(JSON.stringify(props.start_coordinate || "缺失"))}<br/>${api.escapeHtml(JSON.stringify(props.end_coordinate || "缺失"))}</div>
      <div class="detail-row"><small>几何</small>坡高：${api.escapeHtml(props.slope_height_raw || "缺失")}；坡度：${api.escapeHtml(props.slope_gradient_raw || "缺失")}；长度：${api.escapeHtml(props.slope_length_m ?? "缺失")} m</div>
      <div class="detail-row"><small>关联节点</small>${api.escapeHtml(Object.entries(relatedCounts).map(([k,v]) => `${k}:${v}`).join(" / ") || "无")}</div>
    </div>
    <h3 style="margin-top:18px">缺失或待接入（${missing.length}）</h3>
    <div class="field-grid">${missing.map((item) => `<div class="field-item"><strong>${api.escapeHtml(item.label_zh)}</strong>${badge(item.status)}<br/><small>${api.escapeHtml(item.requirement || "")}${item.interface ? ` · ${api.escapeHtml(item.interface)}` : ""}</small></div>`).join("")}</div>
    <p style="margin-top:16px"><a href="./graph.html">在关系图谱中查看</a></p>`;
}

async function dataStatusPage() {
  const [completeness, interfaces, evaluation, status] = await Promise.all([
    api.getJson("/api/completeness", { summary: {}, reports: [] }),
    api.getJson("/api/interfaces", { interfaces: [] }),
    api.getJson("/api/evaluation", { automatic_coverage: {}, parser: {}, discovery: {}, semantic: {} }),
    api.getJson("/api/status", { parsed: {} }),
  ]);
  const summary = completeness.summary || {};
  const rows = interfaces.interfaces || [];
  document.querySelector("#statusMetrics").innerHTML = [
    metric(summary.slopes || 0, "候选边坡"),
    metric(percent(summary.average_risk_data_completeness), "平均完整度"),
    metric(rows.filter((row) => row.status === "implemented").length, "已实现接口"),
    metric(rows.filter((row) => row.status !== "implemented").length, "待接入/待确认接口"),
  ].join("");
  document.querySelector("#interfaceRows").innerHTML = rows.map((row) => `<tr><td class="code-path">${api.escapeHtml(row.path)}</td><td>${api.escapeHtml(row.method)}</td><td>${api.escapeHtml(row.domain)}</td><td>${badge(row.status)}</td><td>${api.escapeHtml(row.description)}</td></tr>`).join("");
  const coverageRows = Object.entries(evaluation.automatic_coverage || {});
  const discovery = evaluation.discovery || {};
  const semantic = evaluation.semantic || {};
  document.querySelector("#evaluationRows").innerHTML = [
    `<tr><td>自动发现边坡</td><td>${discovery.slopes ?? "-"} 个；唯一路线+桩号 ${discovery.unique_slope_keys ?? discovery.unique_stations ?? "-"}</td><td>${discovery.slopes && discovery.slopes === (discovery.unique_slope_keys ?? discovery.unique_stations) ? "通过" : "检查"}</td></tr>`,
    ...coverageRows.map(([name, value]) => `<tr><td>${api.escapeHtml(evaluationLabel(name))}</td><td>${api.escapeHtml(`${value.available ?? "-"}/${value.total ?? "-"}`)}</td><td>${value.coverage == null ? "未覆盖" : percent(value.coverage)}</td></tr>`),
    `<tr><td>大模型语义校验</td><td>${semantic.passed ?? 0}/${semantic.candidates ?? 0}</td><td>${semantic.pass_rate == null ? "未启用" : percent(semantic.pass_rate)}</td></tr>`,
    `<tr><td>质量门通过率</td><td>${Object.keys(evaluation.quality_gates || {}).length} 项</td><td>${percent(evaluation.quality_gate_pass_rate)}</td></tr>`,
  ].join("");
  const pageTypes = evaluation.parser?.page_type_counts || {};
  const ocrCounts = status.parsed?.ocr_status_counts || {};
  const strategyDescription = { native_text: "原生文字+章节解析", table: "原生版面+表格抽取", drawing: "矢量图纸+标题栏局部OCR", scanned: "扫描页整页OCR", mixed: "原生文字与OCR合并" };
  const strategyRows = Object.entries(pageTypes).map(([type, count]) => `<div class="detail-row"><small>${api.escapeHtml(strategyDescription[type] || type)}</small><strong>${api.escapeHtml(count)} 页</strong> ${badge(type === "native_text" || type === "table" ? "implemented" : "pending")}</div>`);
  strategyRows.push(`<div class="detail-row"><small>OCR执行状态</small>完成 ${api.escapeHtml(ocrCounts.done || 0)} / 失败 ${api.escapeHtml(ocrCounts.failed || 0)} / 待执行 ${api.escapeHtml(ocrCounts.pending || 0)} ${badge((ocrCounts.failed || 0) > 0 ? "failed" : "pending")}</div>`);
  document.querySelector("#parserStrategyRows").innerHTML = strategyRows.join("");
  document.querySelector("#missingRows").innerHTML = (completeness.reports || []).map((report) => {
    const missing = (report.fields || []).filter((item) => item.status !== "available").slice(0, 8);
    return `<tr><td>${api.escapeHtml(report.label)}</td><td>${percent(report.risk_data_completeness)}</td><td>${report.blocking_count}</td><td>${missing.map((item) => `${api.escapeHtml(item.label_zh)} ${badge(item.status)}`).join(" ")}</td></tr>`;
  }).join("");
}

function evaluationLabel(code) {
  return ({ registry: "边坡登记", coordinates: "控制点坐标", treatment: "治理方案与安全系数", geometry: "坡高坡度", lithology: "岩性词项", stratum: "地层词项", multi_source: "多来源交叉确认" })[code] || code;
}

async function riskPage() {
  const [slopes, readiness] = await Promise.all([
    api.getJson("/api/slopes", { rows: [] }),
    api.getJson("/api/risk/readiness", { blocking_global_items: [], reports: [] }),
  ]);
  const select = document.querySelector("#riskSlopeSelect");
  select.innerHTML += (slopes.rows || []).map((slope) => `<option value="${api.escapeHtml(slope.id)}">${api.escapeHtml(slope.props?.slope_id || slope.label)}</option>`).join("");
  document.querySelector("#globalBlocking").innerHTML = (readiness.blocking_global_items || []).map((item) => `<div class="detail-row">${badge("reserved_blocked")} ${api.escapeHtml(globalBlockingLabel(item))}</div>`).join("");
  document.querySelector("#checkRiskBtn").addEventListener("click", async () => {
    const slopeId = select.value;
    if (!slopeId) return;
    const payload = await api.getJson(`/api/risk/readiness?slope_id=${encodeURIComponent(slopeId)}`, {});
    const report = payload.reports?.[0];
    const result = document.querySelector("#riskResult");
    if (!report) { result.textContent = "未找到边坡完整性报告。"; return; }
    const blockers = (report.fields || []).filter((item) => item.status !== "available" && ["risk_required", "scenario_required"].includes(item.requirement));
    result.className = "";
    result.innerHTML = `<h2>${api.escapeHtml(report.label)}</h2><p>${badge(report.risk_readiness)} 风险数据完整度：<strong>${percent(report.risk_data_completeness)}</strong></p><div class="notice">当前不能输出正式风险等级。以下数据缺失或尚未接入。</div><div class="field-grid">${blockers.map((item) => `<div class="field-item"><strong>${api.escapeHtml(item.label_zh)}</strong>${badge(item.status)}<br/><small>${item.interface ? `预留接口：${api.escapeHtml(item.interface)}` : "需从工程资料或现场调查补充"}</small></div>`).join("")}</div>`;
  });
}

function globalBlockingLabel(code) {
  return ({ approved_risk_rule_set: "正式风险规则尚未确认", dynamic_data_connectors: "动态监测、气象和巡检数据源尚未连接", human_review_workflow: "风险结论人工审核与发布流程尚未建立" })[code] || code;
}

(async () => {
  try {
    if (page === "dashboard") await dashboard();
    if (page === "slopes") await slopesPage();
    if (page === "data-status") await dataStatusPage();
    if (page === "risk") await riskPage();
    const initialStatus = await api.getJson("/api/status", {});
    let version = `${initialStatus.updated_at || ""}|${initialStatus.publication_stage || ""}|${initialStatus.graph?.nodes || ""}`;
    setInterval(async () => {
      if (document.hidden) return;
      const latest = await api.getJson("/api/status", {});
      const next = `${latest.updated_at || ""}|${latest.publication_stage || ""}|${latest.graph?.nodes || ""}`;
      if (version && next !== version) window.location.reload();
      version = next;
    }, 5000);
  } catch (error) {
    console.error(error);
    document.querySelectorAll(".loading").forEach((node) => { node.textContent = `加载失败：${error.message}`; });
  }
})();
