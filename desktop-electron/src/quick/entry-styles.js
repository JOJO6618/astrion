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
  const links = entryStyles(manifest).map(css => `<link rel="stylesheet" href="/static/dist/${css}">`).join('');
  return template.replace('<link rel="stylesheet" href="/static/dist/assets/quick.css">', links);
}
