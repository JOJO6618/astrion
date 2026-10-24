'use strict';
/* 无浏览器的功能冒烟测试。
   本机没有 jsdom 且不允许安装依赖，这里用一个最小 DOM 桩（解析 index.html、
   实现本页面用到的选择器/事件/焦点语义）真实执行 js/app.js 的交互路径。
   注意：这不是浏览器渲染，视觉、CSS 布局与真实辅助技术表现均未覆盖。
   运行：node checks/dom-smoke.cjs
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');

/* ------------------------------------------------------------ DOM 桩 --- */

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
const ENTITIES = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&nbsp;': '\u00a0' };
const decode = (s) => s.replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (m) => ENTITIES[m]);

class ClassList {
  constructor(el) { this.el = el; this.set = new Set(); }
  add(...names) { names.forEach((n) => n && this.set.add(n)); this.sync(); }
  remove(...names) { names.forEach((n) => this.set.delete(n)); this.sync(); }
  contains(name) { return this.set.has(name); }
  toggle(name, force) {
    const on = force === undefined ? !this.set.has(name) : !!force;
    if (on) this.set.add(name); else this.set.delete(name);
    this.sync();
    return on;
  }
  sync() { this.el.attributes.class = Array.from(this.set).join(' '); }
}

let docRef = null;

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.attributes = Object.create(null);
    this.childNodes = [];
    this.parentNode = null;
    this._listeners = Object.create(null);
    this._classes = new Set();
    this.classList = new ClassList(this);
    this.style = {};
    this._value = undefined;
  }
  get classList2() { return null; }
  setAttribute(name, value) {
    const v = String(value);
    this.attributes[name] = v;
    if (name === 'class') { this._classes = new Set(v.split(/\s+/).filter(Boolean)); this.classList.set = this._classes; }
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'class') { this._classes = new Set(); this.classList.set = this._classes; } }
  get className() { return this.getAttribute('class') || ''; }
  set className(v) { this.setAttribute('class', v); }
  get id() { return this.getAttribute('id') || ''; }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(v) { if (v) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(v) { if (v) this.setAttribute('disabled', ''); else this.removeAttribute('disabled'); }
  get value() {
    if (this._value !== undefined) return this._value;
    return this.tagName === 'TEXTAREA' ? this.textContent : '';
  }
  set value(v) { this._value = String(v); }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  appendChild(node) {
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }
  removeChild(node) {
    const i = this.childNodes.indexOf(node);
    if (i >= 0) this.childNodes.splice(i, 1);
    node.parentNode = null;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get textContent() {
    return this.childNodes.map((n) => (n.nodeType === 3 ? n.data : n.textContent)).join('');
  }
  set textContent(v) {
    this.childNodes.forEach((n) => { n.parentNode = null; });
    this.childNodes = [];
    if (v !== '' && v !== null && v !== undefined) this.appendChild({ nodeType: 3, data: String(v), parentNode: null });
  }
  get innerHTML() { return this.childNodes.map(serialize).join(''); }
  set innerHTML(html) {
    this.childNodes.forEach((n) => { n.parentNode = null; });
    this.childNodes = [];
    parseHTML(String(html), this);
  }
  contains(node) {
    let n = node;
    while (n) { if (n === this) return true; n = n.parentNode; }
    return false;
  }
  closest(sel) {
    let n = this;
    while (n && n.nodeType === 1) {
      if (matchesSelector(n, sel)) return n;
      n = n.parentNode;
    }
    return null;
  }
  matches(sel) { return matchesSelector(this, sel); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) { return queryAll(this, sel); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  dispatchEvent(event) { bubble(this, event); return !event.defaultPrevented; }
  focus() { docRef.activeElement = this; }
  blur() { if (docRef.activeElement === this) docRef.activeElement = docRef.body; }
  select() {}
  getClientRects() {
    let n = this;
    while (n && n.nodeType === 1) {
      if (n.hasAttribute('hidden')) return [];
      n = n.parentNode;
    }
    return [{ width: 10, height: 10 }];
  }
  getBoundingClientRect() { return { top: 0, left: 0, right: 10, bottom: 10, width: 10, height: 10 }; }
  scrollIntoView() {}
}

function serialize(node) {
  if (node.nodeType === 3) return node.data;
  const attrs = Object.keys(node.attributes).map((k) => ` ${k}="${node.attributes[k]}"`).join('');
  const tag = node.tagName.toLowerCase();
  return `<${tag}${attrs}>${node.childNodes.map(serialize).join('')}</${tag}>`;
}

/* ---- 选择器：只实现本页面使用的子集（后代、class、id、[attr]、:not） ---- */

function splitTop(sel, sep) {
  const out = [];
  let depth = 0;
  let quote = null;
  let cur = '';
  for (const ch of sel) {
    if (quote) { cur += ch; if (ch === quote) quote = null; continue; }
    if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
    if (ch === '[' || ch === '(') depth += 1;
    if (ch === ']' || ch === ')') depth -= 1;
    if (ch === sep && depth === 0) { out.push(cur); cur = ''; continue; }
    cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim()).filter(Boolean);
}

function matchCompound(el, comp) {
  const tokens = comp.match(/(\[[^\]]*\]|:not\([^)]*\)|[.#]?[A-Za-z_][\w-]*)/g) || [];
  if (!tokens.length) return false;
  return tokens.every((tok) => {
    if (tok[0] === '[') {
      const m = tok.match(/^\[([^\]=]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]$/);
      if (!m) return false;
      const name = m[1];
      const val = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4];
      if (!el.hasAttribute(name)) return false;
      return val === undefined ? true : el.getAttribute(name) === val;
    }
    if (tok.startsWith(':not(')) return !matchCompound(el, tok.slice(5, -1));
    if (tok[0] === '.') return el.classList.contains(tok.slice(1));
    if (tok[0] === '#') return el.id === tok.slice(1);
    return el.tagName === tok.toUpperCase();
  });
}

function matchesSelector(el, sel) {
  return splitTop(sel, ',').some((part) => {
    const steps = splitTop(part, ' ');
    let i = steps.length - 1;
    if (!matchCompound(el, steps[i])) return false;
    i -= 1;
    let node = el.parentNode;
    while (i >= 0 && node && node.nodeType === 1) {
      if (matchCompound(node, steps[i])) i -= 1;
      node = node.parentNode;
    }
    return i < 0;
  });
}

function queryAll(scope, sel) {
  const out = [];
  (function walk(node) {
    node.childNodes.forEach((child) => {
      if (child.nodeType !== 1) return;
      if (matchesSelector(child, sel)) out.push(child);
      walk(child);
    });
  })(scope);
  return out;
}

/* ---- 解析 HTML ---- */

const TAG_RE = /<!--[\s\S]*?-->|<\/([A-Za-z][\w:-]*)\s*>|<([A-Za-z][\w:-]*)((?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)/g;
const ATTR_RE = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

function parseHTML(html, parent) {
  const stack = [parent];
  let m;
  TAG_RE.lastIndex = 0;
  while ((m = TAG_RE.exec(html)) !== null) {
    const top = stack[stack.length - 1];
    if (m[0].startsWith('<!--')) continue;
    if (m[1]) {
      const tag = m[1].toUpperCase();
      for (let i = stack.length - 1; i > 0; i -= 1) {
        if (stack[i].tagName === tag) { stack.length = i; break; }
      }
      continue;
    }
    if (m[2]) {
      const el = new El(m[2]);
      let a;
      ATTR_RE.lastIndex = 0;
      const attrText = m[3] || '';
      while ((a = ATTR_RE.exec(attrText)) !== null) {
        const name = a[1];
        if (!name) continue;
        const val = a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : '';
        el.setAttribute(name, decode(val));
      }
      top.appendChild(el);
      if (!VOID.has(el.tagName.toLowerCase()) && m[4] !== '/') stack.push(el);
      continue;
    }
    if (m[5] !== undefined && m[5].trim() !== '') top.appendChild({ nodeType: 3, data: decode(m[5]), parentNode: null });
  }
}

/* ---- 事件冒泡 ---- */

function bubble(target, event) {
  let node = target;
  while (node) {
    const list = node._listeners && node._listeners[event.type];
    if (list) {
      event.currentTarget = node;
      list.slice().forEach((fn) => fn.call(node, event));
    }
    node = node.parentNode;
  }
}

/* ---- 文档/窗口 ---- */

function createEnv(bodyHTML) {
  const document = new El('#document');
  document.nodeType = 9;
  const html = new El('html');
  const body = new El('body');
  document.appendChild(html);
  html.appendChild(body);
  document.body = body;
  document.documentElement = html;
  document.activeElement = body;
  document.createElement = (tag) => new El(tag);
  document.getElementById = (id) => queryAll(document, '#' + id)[0] || null;
  document.execCommand = () => false;
  document.contains = (node) => document.containsNode ? document.containsNode(node) : walkContains(document, node);
  parseHTML(bodyHTML, body);

  const store = new Map();
  const timers = [];
  let timerSeq = 1;

  const window = {
    innerHeight: 900,
    innerWidth: 1440,
    isSecureContext: false,
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      removeItem: (k) => { store.delete(k); }
    },
    setTimeout: (fn) => { const id = timerSeq++; timers.push({ id, fn }); return id; },
    clearTimeout: (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    _store: store,
    _flush: () => { let guard = 0; while (timers.length && guard < 100) { guard += 1; const t = timers.shift(); t.fn(); } }
  };

  return { document, window, body };
}

function walkContains(root, node) {
  let n = node;
  while (n) { if (n === root) return true; n = n.parentNode; }
  return false;
}

/* ------------------------------------------------------------ 载入页面 --- */

const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const bodyHTML = (html.match(/<body[^>]*>([\s\S]*)<\/body>/i) || [, ''])[1];

const env = createEnv(bodyHTML);
const doc = env.document;
docRef = doc;

const sandbox = {
  window: env.window,
  document: doc,
  navigator: { clipboard: undefined },
  console,
  setTimeout: env.window.setTimeout,
  clearTimeout: env.window.clearTimeout
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'js/data.js'), 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'js/app.js'), 'utf8'), sandbox);

env.window._store.set('__loaded__', 'ok');

/* --------------------------------------------------------------- 断言 --- */

const results = [];
function test(name, fn) {
  try {
    fn();
    results.push({ name, pass: true, detail: '' });
  } catch (err) {
    results.push({ name, pass: false, detail: err.message });
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const $ = (sel) => doc.querySelector(sel);
const $$ = (sel) => doc.querySelectorAll(sel);
const rows = () => $$('#papers .entry');
const flush = () => env.window._flush();

function fire(el, type, init) {
  const event = Object.assign({
    type,
    target: el,
    preventDefault() { this.defaultPrevented = true; },
    defaultPrevented: false
  }, init || {});
  bubble(el, event);
  return event;
}
const click = (el) => fire(el, 'click');
const key = (el, k, init) => fire(el, 'keydown', Object.assign({ key: k }, init || {}));

/* 1. 初始渲染 */
test('初始渲染 9 条记录，计数与列表一致', () => {
  assert(rows().length === 9, '条目数 ' + rows().length);
  assert($('#result-count').textContent.includes('显示 9 篇'), $('#result-count').textContent);
  assert($('#result-count').textContent.includes('共 9 篇'), $('#result-count').textContent);
  const counts = $$('#status-filters [data-count]').map((el) => el.textContent);
  assert(counts.join(',') === '9,4,2,3', '状态计数 ' + counts.join(','));
  const topicCounts = $$('#topic-filters [data-topic]').map((b) => b.querySelector('.facet__count').textContent);
  assert(topicCounts.join(',') === '9,3,3,3', '主题计数 ' + topicCounts.join(','));
});

test('右侧概览的图例与状态计数同步', () => {
  const legend = $('#legend').textContent;
  ['未读4', '在读2', '已读3'].forEach((piece) => assert(legend.includes(piece), '缺少 ' + piece));
  assert($('#rail-notes').textContent.includes('面向生成艺术'), '有笔记列表未渲染');
});

test('默认按年份降序，首条为 2026 年', () => {
  assert(rows()[0].textContent.includes('2026'), '首条不是 2026');
  assert(rows()[8].textContent.includes('2023'), '末条不是 2023');
});

/* 2. 搜索 */
test('搜索作者「陈悦」得到 3 条，并高亮命中', () => {
  const input = $('#search');
  input.value = '陈悦';
  fire(input, 'input');
  assert(rows().length === 3, '条数 ' + rows().length);
  assert($('#result-count').textContent.includes('显示 3 篇'), $('#result-count').textContent);
  assert($$('#papers mark').length > 0, '没有高亮');
  assert(rows()[0].querySelector('.entry__authors mark'), '作者未高亮');
});

test('搜索无结果时进入空状态', () => {
  const input = $('#search');
  input.value = 'zzzz不存在';
  fire(input, 'input');
  assert(rows().length === 0, '仍有 ' + rows().length + ' 条');
  assert($('#empty').hidden === false, '空状态未显示');
  click($('#empty-clear'));
  assert(rows().length === 9, '清空后 ' + rows().length);
  assert($('#empty').hidden === true, '空状态未隐藏');
  assert($('#search').value === '', '搜索框未清空');
});

/* 3. 状态筛选 + 计数 */
test('状态筛选：未读得到 4 条，清除后回到 9 条', () => {
  const unread = $('#status-filters [data-status="unread"]');
  click(unread);
  assert(unread.getAttribute('aria-pressed') === 'true', '筛选按钮未选中');
  assert(rows().length === 4, '未读条数 ' + rows().length);
  assert(rows().every((r) => r.getAttribute('data-status') === 'unread'), '混入其他状态');
  assert($('#active-filters').textContent.includes('状态：未读'), $('#active-filters').textContent);

  const clear = $('#active-filters [data-clear-filters]');
  assert(clear, '没有清除筛选按钮');
  click(clear);
  assert(rows().length === 9, '清除后 ' + rows().length);
  assert($('#status-filters [data-status="all"]').getAttribute('aria-pressed') === 'true', '未回到全部');
  assert(doc.activeElement === $('#status-filters [data-status="all"]'), '清除后焦点未落到「全部」');
});

test('主题筛选与搜索叠加后计数自洽', () => {
  click($('#status-filters [data-status="all"]'));
  click($('#topic-filters [data-topic="人机交互"]'));
  assert(rows().length === 3, '主题条数 ' + rows().length);
  const input = $('#search');
  input.value = '王禾';
  fire(input, 'input');
  // 「王禾」在人机交互主题下命中 p02 与 p05 两篇
  assert(rows().length === 2, '叠加后条数 ' + rows().length);
  const unreadCount = $('#status-filters [data-count="unread"]').textContent;
  assert(unreadCount === '1', '未读分面计数 ' + unreadCount);
  input.value = '';
  fire(input, 'input');
  click($('#topic-filters [data-topic="all"]'));
  assert(rows().length === 9, '复位后 ' + rows().length);
});

/* 4. 排序 */
test('页数排序可点击切换，方向箭头同步', () => {
  const pagesBtn = $('[data-sort="pages"]');
  click(pagesBtn);
  assert(pagesBtn.getAttribute('aria-pressed') === 'true', '排序未激活');
  assert(pagesBtn.querySelector('[data-dir]').hidden === false, '箭头未显示');
  assert(pagesBtn.querySelector('[data-dir]').textContent === '↓', '默认方向不是降序');
  assert(rows()[0].textContent.includes('18 页'), '首条不是 18 页');
  click(pagesBtn);
  assert(rows()[0].textContent.includes('8 页'), '升序首条不是 8 页');
  assert(pagesBtn.querySelector('[data-dir]').textContent === '↑', '箭头未翻转');
  click($('[data-sort="year"]'));
  assert(rows()[0].textContent.includes('2026'), '回到年份降序失败');
});

/* 5. 行内状态菜单 */
test('行内状态菜单可打开、可改状态，列表与计数同步', () => {
  const first = rows()[0];
  const id = first.getAttribute('data-id');
  const menuBtn = first.querySelector('[data-menu-btn]');
  click(menuBtn);
  assert(menuBtn.getAttribute('aria-expanded') === 'true', '菜单未展开');
  const menu = doc.getElementById(menuBtn.getAttribute('aria-controls'));
  assert(menu.hidden === false, '菜单未显示');
  assert(doc.activeElement === menu.querySelector('[aria-checked="true"]'), '焦点未落到当前状态项');

  click(menu.querySelector('[data-set-status="read"]'));
  assert(rows()[0].getAttribute('data-status') === 'read', '行状态未更新');
  assert(rows()[0].querySelector('.statbtn__label').textContent === '已读', '标签未更新');
  assert($('#status-filters [data-count="read"]').textContent === '4', '已读计数未更新');
  assert($('#toast').textContent.includes('已标为已读'), '缺少提示');
  assert(doc.getElementById('menu-' + id).hidden === true, '菜单未关闭');
});

test('筛选条件下的状态切换会移出该行并保持焦点', () => {
  click($('#status-filters [data-status="unread"]'));
  const before = rows().length;
  const first = rows()[0];
  const title = first.querySelector('.entry__open').textContent;
  click(first.querySelector('[data-menu-btn]'));
  click(first.querySelector('[data-set-status="read"]'));
  assert(rows().length === before - 1, '行数 ' + rows().length + ' 期望 ' + (before - 1));
  assert($('#status-filters [data-count="unread"]').textContent === String(before - 1), '未读计数不同步');
  assert(doc.activeElement && doc.activeElement.classList.contains('entry__open'), '焦点未交给相邻条目');
  assert(doc.activeElement.textContent !== title, '焦点仍指向被移走的条目');
  click($('#active-filters [data-clear-filters]'));
});

/* 6. 详情抽屉 */
test('打开详情：标题、字段、状态、按钮都正确', () => {
  const first = rows()[0];
  const id = first.getAttribute('data-id');
  const trigger = first.querySelector('.entry__open');
  click(trigger);
  assert($('#drawer').classList.contains('is-open'), '抽屉未打开');
  assert(!$('#drawer').hasAttribute('inert'), '抽屉仍是 inert');
  assert($('#drawer').getAttribute('aria-modal') === 'true', '缺少 aria-modal');
  assert(doc.activeElement === $('#drawer-title'), '焦点未进入抽屉标题');

  const paper = env.window.PAPERS.find((p) => p.id === id);
  assert($('#drawer-title').textContent === paper.title, '标题不一致');
  assert($('#drawer').textContent.includes(paper.abstract), '摘要缺失');
  assert($('#drawer').textContent.includes(String(paper.pages) + ' 页'), '页数缺失');
  assert($('#drawer').textContent.includes(paper.venue), '期刊缺失');
  assert($('#drawer').querySelector('[role="radio"][aria-checked="true"]').getAttribute('data-set-status') === first.getAttribute('data-status'), '状态未选中');
  assert($('#prev-paper').disabled === true, '第一条不应能上一页');
  assert($('#next-paper').disabled === false, '应能进入下一篇');
});

test('详情内切换状态会同步列表行与计数', () => {
  const first = rows()[0];
  const id = first.getAttribute('data-id');
  const from = first.getAttribute('data-status');
  const to = from === 'reading' ? 'unread' : 'reading';
  const before = Number($('#status-filters [data-count="' + to + '"]').textContent);
  click(first.querySelector('.entry__open'));
  const target = $('#drawer').querySelector('[data-set-status="' + to + '"]');
  click(target);
  assert(target.getAttribute('aria-checked') === 'true', '抽屉内状态未更新');
  assert($('#papers .entry[data-id="' + id + '"]').getAttribute('data-status') === to, '列表行未更新');
  assert($('#status-filters [data-count="' + to + '"]').textContent === String(before + 1), '计数未随状态变化');
});

test('上一篇/下一篇按当前列表顺序切换', () => {
  click(rows()[1].querySelector('.entry__open'));
  const secondTitle = $('#drawer-title').textContent;
  click($('#next-paper'));
  assert($('#drawer-title').textContent !== secondTitle, '未切到下一篇');
  assert($('#drawer-title').textContent === rows()[2].querySelector('.entry__open').textContent, '顺序不一致');
  click($('#prev-paper'));
  assert($('#drawer-title').textContent === secondTitle, '未回到上一篇');
});

/* 7. 笔记 */
test('编辑笔记会写入本机存储，并更新行内标记与概览', () => {
  const id = rows()[1].getAttribute('data-id');
  click(rows()[1].querySelector('.entry__open'));
  const ta = $('#note-input');
  assert(ta, '没有笔记输入框');
  ta.value = '冒烟测试写入的笔记';
  fire(ta, 'input');
  assert($('#note-count').textContent.includes('9 字'), '字数 ' + $('#note-count').textContent);
  assert($('[data-note-reset]').hidden === false, '恢复按钮未出现');
  flush();
  const stored = JSON.parse(env.window._store.get('papertrail.notes.v1'));
  assert(stored[id] === '冒烟测试写入的笔记', '未写入 localStorage: ' + env.window._store.get('papertrail.notes.v1'));
  const row = $('#papers .entry[data-id="' + id + '"]');
  assert(row.querySelector('.entry__note').textContent.includes('有笔记'), '行内笔记标记未更新');
  assert($('#rail-notes').textContent.includes('冒烟测试') === false, '概览不应显示笔记正文');
  click($('[data-note-reset]'));
  assert($('#note-input').value.length > 0, '恢复原文后不应为空');
  assert(!JSON.parse(env.window._store.get('papertrail.notes.v1'))[id], '恢复后仍占用存储覆盖');
});

test('关闭抽屉时会提交尚未计时完成的笔记', () => {
  if ($('#drawer').classList.contains('is-open')) key(doc.activeElement, 'Escape');
  const id = rows()[0].getAttribute('data-id');
  click(rows()[0].querySelector('.entry__open'));
  const ta = $('#note-input');
  ta.value = '关闭时提交的草稿';
  fire(ta, 'input');
  click($('#close-drawer'));
  const stored = JSON.parse(env.window._store.get('papertrail.notes.v1') || '{}');
  assert(stored[id] === '关闭时提交的草稿', '关抽屉时未提交: ' + JSON.stringify(stored));
});

test('抽屉内 Tab 循环，焦点不会跑出对话框', () => {
  click(rows()[2].querySelector('.entry__open'));
  const focusables = $$('#drawer button, #drawer textarea').filter((el) => !el.disabled);
  assert(focusables.length > 2, '可聚焦元素太少');
  const last = focusables[focusables.length - 1];
  last.focus();
  key(last, 'Tab');
  assert(doc.activeElement === focusables[0], 'Tab 未从末尾回到开头');
  key(doc.activeElement, 'Tab', { shiftKey: true });
  assert(doc.activeElement === last, 'Shift+Tab 未从开头回到末尾');
});

test('点击遮罩关闭抽屉', () => {
  if (!$('#drawer').classList.contains('is-open')) click(rows()[0].querySelector('.entry__open'));
  click($('#scrim'));
  assert(!$('#drawer').classList.contains('is-open'), '遮罩点击未关闭');
});

test('Esc 关闭抽屉并把焦点还给触发元素', () => {
  if ($('#drawer').classList.contains('is-open')) key(doc.activeElement, 'Escape');
  const trigger = rows()[3].querySelector('[data-open]');
  click(trigger);
  assert($('#drawer').classList.contains('is-open'), '抽屉未打开');
  key(doc.activeElement, 'Escape');
  assert(!$('#drawer').classList.contains('is-open'), '抽屉未关闭');
  assert($('#drawer').hasAttribute('inert'), '关闭后未设回 inert');
  assert(doc.activeElement === trigger, '焦点没有回到触发元素');
  assert($('#scrim').classList.contains('is-open') === false, '遮罩未收起');
});

/* 8. 键盘 */
test('按 / 聚焦搜索，按 j/k 在条目间移动', () => {
  key(doc.body, '/');
  assert(doc.activeElement === $('#search'), '/ 未聚焦搜索');
  key(doc.activeElement, 'Escape');
  key(doc.body, 'j');
  assert(doc.activeElement === rows()[0].querySelector('.entry__open'), 'j 未移到首条');
  key(doc.activeElement, 'j');
  assert(doc.activeElement === rows()[1].querySelector('.entry__open'), 'j 未移到第二条');
  key(doc.activeElement, 'k');
  assert(doc.activeElement === rows()[0].querySelector('.entry__open'), 'k 未回到首条');
  key(doc.activeElement, 'ArrowDown');
  assert(doc.activeElement === rows()[1].querySelector('.entry__open'), '方向键未生效');
});

test('菜单内方向键与 Esc 可操作', () => {
  const btn = rows()[0].querySelector('[data-menu-btn]');
  click(btn);
  const menu = doc.getElementById(btn.getAttribute('aria-controls'));
  assert(doc.activeElement === menu.querySelector('[aria-checked="true"]'), '焦点不在当前状态');
  key(doc.activeElement, 'ArrowDown');
  assert(menu.querySelectorAll('[role="menuitemradio"]').indexOf(doc.activeElement) >= 0, '方向键离开了菜单');
  key(doc.activeElement, 'Escape');
  assert(menu.hidden === true, 'Esc 未关闭菜单');
  assert(doc.activeElement === btn, 'Esc 后焦点未回到按钮');
});

test('快捷键面板可开可关，Esc 可退出', () => {
  const btn = $('#shortcut-btn');
  click(btn);
  assert($('#shortcut-panel').hidden === false, '面板未打开');
  assert(btn.getAttribute('aria-expanded') === 'true', 'aria-expanded 未同步');
  key(doc.body, 'Escape');
  assert($('#shortcut-panel').hidden === true, 'Esc 未关闭面板');
});

/* 9. 复制引用（无剪贴板权限时的兜底路径） */
test('复制按钮在无剪贴板时给出真实失败提示', () => {
  click(rows()[0].querySelector('.entry__open'));
  click($('[data-copy]'));
  assert($('#toast').textContent.includes('不允许自动复制'), '提示不真实: ' + $('#toast').textContent);
  key(doc.activeElement, 'Escape');
  flush();
});

/* 10. 重置 */
test('恢复演示初始状态会还原状态与笔记', () => {
  click($('#status-filters [data-status="read"]'));
  assert(rows().length > 0, '已读筛选应至少有 1 条');
  click($('#reset-all'));
  assert(rows().length === 9, '重置后 ' + rows().length);
  assert($('#status-filters [data-count="unread"]').textContent === '4', '未读计数未恢复');
  assert($('#status-filters [data-count="read"]').textContent === '3', '已读计数未恢复');
  assert($('#status-filters [data-status="all"]').getAttribute('aria-pressed') === 'true', '筛选未复位');
  assert(!env.window._store.get('papertrail.notes.v1'), '笔记存储未清除');
  assert($('#toast').textContent.includes('恢复'), '缺少恢复提示');
});

/* --------------------------------------------------------------- 输出 --- */

results.forEach((r) => console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.detail ? '\n      ' + r.detail : ''}`));
const failed = results.filter((r) => !r.pass).length;
console.log(`\n${results.length - failed}/${results.length} 项通过（最小 DOM 桩，非浏览器）`);
process.exit(failed ? 1 : 0);
