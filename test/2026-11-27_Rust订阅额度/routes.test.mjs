import assert from 'node:assert/strict';
import test from 'node:test';
import { startGatewayFixture } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const endpoints = [
  ['GET', '/api/codex/usage'],
  ['GET', '/api/codex/weekly-value'],
  ['GET', '/api/codex/reset-credits'],
  ['POST', '/api/codex/reset-credits/consume'],
  ['GET', '/api/subscription/opencode-usage?provider_id=missing-fixture']
];
const startMock = async () => ({ endpoint: 'http://127.0.0.1:1/v1/chat/completions', close() {} });

const cookie = response => response.headers.get('set-cookie')?.split(';', 1)[0];

async function login(origin) {
  const first = await fetch(`${origin}/api/csrf-token`);
  const csrf = (await first.json()).token;
  const response = await fetch(`${origin}/host-login`, {
    method: 'POST',
    headers: { cookie: cookie(first), origin, 'x-csrf-token': csrf, 'content-type': 'application/json' },
    body: '{}'
  });
  assert.equal(response.status, 200);
  const session = cookie(response);
  assert.ok(session);
  const second = await fetch(`${origin}/api/csrf-token`, { headers: { cookie: session } });
  return { session, token: (await second.json()).token };
}

function send(origin, method, path, headers = {}) {
  return fetch(origin + path, {
    method, signal: AbortSignal.timeout(3000),
    headers: { ...headers, 'content-type': 'application/json' },
    body: method === 'POST' ? '{"credit_id":"synthetic"}' : undefined
  });
}

test('Host serves subscription quota only to authenticated sessions and checks POST CSRF', async t => {
  const { gateway } = await startGatewayFixture(t, { web: true, startMock });
  const origin = gateway.baseUrl;
  const { session, token } = await login(origin);
  const authorization = `Bearer ${await gateway.readToken()}`;
  for (const [method, path] of endpoints) {
    assert.equal((await send(origin, method, path)).status, method === 'POST' ? 403 : 401, path);
    assert.ok([401, 403].includes((await send(origin, method, path, { authorization })).status), path);
    if (method === 'POST') {
      assert.equal((await send(origin, method, path, { cookie: session })).status, 403, path);
    }
    const response = await send(origin, method, path, { cookie: session, 'x-csrf-token': token });
    if (path.includes('opencode')) {
      assert.equal(response.status, 200);
      assert.equal((await response.json()).error, 'provider_not_found');
    } else {
      assert.equal(response.status, 404, path);
      assert.equal((await response.json()).error, 'account_not_found', path);
    }
  }
});

test('Headless exposes no subscription quota routes', async t => {
  const { gateway } = await startGatewayFixture(t, { startMock });
  const authorization = `Bearer ${await gateway.readToken()}`;
  for (const [method, path] of endpoints) {
    assert.equal((await send(gateway.baseUrl, method, path, { authorization })).status, 404, path);
  }
});
