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
  const parameterNames = {unit_weight: "重度", cohesion: "黏聚力", friction_angle: "内摩擦角"};
  const parameterTables = (payload.related_nodes || []).filter(node => node.type === "MaterialParameterSet").map(node => {
    const p = node.props || {};
    return `<details class="detail-block"><summary>${api.escapeHtml(p.source_file || "计算参数")} · 第${api.escapeHtml(p.source_page)}页</summary><p>历史报告取值，非实时测量；适用断面与工况需核验。</p><div class="table-wrap"><table class="data-table"><thead><tr><th>材料</th><th>状态</th><th>参数</th><th>数值</th></tr></thead><tbody>${(p.parameters || []).map(v => `<tr><td>${api.escapeHtml(v.material)}</td><td>${api.escapeHtml(v.condition)}</td><td>${api.escapeHtml(parameterNames[v.parameter] || v.parameter)}</td><td>${api.escapeHtml(v.value)} ${api.escapeHtml(v.unit)}</td></tr>`).join("")}</tbody></table></div></details>`;
  }).join("");
  detail.className = "";
  detail.innerHTML = `<h3>${api.escapeHtml(props.slope_id || slope.label)}</h3>
    <p>${badge(props.entity_resolution_status)} ${badge(props.review_status)}</p>
    <div class="detail-list">
      <div class="detail-row"><small>原资料名称</small>${api.escapeHtml(props.source_alias || "缺失")}</div>
      <div class="detail-row"><small>边坡端点坐标</small>${api.escapeHtml(JSON.stringify(props.start_coordinate || "未确定"))}<br/>${api.escapeHtml(JSON.stringify(props.end_coordinate || "未确定"))}</div>
      ${props.control_points?.length ? `<div class="detail-row"><small>测量控制点（非边坡端点）</small>${api.escapeHtml(JSON.stringify(props.control_points.map(p => ({point_no:p.point_no,x:p.x,y:p.y,z:p.z}))))}</div>` : ""}
      <div class="detail-row"><small>几何</small>坡高：${api.escapeHtml(props.slope_height_raw || "缺失")}；坡度：${api.escapeHtml(props.slope_gradient_raw || "缺失")}；长度：${api.escapeHtml(props.slope_length_m ?? "缺失")} m</div>
      <div class="detail-row"><small>关联节点</small>${api.escapeHtml(Object.entries(relatedCounts).map(([k,v]) => `${k}:${v}`).join(" / ") || "无")}</div>
    </div>
    ${parameterTables ? `<h3 style="margin-top:18px">已提取的材料计算参数</h3>${parameterTables}` : ""}
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
  const coverageRows = Object.entries(evaluation.active_graph_coverage || evaluation.automatic_coverage || {});
  const discovery = evaluation.discovery || {};
  const semantic = evaluation.semantic || {};
  document.querySelector("#evaluationRows").innerHTML = [
    `<tr><td>自动发现边坡</td><td>${discovery.slopes ?? "-"} 个；唯一路线+桩号 ${discovery.unique_slope_keys ?? discovery.unique_stations ?? "-"}</td><td>${discovery.slopes && discovery.slopes === (discovery.unique_slope_keys ?? discovery.unique_stations) ? "通过" : "检查"}</td></tr>`,
    ...coverageRows.map(([name, value]) => `<tr><td>${api.escapeHtml(evaluationLabel(name))}</td><td>${api.escapeHtml(`${value.available ?? "-"}/${value.total ?? "-"}`)}</td><td>${value.coverage == null ? "未覆盖" : percent(value.coverage)}</td></tr>`),
    `<tr><td>大模型语义校验</td><td>${semantic.passed ?? 0}/${semantic.candidates ?? 0}</td><td>${semantic.pass_rate == null ? "未启用" : percent(semantic.pass_rate)}</td></tr>`,
    `<tr><td>质量门通过率</td><td>${Object.keys(evaluation.quality_gates || {}).length} 项</td><td>${percent(evaluation.quality_gate_pass_rate)}</td></tr>`,
  ].join("");
  const pageTypes = evaluation.parser?.page_type_counts || {};
  const engineering = evaluation.engineering_tables || {};
  const issueLabels = {station_unresolved: "报告通用参数，未绑定具体边坡", caption_station_mismatch: "表题桩号与章节桩号不一致", possible_text_table_status_conflict: "正文与表格结论可能不一致", text_table_status_conflict: "正文与表格结论不一致"};
  document.querySelector("#engineeringReviewRows").innerHTML = `<p>已提取 ${api.escapeHtml(engineering.tables || 0)} 张计算相关表、${api.escapeHtml(engineering.parameter_values || 0)} 个材料参数值、${api.escapeHtml(engineering.stability_results || 0)} 条工况结果。数量不等于准确率；适用范围未确定的结果暂不参与筛查。</p>${(engineering.review_candidates || []).map(r => `<div class="detail-row"><small>${api.escapeHtml(r.source_file)} · 第${api.escapeHtml(r.page)}页</small><strong>${api.escapeHtml(r.station || "未绑定具体边坡")}</strong><p>${api.escapeHtml((r.quality_issues || []).map(i => issueLabels[i] || i).join("；"))}</p></div>`).join("")}`;
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
  return ({
    registry: "边坡登记",
    coordinates: "边坡端点坐标",
    survey_control_points: "测量控制点（非端点）",
    treatment: "治理方案与安全系数",
    geometry: "坡高坡度",
    slope_type: "路堤/路堑",
    material_nature: "土质/岩质",
    slope_aspect: "坡向",
    slope_structure: "斜坡结构",
    lithology: "岩性词项",
    stratum: "地层词项",
    structural_plane: "结构面",
    vegetation: "植被情况",
    historical_deformation: "历史变形迹象",
    hydrology_baseline: "历史水文基线",
    protection_design: "防护设计",
    current_deformation: "近期变形巡检",
    stability: "稳定性工况",
    multi_source: "多来源交叉确认",
  })[code] || code;
}

async function riskPage() {
  setupRiskExport();
  const [payload, manualSchema, validation] = await Promise.all([
    api.getJson("/api/risk/screening", { summary: {}, assessments: [], boundary: {} }),
    api.getJson("/api/manual/schema", { slope_fields: [] }),
    api.getJson("/api/risk/evaluation", { status: "requires_independent_expert_gold_set", evaluated: 0, metrics: null }),
  ]);
  const rows = payload.assessments || [];
  let selectedId = rows[0]?.slope_id || null;
  const counts = payload.summary?.priority_counts || {};
  document.querySelector("#riskMetrics").innerHTML = [
    ["P1", counts.P1 || 0, "优先复核"], ["P2", counts.P2 || 0, "重点复核"],
    ["P3", counts.P3 || 0, "常规复核"], ["P4", counts.P4 || 0, "资料补录"],
    ["NOW", payload.summary?.with_current_dynamic_evidence || 0, "有近期动态证据"],
    ["CONFLICT", payload.summary?.with_stability_conflicts || 0, "稳定性资料冲突"],
  ].map(([code, value, label]) => `<article class="card metric-card risk-metric ${code.toLowerCase()}"><strong>${api.escapeHtml(value)}</strong><span>${code.startsWith("P") ? `${code} ` : ""}${label}</span></article>`).join("");
  const validationTarget = document.querySelector("#riskValidation");
  if (validation.status === "evaluated_against_independent_labels") {
    validationTarget.innerHTML = `独立专家样本已评测 <strong>${api.escapeHtml(validation.evaluated)}</strong> 个边坡；重点对象召回率 <strong>${api.escapeHtml(validation.metrics?.urgent_recall_p1_p2 ?? "—")}</strong>，P1严重漏判 <strong>${api.escapeHtml(validation.metrics?.critical_p1_miss_count ?? "—")}</strong> 个。`;
  } else {
    validationTarget.innerHTML = "<strong>当前没有独立专家风险金标准，不能报告风险判断准确率。</strong> 页面抽取率和稳定系数复核结果不会被冒充为风险准确率。";
  }

  const search = document.querySelector("#riskSearch");
  const priorityFilter = document.querySelector("#riskPriorityFilter");
  const tableBody = document.querySelector("#riskRows");

  function visibleRows() {
    const keyword = search.value.trim().toLowerCase();
    const priority = priorityFilter.value;
    return rows.filter((row) => {
      const text = [row.slope_label, row.business_id, row.route_code, row.station, ...(row.hazard_types || []), ...(row.hazard_body_types || [])].join(" ").toLowerCase();
      return (!keyword || text.includes(keyword)) && (!priority || row.screening_priority_code === priority);
    });
  }

  function renderRows() {
    const filtered = visibleRows();
    if (!filtered.length) {
      tableBody.innerHTML = `<tr><td colspan="6">没有符合条件的边坡。</td></tr>`;
      return;
    }
    tableBody.innerHTML = filtered.map((row) => `<tr data-risk-id="${api.escapeHtml(row.slope_id)}" class="${selectedId === row.slope_id ? "selected" : ""}">
      <td><span class="priority-badge ${row.screening_priority_code.toLowerCase()}">${api.escapeHtml(row.screening_priority_code)}</span><span class="table-subtext">${api.escapeHtml(row.screening_priority)}</span></td>
      <td><strong>${api.escapeHtml(row.business_id || row.slope_label)}</strong><span class="table-subtext">${api.escapeHtml(row.slope_label)}</span></td>
      <td>${api.escapeHtml([...(row.hazard_types || []), ...(row.hazard_body_types || [])].filter((value, index, all) => all.indexOf(value) === index).join("、") || "未明确")}</td>
      <td>${api.escapeHtml(row.static_susceptibility?.level || row.stability_concern || "资料不足")}<span class="table-subtext">${api.escapeHtml(row.stability_concern || "稳定性无法判断")}</span></td>
      <td>${api.escapeHtml(row.dynamic_alert?.level || "无法判断")}</td>
      <td><strong>${api.escapeHtml(row.risk_data_inventory?.available_categories ?? "—")}/${api.escapeHtml(row.risk_data_inventory?.total_categories ?? "—")} 类</strong><span class="table-subtext missing-count">首轮建议补 ${api.escapeHtml(row.risk_data_inventory?.first_round_recommended ?? 0)} 类</span></td>
    </tr>`).join("");
    tableBody.querySelectorAll("[data-risk-id]").forEach((node) => {
      node.addEventListener("click", () => {
        selectedId = node.dataset.riskId;
        renderRows();
        renderDetail(rows.find((row) => row.slope_id === selectedId));
      });
    });
  }

  function renderDetail(row) {
    const target = document.querySelector("#riskDetail");
    if (!row) {
      target.className = "card detail-empty";
      target.textContent = "未找到筛查结果。";
      return;
    }
    const basis = (row.basis || []).map((item) => {
      const evidence = item.evidence;
      const evidenceHtml = evidence ? `<blockquote class="evidence-quote">${api.escapeHtml(evidence.text || "已定位证据")}<span class="table-subtext">${api.escapeHtml(evidence.source_file || "")} · PDF第${api.escapeHtml(evidence.page)}页</span></blockquote>` : "";
      return `<div class="risk-basis"><strong>${api.escapeHtml(item.label)}</strong><span>${api.escapeHtml(item.value)}</span><small>${api.escapeHtml(item.interpretation || "")}</small>${evidenceHtml}${item.boundary ? `<small class="missing-text">${api.escapeHtml(item.boundary)}</small>` : ""}</div>`;
    }).join("") || `<p class="detail-empty">暂无足够的命中依据。</p>`;
    const allGaps = row.critical_data_gaps || [];
    const sourceGapCount = allGaps.filter((item) => item.gap_scope !== "interface_pending").length;
    const interfaceGapCount = allGaps.length - sourceGapCount;
    const inventory = row.risk_data_inventory || {};
    const basisWarning = row.screening_basis_status?.minimum_basis_met === false
      ? `<div class="notice error">当前只有自动识别的候选灾害标签，尚无可核验的灾害文字或独立工程信息；应先补录基础资料，不能据此判断风险高低。</div>`
      : "";
    const topGaps = allGaps.slice(0, 5);
    const topGapList = topGaps.map((item) => `<li><strong>${api.escapeHtml(item.label || item.code)} <small class="gap-scope">${api.escapeHtml(item.gap_scope_label || "")}</small></strong><span>${api.escapeHtml(item.collection_hint || "")}</span></li>`).join("");
    const gaps = allGaps.map((item) => `<div class="field-item risk-gap-item">
      <div class="risk-gap-head"><strong>${api.escapeHtml(item.label || item.code)}</strong><span class="gap-scope">${api.escapeHtml(item.gap_scope_label || "")}</span><span class="gap-priority priority-${api.escapeHtml(item.improvement_priority ?? 2)}">${api.escapeHtml(item.improvement_priority_label || "基础补录")}</span>${badge(item.status || "missing")}</div>
      <small class="gap-role">作用：${api.escapeHtml(item.accuracy_role || "基础信息")}</small>
      <p>${api.escapeHtml(item.why_it_matters || "")}</p>
      <small><b>建议补采：</b>${api.escapeHtml(item.collection_hint || "从资料或现场补录，并保留来源和时间。")}</small>
      ${item.interface ? `<small class="gap-interface">待接接口：${api.escapeHtml(item.interface)}</small>` : ""}
    </div>`).join("");
    const scenarios = (row.stability_scenarios || []).map((item) => `<tr><td>${api.escapeHtml(item.condition)}</td><td>${item.fs == null ? "—" : api.escapeHtml(item.fs)}</td><td>${api.escapeHtml(item.status || "未判定")}</td><td>${api.escapeHtml(item.review_note || "历史报告，时效待核")}</td></tr>`).join("");
    const profiles = (row.hazard_profiles || []).map((item) => `<div class="risk-basis"><strong>${api.escapeHtml(item.hazard_type)}</strong><span>${item.missing_key_inputs?.length ? `关键输入待补：${api.escapeHtml(item.missing_key_inputs.join("、"))}` : "现有关键输入已覆盖"}</span><small>${api.escapeHtml(item.boundary || "")}</small></div>`).join("");
    const conflicts = (row.data_conflicts || []).map((item) => `<div class="notice error"><strong>${api.escapeHtml(item.scenario)}稳定性资料待核对</strong><br/>Fs：${api.escapeHtml((item.fs_values || []).join("、") || "—")}；状态：${api.escapeHtml((item.statuses || []).join("、") || "—")}。${api.escapeHtml(item.handling || "")}</div>`).join("");
    const gate = row.formal_risk_gate || {};
    const calculation = row.stability_calculation_readiness || {};
    target.className = "card risk-detail";
    target.innerHTML = `
      <div class="card-title-row"><div><h2>${api.escapeHtml(row.slope_label)}</h2><p class="section-subtitle">${api.escapeHtml(row.business_id || "")}</p></div><div class="detail-actions"><span class="priority-badge ${row.screening_priority_code.toLowerCase()}">${api.escapeHtml(row.screening_priority_code)} ${api.escapeHtml(row.screening_priority)}</span><button class="button secondary manual-edit-toggle" type="button">补录/修正</button></div></div>
      <div class="notice">${row.formal_risk_level ? `正式风险等级：${api.escapeHtml(row.formal_risk_level)}` : `正式风险等级：暂无法确定。${row.provisional_risk_matrix_level ? `参考矩阵为“${api.escapeHtml(row.provisional_risk_matrix_level)}”，但尚未通过发布门槛。` : "当前只给出分项关注和人工复核优先级。"}`}</div>
      ${basisWarning}
      ${conflicts}
      <div class="risk-data-callout">
        <h3>为了进一步判断风险值，建议补充的数据</h3>
        <p>共定义 <strong>${api.escapeHtml(inventory.total_categories ?? 18)}</strong> 类可提高研判准确度的信息，当前已具备 <strong>${api.escapeHtml(inventory.available_categories ?? "—")}</strong> 类。其余信息不要求一次性全部补齐；建议首轮先补下面${topGaps.length}类。</p>
        <ol>${topGapList || "<li>当前未列出优先补充项。</li>"}</ol>
        <small class="callout-footnote">其余未具备信息：该边坡资料 ${sourceGapCount} 类，待接接口/补录 ${interfaceGapCount} 类。可在下方展开查看。</small>
      </div>
      <div class="manual-editor" hidden><div class="loading">正在读取人工补录数据…</div></div>
      <div class="detail-list compact">
        <div class="detail-row"><small>静态易发性关注</small>${api.escapeHtml(row.static_susceptibility?.level || "资料不足")}<span class="table-subtext">${api.escapeHtml(row.static_susceptibility?.interpretation || "")}</span></div>
        <div class="detail-row"><small>近期动态状态</small>${api.escapeHtml(row.dynamic_alert?.level || "无法判断")}<span class="table-subtext">${api.escapeHtml(row.dynamic_alert?.interpretation || "")}</span></div>
        <div class="detail-row"><small>暴露后果</small>${api.escapeHtml(row.consequence_assessment?.level || "无法判断")}<span class="table-subtext">${api.escapeHtml(row.consequence_assessment?.interpretation || "")}</span></div>
        <div class="detail-row"><small>稳定性关注</small>${api.escapeHtml(row.stability_concern)}</div>
        <div class="detail-row"><small>数据时效</small>${api.escapeHtml(row.temporal_validity?.label || "无带日期的近期数据")}<span class="table-subtext">${api.escapeHtml(row.temporal_validity?.observed_at || "未提供日期")} · ${api.escapeHtml(row.temporal_validity?.policy || "")}</span></div>
        <div class="detail-row"><small>证据置信度</small>${api.escapeHtml(row.confidence)}（定性，不是概率）</div>
        <div class="detail-row"><small>复核理由</small>${api.escapeHtml((row.screening_reasons || []).join("；"))}</div>
      </div>
      <div class="detail-block"><h3>分灾种检查</h3><div class="risk-basis-list">${profiles}</div></div>
      <details class="detail-block"><summary>稳定性程序化计算：${calculation.ready ? "输入已具备，待审核运行" : `尚缺${(calculation.blocking_items || []).length}项硬条件`}</summary><p class="section-subtitle">${api.escapeHtml(calculation.boundary || "")}</p><ul class="boundary-list">${Object.entries(calculation.checks || {}).map(([code, passed]) => `<li>${passed ? "✓" : "✗"} ${api.escapeHtml(code)}</li>`).join("")}</ul></details>
      <details class="detail-block"><summary>正式结论门槛：${gate.ready ? "已满足，仍需审核" : `尚缺${(gate.blocking_items || []).length}项`}</summary><p class="section-subtitle">${api.escapeHtml(gate.message || "")}</p><ul class="boundary-list">${Object.entries(gate.checks || {}).map(([code, passed]) => `<li>${passed ? "✓" : "✗"} ${api.escapeHtml(code)}</li>`).join("")}</ul></details>
      <div class="detail-block"><h3>命中依据</h3><div class="risk-basis-list">${basis}</div></div>
      ${scenarios ? `<div class="detail-block"><h3>稳定性工况</h3><div class="table-wrap"><table class="data-table"><thead><tr><th>工况</th><th>Fs</th><th>原报告结论</th><th>时效边界</th></tr></thead><tbody>${scenarios}</tbody></table></div></div>` : ""}
      <div class="detail-block"><h3>建议动作</h3><ol class="action-list">${(row.recommended_action || []).map((item) => `<li>${api.escapeHtml(item)}</li>`).join("")}</ol></div>
      <details class="detail-block risk-gap-details"><summary>查看全部${allGaps.length}项缺失数据及采集方法</summary><p class="section-subtitle">已按预计作用排序；制度项不替代现场数据。</p><div class="risk-gap-list">${gaps || "<p>未列出。</p>"}</div></details>
      <div class="detail-block"><h3>结论边界</h3><ul class="boundary-list">${(row.limitations || []).map((item) => `<li>${api.escapeHtml(item)}</li>`).join("")}</ul></div>`;
    setupManualSlopeEditor(target, row);
  }

  async function setupManualSlopeEditor(target, row) {
    const toggle = target.querySelector(".manual-edit-toggle");
    const editor = target.querySelector(".manual-editor");
    toggle.addEventListener("click", async () => {
      editor.hidden = !editor.hidden;
      if (editor.hidden || editor.dataset.loaded === "true") return;
      editor.dataset.loaded = "true";
      const [record, detail] = await Promise.all([
        api.getJson(`/api/manual/slopes/${encodeURIComponent(row.slope_id)}`, { fields: {} }),
        api.getJson(`/api/slopes/${encodeURIComponent(row.slope_id)}`, { slope: { props: {} } }),
      ]);
      const existing = detail.slope?.props || {};
      const controls = (manualSchema.slope_fields || []).map((spec) => {
        const saved = record.fields?.[spec.code] ?? "";
        const current = existing[spec.code] ?? (spec.code === "slope_height_m" ? existing.slope_height_max_m : spec.code === "slope_gradient_deg" ? existing.slope_gradient_max_deg : "");
        const help = current !== "" && current != null ? `当前有效值：${typeof current === "object" ? JSON.stringify(current) : current}` : "当前缺失";
        const input = spec.type === "enum"
          ? `<select name="${api.escapeHtml(spec.code)}"><option value="">不设置人工值</option>${(spec.options || []).map((option) => `<option value="${api.escapeHtml(option)}" ${saved === option ? "selected" : ""}>${api.escapeHtml(option)}</option>`).join("")}</select>`
          : `<input name="${api.escapeHtml(spec.code)}" type="${spec.type === "number" ? "number" : "text"}" value="${api.escapeHtml(saved)}" ${spec.type === "number" ? `step="any" min="${api.escapeHtml(spec.min ?? "")}" max="${api.escapeHtml(spec.max ?? "")}"` : ""}/>`;
        return `<label class="manual-field"><span>${api.escapeHtml(spec.label)}${spec.dynamic ? " · 近期" : ""}</span>${input}<small>${api.escapeHtml(help)}</small></label>`;
      }).join("");
      editor.innerHTML = `<form class="manual-slope-form">
        <div class="manual-editor-head"><div><h3>人工补录数据</h3><p>仅填写需要覆盖或补充的字段。近期数据必须填写观测日期；空白字段继续使用PDF自动提取值。</p></div><span class="badge ${record.updated_at ? "approved" : "pending"}">${record.updated_at ? "已有人工记录" : "尚未补录"}</span></div>
        <div class="manual-meta-grid"><label>近期数据日期<input name="observed_at" type="date" value="${api.escapeHtml(record.observed_at || "")}"/></label><label>来源/备注<input name="note" value="${api.escapeHtml(record.note || "")}" placeholder="例如：2026年现场巡检记录"/></label></div>
        <div class="manual-field-grid">${controls}</div>
        <div class="action-row"><button class="button" type="submit">保存并重新计算</button><button class="button ghost reset-manual" type="button">撤销全部人工值</button><span class="manual-save-status"></span></div>
      </form>`;
      const form = editor.querySelector(".manual-slope-form");
      const status = editor.querySelector(".manual-save-status");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const data = new FormData(form);
        const fields = {};
        (manualSchema.slope_fields || []).forEach((spec) => {
          const value = String(data.get(spec.code) || "").trim();
          if (value !== "") fields[spec.code] = spec.type === "number" ? Number(value) : value;
        });
        status.textContent = "正在保存…";
        try {
          const response = await fetch(`/api/manual/slopes/${encodeURIComponent(row.slope_id)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fields, observed_at: data.get("observed_at"), note: data.get("note") }) });
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
          status.textContent = "保存成功，正在刷新结果…";
          window.location.reload();
        } catch (error) { status.textContent = `保存失败：${error.message}`; }
      });
      editor.querySelector(".reset-manual").addEventListener("click", async () => {
        if (!window.confirm("确定撤销该边坡的全部人工补录值吗？PDF自动提取结果不会删除。")) return;
        const response = await fetch(`/api/manual/slopes/${encodeURIComponent(row.slope_id)}`, { method: "DELETE" });
        const result = await response.json();
        if (!response.ok) { status.textContent = `撤销失败：${result.error || result.status}`; return; }
        window.location.reload();
      });
    });
  }

  const canDo = (payload.boundary?.can_do || []).map((item) => `<li>${api.escapeHtml(item)}</li>`).join("");
  const cannotDo = (payload.boundary?.cannot_do || []).map((item) => `<li>${api.escapeHtml(item)}</li>`).join("");
  document.querySelector("#riskBoundary").innerHTML = `<div><h3>当前可以做</h3><ul>${canDo}</ul></div><div><h3>当前不能做</h3><ul>${cannotDo}</ul></div>`;
  search.addEventListener("input", renderRows);
  priorityFilter.addEventListener("change", renderRows);
  renderRows();
  renderDetail(rows[0]);
}

function setupRiskExport() {
  const button = document.querySelector("#exportRiskBtn");
  if (!button || button.dataset.ready === "true") return;
  button.dataset.ready = "true";
  const fileName = document.querySelector("#riskExportFileName");
  const status = document.querySelector("#riskExportStatus");
  const link = document.querySelector("#riskExportDownloadLink");
  button.addEventListener("click", async () => {
    button.disabled = true;
    link.hidden = true;
    status.textContent = "正在生成风险研判离线 HTML…";
    try {
      const response = await fetch("/api/export/risk-standalone", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_name: fileName.value.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `导出失败：HTTP ${response.status}`);
      link.href = payload.download_url;
      link.download = payload.file_name;
      link.textContent = `再次下载 ${payload.file_name}`;
      link.hidden = false;
      status.textContent = `已生成 ${payload.file_name}，包含 ${payload.slopes || 0} 个边坡，大小 ${(Number(payload.bytes || 0) / 1024).toFixed(1)} KB。`;
      link.click();
    } catch (error) {
      status.textContent = `导出失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
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
