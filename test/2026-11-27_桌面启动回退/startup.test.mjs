import assert from 'node:assert/strict';
import test from 'node:test';
import { startDesktopPort } from '../../desktop-electron/src/backend-startup.js';

const port = 12345;
const fresh = 12346;

function harness(overrides = {}) {
  const operations = [];
  const start = () => startDesktopPort({
    preferredPort: port,
    resolveBackendPort: async () => port,
    pickFreePort: async () => fresh,
    spawnBackend: bound => {
      operations.push(['spawn', bound]);
      return { port: bound };
    },
    waitBackendReady: async (bound, child) => {
      assert.equal(bound, child.port);
      operations.push(['ready', bound]);
      if (bound === port) throw new Error('sidecar exited before readiness');
    },
    shutdownBackend: async () => { operations.push('stop'); },
    primeDesktopSession: async bound => { operations.push(['prime', bound]); return true; },
    savePreferredPort: bound => operations.push(['save', bound]),
    warn: message => operations.push(['warning', message]),
    ...overrides,
  });
  return { start, operations };
}

test('a lost preferred port starts exactly one replacement, after the old child exits', async () => {
  let stopped;
  const exiting = new Promise(resolve => { stopped = resolve; });
  const fixture = harness({ shutdownBackend: () => exiting });
  const ready = fixture.start();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(fixture.operations, [['spawn', port], ['ready', port]]);
  stopped({ code: 1 });
  assert.equal(await ready, fresh);
  assert.deepEqual(fixture.operations.filter(operation => operation[0] !== 'warning'), [
    ['spawn', port], ['ready', port], ['spawn', fresh], ['ready', fresh],
    ['save', fresh], ['prime', fresh],
  ]);
});

test('never replaces a backend whose exit was not confirmed', async () => {
  const { start, operations } = harness({ shutdownBackend: async () => false });
  await assert.rejects(start, /exited before readiness/);
  assert.deepEqual(operations, [['spawn', port], ['ready', port]]);
});

test('transient session priming failure retains the healthy backend for frontend login', async () => {
  for (const primeDesktopSession of [
    async () => false,
    async () => { throw new Error('temporarily unavailable'); },
  ]) {
    const { start, operations } = harness({
      resolveBackendPort: async () => fresh, primeDesktopSession,
    });
    assert.equal(await start(), fresh);
    assert.deepEqual(operations.filter(operation => operation[0] !== 'warning'), [
      ['spawn', fresh], ['ready', fresh], ['save', fresh],
    ]);
    assert.equal(operations[3][0], 'warning');
  }
});

test('a failed fresh port is cleaned up once and not retried', async () => {
  const { start, operations } = harness({
    resolveBackendPort: async () => fresh,
    waitBackendReady: async () => { throw new Error('invalid backend configuration'); },
  });
  await assert.rejects(start, /invalid backend configuration/);
  assert.deepEqual(operations, [['spawn', fresh], 'stop']);
});
