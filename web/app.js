const state = {
  graph: null,
  status: null,
  ocrTasks: [],
  selectedNodeId: null,
  activeHazardId: null,
  mode: "all",
  search: "",
  presentationMode: true,
  enabledTypes: new Set(),
  view: {
    scale: 1,
    x: 0,
    y: 0,
    minScale: 0.25,
    maxScale: 6,
    initialized: false,
    nodePositions: new Map(),
    contentBounds: null,
    lastPointer: null,
    isPanning: false,
    lastX: 0,
    lastY: 0,
    startX: 0,
    startY: 0,
    pressNodeId: null,
    dragged: false,
  },
};

const typeLabels = {
  Project: "项目",
  RouteSegment: "路线段",
  Slope: "边坡",
  HazardBody: "灾害体/隐患体",
  HazardType: "灾害类型",
  HazardSusceptibilityAssessment: "易感性评价",
  Lithology: "岩性",
  Stratum: "地层",
  CausalFactor: "影响因素",
  ProtectionType: "防护类型",
  ProtectionWork: "防护工程",
  Drawing: "图纸",
  Document: "文档",
  StabilityAnalysis: "稳定性分析",
  StructuralPlane: "结构面",
  DeformationObservation: "历史变形记录",
  HydrologyObservation: "历史水文记录",
  TerrainSetting: "地形地貌",
  VegetationSurvey: "植被调查",
};

const compactHiddenTypes = new Set([
  "DeformationObservation",
  "HydrologyObservation",
  "StructuralPlane",
  "StabilityAnalysis",
  "ProtectionType",
  "HazardSusceptibilityAssessment",
  "CausalFactor",
  "TerrainSetting",
  "VegetationSurvey",
  "Drawing",
]);

const nodeColors = {
  Project: "#0f7b6c",
  RouteSegment: "#0f7b6c",
  Slope: "#cc6b2c",
  HazardBody: "#d98b46",
  HazardSusceptibilityAssessment: "#b66a32",
  Drawing: "#4657a8",
  ProtectionType: "#8b6aa8",
  ProtectionWork: "#7b4ea3",
  Document: "#52606d",
  StabilityAnalysis: "#b53b4a",
  StructuralPlane: "#99702a",
  DeformationObservation: "#a07162",
  HydrologyObservation: "#4b82a8",
  TerrainSetting: "#6f8d61",
  VegetationSurvey: "#5d9366",
  Lithology: "#23836d",
  Stratum: "#23836d",
  CausalFactor: "#777f88",
  HazardType: "#cc6b2c",
  StakeRange: "#777f88",
};

const $ = (selector) => document.querySelector(selector);
let graphVersion = null;
let refreshInFlight = false;

async function main() {
  state.status = await fetchStatus();
  state.graph = await fetchGraph();
  state.ocrTasks = await fetchParsedRows("ocr_tasks");
  const allTypes = [...new Set(state.graph.nodes.map((n) => n.type))];
  state.enabledTypes = new Set(allTypes);
  bindEvents();
  render();
  graphVersion = statusVersion(state.status);
  setInterval(refreshPublishedGraph, 4000);
}

async function fetchStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (response.ok) return response.json();
  } catch (error) {
    console.warn("status api unavailable", error);
  }
  return null;
}

async function fetchGraph() {
  const api = await fetch("/api/graph", { cache: "no-store" }).catch(() => null);
  if (api?.ok) return api.json();
  const response = await fetch("/output/demo/web/data/demo_graph.json", { cache: "no-store" });
  if (!response.ok) throw new Error(`无法加载图谱数据: ${response.status}`);
  return response.json();
}

function statusVersion(status) {
  return `${status?.updated_at || ""}|${status?.publication_stage || ""}|${status?.graph?.nodes || ""}|${status?.graph?.edges || ""}`;
}

async function refreshPublishedGraph() {
  if (refreshInFlight || document.hidden) return;
  refreshInFlight = true;
  try {
    const nextStatus = await fetchStatus();
    const nextVersion = statusVersion(nextStatus);
    if (nextStatus && graphVersion && nextVersion !== graphVersion) {
      state.status = nextStatus;
      state.graph = await fetchGraph();
      state.ocrTasks = await fetchParsedRows("ocr_tasks");
      state.enabledTypes = new Set(state.graph.nodes.map((node) => node.type));
      if (!state.graph.nodes.some((node) => node.id === state.selectedNodeId)) state.selectedNodeId = null;
      state.view.initialized = false;
      graphVersion = nextVersion;
      render();
    } else if (nextStatus) {
      state.status = nextStatus;
      graphVersion = nextVersion;
    }
  } finally {
    refreshInFlight = false;
  }
}

async function fetchParsedRows(name) {
  try {
    const response = await fetch(`/api/parsed/${name}`);
    if (!response.ok) return [];
    const payload = await response.json();
    return payload.rows || [];
  } catch {
    return [];
  }
}

function bindEvents() {
  $("#searchInput").addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    state.view.initialized = false;
    render();
  });

  $("#presentationToggle").addEventListener("change", (event) => {
    state.presentationMode = event.target.checked;
    state.view.initialized = false;
    render();
  });

  document.querySelectorAll(".segment").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".segment").forEach((b) => b.classList.remove("active"));
      button.classList.add("active");
      state.mode = button.dataset.mode;
      state.activeHazardId = null;
      state.selectedNodeId = null;
      state.view.initialized = false;
      render();
    });
  });

  $("#resetBtn").addEventListener("click", () => {
    state.search = "";
    state.mode = "all";
    state.presentationMode = true;
    state.activeHazardId = null;
    state.selectedNodeId = null;
    $("#searchInput").value = "";
    $("#presentationToggle").checked = true;
    document.querySelectorAll(".segment").forEach((b) => b.classList.toggle("active", b.dataset.mode === "all"));
    state.enabledTypes = new Set(state.graph.nodes.map((n) => n.type));
    state.view.initialized = false;
    render();
  });

  const runPipelineButton = $("#runPipelineBtn");
  if (runPipelineButton) runPipelineButton.addEventListener("click", runPipeline);
  window.slopeKgZoomFit = () => fitGraphView();
  window.slopeKgCenterSelected = () => centerSelectedNode();
  bindGraphNavigation();
  bindViewportShortcuts();
}

async function runPipeline() {
  const button = $("#runPipelineBtn");
  const msg = $("#pipelineMessage");
  const ocrPages = Number($("#ocrPages").value || 0);
  button.disabled = true;
  msg.textContent = "正在解析 PDF、渲染 OCR 页并生成图谱...";
  try {
    const response = await fetch("/api/pipeline/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ocr_pages: ocrPages, parse_mode: "deep", llm_model: "deepseek-v4-flash" }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.status = payload;
    state.graph = await fetchGraph();
    state.ocrTasks = await fetchParsedRows("ocr_tasks");
    const allTypes = [...new Set(state.graph.nodes.map((n) => n.type))];
    state.enabledTypes = new Set(allTypes);
    msg.textContent = `完成：${payload.parsed.pages} 页元信息，${payload.parsed.text_blocks} 个文本块，${payload.parsed.ocr_tasks} 个 OCR 任务。`;
    render();
  } catch (error) {
    msg.textContent = `运行失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function render() {
  renderMeta();
  renderPipeline();
  renderMetrics();
  renderTypeFilters();
  renderHazardList();
  renderOcrList();
  renderGraph();
  renderDetail();
}

function renderMeta() {
  const meta = state.graph.meta;
  $("#metaSummary").textContent = `${meta.stats.nodes} 个节点，${meta.stats.edges} 条关系，${meta.stats.evidence} 条证据。`;
  const routeSummary = $("#routeSummary");
  if (routeSummary) {
    const routes = state.graph.nodes.filter((node) => node.type === "RouteSegment").map((node) => node.label);
    routeSummary.textContent = routes.join(" · ") || "路线范围待识别";
  }
}

function renderPipeline() {
  const status = state.status || state.graph.meta.pipeline || {};
  const parsed = status.parsed || state.graph.meta.pipeline || {};
  const deps = status.dependencies || state.graph.meta.dependencies || {};
  const llm = status.llm || state.graph.meta.llm || {};
  $("#pipelineStats").innerHTML = [
    pipelineStat(parsed.documents ?? "-", "文档"),
    pipelineStat(parsed.pages ?? "-", "页"),
    pipelineStat(parsed.text_blocks ?? "-", "文本块"),
    pipelineStat(parsed.ocr_tasks ?? "-", "OCR任务"),
  ].join("");
  const ocrText = deps.paddleocr ? "PaddleOCR 已可用" : "PaddleOCR 未安装，OCR任务已排队";
  const llmText = llm.enabled ? `DeepSeek 已参与语义抽取（通过 ${llm.run?.passed ?? 0}/${llm.run?.candidates ?? 0}）` : (llm.key_available ? "DeepSeek 已配置，本次未调用" : "DeepSeek 未配置，已使用确定性降级流程");
  const statusCounts = parsed.ocr_status_counts ? formatStatusCounts(parsed.ocr_status_counts) : "";
  $("#pipelineMessage").textContent = `${ocrText}${statusCounts ? `（${statusCounts}）` : ""}；${llmText}。${status.updated_at ? `上次运行：${status.updated_at}` : ""}`;
}

function pipelineStat(value, label) {
  return `<div class="pipeline-stat"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`;
}

function formatStatusCounts(counts) {
  return Object.entries(counts)
    .filter(([, count]) => Number(count) > 0)
    .map(([name, count]) => `${name}:${count}`)
    .join(" / ");
}

function renderMetrics() {
  const stats = state.graph.meta.stats;
  const hazards = state.graph.nodes.filter((n) => n.type === "Slope").length;
  const visible = visibleGraph();
  $("#metrics").innerHTML = [
    metric(stats.nodes, "节点"),
    metric(stats.edges, "关系"),
    metric(hazards, "候选边坡"),
    metric(`${visible.nodes.length}/${stats.nodes}`, "显示/全部节点"),
  ].join("");
  const hidden = Math.max(0, stats.nodes - visible.nodes.length);
  const hint = $("#presentationHint");
  if (hint) {
    hint.textContent = state.presentationMode && !state.search
      ? `默认折叠 ${hidden} 个次要节点；取消勾选即可显示全部。`
      : "次要节点已显示，可通过节点类型继续筛选。";
  }
}

function metric(value, label) {
  return `<div class="metric"><div class="metric-value">${value}</div><div class="metric-label">${label}</div></div>`;
}

function renderTypeFilters() {
  const counts = state.graph.meta.stats.node_type_counts;
  const entries = Object.entries(counts).sort((a, b) => a[0].localeCompare(b[0]));
  $("#typeFilters").innerHTML = entries
    .map(([type, count]) => {
      const checked = state.enabledTypes.has(type) ? "checked" : "";
      return `
        <div class="check-item">
          <label>
            <input type="checkbox" data-type="${escapeHtml(type)}" ${checked} />
            <span>${typeLabels[type] || type}</span>
          </label>
          <span class="count">${count}</span>
        </div>
      `;
    })
    .join("");

  $("#typeFilters").querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.enabledTypes.add(input.dataset.type);
      else state.enabledTypes.delete(input.dataset.type);
      state.view.initialized = false;
      renderMetrics();
      renderGraph();
      renderDetail();
    });
  });
}

function renderHazardList() {
  const hazards = state.graph.nodes
    .filter((n) => n.type === "Slope")
    .filter((n) => matchesSearch(n))
    .sort((a, b) => (a.props.no || 0) - (b.props.no || 0));

  $("#hazardList").innerHTML = hazards
    .map((node) => {
      const active = node.id === state.activeHazardId || node.id === state.selectedNodeId ? "active" : "";
      return `
        <div class="hazard-item ${active}" data-node-id="${node.id}">
          <div class="hazard-title">${escapeHtml(node.label)}</div>
          <div class="hazard-meta">
            <span class="tag">${escapeHtml(node.props.side || "")}</span>
            <span class="tag">${escapeHtml(node.props.slope_length_m || "-")}m</span>
            <span class="tag">${escapeHtml(node.props.slope_gradient_raw || "坡度缺失")}</span>
            <span class="tag">${escapeHtml(node.props.review_status === "approved" ? "已审核" : "待审核")}</span>
          </div>
        </div>
      `;
    })
    .join("");

  $("#hazardList").querySelectorAll(".hazard-item").forEach((item) => {
    item.addEventListener("click", () => {
      state.activeHazardId = item.dataset.nodeId;
      state.selectedNodeId = item.dataset.nodeId;
      state.mode = "hazard";
      state.view.initialized = false;
      document.querySelectorAll(".segment").forEach((b) => b.classList.toggle("active", b.dataset.mode === "hazard"));
      render();
    });
  });
}

function renderOcrList() {
  const tasks = state.ocrTasks.slice(0, 8);
  const list = $("#ocrList");
  if (!tasks.length) {
    list.innerHTML = `<div class="muted">暂无 OCR 任务。运行流水线后生成。</div>`;
    return;
  }
  list.innerHTML = tasks
    .map((task) => {
      const image = task.image_path ? `<a href="/${encodeURI(task.image_path.replaceAll("\\", "/"))}" target="_blank" rel="noreferrer">查看截图</a>` : "";
      return `
        <div class="ocr-item">
          <div>${escapeHtml(task.file_name)}</div>
          <div class="muted">第 ${escapeHtml(task.page)} 页 ${image}</div>
          ${task.error ? `<div class="muted">${escapeHtml(task.error).slice(0, 96)}</div>` : ""}
          <span class="ocr-status ${escapeHtml(task.status)}">${escapeHtml(task.status)}</span>
        </div>
      `;
    })
    .join("");
}

function visibleGraph() {
  const nodesById = new Map(state.graph.nodes.map((n) => [n.id, n]));
  let candidateIds = new Set(
    state.graph.nodes
      .filter((n) => state.enabledTypes.has(n.type) && matchesSearch(n) && passesPresentationFilter(n))
      .map((n) => n.id)
  );

  if (state.mode === "hazard" && state.activeHazardId) {
    const neighbors = connectedIds(state.activeHazardId);
    candidateIds = new Set(
      [state.activeHazardId, ...neighbors].filter((id) => {
        const node = nodesById.get(id);
        return node && state.enabledTypes.has(node.type) && passesPresentationFilter(node, { forceKeepSelected: true });
      })
    );
  } else if (state.mode === "measure") {
    const measureIds = new Set(state.graph.nodes.filter((n) => n.type === "ProtectionWork" && matchesSearch(n)).map((n) => n.id));
    const linkedHazards = new Set(
      state.graph.edges
        .filter((e) => e.relation === "HAS_PROTECTION_DESIGN" && measureIds.has(e.target))
        .map((e) => e.source)
    );
    const contextIds = state.graph.nodes
      .filter((node) => ["Project", "RouteSegment"].includes(node.type))
      .map((node) => node.id);
    candidateIds = new Set([...measureIds, ...linkedHazards, ...contextIds]);
  } else if (state.selectedNodeId && !candidateIds.has(state.selectedNodeId)) {
    candidateIds.add(state.selectedNodeId);
  }

  const nodes = [...candidateIds].map((id) => nodesById.get(id)).filter(Boolean);
  const nodeSet = new Set(nodes.map((n) => n.id));
  const edges = state.graph.edges.filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target));
  return { nodes, edges };
}

function passesPresentationFilter(node, options = {}) {
  if (!state.presentationMode) return true;
  if (state.search) return true;
  if (options.forceKeepSelected && node.id === state.selectedNodeId) return true;
  if (node.id === state.activeHazardId) return true;
  return !compactHiddenTypes.has(node.type);
}

function connectedIds(nodeId) {
  const result = new Set();
  for (const edge of state.graph.edges) {
    if (edge.source === nodeId) result.add(edge.target);
    if (edge.target === nodeId) result.add(edge.source);
  }
  return result;
}

function matchesSearch(node) {
  if (!state.search) return true;
  const payload = `${node.label} ${node.type} ${JSON.stringify(node.props)} ${node.summary || ""}`.toLowerCase();
  return payload.includes(state.search);
}

function renderGraph() {
  const svg = $("#graphSvg");
  const { width, height } = svg.getBoundingClientRect();
  const graph = visibleGraph();
  $("#emptyState").hidden = graph.nodes.length > 0;
  $("#viewTitle").textContent = state.activeHazardId
    ? state.graph.nodes.find((n) => n.id === state.activeHazardId)?.label || "边坡子图"
    : state.mode === "measure"
      ? "防护工程关联图"
      : state.presentationMode && !state.search
        ? "工程核心关系图"
        : "全量图谱";

  const layout = layoutNodes(graph.nodes, width || 900, height || 640);
  state.view.nodePositions = layout;
  state.view.contentBounds = graphBounds(graph.nodes, layout);
  const activeIds = state.selectedNodeId ? new Set([state.selectedNodeId, ...connectedIds(state.selectedNodeId)]) : null;

  const edgeMarkup = graph.edges
    .map((edge) => {
      const a = layout.get(edge.source);
      const b = layout.get(edge.target);
      if (!a || !b) return "";
      const active = activeIds && activeIds.has(edge.source) && activeIds.has(edge.target);
      const faded = activeIds && !active;
      return `<line class="edge ${active ? "active" : ""} ${faded ? "faded" : ""}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"><title>${escapeHtml(edge.relation)}</title></line>`;
    })
    .join("");

  const nodeMarkup = graph.nodes
    .map((node) => {
      const p = layout.get(node.id);
      const radius = radiusFor(node);
      const active = node.id === state.selectedNodeId;
      const faded = activeIds && !activeIds.has(node.id);
      const label = displayNodeLabel(node);
      const labelMode = labelModeFor(node, graph.nodes.length, active);
      const muted = isPeripheralNode(node) && !active ? "peripheral" : "";
      const hitRadius = Math.max(radius + 10, 18);
      return `
        <g class="node ${muted} ${active ? "active" : ""} ${faded ? "faded" : ""}" data-node-id="${node.id}" transform="translate(${p.x}, ${p.y})">
          <circle class="node-hit" r="${hitRadius}"></circle>
          <circle r="${radius}" fill="${nodeColors[node.type] || "#79828c"}"></circle>
          ${labelMode === "mini" ? `<text class="node-mini-label" text-anchor="middle" y="${radius + 12}">${escapeHtml(miniLabel(node))}</text>` : ""}
          ${labelMode === "full" ? `<text class="node-label" text-anchor="middle" y="${radius + 14}">${escapeHtml(label)}</text>` : ""}
          <title>${escapeHtml(node.label)}&#10;${escapeHtml(typeLabels[node.type] || node.type)}</title>
        </g>
      `;
    })
    .join("");

  svg.innerHTML = `<g id="graphViewport"><g>${edgeMarkup}</g><g>${nodeMarkup}</g></g>`;
  if (!state.view.initialized) fitGraphView({ silent: true });
  else applyGraphTransform();
  svg.querySelectorAll(".node").forEach((nodeEl) => {
    nodeEl.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.view.dragged) return;
      selectNode(nodeEl.dataset.nodeId);
    });
  });
}

function bindGraphNavigation() {
  const svg = $("#graphSvg");
  const wrap = document.querySelector(".canvas-wrap");
  if (!svg || !wrap) return;

  wrap.addEventListener("mousemove", (event) => {
    state.view.lastPointer = clientToLocal(event.clientX, event.clientY);
  });
  wrap.addEventListener("mouseleave", () => {
    state.view.lastPointer = null;
  });
  wrap.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      state.view.lastPointer = clientToLocal(event.clientX, event.clientY);
      const factor = event.deltaY < 0 ? 1.16 : 1 / 1.16;
      zoomAt(event.clientX, event.clientY, factor);
    },
    { passive: false }
  );

  wrap.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    const nodeEl = event.target.closest?.(".node");
    state.view.lastPointer = clientToLocal(event.clientX, event.clientY);
    state.view.isPanning = true;
    state.view.dragged = false;
    state.view.pressNodeId = nodeEl?.dataset?.nodeId || null;
    state.view.lastX = event.clientX;
    state.view.lastY = event.clientY;
    state.view.startX = event.clientX;
    state.view.startY = event.clientY;
    wrap.classList.add("panning");
  });

  window.addEventListener("mousemove", (event) => {
    if (!state.view.isPanning) return;
    const dx = event.clientX - state.view.lastX;
    const dy = event.clientY - state.view.lastY;
    const moved = Math.hypot(event.clientX - state.view.startX, event.clientY - state.view.startY);
    state.view.lastPointer = clientToLocal(event.clientX, event.clientY);
    if (moved <= 4) return;
    event.preventDefault();
    state.view.dragged = true;
    state.view.x += dx;
    state.view.y += dy;
    state.view.lastX = event.clientX;
    state.view.lastY = event.clientY;
    constrainView();
    applyGraphTransform();
  });

  window.addEventListener("mouseup", endPan);
  window.addEventListener("blur", endPan);
}

function endPan() {
  const wrap = document.querySelector(".canvas-wrap");
  if (!state.view.isPanning) return;
  const shouldSelect = state.view.pressNodeId && !state.view.dragged;
  const nodeId = state.view.pressNodeId;
  state.view.isPanning = false;
  state.view.pressNodeId = null;
  wrap?.classList.remove("panning");
  if (shouldSelect) {
    selectNode(nodeId);
    return;
  }
  window.setTimeout(() => {
    state.view.dragged = false;
  }, 0);
}

function selectNode(nodeId) {
  if (!nodeId) return;
  state.selectedNodeId = nodeId;
  const node = state.graph.nodes.find((item) => item.id === nodeId);
  state.activeHazardId = node?.type === "Slope" ? nodeId : null;
  state.view.dragged = false;
  render();
}

function zoomAt(clientX, clientY, factor) {
  zoomAtPoint(clientToLocal(clientX, clientY), factor);
}

function zoomAtPoint(point, factor) {
  if (!point) return;
  const svg = $("#graphSvg");
  if (!svg) return;
  const nextScale = clamp(state.view.scale * factor, state.view.minScale, state.view.maxScale);
  const graphX = (point.x - state.view.x) / state.view.scale;
  const graphY = (point.y - state.view.y) / state.view.scale;
  state.view.scale = nextScale;
  state.view.x = point.x - graphX * nextScale;
  state.view.y = point.y - graphY * nextScale;
  constrainView();
  applyGraphTransform();
}

function fitGraphView(options = {}) {
  const svg = $("#graphSvg");
  const bounds = state.view.contentBounds;
  if (!svg || !bounds) return;
  const box = svg.getBoundingClientRect();
  const padding = Math.min(72, Math.max(38, Math.min(box.width, box.height) * 0.08));
  const graphWidth = Math.max(bounds.maxX - bounds.minX, 1);
  const graphHeight = Math.max(bounds.maxY - bounds.minY, 1);
  const nextScale = clamp(
    Math.min((box.width - padding * 2) / graphWidth, (box.height - padding * 2) / graphHeight),
    state.view.minScale,
    state.view.maxScale
  );
  state.view.scale = nextScale;
  state.view.x = padding + (box.width - padding * 2 - graphWidth * nextScale) / 2 - bounds.minX * nextScale;
  state.view.y = padding + (box.height - padding * 2 - graphHeight * nextScale) / 2 - bounds.minY * nextScale;
  state.view.initialized = true;
  applyGraphTransform();
  if (!options.silent) flashCanvas();
}

function centerSelectedNode() {
  if (!state.selectedNodeId) {
    fitGraphView();
    return;
  }
  centerOnNode(state.selectedNodeId, Math.max(state.view.scale, 1.7));
}

function centerOnNode(nodeId, scale = state.view.scale) {
  const point = state.view.nodePositions.get(nodeId);
  const svg = $("#graphSvg");
  if (!point || !svg) return;
  const box = svg.getBoundingClientRect();
  state.view.scale = clamp(scale, state.view.minScale, state.view.maxScale);
  state.view.x = box.width / 2 - point.x * state.view.scale;
  state.view.y = box.height / 2 - point.y * state.view.scale;
  constrainView();
  applyGraphTransform();
  flashCanvas();
}

function applyGraphTransform() {
  const viewport = $("#graphViewport");
  updateZoomLabel();
  if (!viewport) return;
  viewport.setAttribute("transform", `translate(${state.view.x}, ${state.view.y}) scale(${state.view.scale})`);
}

function updateZoomLabel() {
  const label = $("#zoomValue");
  if (label) label.textContent = `${Math.round(state.view.scale * 100)}%`;
}

function clientToLocal(clientX, clientY) {
  const svg = $("#graphSvg");
  if (!svg) return null;
  const box = svg.getBoundingClientRect();
  return { x: clientX - box.left, y: clientY - box.top };
}

function worldToLocal(point) {
  return {
    x: point.x * state.view.scale + state.view.x,
    y: point.y * state.view.scale + state.view.y,
  };
}

function graphBounds(nodes, layout) {
  if (!nodes.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    const p = layout.get(node.id);
    if (!p) continue;
    const r = radiusFor(node) + 44;
    minX = Math.min(minX, p.x - r);
    minY = Math.min(minY, p.y - r);
    maxX = Math.max(maxX, p.x + r);
    maxY = Math.max(maxY, p.y + r);
  }
  return { minX, minY, maxX, maxY };
}

function constrainView() {
  const svg = $("#graphSvg");
  const bounds = state.view.contentBounds;
  if (!svg || !bounds) return;
  const box = svg.getBoundingClientRect();
  const margin = 120;
  const left = bounds.minX * state.view.scale + state.view.x;
  const right = bounds.maxX * state.view.scale + state.view.x;
  const top = bounds.minY * state.view.scale + state.view.y;
  const bottom = bounds.maxY * state.view.scale + state.view.y;
  if (right < margin) state.view.x += margin - right;
  if (left > box.width - margin) state.view.x -= left - (box.width - margin);
  if (bottom < margin) state.view.y += margin - bottom;
  if (top > box.height - margin) state.view.y -= top - (box.height - margin);
}

function flashCanvas() {
  const wrap = document.querySelector(".canvas-wrap");
  if (!wrap) return;
  wrap.classList.remove("viewport-flash");
  void wrap.offsetWidth;
  wrap.classList.add("viewport-flash");
}

function bindViewportShortcuts() {
  window.addEventListener("keydown", (event) => {
    const target = event.target;
    if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
    if (event.key === "0" || event.key.toLowerCase() === "f") {
      event.preventDefault();
      fitGraphView();
    } else if (event.key.toLowerCase() === "c") {
      event.preventDefault();
      centerSelectedNode();
    }
  });
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function layoutNodes(nodes, width, height) {
  const map = new Map();
  const cx = width / 2;
  const cy = height / 2;
  const byType = groupBy(nodes, (n) => n.type);

  const centerIds = nodes.filter((node) => ["Project", "RouteSegment"].includes(node.type)).map((node) => node.id);
  centerIds.forEach((id, idx) => map.set(id, { x: cx, y: cy - 40 + idx * 80 }));

  const hazards = byType.Slope || [];
  placeRing(map, hazards, cx, cy, Math.min(width, height) * 0.28, -Math.PI / 2);

  const sectors = [
    ["Drawing", -0.05, 0.2],
    ["ProtectionWork", 0.22, 0.38],
    ["HazardBody", 0.12, 0.2],
    ["Lithology", 0.42, 0.52],
    ["Stratum", 0.54, 0.62],
    ["StabilityAnalysis", 0.66, 0.76],
    ["StructuralPlane", 0.78, 0.9],
    ["Document", 0.91, 0.98],
    ["CausalFactor", 0.02, 0.12],
    ["HazardType", 0.12, 0.2],
    ["HazardSusceptibilityAssessment", 0.28, 0.42],
  ];
  for (const [type, start, end] of sectors) {
    const items = (byType[type] || []).filter((n) => !map.has(n.id));
    placeArc(map, items, cx, cy, Math.min(width, height) * 0.43, start * Math.PI * 2, end * Math.PI * 2);
  }

  const rest = nodes.filter((n) => !map.has(n.id));
  placeRing(map, rest, cx, cy, Math.min(width, height) * 0.46, Math.PI / 3);
  return map;
}

function placeRing(map, nodes, cx, cy, radius, offset) {
  const count = Math.max(nodes.length, 1);
  nodes.forEach((node, index) => {
    const angle = offset + (index / count) * Math.PI * 2;
    map.set(node.id, { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius });
  });
}

function placeArc(map, nodes, cx, cy, radius, start, end) {
  const count = Math.max(nodes.length - 1, 1);
  nodes.forEach((node, index) => {
    const angle = start + ((end - start) * index) / count;
    map.set(node.id, { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius });
  });
}

function radiusFor(node) {
  if (node.type === "Project") return 22;
  if (node.type === "Slope") return 14;
  if (node.type === "Drawing") return 6;
  if (node.type === "StabilityAnalysis" || node.type === "StructuralPlane" || node.type === "StakeRange") return 7;
  if (node.type === "ProtectionWork") return 11;
  return 8;
}

function isPeripheralNode(node) {
  return ["Drawing", "StabilityAnalysis", "StructuralPlane", "StakeRange"].includes(node.type);
}

function labelModeFor(node, totalNodes, active) {
  if (active) return "full";
  if (totalNodes <= 45) return "full";
  const zoom = state.view.scale;
  if (zoom >= 1.9) return "full";
  if (zoom >= 1.45) {
    return ["Drawing"].includes(node.type) ? "mini" : "full";
  }
  if (zoom >= 1.15) {
    return ["Project", "RouteSegment", "Slope", "ProtectionWork", "HazardBody", "Document", "Lithology", "Stratum", "CausalFactor"].includes(node.type) ? "full" : "mini";
  }
  return ["Project", "RouteSegment", "Slope", "ProtectionWork", "HazardBody", "Document", "Lithology", "Stratum"].includes(node.type) ? "full" : "mini";
}

function displayNodeLabel(node) {
  if (node.type === "Slope") return `${node.props.slope_id || "边坡"} ${node.props.start_station_raw || ""}`;
  if (node.type === "Drawing") return node.props.drawing_no || node.label;
  if (node.type === "StakeRange") return compactStake(node.label);
  if (node.type === "StabilityAnalysis") {
    const condition = node.props?.condition || "稳定性";
    const fs = node.props?.fs ? ` Fs=${node.props.fs}` : "";
    return `${condition}${fs}`;
  }
  if (node.type === "StructuralPlane") {
    const name = node.props?.name || "结构面";
    const dipDirection = node.props?.dip_direction;
    const dipAngle = node.props?.dip_angle;
    if (dipDirection && dipAngle) return `${name} ${dipDirection}°∠${dipAngle}°`;
    return name;
  }
  if (node.label.length > 13) return `${node.label.slice(0, 12)}…`;
  return node.label;
}

function shortLabel(node) {
  return displayNodeLabel(node);
}

function miniLabel(node) {
  if (node.type === "Drawing") return node.props?.drawing_no || "图纸";
  if (node.type === "StabilityAnalysis") return node.props?.condition || "稳定性";
  if (node.type === "StructuralPlane") return node.props?.name || "结构面";
  if (node.type === "StakeRange") return compactStake(node.label);
  if (node.type === "Slope") return node.props?.slope_id || "边坡";
  return truncateLabel(shortLabel(node), 6);
}

function compactStake(label) {
  const text = String(label || "");
  const match = text.match(/K\d+\+\d+/);
  return match ? match[0] : truncateLabel(text, 7);
}

function truncateLabel(label, max) {
  const text = String(label || "");
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function renderDetail() {
  const node = state.graph.nodes.find((n) => n.id === state.selectedNodeId);
  const detail = $("#detail");
  if (!node) {
    detail.innerHTML = `<p>选择一个节点查看属性、关系和证据。</p>`;
    return;
  }

  const relatedEdges = state.graph.edges.filter((e) => e.source === node.id || e.target === node.id);
  const evidence = evidenceForEdges(relatedEdges);

  detail.innerHTML = `
    <div class="detail-title">${escapeHtml(node.label)}</div>
    <div class="detail-type">${escapeHtml(typeLabels[node.type] || node.type)}</div>
    ${node.summary ? `<p>${escapeHtml(node.summary)}</p>` : ""}
    ${propsTable(node.props)}
    <div class="section-title">关系</div>
    <div class="relation-list">
      ${relatedEdges.slice(0, 20).map((edge) => relationItem(edge, node.id)).join("") || `<div class="muted">暂无关系</div>`}
    </div>
    <div class="section-title" style="margin-top:18px">证据</div>
    <div class="evidence-list">
      ${evidence.map(evidenceItem).join("") || `<div class="muted">暂无证据</div>`}
    </div>
  `;

  detail.querySelectorAll("[data-jump-node]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedNodeId = button.dataset.jumpNode;
      if (state.graph.nodes.find((n) => n.id === state.selectedNodeId)?.type === "Slope") {
        state.activeHazardId = state.selectedNodeId;
      }
      render();
    });
  });
}

function propsTable(props) {
  const entries = Object.entries(props || {}).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (!entries.length) return "";
  return `
    <table class="prop-table">
      ${entries
        .map(([key, value]) => `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(formatPropValue(value))}</td></tr>`)
        .join("")}
    </table>
  `;
}

function relationItem(edge, currentId) {
  const otherId = edge.source === currentId ? edge.target : edge.source;
  const other = state.graph.nodes.find((n) => n.id === otherId);
  const arrow = edge.source === currentId ? "->" : "<-";
  return `
    <div class="relation-item">
      <div><strong>${escapeHtml(edge.relation)}</strong> <span class="muted">${arrow}</span></div>
      <button type="button" data-jump-node="${escapeHtml(otherId)}">${escapeHtml(other?.label || otherId)}</button>
    </div>
  `;
}

function evidenceForEdges(edges) {
  const ids = new Set(edges.map((e) => e.props?.evidence).filter(Boolean));
  if (!ids.size && state.selectedNodeId?.startsWith("slope_")) {
    ids.add("ev_survey_7");
    ids.add("ev_design_5");
  }
  return state.graph.evidence.filter((item) => ids.has(item.id));
}

function formatPropValue(value) {
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "object" && value !== null) return JSON.stringify(value, null, 0);
  return String(value);
}

function evidenceItem(item) {
  const fileUrl = `/data/rawPDF/${encodeURIComponent(item.source_file)}`;
  return `
    <div class="evidence-item">
      <div><a href="${fileUrl}" target="_blank" rel="noreferrer">${escapeHtml(item.source_file)}</a></div>
      <div class="muted">第 ${escapeHtml(String(item.page))} 页 · ${escapeHtml(item.kind)}</div>
      <div>${escapeHtml(item.text)}</div>
    </div>
  `;
}

function groupBy(items, fn) {
  return items.reduce((acc, item) => {
    const key = fn(item);
    if (!acc[key]) acc[key] = [];
    acc[key].push(item);
    return acc;
  }, {});
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

main().catch((error) => {
  console.error(error);
  $("#metaSummary").textContent = error.message;
  $("#emptyState").hidden = false;
  $("#emptyState").textContent = error.message;
});
