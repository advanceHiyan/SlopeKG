const knowledgeBase = window.EXPERT_KNOWLEDGE_BASE || { summary: {}, sources: [], items: [] };
const rules = (knowledgeBase.items || []).filter(item => item.kind === "rule");
const formulas = (knowledgeBase.items || []).filter(item => item.kind === "formula");
const sources = knowledgeBase.sources || [];
const PAGE_SIZE = 10;
const state = { filter: "all", query: "", page: 1 };

const ruleList = document.querySelector("#ruleList");
const formulaList = document.querySelector("#formulaList");
const sourceList = document.querySelector("#sourceList");
const filters = document.querySelector("#ruleFilters");
const pagination = document.querySelector("#rulePagination");
const searchInput = document.querySelector("#globalSearch");
const modalBackdrop = document.querySelector("#modalBackdrop");
const modalTitle = document.querySelector("#modalTitle");
const modalKicker = document.querySelector("#modalKicker");
const modalBody = document.querySelector("#modalBody");
const modalClose = document.querySelector("#modalClose");
let lastFocusedElement = null;

const labelMap = {
  all: "全部规则",
  hazard: "灾种判定",
  assessment: "危险性评估",
  investigation: "调查要求",
  general: "通用规则",
};
const tagClass = { hazard: "purple", assessment: "amber", investigation: "green", general: "" };

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[char]);
}

function cleanText(value = "") {
  return String(value)
    .replace(/\s+/g, " ")
    .replace(/\s+([，。；：、）])/g, "$1")
    .replace(/([（])\s+/g, "$1")
    .trim();
}

function shorten(value, length) {
  const text = cleanText(value).replace(/…+$/, "");
  return text.length > length ? `${text.slice(0, length).trim()}…` : text;
}

function ruleHeading(item) {
  const evidence = cleanText(item.evidence || item.desc || "");
  const supplied = cleanText(item.title || "").replace(/^((?:\d+\.)+\d+)\s+\1\s+/, "$1 ");
  const firstStop = evidence.search(/[。；]/);
  const firstSentence = firstStop > 12 ? evidence.slice(0, firstStop) : evidence;
  const candidate = supplied.endsWith("…") || supplied.length < 8 ? firstSentence : supplied;
  return shorten(candidate, 92);
}

function formulaHeading(item) {
  const evidence = cleanText(item.evidence || item.formula || "");
  const tableMatch = evidence.match(/(表\s*\d+\s*(?:（续）)?\s*[^。；]{2,58}?表)(?=\s)/);
  if (tableMatch) return cleanText(tableMatch[1]);
  const conditionMatch = evidence.match(/(?:滑体平均坡度|主支沟交角|稳定系数|沉降速率|坡高|地震基本烈度)[^。；]{0,54}/);
  if (conditionMatch) return shorten(conditionMatch[0], 58);
  return `${item.source_location}定量判据`;
}

function evidenceHtml(value) {
  const text = cleanText(value);
  const structured = text
    .replace(/([。；])\s*/g, "$1\n")
    .replace(/\s+(?=(?:\d+\.)+\d+\s)/g, "\n")
    .replace(/\s+(?=[a-zA-Z][）)]\s)/g, "\n");
  return structured
    .split(/\n+/)
    .map(paragraph => paragraph.trim())
    .filter(Boolean)
    .map(paragraph => `<p>${escapeHtml(paragraph)}</p>`)
    .join("");
}

function updateSummary() {
  const summary = knowledgeBase.summary || {};
  const ruleCount = summary.rules || rules.length;
  const formulaCount = summary.formulas || formulas.length;
  const sourceCount = summary.sources || sources.length;
  document.querySelector("#ruleCount").textContent = ruleCount;
  document.querySelector("#formulaCount").textContent = formulaCount;
  document.querySelector("#sourceCount").textContent = sourceCount;
  document.querySelector("#ruleNavCount").textContent = ruleCount;
  document.querySelector("#formulaNavCount").textContent = formulaCount;
  document.querySelector("#sourceNavCount").textContent = sourceCount;
  document.querySelector("#formulaSectionCount").textContent = `${formulaCount} 条`;
  document.querySelector("#syncDate").textContent = (knowledgeBase.generated_at || "--").replaceAll("-", ".");
  document.querySelector("#sourceInfo").textContent = sources[0]
    ? `${sources[0].pages} 页国标 PDF · OCR 已完成`
    : "暂无数据源";
}

function buildFilters() {
  const counts = rules.reduce((result, rule) => {
    result[rule.type] = (result[rule.type] || 0) + 1;
    return result;
  }, {});
  const keys = ["all", "hazard", "assessment", "investigation", "general"]
    .filter(key => key === "all" || counts[key]);
  filters.innerHTML = keys.map(key => `
    <button class="filter ${key === state.filter ? "active" : ""}" data-filter="${key}">
      ${labelMap[key]} <b>${key === "all" ? rules.length : counts[key]}</b>
    </button>
  `).join("");
  filters.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    state.page = 1;
    buildFilters();
    renderRules();
  }));
}

function filteredRules() {
  const query = state.query.trim().toLowerCase();
  return rules.filter(rule => {
    const matchType = state.filter === "all" || rule.type === state.filter;
    const text = `${rule.title} ${rule.desc} ${rule.evidence} ${rule.source_location}`.toLowerCase();
    return matchType && (!query || text.includes(query));
  });
}

function bindInteractiveCards(selector, callback) {
  document.querySelectorAll(selector).forEach(card => {
    const activate = () => callback(card);
    card.addEventListener("click", activate);
    card.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
}

function renderRules() {
  const visible = filteredRules();
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  state.page = Math.min(state.page, pageCount);
  const start = (state.page - 1) * PAGE_SIZE;
  const pageItems = visible.slice(start, start + PAGE_SIZE);

  ruleList.innerHTML = pageItems.length ? pageItems.map((item, index) => `
    <article class="rule-card" data-rule-id="${escapeHtml(item.id)}" tabindex="0" role="button" aria-label="查看完整规则：${escapeHtml(ruleHeading(item))}">
      <div class="rule-number">${String(start + index + 1).padStart(2, "0")}</div>
      <div class="rule-content">
        <div class="rule-card-top">
          <span class="tag ${tagClass[item.type] || ""}">${escapeHtml(item.label)}</span>
          <span class="rule-location">${escapeHtml(item.source_location)}</span>
        </div>
        <h3>${escapeHtml(ruleHeading(item))}</h3>
        <p class="rule-excerpt">${escapeHtml(shorten(item.evidence || item.desc, 210))}</p>
        <div class="rule-foot"><span>查看完整规则与 OCR 原文</span><b>${escapeHtml(item.id)}</b></div>
      </div>
      <span class="card-arrow" aria-hidden="true">›</span>
    </article>
  `).join("") : `<div class="no-results">没有匹配的规则，可尝试搜索“滑坡”“危险性”或“调查”。</div>`;

  bindInteractiveCards(".rule-card", card => {
    openRule(rules.find(item => item.id === card.dataset.ruleId));
  });

  pagination.innerHTML = visible.length ? `
    <span>显示 ${start + 1}-${Math.min(start + PAGE_SIZE, visible.length)} / ${visible.length} 条</span>
    <div>
      <button class="page-button" data-page="prev" aria-label="上一页" ${state.page === 1 ? "disabled" : ""}>←</button>
      <b>第 ${state.page} / ${pageCount} 页</b>
      <button class="page-button" data-page="next" aria-label="下一页" ${state.page === pageCount ? "disabled" : ""}>→</button>
    </div>
  ` : "";
  pagination.querySelectorAll(".page-button").forEach(button => button.addEventListener("click", () => {
    state.page += button.dataset.page === "next" ? 1 : -1;
    renderRules();
    document.querySelector("#rules").scrollIntoView({ behavior: "smooth" });
  }));
}

function renderFormulas() {
  formulaList.innerHTML = formulas.map((item, index) => `
    <article class="formula-card" data-formula-id="${escapeHtml(item.id)}" tabindex="0" role="button" aria-label="查看完整判据：${escapeHtml(formulaHeading(item))}">
      <div class="formula-head">
        <span class="formula-symbol">ƒ${index + 1}</span>
        <div class="formula-title">
          <strong>${escapeHtml(formulaHeading(item))}</strong>
          <small>${escapeHtml(item.source_location)} · ${escapeHtml(item.id)}</small>
        </div>
        <span class="formula-status">${escapeHtml(item.status)}</span>
      </div>
      <div class="formula-preview">${escapeHtml(shorten(item.evidence || item.formula, 220))}</div>
      <div class="formula-foot"><span>查看完整公式 / 判据</span><b>完整原文 ›</b></div>
    </article>
  `).join("");
  bindInteractiveCards(".formula-card", card => {
    openFormula(formulas.find(item => item.id === card.dataset.formulaId));
  });
}

function renderSources() {
  sourceList.innerHTML = sources.map(source => `
    <article class="source-item" data-source="${escapeHtml(source.name)}" tabindex="0" role="button" aria-label="查看数据源：${escapeHtml(source.name)}">
      <div class="file-icon">${escapeHtml(source.extension)}</div>
      <div>
        <div class="source-title">${escapeHtml(source.name)}</div>
        <div class="source-sub">${source.pages} 页 · ${escapeHtml(source.pipeline)}<b>${source.candidate_count} 条候选</b></div>
      </div>
      <div class="source-status"><strong>${escapeHtml(source.status)}</strong><span>查看来源与处理信息 ›</span></div>
    </article>
  `).join("");
  bindInteractiveCards(".source-item", card => {
    openSource(sources.find(source => source.name === card.dataset.source));
  });
}

function metaBox(label, value) {
  return `<div class="meta-box"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`;
}

function openModal(kicker, title, body) {
  lastFocusedElement = document.activeElement;
  modalKicker.textContent = kicker;
  modalTitle.textContent = title;
  modalBody.innerHTML = body;
  modalBackdrop.classList.add("open");
  modalBackdrop.setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
  requestAnimationFrame(() => modalClose.focus());
}

function openRule(item) {
  if (!item) return;
  const heading = ruleHeading(item);
  openModal(
    `RULE DETAIL / ${item.id}`,
    heading,
    `<p class="detail-summary">以下为该条规则的来源信息与完整 OCR 内容。列表中的短摘要仅用于快速浏览，此处原文不做省略。</p>
    <div class="detail-meta">
      ${metaBox("规则分类", item.label)}
      ${metaBox("来源定位", item.source_location)}
      ${metaBox("审核状态", item.status)}
    </div>
    <div class="detail-section">
      <div class="detail-section-header"><h3>完整规则原文</h3><span class="complete-badge">完整展示 · 未省略</span></div>
      <div class="full-evidence">${evidenceHtml(item.evidence || item.desc)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-section-header"><h3>规范来源</h3></div>
      <div class="full-evidence"><p>${escapeHtml(item.source)}</p><p>${escapeHtml(item.source_file)}</p></div>
    </div>
    <div class="detail-note">自动识别内容可能存在字符、单位或条款切分误差，正式使用前请在对应 PDF 页面对照核验。</div>`
  );
}

function openFormula(item) {
  if (!item) return;
  const heading = formulaHeading(item);
  const formulaText = cleanText(item.formula || "");
  openModal(
    `FORMULA DETAIL / ${item.id}`,
    heading,
    `<p class="detail-summary">该条目可能是计算公式，也可能是表格中的定量分级判据。完整来源内容已在下方展开。</p>
    <div class="detail-meta">
      ${metaBox("内容类型", item.label)}
      ${metaBox("来源定位", item.source_location)}
      ${metaBox("审核状态", item.status)}
    </div>
    <div class="detail-section">
      <div class="detail-section-header"><h3>公式 / 判据摘录</h3></div>
      <div class="formula-detail">${escapeHtml(formulaText)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-section-header"><h3>完整来源内容</h3><span class="complete-badge">完整展示 · 未省略</span></div>
      <div class="full-evidence">${evidenceHtml(item.evidence || item.formula)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-section-header"><h3>规范来源</h3></div>
      <div class="full-evidence"><p>${escapeHtml(item.source)}</p><p>${escapeHtml(item.source_file)}</p></div>
    </div>
    <div class="detail-note">使用前应重点核对上下限符号、单位、变量定义、表格列关系、分级条件和适用灾种。</div>`
  );
}

function openSource(source) {
  if (!source) return;
  openModal(
    "DATA SOURCE / PDF",
    source.name,
    `<p class="detail-summary">${escapeHtml(source.pipeline)}</p>
    <div class="detail-meta">
      ${metaBox("文件页数", `${source.pages} 页`)}
      ${metaBox("OCR 识别", `${source.ocr_pages} 页`)}
      ${metaBox("入库候选", `${source.candidate_count} 条`)}
    </div>
    <div class="detail-section">
      <div class="detail-section-header"><h3>文件路径</h3></div>
      <div class="full-evidence"><p>${escapeHtml(source.file)}</p></div>
    </div>
    <div class="detail-note">${escapeHtml(source.status)}。当前页面的规则与公式只来自这一份国家标准文件。</div>`
  );
}

function closeModal() {
  if (!modalBackdrop.classList.contains("open")) return;
  modalBackdrop.classList.remove("open");
  modalBackdrop.setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
  if (lastFocusedElement && typeof lastFocusedElement.focus === "function") lastFocusedElement.focus();
}

searchInput.addEventListener("input", event => {
  state.query = event.target.value;
  state.page = 1;
  renderRules();
});

document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    searchInput.focus();
  }
  if (event.key === "Escape") closeModal();
});

modalClose.addEventListener("click", closeModal);
modalBackdrop.addEventListener("click", event => {
  if (event.target === modalBackdrop) closeModal();
});

const navLinks = [...document.querySelectorAll(".section-nav a")];
navLinks.forEach(link => link.addEventListener("click", () => {
  navLinks.forEach(item => item.classList.toggle("active", item === link));
}));
const navObserver = new IntersectionObserver(entries => {
  const visibleSection = entries
    .filter(entry => entry.isIntersecting)
    .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
  if (!visibleSection) return;
  navLinks.forEach(link => link.classList.toggle("active", link.hash === `#${visibleSection.target.id}`));
}, { rootMargin: "-18% 0px -68% 0px", threshold: [0, .1, .4] });
document.querySelectorAll("#rules, #formulas, #sources").forEach(section => navObserver.observe(section));

updateSummary();
buildFilters();
renderRules();
renderFormulas();
renderSources();
