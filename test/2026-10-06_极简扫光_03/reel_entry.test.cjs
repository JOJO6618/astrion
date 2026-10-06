const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const { createLoader, repo } = require('../2026-10-06_极简扫光_02/helpers.cjs');

function harness() {
  let groups = [], notify, dispose, now = 0, id = 0;
  const tasks = new Map();
  const add = (callback, delay, interval = false) => {
    tasks.set(++id, { callback, time: now + delay, delay, interval });
    return id;
  };
  const vue = {
    reactive: (value) => value,
    watch(getter, callback) { notify = () => callback(getter()); notify(); },
    onBeforeUnmount(callback) { dispose = callback; }
  };
  const window = {
    setTimeout: (fn, ms) => add(fn, ms), clearTimeout: (key) => tasks.delete(key),
    setInterval: (fn, ms) => add(fn, ms, true), clearInterval: (key) => tasks.delete(key),
    requestAnimationFrame: (fn) => add(fn, 16), cancelAnimationFrame: (key) => tasks.delete(key)
  };
  const load = createLoader({ vue }, window);
  const { useSummaryToolReel } = load(path.join(repo, 'static/src/composables/useSummaryToolReel.ts'));
  const reel = useSummaryToolReel(() => groups);
  return {
    reel, tasks,
    set(ready, active = true, key = 'batch') {
      groups = [{ id: 'group', batchKey: key, items: ['first', 'second'], ready, active, running: true }];
      notify();
    },
    advance(delta) {
      const until = now + delta;
      for (;;) {
        const next = [...tasks.entries()].sort((a, b) => a[1].time - b[1].time)[0];
        if (!next || next[1].time > until) break;
        const [key, task] = next;
        now = task.time;
        if (task.interval) task.time += task.delay;
        else tasks.delete(key);
        task.callback();
      }
      now = until;
    },
    dispose() { dispose(); }
  };
}

test('no reel, parking or timer is created while the first entry is unfinished', () => {
  const h = harness();
  h.set(false);
  h.advance(5000);
  assert.equal(h.tasks.size, 0);
  assert.equal(h.reel.shouldShow('group'), false);
  h.set(false, false);
  assert.equal(h.reel.shouldShow('group'), false);
  h.dispose();
});

test('the original roll interval starts only after the appearance has settled', () => {
  const h = harness();
  h.set(false);
  h.advance(4000);
  h.set(true);
  assert.equal(h.reel.shouldShow('group'), true);
  h.advance(1449);
  assert.equal(h.reel.getView('group').offsetPx, 0);
  h.advance(1);
  assert.equal(h.reel.getView('group').offsetPx, -28);
  h.dispose();
});

test('a new batch entry cancels every old rolling and completion callback', () => {
  const h = harness();
  h.set(true);
  h.advance(1500);
  h.set(false, true, 'next');
  assert.equal(h.tasks.size, 0);
  assert.equal(h.reel.shouldShow('group'), false);
  h.advance(5000);
  h.set(true, true, 'next');
  assert.equal(h.reel.getView('group').offsetPx, 0);
  h.dispose();
});
