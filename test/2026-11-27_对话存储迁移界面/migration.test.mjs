import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, beforeEach, test } from 'node:test';
import { runInNewContext } from 'node:vm';
import { createPinia, setActivePinia } from 'pinia';
import { createServer } from 'vite';

const migrationUrl = '/api/conversation-storage/migration';
const required = { code: 'conversation_migration_required', error: 'Incompatible conversation' };
const snapshot = (changes = {}) => ({
  pending: 0,
  migrated: 0,
  failed: 0,
  errors: [],
  running: false,
  ...changes
});

const server = await createServer({
  server: { middlewareMode: true, ws: false },
  appType: 'custom',
  logLevel: 'error'
});
const {
  requiresConversationMigration,
  onConversationStorageMigrated,
  useConversationStorageMigrationStore
} = await server.ssrLoadModule('/static/src/stores/conversationStorageMigration.ts');

const previousFetch = globalThis.fetch;
after(async () => {
  globalThis.fetch = previousFetch;
  delete globalThis.window;
  await server.close();
});

let requests;
function wireFetch(responses) {
  window.fetch = async (url, options) => {
    requests.push([url, options]);
    const next = responses.shift();
    assert.ok(next, `unexpected request to ${url}`);
    return next;
  };
  runInNewContext(readFileSync('static/security.js', 'utf8'), { window, Headers, Request });
  globalThis.fetch = (url, options) => window.fetch(url, options);
}

beforeEach(() => {
  requests = [];
  globalThis.window = Object.assign(new EventTarget(), {
    setTimeout: queueMicrotask
  });
  setActivePinia(createPinia());
});

test('the startup check offers migration and never starts it automatically', async () => {
  wireFetch([Response.json(snapshot({ pending: 2 }))]);
  const store = useConversationStorageMigrationStore();
  await store.checkStartup();
  assert.equal(store.open, true);
  assert.equal(store.status.pending, 2);
  store.dismiss();
  await store.checkStartup();
  assert.equal(store.open, false);
  assert.deepEqual(requests.map(([url, options]) => [url, options.method]), [[migrationUrl, 'GET']]);
});

test('explicit migration uses CSRF, polls a single job and can retry failures', async () => {
  wireFetch([
    Response.json(snapshot({ pending: 2 })),
    Response.json({ token: 'fixture-csrf' }),
    Response.json(snapshot({ pending: 2, running: true })),
    Response.json(snapshot({ pending: 1, migrated: 1, failed: 1, errors: ['old-b'] })),
    Response.json(snapshot({ pending: 1, running: true })),
    Response.json(snapshot({ migrated: 1 }))
  ]);
  const store = useConversationStorageMigrationStore();
  await store.checkStartup();
  let refreshes = 0;
  const stop = onConversationStorageMigrated(() => refreshes++);
  try {
    const pending = store.load('POST');
    assert.equal(await store.load('POST'), false);
    store.dismiss();
    assert.equal(store.open, true);
    assert.equal(await pending, true);
    assert.deepEqual(store.status.errors, ['old-b']);
    assert.equal(store.status.failed, 1);
    assert.equal(store.busy, false);
    assert.equal(await store.load('POST'), true);
    assert.deepEqual(store.status, snapshot({ migrated: 1 }));
    assert.equal(refreshes, 2);
    assert.equal(requests.filter(([url]) => url === '/api/csrf-token').length, 1);
    const writes = requests.filter(([url, options]) => url === migrationUrl && options.method === 'POST');
    assert.equal(writes.length, 2);
    assert.ok(writes.every(([, options]) => options.credentials === 'same-origin'));
    assert.ok(writes.every(([, options]) => options.headers.get('X-CSRF-Token') === 'fixture-csrf'));
  } finally {
    stop();
  }
});

test('a backend without the migration route leaves startup quiet', async () => {
  wireFetch([new Response('', { status: 404 })]);
  const store = useConversationStorageMigrationStore();
  await store.checkStartup();
  assert.equal(store.open, false);
  assert.equal(store.startupChecked, true);
});

test('only the migration conflict is presented as an incompatible conversation', async () => {
  wireFetch([Response.json(required, { status: 409 })]);
  const { loadView, ChatApiError } = await server.ssrLoadModule('/static/src/chat/api.ts');
  await assert.rejects(loadView('conv_old'), (error) => {
    assert.ok(error instanceof ChatApiError);
    assert.equal(requiresConversationMigration(error.status, error.code), true);
    return true;
  });
  assert.equal(requiresConversationMigration(409, 'navigation_superseded'), false);
  assert.equal(requiresConversationMigration(404, required.code), false);
});
