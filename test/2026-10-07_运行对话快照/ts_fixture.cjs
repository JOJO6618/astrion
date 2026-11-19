const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');
const noop = () => {};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
};
function compile(file, imports = {}, globals = {}) {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText;
  vm.runInNewContext(code, {
    module, exports: module.exports, AbortController, AbortSignal, Promise, Map, Set, WeakMap,
    setTimeout: noop, clearTimeout: noop, setInterval: noop, clearInterval: noop,
    Date, console: { log: noop, warn: noop, error: noop },
    require: (key) => {
      if (Object.hasOwn(imports, key)) return imports[key];
      if (['@/utils/generatingLabel', '@/utils/taskErrorToast'].includes(key)) {
        return compile(`static/src/utils/${key.split('/').pop()}.ts`, { '@/locales': imports['@/locales'] || locale }, globals);
      }
      // 桌面多视图可见性：测试默认恒可见（轮询/刷新行为同网页端）
      if (key.endsWith('/viewVisibility')) return {
        isViewVisible: () => true, onViewVisibilityChange: () => noop, setViewVisible: noop,
        pollingDelayMs: () => 250, HIDDEN_POLL_MS: 1000, VISIBLE_POLL_MS: 250
      };
      // 未显式 mock 时标签 store 视为网页端（未启用）
      if (key.endsWith('/stores/conversationTabs')) return { useConversationTabsStore: () => ({ enabled: false }) };
      if (key === '@/stores/browserTools') return { useBrowserToolsStore: () => ({ reset: noop, $patch: noop }) };
      if (key === '@/stores/gitWorktree') return { useGitWorktreeStore: () => ({ reset: noop, $patch: noop, hydrate: noop }) };
      if (key === './draftStorage') return compile('static/src/app/methods/ui/draftStorage.ts', {}, globals);
      if (key.endsWith('/workspaceRequestScope')) return {
        bindWorkspaceRequestHost: noop, workspaceScopedFetch: (...args) => globals.fetch(...args)
      };
      if (key.endsWith('/workspaceConversationCache') || key === './workspaceConversationCache') {
        return compile('static/src/stores/workspaceConversationCache.ts', {}, globals);
      }
      throw new Error(`Unmocked dependency ${file}: ${key}`);
    }, ...globals
  }, { filename: file });
  return module.exports;
}
const pinia = {
  defineStore: (_id, options) => {
    let store;
    return () => {
      if (store) return store;
      store = { ...options.state() };
      for (const [key, value] of Object.entries(options.actions || {})) {
        store[key] = value.bind(store);
      }
      for (const [key, value] of Object.entries(options.getters || {})) {
        Object.defineProperty(store, key, { get: () => value.call(store, store) });
      }
      return store;
    };
  }
};
const locale = { t: (key) => key };
const visibility = { getMessageVisibility: () => 'chat', messageStartsWork: () => false };
module.exports = { compile, deferred, noop, pinia, locale, visibility };
