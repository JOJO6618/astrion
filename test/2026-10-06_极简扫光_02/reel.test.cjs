const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const { createLoader, repo } = require('./helpers.cjs');

function harness() {
  let groups = [];
  let onChange;
  let dispose;
  let nextId = 0;
  let now = 0;
  const tasks = new Map();
  const register = (callback, delay, interval = 0) => {
    const id = ++nextId;
    tasks.set(id, { callback, at: now + delay, interval });
    return id;
  };
  const window = {
    setTimeout: (callback, delay) => register(callback, delay),
    clearTimeout: (id) => tasks.delete(id),
    setInterval: (callback, delay) => register(callback, delay, delay),
    clearInterval: (id) => tasks.delete(id),
    requestAnimationFrame: (callback) => register(callback, 16),
    cancelAnimationFrame: (id) => tasks.delete(id)
  };
  const vue = {
    reactive: (value) => value,
    watch(getGroups, callback) { onChange = () => callback(getGroups()); onChange(); },
    onBeforeUnmount(callback) { dispose = callback; }
  };
  const load = createLoader({ vue }, window);
  const { useSummaryToolReel } = load(path.join(repo, 'static/src/composables/useSummaryToolReel.ts'));
  const { getLatestToolBatch } = load(path.join(repo, 'static/src/components/chat/toolSummaryBatch.ts'));
  const reel = useSummaryToolReel(() => groups);
  return {
    reel, tasks, getLatestToolBatch,
    set(value) { groups = value; onChange(); },
    advance(delta) {
      const until = now + delta;
      for (;;) {
        const next = [...tasks.entries()].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > until) break;
        const [id, task] = next;
        now = task.at;
        if (task.interval) tasks.set(id, { ...task, at: now + task.interval });
        else tasks.delete(id);
        task.callback(now);
      }
      now = until;
    },
    dispose() { dispose(); }
  };
}

function batch(key = 'one', active = true, running = true) {
  return [{ id: 'group', batchKey: key, items: ['B', 'C'], active, running }];
}

test('parallel tools keep the original 1450ms roll, 520ms motion, and 170ms rebound', () => {
  const h = harness();
  h.set(batch());
  assert.deepEqual(h.reel.getView('group').items, ['B', 'C', 'B']);
  h.advance(1449);
  assert.equal(h.reel.getView('group').offsetPx, 0);
  h.advance(1);
  assert.equal(h.reel.getView('group').phase, 'rolling');
  assert.equal(h.reel.getView('group').offsetPx, -28);
  h.advance(519);
  assert.equal(h.reel.getView('group').offsetPx, -28);
  h.advance(1);
  assert.equal(h.reel.getView('group').phase, 'settle');
  assert.equal(h.reel.getView('group').offsetPx, -26);
  h.advance(930);
  assert.equal(h.reel.getView('group').offsetPx, -54);
  h.advance(520);
  assert.equal(h.reel.getView('group').offsetPx, -52);
  h.advance(170);
  assert.equal(h.reel.getView('group').phase, 'idle');
  assert.equal(h.reel.getView('group').offsetPx, 0);
  h.dispose();
});

test('a completed B remains in the parallel reel while C is still running', () => {
  const h = harness();
  const actions = [
    { type: 'tool', toolBatchId: 'one', status: 'completed', text: 'B' },
    { type: 'tool', toolBatchId: 'one', status: 'running', text: 'C' }
  ];
  const members = h.getLatestToolBatch(actions);
  h.set([{ id: 'group', batchKey: 'one', running: true,
    active: members.some((item) => item.status === 'running'),
    items: members.map((item) => item.text) }]);
  assert.deepEqual(h.reel.getView('group').items, ['B', 'C', 'B']);
  h.advance(1450 + 520);
  assert.equal(h.reel.getView('group').offsetPx, -26);
  h.dispose();
});

test('an entire completed batch settles on its last member without continuing to roll', () => {
  const h = harness();
  h.set(batch());
  h.advance(1450 + 520);
  h.set(batch('one', false));
  h.advance(16 + 520 + 170 + 260);
  assert.equal(h.reel.shouldShow('group'), true);
  assert.equal(h.reel.getView('group').phase, 'idle');
  assert.equal(h.reel.getView('group').offsetPx, -26);
  h.advance(10000);
  assert.equal(h.reel.getView('group').offsetPx, -26);
  assert.equal(h.tasks.size, 0);
});

test('a new batch cancels an old pending finish frame and its timers', () => {
  const h = harness();
  h.set(batch());
  h.advance(1450 + 520);
  h.set(batch('one', false));
  h.set(batch('two'));
  assert.equal(h.reel.getView('group').offsetPx, 0);
  h.advance(16);
  assert.equal(h.reel.getView('group').phase, 'idle');
  assert.equal(h.reel.getView('group').offsetPx, 0);
  h.advance(1434);
  assert.equal(h.reel.getView('group').offsetPx, -28);
  h.dispose();
  assert.equal(h.tasks.size, 0);
});

test('task completion finishes the old reel before returning to the static summary', () => {
  const h = harness();
  h.set(batch());
  h.set(batch('one', false, false));
  assert.equal(h.reel.shouldShow('group'), true);
  h.advance(16 + 520 + 170 + 260);
  assert.equal(h.reel.shouldShow('group'), false);
  assert.deepEqual(h.reel.getView('group').items, []);
  assert.equal(h.tasks.size, 0);
});
