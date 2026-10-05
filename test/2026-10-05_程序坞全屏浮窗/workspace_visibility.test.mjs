import { test } from 'node:test';
import assert from 'node:assert/strict';
import { configureWorkspaceVisibility } from '../../desktop-electron/src/quick/workspace-visibility.js';

test('cross-Space/full-screen visibility never requests process-type transformation', () => {
  const calls = [];
  configureWorkspaceVisibility({setVisibleOnAllWorkspaces: (...args) => calls.push(args)});
  assert.deepEqual(calls, [[true, {visibleOnFullScreen:true, skipTransformProcessType:true}]]);
});

test('quick panels and multiple display overlays receive independent safe options', () => {
  const calls = [];
  const window = {setVisibleOnAllWorkspaces: (_visible, options) => calls.push(options)};
  for (let index = 0; index < 3; index++) configureWorkspaceVisibility(window);
  calls[0].skipTransformProcessType = false;
  assert.equal(calls[1].skipTransformProcessType, true);
  assert.equal(calls[2].visibleOnFullScreen, true);
});
