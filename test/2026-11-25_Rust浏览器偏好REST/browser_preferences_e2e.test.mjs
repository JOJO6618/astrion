import assert from 'node:assert/strict';
import { migrate } from '../2026-11-27_Rust对话格式迁移/fixture.mjs';
import { mkdir, readdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { startGatewayFixture } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const TOOLS = '/api/browser-tools';
const SITES = '/api/browser-site-permissions';
const RETENTION = '/api/browser-retention';
const SHARED_ID = 'conv_browser_settings';
const MISSING_ID = 'conv_browser_missing';
const ORIGIN_MISMATCH = 'https://browser.invalid';

const OFF = { success: true, supported: false, enabled: false, pending: null };
const ON = { success: true, supported: false, enabled: true, pending: 'off' };

async function fixtureConversation(data, workspace, id, metadata = {}) {
  const file = join(data, 'conversations', workspace, `${id}.json`);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify({
    id, title: id, created_at: '2024-05-03T10:00:00.000', updated_at: '2024-05-03T10:00:00.000',
    messages: [], metadata,
  }));
  return file;
}

function requestTo(base) {
  return async (path, status, { method = 'GET', headers = {}, body } = {}) => {
    const response = await fetch(base + path, {
      method, redirect: 'manual', signal: AbortSignal.timeout(3_000),
      headers: body === undefined ? headers : { ...headers, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    assert.equal(response.status, status, `${method} ${path}: ${text}`);
    return { json: text ? JSON.parse(text) : null, headers: response.headers };
  };
}

async function login(request, origin) {
  const challenge = await request('/api/csrf-token', 200);
  const cookie = (reply) => reply.headers.get('set-cookie').split(';', 1)[0];
  const accepted = await request('/host-login', 200, {
    method: 'POST', body: {},
    headers: { origin, cookie: cookie(challenge), 'x-csrf-token': challenge.json.token },
  });
  const read = { origin, cookie: cookie(accepted) };
  const after = await request('/api/csrf-token', 200, { headers: read });
  return { read, write: { ...read, 'x-csrf-token': after.json.token } };
}

function owner(workspace, conversation, closed = false) {
  return { scope: ['agent', workspace, conversation], visit: 1, closed };
}

function retentionHeader(headers, ...owners) {
  return { ...headers, 'x-astrion-browser-state': JSON.stringify({ owners }) };
}

async function replaceCatalog(path, workspaces) {
  const next = `${path}.next`;
  await writeFile(next, JSON.stringify({ default_workspace_id: workspaces[0]?.workspace_id, workspaces }));
  await rename(next, path);
}

test('Host browser preferences use authenticated live workspaces and keep passive reads read-only', { timeout: 20_000 }, async (t) => {
  const fixture = await startGatewayFixture(t, {
    web: true, workspaceIds: ['first', 'second'],
    async prepare({ dataDir }) {
      await fixtureConversation(dataDir, 'first', SHARED_ID, { browser_tools_enabled: true, pending_browser_tools: 'off' });
      await fixtureConversation(dataDir, 'second', SHARED_ID);
      await writeFile(join(dataDir, 'browser_site_permissions.json'), JSON.stringify({
        version: 1, sites: { 'a.test': { granted_at: 1 }, 'z.test': { granted_at: 2, legacy: 'retain' } },
      }));
      await writeFile(join(dataDir, 'conversation_tabs.json'), JSON.stringify({
        host: { tabs: [{ kind: 'conv', workspace_id: 'second', conversation_id: SHARED_ID }] },
      }));
    },
  });
  const { gateway, dataDir, workspaces, workspaceCatalog } = fixture;
  const request = requestTo(gateway.baseUrl);
  const tokens = { authorization: `Bearer ${await gateway.readToken()}` };
  for (const path of [TOOLS, SITES, RETENTION]) {
    await request(path, 401);
    await request(path, 401, { headers: tokens });
  }
  const firstSession = await login(request, gateway.baseUrl);
  await migrate(gateway.baseUrl,firstSession.write);
  const secondSession = await login(request, gateway.baseUrl);
  const files = [join(dataDir, 'conversations', 'first', `${SHARED_ID}.json`), join(dataDir, 'conversation_tabs.json')];
  const saved = await Promise.all(files.map((file) => readFile(file, 'utf8')));
  const secondWorkspaceFiles = await readdir(workspaces[1].path);
  const conversation = `${TOOLS}?conversation_id=${SHARED_ID}`;
  const encoded = `${TOOLS}?conversation_id=${SHARED_ID.replace(/^conv_/, '')}`;
  assert.deepEqual((await request(TOOLS, 200, { headers: firstSession.read })).json, OFF);
  assert.deepEqual((await request(encoded, 200, { headers: firstSession.read })).json, ON);
  const head = await request(conversation, 200, { method: 'HEAD', headers: firstSession.read });
  assert.equal(head.json, null);
  assert.equal(head.headers.get('cache-control'), 'no-store');
  await request('/api/host/workspaces/select', 200, {
    method: 'POST', headers: secondSession.write, body: { workspace_id: 'second' },
  });
  assert.deepEqual((await request(conversation, 200, { headers: secondSession.read })).json, OFF);
  assert.deepEqual((await request(conversation, 200, { headers: firstSession.read })).json, ON);
  assert.deepEqual((await request(`${conversation}&workspace_id=second`, 200, { headers: firstSession.read })).json, OFF);
  await request(`${conversation}&workspace_id=second`, 400, { headers: { ...firstSession.read, 'x-astrion-workspace-id': 'first' } });
  await request(`${TOOLS}?conversation_id=missing`, 404, { headers: firstSession.read });

  const switchTools = { method: 'POST', body: { conversation_id: SHARED_ID, enabled: false } };
  await request(TOOLS, 403, { ...switchTools, headers: firstSession.read });
  const refused = await request(TOOLS, 400, { ...switchTools, headers: firstSession.write });
  assert.match(refused.json.error, /macOS\/Windows/);

  const inactive = owner('first', SHARED_ID, true);
  const open = owner('second', SHARED_ID, true);
  const deleted = owner('second', MISSING_ID);
  const unknown = owner('unknown', MISSING_ID, true);
  await request(RETENTION, 400, { headers: firstSession.read });
  assert.deepEqual((await request(RETENTION, 200, {
    headers: retentionHeader(firstSession.read, inactive, open, deleted, unknown),
  })).json, { success: true, username: 'host', deleted: [JSON.stringify(deleted.scope)], release: [JSON.stringify(inactive.scope)] });
  assert.deepEqual((await request(RETENTION, 200, {
    headers: retentionHeader(firstSession.read, owner('first', 'conv_')),
  })).json, { success: true, username: 'host', deleted: [], release: [] });
  assert.deepEqual(await Promise.all(files.map((file) => readFile(file, 'utf8'))), saved);
  assert.deepEqual(await readdir(workspaces[1].path), secondWorkspaceFiles);

  const initialSites = [{ site: 'a.test', granted_at: 1 }, { site: 'z.test', granted_at: 2 }];
  assert.deepEqual((await request(SITES, 200, { headers: secondSession.read })).json, { success: true, supported: false, sites: initialSites });
  const revoke = { method: 'DELETE', body: { site: 'a.test', workspace_id: 'second' } };
  await request(SITES, 403, { ...revoke, headers: firstSession.read });
  await request(SITES, 403, { ...revoke, headers: { ...firstSession.write, origin: ORIGIN_MISMATCH } });
  await request(`${SITES}?workspace_id=first`, 400, { ...revoke, headers: firstSession.write });
  assert.deepEqual((await request(SITES, 200, { headers: firstSession.read })).json.sites, initialSites);
  assert.deepEqual((await request(SITES, 200, { ...revoke, headers: firstSession.write })).json,
    { success: true, removed: true, sites: initialSites.slice(1) });
  assert.deepEqual(JSON.parse(await readFile(join(dataDir, 'browser_site_permissions.json'), 'utf8')),
    { version: 1, sites: { 'z.test': { granted_at: 2, legacy: 'retain' } } });

  const third = { workspace_id: 'third', label: 'third', path: join(dirname(dataDir), 'third') };
  await mkdir(third.path);
  await fixtureConversation(dataDir, 'third', SHARED_ID, { pending_browser_tools: 'on' });
  await migrate(gateway.baseUrl,firstSession.write);
  await replaceCatalog(workspaceCatalog, [workspaces[0], third]);
  assert.deepEqual((await request(`${conversation}&workspace_id=third`, 200, { headers: firstSession.read })).json,
    { ...OFF, pending: 'on' });
  await request(`${conversation}&workspace_id=second`, 404, { headers: secondSession.read });
  await request(SITES, 404, { ...revoke, headers: firstSession.write });
  assert.deepEqual((await request(conversation, 200, { headers: secondSession.read })).json, ON);
  const thirdMissing = owner('third', MISSING_ID);
  assert.deepEqual((await request(RETENTION, 200, {
    headers: retentionHeader(firstSession.read, deleted, thirdMissing),
  })).json.deleted, [JSON.stringify(thirdMissing.scope)]);
  await replaceCatalog(workspaceCatalog, []);
  assert.equal((await request(TOOLS, 200, { headers: firstSession.read })).json.code, 'no_workspace');
  assert.deepEqual((await request(RETENTION, 200, { headers: retentionHeader(firstSession.read) })).json,
    { success: true, username: 'host', deleted: [], release: [] });
});

test('headless listener does not mount browser preference endpoints', { timeout: 20_000 }, async (t) => {
  const { gateway } = await startGatewayFixture(t);
  const request = requestTo(gateway.baseUrl);
  const headers = { authorization: `Bearer ${await gateway.readToken()}` };
  for (const path of [TOOLS, SITES, RETENTION]) {
    await request(path, 404, { headers });
  }
});
