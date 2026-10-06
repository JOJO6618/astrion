const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const { createLoader, repo } = require('../2026-10-06_极简扫光_02/helpers.cjs');
const chat = path.join(repo, 'static/src/components/chat');
const load = createLoader();
const motion = load(path.join(chat, 'summarySweepMotion.ts'));
const { SummarySweepController } = load(path.join(chat, 'summarySweepController.ts'));

function label(text, changes = {}) {
  const kind = changes.kind || 'thinking';
  return {
    text, identity: 'thinking-1', kind, animate: true,
    sweeping: kind === 'tool', lineComplete: false, ...changes
  };
}

function harness(width = 300) {
  let text = '';
  let hidden = false;
  const paints = [];
  const shown = [];
  const settled = [];
  const surface = {
    setText(value) { text = value; shown.push(value); },
    renderTarget(prefix, target) { text = target; shown.push(target); },
    measure() { return { width, textWidth: Array.from(text).length * 10, height: 26 }; },
    paint(mode, progress, shape) { paints.push({ mode, progress, shape }); },
    clear(value = false) { hidden = value; }
  };
  return {
    controller: new SummarySweepController(surface, (input) => settled.push(input)),
    paints, shown, settled,
    get text() { return text; },
    get hidden() { return hidden; }
  };
}

test('slow single-tool sweep follows visible distance instead of fixed duration', () => {
  const short = motion.createSweepGeometry(150);
  const long = motion.createSweepGeometry(490);
  assert.ok(motion.getSweepDuration(long) > motion.getSweepDuration(short));
  for (const shape of [short, long]) {
    assert.equal(Math.round(shape.distance / (motion.getSweepDuration(shape) / 1000)), 320);
  }
  assert.equal(motion.SUMMARY_SWEEP_GAP_MS, 650);
});

test('soft diagonal masks have ordered stops and leave both ends completely', () => {
  for (const width of [1, 150, 300, 490]) {
    const shape = motion.createSweepGeometry(width);
    assert.ok(shape.startX + shape.bandWidth + shape.tilt + shape.feather < 0);
    assert.ok(shape.startX + shape.distance - shape.feather > width);
    for (const progress of [0, 0.25, 0.5, 0.75, 1]) {
      const masks = motion.createSweepMasks(shape, progress);
      for (const mode of ['beam', 'reveal', 'hide']) {
        assert.ok(masks[mode].includes('50%'));
        const stops = Array.from(masks[mode].matchAll(/(-?\d+\.\d+)px/g), (item) => Number(item[1]));
        assert.ok(stops.every((stop, index) => index === 0 || stop >= stops[index - 1]));
      }
    }
  }
});

test('thinking buffers later stream chunks until the next appearance wave', () => {
  const h = harness();
  h.controller.setInput(label('first chunk', { sweeping: true }), 0);
  h.controller.setInput(label('first chunk and the next chunk', { sweeping: true }), 1);
  assert.equal(h.text, 'first chunk');
  h.controller.tick(1000);
  assert.equal(h.text, 'first chunk');
  h.controller.tick(1650);
  assert.equal(h.text, 'first chunk and the next chunk');
  assert.equal(h.paints.at(-1).mode, 'sweep');
});

test('thinking keeps a single sweep within the visible line width', () => {
  const h = harness(40);
  h.controller.setInput(label('a complete and overflowing first line', { sweeping: true }), 0);
  h.controller.tick(1000);
  h.controller.tick(1650);
  assert.equal(h.paints.at(-1).mode, 'sweep');
  assert.equal(h.paints.at(-1).shape.width, 40);
  assert.equal(h.controller.needsFrame, true);
});

test('model completion exposes the full thinking text without waiting for entry', () => {
  const h = harness();
  h.controller.setInput(label('old chunk'), 0);
  h.controller.setInput(label('new chunk', { forceComplete: true }), 1);
  assert.equal(h.text, 'new chunk');
  assert.equal(h.controller.needsFrame, false);
  assert.equal(h.hidden, false);
});

test('tool transitions erase A then reveal full B, with a 90ms gap', () => {
  const h = harness();
  const a = label('AAA', { identity: 'tool-a', kind: 'tool', lineComplete: true });
  const b = label('BBB', { identity: 'tool-b', kind: 'tool', lineComplete: true });
  h.controller.setInput(a, 0);
  h.controller.tick(1000);
  h.controller.setInput(b, 1100);
  assert.equal(h.paints.at(-1).mode, 'hide');
  assert.equal(h.text, 'AAA');
  h.controller.tick(2100);
  assert.equal(h.hidden, true);
  h.controller.tick(2189);
  assert.equal(h.text, 'AAA');
  h.controller.tick(2190);
  assert.equal(h.text, 'BBB');
  assert.equal(h.paints.at(-1).mode, 'reveal');
  assert.ok(!h.shown.includes('B') && !h.shown.includes('BB'));
});

test('a newer label supersedes an obsolete pending label during erase', () => {
  const h = harness();
  const tool = (text) => label(text, { identity: text, kind: 'tool', lineComplete: true });
  h.controller.setInput(tool('AAA'), 0);
  h.controller.tick(1000);
  h.controller.setInput(tool('BBB'), 1100);
  h.controller.setInput(tool('CCC'), 1200);
  h.controller.tick(2100);
  h.controller.tick(2190);
  assert.equal(h.text, 'CCC');
  assert.ok(!h.shown.includes('BBB'));
});

test('another single-tool sweep waits 650ms after the previous one exits', () => {
  const h = harness();
  h.controller.setInput(label('AAA', { kind: 'tool', lineComplete: true }), 0);
  h.controller.tick(1000);
  const count = h.paints.length;
  h.controller.tick(1649);
  assert.equal(h.paints.length, count);
  h.controller.tick(1650);
  assert.equal(h.paints.at(-1).mode, 'sweep');
  h.controller.tick(2004);
  const finishedCount = h.paints.length;
  h.controller.tick(2653);
  assert.equal(h.paints.length, finishedCount);
  h.controller.tick(2654);
  assert.equal(h.paints.at(-1).progress, 0);
});

test('history and reduced motion show complete text without beams', () => {
  for (const reduced of [false, true]) {
    const h = harness();
    h.controller.setReducedMotion(reduced, 0);
    h.controller.setInput(label('complete text', { animate: reduced }), 0);
    assert.equal(h.text, 'complete text');
    assert.equal(h.controller.needsFrame, false);
    assert.equal(h.paints.length, 0);
  }
});

test('a completed single tool remains static after its final label appears', () => {
  const h = harness();
  h.controller.setInput(label('last tool', { kind: 'tool', sweeping: false }), 0);
  h.controller.tick(1000);
  const count = h.paints.length;
  h.controller.tick(10000);
  assert.equal(h.paints.length, count);
  assert.equal(h.controller.needsFrame, false);
});
