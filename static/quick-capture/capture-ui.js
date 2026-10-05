const selection = document.getElementById('selection');
let start = null;
let exclude = [];
window.capture.exclude((regions) => { exclude = Array.isArray(regions) ? regions : []; });
window.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || event.target.closest('.window-capture')) return;
  if (exclude.some(rect => event.clientX >= rect.x && event.clientX <= rect.x + rect.width && event.clientY >= rect.y && event.clientY <= rect.y + rect.height)) return;
  start = { x: event.clientX, y: event.clientY };
  window.captureButtons.lock();
  document.body.setPointerCapture(event.pointerId);
});
function rectangle(event) {
  return { x: Math.min(start.x, event.clientX), y: Math.min(start.y, event.clientY), width: Math.abs(event.clientX - start.x), height: Math.abs(event.clientY - start.y) };
}
window.addEventListener('pointermove', (event) => {
  if (!start) return;
  const rect = rectangle(event);
  if (Math.max(rect.width, rect.height) >= 3) document.body.classList.add('selecting');
  selection.hidden = false;
  Object.assign(selection.style, { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px` });
});
window.addEventListener('pointerup', (event) => {
  if (!start) return;
  const rect = rectangle(event); start = null; selection.hidden = true;
  document.body.classList.remove('selecting');
  window.captureButtons.unlock();
  if (rect.width >= 3 && rect.height >= 3) window.capture.select(rect);
  else if (Math.max(rect.width, rect.height) < 3) window.capture.dismiss();
});
window.addEventListener('pointercancel', () => {
  start = null; selection.hidden = true;
  document.body.classList.remove('selecting');
  window.captureButtons.unlock();
});
window.addEventListener('keydown', (event) => { if (event.key === 'Escape') window.capture.cancel(); });
