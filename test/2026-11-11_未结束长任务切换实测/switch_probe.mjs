// 真实前端 + 后端副本：在目标对话发出原工作段的用户输入，任务运行期间反复从对话 A 切入，记录切换耗时。
// 用法：node switch_probe.mjs <base> <convA> <convB> <replayStateUrl> <outDir> [intervalSec] [maxMinutes] [--no-start]
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const [base, convA, convB, replayState, outDir, intervalArg, maxArg, ...flags] = process.argv.slice(2);
const interval = Number(intervalArg || 60) * 1000;
const maxMs = Number(maxArg || 40) * 60000;
const startTask = !flags.includes('--no-start');
const script = JSON.parse(fs.readFileSync(path.join(outDir, '..', 'replay_script.json'), 'utf8'));
const resultsFile = path.join(outDir, 'switch_results.jsonl');
const log = (row) => {
  row.t = Date.now() / 1000;
  fs.appendFileSync(resultsFile, JSON.stringify(row) + '\n');
  console.log(JSON.stringify(row));
};

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send('Performance.enable');
page.on('pageerror', (e) => log({ kind: 'pageerror', message: e.message.slice(0, 300) }));

await page.addInitScript(() => {
  window.__longTasks = [];
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) window.__longTasks.push([e.startTime, e.duration]);
  }).observe({ type: 'longtask', buffered: true });
});

await page.goto(`${base}/login`);
await page.getByText('宿主机模式').click();
await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30000 });

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
  const clicked = await page.evaluate(([id]) => {
    performance.setResourceTimingBufferSize(100000);
    performance.clearResourceTimings();
    window.__switchStart = performance.now();
    window.__longTasks = [];
    const item =
      document.querySelector(`.conversation-item [data-action-trigger="${id}"]`)?.closest('.conversation-item') ||
      [...document.querySelectorAll('.running-task-item')].find((el) => el.__vueParentComponent?.props?.task?.conversation_id === id);
    if (!item) return false;
    item.click();
    return true;
  }, [convId]);
  if (!clicked) {
    // 侧栏未渲染到该项时，退回与地址栏前进/后退相同的路由入口。
    await page.evaluate(([s]) => {
      performance.clearResourceTimings();
      window.__switchStart = performance.now();
      window.__longTasks = [];
      history.pushState({ conversationId: 'conv_' + s }, '', '/' + s);
      dispatchEvent(new PopStateEvent('popstate', { state: { conversationId: 'conv_' + s } }));
    }, [short(convId)]);
  }
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

if (startTask) {
  // 先进入目标对话，在真实输入框里输入原工作段的用户消息并发送。
  await page.goto(`${base}/${short(convB)}`);
  await page.waitForFunction(() => !document.querySelector('.is-history-loading'), null, { timeout: 120000 });
  await page.waitForTimeout(2000);
  const editor = page.locator('.input-area .stadium-input-editor');
  await editor.click();
  await page.keyboard.insertText(script.trigger);
  await page.keyboard.press('Control+Enter');
  log({ kind: 'sent', conv: convB, chars: script.trigger.length });
  await page.waitForTimeout(8000);
  log({ kind: 'after-send', progress: await replayProgress(), ...(await metrics()) });
  log({ kind: 'switch', ...(await switchTo(convA, 'B->A')), progress: await replayProgress() });
} else {
  await page.goto(`${base}/${short(convA)}`);
  await page.waitForFunction(() => !document.querySelector('.is-history-loading'), null, { timeout: 120000 });
}

const deadline = Date.now() + maxMs;
let round = 0;
let finishedRounds = 0;
const watch = flags.includes('--watch');
if (watch) {
  // 盯着看：大部分时间停在运行中的 B，统计停留期间主线程繁忙度；定期 B->A 停 3 秒再 A->B。
  log({ kind: 'switch', round: 0, ...(await switchTo(convB, 'A->B')), progress: await replayProgress() });
  while (Date.now() < deadline) {
    const b0 = await busy();
    await page.waitForTimeout(interval);
    const b1 = await busy();
    round += 1;
    const progress = await replayProgress();
    log({ kind: 'watching', round, progress, ...busyDelta(b0, b1), ...(await metrics()) });
    log({ kind: 'switch', round, ...(await switchTo(convA, 'B->A')), progress });
    await page.waitForTimeout(3000);
    log({ kind: 'switch', round, ...(await switchTo(convB, 'A->B')), progress: await replayProgress(), ...(await metrics()) });
    if (progress && progress.next >= progress.turns - 1) finishedRounds += 1;
    if (finishedRounds >= 3) break;
  }
}
while (!watch && Date.now() < deadline) {
  await page.waitForTimeout(interval);
  round += 1;
  const before = await replayProgress();
  const into = await switchTo(convB, 'A->B');
  const mInto = await metrics();
  log({ kind: 'switch', round, ...into, progress: before, ...mInto });
  await page.waitForTimeout(5000);
  const back = await switchTo(convA, 'B->A');
  log({ kind: 'switch', round, ...back, progress: await replayProgress(), ...(await metrics()) });
  if (before && before.next >= before.turns - 1) finishedRounds += 1;
  if (finishedRounds >= 3) break;
}
if (flags.includes('--stop-resend')) {
  // 对照：在 B 中点停止，再发一条消息开始新任务（回放服务让新任务保持“仍在思考”），然后同样切换测量。
  log({ kind: 'switch', round: 'pre-stop', ...(await switchTo(convB, 'A->B')), progress: await replayProgress(), ...(await metrics()) });
  await page.waitForTimeout(3000);
  await page.locator('.send-btn').click();
  log({ kind: 'stop-clicked' });
  await page.waitForFunction(() => !document.querySelector('.send-btn .stop-icon'), null, { timeout: 120000 });
  await page.waitForTimeout(3000);
  await page.locator('.input-area .stadium-input-editor').click();
  await page.keyboard.insertText('继续');
  await page.keyboard.press('Control+Enter');
  log({ kind: 'resent' });
  await page.waitForTimeout(10000);
  for (let i = 1; i <= 4; i += 1) {
    log({ kind: 'switch', round: `after-resend-${i}`, ...(await switchTo(convA, 'B->A')), progress: await replayProgress() });
    await page.waitForTimeout(5000);
    log({ kind: 'switch', round: `after-resend-${i}`, ...(await switchTo(convB, 'A->B')), progress: await replayProgress(), ...(await metrics()) });
    await page.waitForTimeout(5000);
  }
}
await browser.close();
