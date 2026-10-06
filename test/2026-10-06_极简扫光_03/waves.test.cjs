const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const fs = require('node:fs');
const { createLoader, repo } = require('../2026-10-06_极简扫光_02/helpers.cjs');
const load = createLoader();
const chat = path.join(repo, 'static/src/components/chat');
const { SummarySweepController } = load(path.join(chat, 'summarySweepController.ts'));
const { createSweepGeometry } = load(path.join(chat, 'summarySweepMotion.ts'));
const demo = {};
for (const name of ['motion.js', 'wave-controller.js']) {
  new Function('window', fs.readFileSync(path.join(repo,
    'test/2026-10-06_极简扫光_03/fixtures', name), 'utf8'))(demo);
}

function label(text, changes = {}) {
  return { text, identity: 'thinking-1', kind: 'thinking', animate: true,
    sweeping: true, ...changes };
}

function harness(width = 500) {
  let prefix = '';
  let target = '';
  let hidden = false;
  const paints = [];
  const commits = [];
  const settled = [];
  const surface = {
    setText(text) { prefix = target = text; commits.push(text); },
    renderTarget(text, next) { prefix = text; target = next; },
    measure() { return { width, textWidth: Array.from(target).length * 10, height: 26 }; },
    paint(mode, progress, shape) {
      paints.push({ mode, progress, x: shape.startX + progress * shape.distance,
        shape: { ...shape }, prefix, target });
    },
    clear(value = false) { hidden = value; }
  };
  return {
    controller: new SummarySweepController(surface, (input) => settled.push(input)),
    paints, commits, settled,
    get prefix() { return prefix; },
    get target() { return target; },
    get hidden() { return hidden; }
  };
}

function second(h, tail = ' second') {
  h.controller.setInput(label('first'), 0);
  h.controller.setInput(label('first' + tail), 100);
  h.controller.tick(500);
  h.controller.tick(1150);
}

test('production follows the approved demo for the same incoming chunks and frame times', () => {
  const h = harness();
  let prefix = '', target = '', masks;
  const reference = new demo.ThinkingWaveController({
    commit(text) { prefix = target = text; },
    renderTarget(text, next) { prefix = text; target = next; },
    targetWidth() { return Array.from(target).length * 10; },
    lineWidth() { return 500; },
    paint(frame) { masks = frame; }
  }, demo.SweepMotion);
  const chunks = ['我先梳理', '摘要的显示', '流程，', '再核对新内容', '的接收与', '动画衔接。'];
  const times = [352, 656, 992, 1456, 1856, 2352];
  let index = 0;
  let text = '';
  for (let now = 0; now <= 8000; now += 16) {
    if (times[index] === now) {
      const chunk = chunks[index++];
      text += chunk;
      reference.receive(chunk, now);
      h.controller.setInput(label(text), now);
    }
    reference.update(now);
    h.controller.tick(now);
    assert.equal(h.prefix, prefix, `fixed prefix at ${now}`);
    assert.equal(h.target, target, `target at ${now}`);
    if (reference.active) assert.ok(Math.abs(h.paints.at(-1).x - masks.x) < 0.001, `edge at ${now}`);
  }
  assert.equal(h.prefix, chunks.join(''));
});

test('first appearance freezes its first packet; incoming tail remains hidden', () => {
  const h = harness();
  h.controller.setInput(label('first'), 0);
  h.controller.setInput(label('first second'), 100);
  assert.equal(h.prefix, '');
  assert.equal(h.target, 'first');
  h.controller.tick(500);
  assert.equal(h.prefix, 'first');
  h.controller.tick(1149);
  assert.equal(h.target, 'first');
  h.controller.tick(1150);
  assert.equal(h.prefix, 'first');
  assert.equal(h.target, 'first second');
  assert.equal(h.paints.at(-1).x, createSweepGeometry(120).startX);
});

test('every sweep repeats after 650ms, including sweeps with no new text', () => {
  const h = harness();
  second(h, '');
  h.controller.tick(1650);
  const count = h.paints.length;
  h.controller.tick(2299);
  assert.equal(h.paints.length, count);
  h.controller.tick(2300);
  assert.equal(h.paints.at(-1).progress, 0);
  assert.equal(h.paints.at(-1).mode, 'sweep');
});

test('extending a sweep keeps the same light position and 320px/s speed', () => {
  const h = harness();
  second(h);
  h.controller.tick(1200);
  const x = h.paints.at(-1).x;
  h.controller.setInput(label('first second third'), 1200);
  h.controller.tick(1200);
  assert.equal(h.paints.at(-1).x, x);
  h.controller.tick(1250);
  assert.ok(Math.abs(h.paints.at(-1).x - x - 16) < 0.001);
  assert.equal(h.prefix, 'first');
  assert.equal(h.target, 'first second third');
});

test('late tails behind the light wait until the next whole-line sweep', () => {
  const h = harness();
  second(h, 'B');
  h.controller.setInput(label('firstBC'), 1550);
  assert.equal(h.target, 'firstB');
  h.controller.tick(1700);
  assert.equal(h.prefix, 'firstB');
  h.controller.tick(2350);
  assert.equal(h.target, 'firstBC');
});

test('model completion immediately exposes both the current and queued thinking text', () => {
  for (const now of [100, 700, 1200]) {
    const h = harness();
    second(h);
    h.controller.setInput(label('first second third', { forceComplete: true }), now);
    assert.equal(h.prefix, 'first second third');
    assert.equal(h.controller.needsFrame, false);
    assert.equal(h.hidden, false);
  }
});

test('a tool that arrives before thinking entry ends appears instantly when ready', () => {
  const h = harness();
  h.controller.setInput(label('first'), 0);
  h.controller.setInput(label('first second'), 50);
  h.controller.setInput(label('完整工具意图', { identity: 'batch:tools', kind: 'tool' }), 100);
  assert.equal(h.prefix, '完整工具意图');
  assert.equal(h.hidden, false);
  assert.equal(h.settled.at(-1).identity, 'batch:tools');
});

test('incomplete intents do not enter; their arrival flushes pending thinking', () => {
  const h = harness();
  h.controller.setInput(label('first'), 0);
  h.controller.setInput(label('first second'), 50);
  const tool = label('部分', { identity: 'batch:tools', kind: 'tool', ready: false });
  h.controller.setInput(tool, 100);
  assert.equal(h.prefix, 'first second');
  assert.equal(h.controller.needsFrame, false);
  h.controller.tick(2000);
  assert.equal(h.prefix, 'first second');
  h.controller.setInput({ ...tool, text: '完整意图', ready: true }, 2100);
  assert.equal(h.paints.at(-1).mode, 'hide');
  h.controller.tick(4000);
  h.controller.tick(4090);
  assert.equal(h.target, '完整意图');
  assert.equal(h.paints.at(-1).mode, 'reveal');
});

test('a settled tool exits completely before new thinking appears', () => {
  const h = harness();
  h.controller.setInput(label('tool', { identity: 'tool', kind: 'tool' }), 0);
  h.controller.tick(1000);
  h.controller.setInput(label('next'), 1100);
  h.controller.setInput(label('next thinking'), 1200);
  assert.equal(h.prefix, 'tool');
  h.controller.tick(2100);
  assert.equal(h.hidden, true);
  assert.equal(h.prefix, 'tool');
  h.controller.tick(2189);
  assert.equal(h.prefix, 'tool');
  h.controller.tick(2190);
  assert.equal(h.target, 'next thinking');
  assert.equal(h.prefix, '');
  assert.equal(h.paints.at(-1).mode, 'reveal');
});

test('a new step skips an unfinished tool entry instead of revealing obsolete text later', () => {
  const h = harness();
  h.controller.setInput(label('tool', { identity: 'tool', kind: 'tool' }), 0);
  h.controller.setInput(label('next thinking'), 100);
  assert.equal(h.prefix, 'next thinking');
  assert.equal(h.settled.at(-1).kind, 'thinking');
  h.controller.tick(1000);
  assert.equal(h.target, 'next thinking');
});

test('same-batch parallel tools finish the first entry before the reel starts', () => {
  const h = harness();
  const tool = label('first tool', { identity: 'batch:tools', kind: 'tool' });
  h.controller.setInput(tool, 0);
  h.controller.setInput({ ...tool, sweeping: false }, 100);
  assert.equal(h.settled.length, 0);
  assert.equal(h.controller.needsFrame, true);
  h.controller.tick(1000);
  assert.equal(h.settled.at(-1).identity, 'batch:tools');
  assert.equal(h.paints.at(-1).mode, 'reveal');
  assert.equal(h.paints.at(-1).progress, 1);
});

module.exports = { harness, label };

test('chunks received during the switch gap wait for the old tool exit', () => {
  const h = harness();
  h.controller.setInput(label('tool', { identity: 'tool', kind: 'tool' }), 0);
  h.controller.tick(1000);
  h.controller.setInput(label('next'), 1100);
  h.controller.tick(2100);
  h.controller.setInput(label('next thinking'), 2150);
  assert.equal(h.hidden, true);
  assert.equal(h.prefix, 'tool');
  h.controller.tick(2190);
  assert.equal(h.target, 'next thinking');
  assert.equal(h.paints.at(-1).mode, 'reveal');
});

test('a following step supersedes a pending label before it ever enters', () => {
  const h = harness();
  h.controller.setInput(label('tool', { identity: 'tool', kind: 'tool' }), 0);
  h.controller.tick(1000);
  h.controller.setInput(label('pending'), 1100);
  h.controller.setInput(label('new tool', { identity: 'next-tool', kind: 'tool' }), 1200);
  assert.equal(h.prefix, 'new tool');
  assert.equal(h.hidden, false);
  h.controller.tick(2100);
  assert.equal(h.target, 'new tool');
  assert.ok(!h.commits.includes('pending'));
});

test('a not-yet-ready following tool cancels the old exit and exposes pending thinking', () => {
  const h = harness();
  h.controller.setInput(label('tool', { identity: 'tool', kind: 'tool' }), 0);
  h.controller.tick(1000);
  h.controller.setInput(label('pending thinking'), 1100);
  h.controller.setInput(label('', { identity: 'new-tool', kind: 'tool', ready: false }), 1200);
  assert.equal(h.prefix, 'pending thinking');
  assert.equal(h.hidden, false);
  assert.equal(h.controller.needsFrame, false);
  h.controller.tick(3000);
  assert.equal(h.prefix, 'pending thinking');
});

test('a late first intent still finishes its entry when another same-batch member arrived', () => {
  const h = harness();
  h.controller.setInput(label('', { identity: 'batch:tools', kind: 'tool', ready: false }), 0);
  h.controller.setInput(label('', { identity: 'batch:tools', kind: 'tool', ready: false,
    sweeping: false }), 100);
  h.controller.setInput(label('first tool', { identity: 'batch:tools', kind: 'tool', ready: true,
    sweeping: false }), 200);
  assert.equal(h.settled.length, 0);
  assert.equal(h.paints.at(-1).mode, 'reveal');
  assert.equal(h.paints.at(-1).progress, 0);
  h.controller.tick(1200);
  assert.equal(h.settled.at(-1).identity, 'batch:tools');
});
