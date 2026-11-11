(function () {
  'use strict';
  const page = location.pathname.split('/').pop(), params = new URLSearchParams(location.search);
  function localPage(value) {
    if (typeof value !== 'string' || !value.trim()) return '';
    try {
      const u = new URL(value, location.href);
      return u.origin === location.origin && /^\/web\/[a-z0-9-]+\.html$/.test(u.pathname) ? u.pathname + u.search + u.hash : '';
    } catch (_) { return ''; }
  }
  function withReturn(value, from = location.pathname + location.search + location.hash) {
    const target = localPage(value), back = localPage(from);
    if (!target) return '#';
    const u = new URL(target, location.origin);
    if (back) u.searchParams.set('return_to', back);
    return u.pathname + u.search + u.hash;
  }
  const returnTo = localPage(params.get('return_to') || '');
  const methods = [
    ['basic', 'recognition-methods.html?method=basic', '基读 Basic'],
    ['knowledge', 'knowledge-recognition.html', '知联 Knowledge'],
    ['visual', 'recognition-methods.html?method=visual', '视读 Visual'],
    ['document', 'document-recognition.html', '整读 Document'],
    ['agent', 'pdf-ingestion.html', '智能识别'],
  ];
  const methodLabels = Object.fromEntries(methods.map(([id, , label]) => [id, label]));
  const currentMethod = page === 'recognition-methods.html'
    ? (params.get('method') === 'basic' ? 'basic' : 'visual')
    : methods.find(([, file]) => file === page)?.[0];
  window.SlopeKGNav = { localPage, withReturn, returnTo, methodLabels, currentMethod };
  const oldBack = document.getElementById('recognitionBack');
  let header = document.querySelector('.site-header');
  if (!header) { header = document.createElement('header'); header.className = 'site-header'; document.body.prepend(header); }
  header.classList.add('app-header');
  const links = [
    ['index.html', '总览'],
    ['pdf-library.html', '资料管理'],
    ['recognition-methods.html', '识别方法'],
    ['slopes.html', '边坡档案'],
    ['graph.html', '关系图谱'],
    ['slope-assessment.html', '风险研判'],
  ];
  const moreLinks = [
    ['rules.html', '规则库'],
    ['data-status.html', '数据与接口'],
    ['recognition-debug.html', '开发工具 · 识别调试'],
  ];
  function link([file, title]) {
    const current = page === file ? 'page' : currentMethod && file === 'recognition-methods.html' ? 'location' : '';
    return `<a href="./${file}" ${current ? `class="active" aria-current="${current}"` : ''}>${title}</a>`;
  }
  header.innerHTML = `<div class="app-header-inner"><a class="app-brand" href="./index.html">SlopeKG<small>边坡资料与风险分析</small></a><nav class="app-nav" aria-label="主导航">${links.map(link).join('')}<details class="app-more"><summary>更多</summary><div>${moreLinks.map(link).join('')}</div></details></nav></div>`;
  if (oldBack || returnTo) {
    const bar = document.createElement('div'); bar.className = 'app-context';
    const a = oldBack || document.createElement('a'); a.className = 'context-back';
    if (returnTo) a.href = returnTo;
    a.textContent = returnTo ? '← 返回刚才的任务 / 页面' : '← 返回识别结果';
    bar.append(a); header.after(bar);
  }
  const methodNav = document.querySelector('.method-nav');
  if (methodNav) {
    const taskPath = '/web/pdf-ingestion.html';
    let task = returnTo.startsWith(taskPath + '?') ? returnTo : '';
    if (!task && currentMethod !== 'agent') {
      try {
        const saved = JSON.parse(sessionStorage.getItem('slopekg-agent-context') || '{}');
        const target = localPage(saved.url);
        if (target.startsWith(taskPath + '?')) task = target;
      } catch (_) {}
    }
    for (const [id, file, label] of methods) {
      const a = document.createElement('a');
      const target = id === 'agent' && task ? task : `./${file}`;
      a.href = id === 'agent' && currentMethod === 'agent' ? location.href
        : returnTo && target !== returnTo ? withReturn(target, returnTo) : target;
      a.textContent = label;
      if (id === currentMethod) {
        a.classList.add('active');
        a.setAttribute('aria-current', 'page');
      }
      methodNav.append(a);
    }
  }
  document.addEventListener('click', event => {
    if (!event.target.closest('.app-more')) header.querySelector('.app-more').open = false;
  });
})();
