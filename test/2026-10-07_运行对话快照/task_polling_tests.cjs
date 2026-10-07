/* Offline regression: transpile the current task store and schedule synthetic IO. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('../../node_modules/typescript');
const root = path.resolve(__dirname, '../..');
const noop = () => {};
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const response = (data) => ({ ok: true, json: async () => ({ success: true, data }) });
const task = (id, fields = {}) => ({ task_id: id, status: 'running', created_at: 100, updated_at: 200, ...fields });
const batch = (id, fields = {}) => ({ ...task(id), window_start: 0, next_offset: 0, events: [], ...fields });

function loadPureProtocol() {
  const source = fs.readFileSync(path.join(root, 'static/src/stores/taskPolling.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, module: { exports } }, { filename: 'taskPolling.ts' });
  return exports;
}

function fixture(useRealPinia = false) {
  const requests = [], intervals = new Map(), timeouts = new Map(), toasts = [];
  const realPinia = useRealPinia ? require('pinia') : null;
  let store, timerId = 0;
  const source = fs.readFileSync(path.join(root, 'static/src/stores/task.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    reportDiagnostics: true
  });
  assert.equal((compiled.diagnostics || []).filter((d) => d.category === ts.DiagnosticCategory.Error).length, 0);
  const exports = {};
  const window = {
    setInterval: (cb) => { const id = ++timerId; intervals.set(id, cb); return id; },
    localStorage: { getItem: () => null },
    __vueApp: { uiPushToast: (toast) => toasts.push(toast) }
  };
  const context = {
    exports, module: { exports }, Date, Promise, AbortController, DOMException,
    console: { log: noop, warn: noop, error: noop }, window,
    clearInterval: (id) => intervals.delete(id),
    setTimeout: (cb) => { const id = ++timerId; timeouts.set(id, cb); return id; },
    clearTimeout: (id) => timeouts.delete(id),
    // Deliberately ignore abort for resolution: identity checks must work even if transport races it.
    fetch: (url, options = {}) => {
      const pending = deferred();
      requests.push({ url, options, ...pending });
      return pending.promise;
    },
    require: (name) => {
      if (name === 'pinia' && realPinia) return realPinia;
      if (name === 'pinia') return { defineStore: (_name, config) => {
        store = config.state();
        store.$patch = (patch) => Object.assign(store, patch);
        for (const [key, action] of Object.entries(config.actions)) store[key] = action.bind(store);
        for (const [key, getter] of Object.entries(config.getters)) {
          Object.defineProperty(store, key, { get: () => getter(store) });
        }
        return () => store;
      } };
      if (name === '@/locales') return { t: (key) => key };
      if (name === '../app/methods/common') return { debugLog: noop, goalModeDebugLog: noop };
      if (name === './taskPolling') return loadPureProtocol();
      throw new Error(`Unmocked import: ${name}`);
    }
  };
  vm.runInNewContext(compiled.outputText, context, { filename: 'static/src/stores/task.ts' });
  if (realPinia) store = context.module.exports.useTaskStore(realPinia.createPinia());
  return { store, requests, intervals, timeouts, toasts, window };
}
const eventTypes = (received) => received.map((e) => e.type);

async function snapshotAttachesAbsoluteCursor() {
  const f = fixture(), received = [];
  f.store.attachSnapshot(task('A'), 42, (event) => received.push(event));
  assert.equal(f.requests[0].url, '/api/tasks/A?from=42');
  assert.equal(f.store.taskCreatedAt, 100);
  assert.equal(f.store.taskUpdatedAt, 200);
  assert.equal(f.store.pollGeneration, 1);
  f.requests[0].resolve(response(batch('A', {
    next_offset: 43, window_start: 40, events: [{ idx: 42, type: 'text_chunk', data: { chunk: 'new' } }]
  })));
  await flush();
  assert.deepEqual(eventTypes(received), ['runtime_queue_sync', 'text_chunk']);
  assert.equal(f.store.lastEventIndex, 43);
  assert.equal(f.store.pollingInFlight, false);
  assert.ok(f.requests[0].options.headers['X-Task-Poll']);
}

async function abaOldSuccessAndFinallyAreIgnored() {
  const f = fixture(), received = [];
  const handler = (event) => received.push(event);
  f.store.attachSnapshot(task('A'), 10, handler);
  const oldRequestId = f.store.pollingRequestId;
  const oldTimeout = [...f.timeouts.values()][0];
  f.store.attachSnapshot(task('B'), 20, handler);
  f.store.attachSnapshot(task('A'), 30, handler);
  const owner = f.store.pollingRequestId, controller = f.store.pollingAbortController;
  assert.notEqual(owner, oldRequestId);
  assert.equal(f.store.pollGeneration, 3);
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.equal(f.requests[1].options.signal.aborted, true);
  oldTimeout(); // A queued timer from the old generation cannot abort the new request.
  assert.equal(controller.signal.aborted, false);
  f.requests[0].resolve(response(batch('A', {
    status: 'succeeded', updated_at: 999, next_offset: 999, events: [{ type: 'old_success' }]
  })));
  f.requests[1].resolve(response(batch('B', { next_offset: 888, events: [{ type: 'old_B' }] })));
  await flush();
  assert.deepEqual(received, []);
  assert.equal(f.store.currentTaskId, 'A');
  assert.equal(f.store.taskStatus, 'running');
  assert.equal(f.store.taskUpdatedAt, 200);
  assert.equal(f.store.lastEventIndex, 30);
  assert.equal(f.store.pollingInFlight, true);
  assert.equal(f.store.pollingRequestId, owner);
  assert.equal(f.store.pollingAbortController, controller);
  for (const tick of f.intervals.values()) tick();
  assert.equal(f.requests.length, 3, 'old finally must not allow a parallel fresh poll');
  f.requests[2].resolve(response(batch('A', { next_offset: 31, events: [{ type: 'new_success' }] })));
  await flush();
  assert.deepEqual(eventTypes(received), ['runtime_queue_sync', 'new_success']);
  assert.equal(f.store.lastEventIndex, 31);
  assert.equal(f.store.pollingInFlight, false);
  assert.equal(f.timeouts.size, 0);
}

async function stale404And410CannotStopNewTask() {
  for (const status of [404, 410]) {
    const f = fixture();
    f.store.attachSnapshot(task('A'), 1, noop);
    f.store.attachSnapshot(task('B'), 7, noop);
    const owner = f.store.pollingRequestId;
    f.requests[0].resolve({ ok: false, status });
    await flush();
    assert.equal(f.store.currentTaskId, 'B');
    assert.equal(f.store.isPolling, true);
    assert.equal(f.store.pollingErrorCount, 0);
    assert.equal(f.store.pollingRequestId, owner);
    assert.equal(f.store.pollingInFlight, true);
    assert.equal(f.requests[1].options.signal.aborted, false);
  }
}

async function staleBodySuccessAndFailureAreIgnored() {
  for (const rejectBody of [false, true]) {
    const f = fixture(), body = deferred(), received = [];
    f.store.attachSnapshot(task('A'), 4, (event) => received.push(event));
    f.requests[0].resolve({ ok: true, json: () => body.promise });
    await flush();
    f.store.attachSnapshot(task('A'), 9, (event) => received.push(event));
    if (rejectBody) body.reject(new Error('HTTP 404 from old body'));
    else body.resolve({ success: true, data: batch('A', { next_offset: 99, events: [{ type: 'old' }] }) });
    await flush();
    assert.deepEqual(received, []);
    assert.equal(f.store.lastEventIndex, 9);
    assert.equal(f.store.pollingInFlight, true);
    assert.equal(f.store.pollingErrorCount, 0);
  }
}

async function gapFromZeroAndNonzeroConsumesNothing() {
  for (const offset of [0, 5]) {
    const f = fixture(), received = [];
    f.store.attachSnapshot(task('A'), offset, (event) => received.push(event));
    f.requests[0].resolve(response(batch('A', {
      window_start: 100, next_offset: 102, status: 'succeeded', updated_at: 999,
      runtime_queued_messages: [{ id: 'q', text: 'untrusted tail' }],
      events: [{ idx: 100, type: 'text_chunk' }, { idx: 101, type: 'task_complete' }]
    })));
    await flush();
    assert.deepEqual(eventTypes(received), ['event_window_gap']);
    assert.equal(received[0].data.from_offset, offset);
    assert.equal(received[0].data.window_start, 100);
    assert.equal(f.store.lastEventIndex, offset);
    assert.equal(f.store.taskStatus, 'running');
    assert.equal(f.store.taskUpdatedAt, 200);
    assert.equal(f.store.runtimeQueueSnapshotKey, '');
    assert.equal(f.store.pollingInFlight, false);
  }
}

async function gapHandlerCanAttachReplacement() {
  const f = fixture(), received = [];
  f.store.attachSnapshot(task('A'), 0, (event) => {
    received.push(event);
    if (event.type === 'event_window_gap') f.store.attachSnapshot(task('A'), 102, noop);
  });
  f.requests[0].resolve(response(batch('A', { window_start: 100, next_offset: 102, events: [{ type: 'old_tail' }] })));
  await flush();
  assert.deepEqual(eventTypes(received), ['event_window_gap']);
  assert.equal(f.store.lastEventIndex, 102);
  assert.equal(f.requests[1].url, '/api/tasks/A?from=102');
  assert.equal(f.store.pollingInFlight, true);
}

async function handlerReplacementStopsOldBatch() {
  for (const replacementId of ['B', 'A']) {
    for (const trigger of ['runtime_queue_sync', 'task_complete', 'view_invalidated']) {
      const f = fixture(), received = [];
      f.store.attachSnapshot(task('A'), 4, (event) => {
        received.push(event);
        if (event.type === trigger) {
          f.store.clearTask();
          f.store.attachSnapshot(task(replacementId), 70, noop);
        }
      });
      f.requests[0].resolve(response(batch('A', {
        status: 'succeeded', next_offset: 6,
        events: [{ idx: 4, type: trigger }, { idx: 5, type: 'old_tail' }]
      })));
      await flush();
      assert.equal(received.some((event) => event.type === 'old_tail'), false);
      assert.equal(f.store.currentTaskId, replacementId);
      assert.equal(f.store.lastEventIndex, 70);
      assert.equal(f.store.taskStatus, 'running');
      assert.equal(f.store.taskUpdatedAt, 200);
      assert.equal(f.store.isPolling, true);
      assert.equal(f.store.pollingInFlight, true);
      assert.equal(f.requests[1].url, `/api/tasks/${replacementId}?from=70`);
    }
  }
}

async function syntheticTerminalHandlerCannotStopReplacement() {
  for (const status of ['succeeded', 'canceled']) {
    const f = fixture(), terminalType = status === 'succeeded' ? 'task_complete' : 'task_stopped';
    f.store.attachSnapshot(task('A'), 3, (event) => {
      if (event.type === terminalType) f.store.attachSnapshot(task('B'), 90, noop);
    });
    f.requests[0].resolve(response(batch('A', { status, next_offset: 3 })));
    await flush();
    assert.equal(f.store.currentTaskId, 'B');
    assert.equal(f.store.lastEventIndex, 90);
    assert.equal(f.store.pollingInFlight, true);
    assert.equal(f.store.pollingErrorCount, 0);
  }
}

async function stopAbortsAndQueuedIntervalCannotPollAgain() {
  const f = fixture();
  f.store.attachSnapshot(task('A'), 2, noop);
  const staleTick = [...f.intervals.values()][0];
  const generation = f.store.pollGeneration;
  f.store.stopPolling();
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.equal(f.store.pollGeneration, generation + 1);
  assert.equal(f.store.pollingAbortController, null);
  assert.equal(f.store.pollingRequestId, null);
  assert.equal(f.intervals.size, 0);
  f.store.attachSnapshot(task('B'), 9, noop);
  staleTick();
  assert.equal(f.requests.length, 2);
  f.requests[0].reject(new DOMException('Aborted', 'AbortError'));
  await flush();
  assert.equal(f.store.pollingInFlight, true);
  assert.equal(f.store.pollingErrorCount, 0);
}

async function timeoutRetriesAndStaleTimeoutCannotDamageReplacement() {
  const f = fixture();
  f.store.attachSnapshot(task('A'), 2, noop);
  [...f.timeouts.values()][0]();
  assert.equal(f.requests[0].options.signal.aborted, true);
  assert.equal(f.requests[0].options.signal.reason.name, 'TimeoutError');
  // Even an abort-ignoring transport cannot deliver a timed-out batch as a success.
  f.requests[0].resolve(response(batch('A', { next_offset: 99 })));
  await flush();
  assert.equal(f.store.lastEventIndex, 2);
  assert.equal(f.store.pollingErrorCount, 1);
  assert.equal(f.store.pollingInFlight, false);
  assert.equal(f.store.isPolling, true);
  [...f.intervals.values()][0]();
  assert.equal(f.requests[1].url, '/api/tasks/A?from=2');
  f.store.attachSnapshot(task('B'), 8, noop);
  f.requests[1].reject(new DOMException('Task poll timed out', 'TimeoutError'));
  await flush();
  assert.equal(f.store.currentTaskId, 'B');
  assert.equal(f.store.pollingErrorCount, 0);
  assert.equal(f.store.pollingInFlight, true);
}

async function currentErrorsWarnBut404Stops() {
  const f = fixture();
  f.store.pollingFailThreshold = 2;
  f.store.attachSnapshot(task('A'), 2, noop);
  for (let i = 0; i < 3; i++) {
    f.requests[i].reject(new Error('network unavailable'));
    await flush();
    assert.equal(f.store.isPolling, true);
    [...f.intervals.values()][0]();
  }
  assert.equal(f.toasts.length, 1);
  f.requests[3].resolve(response(batch('A', { next_offset: 2 })));
  await flush();
  assert.equal(f.store.pollingErrorCount, 0);
  assert.equal(f.store.pollingWarned, false);
  [...f.intervals.values()][0]();
  f.requests[4].resolve({ ok: false, status: 404 });
  await flush();
  assert.equal(f.store.currentTaskId, null);
  assert.equal(f.store.isPolling, false);
}

async function queueSyncAndTerminalFallbackRemainNormal() {
  for (const status of ['succeeded', 'canceled', 'failed', 'stopped']) {
    const f = fixture(), received = [];
    f.store.attachSnapshot(task('A'), 1, (event) => received.push(event));
    f.requests[0].resolve(response(batch('A', {
      status, next_offset: 2, task_type: 'compression', runtime_queue_paused: true,
      runtime_queued_messages: [{ id: 'q', text: 'queued' }], events: [{ idx: 1, type: 'text_end' }]
    })));
    await flush();
    const expected = ['runtime_queue_sync', 'text_end'];
    if (status === 'succeeded') expected.push('task_complete');
    if (status === 'canceled') expected.push('task_stopped');
    assert.deepEqual(eventTypes(received), expected);
    assert.equal(received[0].data.paused, true);
    assert.equal(received[0].data.messages[0].id, 'q');
    assert.equal(f.store.lastEventIndex, 2);
    assert.equal(f.store.taskStatus, status);
    assert.equal(f.store.isPolling, false);
    assert.equal(f.store.pollingInFlight, false);
    assert.equal(f.intervals.size, 0);
    if (status === 'succeeded') assert.equal(received[2].data.preserve_pending_messages, true);
  }
}

async function terminalEventsAreNotSynthesizedTwice() {
  for (const [status, type] of [['succeeded', 'task_complete'], ['canceled', 'task_stopped']]) {
    const f = fixture(), received = [];
    f.store.attachSnapshot(task('A'), 0, (event) => received.push(event));
    f.requests[0].resolve(response(batch('A', { status, next_offset: 1, events: [{ idx: 0, type }] })));
    await flush();
    assert.equal(received.filter((e) => e.type === type).length, 1);
  }
}

async function queueDedupAndEmptyBatchCursorAdvance() {
  const f = fixture(), received = [];
  f.store.attachSnapshot(task('A'), 5, (event) => received.push(event));
  for (let i = 0; i < 2; i++) {
    f.requests[i].resolve(response(batch('A', { next_offset: 6 })));
    await flush();
    if (i === 0) [...f.intervals.values()][0]();
  }
  assert.deepEqual(eventTypes(received), ['runtime_queue_sync']);
  assert.equal(f.store.lastEventIndex, 6);
  assert.equal(f.requests[1].url, '/api/tasks/A?from=6');
  assert.ok(f.window.__CONN_DIAG_LOGS__.some((e) => e.event === 'task-poll-start' && e.generation === 1));
}

async function invalidSnapshotDoesNotInterruptOwner() {
  const f = fixture();
  f.store.attachSnapshot(task('A'), 9, noop);
  const owner = f.store.pollingRequestId;
  for (const cursor of [-1, 1.2, NaN, undefined]) {
    assert.throws(() => f.store.attachSnapshot(task('B'), cursor, noop), /absolute cursor/);
  }
  assert.throws(() => f.store.attachSnapshot({}, 0, noop), /task_id/);
  assert.equal(f.store.pollingRequestId, owner);
  assert.equal(f.requests[0].options.signal.aborted, false);
}

async function cancelWaitsForTerminalEvent() {
  const f = fixture();
  f.store.attachSnapshot(task('created'), 3, noop);
  const canceling = f.store.cancelTask();
  assert.equal(f.requests[1].url, '/api/tasks/created/cancel');
  f.requests[1].resolve(response({}));
  await canceling;
  assert.equal(f.store.isPolling, true, 'cancel waits for backend terminal event');
}

async function missingOwnedHandlerDoesNotInterruptOwner() {
  const f = fixture();
  f.store.attachSnapshot(task('A'), 9, noop);
  const owner = f.store.pollingRequestId;
  assert.throws(() => f.store.attachSnapshot(task('B'), 12), /owned event handler/);
  assert.equal(f.store.pollingRequestId, owner);
  assert.equal(f.store.currentTaskId, 'A');
}

async function realPiniaKeepsNativeControllerAndOwnership() {
  const f = fixture(true);
  f.store.attachSnapshot(task('A'), 5, noop);
  const oldSignal = f.requests[0].options.signal;
  assert.equal(require('vue').isReactive(f.store.pollingAbortController), false);
  f.store.attachSnapshot(task('A'), 9, noop);
  assert.equal(oldSignal.aborted, true);
  const owner = f.store.pollingRequestId;
  f.requests[0].reject(new DOMException('Aborted', 'AbortError'));
  await flush();
  assert.equal(f.store.pollingRequestId, owner);
  assert.equal(f.store.pollingInFlight, true);
  f.requests[1].resolve(response(batch('A', { next_offset: 10 })));
  await flush();
  assert.equal(f.store.lastEventIndex, 10);
  assert.equal(f.store.pollingInFlight, false);
}

(async () => {
  const tests = { snapshotAttachesAbsoluteCursor, abaOldSuccessAndFinallyAreIgnored,
    stale404And410CannotStopNewTask, staleBodySuccessAndFailureAreIgnored,
    gapFromZeroAndNonzeroConsumesNothing, gapHandlerCanAttachReplacement,
    handlerReplacementStopsOldBatch, syntheticTerminalHandlerCannotStopReplacement,
    stopAbortsAndQueuedIntervalCannotPollAgain, timeoutRetriesAndStaleTimeoutCannotDamageReplacement,
    currentErrorsWarnBut404Stops, queueSyncAndTerminalFallbackRemainNormal,
    terminalEventsAreNotSynthesizedTwice, queueDedupAndEmptyBatchCursorAdvance,
    invalidSnapshotDoesNotInterruptOwner, cancelWaitsForTerminalEvent, missingOwnedHandlerDoesNotInterruptOwner,
    realPiniaKeepsNativeControllerAndOwnership };
  for (const [name, run] of Object.entries(tests)) {
    await run();
    console.log(`PASS ${name}`);
  }
  console.log(`All ${Object.keys(tests).length} task polling regression groups passed (offline, current TS).`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
