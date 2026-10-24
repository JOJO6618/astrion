'use strict';
/* ==========================================================================
   Papertrail 论文阅读工作台 —— 逻辑层
   前半部分是纯函数（筛选/排序/计数），后半部分是 DOM 交互。
   纯函数在文件末尾导出，便于在没有浏览器的环境里做逻辑核对。
   ========================================================================== */

(function (root) {
  var STATUS_LABEL = { unread: '未读', reading: '在读', read: '已读' };
  var STATUS_ORDER = ['unread', 'reading', 'read'];
  var NOTE_KEY = 'papertrail.notes.v1';

  function text(value) { return value === null || value === undefined ? '' : String(value); }
  function normalize(value) { return text(value).toLowerCase().replace(/\s+/g, ' ').trim(); }

  /* ------------------------------------------------------------ 纯函数 --- */

  // 搜索只覆盖标题与作者，和界面上的说明保持一致。
  function matchesQuery(paper, query) {
    var needle = normalize(query);
    if (!needle) return true;
    if (normalize(paper.title).indexOf(needle) !== -1) return true;
    return normalize(paper.authors).indexOf(needle) !== -1;
  }

  function byStatus(list, status) {
    return status === 'all' ? list.slice() : list.filter(function (p) { return p.status === status; });
  }

  function byTopic(list, topic) {
    return topic === 'all' ? list.slice() : list.filter(function (p) { return p.topic === topic; });
  }

  function byQuery(list, query) {
    return list.filter(function (p) { return matchesQuery(p, query); });
  }

  var collator = null;
  try {
    collator = new Intl.Collator('zh-Hans-CN', { numeric: true, sensitivity: 'base' });
  } catch (err) {
    collator = null;
  }
  function cmpTitle(a, b) {
    return collator ? collator.compare(a.title, b.title) : (a.title < b.title ? -1 : a.title > b.title ? 1 : 0);
  }

  function compare(a, b, key) {
    if (key === 'title') return cmpTitle(a, b);
    if (key === 'pages') return (a.pages - b.pages) || cmpTitle(a, b);
    return (a.year - b.year) || cmpTitle(a, b);
  }

  function sortPapers(list, key, dir) {
    var sign = dir === 'asc' ? 1 : -1;
    return list.slice().sort(function (a, b) { return sign * compare(a, b, key); });
  }

  function counts(list) {
    var out = { all: list.length, unread: 0, reading: 0, read: 0 };
    list.forEach(function (p) { if (STATUS_LABEL[p.status]) out[p.status] += 1; });
    return out;
  }

  // 当前可见列表：搜索 ∩ 主题 ∩ 状态，然后再排序。
  function selectPapers(papers, state) {
    var scoped = byStatus(byTopic(byQuery(papers, state.query), state.topic), state.status);
    return sortPapers(scoped, state.sortKey, state.sortDir);
  }

  // 分面计数：状态计数在“搜索+主题”范围内，主题计数在“搜索+状态”范围内，
  // 因此每个数字都等于点击它之后能看到的条目数。
  function facetCounts(papers, state) {
    var base = byTopic(byQuery(papers, state.query), state.topic);
    var statusCounts = counts(base);
    var topicBase = byStatus(byQuery(papers, state.query), state.status);
    var topicCounts = { all: topicBase.length };
    topicBase.forEach(function (p) { topicCounts[p.topic] = (topicCounts[p.topic] || 0) + 1; });
    return { status: statusCounts, topic: topicCounts, total: papers.length };
  }

  var api = {
    STATUS_LABEL: STATUS_LABEL,
    STATUS_ORDER: STATUS_ORDER,
    matchesQuery: matchesQuery,
    byStatus: byStatus,
    byTopic: byTopic,
    byQuery: byQuery,
    sortPapers: sortPapers,
    counts: counts,
    selectPapers: selectPapers,
    facetCounts: facetCounts
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  /* ---------------------------------------------------------- DOM 部分 --- */

  var els = {
    statusFilters: document.getElementById('status-filters'),
    topicFilters: document.getElementById('topic-filters'),
    search: document.getElementById('search'),
    searchClear: document.getElementById('search-clear'),
    resultCount: document.getElementById('result-count'),
    activeFilters: document.getElementById('active-filters'),
    papers: document.getElementById('papers'),
    empty: document.getElementById('empty'),
    emptyClear: document.getElementById('empty-clear'),
    resetAll: document.getElementById('reset-all'),
    meter: document.getElementById('meter'),
    legend: document.getElementById('legend'),
    railNotes: document.getElementById('rail-notes'),
    drawer: document.getElementById('drawer'),
    drawerBody: document.getElementById('drawer-body'),
    scrim: document.getElementById('scrim'),
    prev: document.getElementById('prev-paper'),
    next: document.getElementById('next-paper'),
    close: document.getElementById('close-drawer'),
    toast: document.getElementById('toast'),
    shortcutBtn: document.getElementById('shortcut-btn'),
    shortcutPanel: document.getElementById('shortcut-panel')
  };
  var sortBtns = Array.prototype.slice.call(document.querySelectorAll('[data-sort]'));

  var source = (typeof window !== 'undefined' && window.PAPERS) ? window.PAPERS : [];
  var initial = source.map(function (p) { return Object.assign({}, p); });
  var papers = source.map(function (p) { return Object.assign({}, p); });

  var state = {
    query: '',
    status: 'all',
    topic: 'all',
    sortKey: 'year',
    sortDir: 'desc',
    openId: null
  };

  var lastTrigger = null;
  var noteTimer = null;

  /* --------------------------------------------------------- 文本工具 --- */

  var ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function esc(value) {
    return text(value).replace(/[&<>"']/g, function (c) { return ESC_MAP[c]; });
  }

  // 搜索结果高亮：只在标题/作者里标出首处命中，其余保持原文。
  function hl(value, query) {
    var raw = text(value);
    var q = text(query).trim();
    if (!q) return esc(raw);
    var idx = raw.toLowerCase().indexOf(q.toLowerCase());
    if (idx < 0) return esc(raw);
    return esc(raw.slice(0, idx)) + '<mark>' + esc(raw.slice(idx, idx + q.length)) + '</mark>' + esc(raw.slice(idx + q.length));
  }

  function cssId(id) { return text(id).replace(/["\\]/g, '\\$&'); }

  function isTyping(el) {
    if (!el || !el.tagName) return false;
    var tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable === true;
  }

  /* ------------------------------------------------------- 笔记存储 --- */

  var storageOk = (function () {
    try {
      var probe = '__papertrail_probe__';
      window.localStorage.setItem(probe, '1');
      window.localStorage.removeItem(probe);
      return true;
    } catch (err) {
      return false;
    }
  })();

  var notes = {};
  if (storageOk) {
    try {
      var rawNotes = window.localStorage.getItem(NOTE_KEY);
      if (rawNotes) {
        var parsed = JSON.parse(rawNotes);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) notes = parsed;
      }
    } catch (err) {
      notes = {};
    }
  }

  function noteOf(paper) {
    return Object.prototype.hasOwnProperty.call(notes, paper.id) ? text(notes[paper.id]) : text(paper.note);
  }

  function persistNotes() {
    if (!storageOk) return false;
    try {
      window.localStorage.setItem(NOTE_KEY, JSON.stringify(notes));
      return true;
    } catch (err) {
      storageOk = false;
      return false;
    }
  }

  /* ------------------------------------------------------------ 渲染 --- */

  function currentPaper() {
    for (var i = 0; i < papers.length; i += 1) {
      if (papers[i].id === state.openId) return papers[i];
    }
    return null;
  }

  function rowTemplate(paper) {
    var hasNote = normalize(noteOf(paper)).length > 0;
    var menuId = 'menu-' + paper.id;
    var items = STATUS_ORDER.map(function (key) {
      var checked = paper.status === key;
      return '<button type="button" class="statmenu__item" role="menuitemradio" aria-checked="' + checked +
        '" data-set-status="' + key + '"><span class="statmenu__check" aria-hidden="true">' +
        (checked ? '✓' : '') + '</span>' + STATUS_LABEL[key] + '</button>';
    }).join('');

    return '<li class="entry' + (state.openId === paper.id ? ' is-open' : '') + '" data-id="' + esc(paper.id) +
      '" data-status="' + esc(paper.status) + '">' +
      '<div class="entry__head">' +
        '<h3 class="entry__title"><button type="button" class="entry__open" data-open="' + esc(paper.id) +
          '" aria-haspopup="dialog">' + hl(paper.title, state.query) + '</button></h3>' +
        '<div class="statmenu">' +
          '<button type="button" class="statbtn" data-menu-btn data-status="' + esc(paper.status) +
            '" aria-haspopup="true" aria-expanded="false" aria-controls="' + menuId +
            '" aria-label="阅读状态：' + STATUS_LABEL[paper.status] + '，点击更改">' +
            '<span class="dot dot--' + esc(paper.status) + '" aria-hidden="true"></span>' +
            '<span class="statbtn__label">' + STATUS_LABEL[paper.status] + '</span>' +
            '<svg class="statbtn__chev" viewBox="0 0 10 10" aria-hidden="true"><path d="M1 3l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.4"></path></svg>' +
          '</button>' +
          '<div class="statmenu__list" id="' + menuId + '" role="menu" aria-label="设置阅读状态" hidden>' + items + '</div>' +
        '</div>' +
      '</div>' +
      '<p class="entry__meta">' +
        '<span class="entry__authors">' + hl(paper.authors, state.query) + '</span>' +
        '<span class="sep" aria-hidden="true">·</span><span>' + esc(paper.year) + '</span>' +
        '<span class="sep" aria-hidden="true">·</span><span>' + esc(paper.venue) + '</span>' +
        '<span class="sep" aria-hidden="true">·</span><span>' + esc(paper.pages) + ' 页</span>' +
        '<span class="topic">' + esc(paper.topic) + '</span>' +
      '</p>' +
      '<p class="entry__abstract">' + esc(paper.abstract) + '</p>' +
      '<div class="entry__foot">' +
        '<span class="entry__note">' + (hasNote ? '<svg aria-hidden="true"><use href="#icon-note"></use></svg>有笔记' : '') + '</span>' +
        '<button type="button" class="btn btn--ghost btn--sm" data-open="' + esc(paper.id) + '" aria-haspopup="dialog">查看详情</button>' +
      '</div>' +
    '</li>';
  }

  function renderList() {
    var visible = selectPapers(papers, state);
    els.papers.innerHTML = visible.map(rowTemplate).join('');
    renderDerived(visible);
  }

  function updateFacetCounts(facets) {
    Array.prototype.forEach.call(els.statusFilters.querySelectorAll('[data-count]'), function (el) {
      var key = el.getAttribute('data-count');
      var n = facets.status[key] || 0;
      el.textContent = String(n);
      var item = el.closest('.facet__item');
      if (item) item.classList.toggle('is-zero', n === 0);
    });
    Array.prototype.forEach.call(els.topicFilters.querySelectorAll('[data-topic]'), function (btn) {
      var n = facets.topic[btn.getAttribute('data-topic')] || 0;
      var count = btn.querySelector('.facet__count');
      if (count) count.textContent = String(n);
      btn.classList.toggle('is-zero', n === 0);
    });
  }

  function renderRail(facets) {
    var s = facets.status;
    var total = Math.max(1, s.all);
    var shown = STATUS_ORDER.filter(function (k) { return s[k] > 0; });
    els.meter.innerHTML = shown.map(function (k) {
      return '<span class="meter--' + k + '" style="width:' + (s[k] / total * 100).toFixed(2) + '%"></span>';
    }).join('');
    els.meter.setAttribute('aria-label', '当前范围 ' + s.all + ' 篇：未读 ' + s.unread + '，在读 ' + s.reading + '，已读 ' + s.read);

    els.legend.innerHTML = STATUS_ORDER.map(function (k) {
      return '<div><span class="dot dot--' + k + '" aria-hidden="true"></span>' + STATUS_LABEL[k] + '<b>' + s[k] + '</b></div>';
    }).join('');

    var noted = papers.filter(function (p) { return normalize(noteOf(p)).length > 0; });
    noted.sort(function (a, b) { return (b.year - a.year) || cmpTitle(a, b); });
    els.railNotes.innerHTML = noted.length
      ? noted.slice(0, 5).map(function (p) {
        return '<button type="button" class="rail__note-item" data-open="' + esc(p.id) + '" aria-haspopup="dialog">' +
          esc(p.title) + '<span>' + STATUS_LABEL[p.status] + ' · ' + esc(p.authors) + '</span></button>';
      }).join('')
      : '<p class="rail__hint">还没有笔记。打开论文详情即可写下第一条。</p>';
  }

  function renderDerived(visible) {
    var list = visible || selectPapers(papers, state);
    var facets = facetCounts(papers, state);

    updateFacetCounts(facets);
    renderRail(facets);

    els.resultCount.innerHTML = '显示 <strong>' + list.length + '</strong> 篇 · 共 <strong>' + papers.length + '</strong> 篇';
    els.empty.hidden = list.length !== 0;

    var parts = [];
    if (state.status !== 'all') parts.push('状态：' + STATUS_LABEL[state.status]);
    if (state.topic !== 'all') parts.push('主题：' + state.topic);
    if (state.query.trim()) parts.push('关键词：“' + state.query.trim() + '”');

    if (parts.length) {
      els.activeFilters.innerHTML = '<span class="resultbar__summary">' + esc(parts.join(' / ')) +
        '</span><button type="button" class="linkbtn" data-clear-filters>清除筛选</button>';
    } else {
      els.activeFilters.innerHTML = '';
    }
  }

  function syncFacets() {
    Array.prototype.forEach.call(els.statusFilters.querySelectorAll('[data-status]'), function (btn) {
      btn.setAttribute('aria-pressed', String(btn.getAttribute('data-status') === state.status));
    });
    Array.prototype.forEach.call(els.topicFilters.querySelectorAll('[data-topic]'), function (btn) {
      btn.setAttribute('aria-pressed', String(btn.getAttribute('data-topic') === state.topic));
    });
  }

  function syncSort() {
    sortBtns.forEach(function (btn) {
      var active = btn.getAttribute('data-sort') === state.sortKey;
      btn.setAttribute('aria-pressed', String(active));
      var dir = btn.querySelector('[data-dir]');
      if (dir) {
        dir.hidden = !active;
        dir.textContent = state.sortDir === 'asc' ? '↑' : '↓';
      }
    });
  }

  function buildTopicFacets() {
    var topics = [];
    papers.forEach(function (p) { if (topics.indexOf(p.topic) === -1) topics.push(p.topic); });
    var html = '<button type="button" class="facet__item" data-topic="all" aria-pressed="' +
      (state.topic === 'all') + '">全部主题<span class="facet__count">' + papers.length + '</span></button>';
    html += topics.map(function (t) {
      return '<button type="button" class="facet__item" data-topic="' + esc(t) + '" aria-pressed="' +
        (state.topic === t) + '">' + esc(t) + '<span class="facet__count">0</span></button>';
    }).join('');
    els.topicFilters.innerHTML = html;
  }

  /* --------------------------------------------------------- 状态菜单 --- */

  function closeMenus(except) {
    Array.prototype.forEach.call(document.querySelectorAll('.statmenu__list'), function (list) {
      if (list === except) return;
      list.hidden = true;
      list.classList.remove('is-up');
      var btn = list.parentElement.querySelector('[data-menu-btn]');
      if (btn) btn.setAttribute('aria-expanded', 'false');
    });
  }

  function toggleMenu(btn) {
    var list = document.getElementById(btn.getAttribute('aria-controls'));
    if (!list) return;
    var willOpen = list.hidden;
    closeMenus();
    if (!willOpen) {
      btn.setAttribute('aria-expanded', 'false');
      btn.focus();
      return;
    }
    list.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    list.classList.remove('is-up');
    var rect = list.getBoundingClientRect();
    if (rect.bottom > window.innerHeight - 8) list.classList.add('is-up');
    var checked = list.querySelector('[aria-checked="true"]') || list.querySelector('[role="menuitemradio"]');
    if (checked) checked.focus();
  }

  /* ------------------------------------------------------------ 状态 --- */

  function updateRow(row, paper) {
    row.setAttribute('data-status', paper.status);
    var btn = row.querySelector('[data-menu-btn]');
    if (btn) {
      btn.setAttribute('data-status', paper.status);
      btn.setAttribute('aria-label', '阅读状态：' + STATUS_LABEL[paper.status] + '，点击更改');
      var label = btn.querySelector('.statbtn__label');
      if (label) label.textContent = STATUS_LABEL[paper.status];
      var dot = btn.querySelector('.dot');
      if (dot) dot.className = 'dot dot--' + paper.status;
    }
    Array.prototype.forEach.call(row.querySelectorAll('[data-set-status]'), function (item) {
      var checked = item.getAttribute('data-set-status') === paper.status;
      item.setAttribute('aria-checked', String(checked));
      var mark = item.querySelector('.statmenu__check');
      if (mark) mark.textContent = checked ? '✓' : '';
    });
  }

  function syncDrawerStatus(paper) {
    if (state.openId !== paper.id) return;
    Array.prototype.forEach.call(els.drawerBody.querySelectorAll('.segmented [role="radio"]'), function (radio) {
      var checked = radio.getAttribute('data-set-status') === paper.status;
      radio.setAttribute('aria-checked', String(checked));
      radio.setAttribute('tabindex', checked ? '0' : '-1');
    });
  }

  function setStatus(id, next) {
    var paper = null;
    for (var i = 0; i < papers.length; i += 1) if (papers[i].id === id) paper = papers[i];
    closeMenus();
    if (!paper || !STATUS_LABEL[next] || paper.status === next) return;

    paper.status = next;

    var row = els.papers.querySelector('.entry[data-id="' + cssId(id) + '"]');
    var focusInRow = !!(row && row.contains(document.activeElement));
    var staysVisible = state.status === 'all' || state.status === next;

    if (row && !staysVisible) {
      // 条目不再符合当前状态筛选：移除它，并把焦点交给相邻条目。
      var entries = Array.prototype.slice.call(els.papers.querySelectorAll('.entry__open'));
      var index = entries.indexOf(row.querySelector('.entry__open'));
      row.remove();
      renderDerived();
      if (focusInRow) {
        var rest = Array.prototype.slice.call(els.papers.querySelectorAll('.entry__open'));
        if (rest.length) rest[Math.min(Math.max(index, 0), rest.length - 1)].focus();
        else els.search.focus();
      }
    } else if (row) {
      updateRow(row, paper);
      renderDerived();
      if (focusInRow) {
        var menuBtn = row.querySelector('[data-menu-btn]');
        if (menuBtn) menuBtn.focus();
      }
    } else {
      renderList();
    }

    syncDrawerStatus(paper);
    showToast('「' + paper.title + '」已标为' + STATUS_LABEL[next]);
  }

  /* ------------------------------------------------------------ 抽屉 --- */

  function renderDrawerBody() {
    var paper = currentPaper();
    if (!paper) return;

    var note = noteOf(paper);
    var visible = selectPapers(papers, state);
    var idx = -1;
    for (var i = 0; i < visible.length; i += 1) if (visible[i].id === paper.id) idx = i;
    els.prev.disabled = idx <= 0;
    els.next.disabled = idx < 0 || idx >= visible.length - 1;

    var segmented = STATUS_ORDER.map(function (key) {
      var checked = paper.status === key;
      return '<button type="button" role="radio" aria-checked="' + checked + '" tabindex="' +
        (checked ? '0' : '-1') + '" data-set-status="' + key + '"><span class="dot dot--' + key +
        '" aria-hidden="true"></span>' + STATUS_LABEL[key] + '</button>';
    }).join('');

    els.drawerBody.innerHTML =
      '<h2 class="drawer__title" id="drawer-title" tabindex="-1">' + esc(paper.title) + '</h2>' +
      '<p class="drawer__byline">' + esc(paper.authors) + '</p>' +
      '<dl class="meta">' +
        '<dt>年份</dt><dd>' + esc(paper.year) + '</dd>' +
        '<dt>发表</dt><dd>' + esc(paper.venue) + '</dd>' +
        '<dt>主题</dt><dd>' + esc(paper.topic) + '</dd>' +
        '<dt>页数</dt><dd>' + esc(paper.pages) + ' 页</dd>' +
        '<dt>编号</dt><dd class="mono">' + esc(paper.id) + '</dd>' +
      '</dl>' +
      '<section class="section">' +
        '<h3 class="section__title" id="drawer-status-label">阅读状态</h3>' +
        '<div class="segmented" role="radiogroup" aria-labelledby="drawer-status-label">' + segmented + '</div>' +
      '</section>' +
      '<section class="section">' +
        '<h3 class="section__title">摘要</h3>' +
        '<p class="prose">' + esc(paper.abstract) + '</p>' +
      '</section>' +
      '<section class="section">' +
        '<div class="section__head">' +
          '<h3 class="section__title">我的笔记</h3>' +
          '<button type="button" class="linkbtn" data-note-reset' + (note === paper.note ? ' hidden' : '') + '>恢复原文笔记</button>' +
        '</div>' +
        '<textarea class="note-editor" id="note-input" rows="5" aria-label="我的笔记" placeholder="记录疑问、方法和可复用之处……">' + esc(note) + '</textarea>' +
        '<p class="note-bar"><span id="note-state">' +
          (storageOk ? '笔记保存在本机浏览器，不会同步' : '当前环境无法本地保存，笔记只在本页有效') +
        '</span><span class="mono" id="note-count">' + Array.from(note).length + ' 字</span></p>' +
      '</section>' +
      '<div class="drawer__foot">' +
        '<button type="button" class="btn btn--primary" data-copy>复制引用</button>' +
        '<button type="button" class="btn" data-close>关闭</button>' +
      '</div>' +
      '<p class="drawer__demo">演示记录：字段与原始素材一致；未补充 DOI、机构、引用次数等素材中没有的信息。</p>';

    els.drawerBody.scrollTop = 0;
  }

  function markOpenRow() {
    Array.prototype.forEach.call(els.papers.querySelectorAll('.entry'), function (row) {
      row.classList.toggle('is-open', row.getAttribute('data-id') === state.openId);
    });
  }

  function openDrawer() {
    els.scrim.classList.add('is-open');
    els.drawer.classList.add('is-open');
    els.drawer.removeAttribute('inert');
    document.documentElement.classList.add('is-locked');
    markOpenRow();
    var title = els.drawer.querySelector('#drawer-title');
    if (title) title.focus({ preventScroll: true });
    els.drawerBody.scrollTop = 0;
  }

  function flushNote() {
    if (!noteTimer) return;
    window.clearTimeout(noteTimer);
    noteTimer = null;
    var input = els.drawerBody.querySelector('#note-input');
    if (input && state.openId) commitNote(state.openId, input.value);
  }

  function closeDrawer() {
    if (!state.openId) return;
    flushNote();
    state.openId = null;
    els.drawer.classList.remove('is-open');
    els.drawer.setAttribute('inert', '');
    els.scrim.classList.remove('is-open');
    document.documentElement.classList.remove('is-locked');
    closeMenus();
    markOpenRow();
    var back = lastTrigger;
    lastTrigger = null;
    if (back && document.contains(back)) back.focus();
    else els.search.focus();
  }

  function openPaper(id, trigger) {
    var found = null;
    for (var i = 0; i < papers.length; i += 1) if (papers[i].id === id) found = papers[i];
    if (!found) return;
    if (!state.openId) lastTrigger = trigger || document.activeElement;
    state.openId = id;
    renderDrawerBody();
    openDrawer();
  }

  function step(delta) {
    var visible = selectPapers(papers, state);
    var idx = -1;
    for (var i = 0; i < visible.length; i += 1) if (visible[i].id === state.openId) idx = i;
    if (idx < 0) return;
    var next = idx + delta;
    if (next < 0 || next >= visible.length) return;
    state.openId = visible[next].id;
    renderDrawerBody();
    openDrawer();
  }

  /* ------------------------------------------------------------ 笔记 --- */

  function noteMessage(saved) {
    if (saved) return '笔记保存在本机浏览器，不会同步';
    return '当前环境无法本地保存，笔记只在本页有效';
  }

  function setNoteState(message) {
    var el = els.drawerBody.querySelector('#note-state');
    if (el) el.textContent = message;
  }

  function syncNoteUi(paper, value) {
    var count = els.drawerBody.querySelector('#note-count');
    if (count) count.textContent = Array.from(value).length + ' 字';
    var reset = els.drawerBody.querySelector('[data-note-reset]');
    if (reset) reset.hidden = value === paper.note;
  }

  function updateRowNoteFlag(id) {
    var row = els.papers.querySelector('.entry[data-id="' + cssId(id) + '"]');
    if (!row) return;
    var paper = null;
    for (var i = 0; i < papers.length; i += 1) if (papers[i].id === id) paper = papers[i];
    var has = !!(paper && normalize(noteOf(paper)).length > 0);
    var slot = row.querySelector('.entry__note');
    if (slot) slot.innerHTML = has ? '<svg aria-hidden="true"><use href="#icon-note"></use></svg>有笔记' : '';
  }

  function commitNote(id, value) {
    var paper = null;
    for (var i = 0; i < papers.length; i += 1) if (papers[i].id === id) paper = papers[i];
    if (!paper) return;
    if (value === paper.note) delete notes[id];
    else notes[id] = value;
    var saved = persistNotes();
    setNoteState(noteMessage(saved));
    updateRowNoteFlag(id);
    renderDerived();
  }

  /* ------------------------------------------------------------ 提示 --- */

  var toastTimer = null;
  function showToast(message) {
    els.toast.textContent = message;
    els.toast.classList.add('is-visible');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { els.toast.classList.remove('is-visible'); }, 2600);
  }

  /* --------------------------------------------------------- 复制引用 --- */

  function legacyCopy(value) {
    var ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function copyCitation() {
    var paper = currentPaper();
    if (!paper) return;
    var citation = paper.authors + '：《' + paper.title + '》，' + paper.venue + '，' +
      paper.year + '，' + paper.pages + ' 页。（演示数据）';
    var done = function (ok) {
      showToast(ok ? '引用已复制到剪贴板' : '当前浏览器不允许自动复制，请手动选择文本');
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(citation).then(function () { done(true); }, function () { done(legacyCopy(citation)); });
    } else {
      done(legacyCopy(citation));
    }
  }

  /* --------------------------------------------------------- 快捷键 --- */

  function setShortcuts(open) {
    els.shortcutPanel.hidden = !open;
    els.shortcutBtn.setAttribute('aria-expanded', String(!!open));
  }

  function entryButtons() {
    return Array.prototype.slice.call(els.papers.querySelectorAll('.entry__open'));
  }

  function moveFocus(delta) {
    var buttons = entryButtons();
    if (!buttons.length) { els.search.focus(); return; }
    var current = buttons.indexOf(document.activeElement);
    var next;
    if (current < 0) next = delta > 0 ? 0 : buttons.length - 1;
    else next = Math.min(Math.max(current + delta, 0), buttons.length - 1);
    var target = buttons[next];
    if (target) {
      target.focus();
      target.scrollIntoView({ block: 'nearest' });
    }
  }

  function trapTab(event) {
    var focusables = Array.prototype.slice.call(
      els.drawer.querySelectorAll('button, textarea, input, [href], [tabindex]:not([tabindex="-1"])')
    ).filter(function (el) { return !el.disabled && el.getClientRects().length > 0; });
    if (!focusables.length) return;
    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    var active = document.activeElement;
    var inside = els.drawer.contains(active);
    if (event.shiftKey) {
      if (!inside || active === first) { event.preventDefault(); last.focus(); }
    } else if (!inside || active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function handleEscape(event) {
    if (state.openId) { event.preventDefault(); closeDrawer(); return; }
    if (!els.shortcutPanel.hidden) {
      event.preventDefault();
      setShortcuts(false);
      els.shortcutBtn.focus();
      return;
    }
    if (isTyping(event.target)) {
      if (event.target === els.search) {
        if (els.search.value) {
          event.preventDefault();
          els.search.value = '';
          state.query = '';
          els.searchClear.hidden = true;
          renderList();
        } else {
          els.search.blur();
        }
      }
      return;
    }
    if (document.querySelector('.statmenu__list:not([hidden])')) closeMenus();
  }

  function onDocumentKeydown(event) {
    if (event.defaultPrevented) return;

    if (event.key === 'Tab' && state.openId) { trapTab(event); return; }
    if (event.key === 'Escape') { handleEscape(event); return; }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (isTyping(event.target)) return;

    if (event.key === '/') {
      event.preventDefault();
      els.search.focus();
      els.search.select();
      return;
    }
    if (event.key === '?') {
      event.preventDefault();
      setShortcuts(els.shortcutPanel.hidden);
      return;
    }
    if (event.key === 'j' || event.key === 'k') {
      event.preventDefault();
      moveFocus(event.key === 'j' ? 1 : -1);
    }
  }

  function onDocumentClick(event) {
    if (!event.target.closest('.statmenu')) closeMenus();
    if (!event.target.closest('.shortcut-panel') && !event.target.closest('#shortcut-btn')) setShortcuts(false);
  }

  /* --------------------------------------------------------- 列表事件 --- */

  function onListClick(event) {
    var openBtn = event.target.closest('[data-open]');
    if (openBtn) { openPaper(openBtn.getAttribute('data-open'), openBtn); return; }

    var menuBtn = event.target.closest('[data-menu-btn]');
    if (menuBtn) { toggleMenu(menuBtn); return; }

    var setBtn = event.target.closest('[data-set-status]');
    if (setBtn) {
      var row = setBtn.closest('.entry');
      if (row) setStatus(row.getAttribute('data-id'), setBtn.getAttribute('data-set-status'));
    }
  }

  function onListKeydown(event) {
    var menuItem = event.target.closest('[role="menuitemradio"]');
    if (menuItem) {
      var items = Array.prototype.slice.call(menuItem.parentElement.querySelectorAll('[role="menuitemradio"]'));
      var i = items.indexOf(menuItem);
      var key = event.key;
      if (key === 'ArrowDown') { event.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (key === 'ArrowUp') { event.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (key === 'Home') { event.preventDefault(); items[0].focus(); }
      else if (key === 'End') { event.preventDefault(); items[items.length - 1].focus(); }
      else if (key === 'Escape') {
        event.preventDefault();
        var list = menuItem.parentElement;
        var btn = list.parentElement.querySelector('[data-menu-btn]');
        closeMenus();
        if (btn) btn.focus();
      }
      return;
    }

    var openBtn = event.target.closest('.entry__open');
    if (!openBtn) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      moveFocus(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Home' || event.key === 'End') {
      var buttons = entryButtons();
      if (!buttons.length) return;
      event.preventDefault();
      (event.key === 'Home' ? buttons[0] : buttons[buttons.length - 1]).focus();
    }
  }

  /* --------------------------------------------------------- 抽屉事件 --- */

  function onDrawerClick(event) {
    var statusBtn = event.target.closest('[data-set-status]');
    if (statusBtn && state.openId) {
      setStatus(state.openId, statusBtn.getAttribute('data-set-status'));
      return;
    }
    if (event.target.closest('[data-copy]')) { copyCitation(); return; }
    if (event.target.closest('[data-close]')) { closeDrawer(); return; }
    if (event.target.closest('[data-note-reset]')) {
      var paper = currentPaper();
      var input = els.drawerBody.querySelector('#note-input');
      if (!paper || !input) return;
      input.value = paper.note;
      delete notes[paper.id];
      persistNotes();
      syncNoteUi(paper, paper.note);
      setNoteState(noteMessage(storageOk));
      updateRowNoteFlag(paper.id);
      renderDerived();
      input.focus();
      showToast('已恢复原文笔记');
    }
  }

  function onDrawerInput(event) {
    if (!event.target.matches('#note-input')) return;
    var paper = currentPaper();
    if (!paper) return;
    var value = event.target.value;
    syncNoteUi(paper, value);
    window.clearTimeout(noteTimer);
    noteTimer = window.setTimeout(function () {
      noteTimer = null;
      commitNote(paper.id, value);
    }, 400);
  }

  function onDrawerKeydown(event) {
    var radio = event.target.closest('.segmented [role="radio"]');
    if (!radio) return;
    var radios = Array.prototype.slice.call(radio.parentElement.querySelectorAll('[role="radio"]'));
    var i = radios.indexOf(radio);
    var j = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') j = (i + 1) % radios.length;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') j = (i - 1 + radios.length) % radios.length;
    if (j === null || !state.openId) return;
    event.preventDefault();
    radios[j].focus();
    setStatus(state.openId, radios[j].getAttribute('data-set-status'));
  }

  /* --------------------------------------------------------- 筛选事件 --- */

  function onSearchInput() {
    state.query = els.search.value;
    els.searchClear.hidden = els.search.value.length === 0;
    renderList();
  }

  function clearFilters(mode) {
    state.query = '';
    state.status = 'all';
    state.topic = 'all';
    if (mode === 'all') {
      state.sortKey = 'year';
      state.sortDir = 'desc';
      syncSort();
    }
    els.search.value = '';
    els.searchClear.hidden = true;
    syncFacets();
    renderList();
    if (mode === 'all') {
      els.search.focus();
    } else {
      var all = els.statusFilters.querySelector('[data-status="all"]');
      if (all) all.focus();
    }
  }

  function onReset() {
    // 先丢弃未提交的笔记编辑，否则关抽屉时的 flush 会把内容重新写回存储。
    window.clearTimeout(noteTimer);
    noteTimer = null;
    closeDrawer();
    papers = initial.map(function (p) { return Object.assign({}, p); });
    notes = {};
    try { window.localStorage.removeItem(NOTE_KEY); } catch (err) { /* 存储不可用时忽略 */ }
    state.query = '';
    state.status = 'all';
    state.topic = 'all';
    state.sortKey = 'year';
    state.sortDir = 'desc';
    els.search.value = '';
    els.searchClear.hidden = true;
    syncFacets();
    syncSort();
    renderList();
    var all = els.statusFilters.querySelector('[data-status="all"]');
    if (all) all.focus();
    showToast('已恢复 9 条演示记录的初始状态');
  }

  /* ------------------------------------------------------------ 启动 --- */

  function init() {
    buildTopicFacets();

    sortBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.getAttribute('data-sort');
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === 'desc' ? 'asc' : 'desc';
        } else {
          state.sortKey = key;
          state.sortDir = key === 'title' ? 'asc' : 'desc';
        }
        syncSort();
        renderList();
      });
    });

    els.statusFilters.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-status]');
      if (!btn) return;
      state.status = btn.getAttribute('data-status');
      syncFacets();
      renderList();
    });

    els.topicFilters.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-topic]');
      if (!btn) return;
      state.topic = btn.getAttribute('data-topic');
      syncFacets();
      renderList();
    });

    els.activeFilters.addEventListener('click', function (event) {
      if (event.target.closest('[data-clear-filters]')) clearFilters('filters');
    });

    els.search.addEventListener('input', onSearchInput);
    els.searchClear.addEventListener('click', function () {
      els.search.value = '';
      state.query = '';
      els.searchClear.hidden = true;
      renderList();
      els.search.focus();
    });

    els.emptyClear.addEventListener('click', function () { clearFilters('all'); });
    els.resetAll.addEventListener('click', onReset);

    els.papers.addEventListener('click', onListClick);
    els.papers.addEventListener('keydown', onListKeydown);

    els.railNotes.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-open]');
      if (btn) openPaper(btn.getAttribute('data-open'), btn);
    });

    els.prev.addEventListener('click', function () { step(-1); });
    els.next.addEventListener('click', function () { step(1); });
    els.close.addEventListener('click', closeDrawer);
    els.scrim.addEventListener('click', closeDrawer);
    els.drawerBody.addEventListener('click', onDrawerClick);
    els.drawerBody.addEventListener('input', onDrawerInput);
    els.drawerBody.addEventListener('keydown', onDrawerKeydown);

    els.shortcutBtn.addEventListener('click', function () {
      setShortcuts(els.shortcutPanel.hidden);
    });

    document.addEventListener('keydown', onDocumentKeydown);
    document.addEventListener('click', onDocumentClick);

    syncFacets();
    syncSort();
    renderList();
  }

  init();
})(typeof globalThis !== 'undefined' ? globalThis : this);
