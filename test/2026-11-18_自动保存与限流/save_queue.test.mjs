// 运行：node --test test/2026-11-18_自动保存与限流/save_queue.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../../', import.meta.url));

async function loadQueue() {
  const { outputFiles } = await build({
    entryPoints: [resolve(root, 'static/src/utils/saveQueue.ts')],
    bundle: true,
    write: false,
    platform: 'node',
    format: 'esm'
  });
  const url = `data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString('base64')}`;
  return import(url);
}

const { createSaveQueue } = await loadQueue();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function harness(opts = {}) {
  const calls = [];
  let value = 0;
  let delay = opts.runMs ?? 0;
  const queue = createSaveQueue({
    idleMs: opts.idleMs ?? 40,
    maxWaitMs: opts.maxWaitMs ?? 120,
    run: async (ctx) => {
      const snapshot = value;
      calls.push({ snapshot, keepalive: ctx.keepalive, at: Date.now() });
      if (delay) await sleep(delay);
      return snapshot;
    }
  });
  return {
    queue,
    calls,
    edit(v) {
      value = v;
      queue.schedule();
    },
    setDelay(ms) {
      delay = ms;
    }
  };
}

test('停手 idle 后只保存一次，且保存的是最新值', async () => {
  const h = harness();
  for (let i = 1; i <= 5; i += 1) {
    h.edit(i);
    await sleep(10);
  }
  await sleep(80);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].snapshot, 5);
});

test('持续修改时最长 maxWait 也会保存', async () => {
  const h = harness({ idleMs: 40, maxWaitMs: 120 });
  const start = Date.now();
  // 每 20ms 改一次，持续 300ms：idle 永远等不到，靠 maxWait 触发
  while (Date.now() - start < 300) {
    h.edit(Date.now());
    await sleep(20);
  }
  await sleep(80);
  assert.ok(h.calls.length >= 2, `calls=${h.calls.length}`);
  assert.ok(h.calls.length <= 4, `calls=${h.calls.length}`);
  assert.ok(h.calls[0].at - start <= 170, '首次保存应在 maxWait 附近');
});

test('在途期间的修改不丢失：结束后补发最新值，且不并发', async () => {
  const h = harness({ idleMs: 10, runMs: 60 });
  h.edit(1);
  await sleep(20); // 第一次已在途
  h.edit(2);
  h.edit(3);
  await sleep(30); // 计时器到点时仍在途
  assert.equal(h.calls.length, 1);
  await sleep(120);
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].snapshot, 3);
  assert.ok(h.calls[1].at - h.calls[0].at >= 55, '补发必须在上一请求结束后');
});

test('flush 立即发出并返回结果；无积压时返回 undefined', async () => {
  const h = harness({ idleMs: 1000 });
  h.edit(7);
  const result = await h.queue.flush();
  assert.equal(result, 7);
  assert.equal(h.calls.length, 1);
  assert.equal(await h.queue.flush(), undefined);
  await sleep(20);
  assert.equal(h.calls.length, 1);
});

test('在途时 flush：等待补发并返回补发结果', async () => {
  const h = harness({ idleMs: 1000, runMs: 40 });
  h.edit(1);
  const first = h.queue.flush();
  await sleep(5); // 第一次请求已真正发出（run 在微任务中启动，读取当时的最新状态）
  h.edit(2);
  const second = await h.queue.flush();
  assert.equal(await first, 1);
  assert.equal(second, 2);
  assert.equal(h.calls.length, 2);
});

test('卸载场景 flush 透传 keepalive，后续普通保存不带', async () => {
  const h = harness({ idleMs: 10 });
  h.edit(1);
  await h.queue.flush({ keepalive: true });
  h.edit(2);
  await sleep(30);
  assert.deepEqual(
    h.calls.map((c) => c.keepalive),
    [true, false]
  );
});

test('run 抛错后队列仍可继续保存', async () => {
  let fail = true;
  const calls = [];
  const queue = createSaveQueue({
    idleMs: 10,
    run: async () => {
      calls.push(fail);
      if (fail) throw new Error('boom');
      return 'ok';
    }
  });
  queue.schedule();
  await assert.rejects(queue.flush(), /boom/);
  fail = false;
  queue.schedule();
  assert.equal(await queue.flush(), 'ok');
  assert.equal(calls.length, 2);
});
