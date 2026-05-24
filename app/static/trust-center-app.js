function _windowPrint() {
  window.print();
}

function _parseDataArgs(raw) {
  try { return JSON.parse(raw || '[]'); } catch (_) { return []; }
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action]');
  if (!el || !el.dataset.action) return;
  const fn = window[el.dataset.action];
  if (typeof fn !== 'function') return;
  fn.call(el, ..._parseDataArgs(el.dataset.args));
});
