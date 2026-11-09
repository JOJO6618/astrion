// 快照文本后处理（2026-11-08 实验定稿，见 docs/native_browser.md「动作后返回」）。
// 纯函数，便于离线回归：test/2026-11-09_AI浏览器工具/。

export const ACTION_SNAPSHOT_LIMIT = 20000;
export const EXPLICIT_SNAPSHOT_LIMIT = 60000;

const rows = text => (text ? text.split('\n').filter(Boolean) : []);
export const stripActive = text => String(text || '').replace(/ \[active\]/g, '');

function parseTree(snapshot) {
  const nodes = new Map(), stack = [];
  for (const line of rows(snapshot)) {
    const indent = line.match(/^ */)[0].length;
    while (stack.length && stack.at(-1).indent >= indent) stack.pop();
    const ref = line.match(/\[ref=([a-z\d]+)\]/)?.[1];
    const parent = stack.findLast(row => row.ref)?.ref || null;
    if (ref) nodes.set(ref, { parent, text: line.trim() });
    stack.push({ indent, ref });
  }
  return nodes;
}

/** 折叠同层连续 >=3 个未变化节点；列出最外层被移除的节点（最多 5 个）。 */
export function postprocess(incremental, beforeFull, afterFull, { removedLimit = 5 } = {}) {
  if (stripActive(beforeFull) === stripActive(afterFull)) return { text: '', noChange: true };
  const out = [], lines = rows(incremental);
  for (let i = 0; i < lines.length;) {
    const indent = lines[i].match(/^ */)[0];
    let j = i;
    while (j < lines.length && lines[j].startsWith(indent) && lines[j].slice(indent.length).startsWith('- ref=') && /\[unchanged\]$/.test(lines[j])) j++;
    if (j - i >= 3) { out.push(`${indent}- …（${j - i} 个未变化节点）`); i = j; }
    else { out.push(lines[i]); i++; }
  }
  const before = parseTree(beforeFull), after = parseTree(afterFull);
  const removed = [...before].filter(([ref, row]) => !after.has(ref) && !(row.parent && !after.has(row.parent)));
  if (removed.length) {
    out.push(`已移除 ${removed.length} 个节点${removed.length > removedLimit ? `（列出前 ${removedLimit} 个）` : ''}：`);
    for (const [, row] of removed.slice(0, removedLimit)) out.push(`  - ${row.text.replace(/^- /, '').replace(/:$/, '').slice(0, 160)}`);
  }
  return { text: out.join('\n'), noChange: false };
}

/** 按行截断，避免把一行 ref 截成半截。 */
export function truncate(text, limit) {
  const value = String(text || '');
  if (value.length <= limit) return { text: value, truncated: false, total: value.length };
  const cut = value.lastIndexOf('\n', limit);
  return { text: value.slice(0, cut > limit * 0.5 ? cut : limit), truncated: true, total: value.length };
}

/**
 * 选择动作后返回的快照形式。
 * newDocument：模型上次看到之后加载了新文档（或基线已重置）→ 完整快照。
 * 差异超过完整快照 50% → 完整快照；否则返回差异；无变化 → none。
 */
export function chooseSnapshot({ newDocument, incremental, beforeFull, afterFull, limit = ACTION_SNAPSHOT_LIMIT }) {
  if (newDocument || incremental === undefined || beforeFull === undefined) {
    return { kind: 'full', ...truncate(afterFull, limit) };
  }
  const processed = postprocess(incremental, beforeFull, afterFull);
  if (processed.noChange) return { kind: 'none', text: '', truncated: false, total: 0 };
  if (processed.text.length > afterFull.length * 0.5) return { kind: 'full', ...truncate(afterFull, limit) };
  return { kind: 'diff', ...truncate(processed.text, limit) };
}
