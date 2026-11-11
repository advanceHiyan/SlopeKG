(function () {
  'use strict';
  const {getJson,escapeHtml:e} = window.SlopeKGApi, $ = id => document.getElementById(id);
  let documents = [], runs = [];
  const selected = new Set();
  function render() {
    const q = $('librarySearch').value.trim().toLowerCase();
    const shown = documents.filter(d => d.path.toLowerCase().includes(q));
    $('libraryCount').textContent = `${documents.length} 份资料 · 当前显示 ${shown.length} 份`;
    $('libraryRows').innerHTML = shown.map(d => `<tr><td><input type="checkbox" aria-label="选择 ${e(d.name)}" data-file="${e(d.path)}" ${selected.has(d.path)?'checked':''}></td><td><strong>${e(d.name)}</strong><small class="library-path">${e(d.path)}</small></td><td>${(d.size_bytes/1024/1024).toFixed(2)} MB</td><td><button class="button ghost" data-preview="${e(d.path)}">预览原文</button></td></tr>`).join('') || '<tr><td colspan="4">没有匹配的资料。</td></tr>';
    $('identifySelected').disabled=!selected.size;
    $('identifySelected').textContent=selected.size?`识别所选 ${selected.size} 份资料 →`:'选择资料后开始识别';
    $('libraryRuns').innerHTML=runs.slice(0,6).map(r=>`<a class="library-run" href="./pdf-ingestion.html?${new URLSearchParams({run:r.run_id,tab:'results'})}"><strong>${e(r.title)}</strong><span>${e({completed:'已完成 · 查看结果',partial:'部分完成',failed:'失败 · 查看记录',running:'正在识别',prepared:'已准备'}[r.processing_status]||r.processing_status)}</span></a>`).join('')||'<p>暂无识别任务。</p>';
  }
  async function load() {
    try { const [d,r]=await Promise.all([getJson('/api/recognition/documents'),getJson('/api/agent-recognition/runs')]);documents=d.documents||[];runs=r.runs||[];render(); }
    catch(err){$('libraryStatus').textContent='资料读取失败：'+err.message;}
  }
  $('librarySearch').oninput=render;
  $('libraryRows').onchange=event=>{const p=event.target.dataset.file;if(p){event.target.checked?selected.add(p):selected.delete(p);render();}};
  $('libraryRows').onclick=event=>{const b=event.target.closest('[data-preview]');if(!b)return;$('libraryPreviewTitle').textContent=b.dataset.preview;$('libraryPdf').src='/data/rawPDF/'+b.dataset.preview.split('/').map(encodeURIComponent).join('/');$('libraryPreview').showModal();};
  $('libraryPreview').addEventListener('close',()=>{$('libraryPdf').src='about:blank';});
  $('identifySelected').onclick=()=>{const q=new URLSearchParams({new:'1',return_to:location.pathname});for(const f of selected)q.append('file',f);location.href='./pdf-ingestion.html?'+q;};
  $('libraryUpload').onclick=()=>$('libraryInput').click();
  $('libraryInput').onchange=async()=>{const files=[...$('libraryInput').files];if(!files.length)return;const form=new FormData();files.forEach(f=>form.append('files',f,f.name));$('libraryUpload').disabled=true;$('libraryStatus').textContent='正在保存资料并建立本地索引…';try{const r=await fetch('/api/documents/upload',{method:'POST',body:form}),d=await r.json();if(!r.ok)throw Error(d.error||r.status);for(const f of d.files||[])selected.add(f.file_name);$('libraryStatus').textContent=d.message;await load();}catch(err){$('libraryStatus').textContent='上传未完成：'+err.message;}finally{$('libraryUpload').disabled=false;$('libraryInput').value='';}};
  load();
})();
