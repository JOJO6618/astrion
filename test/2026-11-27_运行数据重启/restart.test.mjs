import assert from 'node:assert/strict';
import test from 'node:test';
import { createRunDataRestart } from '../../desktop-electron/src/rundata-restart.js';

function harness(stopBackend) {
  const operations = [];
  const timers = [];
  const errors = [];
  const restart = createRunDataRestart({
    app: {
      relaunch: () => operations.push('relaunch'),
      exit: code => operations.push(['exit', code]),
    },
    stopBackend: () => {
      operations.push('stop');
      return stopBackend();
    },
    onFailure: error => errors.push(error),
    schedule: (fn, delay) => {
      assert.equal(delay, 250);
      timers.push(fn);
    },
  });
  return { restart, operations, timers, errors };
}

test('restart waits for the managed backend exit and ignores duplicate requests', async () => {
  let markExited;
  const shutdown = new Promise(resolve => { markExited = resolve; });
  const { restart, operations, timers, errors } = harness(() => shutdown);
  restart();
  restart();
  assert.deepEqual(operations, []);
  assert.equal(timers.length, 1);
  const run = timers.shift()();
  restart();
  assert.deepEqual(operations, ['stop']);
  assert.equal(timers.length, 0);
  markExited({ code: 0, signal: null });
  await run;
  assert.deepEqual(operations, ['stop', 'relaunch', ['exit', 0]]);
  assert.deepEqual(errors, []);
});

test('a timeout or a shutdown error does not start a migration; a later retry works', async () => {
  const failures = [
    () => false,
    () => { throw new Error('shutdown failed'); },
  ];
  const { restart, operations, timers, errors } = harness(() => failures.shift()?.());
  restart();
  await timers.shift()();
  assert.deepEqual(operations, ['stop']);
  restart();
  await timers.shift()();
  assert.deepEqual(operations, ['stop', 'stop']);
  assert.equal(errors.length, 2);
  restart();
  await timers.shift()();
  assert.deepEqual(operations, ['stop', 'stop', 'stop', 'relaunch', ['exit', 0]]);
});
