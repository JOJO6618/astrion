'use strict';
const variants = [
  {label: 'A', path: '../runs/poster-iris/index.html', condition: '阅读 canvas-design'},
  {label: 'B', path: '../runs/poster-moss/index.html', condition: '未阅读任何技能'},
];
let size = 'desktop';
let revealed = false;
let preference = '';
let observer;
const results = document.getElementById('results');
const notes = document.getElementById('notes');
const status = document.getElementById('save-status');
function updateScale() {
  const width = size === 'desktop' ? 720 : 390;
  const height = size === 'desktop' ? 1000 : 844;
  document.querySelectorAll('.viewport').forEach((viewport) => {
    const scale = Math.min(1, viewport.clientWidth / width);
    const iframe = viewport.querySelector('iframe');
    iframe.style.width = `${width}px`;
    iframe.style.height = `${height}px`;
    iframe.style.transform = `scale(${scale})`;
    iframe.style.marginInline = viewport.clientWidth > width ? `${(viewport.clientWidth - width) / 2}px` : '0';
    viewport.style.height = `${Math.ceil(height * scale)}px`;
  });
}
function render() {
  if (observer) observer.disconnect();
  results.replaceChildren();
  variants.forEach((variant) => {
    const article = document.createElement('article');
    article.className = 'result';
    const header = document.createElement('div');
    header.className = 'result-header';
    const heading = document.createElement('h2');
    heading.textContent = `作品 ${variant.label}`;
    const condition = document.createElement('span');
    condition.className = 'condition';
    condition.textContent = revealed ? variant.condition : '';
    const link = document.createElement('a');
    link.className = 'open-link';
    link.href = variant.path;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = '单独查看';
    header.append(heading, condition, link);
    const viewport = document.createElement('div');
    viewport.className = 'viewport';
    const iframe = document.createElement('iframe');
    iframe.title = `午夜植物园海报 ${variant.label}`;
    iframe.src = variant.path;
    iframe.sandbox = 'allow-scripts allow-same-origin allow-downloads';
    viewport.append(iframe);
    article.append(header, viewport);
    results.append(article);
  });
  observer = new ResizeObserver(updateScale);
  document.querySelectorAll('.viewport').forEach((element) => observer.observe(element));
  updateScale();
}
document.querySelectorAll('[data-size]').forEach((button) => button.addEventListener('click', () => {
  size = button.dataset.size;
  document.querySelectorAll('[data-size]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
  updateScale();
}));
document.getElementById('reveal').addEventListener('click', (event) => {
  revealed = !revealed;
  event.currentTarget.textContent = revealed ? '隐藏技能条件' : '揭示技能条件';
  document.querySelectorAll('.condition').forEach((element, index) => {
    element.textContent = revealed ? variants[index].condition : '';
  });
});
document.querySelectorAll('[data-preference]').forEach((button) => button.addEventListener('click', () => {
  preference = button.dataset.preference;
  document.querySelectorAll('[data-preference]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
  status.textContent = '';
}));
document.getElementById('export').addEventListener('click', () => {
  const feedback = {task: 'canvas-poster', requested_intelligence: 'medium', preference, notes: notes.value,
    conditions_revealed_at_export: revealed, recorded_at: new Date().toISOString()};
  const url = URL.createObjectURL(new Blob([JSON.stringify(feedback, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'canvas-poster-feedback.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  status.textContent = '已导出；反馈不会自动发送到对话。';
});
render();
