import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const source = await fs.readFile('desktop-electron/src/quick/capture-ui.js', 'utf8');
function fixture() {
  const handlers = {}, calls = [], selection = { hidden: true, style: {} };
  let setExclude;
  vm.runInNewContext(source, {
    document: { getElementById: () => selection, body: { setPointerCapture() {} } },
    window: { addEventListener: (name, callback) => { handlers[name] = callback; },
      capture: { exclude: callback => { setExclude = callback; },
        select: rect => calls.push(['select', { ...rect }]),
        dismiss: () => calls.push(['dismiss']), cancel: () => calls.push(['cancel']) } }
  });
  const pointer = (name, x, y) => handlers[name]({ button: 0, pointerId: 1, clientX: x, clientY: y });
  return { handlers, calls, selection, pointer, setExclude };
}

test('outside click dismisses without taking a screenshot', () => {
  const f = fixture();
  f.pointer('pointerdown', 100, 100); f.pointer('pointerup', 101, 100);
  assert.deepEqual(f.calls, [['dismiss']]);
});
test('outside drag captures the selected rectangle without dismissing', () => {
  const f = fixture();
  f.pointer('pointerdown', 200, 180); f.pointer('pointermove', 100, 80);
  assert.equal(f.selection.hidden, false);
  f.pointer('pointerup', 100, 80);
  assert.deepEqual(f.calls, [['select', { x: 100, y: 80, width: 100, height: 100 }]]);
});
test('composer and menus cannot initiate a screenshot or dismiss', () => {
  const f = fixture(); f.setExclude([{ x: 90, y: 90, width: 150, height: 150 }]);
  f.pointer('pointerdown', 100, 100); f.pointer('pointerup', 100, 100);
  assert.deepEqual(f.calls, []);
});
test('thin drags are ignored instead of being mistaken for clicks', () => {
  const f = fixture(); f.pointer('pointerdown', 100, 100); f.pointer('pointerup', 200, 101);
  assert.deepEqual(f.calls, []);
});
