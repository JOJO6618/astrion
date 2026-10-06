const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const demo = path.resolve(__dirname, '../../output/thinking-waves-demo-20261006/js');
const window = {};
for (const file of ['motion.js', 'wave-controller.js']) {
  new Function('window', fs.readFileSync(path.join(demo, file), 'utf8'))(window);
}

function harness() {
  let prefix = '';
  let target = '';
  const commits = [];
  const frames = [];
  const surface = {
    commit(text) { prefix = text; target = text; commits.push(text); },
    renderTarget(text, next) { prefix = text; target = next; },
    targetWidth() { return Array.from(target).length * 10; },
    lineWidth() { return 500; },
    paint(masks) { frames.push(masks); }
  };
  return {
    controller: new window.ThinkingWaveController(surface, window.SweepMotion),
    commits, frames,
    get prefix() { return prefix; }
  };
}

function startSecondSweep(h, tail = ' second') {
  h.controller.receive('first', 0);
  h.controller.update(0);
  h.controller.receive(tail, 100);
  h.controller.update(500);
  h.controller.update(1150);
}

test('the first appearance freezes the first packet while later packets wait', () => {
  const h = harness();
  h.controller.receive('first', 0);
  h.controller.update(0);
  h.controller.receive(' second', 100);
  assert.equal(h.controller.getState().target, 'first');
  assert.equal(h.controller.getState().queued, ' second');
  h.controller.update(500);
  assert.equal(h.prefix, 'first');
});

test('a whole-line sweep starts after 650ms even when no new content arrives', () => {
  const h = harness();
  h.controller.receive('first', 0, true);
  h.controller.update(0);
  h.controller.update(500);
  h.controller.update(1149);
  assert.equal(h.controller.active, null);
  h.controller.update(1150);
  assert.equal(h.controller.getState().wave, 2);
  assert.equal(h.controller.getState().target, 'first');
  assert.equal(h.frames.at(-1).x, window.SweepMotion.geometry(50).startX);
  assert.equal(h.prefix, 'first');
  assert.deepEqual(h.commits, ['', 'first']);
});

test('new content received in the interval does not start a sweep early', () => {
  const h = harness();
  h.controller.receive('first', 0);
  h.controller.update(0);
  h.controller.update(500);
  h.controller.receive('B', 800);
  h.controller.update(800);
  assert.equal(h.controller.active, null);
  assert.equal(h.prefix, 'first');
  h.controller.update(1150);
  assert.equal(h.controller.getState().target, 'firstB');
  assert.equal(h.prefix, 'first');
});

test('the same moving light extends right to new content without changing speed', () => {
  const h = harness();
  startSecondSweep(h);
  h.controller.update(1200);
  const before = h.frames.at(-1).x;
  h.controller.receive(' third', 1200);
  h.controller.update(1200);
  assert.equal(h.frames.at(-1).x, before);
  assert.equal(h.controller.getState().target, 'first second third');
  h.controller.update(1250);
  assert.ok(Math.abs(h.frames.at(-1).x - before - 16) < 0.001);
  assert.equal(h.prefix, 'first');
});

test('late content behind the light waits for the next fixed-interval sweep', () => {
  const h = harness();
  startSecondSweep(h, 'B');
  h.controller.receive('C', 1550);
  assert.equal(h.controller.getState().target, 'firstB');
  assert.equal(h.controller.getState().queued, 'C');
  h.controller.update(1700);
  assert.equal(h.prefix, 'firstB');
  h.controller.update(2350);
  assert.equal(h.controller.getState().wave, 3);
  assert.equal(h.controller.getState().target, 'firstBC');
  assert.equal(h.prefix, 'firstB');
});

test('all text settles, old text never disappears, and sweeps keep recurring', () => {
  const h = harness();
  const chunks = ['我先梳理', '摘要的显示', '流程，', '再核对新内容', '的接收与', '动画衔接。'];
  const times = [350, 650, 980, 1450, 1850, 2350];
  let packet = 0;
  for (let now = 0; now <= 8000; now += 16) {
    while (packet < chunks.length && times[packet] <= now) {
      h.controller.receive(chunks[packet], now, packet === chunks.length - 1);
      packet++;
    }
    h.controller.update(now);
  }
  assert.equal(h.prefix, chunks.join(''));
  assert.equal(h.controller.getState().finished, true);
  assert.ok(h.controller.getState().wave >= 3);
  assert.ok(h.commits.every((text, index) => index === 0 || text.startsWith(h.commits[index - 1])));
});

test('later lines are not appended to the first thinking line', () => {
  const h = harness();
  h.controller.receive('first\nsecond', 0);
  h.controller.update(0);
  h.controller.receive(' more second-line text', 100, true);
  h.controller.update(1000);
  assert.equal(h.prefix, 'first');
  assert.equal(h.controller.getState().received, 'first');
});
