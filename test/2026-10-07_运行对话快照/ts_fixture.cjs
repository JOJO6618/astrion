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
    module, exports: module.exports, AbortController, Promise, Map, Set, WeakMap,
    setTimeout: noop, clearTimeout: noop, setInterval: noop, clearInterval: noop,
    Date, console: { log: noop, warn: noop, error: noop },
    require: (key) => {
      if (Object.hasOwn(imports, key)) return imports[key];
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
