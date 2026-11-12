// 真实前端 + 后端副本：在目标对话发出原工作段的用户输入，任务运行期间反复从对话 A 切入，记录切换耗时。
// 用法：node switch_probe.mjs <base> <convA> <convB> <replayStateUrl> <outDir> [intervalSec] [maxMinutes] [--no-start]
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const [base, convA, convB, replayState, outDir, intervalArg, maxArg, ...flags] = process.argv.slice(2);
const interval = Number(intervalArg || 60) * 1000;
const maxMs = Number(maxArg || 40) * 60000;

const script = JSON.parse(fs.readFileSync(path.join(outDir, '..', 'replay_script.json'), 'utf8'));
const resultsFile = path.join(outDir, 'switch_results.jsonl');
const log = (row) => {
  row.t = Date.now() / 1000;
  fs.appendFileSync(resultsFile, JSON.stringify(row) + '\n');
  console.log(JSON.stringify(row));
};

const browser = await chromium.connectOverCDP('http://127.0.0.1:' + (process.env.CDP_PORT || '19222') + '');
const context = browser.contexts()[0];
let page = null;
for (let i = 0; i < 60 && !page; i += 1) {
  page = context.pages().find((p) => /127\.0\.0\.1:\d+\/(?!chrome)/.test(p.url()) && !p.url().includes('/chrome') && !p.url().startsWith('astrion-quick'));
  if (!page) await new Promise((r) => setTimeout(r, 500));
}
log({ kind: 'attached', url: page.url() });
const cdp = await context.newCDPSession(page);
await cdp.send('Performance.enable');
page.on('pageerror', (e) => log({ kind: 'pageerror', message: e.message.slice(0, 300) }));
await page.evaluate(() => {
  window.__longTasks = [];
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) window.__longTasks.push([e.startTime, e.duration]);
  }).observe({ type: 'longtask', buffered: true });
});

const short = (id) => id.replace(/^conv_/, '');

async function busy() {
  const { metrics } = await cdp.send('Performance.getMetrics');
  const pick = Object.fromEntries(metrics.map((m) => [m.name, m.value]));
  return { task: pick.TaskDuration, script: pick.ScriptDuration, layout: pick.LayoutDuration,
    style: pick.RecalcStyleDuration, at: Date.now() / 1000 };
}

function busyDelta(a, b) {
  const span = b.at - a.at;
  return { span_s: +span.toFixed(1), main_busy_pct: +((100 * (b.task - a.task)) / span).toFixed(1),
    script_pct: +((100 * (b.script - a.script)) / span).toFixed(1),
    layout_style_pct: +((100 * (b.layout - a.layout + b.style - a.style)) / span).toFixed(1) };
}

async function metrics() {
  const { metrics } = await cdp.send('Performance.getMetrics');
  const pick = Object.fromEntries(metrics.map((m) => [m.name, m.value]));
  const dom = await page.evaluate(() => ({
    nodes: document.getElementsByTagName('*').length,
    stacked: document.querySelectorAll('.stacked-item').length,
    collapsible: document.querySelectorAll('.collapsible-block').length
  }));
  return { heap_mb: +(pick.JSHeapUsedSize / 1048576).toFixed(1), ...dom };
}

// 在页面里点击侧栏项，并以 rAF 轮询等待：URL 切到目标、加载态结束、目标对话已渲染，再等两帧。
async function switchTo(convId, label) {
  const begin = Date.now();
  const TITLES = { conv_20261111_014826_508: 'Playwright工具确认', conv_20261031_170429_608: '组件设计库规范梳理' };
  const clicked = await page.evaluate(([title]) => {
    performance.setResourceTimingBufferSize(100000);
    performance.clearResourceTimings();
    window.__switchStart = performance.now();
    window.__longTasks = [];
    const item = [...document.querySelectorAll('.conversation-item')].find(
      (el) => el.querySelector('.conversation-title')?.textContent.trim() === title);
    if (!item) return false;
    item.click();
    return true;
  }, [TITLES[convId]]);
  if (!clicked) throw new Error('sidebar item not found for ' + convId);
  let timedOut = false;
  try {
    await page.waitForFunction(
      ([s]) => location.pathname === '/' + s && !document.querySelector('.is-history-loading'),
      [short(convId)], { polling: 'raf', timeout: 600000 });
  } catch {
    timedOut = true;
  }
  const detail = await page.evaluate(async () => {
    const committed = performance.now() - window.__switchStart;
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const painted = performance.now() - window.__switchStart;
    const boot = performance.getEntriesByType('resource')
      .filter((e) => e.name.includes('/bootstrap') && e.startTime >= window.__switchStart - 5)
      .map((e) => ({ start: +(e.startTime - window.__switchStart).toFixed(1),
        ttfb: +(e.responseStart - e.startTime).toFixed(1), total: +(e.responseEnd - e.startTime).toFixed(1),
        bytes: e.encodedBodySize }));
    const longs = window.__longTasks.map(([s, d]) => [+(s - window.__switchStart).toFixed(0), +d.toFixed(0)]);
    return { committed_ms: +committed.toFixed(1), painted_ms: +painted.toFixed(1), bootstrap: boot,
      long_tasks: longs.filter(([s]) => s > -50).slice(0, 20),
      long_total_ms: longs.reduce((a, [, d]) => a + d, 0) };
  });
  return { label, conv: convId, clicked, timedOut, wall_ms: Date.now() - begin, ...detail };
}

async function replayProgress() {
  try {
    return await (await fetch(replayState)).json();
  } catch {
    return null;
  }
}


const limitUrl = replayState.replace('/control/state', '/control/limit');
const checkpoints = (process.env.CHECKPOINTS || '50,100,200,400,800').split(',').map(Number);
const countTools = () => { try { return fs.readFileSync(path.join(outDir, 'tool.jsonl'), 'utf8').split('\n').filter(Boolean).length; } catch { return 0; } };

log({ kind: 'enter-B', ...(await switchTo(convB, 'start->B')) });
await page.waitForTimeout(1500);
if (!process.env.NO_SEND) {
await fetch(limitUrl + '?n=0');           // 先把检查点压到 0，确保发送后任务停在“等待模型”
const editor = page.locator('.input-area .stadium-input-editor');
await editor.click();
await page.keyboard.insertText(script.trigger);
await page.keyboard.press('Control+Enter');
log({ kind: 'sent', conv: convB });
await page.waitForTimeout(3000);

}

for (const limit of checkpoints) {
  const t0 = Date.now();
  await fetch(limitUrl + '?n=' + limit);
  // 等快进到检查点：回放服务停在 limit，并且工具计数连续 4 秒不再变化。
  let lastTools = -1, stable = 0;
  while (stable < 4) {
    await new Promise((r) => setTimeout(r, 1000));
    const st = await replayProgress();
    const n = countTools();
    stable = st && st.next >= limit + 1 && n === lastTools ? stable + 1 : 0;
    lastTools = n;
    if (Date.now() - t0 > 40 * 60000) throw new Error('fast-forward timeout at ' + limit);
  }
  const ff = (Date.now() - t0) / 1000;
  log({ kind: 'checkpoint', limit, tools_done: lastTools, fast_forward_s: +ff.toFixed(0), ...(await metrics()) });
  const outFile = path.join(outDir, '..', 'conv_B_size.txt');
  for (let i = 1; i <= 3; i += 1) {
    const back = await switchTo(convA, 'B->A');
    log({ kind: 'switch', limit, rep: i, ...back });
    await page.waitForTimeout(2500);
    const into = await switchTo(convB, 'A->B');
    log({ kind: 'switch', limit, rep: i, ...into, ...(await metrics()) });
    await page.waitForTimeout(2500);
  }
}
await browser.close().catch(() => {});
