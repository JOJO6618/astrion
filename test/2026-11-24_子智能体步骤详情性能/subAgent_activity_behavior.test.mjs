/**
 * 子智能体活动：真实 Pinia store + 真实 RunnerDetailPanel 组件行为测试。
 *
 * 不启动浏览器、不改生产代码：
 * - Pinia store 由 Vite SSR 加载真实 TS 源码；
 * - RunnerDetailPanel.vue 由 @vue/compiler-sfc 以 client 模式编译，
 *   挂到 @vue/runtime-core 的轻量宿主节点上；
 * - fetch/定时器为测试替身，网络延迟由 deferred 控制。
 *
 * 运行：
 *   node --test test/2026-11-24_子智能体步骤详情性能/subAgent_activity_behavior.test.mjs
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { after, test } from 'node:test';

import { parse, compileScript } from '@vue/compiler-sfc';
import { transform } from 'esbuild';
import { createPinia, setActivePinia } from 'pinia';
import { createRenderer, defineComponent, h, nextTick } from 'vue';
import { createServer } from 'vite';

const ROOT = process.cwd();
const AGENT = { task_id: 'sub_a', status: 'running' };
const AGENT_B = { task_id: 'sub_b', status: 'running' };

// ---- 浏览器外壳替身：真实 store/组件只依赖这些全局 ----
const storage = new Map();
globalThis.window = {
  location: {
    pathname: '/c/conv_1',
    href: 'http://localhost/c/conv_1',
    origin: 'http://localhost',
    protocol: 'http:',
    hostname: 'localhost'
  },
  innerWidth: 1200,
  performance: { now: () => Date.now() },
  localStorage: {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value))
  },
  matchMedia: () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {}
  })
};
globalThis.location = window.location;
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};

// 定时器替身：store 轮询与组件离开动画都由测试显式触发。
const timers = [];
globalThis.setTimeout = (fn, ms) => {
  const timer = { fn, ms, cleared: false };
  timers.push(timer);
  return timer;
};
globalThis.clearTimeout = (timer) => {
  if (timer) timer.cleared = true;
};

function runTimer(ms) {
  const timer = timers.find((item) => !item.cleared && item.ms === ms);
  assert.ok(timer, `no pending ${ms}ms timer; queued=${JSON.stringify(timers.map((t) => t.ms))}`);
  timer.cleared = true;
  return timer.fn();
}

const server = await createServer({
  root: ROOT,
  configFile: path.join(ROOT, 'vite.config.ts'),
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error'
});

const conversationMod = await server.ssrLoadModule('/static/src/stores/conversation.ts');
const subAgentMod = await server.ssrLoadModule('/static/src/stores/subAgent.ts');
const quickDockMod = await server.ssrLoadModule('/static/src/stores/quickDock.ts');
const backgroundMod = await server.ssrLoadModule('/static/src/stores/backgroundCommand.ts');
const uiMod = await server.ssrLoadModule('/static/src/stores/ui.ts');
const localesMod = await server.ssrLoadModule('/static/src/locales/index.ts');
const toolTextMod = await server.ssrLoadModule('/static/src/components/chat/quickdock/subAgentToolText.ts');

let Runner = null;

async function compileRunner() {
  const filename = path.join(
    ROOT,
    'static/src/components/chat/quickdock/RunnerDetailPanel.vue'
  );
  const { descriptor } = parse(readFileSync(filename, 'utf8'), { filename });
  const compiled = compileScript(descriptor, {
    id: 'runner-detail-test',
    inlineTemplate: true,
    ssr: false
  });
  // compileScript 输出仍是 TS：先转 JS，再替换 ESM import 为依赖注入。
  let js = (await transform(compiled.content, {
    loader: 'ts',
    format: 'esm',
    target: 'es2020'
  })).code;
  js = js.replace(
    /^import\s+\{([^}]*)\}\s+from\s+['"]([^'"]+)['"];?/gm,
    (_, names, spec) =>
      `const { ${names
        .split(',')
        .map((name) => name.trim().replace(/\s+as\s+/, ': '))
        .join(', ')} } = __deps[${JSON.stringify(spec)}];`
  );
  js = js.replace(
    /^import\s+([A-Za-z_$][\w$]*)\s+from\s+['"]([^'"]+)['"];?/gm,
    (_, name, spec) => `const ${name} = __deps[${JSON.stringify(spec)}].default;`
  );
  js = js
    .replace(/export\s*\{\s*stdin_default as default\s*\};?/, 'return stdin_default;')
    .replace(/export default/, 'return');
  assert.match(js, /return stdin_default;/, 'compiled component export was not rewritten');
  const deps = {
    vue: await import('vue'),
    pinia: await import('pinia'),
    '@/locales': localesMod,
    '@/stores/quickDock': quickDockMod,
    '@/stores/subAgent': subAgentMod,
    '@/stores/backgroundCommand': backgroundMod,
    '@/stores/ui': uiMod,
    '@/components/common/CloseButton.vue': {
      default: defineComponent({ render: () => h('button') })
    },
    '@/composables/useSubAgentActivityScroll': await server.ssrLoadModule(
      '/static/src/composables/useSubAgentActivityScroll.ts'
    ),
    './subAgentToolText': toolTextMod
  };
  return new Function('__deps', js)(deps);
}

Runner = await compileRunner();

after(async () => {
  await server.close();
});

// ---------------------------------------------------------------------------
// 轻量 DOM 宿主
// ---------------------------------------------------------------------------
function makeNode(tag) {
  const node = {
    tag,
    props: {},
    text: '',
    parent: null,
    firstChild: null,
    lastChild: null,
    next: null,
    prev: null
  };
  Object.defineProperty(node, 'parentElement', { get: () => node.parent });
  node.getBoundingClientRect = () => ({ left: 0, top: 0, width: 294, height: 400 });
  node.scrollTo = () => {};
  node.scrollHeight = 0;
  node.scrollTop = 0;
  node.clientHeight = 0;
  return node;
}

function appendChild(parent, child) {
  child.parent = parent;
  child.prev = parent.lastChild;
  child.next = null;
  if (parent.lastChild) parent.lastChild.next = child;
  else parent.firstChild = child;
  parent.lastChild = child;
}

function insertBefore(parent, child, anchor) {
  child.parent = parent;
  child.next = anchor;
  child.prev = anchor.prev;
  if (anchor.prev) anchor.prev.next = child;
  else parent.firstChild = child;
  anchor.prev = child;
}

const bodyTarget = makeNode('body');
const renderer = createRenderer({
  createElement: makeNode,
  createElementNS: (_ns, tag) => makeNode(tag),
  createText: (text) => ({ ...makeNode('#text'), text: String(text) }),
  createComment: (text) => ({ ...makeNode('#comment'), text: String(text) }),
  insert: (child, parent, anchor = null) => {
    if (anchor) insertBefore(parent, child, anchor);
    else appendChild(parent, child);
  },
  remove: (child) => {
    const parent = child.parent;
    if (!parent) return;
    if (child.prev) child.prev.next = child.next;
    else parent.firstChild = child.next;
    if (child.next) child.next.prev = child.prev;
    else parent.lastChild = child.prev;
    child.parent = child.prev = child.next = null;
  },
  parentNode: (node) => node.parent || null,
  nextSibling: (node) => node.next || null,
  setElementText: (node, text) => {
    node.text = String(text);
    node.firstChild = node.lastChild = null;
  },
  setText: (node, text) => {
    node.text = String(text);
  },
  patchProp: (node, key, _prev, next) => {
    node.props[key] = next;
  },
  setScopeId: () => {},
  cloneNode: (node) => ({ ...node }),
  insertStaticContent: () => [makeNode('div'), makeNode('div')],
  querySelector: () => bodyTarget
});

function classesOf(node) {
  const value = node.props?.class;
  if (Array.isArray(value)) return value.flatMap((item) => String(item).split(/\s+/));
  return String(value || '').split(/\s+/).filter(Boolean);
}

function collectRows(node, rows = []) {
  for (let child = node.firstChild; child; child = child.next) {
    if (classesOf(child).includes('qd-feed-row')) rows.push(child);
    collectRows(child, rows);
  }
  return rows;
}

function nodeText(node) {
  let text = node.text || '';
  for (let child = node.firstChild; child; child = child.next) text += nodeText(child);
  return text;
}

// ---------------------------------------------------------------------------
// fetch / store 夹具
// ---------------------------------------------------------------------------
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function response(data) {
  return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
}

function installFetch(handler) {
  const calls = [];
  globalThis.fetch = (url, init) => {
    const call = { url: String(url), init };
    calls.push(call);
    return handler(call);
  };
  return calls;
}

function taskFromUrl(url) {
  return new URL(url, 'http://localhost').pathname.split('/')[3];
}

function queryFromUrl(url) {
  return new URL(url, 'http://localhost').searchParams;
}

function freshStores() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const conversation = conversationMod.useConversationStore();
  conversation.currentConversationId = 'conv_1';
  return {
    pinia,
    conversation,
    sub: subAgentMod.useSubAgentStore(),
    quick: quickDockMod.useQuickDockStore()
  };
}

async function flush(times = 4) {
  for (let index = 0; index < times; index += 1) {
    await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
    await nextTick();
  }
}

function mountRunner({ pinia, sub, quick, fetchHandler, agent = AGENT }) {
  installFetch(fetchHandler);
  sub.subAgents = [AGENT, AGENT_B];
  quick.openDetail('agent', agent.task_id);
  const app = renderer.createApp(Runner);
  app.use(pinia);
  app.config.globalProperties.$t = (key) => key;
  const root = makeNode('root');
  app.mount(root);
  return { app, root };
}

function entries(start, count, prefix = 'step') {
  return Array.from({ length: count }, (_, index) => ({
    type: 'progress',
    subtype: 'output',
    content: `${prefix}-${start + index}`,
    ts: start + index
  }));
}

function activity(entriesList, start, next, extra = {}) {
  return {
    success: true,
    data: {
      task_id: 'sub_a',
      status: 'running',
      entries: entriesList,
      start_offset: start,
      next_offset: next,
      reset: false,
      truncated: false,
      has_more_before: false,
      has_more_after: false,
      partial_tail: false,
      ...extra
    }
  };
}

// 编译已在顶层 await 完成（Runner）。

test('slow activity request stays single-flight and only applies once', async () => {
  const { sub } = freshStores();
  const pending = [];
  const calls = installFetch(() => {
    const item = deferred();
    pending.push(item);
    return item.promise;
  });
  sub.subAgents = [AGENT];
  sub.openSubAgent(AGENT);
  await flush();
  assert.equal(calls.length, 1, 'initial open must issue exactly one request');
  assert.equal(sub.activityLoading, true, 'slow request must keep loading true');

  await sub.fetchSubAgentActivity(AGENT.task_id);
  await flush();
  assert.equal(calls.length, 1, 'second call while in-flight must be ignored');

  pending[0].resolve(response(activity(entries(0, 3), 0, 3)));
  await flush();
  assert.equal(sub.activityEntries.length, 3, JSON.stringify(sub.activityEntries));
  assert.equal(sub.activityLoading, false);
  assert.equal(calls.length, 1);
  sub.closeSubAgent();
});

test('switching agents discards the old agent late response', async () => {
  const { sub } = freshStores();
  const pending = new Map();
  installFetch((call) => {
    const taskId = taskFromUrl(call.url);
    const item = deferred();
    pending.set(taskId, item);
    return item.promise;
  });
  sub.subAgents = [AGENT, AGENT_B];
  sub.openSubAgent(AGENT);
  await flush();
  assert.ok(pending.has('sub_a'));

  sub.openSubAgent(AGENT_B);
  await flush();
  assert.ok(pending.has('sub_b'));

  pending.get('sub_a').resolve(response(activity(entries(0, 2, 'A'), 0, 2)));
  await flush();
  assert.equal(sub.activeAgent?.task_id, 'sub_b');
  assert.equal(sub.activityEntries.length, 0, JSON.stringify(sub.activityEntries));

  pending.get('sub_b').resolve(response(activity(entries(0, 2, 'B'), 0, 2)));
  await flush();
  assert.deepEqual(
    sub.activityEntries.map((entry) => entry.content),
    ['B-0', 'B-1']
  );
  sub.closeSubAgent();
});

test('closing aborts the in-flight request and ignores its late response', async () => {
  const { sub } = freshStores();
  const pending = [];
  const calls = installFetch(() => {
    const item = deferred();
    pending.push(item);
    return item.promise;
  });
  sub.subAgents = [AGENT];
  sub.openSubAgent(AGENT);
  await flush();
  assert.equal(calls[0].init.signal.aborted, false);

  sub.closeSubAgent();
  assert.equal(calls[0].init.signal.aborted, true, 'close must abort the activity request');
  pending[0].resolve(response(activity(entries(0, 3), 0, 3, { status: 'completed' })));
  await flush();

  assert.equal(sub.activeAgent, null);
  assert.equal(sub.activityEntries.length, 0, JSON.stringify(sub.activityEntries));
  assert.equal(sub.activityLoading, false);
});

test('conversation switch invalidates an in-flight response', async () => {
  const { sub, conversation } = freshStores();
  const pending = [];
  installFetch(() => {
    const item = deferred();
    pending.push(item);
    return item.promise;
  });
  sub.subAgents = [AGENT];
  sub.openSubAgent(AGENT);
  await flush();

  conversation.currentConversationId = 'conv_2';
  pending[0].resolve(response(activity(entries(0, 3), 0, 3)));
  await flush();
  assert.equal(sub.activityEntries.length, 0, JSON.stringify(sub.activityEntries));
  sub.closeSubAgent();
});

test('older and live batches extend the same timeline from opposite ends', async () => {
  const { sub } = freshStores();
  installFetch((call) => {
    const params = queryFromUrl(call.url);
    if (params.has('before')) {
      return Promise.resolve(response(activity(entries(0, 200, 'old'), 0, 200, {
        has_more_before: false
      })));
    }
    if (params.has('offset')) {
      return Promise.resolve(response(activity(entries(400, 10, 'new'), 400, 410, {
        has_more_before: false
      })));
    }
    return Promise.resolve(response(activity(entries(200, 200, 'live'), 200, 400, {
      has_more_before: true
    })));
  });
  sub.subAgents = [AGENT];
  sub.openSubAgent(AGENT);
  await flush();
  assert.equal(sub.liveOffset, 400);
  assert.equal(sub.activityEntries.length, 200);

  await sub.fetchSubAgentActivity(AGENT.task_id, 'older');
  await flush();
  assert.equal(sub.activityEntries.length, 400);
  assert.equal(sub.activityStart, 0);
  assert.equal(sub.activityEntries[200].content, 'live-200');
  assert.equal(sub.activityEntries[0].content, 'old-0');

  await runTimer(2000);
  await flush();
  assert.equal(sub.liveOffset, 410);
  assert.equal(sub.livePages.length, 3);
  assert.equal(
    sub.activityEntries[0].content,
    'old-0',
    'history page stays visible while browsing history'
  );

  assert.equal(sub.activityEntries.length, 410);
  assert.equal(sub.activityEntries.at(-1).content, 'new-409');
  sub.closeSubAgent();
});

test('live pages stay capped at two pages (400 entries)', async () => {
  const { sub } = freshStores();
  installFetch((call) => {
    const offset = Number(queryFromUrl(call.url).get('offset') ?? 0);
    return Promise.resolve(
      response(activity(entries(offset, 200, 'p'), offset, offset + 200))
    );
  });
  sub.subAgents = [AGENT];
  sub.openSubAgent(AGENT);
  await flush();
  await sub.fetchSubAgentActivity(AGENT.task_id);
  await flush();
  await sub.fetchSubAgentActivity(AGENT.task_id);
  await flush();
  assert.equal(sub.livePages.length, 2, JSON.stringify(sub.livePages.map((p) => p.start)));
  assert.equal(sub.activityEntries.length, 400);
  assert.equal(sub.activityEntries[0].content, 'p-200');
  assert.equal(sub.activityEntries.at(-1).content, 'p-599');
  sub.closeSubAgent();
});

test('200k payload must not create unbounded store entries or DOM rows', async () => {
  const { pinia, sub, quick } = freshStores();
  const count = 200000;
  // store 的增量页自身以 PAGE_SIZE * 2 = 400 为界；单页/旧返回也必须遵守同一可见上限。
  const visibleLimit = 400;
  const payload = activity(entries(0, count), 0, count);
  const { app, root } = mountRunner({
    pinia,
    sub,
    quick,
    fetchHandler: async () => response(payload)
  });
  const started = Date.now();
  await flush(6);
  const rows = collectRows(root);
  const elapsed = Date.now() - started;

  assert.ok(
    sub.activityEntries.length > 0 && rows.length > 0,
    `expected some rows: storeEntries=${sub.activityEntries.length}, domRows=${rows.length}`
  );
  assert.ok(
    sub.activityEntries.length <= visibleLimit && rows.length <= visibleLimit,
    `bounded rendering failed: storeEntries=${sub.activityEntries.length}, ` +
      `domRows=${rows.length}, limit=${visibleLimit}, elapsed=${elapsed}ms`
  );
  app.unmount();
  sub.closeSubAgent();
});

test('RunnerDetailPanel normalizes a legacy payload without start_offset to one page', async () => {
  const { pinia, sub, quick } = freshStores();
  const count = 50000;
  const legacy = {
    success: true,
    data: {
      status: 'idle',
      entries: entries(0, count),
      next_offset: count
      // 旧返回没有 start_offset / has_more_* / partial_tail / truncated
    }
  };
  const { app, root } = mountRunner({
    pinia,
    sub,
    quick,
    fetchHandler: async () => response(legacy)
  });
  await flush(6);
  const rows = collectRows(root);
  assert.equal(sub.activityEntries.length, 200);
  assert.equal(rows.length, 200, `legacy rendered ${rows.length} rows`);
  assert.equal(sub.liveOffset, count);
  app.unmount();
  sub.closeSubAgent();
});

test('legacy backend rail replaces its tail page instead of duplicating it', async () => {
  const { sub } = freshStores();
  installFetch((call) => {
    const offset = queryFromUrl(call.url).get('offset');
    return Promise.resolve(
      response({
        success: true,
        data: {
          status: 'running',
          // 旧后端不支持 offset：无论传不传 offset 都返回尾部一页。
          entries: offset ? entries(100, 200, 'legacy') : entries(0, 200, 'legacy'),
          next_offset: offset ? 300 : 200
        }
      })
    );
  });
  sub.subAgents = [AGENT];
  sub.openSubAgent(AGENT);
  await flush();
  assert.equal(sub.livePages.length, 1);
  assert.equal(sub.activityEntries.length, 200);

  await runTimer(2000);
  await flush();
  assert.equal(sub.livePages.length, 1, 'legacy polling must replace, not append');
  assert.equal(sub.activityEntries.length, 200);
  assert.equal(sub.activityEntries.at(-1).content, 'legacy-299');
  assert.equal(sub.liveOffset, 300);
  sub.closeSubAgent();
});

test('legacy payload without next_offset falls back to the last page', async () => {
  const { pinia, sub, quick } = freshStores();
  const count = 200000;
  const legacy = {
    success: true,
    data: {
      status: 'idle',
      entries: entries(0, count)
      // 更旧的返回连 next_offset 都没有：store 应退化为最后一页而不是全量渲染。
    }
  };
  const { app, root } = mountRunner({
    pinia,
    sub,
    quick,
    fetchHandler: async () => response(legacy)
  });
  await flush(6);
  const rows = collectRows(root);
  assert.equal(sub.activityEntries.length, 200);
  assert.equal(rows.length, 200, `legacy no-next rendered ${rows.length} rows`);
  assert.equal(sub.liveOffset, null);
  app.unmount();
  sub.closeSubAgent();
});

test('RunnerDetailPanel switch ignores late rows from the previous agent', async () => {
  const { pinia, sub, quick } = freshStores();
  const pending = new Map();
  installFetch((call) => {
    const taskId = taskFromUrl(call.url);
    const item = deferred();
    pending.set(taskId, item);
    return item.promise;
  });
  sub.subAgents = [AGENT, AGENT_B];
  quick.openDetail('agent', 'sub_a');
  const app = renderer.createApp(Runner);
  app.use(pinia);
  app.config.globalProperties.$t = (key) => key;
  const root = makeNode('root');
  app.mount(root);
  await flush();
  assert.ok(pending.has('sub_a'));

  quick.openDetail('agent', 'sub_b');
  await flush();
  assert.ok(pending.has('sub_b'));

  pending.get('sub_a').resolve(response(activity(entries(0, 2, 'A-late'), 0, 2)));
  await flush();
  assert.equal(collectRows(root).length, 0, 'late A rows must not render under B');

  pending.get('sub_b').resolve(response(activity(entries(0, 2, 'B-new'), 0, 2)));
  await flush();
  const rows = collectRows(root);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows.map(nodeText), ['B-new-0', 'B-new-1']);
  app.unmount();
  sub.closeSubAgent();
});

test('RunnerDetailPanel close clears rows and ignores a late response', async () => {
  const { pinia, sub, quick } = freshStores();
  const pending = new Map();
  installFetch((call) => {
    const item = deferred();
    pending.set(taskFromUrl(call.url), item);
    return item.promise;
  });
  sub.subAgents = [AGENT];
  quick.openDetail('agent', 'sub_a');
  const app = renderer.createApp(Runner);
  app.use(pinia);
  app.config.globalProperties.$t = (key) => key;
  const root = makeNode('root');
  app.mount(root);
  await flush();
  assert.ok(pending.has('sub_a'));

  quick.closeDetail();
  await flush();
  await runTimer(190);
  await flush();
  assert.equal(sub.activeAgent, null);
  assert.equal(sub.activityEntries.length, 0);

  pending.get('sub_a').resolve(response(activity(entries(0, 3, 'late'), 0, 3)));
  await flush();
  assert.equal(collectRows(root).length, 0, 'late rows must stay hidden after close');
  app.unmount();
});
