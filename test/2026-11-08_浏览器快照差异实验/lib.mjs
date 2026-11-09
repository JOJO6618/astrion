// 快照差异实验的共享逻辑：页面稳定判定、尺寸统计与设计中的后处理。
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const here = path.dirname(fileURLToPath(import.meta.url));
export const repo = path.resolve(here, '../..');

export const lines = text => (text ? text.split('\n').filter(Boolean) : []);

export async function installWatch(page) {
  await page.evaluate(() => {
    window.__last = performance.now();
    new MutationObserver(() => { window.__last = performance.now(); })
      .observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  });
}

export function trackRequests(page) {
  const state = { inflight: new Set() };
  page.on('request', req => state.inflight.add(req));
  page.on('requestfinished', req => state.inflight.delete(req));
  page.on('requestfailed', req => state.inflight.delete(req));
  return state;
}

// DOM 静默 quietMs，且（可选）没有进行中的 fetch/xhr/document 请求；最长 capMs。
// 只看这三类请求：图片、埋点 beacon、长连接不应拖住稳定判定。
export async function settle(page, net, { quietMs = 300, capMs = 3000, useNetwork = true } = {}) {
  const start = Date.now();
  await page.waitForTimeout(50);
  while (Date.now() - start < capMs) {
    let idle;
    try {
      const state = await page.evaluate(q => typeof window.__last !== 'number' ? 'missing' : performance.now() - window.__last >= q, quietMs);
      if (state === 'missing') { await installWatch(page); idle = false; } else idle = state;
    } catch {
      idle = false;
      await page.waitForLoadState('domcontentloaded').catch(() => {});
      await installWatch(page).catch(() => {});
    }
    const busy = useNetwork && [...net.inflight].some(req => ['fetch', 'xhr', 'document'].includes(req.resourceType()));
    if (idle && !busy) return { ms: Date.now() - start, capped: false };
    await page.waitForTimeout(50);
  }
  const pending = [...net.inflight].filter(req => ['fetch', 'xhr', 'document'].includes(req.resourceType())).map(req => req.url().slice(0, 120));
  return { ms: Date.now() - start, capped: true, pending: pending.slice(0, 5) };
}

export function measure(full, inc) {
  return {
    full_chars: full.length, full_lines: lines(full).length,
    inc_chars: inc.length, inc_lines: lines(inc).length,
    unchanged_lines: lines(inc).filter(l => /\[unchanged\]\s*$/.test(l)).length,
    ratio: full.length ? +(inc.length / full.length).toFixed(3) : null
  };
}

function parseTree(snapshot) {
  const nodes = new Map(), stack = [];
  for (const line of lines(snapshot)) {
    const indent = line.match(/^ */)[0].length;
    while (stack.length && stack.at(-1).indent >= indent) stack.pop();
    const ref = line.match(/\[ref=(e\d+)\]/)?.[1];
    const parent = stack.findLast(row => row.ref)?.ref || null;
    if (ref) nodes.set(ref, { parent, text: line.trim() });
    stack.push({ indent, ref });
  }
  return nodes;
}

export const stripActive = s => s.replace(/ \[active\]/g, '');

// 后处理：折叠连续 >=3 行 [unchanged]；列出最外层被移除节点（最多 removedLimit 条）；
// 去掉 [active] 后前后完整快照一致则为「无变化」。
export function postprocess(inc, beforeFull, afterFull, { removedLimit = 5 } = {}) {
  if (stripActive(beforeFull) === stripActive(afterFull)) return { text: '页面无变化', no_change: true, removed_top: 0 };
  const out = [], rows = lines(inc);
  for (let i = 0; i < rows.length;) {
    const indent = rows[i].match(/^ */)[0];
    let j = i;
    while (j < rows.length && rows[j].startsWith(indent) && rows[j].slice(indent.length).startsWith('- ref=') && /\[unchanged\]$/.test(rows[j])) j++;
    if (j - i >= 3) { out.push(`${indent}- …（${j - i} 个未变化节点）`); i = j; }
    else { out.push(rows[i]); i++; }
  }
  const before = parseTree(beforeFull), after = parseTree(afterFull);
  const removed = [...before].filter(([ref, row]) => !after.has(ref) && !(row.parent && !after.has(row.parent)));
  if (removed.length) {
    out.push(`已移除 ${removed.length} 个节点${removed.length > removedLimit ? `（列出前 ${removedLimit} 个）` : ''}：`);
    for (const [, row] of removed.slice(0, removedLimit)) out.push(`  - ${row.text.replace(/^- /, '').replace(/:$/, '').slice(0, 160)}`);
  }
  return { text: out.join('\n'), no_change: false, removed_top: removed.length };
}
