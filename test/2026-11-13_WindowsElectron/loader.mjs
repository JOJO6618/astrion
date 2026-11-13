// 测试辅助：用 Node 内建 vm.SourceTextModule + SyntheticModule 在纯 Node 进程里加载
// 生产模块。不依赖 esbuild（当前 Linux 沙箱的根 node_modules 只装了 Windows 原生依赖，
// esbuild 二进制不可执行），也不需要 Electron。
//
// 运行命令必须带 flag：
//   node --experimental-vm-modules --test test/2026-11-13_WindowsElectron/*.test.mjs
//
// 解析规则：
//   - 相对路径：按 Node 的 .js / .mjs / index.js 顺序解析，真实递归加载并执行；
//   - node: 内建：用 SyntheticModule 包装真实内建命名空间（导出名从命名空间读取）；
//   - electron / './window.js'：用桩替换（可按需通过 mocks 覆盖）；
//   - 其它裸模块：直接报错，避免悄悄把不存在的依赖当成通过。
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { isBuiltin } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
export const srcRoot = path.resolve(here, '../../desktop-electron/src');

// electron 桩：只保证"被打包模块顶层 import 时不炸"。被测模块按定稿接口是依赖注入/
// 纯函数，正常不会真正调用这些 API。
const electronStub = `
const noop = () => {};
export const app = { getVersion: () => '0.0.0', getName: () => 'Astrion', isPackaged: true,
  getPath: () => '/tmp/astrion-test', setName: noop, on: noop, once: noop, quit: noop,
  exit: noop, relaunch: noop, requestSingleInstanceLock: () => true };
export const BaseWindow = class { on() {} };
export const BrowserWindow = class { on() {} };
export const WebContentsView = class {};
export const ipcMain = { handle: noop, on: noop, once: noop, removeHandler: noop, removeListener: noop };
export const shell = { openExternal: async () => {}, showItemInFolder: noop };
export const dialog = { showErrorBox: noop, showSaveDialog: async () => ({ canceled: true }), showOpenDialog: async () => ({ canceled: true, filePaths: [] }) };
export const autoUpdater = { on: noop, once: noop, removeListener: noop, checkForUpdates: async () => {}, quitAndInstall: noop };
export const systemPreferences = { canPromptTouchID: () => false, promptTouchID: async () => {}, askForMediaAccess: async () => true, getMediaAccessStatus: () => 'granted' };
export const safeStorage = { isEncryptionAvailable: () => true, encryptString: (v) => Buffer.from(v), decryptString: (v) => Buffer.from(v).toString() };
export const Menu = { buildFromTemplate: () => ({}), setApplicationMenu: noop };
export const Tray = class { on() {} setContextMenu() {} setToolTip() {} setTitle() {} };
export const nativeImage = { createFromDataURL: () => ({}) };
export const screen = { getAllDisplays: () => [], getCursorScreenPoint: () => ({ x: 0, y: 0 }), getDisplayNearestPoint: () => ({ id: 1, bounds: {}, workArea: {} }), on: noop };
export const globalShortcut = { register: () => true, unregister: noop, unregisterAll: noop };
export const clipboard = { writeText: noop, readText: () => '', clear: noop };
export const protocol = { registerSchemesAsPrivileged: noop };
export const session = { fromPath: () => ({}) };
export const Notification = class {};
export const powerMonitor = { on: noop };
export default { app };
`;

const windowStub = `
export const CHROME_STRIP_HEIGHT = 46;
export const createMainWindow = () => null;
export default { CHROME_STRIP_HEIGHT };
`;

const defaultStubs = {
  electron: electronStub,
  './window.js': windowStub,
  '../window.js': windowStub
};

const GLOBAL_NAMES = [
  'console', 'process', 'Buffer', 'URL', 'URLSearchParams', 'TextEncoder', 'TextDecoder',
  'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'setImmediate', 'clearImmediate',
  'queueMicrotask', 'structuredClone', 'crypto', 'performance', 'AbortController', 'AbortSignal',
  'Blob', 'atob', 'btoa', 'fetch', 'Headers', 'Request', 'Response'
];

function createContext() {
  const sandbox = {};
  for (const name of GLOBAL_NAMES) {
    if (name in globalThis) sandbox[name] = globalThis[name];
  }
  return vm.createContext(sandbox);
}

function resolveFile(specifier, parentIdentifier) {
  const base = parentIdentifier.startsWith('stub:') || parentIdentifier.startsWith('builtin:')
    ? srcRoot
    : path.dirname(parentIdentifier);
  const candidate = path.resolve(base, specifier);
  for (const suffix of ['', '.js', '.mjs', '/index.js']) {
    const file = `${candidate}${suffix}`;
    if (fs.existsSync(file) && fs.statSync(file).isFile()) return file;
  }
  return null;
}

/**
 * 加载 production 模块（相对 desktop-electron/src 的路径）。
 * @param {string} entry 例如 'platform.js'、'windows/backend-guardian.js'
 * @param {{ mocks?: Record<string, string> }} [options] 追加/覆盖 import 说明符桩
 * @returns {Promise<Record<string, unknown>>} 模块命名空间
 */
export async function load(entry, { mocks = {} } = {}) {
  if (typeof vm.SourceTextModule !== 'function') {
    throw new Error('需要 --experimental-vm-modules（node --experimental-vm-modules --test …）');
  }
  const context = createContext();
  const stubs = { ...defaultStubs, ...mocks };
  const cache = new Map();

  /** 用真实 Node 模块命名空间构造 SyntheticModule：同一 realm、同一模块级状态。 */
  async function syntheticFrom(identifier, namespace) {
    const names = Object.keys(namespace);
    return new vm.SyntheticModule(names, function evaluate() {
      for (const name of names) this.setExport(name, namespace[name]);
    }, { context, identifier });
  }

  async function instantiate(identifier, source) {
    const module = new vm.SourceTextModule(source, {
      identifier,
      context,
      initializeImportMeta(meta) {
        meta.url = path.isAbsolute(identifier) ? pathToFileURL(identifier).href : identifier;
      },
      importModuleDynamically: async (specifier, referencing) => {
        const { module: child } = await resolveModule(specifier, referencing.identifier);
        await child.link(linker);
        await child.evaluate();
        return child;
      }
    });
    return module;
  }

  async function resolveModule(specifier, parentIdentifier) {
    const stub = Object.hasOwn(stubs, specifier) ? stubs[specifier] : null;
    if (stub !== null) {
      const key = `stub:${specifier}`;
      if (!cache.has(key)) {
        // { synthetic: namespace }：把真实 Node 模块实例（同一模块级状态）按命名导出
        // 转成 SyntheticModule，用于需要跨模块共享状态的依赖（如 updater-state）。
        cache.set(key, typeof stub === 'object' && stub.synthetic
          ? syntheticFrom(key, stub.synthetic)
          : instantiate(key, stub));
      }
      return { module: await cache.get(key), kind: 'stub' };
    }

    if (isBuiltin(specifier)) {
      const key = `builtin:${specifier}`;
      if (!cache.has(key)) {
        cache.set(key, (async () => {
          const namespace = await import(specifier);
          const names = Object.keys(namespace);
          // SyntheticModule 第二个参数是 evaluate 回调（this=module），不是 per-export
          // getter：必须显式 setExport，否则命名导入得到 undefined。
          return new vm.SyntheticModule(names, function evaluate() {
            for (const name of names) this.setExport(name, namespace[name]);
          }, { context, identifier: key });
        })());
      }
      return { module: await cache.get(key), kind: 'builtin' };
    }

    if (!specifier.startsWith('.') && !path.isAbsolute(specifier)) {
      throw new Error(`未提供依赖桩: ${specifier}（来自 ${parentIdentifier}）`);
    }

    const file = resolveFile(specifier, parentIdentifier);
    if (!file) {
      throw new Error(`无法解析模块: ${specifier}（来自 ${parentIdentifier}）`);
    }
    if (!cache.has(file)) {
      cache.set(file, instantiate(file, fs.readFileSync(file, 'utf8')));
    }
    return { module: await cache.get(file), kind: 'file' };
  }

  const linker = async (specifier, referencingModule) => {
    const { module } = await resolveModule(specifier, referencingModule.identifier);
    return module;
  };

  const entryFile = resolveFile(`./${entry.replace(/^\.\//, '')}`, path.join(srcRoot, 'loader-anchor.js'));
  if (!entryFile) {
    throw new Error(`被测模块不存在: desktop-electron/src/${entry}`);
  }
  const { module } = await resolveModule(entryFile, path.join(srcRoot, 'loader-anchor.js'));
  await module.link(linker);
  await module.evaluate();
  return module.namespace;
}

/** 兼容 named export 与 default 对象两种导出形状，避免因导出风格差异误判功能。 */
export function exportsOf(module, name) {
  if (typeof module?.[name] === 'function') return module;
  if (typeof module?.default === 'object' && typeof module.default?.[name] === 'function') {
    return module.default;
  }
  throw new Error(`模块缺少导出 ${name}()`);
}
