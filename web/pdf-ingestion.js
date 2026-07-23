(function () {
  const { getJson, escapeHtml } = window.SlopeKGApi;
  const elements = {
    input: document.getElementById("pdfInput"),
    drop: document.getElementById("dropZone"),
    selected: document.getElementById("selectedFiles"),
    uploadOnly: document.getElementById("uploadOnlyBtn"),
    uploadRun: document.getElementById("uploadRunBtn"),
    rerun: document.getElementById("rerunBtn"),
    reextract: document.getElementById("reextractBtn"),
    deepRun: document.getElementById("deepRunBtn"),
    refresh: document.getElementById("refreshBtn"),
    metrics: document.getElementById("ingestMetrics"),
    rows: document.getElementById("documentRows"),
    summary: document.getElementById("documentSummary"),
    status: document.getElementById("jobStatus"),
    percent: document.getElementById("jobPercent"),
    stage: document.getElementById("jobStage"),
    bar: document.getElementById("jobProgressBar"),
    track: document.querySelector(".job-progress-track"),
    message: document.getElementById("jobMessage"),
    details: document.getElementById("jobDetails"),
    error: document.getElementById("jobError"),
    ocrPages: document.getElementById("ocrPages"),
    forceOcr: document.getElementById("forceOcr"),
  };

  let selectedFiles = [];
  let activeJobId = null;
  let pollTimer = null;
  let jobIsBusy = false;

  const stageLabels = {
    queued: "等待执行", preparing: "准备输入", parsing: "逐页解析", ocr: "按需OCR",
    extracting: "属性抽取", semantic: "语义识别", graph: "图谱构建", writing: "写入结果",
    completed: "已完成", failed: "处理失败", upload: "上传文件",
  };
  const statusLabels = { none: "暂无任务", queued: "排队中", running: "处理中", completed: "已完成", failed: "失败" };

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 ** 2).toFixed(2)} MB`;
  }

  function setFiles(files) {
    const seen = new Set();
    selectedFiles = Array.from(files).filter((file) => {
      const key = `${file.name}:${file.size}:${file.lastModified}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    renderSelectedFiles();
  }

  function renderSelectedFiles() {
    const valid = selectedFiles.length > 0 && selectedFiles.every((file) => file.name.toLowerCase().endsWith(".pdf") && file.size > 0 && file.size <= 256 * 1024 ** 2);
    elements.uploadOnly.disabled = !valid || jobIsBusy;
    elements.uploadRun.disabled = !valid || jobIsBusy;
    if (!selectedFiles.length) {
      elements.selected.innerHTML = '<div class="detail-empty">尚未选择文件。</div>';
      return;
    }
    elements.selected.innerHTML = selectedFiles.map((file) => {
      const ok = file.name.toLowerCase().endsWith(".pdf") && file.size > 0 && file.size <= 256 * 1024 ** 2;
      return `<div class="selected-file"><div><strong>${escapeHtml(file.name)}</strong><small>${formatBytes(file.size)}</small></div><span class="badge ${ok ? "available" : "missing"}">${ok ? "可上传" : "类型或大小不合规"}</span></div>`;
    }).join("");
  }

  function setProgress(percent, stage, message, status = "running") {
    const value = Math.max(0, Math.min(100, Number(percent || 0)));
    elements.percent.textContent = `${Math.round(value)}%`;
    elements.bar.style.width = `${value}%`;
    elements.track.setAttribute("aria-valuenow", String(Math.round(value)));
    elements.stage.textContent = stageLabels[stage] || stage || "等待开始";
    elements.message.textContent = message || "正在处理";
    elements.status.textContent = statusLabels[status] || status;
    elements.status.className = `badge ${status === "completed" ? "available" : status === "failed" ? "missing" : "pending"}`;
  }

  function renderJob(job) {
    if (!job || job.status === "none") return;
    activeJobId = job.id;
    setProgress(job.progress, job.stage, job.message, job.status);
    const options = job.options || {};
    const result = job.result || {};
    const parsed = result.parsed || {};
    const llm = result.llm?.run || {};
    elements.details.innerHTML = [
      ["任务编号", job.id],
      ["开始时间", job.started_at || job.created_at || "等待中"],
      ["处理模式", options.parse_mode === "deep" ? "深度解析（基础结果 + DeepSeek）" : "基础解析（不调用大模型）"],
      ["处理设置", `OCR最多 ${options.ocr_pages ?? 0} 页`],
      parsed.documents != null ? ["完成结果", `${parsed.documents} 份PDF / ${parsed.pages} 页 / ${result.graph?.nodes ?? 0} 个节点`] : null,
      llm.candidates != null ? ["语义校验", `${llm.passed ?? 0}/${llm.candidates} 通过`] : null,
    ].filter(Boolean).map(([label, value]) => `<div class="detail-row"><small>${escapeHtml(label)}</small>${escapeHtml(value)}</div>`).join("");
    elements.error.hidden = !job.error;
    elements.error.textContent = job.error || "";
    const busy = ["queued", "running"].includes(job.status);
    jobIsBusy = busy;
    renderSelectedFiles();
    elements.rerun.disabled = busy;
    elements.deepRun.disabled = busy;
    if (busy) schedulePoll();
    if (["completed", "failed"].includes(job.status)) {
      clearTimeout(pollTimer);
      pollTimer = null;
      loadOverview();
    }
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pollJob, 900);
  }

  async function pollJob() {
    if (!activeJobId) return;
    try {
      renderJob(await getJson(`/api/pipeline/jobs/${encodeURIComponent(activeJobId)}`));
    } catch (error) {
      elements.message.textContent = `任务状态读取失败：${error.message}`;
      schedulePoll();
    }
  }

  function uploadFiles(files) {
    return new Promise((resolve, reject) => {
      const data = new FormData();
      files.forEach((file) => data.append("files", file, file.name));
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/documents/upload");
      xhr.upload.onprogress = (event) => {
        const percent = event.lengthComputable ? Math.round(event.loaded / event.total * 100) : 0;
        setProgress(percent, "upload", `正在上传 ${files.length} 个PDF（${formatBytes(event.loaded)} / ${formatBytes(event.total)}）`, "running");
      };
      xhr.onload = () => {
        let payload = {};
        try { payload = JSON.parse(xhr.responseText || "{}"); } catch (_) { /* handled below */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
        else reject(new Error(payload.error || `上传失败：HTTP ${xhr.status}`));
      };
      xhr.onerror = () => reject(new Error("上传网络连接失败"));
      xhr.send(data);
    });
  }

  function jobOptions(parseMode, reuseParsed = false, ocrPagesOverride = null) {
    return {
      ocr_pages: ocrPagesOverride == null ? Math.max(0, Math.min(1000, Number(elements.ocrPages.value || 0))) : ocrPagesOverride,
      force_ocr: elements.forceOcr.checked,
      parse_mode: parseMode,
      llm_model: "deepseek-v4-flash",
      reuse_parsed: reuseParsed,
    };
  }

  async function startJob(parseMode = "basic", reuseParsed = false, ocrPagesOverride = null) {
    const response = await fetch("/api/pipeline/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(jobOptions(parseMode, reuseParsed, ocrPagesOverride)),
    });
    const job = await response.json();
    if (!response.ok && response.status !== 409) throw new Error(job.error || `启动失败：HTTP ${response.status}`);
    renderJob(job);
  }

  async function handleUpload() {
    if (!selectedFiles.length) return;
    setBusy(true);
    elements.error.hidden = true;
    try {
      const result = await uploadFiles(selectedFiles);
      setProgress(100, "upload", result.message || `${result.count} 个PDF已上传并自动触发解析`, "completed");
      selectedFiles = [];
      elements.input.value = "";
      renderSelectedFiles();
      await loadOverview();
      if (result.job) renderJob(result.job);
    } catch (error) {
      setProgress(0, "failed", "上传失败", "failed");
      elements.error.hidden = false;
      elements.error.textContent = error.message;
    } finally {
      setBusy(false);
    }
  }

  function setBusy(busy) {
    elements.uploadOnly.disabled = busy || !selectedFiles.length;
    elements.uploadRun.disabled = busy || !selectedFiles.length;
    elements.rerun.disabled = busy || jobIsBusy;
    elements.reextract.disabled = busy || jobIsBusy;
    elements.deepRun.disabled = busy || jobIsBusy;
  }

  function renderMetrics(inventory, status) {
    const parsed = status.parsed || {};
    const ocrCounts = parsed.ocr_status_counts || {};
    const cards = [
      [inventory.count, "目录中的PDF"], [parsed.pages || 0, "已逐页解析"],
      [parsed.text_blocks || 0, "原生文本/识别块"], [Object.values(ocrCounts).reduce((sum, value) => sum + Number(value || 0), 0), "按需OCR任务"],
    ];
    elements.metrics.innerHTML = cards.map(([value, label]) => `<div class="card metric-card"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
  }

  function pageTypeSummary(types) {
    const labels = { native_text: "原生文本", table: "表格", drawing: "图纸", scanned: "扫描", mixed: "图文混合", blank: "空白", not_parsed: "未处理" };
    const entries = Object.entries(types || {}).filter(([, count]) => count);
    return entries.length ? entries.map(([type, count]) => `${labels[type] || type} ${count}`).join("；") : "暂无逐页结果";
  }

  function ocrSummary(counts) {
    const entries = Object.entries(counts || {});
    if (!entries.length) return '<span class="badge available">无需OCR</span>';
    const warning = entries.some(([status]) => ["pending", "failed", "engine_missing"].includes(status));
    const labels = { pending: "待执行", done: "已完成", failed: "失败", engine_missing: "引擎缺失", skipped_limit: "本次未执行" };
    return `<span class="badge ${warning ? "pending" : "available"}">${entries.map(([status, count]) => `${labels[status] || status} ${count}`).join("；")}</span>`;
  }

  function renderDocuments(inventory) {
    elements.summary.textContent = inventory.unparsed_count
      ? `${inventory.parsed_count}/${inventory.count} 当前；${inventory.unparsed_count} 个待自动更新`
      : `${inventory.parsed_count}/${inventory.count} 已是最新`;
    elements.summary.className = `badge ${inventory.unparsed_count ? "pending" : "available"}`;
    if (!inventory.rows.length) {
      elements.rows.innerHTML = '<tr><td colspan="6" class="detail-empty">原始资料目录中暂无PDF，请先上传。</td></tr>';
      return;
    }
    elements.rows.innerHTML = inventory.rows.map((row) => `<tr>
      <td><strong>${escapeHtml(row.file_name)}</strong><small class="table-subtext">${escapeHtml(row.modified_at)}</small></td>
      <td>${formatBytes(row.size_bytes)}</td>
      <td>${row.parsed ? `${escapeHtml(row.kind || "资料")}<small class="table-subtext">${escapeHtml(row.pages)} 页</small>` : `<span class="missing-text">${row.stale ? "文件已变化" : "新增文件"}</span>`}</td>
      <td>${escapeHtml(pageTypeSummary(row.page_types))}</td>
      <td>${row.parsed ? `文本块 ${row.text_blocks}<br/>表格 ${row.tables}<br/>${ocrSummary(row.ocr_status_counts)}` : '<span class="missing-text">等待解析任务</span>'}</td>
      <td><span class="badge ${row.parsed ? "available" : "pending"}">${row.parsed ? "已是最新" : "等待自动更新"}</span></td>
    </tr>`).join("");
  }

  async function loadOverview() {
    try {
      const [inventory, status] = await Promise.all([getJson("/api/documents"), getJson("/api/status", {})]);
      renderMetrics(inventory, status);
      renderDocuments(inventory);
    } catch (error) {
      elements.rows.innerHTML = `<tr><td colspan="6">状态加载失败：${escapeHtml(error.message)}</td></tr>`;
    }
  }

  elements.drop.addEventListener("click", () => elements.input.click());
  elements.drop.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); elements.input.click(); } });
  elements.input.addEventListener("change", () => setFiles(elements.input.files));
  ["dragenter", "dragover"].forEach((name) => elements.drop.addEventListener(name, (event) => { event.preventDefault(); elements.drop.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach((name) => elements.drop.addEventListener(name, (event) => { event.preventDefault(); elements.drop.classList.remove("dragging"); }));
  elements.drop.addEventListener("drop", (event) => setFiles(event.dataTransfer.files));
  elements.uploadOnly.addEventListener("click", handleUpload);
  elements.uploadRun.addEventListener("click", handleUpload);
  elements.rerun.addEventListener("click", async () => { try { await startJob("basic", false); } catch (error) { elements.error.hidden = false; elements.error.textContent = error.message; } });
  elements.reextract.addEventListener("click", async () => { try { await startJob("basic", true, 0); } catch (error) { elements.error.hidden = false; elements.error.textContent = error.message; } });
  elements.deepRun.addEventListener("click", async () => { try { await startJob("deep", true); } catch (error) { elements.error.hidden = false; elements.error.textContent = error.message; } });
  elements.refresh.addEventListener("click", async () => { await loadOverview(); const latest = await getJson("/api/pipeline/jobs/latest", { status: "none" }); renderJob(latest); });

  loadOverview();
  getJson("/api/pipeline/jobs/latest", { status: "none" }).then(renderJob);
  setInterval(() => {
    loadOverview();
    getJson("/api/pipeline/jobs/latest", { status: "none" }).then(renderJob);
  }, 5000);
})();
