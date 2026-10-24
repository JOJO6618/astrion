/**
 * Papertrail 论文阅读工作台 - 改版脚本
 * 支持搜索、筛选、详情查看、状态管理
 */

'use strict';

// 状态常量
const STATUS = {
  all: 'all',
  unread: 'unread',
  reading: 'reading',
  read: 'read'
};

const STATUS_NAMES = {
  unread: '未读',
  reading: '在读',
  read: '已读'
};

// 应用状态
const state = {
  papers: [],
  selectedStatus: STATUS.all,
  selectedTopic: null,
  query: '',
  detailId: null,
  openDropdownId: null
};

// DOM 引用
const els = {};

// 初始化
function init() {
  cacheElements();
  bindEvents();
  render();
}

// 缓存常用 DOM 元素
function cacheElements() {
  els.stats = document.getElementById('stats');
  els.papers = document.getElementById('papers');
  els.empty = document.getElementById('empty');
  els.searchInput = document.getElementById('search-input');
  els.detailOverlay = document.getElementById('detail-overlay');
  els.detailPanel = document.getElementById('detail-panel');
  els.sidebarAll = document.getElementById('sidebar-all');
  els.sidebarTopics = document.getElementById('sidebar-topics');
}

// 事件绑定
function bindEvents() {
  // 搜索
  els.searchInput.addEventListener('input', handleSearch);

  // 状态筛选按钮
  document.querySelectorAll('[data-status]').forEach(btn => {
    btn.addEventListener('click', () => handleStatusFilter(btn.dataset.status));
  });

  // 论文列表点击代理
  document.getElementById('papers').addEventListener('click', handlePapersClick);

  // 全局点击（关闭下拉菜单）
  document.addEventListener('click', handleGlobalClick);

  // 详情关闭
  document.getElementById('close-detail').addEventListener('click', closeDetail);

  // 详情遮罩点击关闭
  els.detailOverlay.addEventListener('click', (e) => {
    if (e.target === els.detailOverlay) closeDetail();
  });

  // 键盘导航
  document.addEventListener('keydown', handleKeyboard);
}

// 搜索处理
function handleSearch(e) {
  state.query = e.target.value.trim().toLowerCase();
  render();
}

// 状态筛选处理
function handleStatusFilter(status) {
  state.selectedStatus = status;
  
  // 更新按钮状态
  document.querySelectorAll('[data-status]').forEach(btn => {
    btn.classList.toggle('is-active', btn.dataset.status === status);
  });
  
  render();
}

// 论文列表点击处理
function handlePapersClick(e) {
  // 详情按钮
  const detailBtn = e.target.closest('[data-detail]');
  if (detailBtn) {
    openDetail(detailBtn.dataset.detail);
    return;
  }

  // 状态切换按钮
  const statusBtn = e.target.closest('.btn-status');
  if (statusBtn) {
    e.stopPropagation();
    const paperId = statusBtn.dataset.paperId;
    
    if (state.openDropdownId === paperId) {
      closeAllDropdowns();
    } else {
      closeAllDropdowns();
      statusBtn.classList.add('is-open');
      state.openDropdownId = paperId;
    }
    return;
  }

  // 状态选项
  const statusOption = e.target.closest('[data-set-status]');
  if (statusOption) {
    const paperId = statusOption.closest('.btn-status').dataset.paperId;
    const newStatus = statusOption.dataset.setStatus;
    setPaperStatus(paperId, newStatus);
    closeAllDropdowns();
    return;
  }
}

// 全局点击处理（关闭下拉菜单）
function handleGlobalClick(e) {
  if (!e.target.closest('.btn-status')) {
    closeAllDropdowns();
  }
}

// 关闭所有下拉菜单
function closeAllDropdowns() {
  document.querySelectorAll('.btn-status.is-open').forEach(btn => {
    btn.classList.remove('is-open');
  });
  state.openDropdownId = null;
}

// 设置论文状态
function setPaperStatus(paperId, newStatus) {
  const paper = state.papers.find(p => p.id === paperId);
  if (paper) {
    paper.status = newStatus;
    render();
    
    // 如果详情面板打开同一篇论文，更新详情
    if (state.detailId === paperId) {
      openDetail(paperId);
    }
  }
}

// 打开详情
function openDetail(paperId) {
  const paper = state.papers.find(p => p.id === paperId);
  if (!paper) return;

  state.detailId = paperId;
  
  const statusOptions = Object.entries(STATUS_NAMES)
    .map(([key, name]) => 
      `<button class="btn-status__option${paper.status === key ? ' is-current' : ''}" data-set-status="${key}">${name}</button>`
    ).join('');

  els.detailPanel.innerHTML = `
    <div class="detail-panel__header">
      <h2 class="detail-panel__title">${paper.title}</h2>
      <button id="close-detail" class="detail-panel__close" aria-label="关闭">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M18 6L6 18M6 6l12 12"/>
        </svg>
      </button>
    </div>
    <div class="detail-panel__body">
      <div class="detail-panel__meta">
        <span class="paper__status paper__status--${paper.status}">${STATUS_NAMES[paper.status]}</span>
        <span class="detail-panel__authors">${paper.authors}</span>
        <span class="detail-panel__year">${paper.year} · ${paper.venue} · ${paper.pages}页</span>
      </div>
      <div class="detail-panel__section">
        <h3 class="detail-panel__section-title">摘要</h3>
        <p class="detail-panel__abstract">${paper.abstract}</p>
      </div>
      <div class="detail-panel__section">
        <h3 class="detail-panel__section-title">我的笔记</h3>
        <div class="detail-panel__note${paper.note ? '' : ' detail-panel__note--empty'}">
          ${paper.note || '暂无笔记'}
        </div>
      </div>
    </div>
    <div class="detail-panel__footer">
      <div class="btn-status" data-paper-id="${paper.id}">
        <button class="btn" type="button">
          ${STATUS_NAMES[paper.status]}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M6 9l6 6 6-6"/>
          </svg>
        </button>
        <div class="btn-status__dropdown">
          ${statusOptions}
        </div>
      </div>
    </div>
  `;
  
  // 重新绑定关闭按钮
  document.getElementById('close-detail').addEventListener('click', closeDetail);
  
  // 绑定状态选项
  els.detailPanel.querySelectorAll('[data-set-status]').forEach(opt => {
    opt.addEventListener('click', () => {
      setPaperStatus(paperId, opt.dataset.setStatus);
      closeDetail();
    });
  });

  els.detailOverlay.classList.add('is-open');
  document.body.style.overflow = 'hidden';

  // 聚焦到关闭按钮
  document.getElementById('close-detail').focus();
}

// 关闭详情
function closeDetail() {
  state.detailId = null;
  els.detailOverlay.classList.remove('is-open');
  document.body.style.overflow = '';
}

// 键盘处理
function handleKeyboard(e) {
  // ESC 关闭详情或下拉
  if (e.key === 'Escape') {
    if (state.openDropdownId) {
      closeAllDropdowns();
    } else if (state.detailId) {
      closeDetail();
    }
  }

  // '/' 聚焦搜索
  if (e.key === '/' && !isInputFocused()) {
    e.preventDefault();
    els.searchInput.focus();
  }
}

// 检查焦点是否在输入框
function isInputFocused() {
  const active = document.activeElement;
  return active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA');
}

// 过滤论文
function filterPapers() {
  return state.papers.filter(paper => {
    // 状态过滤
    if (state.selectedStatus !== STATUS.all && paper.status !== state.selectedStatus) {
      return false;
    }
    
    // 主题过滤
    if (state.selectedTopic && paper.topic !== state.selectedTopic) {
      return false;
    }
    
    // 搜索过滤
    if (state.query) {
      const searchable = `${paper.title} ${paper.authors}`.toLowerCase();
      if (!searchable.includes(state.query)) {
        return false;
      }
    }
    
    return true;
  });
}

// 渲染统计
function renderStats() {
  const total = state.papers.length;
  const unread = state.papers.filter(p => p.status === STATUS.unread).length;
  const reading = state.papers.filter(p => p.status === STATUS.reading).length;
  const read = state.papers.filter(p => p.status === STATUS.read).length;

  els.stats.innerHTML = `
    <div class="stat-card">
      <span class="stat-card__dot stat-card__dot--all"></span>
      <div>
        <div class="stat-card__value">${total}</div>
        <div class="stat-card__label">全部论文</div>
      </div>
    </div>
    <div class="stat-card">
      <span class="stat-card__dot stat-card__dot--reading"></span>
      <div>
        <div class="stat-card__value">${reading}</div>
        <div class="stat-card__label">在读</div>
      </div>
    </div>
    <div class="stat-card">
      <span class="stat-card__dot stat-card__dot--read"></span>
      <div>
        <div class="stat-card__value">${read}</div>
        <div class="stat-card__label">已读</div>
      </div>
    </div>
  `;
}

// 渲染侧边栏
function renderSidebar() {
  // 统计各主题数量
  const topicCounts = {};
  state.papers.forEach(p => {
    topicCounts[p.topic] = (topicCounts[p.topic] || 0) + 1;
  });

  // 渲染主题列表
  const topicItems = Object.entries(topicCounts).map(([topic, count]) => `
    <li class="sidebar__item${state.selectedTopic === topic ? ' is-active' : ''}" 
        data-topic="${topic}" role="button" tabindex="0">
      <span>${topic}</span>
      <span class="sidebar__count">${count}</span>
    </li>
  `).join('');

  els.sidebarTopics.innerHTML = topicItems;

  // 绑定主题点击
  els.sidebarTopics.querySelectorAll('[data-topic]').forEach(item => {
    item.addEventListener('click', () => handleTopicFilter(item.dataset.topic));
    item.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handleTopicFilter(item.dataset.topic);
      }
    });
  });

  // 全部分类
  els.sidebarAll.addEventListener('click', () => handleTopicFilter(null));
}

// 处理主题筛选
function handleTopicFilter(topic) {
  state.selectedTopic = topic;
  
  // 更新侧边栏高亮
  els.sidebarTopics.querySelectorAll('[data-topic]').forEach(item => {
    item.classList.toggle('is-active', item.dataset.topic === topic);
  });
  els.sidebarAll.classList.toggle('is-active', !topic);
  
  render();
}

// 渲染论文列表
function renderPapers() {
  const filtered = filterPapers();

  if (filtered.length === 0) {
    els.papers.innerHTML = '';
    els.empty.style.display = 'block';
    return;
  }

  els.empty.style.display = 'none';

  const statusOptions = Object.entries(STATUS_NAMES)
    .map(([key, name]) => `<button class="btn-status__option${''}" data-set-status="${key}">${name}</button>`)
    .join('');

  els.papers.innerHTML = filtered.map(paper => `
    <article class="paper" tabindex="0">
      <div class="paper__header">
        <span class="paper__topic">${paper.topic}</span>
        <span class="paper__status paper__status--${paper.status}">${STATUS_NAMES[paper.status]}</span>
      </div>
      <h3 class="paper__title">${paper.title}</h3>
      <p class="paper__meta">
        <span class="paper__meta-item">${paper.authors}</span>
        <span class="paper__meta-item">${paper.year}</span>
        <span class="paper__meta-item">${paper.venue}</span>
      </p>
      <p class="paper__abstract">${paper.abstract}</p>
      <div class="paper__actions">
        <button class="btn" data-detail="${paper.id}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
            <circle cx="12" cy="12" r="3"/>
          </svg>
          详情
        </button>
        <div class="btn-status" data-paper-id="${paper.id}">
          <button class="btn" type="button">
            ${STATUS_NAMES[paper.status]}
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M6 9l6 6 6-6"/>
            </svg>
          </button>
          <div class="btn-status__dropdown">
            ${Object.entries(STATUS_NAMES).map(([key, name]) => 
              `<button class="btn-status__option${paper.status === key ? ' is-current' : ''}" data-set-status="${key}">${name}</button>`
            ).join('')}
          </div>
        </div>
      </div>
    </article>
  `).join('');
}

// 主渲染函数
function render() {
  renderStats();
  renderSidebar();
  renderPapers();
}

// 数据加载后初始化
document.addEventListener('DOMContentLoaded', () => {
  // 等待 data.js 加载
  if (typeof window.PAPERS !== 'undefined') {
    state.papers = window.PAPERS.map(paper => ({...paper}));
    init();
  } else {
    // 备用：等待脚本加载
    setTimeout(() => {
      if (typeof window.PAPERS !== 'undefined') {
        state.papers = window.PAPERS.map(paper => ({...paper}));
        init();
      }
    }, 100);
  }
});
