(function () {
  const api = window.SlopeKGApi;
  const page = document.body.dataset.page;
  let library = null;
  let documents = new Map();
  let selectedId = null;
  let pollTimer = null;

  const labels = {
    candidate_not_approved: "候选库·未审核",
    candidate_pending_review: "待审核",
    candidate_pending_formula_review: "公式待核验",
    approved: "已批准",
    source_cataloged: "已登记",
    partial_fuzzy: "模糊证据·待核验",
    official_standard: "正式规范",
    technical_guideline: "技术指南",
    duplicate_reference: "重复版本核对",
    web_print_reference: "网页打印参考",
    research_reference: "研究资料",
    taxonomy_reference: "分类资料",
    engineering_application: "工程应用",
    primary: "核心依据",
    supporting: "辅助依据",
    application: "应用案例",
    threshold_candidate: "阈值候选",
    threshold_table_row: "阈值表行",
    classification: "分级规则",
    decision_rule: "判定规则",
    manual_declarative: "人工结构化规则",
    draft: "草稿",
    disabled: "已停用",
    executable: "结构可执行",
    stored_not_executable: "仅保存·不可执行",
    manual_source: "人工来源",
  };

  function label(value) {
    return labels[value] || api.statusLabel(value) || value || "未填写";
  }

  function e(value) {
    return api.escapeHtml(value);
  }

  function encodedPath(path) {
    return "/" + String(path || "").replaceAll("\\", "/").split("/").map(encodeURIComponent).join("/");
  }

  function sourceLinks(item) {
    const doc = documents.get(item.document_id) || {};
    const pageNo = Number(item.page || 1);
    const source = doc.path ? `${encodedPath(doc.path)}#page=${pageNo}` : "";
    const image = item.evidence_page_id
      ? `/output/demo/rules/pages/${encodeURIComponent(item.evidence_page_id)}.png`
      : "";
    const links = [];
    if (source) links.push(`<a href="${e(source)}" target="_blank" rel="noopener">打开原始PDF第${pageNo}页</a>`);
    if (image) links.push(`<a href="${e(image)}" target="_blank" rel="noopener">查看提取证据页</a>`);
    return links.length ? `<div class="evidence-links">${links.join("")}</div>` : "";
  }

  function metric(value, title) {
    return `<article class="card metric-card"><strong>${e(value ?? 0)}</strong><span>${e(title)}</span></article>`;
  }

  async function loadLibrary() {
    library = await api.getJson("/api/rules/library", {
      publication_status: "not_generated",
      execution_enabled: false,
      documents: [],
      rules: [],
      formulas: [],
      stats: {},
    });
    documents = new Map((library.documents || []).map((row) => [row.id, row]));
    return library;
  }

  function renderRuleMetrics() {
    const stats = library.stats || {};
    document.getElementById("ruleMetrics").innerHTML = [
      metric(stats.rules, "候选规则"),
      metric(stats.evidence_validated_rules, "证据已定位"),
      metric(stats.risk_engine_candidates, "风险引擎候选"),
      metric(stats.manual_rules || 0, `人工规则（启用${stats.executable_manual_rules || 0}）`),
    ].join("");
    const publication = document.getElementById("rulePublication");
    publication.textContent = label(library.publication_status);
    publication.className = `badge ${e(library.publication_status)}`;
  }

  function ruleSearchText(row) {
    const doc = documents.get(row.document_id) || {};
    return [
      row.title, row.rule_kind, row.hazard_type, row.scenario, row.condition_text,
      row.output_text, row.evidence_quote, row.document_code, doc.title,
    ].join(" ").toLowerCase();
  }

  function filteredRules() {
    const keyword = document.getElementById("ruleSearch").value.trim().toLowerCase();
    const kind = document.getElementById("ruleKind").value;
    const status = document.getElementById("ruleStatus").value;
    return (library.rules || []).filter((row) =>
      (!keyword || ruleSearchText(row).includes(keyword))
      && (!kind || row.rule_kind === kind)
      && (!status || row.approval_status === status));
  }

  function renderRuleRows() {
    const rows = filteredRules();
    const body = document.getElementById("ruleRows");
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4">没有符合条件的候选规则。</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((row) => {
      const doc = documents.get(row.document_id) || {};
      const scene = [row.hazard_type, row.scenario].filter(Boolean).join(" / ") || "未明确";
      const sourceText = row.extraction_method === "manual_input" ? "人工录入" : (row.document_code || doc.code || "未知");
      const pageText = row.page ? `PDF第${e(row.page)}页` : label(row.execution_status);
      return `<tr data-rule-id="${e(row.id)}" class="${selectedId === row.id ? "selected" : ""}">
        <td><strong>${e(row.title || "未命名规则")}</strong><span class="table-subtext">${e(label(row.rule_kind))}</span></td>
        <td>${e(scene)}</td>
        <td>${e(sourceText)}<span class="table-subtext">${e(pageText)}</span></td>
        <td><span class="badge ${e(row.approval_status)}">${e(label(row.approval_status))}</span></td>
      </tr>`;
    }).join("");
    body.querySelectorAll("[data-rule-id]").forEach((row) => {
      row.addEventListener("click", () => selectRule(row.dataset.ruleId));
    });
  }

  function selectRule(id) {
    selectedId = id;
    renderRuleRows();
    const row = (library.rules || []).find((item) => item.id === id);
    if (!row) return;
    const doc = documents.get(row.document_id) || {};
    const inputs = Array.isArray(row.inputs) && row.inputs.length ? row.inputs.join("、") : "未结构化";
    const exceptions = Array.isArray(row.exceptions) && row.exceptions.length ? row.exceptions.join("；") : "未提取到例外条款";
    const detail = document.getElementById("ruleDetail");
    const isManual = row.extraction_method === "manual_input";
    const unsupported = (row.unsupported_reasons || []).join("；");
    detail.className = "card rule-detail";
    detail.innerHTML = `
      <h2>${e(row.title || "未命名规则")}</h2>
      <div class="detail-meta">
        <span class="badge ${e(row.approval_status)}">${e(label(row.approval_status))}</span>
        <span class="badge ${e(row.evidence_validation)}">${e(label(row.evidence_validation))}</span>
        <span class="badge">${e(label(row.rule_kind))}</span>
        <span class="badge">${e(row.extraction_method || "未知提取方式")}</span>
      </div>
      <div class="detail-list compact">
        <div class="detail-row"><small>来源</small>${isManual ? "人工录入" : `${e(row.document_code || doc.code || "未知")} ${e(doc.title || "")} · PDF第${e(row.page)}页`}</div>
        <div class="detail-row"><small>输入字段</small>${e(inputs)}</div>
        <div class="detail-row"><small>适用场景</small>${e([row.hazard_type, row.scenario].filter(Boolean).join(" / ") || "未明确")}</div>
        <div class="detail-row"><small>执行状态</small>${e(label(row.execution_status || (row.execution_enabled ? "executable" : "未启用")))}${unsupported ? `<span class="table-subtext missing-text">${e(unsupported)}</span>` : ""}</div>
      </div>
      <div class="detail-block"><h3>条件</h3><p>${e(row.condition_text || "尚未结构化")}</p></div>
      <div class="detail-block"><h3>输出</h3><p>${e(row.output_text || "尚未结构化")}</p></div>
      <div class="detail-block"><h3>例外/限制</h3><p>${e(exceptions)}</p></div>
      <div class="detail-block"><h3>${isManual ? "逻辑说明" : "原文证据"}</h3><blockquote class="evidence-quote">${e(row.evidence_quote || "无")}</blockquote>${isManual ? "" : sourceLinks(row)}</div>
      <div class="notice">${isManual ? (row.execution_enabled ? "该规则已审核启用，可调整P1—P4复核顺序，但不会生成正式风险等级。" : "该规则已经保存，但未参与风险排序；请检查状态及不可执行原因。") : "此项为自动提取候选。完成规范有效性、数值、比较符号和适用条件审核前，系统不会执行该规则。"}</div>
      ${isManual ? `<button class="button ghost delete-manual-rule" type="button">删除这条人工规则</button>` : ""}`;
    const deleteButton = detail.querySelector(".delete-manual-rule");
    if (deleteButton) deleteButton.addEventListener("click", async () => {
      if (!window.confirm("确定删除这条人工规则吗？")) return;
      const response = await fetch(`/api/manual/rules/${encodeURIComponent(row.id)}`, { method: "DELETE" });
      if (!response.ok) return;
      window.location.reload();
    });
  }

  async function setupManualRuleForm() {
    const schema = await api.getJson("/api/manual/schema", { rule_fields: [], rule_operators: [] });
    const form = document.getElementById("manualRuleForm");
    const container = document.getElementById("manualRuleConditions");
    document.getElementById("toggleRuleForm").addEventListener("click", () => { form.hidden = !form.hidden; });
    function addCondition() {
      const row = document.createElement("div");
      row.className = "manual-condition-row";
      row.innerHTML = `<select class="condition-field">${schema.rule_fields.map((item) => `<option value="${e(item.code)}">${e(item.label)}</option>`).join("")}</select><select class="condition-operator">${schema.rule_operators.map((item) => `<option value="${e(item.code)}">${e(item.label)}</option>`).join("")}</select><input class="condition-value" placeholder="阈值或文字；多值用逗号分隔"/><button class="button ghost remove-condition" type="button">移除</button>`;
      row.querySelector(".remove-condition").addEventListener("click", () => { if (container.children.length > 1) row.remove(); });
      container.appendChild(row);
    }
    addCondition();
    document.getElementById("addRuleCondition").addEventListener("click", addCondition);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const conditions = [...container.querySelectorAll(".manual-condition-row")].map((row) => ({ field: row.querySelector(".condition-field").value, operator: row.querySelector(".condition-operator").value, value: row.querySelector(".condition-value").value }));
      const status = document.getElementById("manualRuleStatus");
      status.textContent = "正在校验并保存…";
      try {
        const response = await fetch("/api/manual/rules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: data.get("title"), match: data.get("match"), priority: data.get("priority"), approval_status: data.get("approval_status"), reason: data.get("reason"), logic_note: data.get("logic_note"), conditions }) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        status.textContent = result.message;
        window.setTimeout(() => window.location.reload(), 700);
      } catch (error) { status.textContent = `保存失败：${error.message}`; }
    });
  }

  async function initRules() {
    await setupManualRuleForm();
    await loadLibrary();
    renderRuleMetrics();
    const kinds = [...new Set((library.rules || []).map((row) => row.rule_kind).filter(Boolean))].sort();
    document.getElementById("ruleKind").insertAdjacentHTML(
      "beforeend",
      kinds.map((kind) => `<option value="${e(kind)}">${e(label(kind))}</option>`).join(""),
    );
    ["ruleSearch", "ruleKind", "ruleStatus"].forEach((id) => {
      document.getElementById(id).addEventListener(id === "ruleSearch" ? "input" : "change", renderRuleRows);
    });
    renderRuleRows();
    if ((library.rules || []).length) selectRule(library.rules[0].id);
  }

  function formulaSearchText(row) {
    const doc = documents.get(row.document_id) || {};
    return [
      row.name, row.expression_text, row.latex, row.applicability,
      row.evidence_quote, row.document_code, doc.title,
      ...(row.variables || []).flatMap((item) => [item.symbol, item.meaning, item.unit]),
    ].join(" ").toLowerCase();
  }

  function renderFormulaRows() {
    const keyword = document.getElementById("formulaSearch").value.trim().toLowerCase();
    const rows = (library.formulas || []).filter((row) => !keyword || formulaSearchText(row).includes(keyword));
    const target = document.getElementById("formulaRows");
    if (!rows.length) {
      target.innerHTML = `<div class="detail-empty">没有符合条件的公式候选。</div>`;
      return;
    }
    target.innerHTML = rows.map((row) => `
      <div class="formula-card ${selectedId === row.id ? "selected" : ""}" data-formula-id="${e(row.id)}">
        <strong>${e(row.name || "公式候选")}</strong>
        <div class="formula-expression">${e(row.expression_text || row.latex || "未识别")}</div>
        <small>${e(row.document_code || "未知来源")} · PDF第${e(row.page)}页 · ${e(label(row.approval_status))}</small>
      </div>`).join("");
    target.querySelectorAll("[data-formula-id]").forEach((row) => {
      row.addEventListener("click", () => selectFormula(row.dataset.formulaId));
    });
  }

  function selectFormula(id) {
    selectedId = id;
    renderFormulaRows();
    const row = (library.formulas || []).find((item) => item.id === id);
    if (!row) return;
    const doc = documents.get(row.document_id) || {};
    const variables = Array.isArray(row.variables) ? row.variables : [];
    const detail = document.getElementById("formulaDetail");
    detail.className = "card rule-detail";
    detail.innerHTML = `
      <h2>${e(row.name || "公式候选")}</h2>
      <div class="detail-meta"><span class="badge ${e(row.approval_status)}">${e(label(row.approval_status))}</span><span class="badge">${e(row.extraction_method || "未知提取方式")}</span></div>
      <div class="formula-expression">${e(row.expression_text || row.latex || "未识别")}</div>
      <div class="detail-block"><h3>适用条件</h3><p>${e(row.applicability || "尚未结构化")}</p></div>
      <div class="detail-block"><h3>变量</h3>
        ${variables.length ? `<div class="table-wrap"><table class="data-table variable-table"><thead><tr><th>符号</th><th>含义</th><th>单位</th></tr></thead><tbody>${variables.map((item) =>
          `<tr><td>${e(item.symbol)}</td><td>${e(item.meaning)}</td><td>${e(item.unit || "—")}</td></tr>`).join("")}</tbody></table></div>` : `<p>变量尚未可靠识别。</p>`}
      </div>
      <div class="detail-block"><h3>来源与证据</h3><p>${e(row.document_code || doc.code || "未知")} ${e(doc.title || "")} · PDF第${e(row.page)}页</p><blockquote class="evidence-quote">${e(row.evidence_quote || "无")}</blockquote>${sourceLinks(row)}</div>
      <div class="notice">公式必须逐符号核对原页，确认上下标、分式、变量定义和单位后才能实现为计算函数。</div>`;
  }

  async function initFormulas() {
    await loadLibrary();
    const stats = library.stats || {};
    document.getElementById("formulaMetrics").innerHTML = [
      metric(stats.formulas, "公式候选"),
      metric(stats.threshold_tables, "阈值表"),
      metric(stats.ocr_pages, "OCR证据页"),
      metric(stats.native_pages, "原生文本页"),
    ].join("");
    document.getElementById("formulaSearch").addEventListener("input", renderFormulaRows);
    renderFormulaRows();
    if ((library.formulas || []).length) selectFormula(library.formulas[0].id);
  }

  function renderSourceMetrics() {
    const stats = library.stats || {};
    const primary = (library.documents || []).filter((row) => row.priority === "primary").length;
    document.getElementById("sourceMetrics").innerHTML = [
      metric(stats.documents, "已登记资料"),
      metric(primary, "核心规范/指南"),
      metric(stats.selected_pages, "已抽取证据页"),
      metric((stats.ocr_pages || 0) + " / " + (stats.native_pages || 0), "OCR页 / 原生文本页"),
    ].join("");
  }

  function renderSources() {
    const rows = library.documents || [];
    const target = document.getElementById("sourceRows");
    if (!rows.length) {
      target.innerHTML = `<tr><td colspan="6">尚未生成规则资料目录，请执行一次提取。</td></tr>`;
      return;
    }
    target.innerHTML = rows.map((row) => `
      <tr>
        <td><strong>${e(row.code || row.title)}</strong><span class="table-subtext">${e(row.title)}</span><span class="source-path">${e(row.path)}</span></td>
        <td>${e(row.authority)}<span class="table-subtext">${e(label(row.priority))} / ${e(label(row.source_type))}</span></td>
        <td>${e(row.pages)}</td>
        <td>${e(row.selected_page_count || 0)}<span class="table-subtext">${e((row.selected_pages || []).join("、") || "未选页")}</span></td>
        <td>${e((row.topics || []).join("、"))}</td>
        <td><span class="badge ${e(row.review_status)}">${e(label(row.review_status))}</span></td>
      </tr>`).join("");
  }

  function setJob(job) {
    const progress = Number(job.progress || 0);
    document.getElementById("ruleJobBar").style.width = `${Math.max(0, Math.min(100, progress))}%`;
    document.getElementById("ruleJobMessage").textContent = job.status === "failed"
      ? `提取失败：${job.error || job.message || "未知错误"}`
      : `${job.message || "等待任务"}${job.status && job.status !== "none" ? `（${progress}%）` : ""}`;
    const running = ["queued", "running"].includes(job.status);
    document.getElementById("runRuleExtraction").disabled = running;
    if (running) {
      window.clearTimeout(pollTimer);
      pollTimer = window.setTimeout(() => pollJob(job.id), 900);
    }
  }

  async function pollJob(id) {
    const job = await api.getJson(`/api/rules/jobs/${encodeURIComponent(id)}`, { status: "failed", error: "无法读取任务状态" });
    setJob(job);
    if (job.status === "completed") {
      await loadLibrary();
      renderSourceMetrics();
      renderSources();
    }
  }

  async function startExtraction() {
    const button = document.getElementById("runRuleExtraction");
    button.disabled = true;
    document.getElementById("ruleJobMessage").textContent = "正在提交规则提取任务…";
    try {
      const response = await fetch("/api/rules/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          use_llm: document.getElementById("ruleUseLlm").checked,
          force_ocr: document.getElementById("ruleForceOcr").checked,
          llm_model: "deepseek-v4-flash",
        }),
      });
      const job = await response.json();
      if (!response.ok && response.status !== 409) throw new Error(job.error || `HTTP ${response.status}`);
      setJob(job);
    } catch (error) {
      setJob({ status: "failed", error: error.message, progress: 0 });
    }
  }

  async function initSources() {
    await loadLibrary();
    renderSourceMetrics();
    renderSources();
    document.getElementById("runRuleExtraction").addEventListener("click", startExtraction);
    const latest = await api.getJson("/api/rules/jobs/latest", { status: "none", progress: 0 });
    if (latest.status !== "none") setJob(latest);
  }

  const initializers = { rules: initRules, formulas: initFormulas, "rule-sources": initSources };
  if (initializers[page]) {
    initializers[page]().catch((error) => {
      const main = document.querySelector(".page-main");
      main.insertAdjacentHTML("afterbegin", `<div class="notice error">规则库页面加载失败：${e(error.message)}</div>`);
    });
  }
})();
