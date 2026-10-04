import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { InputRegions } from '../../desktop-electron/src/quick/input-regions.js';
const bundled = await build({ entryPoints: ['static/src/utils/avatarTracking.ts'], bundle: true, write: false, format: 'esm' });
const { eyeTrackingOffset } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);

test('equal left/right cursor distances produce exactly mirrored eye offsets', () => {
  const center = { x: 43, y: 791 };
  for (const distance of [5, 20, 90, 180, 360]) {
    for (const dy of [0, -90, 90]) {
      const left = eyeTrackingOffset({ x: center.x - distance, y: center.y + dy }, center);
      const right = eyeTrackingOffset({ x: center.x + distance, y: center.y + dy }, center);
      assert.equal(left.x, -right.x);
      assert.equal(left.y, right.y);
    }
  }
});

test('both directions share the same maximum and invalid positions stay neutral', () => {
  assert.deepEqual(eyeTrackingOffset({ x: -500, y: 0 }, { x: 0, y: 0 }), { x: -10, y: 0 });
  assert.deepEqual(eyeTrackingOffset({ x: 500, y: 0 }, { x: 0, y: 0 }), { x: 10, y: 0 });
  assert.deepEqual(eyeTrackingOffset({ x: NaN, y: 0 }, { x: 0, y: 0 }), { x: 0, y: 0 });
});

test('global cursor forwarding keeps negative coordinates outside the left window edge', context => {
  const sent = [];
  let cursor = { x: 200, y: 800 };
  const window = { isDestroyed: () => false, isVisible: () => true, getBounds: () => ({ x: 446, y: 37 }),
    setIgnoreMouseEvents() {}, webContents: { send: (...args) => sent.push(args) } };
  const regions = new InputRegions(window, { getCursorScreenPoint: () => cursor });
  context.after(() => regions.dispose());
  regions.syncCursor();
  assert.deepEqual(sent.at(-1), ['quick:pointer-position', { x: -246, y: 763 }]);
  const count = sent.length;
  regions.syncCursor(); assert.equal(sent.length, count);
  cursor = { x: 1200, y: 800 }; regions.syncCursor();
  assert.deepEqual(sent.at(-1), ['quick:pointer-position', { x: 754, y: 763 }]);
});
