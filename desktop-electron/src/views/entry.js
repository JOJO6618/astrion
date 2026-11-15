// 内容视图条目。一个标签（或 legacy 兜底页）对应一个 Entry。

export const LEGACY_KEY = '__legacy__';

/**
 * @typedef {object} Entry
 * @property {string} key        标签 key（legacy 兜底视图为 LEGACY_KEY）
 * @property {'conv'|'new'|'page'|'legacy'} kind
 * @property {object} view       WebContentsView（测试中为伪对象）
 * @property {object|null} window 视图当前挂载的 BaseWindow；主窗口为 mainWindow
 * @property {'main'|'detached'} placement 视图当前所处位置（TODO(P2) detached 时 window 为独立窗口）
 * @property {number} lastShown  最近一次被显示的时间戳（LRU 与重启预热依据）
 * @property {string} url
 * @property {boolean} shown     当前是否显示（已向页面发送的可见性）
 */
export function createEntry({ key, kind, view, window = null, url = '', lastShown = 0 }) {
  return { key, kind, view, window, placement: 'main', lastShown, url, shown: false };
}

export const isLegacy = (entry) => entry?.key === LEGACY_KEY;
