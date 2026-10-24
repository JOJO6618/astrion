'use strict';
const papers = window.PAPERS.map((paper) => ({...paper}));
let selectedStatus = 'all';
let query = '';
let detailId = null;
const statusNames = {unread:'未读',reading:'在读',read:'已读'};
function render() {
  const read = papers.filter((paper) => paper.status === 'read').length;
  const reading = papers.filter((paper) => paper.status === 'reading').length;
  document.getElementById('stats').innerHTML = `<div>全部 ${papers.length}</div><div>在读 ${reading}</div><div>已读 ${read}</div>`;
  const filtered = papers.filter((paper) => (selectedStatus === 'all' || paper.status === selectedStatus) && `${paper.title} ${paper.authors}`.toLowerCase().includes(query));
  document.getElementById('papers').innerHTML = filtered.map((paper) => `<article class="paper"><span class="tag">${paper.topic}</span><span class="tag">${statusNames[paper.status]}</span><h3>${paper.title}</h3><p class="meta">${paper.authors} / ${paper.year} / ${paper.venue} / ${paper.pages} 页</p><p class="abstract">${paper.abstract}</p><div class="actions"><button data-detail="${paper.id}">详情</button><button data-read="${paper.id}">${paper.status === 'read' ? '标为未读' : '标为已读'}</button></div></article>`).join('');
  document.getElementById('empty').hidden = filtered.length !== 0;
}
function detail(id) {
  detailId = id;
  const paper = papers.find((item) => item.id === id);
  document.getElementById('detail-content').innerHTML = `<h2>${paper.title}</h2><p>${paper.authors} · ${paper.year} · ${statusNames[paper.status]}</p><p>${paper.abstract}</p><h3>我的笔记</h3><p>${paper.note || '暂无笔记'}</p><button data-read="${id}">${paper.status === 'read' ? '标为未读' : '标为已读'}</button>`;
  document.getElementById('detail').hidden = false;
}
document.getElementById('search').addEventListener('input', (event) => {query = event.target.value.trim().toLowerCase(); render();});
document.querySelectorAll('[data-status]').forEach((button) => button.addEventListener('click', () => {
  selectedStatus = button.dataset.status;
  document.querySelectorAll('[data-status]').forEach((item) => item.classList.toggle('active', item === button));
  render();
}));
document.addEventListener('click', (event) => {
  const detailButton = event.target.closest('[data-detail]');
  if (detailButton) detail(detailButton.dataset.detail);
  const readButton = event.target.closest('[data-read]');
  if (readButton) {
    const paper = papers.find((item) => item.id === readButton.dataset.read);
    paper.status = paper.status === 'read' ? 'unread' : 'read';
    render();
    if (detailId) detail(detailId);
  }
});
document.getElementById('close-detail').addEventListener('click', () => {detailId = null; document.getElementById('detail').hidden = true;});
render();
