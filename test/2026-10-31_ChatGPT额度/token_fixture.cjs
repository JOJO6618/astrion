#!/usr/bin/env node
/**
 * ChatGPT 额度回归测试共享 fixture。
 *
 * 提供：真实 InputComposer.vue script 提取、去类型后的交互工厂、最小 Vue
 * renderer 与 document/window/timer/emit/composable stub。断言与用例在
 * token_interaction.cjs / token_card.cjs 中，本文件不包含业务实现副本。
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { ref, computed, watch, onBeforeUnmount, createRenderer } = require('vue');
const { parse } = require('@vue/compiler-sfc');

const ROOT = path.resolve(__dirname, '../..');
const COMPOSER_PATH = path.join(ROOT, 'static/src/components/input/InputComposer.vue');
const CARD_PATH = path.join(ROOT, 'static/src/components/token/ChatgptQuotaCard.vue');

const pass = (label) => console.log(`  ✓ ${label}`);

// ─────────────────────────────────────────────────────────────────────────────
// 一、真实 script 提取
// ─────────────────────────────────────────────────────────────────────────────

function readSfc(file) {
  const source = fs.readFileSync(file, 'utf8');
  const { descriptor, errors } = parse(source, { filename: file });
  assert.deepEqual(errors, [], `SFC 解析失败: ${file}`);
  assert.ok(descriptor.scriptSetup, `缺少 script setup: ${file}`);
  return descriptor;
}

const composer = readSfc(COMPOSER_PATH);
const composerScript = composer.scriptSetup.content;

const BLOCK_START = 'const quotaSwitcherEl = ref<HTMLElement | null>(null);';
const BLOCK_END = 'defineExpose({';
const blockStart = composerScript.indexOf(BLOCK_START);
const blockEnd = composerScript.indexOf(BLOCK_END, blockStart);
assert.ok(blockStart >= 0, 'InputComposer 中找不到额度交互块起点（quotaSwitcherEl）');
assert.ok(blockEnd > blockStart, 'InputComposer 中找不到额度交互块终点（defineExpose）');
const quotaBlock = composerScript.slice(blockStart, blockEnd);

assert.ok(quotaBlock.includes('useChatgptQuota('), '提取块必须真实调用共享 useChatgptQuota');
assert.ok(quotaBlock.includes('handleQuotaTriggerClick'), '提取块必须包含真实点击处理');
assert.ok(quotaBlock.includes('addEventListener'), '提取块必须包含真实外部点击监听');

// 去类型后作为函数体执行：不重写实现，只补参数与返回值导出。
const quotaBlockJs = ts.transpileModule(quotaBlock, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.CommonJS
  }
}).outputText;

const quotaFactory = new Function(
  'ref',
  'computed',
  'watch',
  'onBeforeUnmount',
  'props',
  'emit',
  'document',
  'window',
  'useChatgptQuota',
  'setTimeout',
  'clearTimeout',
  `${quotaBlockJs}
return {
  quotaSwitcherEl,
  quotaCardOpen,
  quotaCardPinned,
  chatgptQuotaVisible,
  handleQuotaPointerEnter,
  handleQuotaPointerLeave,
  handleQuotaTriggerClick,
  handleQuotaFocusIn,
  handleQuotaFocusOut,
  closeQuotaCard
};`
);

// ─────────────────────────────────────────────────────────────────────────────
// 二、最小 renderer + 全套 stub
// ─────────────────────────────────────────────────────────────────────────────

const nodeOps = {
  insert() {},
  remove() {},
  createElement() {
    return {};
  },
  createText() {
    return {};
  },
  createComment() {
    return {};
  },
  setText() {},
  setElementText() {},
  parentNode() {
    return null;
  },
  nextSibling() {
    return null;
  },
  querySelector() {
    return null;
  },
  setScopeId() {},
  cloneNode(node) {
    return node;
  },
  insertStaticContent() {
    return [null, null];
  }
};

function createHarness(options = {}) {
  const state = {
    coarse: options.coarse === true,
    chatMode: options.chatMode === true
  };
  const isAdmin = ref(options.isAdmin !== false);
  const model = options.model || {
    key: 'codex/gpt-5',
    label: 'GPT-5',
    description: '',
    isChatgptModel: true,
    providerId: 'acct-1'
  };
  const props = {
    modelOptions: [model],
    currentModelKey: model.key,
    chatMode: state.chatMode
  };

  const emitted = [];
  const emit = (name) => emitted.push(name);

  // document stub：记录 add/remove（含 capture 标记），可手动派发。
  const listeners = new Map();
  const listenerKey = (type, capture) => `${type}:${capture ? 'capture' : 'bubble'}`;
  const doc = {
    activeElement: null,
    addEventListener(type, fn, capture) {
      const key = listenerKey(type, capture);
      if (!listeners.has(key)) listeners.set(key, new Set());
      listeners.get(key).add(fn);
    },
    removeEventListener(type, fn, capture) {
      listeners.get(listenerKey(type, capture))?.delete(fn);
    },
    dispatch(type, event) {
      for (const fn of [...(listeners.get(listenerKey(type, true)) || [])]) fn(event);
      for (const fn of [...(listeners.get(listenerKey(type, false)) || [])]) fn(event);
    },
    count(type, capture) {
      return (listeners.get(listenerKey(type, capture)) || new Set()).size;
    }
  };

  // timer stub：可查询待执行定时器、可手动触发。
  let timerSeq = 0;
  const timers = new Map();
  const setTimeoutStub = (fn, ms) => {
    const id = ++timerSeq;
    timers.set(id, { fn, ms });
    return id;
  };
  const clearTimeoutStub = (id) => {
    timers.delete(id);
  };
  const runTimers = () => {
    const list = [...timers.values()];
    timers.clear();
    for (const timer of list) timer.fn();
  };

  const win = {
    matchMedia(query) {
      assert.ok(
        query.includes('hover: none') || query.includes('pointer: coarse'),
        `matchMedia 查询意外: ${query}`
      );
      return { matches: state.coarse };
    }
  };

  // 共享 composable stub（按最新契约）：
  // applicable 仅表示「管理员可查询」，账号定位由 composable 内部完成。
  const quota = {
    accountHash: ref('acct-1'),
    windows: ref(
      options.windows || [
        { key: 'primary_window', label: '5 小时窗口', percent: 42, resetText: '10/31 20:30' },
        { key: 'secondary_window', label: '每周窗口', percent: 81, resetText: '11/7 09:00' }
      ]
    ),
    loading: ref(options.loading === true),
    error: ref(options.error || ''),
    // 新契约：applicable 只表示「管理员可查询」，与当前模型/是否有账号无关。
    applicable: computed(() => isAdmin.value),
    activeCalls: []
  };
  let activeGetter = () => false;
  function useChatgptQuota(modelGetter, active) {
    quota.modelGetter = modelGetter;
    activeGetter = () => {
      const value = active();
      quota.activeCalls.push(value);
      return value;
    };
    return {
      accountHash: quota.accountHash,
      windows: quota.windows,
      loading: quota.loading,
      error: quota.error,
      applicable: quota.applicable,
      refresh: () => Promise.resolve()
    };
  }

  const renderer = createRenderer(nodeOps);
  let api;
  const component = {
    name: 'QuotaInteractionHarness',
    setup() {
      api = quotaFactory(
        ref,
        computed,
        watch,
        onBeforeUnmount,
        props,
        emit,
        doc,
        win,
        useChatgptQuota,
        setTimeoutStub,
        clearTimeoutStub
      );
      return () => null;
    }
  };
  const app = renderer.createApp(component);
  const container = { nodeType: 1, querySelector: () => null, _vnode: null };
  app.mount(container);

  return {
    api,
    app,
    state,
    props,
    emitted,
    doc,
    timers,
    quota,
    isAdmin,
    model,
    runTimers,
    active: () => activeGetter(),
    setCoarse(value) {
      state.coarse = value;
    },
    unmount() {
      app.unmount();
    }
  };
}

module.exports = {
  assert,
  composer,
  quotaFactory,
  createHarness,
  pass,
  readSfc,
  CARD_PATH
};
