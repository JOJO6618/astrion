/**
 * show_html 全屏预览 overlay（应用内 fixed 层，不走浏览器 Fullscreen API）。
 * 从 app/bootstrap.ts 抽取为全局单例，供 HTML 卡片与文件卡片（ShowFileCard）共用：
 * - js="on" 卡片：直接复用已注入 CSP/守卫的 srcdoc，沙箱允许脚本
 * - js="off" 卡片：用 sanitize 后的 HTML 现场构建 srcdoc，沙箱禁脚本（与卡片内语义一致）
 * - HTML 文件预览：与 js="on" 卡片同策略（注入 CSP/守卫，allow-scripts）
 */
import { EMPTY_SHOW_HTML_SRCDOC } from './showHtmlSandbox';
import { t } from '@/locales';

export interface ShowHtmlFullscreenPayload {
  srcdoc: string;
  allowScripts: boolean;
  /** 顶栏标题（如文件名），缺省显示「全屏预览」 */
  title?: string;
  /** 顶栏标题旁的提示小字（如「含外部资源引用，预览可能不完整」），缺省隐藏 */
  notice?: string;
}

interface ShowHtmlFullscreenState {
  root: HTMLElement;
  iframe: HTMLIFrameElement;
  titleEl: HTMLElement;
  noticeEl: HTMLElement;
  payload: ShowHtmlFullscreenPayload | null;
}

// 与 bootstrap.ts 的 showHtml 调试通道一致：window.__SHOW_HTML_DEBUG__ 或
// localStorage.showHtmlDebug = '1' 开启，统一 [SHOW_HTML_DEBUG] 前缀便于一次性筛选
const SHOW_HTML_DEBUG_MAX_LOGS = 500;
let debugLogCount = 0;

function isDebugEnabled(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    const explicitFlag = (window as any).__SHOW_HTML_DEBUG__;
    if (explicitFlag === true || explicitFlag === '1') return true;
    if (explicitFlag === false || explicitFlag === '0') return false;
    const localFlag = window.localStorage?.getItem('showHtmlDebug');
    return localFlag === '1' || localFlag === 'true';
  } catch {
    return false;
  }
}

function debugLog(event: string, payload: Record<string, unknown> = {}) {
  if (!isDebugEnabled()) return;
  if (debugLogCount >= SHOW_HTML_DEBUG_MAX_LOGS) return;
  debugLogCount += 1;
  if (debugLogCount === SHOW_HTML_DEBUG_MAX_LOGS) {
    console.warn('[SHOW_HTML_DEBUG]', 'log-limit-reached', { max: SHOW_HTML_DEBUG_MAX_LOGS });
    return;
  }
  console.log('[SHOW_HTML_DEBUG]', event, payload);
}

let showHtmlFullscreenState: ShowHtmlFullscreenState | null = null;
let showHtmlFullscreenEventsBound = false;

export function isShowHtmlFullscreenOpen(): boolean {
  return !!showHtmlFullscreenState?.root.classList.contains('is-open');
}

function bindShowHtmlFullscreenGlobalEvents() {
  if (showHtmlFullscreenEventsBound || typeof document === 'undefined') return;
  showHtmlFullscreenEventsBound = true;
  // 桌面端 Esc 退出；移动端没有 Esc，依赖顶栏 ✕ 按钮（触屏主路径）
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !isShowHtmlFullscreenOpen()) return;
    event.stopPropagation();
    closeShowHtmlFullscreen();
  });
}

function ensureShowHtmlFullscreenDom(): ShowHtmlFullscreenState {
  if (showHtmlFullscreenState) return showHtmlFullscreenState;
  bindShowHtmlFullscreenGlobalEvents();

  const root = document.createElement('div');
  root.className = 'show-html-fullscreen';
  root.setAttribute('role', 'dialog');
  root.setAttribute('aria-modal', 'true');
  root.setAttribute('aria-label', t('utils.fullscreenPreview'));

  const bar = document.createElement('div');
  bar.className = 'show-html-fullscreen__bar';
  const heading = document.createElement('div');
  heading.className = 'show-html-fullscreen__heading';
  const title = document.createElement('span');
  title.className = 'show-html-fullscreen__title';
  title.textContent = t('utils.fullscreenPreview');
  const notice = document.createElement('span');
  notice.className = 'show-html-fullscreen__notice';
  notice.style.display = 'none';
  heading.appendChild(title);
  heading.appendChild(notice);
  const actions = document.createElement('div');
  actions.className = 'show-html-fullscreen__actions';

  const refreshBtn = document.createElement('button');
  refreshBtn.type = 'button';
  refreshBtn.className = 'show-html-fullscreen__btn';
  refreshBtn.title = t('utils.refresh');
  refreshBtn.setAttribute('aria-label', t('utils.refresh'));
  refreshBtn.textContent = '↻';

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'show-html-fullscreen__btn show-html-fullscreen__btn--close';
  closeBtn.title = t('utils.closeEsc');
  closeBtn.setAttribute('aria-label', t('utils.closeFullscreenPreview'));
  closeBtn.textContent = '✕';

  actions.appendChild(refreshBtn);
  actions.appendChild(closeBtn);
  bar.appendChild(heading);
  bar.appendChild(actions);

  const body = document.createElement('div');
  body.className = 'show-html-fullscreen__body';
  const iframe = document.createElement('iframe');
  iframe.className = 'show-html-fullscreen__iframe';
  iframe.setAttribute('referrerpolicy', 'no-referrer');
  // 与内联卡片一致：overlay 用 CSS fixed 占满视口，不需要浏览器 Fullscreen API
  iframe.setAttribute('allow', "fullscreen 'none'");
  body.appendChild(iframe);

  root.appendChild(bar);
  root.appendChild(body);

  refreshBtn.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    const state = showHtmlFullscreenState;
    if (!state?.payload) return;
    // 与内联卡片刷新同策略：先挂空文档再重挂，强制 iframe 内页面重载
    const keep = state.payload.srcdoc;
    state.iframe.srcdoc = EMPTY_SHOW_HTML_SRCDOC;
    requestAnimationFrame(() => {
      if (showHtmlFullscreenState?.payload) {
        showHtmlFullscreenState.iframe.srcdoc = keep;
      }
    });
    debugLog('fullscreen:refresh', { srcdocLength: keep.length });
  };
  closeBtn.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeShowHtmlFullscreen();
  };

  document.body.appendChild(root);
  showHtmlFullscreenState = { root, iframe, titleEl: title, noticeEl: notice, payload: null };
  return showHtmlFullscreenState;
}

export function openShowHtmlFullscreen(payload: ShowHtmlFullscreenPayload) {
  if (typeof document === 'undefined') return;
  const state = ensureShowHtmlFullscreenDom();
  state.payload = payload;
  state.titleEl.textContent = payload.title || t('utils.fullscreenPreview');
  const noticeText = (payload.notice || '').trim();
  state.noticeEl.textContent = noticeText;
  state.noticeEl.style.display = noticeText ? '' : 'none';
  // js=off 卡片内容已 sanitize，全屏时连脚本执行权也不给，语义与卡片内一致
  state.iframe.setAttribute('sandbox', payload.allowScripts ? 'allow-scripts' : '');
  state.iframe.srcdoc = payload.srcdoc;
  state.root.classList.add('is-open');
  document.documentElement.classList.add('show-html-fullscreen-open');
  debugLog('fullscreen:open', {
    allowScripts: payload.allowScripts,
    srcdocLength: payload.srcdoc.length,
    hasNotice: !!noticeText
  });
}

export function closeShowHtmlFullscreen() {
  const state = showHtmlFullscreenState;
  if (!state || !isShowHtmlFullscreenOpen()) return;
  state.root.classList.remove('is-open');
  document.documentElement.classList.remove('show-html-fullscreen-open');
  state.payload = null;
  // 清空 srcdoc：停掉 iframe 内脚本/定时器并释放内存
  state.iframe.srcdoc = EMPTY_SHOW_HTML_SRCDOC;
  debugLog('fullscreen:close', {});
}
