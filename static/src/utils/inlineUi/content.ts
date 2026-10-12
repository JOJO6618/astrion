import DOMPurify from 'dompurify';

export interface InlineUiScript {
  attributes: Record<string, string>;
  content: string;
}

export interface InlineUiHandler {
  key: string;
  attributes: Record<string, string>;
}

export interface InlineUiContent {
  html: string;
  scripts: InlineUiScript[];
  handlers: InlineUiHandler[];
}

/** Preserve completed nodes while the unfinished tail continues to grow. */
export function patchInlineUi(parent: Node, next: Node): void {
  const children = Array.from(next.childNodes);
  let current = parent.firstChild;
  for (const incoming of children) {
    if (current && current.nodeType === 1 && incoming.nodeType === 1) {
      const target = incoming as Element;
      const id = target.getAttribute('id');
      if (id && (current as Element).getAttribute('id') !== id) {
        const match = Array.from(parent.childNodes).find(
          (child) => child.nodeType === 1 && (child as Element).getAttribute('id') === id
        );
        if (match) {
          parent.insertBefore(match, current);
          current = match;
        }
      }
    }
    if (!current) {
      parent.appendChild(incoming.cloneNode(true));
      continue;
    }
    if (current.nodeType !== incoming.nodeType || current.nodeName !== incoming.nodeName) {
      const replacement = incoming.cloneNode(true);
      parent.replaceChild(replacement, current);
      current = replacement.nextSibling;
      continue;
    }
    if (current.nodeType === 1) {
      const element = current as Element;
      const wanted = incoming as Element;
      for (const attr of Array.from(element.attributes)) {
        if (!wanted.hasAttribute(attr.name)) element.removeAttribute(attr.name);
      }
      for (const attr of Array.from(wanted.attributes)) {
        if (element.getAttribute(attr.name) !== attr.value) element.setAttribute(attr.name, attr.value);
      }
      patchInlineUi(element, wanted);
    } else if (current.nodeValue !== incoming.nodeValue) {
      current.nodeValue = incoming.nodeValue;
    }
    current = current.nextSibling;
  }
  while (current) {
    const following = current.nextSibling;
    parent.removeChild(current);
    current = following;
  }
}

export function prepareInlineUi(raw: string, executable: boolean): InlineUiContent {
  const doc = new DOMParser().parseFromString(raw, 'text/html');
  const scripts: InlineUiScript[] = [];
  const handlers: InlineUiHandler[] = [];
  // Keep style nodes even when the model places them before the fragment's body.
  doc.querySelectorAll('script').forEach((script) => {
    const type = (script.getAttribute('type') || '').trim().toLowerCase();
    if (executable && ['', 'text/javascript', 'application/javascript', 'module'].includes(type)) {
      const attributes: Record<string, string> = {};
      for (const name of ['src', 'type', 'integrity', 'crossorigin', 'referrerpolicy', 'nomodule']) {
        if (script.hasAttribute(name)) attributes[name] = script.getAttribute(name) || '';
      }
      if (!attributes.src || /^https:\/\//i.test(attributes.src)) {
        scripts.push({ attributes, content: script.textContent || '' });
      }
    }
    script.remove();
  });
  doc.querySelectorAll('*').forEach((element, index) => {
    // These keys are produced here, never accepted from generated content.
    element.removeAttribute('data-inline-ui-handler');
    const attributes: Record<string, string> = {};
    for (const attr of Array.from(element.attributes)) {
      if (/^on/i.test(attr.name)) {
        if (executable) attributes[attr.name] = attr.value;
        element.removeAttribute(attr.name);
      }
    }
    if (Object.keys(attributes).length) {
      const key = String(index);
      element.setAttribute('data-inline-ui-handler', key);
      handlers.push({ key, attributes });
    }
  });
  const styles = Array.from(doc.head.querySelectorAll('style')).map((style) => style.outerHTML).join('');
  const html = DOMPurify.sanitize(styles + doc.body.innerHTML, {
    USE_PROFILES: { html: true, svg: true, svgFilters: true, mathMl: true },
    FORCE_BODY: true,
    ADD_TAGS: ['style'],
    FORBID_TAGS: ['script', 'noscript', 'iframe', 'object', 'embed', 'link', 'meta', 'base'],
    FORBID_ATTR: ['autofocus'],
    ALLOW_DATA_ATTR: true,
    ALLOW_ARIA_ATTR: true
  }) as string;
  // Imports and executable CSS are not needed for inline content.
  const safe = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  safe.querySelectorAll('style, [style]').forEach((element) => {
    const clean = (css: string) => css
      .replace(/@import[^;]*(?:;|$)/gi, '')
      .replace(/expression\s*\([^)]*\)/gi, '')
      .replace(/url\s*\(\s*['"]?\s*javascript:[^)]*\)/gi, 'url(about:blank)');
    if (element.tagName === 'STYLE') element.textContent = clean(element.textContent || '');
    else element.setAttribute('style', clean(element.getAttribute('style') || ''));
  });
  return { html: safe.body.innerHTML, scripts, handlers };
}
