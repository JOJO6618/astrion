const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const vue = require('vue');
const repo = path.resolve(__dirname, '../..');

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, handler) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(handler);
  }
  removeEventListener(name, handler) { this.listeners.get(name)?.delete(handler); }
  fire(name, data = {}) {
    const event = { target: this, isTrusted: true, ...data };
    for (const handler of [...(this.listeners.get(name) || [])]) handler(event);
  }
  count() { return [...this.listeners.values()].reduce((n, handlers) => n + handlers.size, 0); }
}

class Element extends Events {
  constructor(document = new Events()) {
    super();
    this.ownerDocument = document;
    this.scrollHeight = 1000;
    this.clientHeight = 200;
    this.offsetHeight = 200;
    this.clientWidth = 300;
    this.offsetWidth = 310;
    this.isConnected = true;
    this.parentElement = null;
    this.firstElementChild = null;
    this.top = 800;
  }
  get scrollTop() { return this.top; }
  set scrollTop(value) { this.top = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); }
  getBoundingClientRect() { return { right: 310 }; }
  closest() { return null; }
  contains(node) {
    while (node) { if (node === this) return true; node = node.parentElement; }
    return false;
  }
}

class Observer {
  static instances = [];
  constructor(callback) { this.callback = callback; this.targets = new Set(); Observer.instances.push(this); }
  observe(el) { this.targets.add(el); }
  unobserve(el) { this.targets.delete(el); }
  disconnect() { this.targets.clear(); }
  notify() { this.callback([]); }
}

global.HTMLElement = Element;
global.Element = Element;
global.ResizeObserver = Observer;
global.getComputedStyle = () => ({ overflowY: 'auto' });

function compile(source, imports = {}) {
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS }
  }).outputText;
  const mod = { exports: {} };
  new Function('module', 'exports', 'require', output)(mod, mod.exports, (name) => {
    if (name === 'vue') return vue;
    if (imports[name]) return imports[name];
    throw new Error(`Unexpected dependency: ${name}`);
  });
  return mod.exports;
}
const followModule = compile(fs.readFileSync(path.join(repo, 'static/src/composables/elementScrollFollow.ts'), 'utf8'));
const scrollModule = compile(fs.readFileSync(path.join(repo, 'static/src/composables/useScrollControl.ts'), 'utf8'), {
  './elementScrollFollow': followModule
});

async function flush() { await vue.nextTick(); await Promise.resolve(); await vue.nextTick(); }

function mainList() {
  const raw = fs.readFileSync(path.join(repo, 'static/src/components/chat/ChatArea.vue'), 'utf8');
  const script = raw.split('<script setup lang="ts">')[1].split('</script>')[0];
  const ast = ts.createSourceFile('chat.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const names = ['attachBounceListener', 'setupContentResizeObserver', 'requestFollowWrite'];
  const functions = ast.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text))
    .map((node) => node.getText(ast)).join('\n');
  const setup = `
    const el = new Element();
    const spacer = new Element(el.ownerDocument); spacer.offsetHeight = 300;
    const content = new Element(el.ownerDocument);
    const scrollRef = { value: el }, contentRef = { value: content }, bottomSpacerRef = { value: spacer };
    const followState = { value: 'locked' }, escapedFromLock = { value: false };
    const isAtBottom = { value: true }, isNearBottom = { value: true };
    const pendingMinimalCollapses = new Map();
    let contentResizeObserver = null, followWriteQueued = false, followWriteSource = '';
    let lastObservedTop = 0, lastObservedHeight = 0, lastObservedClientHeight = 0, lastObservedSpacerHeight = 0;
    let scrollListener = null, wheelListener = null, scrollbarPointerListener = null, keyboardScrollListener = null;
    let touchStartListener = null, touchMoveListener = null, touchEndListener = null;
    let touchScrollStartY = 0, touchEscapeFired = false, lastUserScrollInputTs = 0;
    let lastUserDownScrollTs = 0, lastProgrammaticHintTs = 0, lastProgrammaticHintSource = '';
    let traceAttachLogged = false, quickNavJumpActive = false, relockAnimRaf = null;
    const ESCAPE_LEAVE_BOTTOM_PX = 8, RELOCK_BOTTOM_THRESHOLD_PX = 100;
    const noop = () => {};
    const syncStickState = noop, stopBlockExpansionAnchors = noop, cancelQuickNavJump = noop;
    const updateQuickNavCurrent = noop, bounceTraceLog = noop, emit = noop;
    const isNestedScrollableTarget = (target) => target !== el;
    const isScrollBounceTraceEnabled = () => false;
    const getScrollMetrics = () => ({});
    const tryRelockAfterMinimalCollapse = noop;
    const markProgrammaticHint = (source) => { lastProgrammaticHintTs = Date.now(); lastProgrammaticHintSource = source; };
    const escapeFollowLock = () => { followState.value = 'escaped'; };
    const relockFollow = () => { followState.value = 'locked'; };
    ${functions}
    attachBounceListener(); setupContentResizeObserver();
    return { el, spacer, content, observer: contentResizeObserver, followState,
      clearHint() { lastProgrammaticHintTs = 0; },
      read: () => followState.value,
      move(top, height = el.scrollHeight, client = el.clientHeight) {
        el.scrollHeight = height; el.clientHeight = client; el.scrollTop = top; el.fire('scroll');
      }
    };
  `;
  return new Function('Element', 'ResizeObserver', 'nextTick', ts.transpileModule(setup, {
    compilerOptions: { target: ts.ScriptTarget.ES2020 }
  }).outputText)(Element, Observer, vue.nextTick);
}

function minimalGroup() {
  const raw = fs.readFileSync(path.join(repo, 'static/src/components/chat/MinimalBlocks.vue'), 'utf8');
  const script = raw.split('<script setup lang="ts">')[1].split('</script>')[0];
  const ast = ts.createSourceFile('minimal.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const declaration = ast.statements.find((node) => ts.isVariableStatement(node) &&
    node.declarationList.declarations.some((item) => item.name.getText(ast) === 'registerStepsWrapper'));
  const setup = `
    const stepsWrapperRefs = new Map(), stepsWrapperFollowers = new Map();
    const expandedGroups = { value: new Set(['group']) };
    const blockGroups = { value: [{ id: 'group', actions: [] }] };
    let running = true;
    const isSummaryRunning = () => running;
    ${declaration.getText(ast)}
    return { registerStepsWrapper, stepsWrapperFollowers, setRunning(value) { running = value; } };
  `;
  return new Function('createElementScrollFollow', ts.transpileModule(setup, {
    compilerOptions: { target: ts.ScriptTarget.ES2020 }
  }).outputText)(followModule.createElementScrollFollow);
}

module.exports = { Element, Observer, flush, mainList, minimalGroup, ...followModule, ...scrollModule };
