'use strict';

// 状态名称映射
const STATUS_NAMES = {
  unread: '未读',
  reading: '在读',
  read: '已读'
};

// 状态切换映射（循环切换）
const STATUS_CYCLE = {
  unread: 'reading',
  reading: 'read',
  read: 'unread'
};

// 状态颜色类
const STATUS_CLASS = {
  unread: 'unread',
  reading: 'reading',
  read: 'read'
};

// 全局状态
let currentTopic = 'all';
let currentStatus = 'all';
let searchQuery = '';
let detailPaperId = null;
let lastFocusedElement = null;

// 初始化
document.addEventListener('DOMContentLoaded', () => {
  initEventListeners();
  updateCounts();
  renderPapers();
});

// 绑定事件监听
function initEventListeners() {
  // 分类按钮
  document.querySelectorAll('.category-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const topic = btn.dataset.topic;
      setTopic(topic);
    });
  });

  // 筛选按钮
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const status = btn.dataset.status;
      setStatus(status);
    });
  });

  // 搜索输入
  const searchInput = document.getElementById('search-input');
  const clearBtn = document.getElementById('clear-search');
  
  searchInput.addEventListener('input', (e) => {
    searchQuery = e.target.value.trim().toLowerCase();
    clearBtn.hidden = !searchQuery;
    renderPapers();
  });

  clearBtn.addEventListener('click', () => {
    searchInput.value = '';
    searchQuery = '';
    clearBtn.hidden = true;
    renderPapers();
    searchInput.focus();
  });

  // 点击论文卡片区域（委托）
  document.getElementById('papers-grid').addEventListener('click', handlePaperAction);

  // 模态框
  document.getElementById('modal-close').addEventListener('click', closeDetail);
  document.getElementById('modal-toggle-status').addEventListener('click', toggleDetailStatus);
  
  // 点击遮罩关闭
  document.getElementById('modal-overlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
      closeDetail();
    }
  });

  // 键盘导航
  document.addEventListener('keydown', handleKeyboard);
}

// 设置分类
function setTopic(topic) {
  currentTopic = topic;
  
  document.querySelectorAll('.category-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.topic === topic);
  });
  
  renderPapers();
}

// 设置筛选状态
function setStatus(status) {
  currentStatus = status;
  
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.status === status);
  });
  
  renderPapers();
}

// 更新所有计数
function updateCounts() {
  const papers = window.PAPERS;
  
  // 各状态计数
  const statusCounts = {
    all: papers.length,
    unread: papers.filter(p => p.status === 'unread').length,
    reading: papers.filter(p => p.status === 'reading').length,
    read: papers.filter(p => p.status === 'read').length
  };
  
  // 各分类计数
  const topicCounts = {
    all: papers.length,
    '视觉与图形': papers.filter(p => p.topic === '视觉与图形').length,
    '人机交互': papers.filter(p => p.topic === '人机交互').length,
    '科研方法': papers.filter(p => p.topic === '科研方法').length
  };
  
  // 更新头部统计
  const headerStats = document.getElementById('header-stats');
  headerStats.innerHTML = `
    <div class="header-stat"><strong>${statusCounts.all}</strong> 篇论文</div>
    <div class="header-stat"><strong>${statusCounts.reading}</strong> 在读</div>
    <div class="header-stat"><strong>${statusCounts.read}</strong> 已读</div>
  `;
  
  // 更新分类计数
  Object.keys(topicCounts).forEach(topic => {
    const el = document.getElementById(`count-${topic}`);
    if (el) el.textContent = topicCounts[topic];
  });
  
  // 更新侧边栏状态统计
  document.getElementById('stat-unread').textContent = statusCounts.unread;
  document.getElementById('stat-reading').textContent = statusCounts.reading;
  document.getElementById('stat-read').textContent = statusCounts.read;
  
  // 更新筛选按钮计数
  Object.keys(statusCounts).forEach(status => {
    const el = document.getElementById(`filter-${status}`);
    if (el) el.textContent = statusCounts[status];
  });
}

// 筛选论文
function filterPapers() {
  return window.PAPERS.filter(paper => {
    const matchTopic = currentTopic === 'all' || paper.topic === currentTopic;
    const matchStatus = currentStatus === 'all' || paper.status === currentStatus;
    const matchSearch = !searchQuery || 
      paper.title.toLowerCase().includes(searchQuery) || 
      paper.authors.toLowerCase().includes(searchQuery);
    
    return matchTopic && matchStatus && matchSearch;
  });
}

// 渲染论文列表
function renderPapers() {
  const grid = document.getElementById('papers-grid');
  const emptyState = document.getElementById('empty-state');
  const filtered = filterPapers();
  
  if (filtered.length === 0) {
    grid.innerHTML = '';
    emptyState.hidden = false;
    return;
  }
  
  emptyState.hidden = true;
  
  grid.innerHTML = filtered.map(paper => `
    <article class="paper-card" tabindex="0" data-paper-id="${paper.id}">
      <div class="paper-header">
        <span class="paper-topic">${paper.topic}</span>
        <span class="paper-status ${STATUS_CLASS[paper.status]}">${STATUS_NAMES[paper.status]}</span>
      </div>
      <h3 class="paper-title">${paper.title}</h3>
      <div class="paper-meta">
        <span>${paper.authors}</span>
        <span>${paper.year}</span>
        <span>${paper.venue}</span>
        <span>${paper.pages}页</span>
      </div>
      <p class="paper-abstract">${paper.abstract}</p>
      <div class="paper-actions">
        <button class="btn btn-primary" data-action="detail" data-id="${paper.id}">查看详情</button>
        <button class="btn btn-status" data-action="toggle" data-id="${paper.id}">
          ${STATUS_NAMES[STATUS_CYCLE[paper.status]]}
        </button>
      </div>
    </article>
  `).join('');
}

// 处理论文卡片操作
function handlePaperAction(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  
  const action = btn.dataset.action;
  const paperId = btn.dataset.id;
  
  if (action === 'detail') {
    openDetail(paperId);
  } else if (action === 'toggle') {
    togglePaperStatus(paperId);
  }
}

// 切换论文状态
function togglePaperStatus(paperId) {
  const paper = window.PAPERS.find(p => p.id === paperId);
  if (!paper) return;
  
  paper.status = STATUS_CYCLE[paper.status];
  
  updateCounts();
  renderPapers();
  
  // 如果详情面板打开的是同一篇论文，更新详情
  if (detailPaperId === paperId) {
    updateDetailPanel(paper);
  }
}

// 打开详情
function openDetail(paperId) {
  const paper = window.PAPERS.find(p => p.id === paperId);
  if (!paper) return;
  
  detailPaperId = paperId;
  lastFocusedElement = document.activeElement;
  
  updateDetailPanel(paper);
  
  document.getElementById('modal-overlay').hidden = false;
  document.getElementById('modal-overlay').setAttribute('aria-hidden', 'false');
  
  // 聚焦到关闭按钮
  document.getElementById('modal-close').focus();
  
  // 防止背景滚动
  document.body.style.overflow = 'hidden';
}

// 更新详情面板内容
function updateDetailPanel(paper) {
  document.getElementById('modal-topic').textContent = paper.topic;
  
  const statusEl = document.getElementById('modal-status');
  statusEl.textContent = STATUS_NAMES[paper.status];
  statusEl.className = `modal-status ${STATUS_CLASS[paper.status]}`;
  
  document.getElementById('modal-title').textContent = paper.title;
  
  document.getElementById('modal-meta').innerHTML = `
    <span>${paper.authors}</span>
    <span>${paper.year}</span>
    <span>${paper.venue}</span>
    <span>${paper.pages}页</span>
  `;
  
  document.getElementById('modal-abstract').textContent = paper.abstract;
  
  const noteEl = document.getElementById('modal-note');
  noteEl.textContent = paper.note || '';
  
  document.getElementById('modal-toggle-status').textContent = 
    `切换为${STATUS_NAMES[STATUS_CYCLE[paper.status]]}`;
}

// 关闭详情
function closeDetail() {
  document.getElementById('modal-overlay').hidden = true;
  document.getElementById('modal-overlay').setAttribute('aria-hidden', 'true');
  
  detailPaperId = null;
  
  // 恢复背景滚动
  document.body.style.overflow = '';
  
  // 恢复焦点
  if (lastFocusedElement) {
    lastFocusedElement.focus();
  }
}

// 详情面板内切换状态
function toggleDetailStatus() {
  if (!detailPaperId) return;
  
  togglePaperStatus(detailPaperId);
  
  // 更新详情面板
  const paper = window.PAPERS.find(p => p.id === detailPaperId);
  if (paper) {
    updateDetailPanel(paper);
  }
}

// 键盘导航处理
function handleKeyboard(e) {
  // ESC 关闭详情
  if (e.key === 'Escape' && detailPaperId) {
    closeDetail();
    return;
  }
  
  // Tab 键管理（在模态框内循环）
  if (detailPaperId && e.key === 'Tab') {
    const modal = document.getElementById('modal');
    const focusableElements = modal.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    const firstEl = focusableElements[0];
    const lastEl = focusableElements[focusableElements.length - 1];
    
    if (e.shiftKey && document.activeElement === firstEl) {
      e.preventDefault();
      lastEl.focus();
    } else if (!e.shiftKey && document.activeElement === lastEl) {
      e.preventDefault();
      firstEl.focus();
    }
  }
}
