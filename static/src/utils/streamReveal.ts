/** Animate rendered text additions without moving boxes or replaying the old prefix. */
export function createStreamReveal(exclude = '') {
  let previous = '';
  let initialized = false;
  const active = new Map<HTMLElement, Animation>();
  const skipped = [
    'button',
    'script',
    'style',
    'iframe',
    'svg',
    'show_html',
    'show_file',
    'show_image',
    'show-html',
    'show-file',
    'show-image',
    '.math-block',
    '.math-inline',
    '.citation-chip',
    exclude
  ]
    .filter(Boolean)
    .join(',');

  function clear() {
    for (const [span, animation] of active) {
      animation.cancel();
      if (span.isConnected) span.replaceWith(...Array.from(span.childNodes));
    }
    active.clear();
  }

  function update(root: HTMLElement, streaming: boolean) {
    // Markdown and Prism may replace the active tail; discard its detached animations.
    for (const [span, animation] of active) {
      if (!span.isConnected) {
        animation.cancel();
        active.delete(span);
      }
    }
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!streaming || reducedMotion) clear();
    const nodes: Text[] = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        return node.parentElement?.closest(skipped)
          ? NodeFilter.FILTER_REJECT
          : NodeFilter.FILTER_ACCEPT;
      }
    });
    while (walker.nextNode()) nodes.push(walker.currentNode as Text);
    const current = nodes.map((node) => node.data).join('');
    const old = previous;
    previous = current;
    if (!initialized) {
      initialized = true;
      return;
    }
    if (
      !streaming ||
      current === old ||
      reducedMotion ||
      !nodes.some((node) => node.parentElement?.getClientRects().length)
    ) {
      return;
    }

    let start = 0;
    while (start < old.length && start < current.length && old[start] === current[start]) start++;
    let end = current.length;
    let oldEnd = old.length;
    while (end > start && oldEnd > start && current[end - 1] === old[oldEnd - 1]) {
      end--;
      oldEnd--;
    }
    // A Markdown delimiter closing can rewrite existing text. Animate insertions only.
    if (oldEnd > start) return;
    let offset = 0;
    for (const node of nodes) {
      const length = node.length;
      const from = Math.max(0, start - offset);
      const to = Math.min(length, end - offset);
      offset += length;
      if (from >= to || !node.data.slice(from, to).trim()) continue;
      const range = document.createRange();
      range.setStart(node, from);
      range.setEnd(node, to);
      const span = document.createElement('span');
      span.dataset.streamReveal = '1';
      range.surroundContents(span);
      const animation = span.animate([{ opacity: 0.35 }, { opacity: 1 }], {
        duration: 120,
        easing: 'ease-out'
      });
      active.set(span, animation);
      animation.onfinish = () => {
        if (span.isConnected) span.replaceWith(...Array.from(span.childNodes));
        active.delete(span);
      };
    }
  }

  return { update, dispose: clear };
}
