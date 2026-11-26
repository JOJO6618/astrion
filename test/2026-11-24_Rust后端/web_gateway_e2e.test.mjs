import assert from 'node:assert/strict';
import test from 'node:test';
import { startGatewayFixture } from './gateway_harness.mjs';

function sessionCookie(response) {
  const cookie = response.headers.get('set-cookie');
  assert.ok(cookie, 'Server must issue a session cookie');
  return cookie.split(';', 1)[0];
}

test('web binary logs in, serves the authenticated desktop APIs and isolates the bearer channel', { timeout: 30_000 }, async (t) => {
  const { gateway } = await startGatewayFixture(t, {
    model: 'offline', web: true,
    startMock: async () => ({endpoint: 'http://127.0.0.1:1/v1/chat/completions', close() {}}),
  });
  const origin = gateway.baseUrl;
  const request = (path, options = {}) => fetch(origin + path, {signal: AbortSignal.timeout(5_000), ...options});
  const paths = ['/api/health', '/api/conversation-tabs', '/api/project-git/repositories?workspace_id='+gateway.workspaceId, '/api/usage-stats'];
  for (const path of paths) {
    const response = await request(path);
    assert.equal(response.status, 401, path);
    await response.arrayBuffer();
  }
  const challenge = await request('/api/csrf-token');
  const cookie = sessionCookie(challenge);
  const { token } = await challenge.json();
  const login = await request('/host-login', {
    method: 'POST', headers: { cookie, origin, 'content-type': 'application/json', 'x-csrf-token': token }, body: '{}',
  });
  assert.equal(login.status, 200);
  const authenticated = sessionCookie(login);
  assert.notEqual(authenticated, cookie);
  for (const path of [...paths, '/api/tasks?status=active', '/api/tool-approvals/pending']) {
    const response = await request(path, {headers: {cookie: authenticated,'x-astrion-workspace-id':gateway.workspaceId}});
    assert.equal(response.status, 200, path);
    assert.equal((await response.json()).success, true, path);
  }
  const denied = await request('/api/tasks', {headers: {cookie: authenticated, authorization: 'Bearer invalid'}});
  assert.equal(denied.status, 401);
});
