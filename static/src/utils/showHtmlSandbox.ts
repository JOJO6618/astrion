/**
 * show_html 沙箱与 srcdoc 构建工具（纯函数，无 DOM 依赖）。
 * 从 app/bootstrap.ts 抽取，供 HTML 卡片渲染管线与文件卡片（ShowFileCard）全屏预览共用。
 */

/**
 * show_html js=on 沙箱 CSP（通过 srcdoc 内 <meta> 交付，必须位于任何资源引用之前）。
 * - connect-src 'none'：禁 fetch/XHR/WebSocket/EventSource，杜绝数据外发与内网探测
 * - script/style/img/font/media 允许 https:：保留卡片引用 CDN 资源与外链图的能力
 *   （残余风险：GET 类资源加载可作为信标外发少量数据，已与需求方确认接受）
 * - frame/child/worker-src 'none'：禁嵌套远程框架与 Worker；object/form/base 收紧
 * 注：iframe 的 csp 属性对 srcdoc 的支持仍是未决规范（w3c/webappsec-csp#492），故走 meta 交付。
 */
export const SHOW_HTML_IFRAME_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline' https:",
  "style-src 'unsafe-inline' https:",
  "img-src data: blob: https:",
  "font-src data: https:",
  "media-src data: blob: https:",
  "connect-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
  "frame-src 'none'",
  "child-src 'none'",
  "worker-src 'none'",
  "object-src 'none'"
].join('; ');

/**
 * show_html js=on 沙箱守卫脚本（注入 srcdoc 最前，先于任何卡片脚本执行）。
 * 解决问题：iframe 内 scrollIntoView()/focus() 的滚动副作用会穿透 iframe 边界滚动宿主页面
 * （CSSOM View 规范行为，sandbox 属性与 CSS 均无法声明式禁止，见 w3c/csswg-drafts#7134）。
 * 策略：重写相关 API，使滚动只发生在本 iframe 文档内部；拦截同文档锚点导航的默认滚动；
 * 对同源子 frame 递归修补以收窄“干净原型”绕过窗口。
 * 已知边界：不防“针对本守卫的确定性绕过”（威胁模型为提示注入生成的一般恶意内容）。
 */
export const SHOW_HTML_IFRAME_GUARD_SCRIPT = `(function () {
  'use strict';
  if (window.__astrionSandboxGuard) return;
  window.__astrionSandboxGuard = true;

  function scrollingContainerList(el) {
    var list = [];
    var node = el.parentElement;
    while (node && node !== document.body && node !== document.documentElement) {
      var s = window.getComputedStyle(node);
      if (/(auto|scroll|overlay)/.test(String(s.overflow) + String(s.overflowY) + String(s.overflowX))) {
        list.push(node);
      }
      node = node.parentElement;
    }
    list.push(document.scrollingElement || document.documentElement);
    return list;
  }

  function scrollOneContainer(container, el, block) {
    var isRoot = container === (document.scrollingElement || document.documentElement);
    var eRect = el.getBoundingClientRect();
    var viewH = isRoot ? window.innerHeight : container.clientHeight;
    var top = isRoot ? eRect.top : eRect.top - container.getBoundingClientRect().top;
    var bottom = top + eRect.height;
    var delta = 0;
    if (block === 'start') delta = top;
    else if (block === 'end') delta = bottom - viewH;
    else if (block === 'center') delta = top - (viewH - eRect.height) / 2;
    else if (top < 0) delta = top;
    else if (bottom > viewH) delta = Math.min(bottom - viewH, top);
    if (delta) container.scrollTop += delta;
  }

  function localScrollIntoView(el, arg) {
    var block = 'start';
    if (arg === false) block = 'end';
    else if (arg && typeof arg === 'object' && typeof arg.block === 'string') block = arg.block;
    var containers = scrollingContainerList(el);
    for (var i = 0; i < containers.length; i++) {
      scrollOneContainer(containers[i], el, block);
    }
  }

  Element.prototype.scrollIntoView = function (arg) { localScrollIntoView(this, arg); };
  if ('scrollIntoViewIfNeeded' in Element.prototype) {
    Element.prototype.scrollIntoViewIfNeeded = function () { localScrollIntoView(this, { block: 'nearest' }); };
  }

  var origFocus = HTMLElement.prototype.focus;
  HTMLElement.prototype.focus = function (options) {
    var opts = options && typeof options === 'object'
      ? Object.assign({}, options, { preventScroll: true })
      : { preventScroll: true };
    origFocus.call(this, opts);
    localScrollIntoView(this, { block: 'nearest' });
  };

  document.addEventListener('click', function (ev) {
    var t = ev.target;
    var anchor = t && t.closest ? t.closest('a[href^="#"]') : null;
    if (!anchor) return;
    var id = (anchor.getAttribute('href') || '').slice(1);
    ev.preventDefault();
    if (!id) return;
    var target = document.getElementById(id);
    if (target) {
      // Remove the id first so the hash update does not trigger the browser's default anchor scroll
      target.removeAttribute('id');
      try { window.location.hash = id; } catch (err) { /* ignore under opaque origin */ }
      target.setAttribute('id', id);
      localScrollIntoView(target, { block: 'start' });
    } else {
      try { window.location.hash = id; } catch (err) { /* ignore */ }
    }
  }, true);

  function patchFrame(win) {
    try {
      win.Element.prototype.scrollIntoView = Element.prototype.scrollIntoView;
      win.HTMLElement.prototype.focus = HTMLElement.prototype.focus;
    } catch (err) { /* skip cross-origin child frames */ }
  }
  new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (!n || n.nodeType !== 1) continue;
        var frames = [];
        if (n.tagName === 'IFRAME' || n.tagName === 'FRAME') frames.push(n);
        if (n.querySelectorAll) {
          var found = n.querySelectorAll('iframe,frame');
          for (var k = 0; k < found.length; k++) frames.push(found[k]);
        }
        for (var m = 0; m < frames.length; m++) {
          if (frames[m].contentWindow) patchFrame(frames[m].contentWindow);
        }
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
})();`;

/** 生成注入内容：CSP meta 必须早于任何资源引用，守卫脚本必须早于任何卡片脚本 */
export function buildShowHtmlSandboxInjection(): string {
  return `<meta http-equiv="Content-Security-Policy" content="${SHOW_HTML_IFRAME_CSP}" /><script>${SHOW_HTML_IFRAME_GUARD_SCRIPT}</script>`;
}

/** 模型自带完整 HTML 文档时，强制把沙箱注入插到 <head> 最前（无 head 则补一个） */
export function injectShowHtmlSandboxGuards(doc: string): string {
  const injection = buildShowHtmlSandboxInjection();
  const headMatch = /<head\b[^>]*>/i.exec(doc);
  if (headMatch) {
    const idx = headMatch.index + headMatch[0].length;
    return doc.slice(0, idx) + injection + doc.slice(idx);
  }
  const htmlMatch = /<html\b[^>]*>/i.exec(doc);
  if (htmlMatch) {
    const idx = htmlMatch.index + htmlMatch[0].length;
    return doc.slice(0, idx) + `<head>${injection}</head>` + doc.slice(idx);
  }
  return injection + doc;
}

/** 空 srcdoc：刷新/关闭全屏时挂载，强制 iframe 内页面重载并释放脚本与内存 */
export const EMPTY_SHOW_HTML_SRCDOC =
  '<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>';

export function buildShowHtmlIframeSrcdoc(rawHtml: string): string {
  const content = (rawHtml || '').trim();
  if (!content) {
    return EMPTY_SHOW_HTML_SRCDOC;
  }

  // 若模型已经输出完整 HTML 文档，强制注入沙箱 CSP 与守卫脚本后使用。
  if (/<html[\s>]/i.test(content)) {
    return injectShowHtmlSandboxGuards(content);
  }

  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    ${buildShowHtmlSandboxInjection()}
    <style>
      html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        min-height: 100%;
        background: transparent;
        scrollbar-width: thin;
        scrollbar-color: rgba(121, 109, 94, 0.48) transparent;
      }
      html::-webkit-scrollbar, body::-webkit-scrollbar {
        width: 8px;
        height: 8px;
      }
      html::-webkit-scrollbar-track, body::-webkit-scrollbar-track {
        background: transparent;
      }
      html::-webkit-scrollbar-thumb, body::-webkit-scrollbar-thumb {
        background: rgba(121, 109, 94, 0.48);
        border-radius: 8px;
      }
      html::-webkit-scrollbar-thumb:hover, body::-webkit-scrollbar-thumb:hover {
        background: rgba(121, 109, 94, 0.62);
      }
    </style>
  </head>
  <body>${content}</body>
</html>`;
}
