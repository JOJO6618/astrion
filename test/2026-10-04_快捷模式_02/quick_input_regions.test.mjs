import { test } from 'node:test';
import assert from 'node:assert/strict';
import { InputRegions } from '../../desktop-electron/src/quick/input-regions.js';
import { quickCanvasBounds } from '../../desktop-electron/src/quick/geometry.js';

test('the native canvas covers a stable work area above the Dock', () => {
  assert.deepEqual(quickCanvasBounds({ x: 0, y: 25, width: 1512, height: 850 }), { x: 276, y: 37, width: 960, height: 826 });
});

function fixture(context) {
  let cursor = { x: 800, y: 100 }, visible = true;
  const calls = [];
  const window = { isDestroyed: () => false, isVisible: () => visible,
    getBounds: () => ({ x: 446, y: 37, width: 620, height: 826 }),
    setBounds() { throw new Error('Content changes must never resize/reposition the native canvas'); },
    setIgnoreMouseEvents: (...args) => calls.push(args) };
  const regions = new InputRegions(window, { getCursorScreenPoint: () => cursor });
  context.after(() => regions.dispose());
  return { regions, calls, cursor: value => { cursor = value; }, hide: () => { visible = false; } };
}

test('menu opening and reply expansion change hit regions without changing window geometry', context => {
  const { regions, calls, cursor } = fixture(context);
  regions.update([{ x: 14, y: 710, width: 592, height: 100 }]);
  assert.deepEqual(calls, [[true, { forward: true }]]);
  cursor({ x: 500, y: 800 }); regions.syncCursor();
  assert.deepEqual(calls.at(-1), [false, { forward: true }]);
  regions.update([{ x: 14, y: 710, width: 592, height: 100 }, { x: 14, y: 420, width: 285, height: 280 }]);
  cursor({ x: 500, y: 500 }); regions.syncCursor();
  assert.equal(calls.at(-1)[0], false);
  regions.update([{ x: 14, y: 710, width: 592, height: 100 }]);
  assert.deepEqual(calls.at(-1), [true, { forward: true }]);
  regions.update([{ x: 14, y: 410, width: 592, height: 400 }]);
  assert.deepEqual(calls.at(-1), [false, { forward: true }]);
});

test('repeated layout snapshots do not toggle event routing or accept malformed regions', context => {
  const { regions, calls } = fixture(context);
  const snapshot = [{ x: 14, y: 710, width: 592, height: 100 }];
  assert.equal(regions.update(snapshot), true);
  assert.equal(regions.update(snapshot), false);
  assert.equal(regions.update(null), false);
  regions.update([{ x: NaN, y: 0, width: 100, height: 100 }, { x: 0, y: 0, width: -1, height: 100 }]);
  assert.deepEqual(regions.regions, []);
  assert.equal(calls.length, 1);
});
