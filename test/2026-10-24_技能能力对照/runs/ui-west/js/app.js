'use strict';

(function () {
  var DATA = window.PAPERTRAIL_DATA;
  var core = window.PapertrailCore;

  if (!DATA || !core) return;

  var papers = DATA.papers.map(function (paper) { return Object.assign({}, paper); });
  var state = {
    query: '',
    status: 'all',
    topic: 'all',
    sort: 'year-desc',
    selectedId: null
  };

  var TOPIC_CLASS = {
    '视觉与图形': 'viz',
    '人机交互': 'hci',
    '科研方法': 'method'
  };

  var els = {
    shell: document.getElementById('shell'),
    workspace: document.getElementById('workspace'),
    paperList: document.getElementById('paper-list'),
    emptyState: document.getElementById('empty-state'),
    emptyCopy: document.getElementById('empty-copy'),
    emptyClearAll: document.getElementById('empty-clear-all'),
    emptyClearSearch: document.getElementById('empty-clear-search'),
    statusFilter: document.getElementById('status-filter'),
    topicList: document.getElementById('topic-list'),
    search: document.getElementById('search'),
    searchClear: document.getElementById('search-clear'),
    sort: document.getElementById('sort'),
    resultText: document.getElementById('result-text'),
    clearFilters: document.getElementById('clear-filters'),
    progressLabel: document.getElementById('progress-label'),
    progressTrack: document.getElementById('progress-track'),
    listHeading: document.getElementById('list-heading'),
    detailPane: document.getElementById('detail-pane'),
    detailContent: document.getElementById('detail-content'),
    live: document.getElementById('live')
  };

  var announceTimer = 0;

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (character) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[character];
    });
  }

  function statusLabel(status) {
    return core.STATUS_LABELS[status] || status;
  }

  function topicClass(topic) {
    return TOPIC_CLASS[topic] || 'viz';
  }

  function byId(id) {
    return papers.find(function (paper) { return paper.id === id; }) || null;
  }

  function getVisible() {
    return core.sortPapers(core.filterPapers(papers, state), state.sort);
  }

  function hasFilters() {
    return Boolean(state.query.trim()) || state.status !== 'all' || state.topic !== 'all';
  }

  function noteIcon() {
    return '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
      '<path d="M3 11.6V13h1.4l7-7-1.4-1.4-7 7Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"></path>' +
      '<path d="m9.3 5.3 1.4 1.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"></path>' +
      '</svg>';
  }

  function stampMarkup(status) {
    var check = status === 'read'
      ? '<svg viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path d="m2.5 6.3 2.1 2.1 4.9-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"></path></svg>'
      : '';
    return '<span class="stamp stamp--' + status + '">' + check + statusLabel(status) + '</span>';
  }

  function topicTagMarkup(topic) {
    return '<span class="topic-tag topic-tag--' + topicClass(topic) + '">' +
      '<span class="topic-tag__dot" aria-hidden="true"></span>' + escapeHtml(topic) +
      '</span>';
  }

  function rowMarkup(paper, selected) {
    var id = escapeHtml(paper.id);
    var readAction = paper.status === 'read' ? '标为未读' : '标为已读';
    var selectedAttr = selected ? ' aria-current="true"' : '';
    return '<li class="paper-row' + (selected ? ' is-selected' : '') + '" data-row-id="' + id + '">' +
      '<a class="paper-row__open" href="#' + id + '" data-open="' + id + '"' + selectedAttr +
      ' aria-label="打开论文：' + escapeHtml(paper.title) + '，' + escapeHtml(paper.authors) + '，' + statusLabel(paper.status) + '">' +
        '<span class="paper-row__flags">' +
          stampMarkup(paper.status) +
          topicTagMarkup(paper.topic) +
          (paper.note ? '<span class="note-flag">' + noteIcon() + '<span>笔记</span></span>' : '') +
        '</span>' +
        '<span class="paper-row__title">' + escapeHtml(paper.title) + '</span>' +
        '<span class="paper-row__meta">' + escapeHtml(paper.authors) + ' · ' + paper.year + ' · ' +
          escapeHtml(paper.venue) + ' · ' + paper.pages + ' 页</span>' +
        '<span class="paper-row__abstract">' + escapeHtml(paper.abstract) + '</span>' +
        '<span class="paper-row__cta" aria-hidden="true">打开详情 ' +
          '<svg viewBox="0 0 14 14" focusable="false"><path d="M3 7h8M7.5 3.5 11 7l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"></path></svg>' +
        '</span>' +
      '</a>' +
      '<div class="paper-row__side">' +
        '<button class="button button--quiet button--small" type="button" data-toggle="' + id + '"' +
        ' aria-label="' + readAction + '：' + escapeHtml(paper.title) + '">' + readAction + '</button>' +
      '</div>' +
    '</li>';
  }

  function detailMarkup(paper, inResults) {
    var notice = '';
    if (!inResults) {
      var reason = state.status !== 'all'
        ? '「' + statusLabel(state.status) + '」状态筛选'
        : (state.topic !== 'all' ? '「' + escapeHtml(state.topic) + '」主题筛选' : '当前搜索');
      notice = '<div class="status-notice">' +
        '<p>这篇论文当前为「' + statusLabel(paper.status) + '」，不在' + reason + '的结果中。</p>' +
        '<button class="link-button" type="button" data-clear-status>显示全部状态</button>' +
        '</div>';
    }

    var statusButtons = core.STATUSES.map(function (status) {
      var active = paper.status === status;
      return '<button class="segmented__item' + (active ? ' is-active' : '') + '" type="button"' +
        ' data-set-status="' + status + '" aria-pressed="' + String(active) + '">' +
        statusLabel(status) + '</button>';
    }).join('');

    return '<article class="paper-detail" data-detail-id="' + escapeHtml(paper.id) + '">' +
      '<div class="detail-bar">' +
        '<button class="detail-close" type="button" data-close-detail aria-label="关闭详情并返回论文列表">' +
          '<svg class="detail-close__arrow" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M13 8H3M7 4 3 8l4 4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path></svg>' +
          '<span class="detail-close__text detail-close__text--mobile">返回列表</span>' +
          '<span class="detail-close__text detail-close__text--desktop">关闭</span>' +
          '<svg class="detail-close__x" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path></svg>' +
        '</button>' +
      '</div>' +
      '<div class="detail-flags">' +
        stampMarkup(paper.status) +
        topicTagMarkup(paper.topic) +
        (paper.note ? '<span class="note-flag">' + noteIcon() + '<span>有笔记</span></span>' : '') +
      '</div>' +
      '<h2 class="detail-title" id="detail-title" tabindex="-1">' + escapeHtml(paper.title) + '</h2>' +
      '<p class="detail-authors">' + escapeHtml(paper.authors) + '</p>' +
      '<dl class="detail-meta">' +
        '<div><dt>年份</dt><dd>' + paper.year + '</dd></div>' +
        '<div><dt>出处</dt><dd>' + escapeHtml(paper.venue) + '</dd></div>' +
        '<div><dt>篇幅</dt><dd>' + paper.pages + ' 页</dd></div>' +
      '</dl>' +
      '<div class="detail-status">' +
        '<span class="field-label" id="detail-status-label">阅读状态</span>' +
        '<div class="segmented segmented--status" role="group" aria-labelledby="detail-status-label">' +
          statusButtons +
        '</div>' +
      '</div>' +
      notice +
      '<section class="detail-section">' +
        '<h3>摘要</h3>' +
        '<p>' + escapeHtml(paper.abstract) + '</p>' +
      '</section>' +
      '<section class="detail-section detail-section--note">' +
        '<h3>我的笔记</h3>' +
        (paper.note ? '<p>' + escapeHtml(paper.note) + '</p>' : '<p class="is-empty">暂无笔记</p>') +
      '</section>' +
      '<footer class="detail-foot">演示记录 <span class="record-id">' + escapeHtml(paper.id) + '</span> · 虚构演示资料，非真实文献。</footer>' +
      '</article>';
  }

  function workbenchMarkup() {
    var pick = core.continuePaper(papers);
    var label = pick
      ? (pick.status === 'reading' ? '接着读' : (pick.status === 'unread' ? '下一篇未读' : '回看一篇'))
      : '';
    var suggestion = '';
    if (pick) {
      suggestion = '<section class="continue-block" aria-labelledby="continue-label">' +
        '<h3 class="continue-label" id="continue-label">' + label + '</h3>' +
        '<div class="continue-flags">' + stampMarkup(pick.status) + topicTagMarkup(pick.topic) + '</div>' +
        '<p class="continue-title">' + escapeHtml(pick.title) + '</p>' +
        '<p class="continue-note">' + (pick.note ? '笔记：' + escapeHtml(pick.note) : '这篇还没有笔记。') + '</p>' +
        '<button class="button button--primary" type="button" data-open="' + escapeHtml(pick.id) + '">打开这篇</button>' +
        '</section>';
    }

    return '<div class="workbench">' +
      '<h2 class="workbench-title">阅读台</h2>' +
      '<p class="workbench-lede">从列表打开论文，或接着上次的阅读。更改阅读状态后，列表、计数与详情会立即同步。</p>' +
      suggestion +
      '<section class="shortcuts" aria-labelledby="shortcuts-title">' +
        '<h3 id="shortcuts-title">键盘操作</h3>' +
        '<dl class="kbd-list">' +
          '<div><dt><kbd>/</kbd></dt><dd>聚焦搜索</dd></div>' +
          '<div><dt><kbd>J</kbd> <kbd>K</kbd></dt><dd>下一篇 / 上一篇</dd></div>' +
          '<div><dt><kbd>Enter</kbd></dt><dd>打开当前论文</dd></div>' +
          '<div><dt><kbd>R</kbd></dt><dd>标为已读 / 未读</dd></div>' +
          '<div><dt><kbd>Esc</kbd></dt><dd>关闭详情或清除搜索</dd></div>' +
        '</dl>' +
      '</section>' +
      '<p class="workbench-foot">' + papers.length + ' 条虚构演示记录，非真实文献。无账户、无在线同步；阅读状态只在本页生效。</p>' +
      '</div>';
  }

  function syncStatusFilter(counts) {
    els.statusFilter.querySelectorAll('[data-status]').forEach(function (button) {
      var status = button.getAttribute('data-status');
      var active = state.status === status;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
      var count = button.querySelector('[data-status-count]');
      if (count) count.textContent = String(counts[status] == null ? 0 : counts[status]);
    });
  }

  function syncTopicList(counts) {
    els.topicList.querySelectorAll('[data-topic]').forEach(function (button) {
      var topic = button.getAttribute('data-topic');
      var active = state.topic === topic;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
      var count = button.querySelector('[data-topic-count]');
      if (count) count.textContent = String(counts[topic] == null ? 0 : counts[topic]);
    });
  }

  function syncProgress(progress) {
    els.progressLabel.textContent = '已读 ' + progress.read + ' / ' + progress.total;
    els.progressTrack.setAttribute('aria-label',
      '已读 ' + progress.read + ' 篇，在读 ' + progress.reading + ' 篇，未读 ' + progress.unread + ' 篇，共 ' + progress.total + ' 篇');
    ['read', 'reading', 'unread'].forEach(function (key) {
      var segment = els.progressTrack.querySelector('[data-seg="' + key + '"]');
      if (!segment) return;
      var amount = progress[key];
      segment.hidden = amount === 0;
      segment.style.flexGrow = String(amount);
      segment.style.flexBasis = '0';
    });
  }

  function syncResult(visible) {
    var total = papers.length;
    var text;
    if (!hasFilters()) {
      text = total + ' 篇论文';
    } else {
      var parts = [];
      if (state.query.trim()) parts.push('搜索「' + state.query.trim() + '」');
      if (state.status !== 'all') parts.push(statusLabel(state.status));
      if (state.topic !== 'all') parts.push(state.topic);
      text = '筛选出 ' + visible.length + ' 篇 / 共 ' + total + ' 篇 · ' + parts.join(' · ');
    }
    els.resultText.textContent = text;
    els.clearFilters.hidden = !hasFilters();
  }

  function syncEmpty(visible) {
    var filtered = hasFilters();
    if (visible.length) {
      els.emptyState.hidden = true;
      els.paperList.hidden = false;
      return;
    }

    els.paperList.hidden = true;
    els.emptyState.hidden = false;
    els.emptyCopy.textContent = filtered
      ? '当前搜索与筛选下没有记录。可以换一个关键词，或清除筛选后查看全部 ' + papers.length + ' 篇。'
      : '当前没有可显示的演示记录。';
    els.emptyClearAll.hidden = !filtered;
    els.emptyClearAll.textContent = state.query.trim() ? '清除全部搜索与筛选' : '清除筛选';
    els.emptyClearSearch.hidden = !(state.query.trim() && (state.status !== 'all' || state.topic !== 'all'));
  }

  function syncView() {
    var view = state.selectedId ? 'detail' : 'list';
    els.shell.setAttribute('data-view', view);
    els.workspace.setAttribute('data-panel', state.selectedId ? 'open' : 'closed');
  }

  function renderDetail(visible) {
    var paper = state.selectedId ? byId(state.selectedId) : null;
    if (!paper) {
      els.detailContent.innerHTML = workbenchMarkup();
      els.detailPane.setAttribute('aria-label', '阅读台');
      return;
    }
    var inResults = visible.some(function (item) { return item.id === paper.id; });
    els.detailContent.innerHTML = detailMarkup(paper, inResults);
    els.detailPane.setAttribute('aria-label', '论文详情');
  }

  function render() {
    if (state.selectedId && !byId(state.selectedId)) state.selectedId = null;
    var visible = getVisible();
    syncStatusFilter(core.statusCounts(papers, state));
    syncTopicList(core.topicCounts(papers, state));
    syncProgress(core.progress(papers, state));
    syncResult(visible);

    els.searchClear.hidden = !state.query;
    els.paperList.innerHTML = visible.map(function (paper) {
      return rowMarkup(paper, paper.id === state.selectedId);
    }).join('');
    syncEmpty(visible);
    syncView();
    renderDetail(visible);
  }

  function announce(message) {
    window.clearTimeout(announceTimer);
    els.live.textContent = '';
    announceTimer = window.setTimeout(function () {
      els.live.textContent = message;
    }, 30);
  }

  function closeSelectionIfHidden() {
    if (!state.selectedId) return;
    var visible = getVisible();
    if (!visible.some(function (paper) { return paper.id === state.selectedId; })) {
      state.selectedId = null;
    }
  }

  function focusAfterListChange(previousIndex, visible) {
    if (!visible.length) {
      els.listHeading.focus();
      return;
    }
    var index = previousIndex < 0 ? 0 : Math.min(previousIndex, visible.length - 1);
    var target = visible[index];
    var link = els.paperList.querySelector('[data-open="' + target.id + '"]');
    if (link) link.focus();
    else els.listHeading.focus();
  }

  function openDetail(id) {
    var paper = byId(id);
    if (!paper) return;
    state.selectedId = id;
    render();

    var compact = window.matchMedia('(max-width: 1199px)').matches;
    if (compact) window.scrollTo(0, 0);
    els.detailPane.scrollTop = 0;
    var title = document.getElementById('detail-title');
    if (title) title.focus({ preventScroll: true });
    announce('已打开《' + paper.title + '》详情。');
  }

  function closeDetail() {
    if (!state.selectedId) return;
    var id = state.selectedId;
    state.selectedId = null;
    render();
    var row = els.paperList.querySelector('[data-open="' + id + '"]');
    if (row) row.focus();
    else els.listHeading.focus();
    announce('已关闭详情，返回论文列表。');
  }

  function toggleRead(id, source) {
    var paper = byId(id);
    if (!paper) return;

    var beforeVisible = getVisible();
    var previousIndex = beforeVisible.findIndex(function (item) { return item.id === id; });
    paper.status = core.nextStatusForToggle(paper);

    var visible = getVisible();
    var stillVisible = visible.some(function (item) { return item.id === id; });
    render();

    if (source === 'list') {
      if (stillVisible) {
        var button = els.paperList.querySelector('[data-toggle="' + id + '"]');
        if (button) button.focus();
      } else {
        focusAfterListChange(previousIndex, visible);
      }
    } else {
      var statusButton = els.detailContent.querySelector('[data-set-status="' + paper.status + '"]');
      if (statusButton) statusButton.focus();
      else {
        var title = document.getElementById('detail-title');
        if (title) title.focus({ preventScroll: true });
      }
    }

    announce('《' + paper.title + '》已标为' + statusLabel(paper.status) + '。' +
      (stillVisible ? '当前结果显示 ' + visible.length + ' 篇。' : '这篇论文已移出当前筛选。'));
  }

  function setPaperStatus(id, status) {
    var paper = byId(id);
    if (!paper) return;
    if (paper.status !== status) paper.status = status;
    render();
    var button = els.detailContent.querySelector('[data-set-status="' + status + '"]');
    if (button) button.focus();
    announce('《' + paper.title + '》已设为' + statusLabel(status) + '。');
  }

  function applyQuery(value) {
    state.query = value;
    closeSelectionIfHidden();
    render();
  }

  function clearAllFilters(focusTarget) {
    state.query = '';
    state.status = 'all';
    state.topic = 'all';
    els.search.value = '';
    closeSelectionIfHidden();
    render();
    var target = focusTarget || els.statusFilter.querySelector('[data-status="all"]');
    if (target) target.focus();
    announce('已清除全部搜索与筛选，显示 ' + getVisible().length + ' 篇论文。');
  }

  function clearStatusFilter() {
    if (state.status !== 'all') state.status = 'all';
    else if (state.topic !== 'all') state.topic = 'all';
    else {
      state.query = '';
      els.search.value = '';
    }
    render();
    var title = document.getElementById('detail-title');
    if (title) title.focus({ preventScroll: true });
    announce('已显示全部状态，当前筛选中有 ' + getVisible().length + ' 篇论文。');
  }

  function focusedPaperId() {
    var active = document.activeElement;
    var row = active && active.closest ? active.closest('[data-row-id]') : null;
    if (row) return row.getAttribute('data-row-id');
    if (active && active.closest && active.closest('.detail-pane') && state.selectedId) return state.selectedId;
    return null;
  }

  function moveRowFocus(direction) {
    var visible = getVisible();
    if (!visible.length) return;
    var currentId = focusedPaperId();
    if (!currentId) currentId = state.selectedId;
    var index = visible.findIndex(function (paper) { return paper.id === currentId; });
    var next;
    if (index === -1) next = direction > 0 ? 0 : visible.length - 1;
    else next = Math.min(visible.length - 1, Math.max(0, index + direction));
    var link = els.paperList.querySelector('[data-open="' + visible[next].id + '"]');
    if (link) link.focus();
  }

  function bind() {
    els.statusFilter.addEventListener('click', function (event) {
      var button = event.target.closest('[data-status]');
      if (!button) return;
      var value = button.getAttribute('data-status');
      if (value === state.status) return;
      state.status = value;
      closeSelectionIfHidden();
      render();
    });

    els.topicList.addEventListener('click', function (event) {
      var button = event.target.closest('[data-topic]');
      if (!button) return;
      var value = button.getAttribute('data-topic');
      if (value === state.topic) return;
      state.topic = value;
      closeSelectionIfHidden();
      render();
    });

    els.search.addEventListener('input', function () {
      applyQuery(els.search.value);
    });

    els.search.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && els.search.value) {
        event.preventDefault();
        els.search.value = '';
        applyQuery('');
      }
    });

    els.searchClear.addEventListener('click', function () {
      els.search.value = '';
      applyQuery('');
      els.search.focus();
    });

    els.sort.addEventListener('change', function () {
      state.sort = els.sort.value;
      render();
      announce('已按' + els.sort.options[els.sort.selectedIndex].text + '排序。');
    });

    els.clearFilters.addEventListener('click', function () {
      clearAllFilters();
    });

    els.emptyClearAll.addEventListener('click', function () {
      clearAllFilters(els.search);
    });

    els.emptyClearSearch.addEventListener('click', function () {
      els.search.value = '';
      applyQuery('');
      els.search.focus();
    });

    els.paperList.addEventListener('click', function (event) {
      var open = event.target.closest('[data-open]');
      if (open) {
        event.preventDefault();
        openDetail(open.getAttribute('data-open'));
        return;
      }
      var toggle = event.target.closest('[data-toggle]');
      if (toggle) {
        toggleRead(toggle.getAttribute('data-toggle'), 'list');
      }
    });

    els.detailContent.addEventListener('click', function (event) {
      var close = event.target.closest('[data-close-detail]');
      if (close) {
        closeDetail();
        return;
      }
      var open = event.target.closest('[data-open]');
      if (open) {
        openDetail(open.getAttribute('data-open'));
        return;
      }
      var setStatus = event.target.closest('[data-set-status]');
      if (setStatus && state.selectedId) {
        setPaperStatus(state.selectedId, setStatus.getAttribute('data-set-status'));
        return;
      }
      var clearStatus = event.target.closest('[data-clear-status]');
      if (clearStatus) {
        clearStatusFilter();
      }
    });

    document.addEventListener('keydown', function (event) {
      var target = event.target;
      var typing = target && target.closest ? target.closest('input, textarea, select, [contenteditable="true"]') : null;
      var plain = !event.metaKey && !event.ctrlKey && !event.altKey;

      if (event.key === '/' && !typing && plain) {
        event.preventDefault();
        els.search.focus();
        els.search.select();
        return;
      }

      if (event.key === 'Escape') {
        if (typing) {
          if (target === els.search && els.search.value) {
            event.preventDefault();
            els.search.value = '';
            applyQuery('');
            return;
          }
          target.blur();
          return;
        }
        if (state.selectedId) {
          event.preventDefault();
          closeDetail();
          return;
        }
        return;
      }

      if (typing || !plain) return;
      var key = event.key.toLowerCase();

      if (key === 'j' || key === 'k') {
        event.preventDefault();
        moveRowFocus(key === 'j' ? 1 : -1);
        return;
      }

      if (key === 'r') {
        var id = focusedPaperId() || state.selectedId;
        if (!id) return;
        event.preventDefault();
        var row = document.activeElement && document.activeElement.closest
          ? document.activeElement.closest('[data-row-id]')
          : null;
        toggleRead(id, row ? 'list' : 'detail');
      }
    });
  }

  bind();
  render();
})();
