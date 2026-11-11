(function () {
  const basic = window.SlopeKGNav.currentMethod === 'basic';
  document.querySelectorAll('.recognition-card').forEach(section => { section.hidden = basic; });
  document.getElementById('basicMethod').hidden = !basic;
  document.getElementById('ingestMetrics').hidden = true;
  if (basic) document.querySelector('.legacy-controls').open = true;
})();
