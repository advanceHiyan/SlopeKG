(function () {
  "use strict";
  const api = window.SlopeKGApi;
  const $ = id => document.getElementById(id);
  const escape = api.escapeHtml;
  const names = { pdf_full_page: "PDF整页", drawing_title_block: "图纸标题栏", pdf_page_region: "PDF局部", site_photo: "现场照片", unclassified_visual: "未分类图片" };
  const filterIds = ["assetDocument", "assetType", "assetPage", "assetStatus", "assetSearch"];
  let assets = [], documents = new Map(), page = 0;
  const pageSize = 24;

  function publicPath(value, prefix) {
    const path = String(value || "").replaceAll("\\", "/");
    const parts = path.split("/");
    if (!path.startsWith(prefix) || parts.some(p => p === ".." || p === ".") || /[:?#]/.test(path)) return null;
    return "/" + parts.map(encodeURIComponent).join("/");
  }

  function approved(asset) {
    return asset.validation_status === "valid" && asset.slope_id && asset.slope_review_status === "approved";
  }

  function sourceTitle(asset) {
    return asset.source_document_title || documents.get(asset.source_document_id)?.file_name || "";
  }

  function updateOptions(id, values, emptyLabel, label) {
    const select = $(id);
    const selected = select.value;
    select.innerHTML = `<option value="">${escape(emptyLabel)}</option>` + values.map(value => `<option value="${escape(value)}">${escape(label(value))}</option>`).join("");
    select.value = values.includes(selected) ? selected : "";
  }

  function render() {
    const query = $("assetSearch").value.trim().toLowerCase();
    const filtered = assets.filter(a => (!$("assetDocument").value || a.source_document_id === $("assetDocument").value)
      && (!$("assetType").value || a.subtype === $("assetType").value)
      && (!$("assetPage").value || a.source_page === Number($("assetPage").value))
      && (!query || [a.file_name, sourceTitle(a), documents.get(a.source_document_id)?.file_name, a.slope_id].join(" ").toLowerCase().includes(query))
      && (!$("assetStatus").value || ($("assetStatus").value === "pending" ? !approved(a) : $("assetStatus").value === "approved" ? approved(a) : a.validation_status === "invalid")));
    const pages = Math.ceil(filtered.length / pageSize);
    page = Math.min(page, Math.max(0, pages - 1));
    $("assetCount").textContent = `共 ${filtered.length} 张符合条件的图片`;
    $("assetCards").innerHTML = filtered.slice(page * pageSize, (page + 1) * pageSize).map(a => {
      const preview = publicPath(a.href, "output/demo/assets/ocr_pages/");
      const doc = documents.get(a.source_document_id);
      const source = publicPath(doc?.path, "data/rawPDF/");
      const pageNumber = Number.isInteger(a.source_page) && a.source_page > 0 ? a.source_page : null;
      const state = a.validation_status === "invalid" ? "文件或元数据有错误" : approved(a) ? `边坡归属已审核：${a.slope_id}` : "边坡归属待核对";
      return `<article class="card asset-card">
        ${preview ? `<a class="asset-preview" href="${preview}" target="_blank" rel="noopener"><img src="${preview}" alt="${escape(a.file_name)}" loading="lazy" decoding="async"/></a>` : '<div class="asset-preview">此文件暂不支持网页预览</div>'}
        <h2>${escape(sourceTitle(a) || "来源文档待核对")}</h2>
        <p>${escape(names[a.subtype] || a.subtype || "未分类图片")} · PDF第 ${pageNumber ?? "未知"} 页</p>
        <p>${escape(state)}</p><p class="asset-file">${escape(a.file_name)}</p>
        ${source && pageNumber ? `<a href="${source}#page=${pageNumber}" target="_blank" rel="noopener">打开来源PDF</a>` : ""}
      </article>`;
    }).join("") || '<div class="card">没有符合当前条件的图片。</div>';
    $("assetCards").querySelectorAll("img").forEach(img => img.addEventListener("error", () => { img.parentElement.textContent = "图片加载失败，请检查文件"; }));
    $("assetPagination").textContent = pages ? `${page + 1} / ${pages}` : "0 / 0";
    $("assetPrevious").disabled = page === 0;
    $("assetNext").disabled = page + 1 >= pages;
  }

  async function load() {
    $("reloadAssets").disabled = true;
    try {
      const [catalog, quality, parsed] = await Promise.all([
        api.getJson("/api/multimodal/assets"), api.getJson("/api/multimodal/quality"), api.getJson("/api/parsed/documents")
      ]);
      assets = (catalog.assets || []).filter(a => a.asset_type === "VisualAsset");
      documents = new Map((parsed.rows || []).map(d => [d.id, d]));
      const choices = [...new Set(assets.map(a => a.source_document_id).filter(Boolean))];
      updateOptions("assetDocument", choices, "全部文档", id => documents.get(id)?.file_name || id);
      updateOptions("assetType", [...new Set(assets.map(a => a.subtype).filter(Boolean))], "全部类型", type => names[type] || type);
      $("assetSummary").textContent = catalog.status === "not_generated" ? "图片目录尚未生成。完成一次PDF解析后可在这里查看。"
        : `${assets.length} 张图片，${assets.filter(a => a.source_document_id).length} 张已记录来源文档，${assets.filter(approved).length} 张边坡归属已审核。文档归属不代表边坡归属。更新于 ${catalog.generated_at || "未知"}。`;
      const issues = quality.issues || [];
      $("assetIssueSummary").textContent = `目录问题（${issues.length}）`;
      $("assetIssues").innerHTML = issues.map(i => `<li>${escape(i.asset_id)}：${escape(i.message)}</li>`).join("") || "<li>当前检查未发现文件或元数据问题。</li>";
      render();
    } catch (error) {
      assets = [];
      render();
      $("assetSummary").textContent = `目录加载失败：${error.message}。请确认本地服务已启动，然后刷新。`;
      $("assetIssueSummary").textContent = "目录问题（未能读取）";
      $("assetIssues").textContent = "尚无法判断，请刷新重试。";
    } finally { $("reloadAssets").disabled = false; }
  }
  filterIds.forEach(id => $(id).addEventListener("input", () => { page = 0; render(); }));
  $("assetPrevious").addEventListener("click", () => { page--; render(); });
  $("assetNext").addEventListener("click", () => { page++; render(); });
  $("reloadAssets").addEventListener("click", load);
  load();
})();
