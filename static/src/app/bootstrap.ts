// @ts-nocheck
import katex from 'katex';
import DOMPurify from 'dompurify';
import { createApp } from 'vue';
import { t } from '@/locales';
import ShowFileCard from '../components/chat/ShowFileCard.vue';
import { buildShowHtmlIframeSrcdoc } from '../utils/showHtmlSandbox';
import {
  openShowHtmlFullscreen,
  closeShowHtmlFullscreen,
  type ShowHtmlFullscreenPayload
} from '../utils/showHtmlFullscreen';

let showImageRenderSeq = 0;
let showImageDebugLogCount = 0;
const SHOW_IMAGE_DEBUG_VERSION = 'v2-lite-throttled';
const SHOW_IMAGE_DEBUG_MAX_LOGS = 200;
let showHtmlDebugLogCount = 0;
const SHOW_HTML_DEBUG_MAX_LOGS = 500;

function getShowImageDebugMode(): 'off' | 'lite' | 'verbose' {
  if (typeof window === 'undefined') return 'off';
  try {
    const explicitFlag = (window as any).__SHOW_IMAGE_DEBUG__;
    if (
      explicitFlag === false ||
      explicitFlag === '0' ||
      explicitFlag === 'off' ||
      explicitFlag === 'false'
    ) {
      return 'off';
    }
    if (explicitFlag === 'verbose') return 'verbose';
    if (explicitFlag === true || explicitFlag === '1' || explicitFlag === 'lite') return 'lite';

    const localFlag = window.localStorage?.getItem('showImageDebug');
    if (localFlag === '0' || localFlag === 'off' || localFlag === 'false' || localFlag === 'none') {
      return 'off';
    }
    if (localFlag === 'verbose') return 'verbose';
    if (localFlag === '1' || localFlag === 'true' || localFlag === 'lite') return 'lite';
    return 'off';
  } catch {
    return 'off';
  }
}

function debugShowImageLog(event: string, payload: Record<string, any> = {}) {
  const mode = getShowImageDebugMode();
  if (mode === 'off') return;
  if (
    mode === 'lite' &&
    !['observer:setup', 'observer:show-image-mutation', 'render:start', 'render:end'].includes(
      event
    )
  ) {
    return;
  }
  if (showImageDebugLogCount >= SHOW_IMAGE_DEBUG_MAX_LOGS) return;
  showImageDebugLogCount += 1;
  if (showImageDebugLogCount === SHOW_IMAGE_DEBUG_MAX_LOGS) {
    console.warn('[SHOW_IMAGE_DEBUG]', 'log-limit-reached', {
      max: SHOW_IMAGE_DEBUG_MAX_LOGS,
      version: SHOW_IMAGE_DEBUG_VERSION
    });
    return;
  }
  console.log('[SHOW_IMAGE_DEBUG]', event, payload);
}

function isShowHtmlDebugEnabled() {
  if (typeof window === 'undefined') return false;
  try {
    const explicitFlag = (window as any).__SHOW_HTML_DEBUG__;
    if (explicitFlag === true || explicitFlag === '1') return true;
    if (explicitFlag === false || explicitFlag === '0') return false;
    const localFlag = window.localStorage?.getItem('showHtmlDebug');
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

function debugShowHtmlLog(event: string, payload: Record<string, any> = {}) {
  if (!isShowHtmlDebugEnabled()) return;
  if (showHtmlDebugLogCount >= SHOW_HTML_DEBUG_MAX_LOGS) return;
  showHtmlDebugLogCount += 1;
  if (showHtmlDebugLogCount === SHOW_HTML_DEBUG_MAX_LOGS) {
    console.warn('[SHOW_HTML_DEBUG]', 'log-limit-reached', {
      max: SHOW_HTML_DEBUG_MAX_LOGS
    });
    return;
  }
  console.log('[SHOW_HTML_DEBUG]', event, payload);
}

function markShowTagDrawingActive(holdMs = 1600) {
  if (typeof window === 'undefined') return;
  try {
    (window as any).__SHOW_TAG_DRAWING_UNTIL__ = Date.now() + holdMs;
  } catch {
    // ignore
  }
}

function summarizeNodeForDebug(node: Node | null) {
  if (!node) return 'null';
  if (node.nodeType === Node.TEXT_NODE) {
    const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
    return `TEXT(${text.slice(0, 80)})`;
  }
  if (node.nodeType === Node.ELEMENT_NODE) {
    const el = node as Element;
    const tag = el.tagName.toLowerCase();
    if (tag === 'show_image' || tag === 'show-image') {
      return `SHOW_IMAGE(src=${el.getAttribute('src') || ''})`;
    }
    return `${tag.toUpperCase()}`;
  }
  return `NODE(${node.nodeType})`;
}

function collectShowImageOrder(root: ParentNode | null = document) {
  if (!root || !(root instanceof Node)) return [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ALL);
  const tokens: string[] = [];
  let current = walker.currentNode;
  while (current) {
    if (current.nodeType === Node.TEXT_NODE) {
      const text = (current.textContent || '').replace(/\s+/g, ' ').trim();
      if (text) tokens.push(`TEXT(${text.slice(0, 40)})`);
    } else if (current.nodeType === Node.ELEMENT_NODE) {
      const el = current as Element;
      const tag = el.tagName.toLowerCase();
      if (tag === 'show_image' || tag === 'show-image') {
        tokens.push(`SHOW_IMAGE(${el.getAttribute('src') || ''})`);
      } else if (tag === 'show_html' || tag === 'show-html') {
        tokens.push(`SHOW_HTML(ratio=${readShowHtmlRatio(el)},js=${readShowHtmlJsMode(el)})`);
      }
    }
    current = walker.nextNode();
  }
  return tokens.slice(0, 200);
}

/* host/docker 模式缓存读取：键名与 app/state.ts 的 WORKSPACE_MODE_STORAGE_KEY 一致。
   模式是部署级属性几乎不变，socket 连接后会校正回写 localStorage，这里直接读缓存即可。 */
function getShowImageWorkspaceMode(): 'host' | 'docker' {
  try {
    return window.localStorage.getItem('agents_workspace_mode') === 'host' ? 'host' : 'docker';
  } catch {
    return 'docker';
  }
}

/**
 * 归一化 show_image 的 src：
 * - http(s) 网络链接、/user_upload/ 上传文件：原样使用；
 * - 其余一律视为本地文件路径，统一走 /api/file/content 通道（inline 预览，MIME 白名单由服务端控制）：
 *   - docker/web 模式：仅工作区内相对路径，越界（../、项目外绝对路径）由后端 _validate_path 拒绝；
 *   - host 模式：工作区相对路径或任意绝对路径均可（后端 _validate_path host 分支全放行）。
 * 无法识别时返回空串，由调用方渲染可见的错误占位（不再静默吞掉）。
 */
function normalizeShowImageSrc(src: string) {
  if (!src) return '';
  let trimmed = src.trim();
  if (!trimmed) return '';
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  // file:// 前缀剥离按本地路径处理（浏览器禁止 http 页面直接加载 file:// 资源）
  if (/^file:\/\//i.test(trimmed)) {
    trimmed = trimmed.replace(/^file:\/\//i, '');
    // Windows file:///C:/... → C:/...
    trimmed = trimmed.replace(/^\/(?=[A-Za-z]:\/)/, '');
  }
  if (trimmed.startsWith('/user_upload/')) return trimmed;
  // 兼容容器内部路径：/workspace/.../user_upload/xxx.png 或 /workspace/user_upload/xxx
  const idx = trimmed.toLowerCase().indexOf('/user_upload/');
  if (idx >= 0) {
    return '/user_upload/' + trimmed.slice(idx + '/user_upload/'.length);
  }
  // /workspace 前缀是容器内绝对路径写法，剥掉后按工作区相对路径处理
  if (trimmed === '/workspace') return '';
  if (trimmed.startsWith('/workspace/')) {
    trimmed = trimmed.slice('/workspace/'.length);
  }
  // 前导 ./ 语义等同工作区相对路径，剥掉保持整洁
  if (trimmed.startsWith('./')) {
    trimmed = trimmed.slice(2);
  }
  if (!trimmed) return '';
  if (trimmed.startsWith('/')) {
    if (getShowImageWorkspaceMode() === 'host') {
      // host 模式保留绝对路径语义（如 /Users/xxx/img.png），后端全放行
      return `/api/file/content?path=${encodeURIComponent(trimmed)}`;
    }
    // docker 模式下前导 / 视为工作区根相对路径（真绝对路径会被后端拒绝）
    trimmed = trimmed.replace(/^\/+/, '');
    if (!trimmed) return '';
  }
  return `/api/file/content?path=${encodeURIComponent(trimmed)}`;
}

function parsePixelSize(raw: string | null, fallback: number) {
  if (!raw) return fallback;
  const n = Number.parseInt(raw.trim(), 10);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(4096, n);
}

const SHOW_HTML_ALLOWED_RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4'] as const;
const SHOW_HTML_RATIO_VALUES: Record<string, number> = {
  '1:1': 1,
  '16:9': 16 / 9,
  '9:16': 9 / 16,
  '4:3': 4 / 3,
  '3:4': 3 / 4
};
// 卡片尺寸的基准像素与上限约束由 CSS 表达（见 _chat-area.scss 的 show_html[data-rendered] 规则），
// 此处仅保留比例数值用于写入 --show-html-ar CSS 变量。

function normalizeShowHtmlRatio(raw: string | null) {
  if (!raw) return '1:1';
  const cleaned = raw.trim().toLowerCase().replace(/[x/]/g, ':').replace(/\s+/g, '');
  if ((SHOW_HTML_ALLOWED_RATIOS as readonly string[]).includes(cleaned)) return cleaned;
  return '1:1';
}

function pickNearestAllowedRatio(width: number, height: number) {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return '1:1';
  }
  const target = width / height;
  let best = '1:1';
  let bestDiff = Number.POSITIVE_INFINITY;
  for (const ratio of SHOW_HTML_ALLOWED_RATIOS) {
    const diff = Math.abs(SHOW_HTML_RATIO_VALUES[ratio] - target);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = ratio;
    }
  }
  return best;
}

function readShowHtmlRatio(node: Element) {
  const ratioAttr = node.getAttribute('ratio');
  if (ratioAttr) return normalizeShowHtmlRatio(ratioAttr);
  // 兼容旧格式：模型输出了 width/height 时，自动映射到允许的比例
  const width = parsePixelSize(node.getAttribute('width'), 0);
  const height = parsePixelSize(node.getAttribute('height'), 0);
  if (width > 0 && height > 0) return pickNearestAllowedRatio(width, height);
  return '1:1';
}

function normalizeShowHtmlJsMode(raw: string | null) {
  if (!raw) return 'off';
  const cleaned = raw.trim().toLowerCase();
  if (['on', '1', 'true', 'yes', 'enabled', 'enable'].includes(cleaned)) {
    return 'on';
  }
  return 'off';
}

function readShowHtmlJsMode(node: Element) {
  return normalizeShowHtmlJsMode(node.getAttribute('js'));
}



function decodeBase64Utf8(input: string) {
  if (!input) return '';
  try {
    return decodeURIComponent(escape(atob(input)));
  } catch {
    return '';
  }
}

function sanitizeInlineCss(css: string) {
  if (!css) return '';
  return css
    .replace(/expression\s*\([^)]*\)/gi, '')
    .replace(/url\s*\(\s*['"]?\s*javascript:[^)]+\)/gi, 'url(about:blank)')
    .replace(/@import[^;]+;/gi, '');
}

function rewriteCssSelectorsForShadowDom(css: string) {
  if (!css) return '';
  // 注意：不能直接把 html/body 连续替换成 .show-html-root，
  // 否则后续 /\bhtml\b/ 会再次命中 ".show-html-root" 里的 "html"，产生
  // 类似 ".show-.show-html-root-root" 的错误选择器，导致布局样式失效（元素偏移）。
  const placeholder = '__SHOW_HTML_ROOT_SELECTOR__';
  return css
    .replace(/\bhtml\s*,\s*body\b/gi, placeholder)
    .replace(/\bbody\s*,\s*html\b/gi, placeholder)
    .replace(/\bhtml\b/gi, placeholder)
    .replace(/\bbody\b/gi, placeholder)
    .replace(new RegExp(placeholder, 'g'), '.show-html-root');
}

function sanitizeShowHtmlContent(rawHtml: string) {
  const purified = DOMPurify.sanitize(rawHtml || '', {
    USE_PROFILES: { html: true, svg: true, svgFilters: true, mathMl: true },
    FORBID_TAGS: ['script', 'noscript', 'iframe', 'object', 'embed', 'link'],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: true
  }) as string;

  const parser = new DOMParser();
  const doc = parser.parseFromString(`<body>${purified}</body>`, 'text/html');

  const elements = Array.from(doc.body.querySelectorAll('*'));
  elements.forEach((el) => {
    Array.from(el.attributes).forEach((attr) => {
      const name = attr.name.toLowerCase();
      const value = attr.value || '';
      if (name.startsWith('on')) {
        el.removeAttribute(attr.name);
        return;
      }
      if (
        (name === 'src' || name === 'href' || name === 'xlink:href') &&
        /^\s*javascript:/i.test(value)
      ) {
        el.removeAttribute(attr.name);
        return;
      }
      if (name === 'style') {
        el.setAttribute('style', sanitizeInlineCss(value));
      }
    });
  });

  doc.querySelectorAll('style').forEach((styleEl) => {
    styleEl.textContent = rewriteCssSelectorsForShadowDom(
      sanitizeInlineCss(styleEl.textContent || '')
    );
  });

  return doc.body.innerHTML;
}

function escapeHtml(input: string) {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

interface ShowHtmlCardControls {
  onRefresh: () => void;
  getFullscreenPayload: () => ShowHtmlFullscreenPayload | null;
}

const SHOW_HTML_CONTROLS_KEY = '__showHtmlCardControls';

function getShowHtmlCardControls(wrapper: HTMLElement): ShowHtmlCardControls | null {
  return ((wrapper as any)[SHOW_HTML_CONTROLS_KEY] as ShowHtmlCardControls | undefined) || null;
}

function triggerShowHtmlCardRefresh(wrapper: HTMLElement, btn: HTMLElement | null) {
  const controls = getShowHtmlCardControls(wrapper);
  if (!controls) return;
  markShowTagDrawingActive(1200);
  if (btn) {
    btn.classList.remove('is-refreshing');
    // 触发重放动画
    void btn.offsetWidth;
    btn.classList.add('is-refreshing');
    window.setTimeout(() => btn.classList.remove('is-refreshing'), 720);
  }
  try {
    controls.onRefresh();
  } catch (error) {
    debugShowHtmlLog('refresh:error', {
      message: error instanceof Error ? error.message : String(error || '')
    });
  }
}

// ===== 卡片 ⋯ 菜单（全局 fixed 单例，同一时间只存在一个） =====
let showHtmlCardMenuEl: HTMLElement | null = null;
let showHtmlCardMenuAnchor: HTMLElement | null = null;
let showHtmlCardMenuEventsBound = false;

function closeShowHtmlCardMenu() {
  if (showHtmlCardMenuEl) {
    showHtmlCardMenuEl.remove();
    showHtmlCardMenuEl = null;
    showHtmlCardMenuAnchor = null;
  }
}

function bindShowHtmlCardMenuGlobalEvents() {
  if (showHtmlCardMenuEventsBound || typeof document === 'undefined') return;
  showHtmlCardMenuEventsBound = true;
  document.addEventListener(
    'click',
    (event) => {
      if (!showHtmlCardMenuEl) return;
      const target = event.target as Node | null;
      if (target && showHtmlCardMenuEl.contains(target)) return;
      // 锚点按钮自身走 toggle 逻辑，不在全局监听里关闭
      if (target && showHtmlCardMenuAnchor?.contains(target)) return;
      closeShowHtmlCardMenu();
    },
    true
  );
  document.addEventListener(
    'keydown',
    (event) => {
      if (event.key === 'Escape' && showHtmlCardMenuEl) {
        event.stopPropagation();
        closeShowHtmlCardMenu();
      }
    },
    true
  );
  window.addEventListener('resize', closeShowHtmlCardMenu);
  // 聊天区滚动时菜单位置即失效，直接关闭（capture 以捕获任意滚动容器）
  document.addEventListener(
    'scroll',
    () => {
      if (showHtmlCardMenuEl) closeShowHtmlCardMenu();
    },
    true
  );
}

function buildShowHtmlCardMenuItem(label: string): HTMLButtonElement {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'show-html-card-menu__item';
  item.textContent = label;
  return item;
}

function openShowHtmlCardMenu(anchor: HTMLElement, wrapper: HTMLElement) {
  bindShowHtmlCardMenuGlobalEvents();
  if (showHtmlCardMenuEl && showHtmlCardMenuAnchor === anchor) {
    closeShowHtmlCardMenu();
    return;
  }
  closeShowHtmlCardMenu();

  const menu = document.createElement('div');
  menu.className = 'show-html-card-menu';
  menu.setAttribute('role', 'menu');

  const refreshItem = buildShowHtmlCardMenuItem(t('appCore.refresh'));
  refreshItem.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeShowHtmlCardMenu();
    triggerShowHtmlCardRefresh(wrapper, anchor);
  };

  const fullscreenItem = buildShowHtmlCardMenuItem(t('appCore.fullscreen'));
  fullscreenItem.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    const controls = getShowHtmlCardControls(wrapper);
    closeShowHtmlCardMenu();
    const payload = controls?.getFullscreenPayload?.();
    if (!payload || !payload.srcdoc) return;
    openShowHtmlFullscreen(payload);
  };

  menu.appendChild(refreshItem);
  menu.appendChild(fullscreenItem);
  document.body.appendChild(menu);

  // 定位：按钮下方、右对齐，视口边缘留 8px 安全距离
  const rect = anchor.getBoundingClientRect();
  const menuWidth = menu.offsetWidth;
  let left = Math.round(rect.right - menuWidth);
  left = Math.max(8, Math.min(left, window.innerWidth - menuWidth - 8));
  menu.style.left = `${left}px`;
  menu.style.top = `${Math.round(rect.bottom + 6)}px`;

  showHtmlCardMenuEl = menu;
  showHtmlCardMenuAnchor = anchor;
}

/**
 * 绑定 show_html 卡片右上角工具栏（⋯ 菜单：刷新 / 全屏）。
 * 渲染器每次重渲染都会调用：最新回调挂在 wrapper 上，按钮与菜单只创建一次。
 */
function bindShowHtmlCardControls(wrapper: HTMLElement, controls: ShowHtmlCardControls) {
  (wrapper as any)[SHOW_HTML_CONTROLS_KEY] = controls;
  let toolbar = wrapper.querySelector(':scope > .chat-inline-card__toolbar') as HTMLElement | null;
  if (!toolbar) {
    toolbar = document.createElement('div');
    toolbar.className = 'chat-inline-card__toolbar';
    wrapper.appendChild(toolbar);
  }
  let menuBtn = toolbar.querySelector(
    ':scope > .chat-inline-card__menu-btn'
  ) as HTMLButtonElement | null;
  if (!menuBtn) {
    menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'chat-inline-card__menu-btn';
    menuBtn.title = t('appCore.cardActions');
    menuBtn.setAttribute('aria-label', t('appCore.cardActions'));
    menuBtn.setAttribute('aria-haspopup', 'menu');
    menuBtn.textContent = '⋯';
    toolbar.appendChild(menuBtn);
  }
  menuBtn.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    openShowHtmlCardMenu(menuBtn!, wrapper);
  };
}

function renderShowImages(root: ParentNode | null = document) {
  if (!root) return;
  const debugMode = getShowImageDebugMode();
  const verbose = debugMode === 'verbose';
  const renderId = ++showImageRenderSeq;
  const imageNodes = Array.from(
    root.querySelectorAll('show_image:not([data-rendered]), show-image:not([data-rendered])')
  );
  const htmlNodes = Array.from(
    root.querySelectorAll('show_html:not([data-rendered]), show-html:not([data-rendered])')
  );
  const fileNodes = Array.from(
    root.querySelectorAll('show_file:not([data-rendered]), show-file:not([data-rendered])')
  );
  // download:// 链接也需要绑定，即使没有 show 标签
  const hasDownloadLinks = !!root.querySelector(
    'a.md-download-link:not([data-download-bound]), a[data-download="1"]:not([data-download-bound])'
  );
  if (htmlNodes.length > 0 || fileNodes.length > 0) {
    markShowTagDrawingActive();
  }
  if (!verbose && imageNodes.length === 0 && htmlNodes.length === 0 && fileNodes.length === 0 && !hasDownloadLinks) return;

  const rootNode = root as Node;
  debugShowImageLog('render:start', {
    renderId,
    rootType:
      rootNode?.nodeType === Node.ELEMENT_NODE
        ? (rootNode as Element).tagName.toLowerCase()
        : `nodeType:${rootNode?.nodeType}`,
    pendingImageCount: imageNodes.length,
    pendingHtmlCount: htmlNodes.length,
    pendingFileCount: fileNodes.length,
    orderBefore: collectShowImageOrder(root)
  });

  if (verbose) {
    debugShowImageLog('render:pending-show-images', {
      renderId,
      count: imageNodes.length,
      nodes: imageNodes.map((n) => n.getAttribute('src') || '')
    });
  }
  imageNodes.forEach((node) => {
    if (!node.isConnected || node.hasAttribute('data-rendered')) return;

    if (verbose) {
      debugShowImageLog('render:node-start', {
        renderId,
        src: node.getAttribute('src') || '',
        parent: summarizeNodeForDebug(node.parentNode),
        prevSibling: summarizeNodeForDebug(node.previousSibling),
        nextSibling: summarizeNodeForDebug(node.nextSibling),
        childCount: node.childNodes.length,
        childPreview: Array.from(node.childNodes)
          .slice(0, 6)
          .map((child) => summarizeNodeForDebug(child))
      });
    }

    // 将 show_image 内误被包裹的内容移动到当前节点之后，保持原有顺序
    if (node.parentNode && node.firstChild) {
      const parent = node.parentNode;
      const ref = node.nextSibling; // 可能为 null，insertBefore 会当 append
      const children = Array.from(node.childNodes);
      if (verbose) {
        debugShowImageLog('render:unwrap-children', {
          renderId,
          src: node.getAttribute('src') || '',
          moveCount: children.length,
          ref: summarizeNodeForDebug(ref),
          children: children.slice(0, 10).map((child) => summarizeNodeForDebug(child))
        });
      }
      children.forEach((child) => parent.insertBefore(child, ref));
    }

    const rawSrc = node.getAttribute('src') || '';
    const mappedSrc = normalizeShowImageSrc(rawSrc);
    if (!mappedSrc) {
      // 不再静默吞掉：渲染可见错误占位，方便定位路径问题
      const figure = document.createElement('figure');
      figure.className =
        'chat-inline-card chat-inline-card--image chat-inline-image chat-inline-card--error chat-inline-image--error';
      const tip = document.createElement('div');
      tip.className = 'chat-inline-card__error chat-inline-image__error';
      tip.textContent = rawSrc
        ? t('appCore.imageUnsupportedPath', { src: rawSrc })
        : t('appCore.imageMissingPath');
      figure.appendChild(tip);
      node.replaceChildren(figure);
      node.setAttribute('data-rendered', '1');
      node.setAttribute('data-rendered-error', 'invalid-src');
      if (verbose) debugShowImageLog('render:invalid-src', { renderId, rawSrc });
      return;
    }
    const alt = node.getAttribute('alt') || '';
    const safeAlt = escapeHtml(alt.trim());
    const figure = document.createElement('figure');
    figure.className = 'chat-inline-card chat-inline-card--image chat-inline-image';

    const img = document.createElement('img');
    img.loading = 'lazy';
    img.src = mappedSrc;
    img.alt = safeAlt;
    img.onerror = () => {
      figure.classList.add('chat-inline-card--error', 'chat-inline-image--error');
      const tip = document.createElement('div');
      tip.className = 'chat-inline-card__error chat-inline-image__error';
      tip.textContent = t('appCore.imageLoadFailed');
      figure.appendChild(tip);
    };
    figure.appendChild(img);

    if (safeAlt) {
      const caption = document.createElement('figcaption');
      caption.className = 'chat-inline-card__caption';
      caption.innerHTML = safeAlt;
      figure.appendChild(caption);
    }

    node.replaceChildren(figure);
    node.setAttribute('data-rendered', '1');
    if (verbose) {
      debugShowImageLog('render:node-done', {
        renderId,
        rawSrc,
        mappedSrc,
        alt
      });
    }
  });

  htmlNodes.forEach((node) => {
    markShowTagDrawingActive();
    if (!node.isConnected) return;
    const inStreaming = !!node.closest('.streaming-text');
    const pathKey = buildNodePathKey(node);
    const isPartial = node.getAttribute('data-partial') === '1';
    const encoded = node.getAttribute('data-encoded') || '';
    const jsMode = readShowHtmlJsMode(node);
    const computedRatioKey = readShowHtmlRatio(node);
    let ratioKey = computedRatioKey;

    // 流式阶段：一旦 show_html 已经出现完整闭合（非 partial），冻结该 path 的已完成快照；
    // 后续即使继续输出其他普通文本，也复用该快照，避免已完成 show_html 反复刷新。
    if (inStreaming && !isPartial && encoded) {
      const frozen = showHtmlCompletedSnapshotByPath.get(pathKey);
      if (!frozen || frozen.encoded !== encoded || frozen.ratioKey !== ratioKey) {
        showHtmlCompletedSnapshotByPath.set(pathKey, {
          encoded,
          ratioKey
        });
      }
    } else if (!inStreaming) {
      showHtmlCompletedSnapshotByPath.delete(pathKey);
    }
    const frozenSnapshot = inStreaming ? showHtmlCompletedSnapshotByPath.get(pathKey) : null;
    const effectiveEncoded = frozenSnapshot?.encoded || encoded;
    if (frozenSnapshot?.ratioKey) {
      ratioKey = frozenSnapshot.ratioKey;
    }

    // 流式期间锁定卡片比例（尺寸由 CSS 自适应，无需锁像素）：
    // 流式初期 ratio 属性可能未输出完整而先落到 1:1，后续允许升级到真实比例
    if (inStreaming) {
      const locked = showHtmlStreamingRatioLockByPath.get(pathKey);
      if (locked) {
        const shouldUpgradeRatio =
          ratioKey !== locked && (locked === '1:1' || ratioKey !== '1:1');
        if (shouldUpgradeRatio) {
          ratioKey = computedRatioKey;
          showHtmlStreamingRatioLockByPath.set(pathKey, ratioKey);
        } else {
          ratioKey = locked;
        }
      } else {
        showHtmlStreamingRatioLockByPath.set(pathKey, ratioKey);
      }
    } else {
      showHtmlStreamingRatioLockByPath.delete(pathKey);
    }

    // 卡片尺寸由 CSS 接管（width: min(...) + aspect-ratio，见 _chat-area.scss），
    // 这里只写入比例变量；窗口/容器尺寸变化时浏览器自动重算，无需 JS 监听 resize
    node.style.setProperty('--show-html-ar', String(SHOW_HTML_RATIO_VALUES[ratioKey] || 1));

    debugShowHtmlLog('render:node-seen', {
      renderId,
      inStreaming,
      pathKey,
      hasRendered: node.hasAttribute('data-rendered'),
      ratioAttr: node.getAttribute('ratio') || '',
      jsMode,
      ratioKey,
      partial: node.getAttribute('data-partial') || '',
      encodedLength: encoded.length,
      effectiveEncodedLength: effectiveEncoded.length,
      innerLength: (node.innerHTML || '').length
    });

    // js="on"：在闭合前只显示占位，不做实时渲染，避免 iframe/srcdoc 反复重建与闪烁
    if (jsMode === 'on' && isPartial) {
      let pending = showHtmlJsPendingRenderByPath.get(pathKey);
      if (!pending) {
        const wrapper = document.createElement('div');
        wrapper.className =
          'chat-inline-card chat-inline-card--html chat-inline-card--pending chat-inline-html chat-inline-html--pending';
        const host = document.createElement('div');
        host.className = 'chat-inline-card__body chat-inline-html__host chat-inline-html__host--pending';
        const tip = document.createElement('div');
        tip.className = 'chat-inline-card__pending-tip chat-inline-html__pending-tip';
        tip.textContent = t('appCore.renderingContent');
        host.appendChild(tip);
        wrapper.appendChild(host);
        pending = { wrapper, host, tip };
        showHtmlJsPendingRenderByPath.set(pathKey, pending);
      }
      node.replaceChildren(pending.wrapper);
      node.setAttribute('data-rendered', '1');
      debugShowHtmlLog('render:node-js-pending', {
        renderId,
        pathKey,
        ratioKey
      });
      return;
    }

    // js="on"：闭合后使用 sandbox iframe 隔离执行（不依赖 shadow root 直渲染）。
    if (jsMode === 'on') {
      const rawHtml = effectiveEncoded ? decodeBase64Utf8(effectiveEncoded) : node.innerHTML || '';
      const srcdoc = buildShowHtmlIframeSrcdoc(rawHtml);
      let persistent = showHtmlJsIframeRenderByPath.get(pathKey);
      if (!persistent) {
        const wrapper = document.createElement('div');
        wrapper.className =
          'chat-inline-card chat-inline-card--html chat-inline-card--iframe chat-inline-html chat-inline-html--iframe';
        const iframe = document.createElement('iframe');
        iframe.className = 'chat-inline-html__iframe';
        iframe.setAttribute('sandbox', 'allow-scripts');
        iframe.setAttribute('referrerpolicy', 'no-referrer');
        iframe.setAttribute('loading', 'lazy');
        // 空 allow 以外的特性显式拒绝：卡片是固定尺寸展示区，无全屏等合理需求
        iframe.setAttribute('allow', "fullscreen 'none'");
        wrapper.appendChild(iframe);
        persistent = {
          encoded: '',
          ratioKey: '1:1',
          srcdoc: '',
          wrapper,
          iframe
        };
        showHtmlJsIframeRenderByPath.set(pathKey, persistent);
      }
      bindShowHtmlCardControls(persistent.wrapper, {
        onRefresh: () => {
          // 强制重新挂载同一份 srcdoc，触发 iframe 内页面重载
          const keep = persistent?.srcdoc || srcdoc;
          persistent.iframe.srcdoc =
            '<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>';
          requestAnimationFrame(() => {
            if (persistent?.iframe) {
              persistent.iframe.srcdoc = keep;
            }
          });
          debugShowHtmlLog('refresh:iframe', {
            pathKey,
            ratioKey: persistent?.ratioKey || ratioKey,
            srcdocLength: keep.length
          });
        },
        // 全屏直接复用当前 srcdoc（已含 CSP meta 与守卫脚本），沙箱保持 allow-scripts
        getFullscreenPayload: () => {
          const current = persistent?.srcdoc || '';
          if (!current) return null;
          return { srcdoc: current, allowScripts: true };
        }
      });
      const needsUpdate =
        persistent.encoded !== effectiveEncoded ||
        persistent.ratioKey !== ratioKey ||
        persistent.srcdoc !== srcdoc;
      if (needsUpdate) {
        persistent.iframe.srcdoc = srcdoc;
        persistent.encoded = effectiveEncoded;
        persistent.ratioKey = ratioKey;
        persistent.srcdoc = srcdoc;
      }
      if (!inStreaming || isPartial || needsUpdate || !node.hasAttribute('data-rendered')) {
        node.replaceChildren(persistent.wrapper);
      }
      node.setAttribute('data-rendered', '1');
      showHtmlJsPendingRenderByPath.delete(pathKey);
      debugShowHtmlLog('render:node-js-iframe', {
        renderId,
        pathKey,
        ratioKey,
        encodedLength: effectiveEncoded.length,
        srcdocLength: srcdoc.length,
        needsUpdate
      });
      return;
    }

    if (inStreaming) {
      const now = Date.now();
      const lastTs = showHtmlStreamingRenderTsByPath.get(pathKey) || 0;
      if (now - lastTs < SHOW_HTML_STREAMING_RENDER_INTERVAL_MS) {
        const persistent = showHtmlPersistentRenderByPath.get(pathKey);
        if (persistent) {
          node.replaceChildren(persistent.wrapper);
          node.setAttribute('data-rendered', '1');
          debugShowHtmlLog('render:node-throttled-rehydrate', {
            renderId,
            pathKey,
            ratioKey
          });
        }
        debugShowHtmlLog('render:node-throttled', {
          renderId,
          pathKey,
          now,
          lastTs,
          interval: SHOW_HTML_STREAMING_RENDER_INTERVAL_MS
        });
        return;
      }
      showHtmlStreamingRenderTsByPath.set(pathKey, now);
    } else {
      showHtmlStreamingRenderTsByPath.delete(pathKey);
    }

    const rawHtml = effectiveEncoded ? decodeBase64Utf8(effectiveEncoded) : node.innerHTML || '';
    const safeHtml = sanitizeShowHtmlContent(rawHtml);
    debugShowHtmlLog('render:node-content', {
      renderId,
      pathKey,
      jsMode,
      encodedLength: encoded.length,
      effectiveEncodedLength: effectiveEncoded.length,
      rawLength: rawHtml.length,
      safeLength: safeHtml.length,
      ratioKey
    });

    let persistent = showHtmlPersistentRenderByPath.get(pathKey);
    if (!persistent) {
      const wrapper = document.createElement('div');
      wrapper.className = 'chat-inline-card chat-inline-card--html chat-inline-html';
      const host = document.createElement('div');
      host.className = 'chat-inline-card__body chat-inline-html__host';
      const shadow = host.attachShadow({ mode: 'open' });
      const style = document.createElement('style');
      style.textContent = `
      :host {
        display: block;
        box-sizing: border-box;
        width: 100%;
        height: 100%;
        overflow: auto;
        background: transparent;
        color: inherit;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 14px;
        line-height: 1.5;
      }
      *, *::before, *::after {
        box-sizing: border-box;
      }
      .show-html-root {
        width: 100%;
        height: 100%;
        min-height: 100%;
        overflow: auto;
        background: transparent;
        scrollbar-width: thin;
        scrollbar-color: rgba(121, 109, 94, 0.48) transparent;
      }
      .show-html-root::-webkit-scrollbar {
        width: 8px;
        height: 8px;
      }
      .show-html-root::-webkit-scrollbar-track {
        background: transparent;
      }
      .show-html-root::-webkit-scrollbar-thumb {
        background: rgba(121, 109, 94, 0.48);
        border-radius: 8px;
      }
      .show-html-root::-webkit-scrollbar-thumb:hover {
        background: rgba(121, 109, 94, 0.62);
      }
    `;
      const root = document.createElement('div');
      root.className = 'show-html-root';
      shadow.appendChild(style);
      shadow.appendChild(root);
      wrapper.appendChild(host);
      persistent = {
        encoded: '',
        safeHtml: '',
        ratioKey: '1:1',
        wrapper,
        host,
        root
      };
      showHtmlPersistentRenderByPath.set(pathKey, persistent);
    }
    bindShowHtmlCardControls(persistent.wrapper, {
      onRefresh: () => {
        const keep = persistent?.safeHtml || '';
        persistent.root.innerHTML = '';
        requestAnimationFrame(() => {
          if (persistent?.root) {
            persistent.root.innerHTML = keep;
          }
        });
        debugShowHtmlLog('refresh:shadow-root', {
          pathKey,
          ratioKey: persistent?.ratioKey || ratioKey,
          safeLength: keep.length
        });
      },
      // js=off 内容已 sanitize，全屏走禁脚本沙箱 iframe，渲染效果与 Shadow DOM 一致
      getFullscreenPayload: () => {
        const current = persistent?.safeHtml || '';
        if (!current.trim()) return null;
        return { srcdoc: buildShowHtmlIframeSrcdoc(current), allowScripts: false };
      }
    });

    const needsUpdate =
      persistent.encoded !== effectiveEncoded ||
      persistent.ratioKey !== ratioKey;
    if (needsUpdate) {
      persistent.root.innerHTML = safeHtml;
      persistent.encoded = effectiveEncoded;
      persistent.safeHtml = safeHtml;
      persistent.ratioKey = ratioKey;
      debugShowHtmlLog('render:node-persistent-update', {
        renderId,
        pathKey,
        encodedLength: effectiveEncoded.length,
        ratioKey
      });
    } else {
      debugShowHtmlLog('render:node-persistent-reuse', {
        renderId,
        pathKey,
        encodedLength: effectiveEncoded.length
      });
    }

    if (!inStreaming || isPartial || needsUpdate || !node.hasAttribute('data-rendered')) {
      node.replaceChildren(persistent.wrapper);
    }
    node.setAttribute('data-rendered', '1');
    const rect = persistent.wrapper.getBoundingClientRect();
    debugShowHtmlLog('render:node-done', {
        renderId,
        pathKey,
        jsMode,
        ratioKey,
        rect: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height)
      },
      partial: node.getAttribute('data-partial') || '',
      inStreaming
    });

    if (verbose) {
      debugShowImageLog('render:show-html-done', {
        renderId,
        ratioKey,
        contentLength: rawHtml.length
      });
    }
  });

  // ===== show_file 预览卡片 =====
  fileNodes.forEach((node) => {
    if (!node.isConnected || node.hasAttribute('data-rendered')) return;
    renderShowFileCard(node);
    node.setAttribute('data-rendered', '1');
  });

  // ===== download:// 链接事件绑定 =====
  bindDownloadLinks(root);

  debugShowImageLog('render:end', {
    renderId,
    orderAfter: collectShowImageOrder(root)
  });
}

let showImageObserver: MutationObserver | null = null;
let showContainerObserver: MutationObserver | null = null;
let showTagRenderScheduled = false;
let showTagBindRetryTimer: number | null = null;
let showTagObservedContainer: Element | null = null;
const showHtmlStreamingRenderTsByPath = new Map<string, number>();
const SHOW_HTML_STREAMING_RENDER_INTERVAL_MS = 180;
const showHtmlCompletedSnapshotByPath = new Map<
  string,
  {
    encoded: string,
    ratioKey: string
  }
>();
// 流式期间只锁定比例；具体尺寸由 CSS min()+aspect-ratio 自适应计算
const showHtmlStreamingRatioLockByPath = new Map<string, string>();
const showHtmlPersistentRenderByPath = new Map<
  string,
  {
    encoded: string,
    safeHtml: string,
    ratioKey: string,
    wrapper: HTMLElement,
    host: HTMLElement,
    root: HTMLElement
  }
>();
const showHtmlJsPendingRenderByPath = new Map<
  string,
  {
    wrapper: HTMLElement,
    host: HTMLElement,
    tip: HTMLElement
  }
>();
const showHtmlJsIframeRenderByPath = new Map<
  string,
  {
    encoded: string,
    ratioKey: string,
    srcdoc: string,
    wrapper: HTMLElement,
    iframe: HTMLIFrameElement
  }
>();
let layoutDebugObserver: MutationObserver | null = null;
let layoutDebugResizeObserver: ResizeObserver | null = null;
let layoutDebugStarted = false;
let layoutDebugBindRetryTimer: number | null = null;
let layoutDebugCount = 0;
const LAYOUT_DEBUG_MAX = 300;
const layoutDebugLastTsByKey = new Map<string, number>();

function scheduleShowTagRender(container: Element) {
  if (showTagRenderScheduled) return;
  showTagRenderScheduled = true;
  const run = () => {
    showTagRenderScheduled = false;
    renderShowImages(container);
  };
  if (typeof queueMicrotask === 'function') {
    queueMicrotask(run);
    return;
  }
  Promise.resolve().then(run);
}

function getShowTagContainer() {
  return document.querySelector('.messages-area');
}

function isLayoutDebugEnabled() {
  // 默认关闭：排障时通过 window.__LAYOUT_DEBUG__ = true 或 localStorage.layoutDebug = '1' 显式打开
  if (typeof window === 'undefined') return false;
  try {
    const explicitFlag = (window as any).__LAYOUT_DEBUG__;
    if (explicitFlag === false || explicitFlag === '0') return false;
    if (explicitFlag === true || explicitFlag === '1') return true;
    const localFlag = window.localStorage?.getItem('layoutDebug');
    if (localFlag === '0' || localFlag === 'false') return false;
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

function layoutDebugLog(event: string, payload: Record<string, any> = {}, key = event) {
  if (!isLayoutDebugEnabled()) return;
  if (layoutDebugCount >= LAYOUT_DEBUG_MAX) return;
  const now = Date.now();
  const lastTs = layoutDebugLastTsByKey.get(key) || 0;
  if (now - lastTs < 120) return;
  layoutDebugLastTsByKey.set(key, now);
  layoutDebugCount += 1;
  if (layoutDebugCount === LAYOUT_DEBUG_MAX) {
    console.warn('[LAYOUT_DEBUG]', 'log-limit-reached', { max: LAYOUT_DEBUG_MAX });
    return;
  }
  console.log('[LAYOUT_DEBUG]', event, payload);
}

function elementRectSummary(el: Element | null) {
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  return {
    className: (el as HTMLElement).className || '',
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  };
}

function setupLayoutDebugObservers() {
  if (layoutDebugStarted || !isLayoutDebugEnabled()) return;
  const trackedSelectors = [
    '.main-container',
    '.workspace-region',
    '.workspace-panel',
    '.chat-container',
    '.messages-area'
  ];
  const tracked = trackedSelectors
    .map((selector) => document.querySelector(selector))
    .filter((el): el is Element => !!el);
  if (tracked.length === 0) {
    layoutDebugLog('layout:setup-wait', { reason: 'targets-not-mounted' }, 'layout:setup-wait');
    if (layoutDebugBindRetryTimer) {
      window.clearTimeout(layoutDebugBindRetryTimer);
    }
    layoutDebugBindRetryTimer = window.setTimeout(() => {
      layoutDebugBindRetryTimer = null;
      setupLayoutDebugObservers();
    }, 250);
    return;
  }
  layoutDebugStarted = true;
  layoutDebugLog('layout:setup', {
    trackedCount: tracked.length,
    tracked: tracked.map((el) => (el as HTMLElement).className || el.tagName.toLowerCase())
  });

  const snapshot = () => {
    layoutDebugLog(
      'layout:snapshot',
      {
        window: {
          innerWidth: window.innerWidth,
          innerHeight: window.innerHeight
        },
        main: elementRectSummary(document.querySelector('.main-container')),
        workspace: elementRectSummary(document.querySelector('.workspace-region')),
        panel: elementRectSummary(document.querySelector('.workspace-panel')),
        chat: elementRectSummary(document.querySelector('.chat-container')),
        messages: elementRectSummary(document.querySelector('.messages-area'))
      },
      'layout:snapshot'
    );
  };
  snapshot();

  layoutDebugObserver = new MutationObserver((mutations) => {
    const interesting = mutations.filter((m) => {
      if (!(m.target instanceof Element)) return false;
      const className =
        typeof (m.target as HTMLElement).className === 'string'
          ? (m.target as HTMLElement).className
          : m.target.getAttribute('class') || '';
      return (
        m.type === 'attributes' &&
        (tracked.some((el) => el === m.target) ||
          className.includes('chat-container') ||
          className.includes('workspace-panel') ||
          className.includes('main-container'))
      );
    });
    if (!interesting.length) return;
    layoutDebugLog('layout:mutation', {
      count: interesting.length,
      items: interesting.slice(0, 8).map((m) => ({
        attr: m.attributeName,
        target: summarizeNodeForDebug(m.target)
      }))
    });
    snapshot();
  });
  layoutDebugObserver.observe(document.body, {
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style']
  });

  layoutDebugResizeObserver = new ResizeObserver((entries) => {
    if (!entries.length) return;
    layoutDebugLog('layout:resize', {
      items: entries.slice(0, 8).map((entry) => ({
        target: summarizeNodeForDebug(entry.target),
        width: Math.round(entry.contentRect.width),
        height: Math.round(entry.contentRect.height)
      }))
    });
    snapshot();
  });
  tracked.forEach((el) => layoutDebugResizeObserver?.observe(el));
  window.addEventListener('resize', snapshot);
}

function buildNodePathKey(node: Element) {
  const getIndexWithin = (el: Element, selector: string) => {
    const list = Array.from(document.querySelectorAll(selector));
    const idx = list.indexOf(el);
    return idx >= 0 ? idx : -1;
  };

  const parts: string[] = [];
  const messageBlock = node.closest('.message-block');
  if (messageBlock) {
    parts.push(`msg:${getIndexWithin(messageBlock, '.message-block')}`);
  }
  const actionItem = node.closest('.action-item');
  if (actionItem) {
    parts.push(`action:${getIndexWithin(actionItem, '.action-item')}`);
  }
  const textContent = node.closest('.text-content');
  if (textContent) {
    parts.push(`text:${getIndexWithin(textContent, '.text-content')}`);
  }

  let cur: Element | null = node;
  while (cur && cur !== document.body && parts.length < 12) {
    const parent = cur.parentElement;
    if (!parent) break;
    const index = Array.prototype.indexOf.call(parent.children, cur);
    parts.push(`${cur.tagName.toLowerCase()}:${index}`);
    if (cur.classList.contains('text-content')) break;
    cur = parent;
  }
  return parts.reverse().join('/');
}

export function setupShowImageObserver() {
  if (showImageObserver || showContainerObserver) return;
  setupLayoutDebugObservers();
  const bind = () => {
    const container = getShowTagContainer();
    if (!container) {
      if (showTagBindRetryTimer) {
        window.clearTimeout(showTagBindRetryTimer);
      }
      showTagBindRetryTimer = window.setTimeout(bind, 250);
      return;
    }
    if (showTagBindRetryTimer) {
      window.clearTimeout(showTagBindRetryTimer);
      showTagBindRetryTimer = null;
    }
    if (showTagObservedContainer === container && showImageObserver) return;
    if (showImageObserver) {
      showImageObserver.disconnect();
      showImageObserver = null;
    }
    showTagObservedContainer = container;

    debugShowImageLog('observer:setup', {
      container: summarizeNodeForDebug(container),
      debugMode: getShowImageDebugMode(),
      version: SHOW_IMAGE_DEBUG_VERSION
    });
    renderShowImages(container);
    showImageObserver = new MutationObserver((mutations) => {
      const debugMode = getShowImageDebugMode();
      const verbose = debugMode === 'verbose';
      const hasShowImageRelatedMutation = mutations.some((mutation) => {
        const targetIsShowImage =
          mutation.target instanceof Element &&
          ['show_image', 'show_html', 'show_file', 'show-image', 'show-html', 'show-file'].includes(
            mutation.target.tagName.toLowerCase()
          );
        const addedHasShowTag = Array.from(mutation.addedNodes).some(
          (n) =>
            n instanceof Element &&
            (['show_image', 'show_html', 'show_file', 'show-image', 'show-html', 'show-file'].includes(
              n.tagName.toLowerCase()
            ) || !!n.querySelector?.('show_image,show_html,show_file,show-image,show-html,show-file,a.md-download-link,a[data-download="1"]'))
        );
        return targetIsShowImage || addedHasShowTag;
      });
      if (!hasShowImageRelatedMutation) return;
      debugShowImageLog('observer:show-image-mutation', {
        mutationCount: mutations.length,
        mutations: verbose
          ? mutations.slice(0, 20).map((m) => ({
              type: m.type,
              target: summarizeNodeForDebug(m.target),
              added: Array.from(m.addedNodes)
                .slice(0, 6)
                .map((n) => summarizeNodeForDebug(n)),
              removed: Array.from(m.removedNodes)
                .slice(0, 6)
                .map((n) => summarizeNodeForDebug(n))
            }))
          : undefined
      });
      scheduleShowTagRender(container);
    });
    showImageObserver.observe(container, { childList: true, subtree: true });
  };
  bind();
  showContainerObserver = new MutationObserver(() => {
    const latest = getShowTagContainer();
    if (!latest) return;
    if (latest !== showTagObservedContainer) {
      bind();
    }
  });
  showContainerObserver.observe(document.body, { childList: true, subtree: true });
}

export function teardownShowImageObserver() {
  if (showTagBindRetryTimer) {
    window.clearTimeout(showTagBindRetryTimer);
    showTagBindRetryTimer = null;
  }
  if (showImageObserver) {
    showImageObserver.disconnect();
    showImageObserver = null;
  }
  if (showContainerObserver) {
    showContainerObserver.disconnect();
    showContainerObserver = null;
  }
  if (layoutDebugObserver) {
    layoutDebugObserver.disconnect();
    layoutDebugObserver = null;
  }
  if (layoutDebugResizeObserver) {
    layoutDebugResizeObserver.disconnect();
    layoutDebugResizeObserver = null;
  }
  if (layoutDebugBindRetryTimer) {
    window.clearTimeout(layoutDebugBindRetryTimer);
    layoutDebugBindRetryTimer = null;
  }
  layoutDebugStarted = false;
  layoutDebugCount = 0;
  layoutDebugLastTsByKey.clear();
  showHtmlStreamingRenderTsByPath.clear();
  showHtmlCompletedSnapshotByPath.clear();
  showHtmlStreamingRatioLockByPath.clear();
  showHtmlPersistentRenderByPath.clear();
  showHtmlJsPendingRenderByPath.clear();
  showHtmlJsIframeRenderByPath.clear();
  showTagObservedContainer = null;
  // 菜单/全屏层是全局单例，卡片 Map 清空后其引用的 wrapper 已卸载，必须一并关闭
  closeShowHtmlCardMenu();
  closeShowHtmlFullscreen();
}

function updateViewportHeightVar() {
  const docEl = document.documentElement;
  const visualViewport = window.visualViewport;

  if (visualViewport) {
    const vh = visualViewport.height;
    const bottomInset = Math.max(
      0,
      (window.innerHeight || docEl.clientHeight || vh) -
        visualViewport.height -
        visualViewport.offsetTop
    );
    docEl.style.setProperty('--app-viewport', `${vh}px`);
    docEl.style.setProperty('--app-bottom-inset', `${bottomInset}px`);
  } else {
    const height = window.innerHeight || docEl.clientHeight;
    if (height) {
      docEl.style.setProperty('--app-viewport', `${height}px`);
    }
    docEl.style.setProperty('--app-bottom-inset', 'env(safe-area-inset-bottom, 0px)');
  }
}

// ===== show_file 预览卡片渲染 =====

function dispatchShowFileDownload(path: string) {
  // 直接 fetch 下载接口，不走 Vue 组件链
  const url = `/api/download/file?path=${encodeURIComponent(path)}`;
  const name = path.split('/').pop() || 'file';

  // Android App 内通过原生桥接下载，避免 WebView 对 a.download / blob URL 支持不佳导致失败
  const androidBridge = (window as any).AndroidDownloadBridge;
  if (androidBridge && typeof androidBridge.downloadFile === 'function') {
    try {
      const absoluteUrl = new URL(url, window.location.href).href;
      androidBridge.downloadFile(absoluteUrl, name);
      return;
    } catch (e) {
      console.warn('[show_file] Android 桥接下载失败，回退:', e);
    }
  }

  fetch(url)
    .then((resp) => {
      if (!resp.ok) {
        return resp.json().then((j) => {
          throw new Error(j.error || j.message || resp.statusText);
        }).catch(() => {
          throw new Error(resp.statusText || t('common.downloadFailed'));
        });
      }
      return resp.blob();
    })
    .then((blob) => {
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    })
    .catch((err) => {
      console.warn('[show_file] 下载失败:', err);
      // 兜底：直接跳转
      window.open(url, '_blank');
    });
}

// 跟踪已挂载的 show_file Vue 应用，便于卸载
const showFileAppMap = new WeakMap<Element, any>();

/**
 * 将 <show_file> 节点渲染为 Vue 预览卡片组件。
 * 只读取 path 属性，预览内容直接在卡片内部展开。
 */
function renderShowFileCard(node: Element) {
  const path = (node.getAttribute('path') || '').trim().replace(/^\/+/, '');

  if (!path) {
    node.replaceChildren(document.createTextNode(t('appCore.showFileMissingPath')));
    return;
  }

  // 清理旧实例
  const oldApp = showFileAppMap.get(node);
  if (oldApp) {
    try { oldApp.unmount(); } catch {}
    showFileAppMap.delete(node);
  }

  const mountEl = document.createElement('div');
  mountEl.className = 'show-file-card-root';
  node.replaceChildren(mountEl);

  const app = createApp(ShowFileCard, { path });
  app.mount(mountEl);
  showFileAppMap.set(node, app);
}

/**
 * 绑定 download:// 链接的点击事件。
 * markdown 里的 [文件](download:///path) 会被渲染为 <a class="md-download-link" href="download://..." data-path="/path">。
 */
function bindDownloadLinks(root: ParentNode) {
  // 优先用 data-download 属性选择（不被 sanitize 影响），
  // md-download-link class 作为兼容回退
  const links = root.querySelectorAll<HTMLAnchorElement>(
    'a[data-download="1"]:not([data-download-bound]), a.md-download-link:not([data-download-bound])'
  );
  links.forEach((link) => {
    link.setAttribute('data-download-bound', '1');
    link.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const path = link.getAttribute('data-path') || '';
      if (path) {
        dispatchShowFileDownload(path);
      }
    });
  });
}

if (typeof window !== 'undefined') {
  window.katex = katex;

  updateViewportHeightVar();
  window.addEventListener('resize', updateViewportHeightVar);
  window.addEventListener('orientationchange', updateViewportHeightVar);
  window.addEventListener('pageshow', updateViewportHeightVar);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', updateViewportHeightVar);
    window.visualViewport.addEventListener('scroll', updateViewportHeightVar);
  }
}
