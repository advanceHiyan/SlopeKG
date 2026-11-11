(function () {
  "use strict";
  const { requestJson, escapeHtml: e } = window.SlopeKGApi;
  const endpoint = "/api/dossier-recognition";
  const query = new URLSearchParams(location.search);
  const incomingRun = query.get("recognition_run") || "";
  const incomingDocument = query.get("recognition_document") || "";
  const dialog = document.getElementById("dossierReviewDialog");
  const form = dialog.querySelector("form");
  const content = dialog.querySelector(".dossier-review-content");
  const error = dialog.querySelector("[role=alert]");
  const submit = form.querySelector("[type=submit]");
  const cancel = form.querySelector("[type=button]");

  function show(title, html, button) {
    dialog.querySelector("h2").textContent = title;
    content.innerHTML = html;
    error.textContent = "";
    submit.textContent = button;
    submit.disabled = false;
    form.onsubmit = null;
    dialog.showModal();
  }
  cancel.onclick = () => dialog.close();
  dialog.addEventListener("cancel", event => {
    if (form.dataset.busy === "true") event.preventDefault();
  });

  function submission(save, onSaved) {
    form.onsubmit = async event => {
      event.preventDefault();
      if (form.dataset.busy === "true") return;
      form.dataset.busy = "true";
      const data = new FormData(form);
      const controls = [...form.querySelectorAll("button,input,select,textarea")].filter(node => !node.disabled);
      controls.forEach(node => { node.disabled = true; });
      error.textContent = "";
      try {
        const result = await save(data);
        dialog.close();
        await onSaved(result);
      } catch (err) {
        error.textContent = err.message;
      } finally {
        form.dataset.busy = "false";
        controls.forEach(node => { node.disabled = false; });
      }
    };
  }

  function sourceEvidence(fact) {
    return fact.evidence.map(ref => `<p>${e(ref.quote)}<br>
      ${String(ref.url).startsWith("/api/agent-recognition/artifact?") ? `<a href="${e(ref.url)}" target="_blank" rel="noopener">查看本资料第 ${e(ref.page)} 页</a>` : ""}
      ${ref.locator ? `<small> · ${e(ref.locator)}</small>` : ""}</p>`).join("") || "原页定位暂不可用";
  }

  function formatted(values, field) {
    const raw = Object.entries(values || {}).find(([key, value]) => key.endsWith("_raw") && value);
    if (raw) return raw[1];
    const value = values?.[field];
    const unit = field.endsWith("_deg") ? "°" : field.endsWith("_m") ? " m" : "";
    const low = values?.slope_height_min_m ?? values?.slope_gradient_min_deg;
    const high = values?.slope_height_max_m ?? values?.slope_gradient_max_deg;
    if (value == null && (low != null || high != null)) {
      return (low == null ? `上限 ${high}` : high == null ? `下限 ${low}` : `${low}～${high}`) + unit;
    }
    if (value === null || value === undefined || value === "") return "未填写";
    return String(value) + unit;
  }

  function detected(fact) {
    if (fact.patch) return formatted(fact.patch, fact.field);
    const value = typeof fact.value === "object" ? JSON.stringify(fact.value) : String(fact.value ?? "未识别");
    return [value, fact.unit].filter(Boolean).join(" ");
  }

  function mount(panel, slope, label, refresh) {
    let state;
    const message = panel.querySelector("[role=status]");
    const sources = panel.querySelector(".dossier-linked-sources");
    const link = panel.querySelector("button");
    const request = (operation, data) => requestJson(endpoint, { method: "POST",
      body: { operation, slope_id: slope.id, ...data } });

    function render() {
      const list = state.sources.map(source => {
        const facts = Object.values(source.facts);
        const adopted = Object.values(state.adopted).filter(value => value.source_id === source.id).length;
        return `<details class="detail-block"><summary>${e(source.document)} · ${e(source.object)}</summary>
          <p>${facts.length} 条识别候选，${adopted} 项已写入档案。</p>
          <button type="button" class="button secondary small" data-review-source="${e(source.id)}">核对并写入档案</button>
          <details class="detail-block"><summary>查看全部候选和原页</summary>
            ${facts.map(fact => `<div class="detail-row"><strong>${e(fact.label)} · ${e(detected(fact))}</strong>
              ${fact.condition ? `<small>${e(fact.condition)}</small>` : ""}${sourceEvidence(fact)}</div>`).join("")}
          </details></details>`;
      }).join("");
      const history = (state.history || []).slice().reverse().map(entry => `<div class="detail-row">
        <small>${e(entry.actor)} · ${e(entry.time)}</small><p>${e(entry.reason)}</p>
        ${entry.changes.map(change => {
          const source = state.sources.find(item => item.id === change.adopted.source_id);
          const fact = source?.facts[change.adopted.fact_id];
          return `<p>${e(fact?.label || change.field)} · ${e(formatted(change.previous, change.field))} →
            ${e(formatted(change.adopted.patch, change.field))}<br><small>${e(source?.document)}</small></p>`;
        }).join("")}</div>`).join("");
      sources.innerHTML = (list || "<p>暂无关联的识别资料。</p>") +
        (history ? `<details class="detail-block"><summary>档案写入历史</summary>${history}</details>` : "");
    }

    link.addEventListener("click", async () => {
      link.disabled = true;
      message.textContent = "正在读取已保存的识别任务…";
      try {
        const { runs } = await requestJson(endpoint);
        if (!panel.isConnected) return;
        if (!runs.length) {
          message.textContent = "暂无可关联的业务识别任务。请先在智能识别中生成候选。";
          return;
        }
        show("关联识别资料", `<p>当前边坡 <strong>${e(label)}</strong></p>
          <label>识别任务<select name="run_id" required>${runs.map(run =>
            `<option value="${e(run.run_id)}">${e(run.documents[0].name)} · ${e(run.run_id)}</option>`).join("")}</select></label>
          <label>资料<select name="document_id" required></select></label>
          <label>识别对象<select name="object_id" required></select></label>
          <label>核对人<input name="actor" required maxlength="100" autocomplete="off"></label>
          <label><input name="same_object_confirmed" type="checkbox" required> 我已核对路线、桩号和侧别，识别对象与本档案是同一处边坡</label>`, "保存关联");
        const run = form.elements.run_id;
        const documentSelect = form.elements.document_id;
        const object = form.elements.object_id;
        function chooseObject() {
          const doc = runs.find(item => item.run_id === run.value).documents.find(item => item.id === documentSelect.value);
          object.innerHTML = '<option value="">请选择识别对象</option>' + doc.objects.map(item =>
            `<option value="${e(item.id)}">${e(item.name)}</option>`).join("");
        }
        function chooseDocument(preferred) {
          const documents = runs.find(item => item.run_id === run.value).documents;
          documentSelect.innerHTML = documents.map(item => `<option value="${e(item.id)}">${e(item.name)}</option>`).join("");
          if (documents.some(item => item.id === preferred)) documentSelect.value = preferred;
          chooseObject();
        }
        if (runs.some(item => item.run_id === incomingRun)) run.value = incomingRun;
        chooseDocument(run.value === incomingRun ? incomingDocument : "");
        run.onchange = () => chooseDocument("");
        documentSelect.onchange = chooseObject;
        message.textContent = "";
        submission(data => request("link", { ...Object.fromEntries(data), same_object_confirmed: data.get("same_object_confirmed") === "on" }), result => {
          state = result;
          render();
          message.textContent = "已关联。请打开该资料核对候选值。";
        });
      } catch (err) {
        message.textContent = err.message;
      } finally {
        link.disabled = false;
      }
    });

    sources.addEventListener("click", event => {
      const button = event.target.closest("[data-review-source]");
      if (!button) return;
      const source = state.sources.find(item => item.id === button.dataset.reviewSource);
      const facts = Object.values(source.facts).filter(fact => fact.patch);
      show("核对并写入档案", `<p><strong>${e(label)}</strong> · ${e(source.document)}</p>
        <p>按原页逐条勾选。同一字段只选一条。选择与现值不同的候选会更新档案，旧值留在历史记录里。</p>
        ${facts.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>采用</th><th>字段</th><th>原档案值</th><th>识别值</th><th>原页证据</th></tr></thead>
          <tbody>${facts.map(fact => {
            const manual = state.manual_fields.includes(fact.field);
            const adopted = state.adopted[fact.field];
            const current = adopted?.source_id === source.id && adopted.fact_id === fact.id;
            return `<tr><td>${manual ? "已有人工值" : current ? "已写入" :
              `<input type="checkbox" name="fact_id" value="${e(fact.id)}" aria-label="采用 ${e(fact.label)} ${e(detected(fact))}">`}</td>
              <td>${e(fact.label)}</td><td>${e(formatted(state.current[fact.field], fact.field))}</td>
              <td>${e(detected(fact))}${fact.condition ? `<br><small>${e(fact.condition)}</small>` : ""}</td><td>${sourceEvidence(fact)}</td></tr>`;
          }).join("")}</tbody></table></div>
          <label>核对人<input name="actor" required maxlength="100" autocomplete="off"></label>
          <label>采用依据<textarea name="reason" required maxlength="500"></textarea></label>
          <label><input type="checkbox" name="source_reviewed" required> 我已核对原页、数值及适用范围</label>` :
          "<p>这组候选暂无可写入的静态字段，可返回查看全部候选和原页。</p>"}`, "确认写入选中字段");
      submit.disabled = !form.querySelector("[name=fact_id]");
      submission(data => request("adopt", { source_id: source.id, revision: state.revision,
        actor: data.get("actor"), reason: data.get("reason"), source_reviewed: data.get("source_reviewed") === "on", fact_ids: data.getAll("fact_id") }),
      async result => {
        try { await refresh(`已写入 ${result.updated} 项，档案已更新。`); }
        catch (err) { message.textContent = `已保存。刷新档案失败，请重新打开。 ${err.message}`; }
      });
    });

    requestJson(`${endpoint}?${new URLSearchParams({ slope_id: slope.id })}`).then(result => {
      state = result;
      render();
      link.disabled = false;
    }).catch(err => {
      message.textContent = `关联资料暂不可读。 ${err.message}`;
    });
  }

  window.SlopeKGDossierRecognition = { mount };
})();
