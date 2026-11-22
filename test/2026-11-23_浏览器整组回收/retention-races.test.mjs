import assert from 'node:assert/strict';
import test from 'node:test';
import { profileKey } from '../../desktop-electron/src/browser/policy.js';
import { BrowserRetention } from '../../desktop-electron/src/browser/retention.js';

const key = profileKey('fixture');
const tab = { key: 'tab_fixture', kind: 'conv', conversation_kind: 'agent', workspace_id: 'ws', conversation_id: 'conv_fixture' };
const scope = JSON.stringify(['agent', 'ws', 'conv_fixture']);
const owner = `${key}:${scope}`;
const open = { tabs: [tab] };
const closed = { tabs: [] };

function fixture() {
  const calls = [];
  const session = { fetch: (_url, { headers }) => new Promise(resolve => {
    calls.push({ state: JSON.parse(headers['X-Astrion-Browser-State']), reply: release => resolve({
      ok: true, json: async () => ({ success: true, username: 'fixture', release, deleted: [] })
    }) });
  }) };
  const view = { webContents: { isDestroyed: () => false, getZoomFactor: () => 1, close: () => {} } };
  const page = { id: 'page', owner, view };
  const manager = {
    origin: 'http://127.0.0.1', anySession: () => session, profiles: new Map([[key, { data: { owners: {} } }]]),
    tabs: new Map([[page.id, page]]), popouts: new Map(), detach() {}, layout() {}, publish() {},
    credentials: { remove() {} }, ai: { isActive: () => false, release() {}, cancelOwner() {} },
    profileFeatures: () => ({ denyStale() {} })
  };
  const retention = new BrowserRetention(manager);
  retention.dispose();
  return { retention, calls, page };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

test('closing a conversation starts an immediate query and reopening supersedes its response', async () => {
  const { retention, calls, page } = fixture();
  retention.navigationChanged(open, closed);
  await flush();
  assert.equal(calls[0].state.owners[0].closed, true);
  retention.navigationChanged(closed, open);
  calls[0].reply([scope]);
  await flush();
  assert.equal(calls[1].state.owners[0].closed, false);
  calls[1].reply([]);
  await flush();
  assert.ok(page.view);
});

test('a native residency change discards an in-flight capacity decision', async () => {
  const { retention, calls, page } = fixture();
  const first = retention.poll();
  retention.residencyChanged();
  calls[0].reply([scope]);
  await first;
  await flush();
  assert.ok(page.view);
  calls[1].reply([]);
  await flush();
});

test('new browser activity invalidates an older eviction for the same conversation', async () => {
  const { retention, calls, page } = fixture();
  retention.visit(owner);
  const first = retention.poll();
  retention.visit(owner);
  calls[0].reply([scope]);
  await first;
  assert.ok(page.view);
});
