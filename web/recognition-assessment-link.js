(function(){
  const api=window.SlopeKGApi,debug=window.SlopeKGDebug;if(!api||!debug)return;
  const host=document.createElement('section');host.className='card';host.id='recognitionAssessmentBridge';
  host.innerHTML='<h2>继续到国标风险预评估</h2><p>绑定当前识别快照，检查对象、时期和工况，自动执行可用的表15/17；缺口、冲突及专业判断分别展示。模型辅助需另行允许。</p><a id="recognitionToAssessment" class="button secondary">衔接当前记录，查看PDF到风险全过程</a><p id="recognitionAssessmentState" role="status"></p><div id="recognitionAssessmentCases"></div>';
  document.querySelector('main').append(host);let generation=0;
  async function update(){
    const n=++generation,c=debug.context(),a=host.querySelector('a'),status=host.querySelector('[role=status]'),links=host.querySelector('#recognitionAssessmentCases');
    a.href='./assessment-debug.html?'+new URLSearchParams({route:c.route,run:c.run_id,document:c.document});
    a.hidden=!c.document;links.innerHTML='';if(!c.document){status.textContent='先选择本次识别中的一份PDF。';return;}
    status.textContent='正在查找与本次PDF绑定的评估记录…';
    try{const data=await api.getJson('/api/assessment-debug/catalog?'+new URLSearchParams(c));if(n!==generation)return;
      status.textContent=data.linked_cases.length?`此PDF已有${data.linked_cases.length}个评估案例；识别快照不同时分别保留。`:'当前还没有关联评估；不会因为浏览识别记录而自动发送模型请求。';
      links.innerHTML=data.linked_cases.map(r=>`<p><a href="./assessment-debug.html?case=${encodeURIComponent(r.case_id)}">打开评估全过程 · ${api.escapeHtml(r.subject_label)} · ${api.escapeHtml(r.scope.time.raw)} · ${api.escapeHtml(r.scope.scenario.raw)} · 第${r.resolution_version}版${r.same_recognition_snapshot?'':'（另一识别快照）'}</a></p>`).join('');
    }catch(e){if(n===generation)status.textContent='风险衔接信息暂不可读：'+e.message;}
  }
  window.addEventListener('debugcontextchange',update);window.addEventListener('debughistorychange',update);window.addEventListener('debugrecordstatechange',update);update();
})();
