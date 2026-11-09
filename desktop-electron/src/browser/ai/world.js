// 隔离环境（isolated world）里的辅助脚本：Playwright 无障碍快照 + ref 定位 + DOM 静默计时。
// 网页主环境看不到这里的任何变量；DOM 共享。
import injectedSource from './vendor/playwright-injected.js';

export const WORLD_NAME = '__astrion_ai__';

const INJECTED_OPTIONS = {
  isUnderTest: false, sdkLanguage: 'javascript', testIdAttributeName: 'data-testid',
  stableRafCount: 1, browserName: 'chromium', shouldPrependErrorPrefix: false,
  isUtilityWorld: true, customEngines: []
};

// 运行于隔离环境；只依赖 DOM 与 InjectedScript 实例。
function helpers(injected) {
  const state = { last: performance.now() };
  const watch = () => {
    try {
      new MutationObserver(() => { state.last = performance.now(); })
        .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
    } catch { /* document not ready */ }
  };
  watch();
  const describe = el => {
    if (!el || el.nodeType !== 1) return '';
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    const id = el.id ? `#${el.id}` : '';
    const cls = typeof el.className === 'string' && el.className.trim() ? `.${el.className.trim().split(/\s+/).slice(0, 2).join('.')}` : '';
    return `<${el.tagName.toLowerCase()}${id}${cls}>${label ? ` "${label}"` : ''}`;
  };
  const deepFromPoint = (x, y) => {
    let hit = document.elementFromPoint(x, y);
    while (hit && hit.shadowRoot) {
      const inner = hit.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    return hit;
  };
  const composedContains = (root, node) => {
    for (let cur = node; cur; cur = cur.parentNode || cur.host) if (cur === root) return true;
    return false;
  };
  const api = {
    state,
    quiet: ms => performance.now() - state.last >= ms,
    rewatch: watch,
    snapshot(options) {
      let root = document.body || document.documentElement;
      if (options.target) {
        root = api.element(options.target);
        if (!root) return { error: 'stale_ref' };
      }
      if (!root) return { full: '' };
      const result = injected.incrementalAriaSnapshot(root, { mode: 'ai', track: options.track, depth: options.depth || undefined });
      // 密码框的值永远不进入模型上下文。
      const secrets = [...document.querySelectorAll('input[type=password]')].map(el => el.value).filter(value => value.length > 0)
        .sort((a, b) => b.length - a.length);
      const mask = text => {
        if (typeof text !== 'string' || !secrets.length) return text;
        return text.split('\n').map(line => {
          for (const secret of secrets) {
            if (/^\s*- (?:<changed> )?textbox\b/.test(line) && line.endsWith(`: ${secret}`)) return line.slice(0, -secret.length) + '[已填写]';
          }
          // 页面若在别处回显较长的密码，同样遮盖；过短的值不做全局替换以免误伤正文。
          return secrets.reduce((out, secret) => (secret.length >= 6 ? out.split(secret).join('[已填写]') : out), line);
        }).join('\n');
      };
      return { full: mask(result.full), incremental: mask(result.incremental) };
    },
    element(ref) {
      const lookup = () => injected._lastAriaSnapshotForQuery?.elements?.get(ref);
      let el = lookup();
      if (!el || !el.isConnected) {
        // 刷新 ref 映射（不影响模型基线的 track）。已存在的元素保持原 ref。
        if (document.body) injected.ariaSnapshot(document.body, { mode: 'ai' });
        el = lookup();
      }
      return el && el.isConnected ? el : null;
    },
    describe,
    point(ref, position) {
      const el = api.element(ref);
      if (!el) return { error: 'stale_ref' };
      const box = () => el.getBoundingClientRect();
      let rect = box();
      const inView = rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth;
      if (!inView) { el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' }); rect = box(); }
      if (rect.width <= 0 || rect.height <= 0) return { error: 'not_visible', element: describe(el) };
      const x = rect.left + (position ? Math.min(Math.max(position.x, 0), rect.width) : rect.width / 2);
      const y = rect.top + (position ? Math.min(Math.max(position.y, 0), rect.height) : rect.height / 2);
      if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return { error: 'outside_viewport', element: describe(el) };
      const hit = deepFromPoint(x, y);
      const label = el.closest('label') || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
      const ok = !!hit && (composedContains(el, hit) || composedContains(hit, el) || (label && composedContains(label, hit)));
      return { x, y, ok, blocker: ok ? '' : describe(hit), element: describe(el), tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase() };
    },
    focusForTyping(ref) {
      const el = api.element(ref);
      if (!el) return { error: 'stale_ref' };
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      const editable = el.isContentEditable || ['INPUT', 'TEXTAREA'].includes(el.tagName);
      if (!editable) return { error: 'not_editable', element: describe(el) };
      if (el.disabled || el.readOnly) return { error: 'not_editable', element: describe(el) };
      el.focus();
      if (el.isContentEditable) {
        const range = document.createRange(); range.selectNodeContents(el);
        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
      } else if (typeof el.select === 'function') {
        try { el.select(); } catch { /* some input types cannot be selected */ }
      }
      return { ok: document.activeElement === el || el.contains(document.activeElement), type: (el.type || '').toLowerCase(), password: el.type === 'password' };
    },
    isPassword(ref) { const el = api.element(ref); return !!el && el.type === 'password'; },
    selectOption(ref, values) {
      const el = api.element(ref);
      if (!el) return { error: 'stale_ref' };
      if (el.tagName !== 'SELECT') return { error: 'not_select', element: describe(el) };
      if (el.disabled) return { error: 'disabled', element: describe(el) };
      const wanted = values.map(String);
      const options = [...el.options];
      const picked = wanted.map(value => options.find(o => o.value === value) || options.find(o => o.label.trim() === value.trim()));
      if (picked.some(o => !o)) return { error: 'option_missing', available: options.slice(0, 40).map(o => o.label.trim() || o.value) };
      if (!el.multiple && picked.length > 1) return { error: 'single_select' };
      el.focus();
      for (const o of options) o.selected = picked.includes(o);
      el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { selected: picked.map(o => o.label.trim() || o.value) };
    },
    checked(ref) {
      const el = api.element(ref);
      if (!el) return { error: 'stale_ref' };
      if ('checked' in el && ['checkbox', 'radio'].includes(el.type)) return { checked: el.checked };
      const aria = el.getAttribute('aria-checked') ?? el.getAttribute('aria-pressed');
      return aria === null ? { error: 'not_checkable', element: describe(el) } : { checked: aria === 'true' };
    },
    setRange(ref, value) {
      const el = api.element(ref);
      if (!el) return { error: 'stale_ref' };
      if (el.tagName !== 'INPUT' || el.type !== 'range') return { error: 'not_slider', element: describe(el) };
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(el, String(value));
      el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { value: el.value };
    },
    viewport() { return { width: innerWidth, height: innerHeight, scrollX, scrollY }; },
    rect(ref) {
      const el = api.element(ref);
      if (!el) return { error: 'stale_ref' };
      el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
      const r = el.getBoundingClientRect();
      return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height };
    },
    text() { return document.body ? document.body.innerText : ''; }
  };
  return api;
}

export const BOOT_SCRIPT = `(() => {
  if (globalThis.__astrionAI) return true;
  const module = {};
  ${injectedSource}
  const injected = new (module.exports.InjectedScript())(globalThis, ${JSON.stringify(INJECTED_OPTIONS)});
  globalThis.__astrionAI = (${helpers.toString()})(injected);
  return true;
})()`;
