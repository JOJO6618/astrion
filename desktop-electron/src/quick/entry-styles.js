// With multiple Vite entries, shared SFC styles live in shared chunks. Load
// their CSS before the entry CSS, so local surface/layout rules remain last.
export function entryStyles(manifest, entry = 'static/src/quick.ts') {
  const visited = new Set(), styles = new Set();
  function visit(key) {
    if (visited.has(key)) return;
    visited.add(key);
    const chunk = manifest[key];
    if (!chunk) throw new Error(`Missing build manifest chunk: ${key}`);
    for (const imported of chunk.imports || []) visit(imported);
    for (const css of chunk.css || []) {
      if (typeof css !== 'string' || !/^[\w./-]+\.css$/.test(css) || css.includes('..')) throw new Error('Invalid stylesheet path');
      styles.add(css);
    }
  }
  visit(entry);
  return [...styles];
}

export function quickDocument(template, manifest) {
  // 产物名带内容哈希后，入口脚本也必须按 manifest 替换；只换 CSS 会让
  // 快捷窗口 404 白屏。见 vite.config.ts 的 entryFileNames。
  const entry = manifest['static/src/quick.ts'];
  if (!entry || typeof entry.file !== 'string') {
    throw new Error('Missing quick entry chunk in build manifest');
  }
  const links = entryStyles(manifest).map(css => `<link rel="stylesheet" href="/static/dist/${css}">`).join('');
  const html = template
    .replace('<link rel="stylesheet" href="/static/dist/assets/quick.css">', links)
    .replace('src="/static/dist/assets/quick.js"', `src="/static/dist/${entry.file}"`);
  if (html.includes('/static/dist/assets/quick.js')) {
    throw new Error('quick.html 未命中入口脚本引用，注入失败（模板与产物不同步）');
  }
  return html;
}
