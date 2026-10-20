(function () {
  'use strict';
  const {getJson, requestJson, escapeHtml: e} = window.SlopeKGApi;
  const icons = {
    edit: '<path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    save: '<path d="M5 3h12l4 4v14H3V3zM7 3v6h10V3M7 21v-8h10v8"/>',
  };
  function icon(name) {
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.edit}</svg>`;
  }
  function status(node, message, state = '') {
    node.textContent = message; node.dataset.state = state;
    node.setAttribute('role', state === 'error' ? 'alert' : 'status');
  }
  async function busy(form, node, action) {
    if (form.dataset.busy === 'true') return;
    form.dataset.busy = 'true'; form.setAttribute('aria-busy', 'true');
    const controls = [...form.querySelectorAll('button,input,select,textarea')].map(el => [el, el.disabled]);
    controls.forEach(([el]) => { el.disabled = true; });
    try { await action(); }
    catch (error) { status(node, error.message, 'error'); }
    finally {
      form.dataset.busy = 'false'; form.removeAttribute('aria-busy');
      controls.forEach(([el, disabled]) => { el.disabled = disabled; });
    }
  }
  async function changed(callback, result, node, message) {
    status(node, message, 'success');
    try { await callback(result); return true; }
    catch (error) { status(node, `${message}，但结果刷新失败：${error.message}。请刷新页面查看。`, 'error'); return false; }
  }

  function mountSlopeEditor(target, row, schema, onChanged) {
    const toggle = target.querySelector('.manual-edit-toggle');
    const editor = target.querySelector('.manual-editor');
    if (!schema?.slope_fields?.length) {
      toggle.disabled = true; toggle.textContent = '录入暂不可用';
      const note = document.createElement('p'); note.className = 'manual-error'; note.setAttribute('role', 'alert');
      note.textContent = '录入配置读取失败，请刷新重试。现有筛查结果仍可查看。';
      editor.before(note);
      return;
    }
    const specs = schema.slope_fields || [];
    let loaded = false, loading = false;
    editor.id = `manual-editor-${row.slope_id}`;
    toggle.setAttribute('aria-controls', editor.id);
    toggle.setAttribute('aria-expanded', 'false');
    toggle.innerHTML = `${icon('edit')}<span>人工录入</span>`;
    toggle.addEventListener('click', () => {
      editor.hidden = !editor.hidden;
      toggle.setAttribute('aria-expanded', String(!editor.hidden));
      toggle.querySelector('span').textContent = editor.hidden ? '人工录入' : '收起录入';
      if (!editor.hidden && !loaded) load();
    });

    async function load() {
      if (loading) return;
      loading = true;
      editor.innerHTML = '<div class="loading" role="status">正在读取人工记录与当前档案…</div>';
      try {
        const [record, detail] = await Promise.all([
          getJson(`/api/manual/slopes/${encodeURIComponent(row.slope_id)}`),
          getJson(`/api/slopes/${encodeURIComponent(row.slope_id)}`),
        ]);
        render(record, detail.slope?.props || {}); loaded = true;
      } catch (error) {
        editor.innerHTML = `<p class="manual-error" role="alert">读取失败：${e(error.message)}。尚未载入原记录，不能保存。</p><button class="button secondary retry-manual" type="button">重新读取</button>`;
        editor.querySelector('.retry-manual').addEventListener('click', load);
      } finally { loading = false; }
    }

    function render(record, existing) {
      function control(spec) {
        const saved = record.fields?.[spec.code] ?? '';
        const current = existing[spec.code] ?? (spec.code === 'slope_height_m' ? existing.slope_height_max_m : spec.code === 'slope_gradient_deg' ? existing.slope_gradient_max_deg : '');
        const help = current !== '' && current != null ? `当前有效值：${typeof current === 'object' ? JSON.stringify(current) : current}` : '当前暂无数据';
        const input = spec.type === 'enum'
          ? `<select name="${e(spec.code)}"><option value="">沿用自动提取值</option>${(spec.options || []).map(option => `<option value="${e(option)}" ${saved === option ? 'selected' : ''}>${e(option)}</option>`).join('')}</select>`
          : `<input name="${e(spec.code)}" type="${spec.type === 'number' ? 'number' : 'text'}" value="${e(saved)}" ${spec.type === 'number' ? `step="any" ${spec.min == null ? '' : `min="${e(spec.min)}"`} ${spec.max == null ? '' : `max="${e(spec.max)}"`}` : 'maxlength="2000"'} />`;
        return `<label class="manual-field"><span>${e(spec.label)}</span>${input}<small>${e(help)}</small></label>`;
      }
      const groups = schema.slope_field_groups || [{title:'补充信息',description:'填写需要补充或修正的字段。',fields:specs.map(s => s.code)}];
      const sections = groups.map(group => {
        const members = specs.filter(spec => group.fields.includes(spec.code));
        return members.length ? `<fieldset class="manual-form-section"><legend>${e(group.title)}</legend><p class="manual-section-note">${e(group.description)}</p><div class="manual-field-grid">${members.map(control).join('')}</div></fieldset>` : '';
      }).join('');
      editor.innerHTML = `<form class="manual-slope-form">
        <div class="manual-editor-head"><div><h3>补充与修正档案</h3><p>空白项沿用PDF自动提取值。已有人工值会显示在输入框中，清空后保存可撤销该项覆盖。</p></div><span class="badge ${record.updated_at ? 'approved' : 'pending'}">${record.updated_at ? '已有人工记录' : '尚未补录'}</span></div>
        <div class="manual-meta-grid"><label>观测日期<input name="observed_at" type="date" value="${e(record.observed_at || '')}"/><small class="form-help">填写近期观测信息时必填</small></label><label>来源与备注<input name="note" maxlength="2000" value="${e(record.note || '')}" placeholder="记录资料名称、巡检人员或来源"/></label></div>
        ${sections}
        <div class="action-row manual-form-footer"><button class="button" type="submit">${icon('save')}保存并更新结果</button><button class="button danger reset-manual" type="button" ${record.updated_at ? '' : 'disabled'}>撤销人工值</button><span class="manual-save-status" role="status" aria-live="polite"></span></div>
      </form>`;
      const form = editor.querySelector('form'), message = editor.querySelector('.manual-save-status');
      const dateInput = form.elements.namedItem('observed_at');
      function updateDateRequirement() {
        dateInput.required = specs.some(spec => spec.dynamic && String(form.elements.namedItem(spec.code)?.value || '').trim());
      }
      form.addEventListener('input', updateDateRequirement); updateDateRequirement();
      form.addEventListener('submit', event => {
        event.preventDefault();
        const data = new FormData(form), fields = {};
        for (const spec of specs) {
          const value = String(data.get(spec.code) ?? '').trim();
          if (value !== '') fields[spec.code] = spec.type === 'number' ? Number(value) : value;
        }
        busy(form, message, async () => {
          status(message, '正在保存并更新…');
          const result = await requestJson(`/api/manual/slopes/${encodeURIComponent(row.slope_id)}`, {method:'POST', body:{fields, observed_at:data.get('observed_at'), note:data.get('note')}});
          await changed(onChanged, result, message, '已保存');
        });
      });
      form.querySelector('.reset-manual').addEventListener('click', () => {
        if (!window.confirm('撤销该边坡的全部人工值？PDF自动提取结果会保留。')) return;
        busy(form, message, async () => {
          status(message, '正在撤销…');
          const result = await requestJson(`/api/manual/slopes/${encodeURIComponent(row.slope_id)}`, {method:'DELETE'});
          await changed(onChanged, result, message, '已撤销人工值');
        });
      });
    }
  }

  async function mountRuleForm(onChanged) {
    const form = document.getElementById('manualRuleForm');
    const toggle = document.getElementById('toggleRuleForm');
    const container = document.getElementById('manualRuleConditions');
    const message = document.getElementById('manualRuleStatus');
    let savedRuleId = null;
    toggle.disabled = true;
    let schema;
    try { schema = await getJson('/api/manual/schema'); }
    catch (error) { status(message, `录入配置读取失败：${error.message}，请刷新重试`, 'error'); return; }
    toggle.disabled = false;
    function setOpen(open) {
      form.hidden = !open; toggle.setAttribute('aria-expanded', String(open));
      toggle.innerHTML = `${icon(open ? 'close' : 'plus')}<span>${open ? '收起表单' : '新建规则'}</span>`;
      if (open) form.elements.namedItem('title').focus();
    }
    toggle.addEventListener('click', () => setOpen(form.hidden));
    document.getElementById('cancelRuleForm').addEventListener('click', () => { setOpen(false); toggle.focus(); });
    function updateRemoveButtons() {
      container.querySelectorAll('.remove-condition').forEach(button => { button.disabled = container.children.length === 1; });
      document.getElementById('addRuleCondition').disabled = container.children.length >= 10;
    }
    function addCondition() {
      if (container.children.length >= 10) return;
      const row = document.createElement('div'); row.className = 'manual-condition-row';
      row.innerHTML = `<label>字段<select class="condition-field">${schema.rule_fields.map(item => `<option value="${e(item.code)}">${e(item.label)}</option>`).join('')}</select></label><label>判断<select class="condition-operator">${schema.rule_operators.map(item => `<option value="${e(item.code)}">${e(item.label)}</option>`).join('')}</select></label><label>比较值<input class="condition-value" placeholder="阈值或文字，多值用逗号分隔"/></label><button class="button ghost small remove-condition" type="button" aria-label="移除此条件">${icon('close')}移除</button>`;
      const operator = row.querySelector('.condition-operator'), value = row.querySelector('.condition-value');
      operator.addEventListener('change', () => {
        value.disabled = operator.value === 'exists';
        value.placeholder = value.disabled ? '无需填写比较值' : '阈值或文字，多值用逗号分隔';
      });
      row.querySelector('.remove-condition').addEventListener('click', () => {
        if (container.children.length > 1) { row.remove(); updateRemoveButtons(); }
      });
      container.appendChild(row); updateRemoveButtons();
    }
    addCondition(); document.getElementById('addRuleCondition').addEventListener('click', addCondition);
    form.addEventListener('submit', event => {
      event.preventDefault();
      const data = new FormData(form);
      const conditions = [...container.children].map(row => ({field:row.querySelector('.condition-field').value,
        operator:row.querySelector('.condition-operator').value, value:row.querySelector('.condition-value').value}));
      const body = Object.fromEntries(['title','match','priority','approval_status','reason','logic_note'].map(key => [key,data.get(key)]));
      body.conditions = conditions;
      if (savedRuleId) body.id = savedRuleId;
      busy(form, message, async () => {
        status(message, '正在校验并保存…');
        const result = await requestJson('/api/manual/rules', {method:'POST', body});
        savedRuleId = result.rule?.id;
        if (await changed(onChanged, result, message, result.message || '规则已保存')) {
          savedRuleId = null; form.reset(); container.replaceChildren(); addCondition(); setOpen(false);
        }
      });
    });
  }

  window.SlopeKGManual = {icon, mountSlopeEditor, mountRuleForm};
})();
