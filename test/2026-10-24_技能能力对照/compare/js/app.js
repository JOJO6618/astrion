'use strict';
const groups = {
  art: {
    description: '为“潮汐档案”艺术展创作可交互的生成艺术。比较主题表达、构图、细节与交互。',
    variants: [
      {label: 'A', path: '../runs/art-south/index.html', condition: '未阅读技能'},
      {label: 'B', path: '../runs/art-north/index.html', condition: '阅读 algorithmic-art'},
    ],
  },
  ui: {
    description: '基于相同论文数据和粗糙输入页面，评审并改版阅读工作台。比较层级、查找效率、交互与细节。',
    variants: [
      {label: 'A', path: '../runs/ui-west/index.html', condition: '阅读 impeccable'},
      {label: 'B', path: '../runs/ui-east/index.html', condition: '未阅读技能'},
    ],
  },
};
let group = 'art';
let size = 'desktop';
let revealed = false;
const feedback = {art: {preference: '', notes: ''}, ui: {preference: '', notes: ''}};
const results = document.getElementById('results');
const notes = document.getElementById('notes');
const saveStatus = document.getElementById('save-status');
let observer;
function updateScale() {
  const width = size === 'desktop' ? 1440 : 390;
  const height = size === 'desktop' ? 1000 : 844;
  document.querySelectorAll('.viewport').forEach((viewport) => {
    const available = viewport.clientWidth;
    const scale = Math.min(1, available / width);
    const iframe = viewport.querySelector('iframe');
    iframe.style.width = `${width}px`;
    iframe.style.height = `${height}px`;
    iframe.style.transform = `scale(${scale})`;
    viewport.style.height = `${Math.ceil(height * scale)}px`;
    if (available > width) iframe.style.marginInline = `${(available - width) / 2}px`;
    else iframe.style.marginInline = '0';
  });
}
function render() {
  if (observer) observer.disconnect();
  document.getElementById('task-description').textContent = groups[group].description;
  results.replaceChildren();
  groups[group].variants.forEach((variant) => {
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
    iframe.title = `${group === 'art' ? '潮汐档案' : '论文阅读工作台'} ${variant.label}`;
    iframe.src = variant.path;
    // The test server has its own origin; retain local storage and module support.
    iframe.sandbox = 'allow-scripts allow-same-origin allow-downloads';
    viewport.append(iframe);
    article.append(header, viewport);
    results.append(article);
  });
  document.querySelectorAll('[data-group]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.group === group)));
  document.querySelectorAll('[data-preference]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.preference === feedback[group].preference)));
  notes.value = feedback[group].notes;
  observer = new ResizeObserver(updateScale);
  document.querySelectorAll('.viewport').forEach((element) => observer.observe(element));
  updateScale();
}
document.querySelectorAll('[data-group]').forEach((button) => button.addEventListener('click', () => {
  feedback[group].notes = notes.value;
  group = button.dataset.group;
  saveStatus.textContent = '';
  render();
}));
document.querySelectorAll('[data-size]').forEach((button) => button.addEventListener('click', () => {
  size = button.dataset.size;
  document.querySelectorAll('[data-size]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
  updateScale();
}));
document.getElementById('reveal').addEventListener('click', (event) => {
  revealed = !revealed;
  event.currentTarget.textContent = revealed ? '隐藏技能条件' : '揭示技能条件';
  document.querySelectorAll('.condition').forEach((element, index) => {
    element.textContent = revealed ? groups[group].variants[index].condition : '';
  });
});
document.querySelectorAll('[data-preference]').forEach((button) => button.addEventListener('click', () => {
  feedback[group].preference = button.dataset.preference;
  document.querySelectorAll('[data-preference]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
  saveStatus.textContent = '已记录在当前页面；可导出两组反馈。';
}));
notes.addEventListener('input', () => { feedback[group].notes = notes.value; });
document.getElementById('export').addEventListener('click', () => {
  feedback[group].notes = notes.value;
  const body = JSON.stringify({experiment: '2026-10-24-skill-comparison', revealed, feedback}, null, 2);
  const url = URL.createObjectURL(new Blob([body], {type: 'application/json'}));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'skill-comparison-feedback.json';
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  saveStatus.textContent = '已导出反馈；刷新页面不会保留未导出的记录。';
});
render();
