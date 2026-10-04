import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { QuickGateway, allowedRoute } from '../../desktop-electron/src/quick/gateway.js';
import { cropRectangle } from '../../desktop-electron/src/quick/geometry.js';

test('trusted bridge permits actual runtime contracts and rejects unrelated routes', () => {
  for (const [route, method] of [
    ['/api/tasks/t1/runtime_guidance', 'POST'], ['/api/tasks/t1?from=2', 'GET'],
    ['/api/tool-approvals/a1/decision', 'POST'], ['/api/user-questions/q1/answer', 'POST'],
    ['/api/plan-approvals/p1/answer', 'POST'], ['/api/runtime/sessions/c1/history', 'GET']
  ]) assert.equal(allowedRoute(route, method), true, `${method} ${route}`);
  for (const route of ['/api/providers', '/api/tasks/../providers', 'https://example.com/api/tasks', '/api/tasks#x']) {
    assert.equal(allowedRoute(route, 'GET'), false);
  }
  assert.equal(allowedRoute('/api/tasks', 'DELETE'), false);
});

test('reuses existing Gateway with host Bearer and workspace binding', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'astrion-quick-test-'));
  await fs.mkdir(path.join(root, 'host/data'), { recursive: true });
  await fs.writeFile(path.join(root, 'host/data/host_api_token'), 'test-only-token');
  let calls = 0;
  const server = http.createServer((request, response) => {
    calls++;
    if (calls > 1) assert.equal(request.headers.authorization, 'Bearer test-only-token');
    if (request.url === '/api/tasks/t1') assert.equal(request.headers['x-astrion-workspace-id'], 'w1');
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ success: true, data: { workspaces: [] } }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const gateway = new QuickGateway({ port: server.address().port, dataRoot: root });
    assert.deepEqual((await gateway.ensureRunning()).data.workspaces, []);
    await gateway.request('/api/tasks/t1', 'GET', undefined, 'w1');
    assert.equal(gateway.startError, undefined);
    assert.equal(calls, 3);
    await assert.rejects(() => gateway.request('/api/providers'), /Unsupported/);
  } finally {
    await new Promise(resolve => server.close(resolve));
    await fs.rm(root, { recursive: true, force: true });
  }
});

test('Retina selections scale in display-local coordinates', () => {
  assert.deepEqual(cropRectangle({ x: 100, y: 200, width: 300, height: 150 },
    { x: -1920, y: 0, width: 1920, height: 1080 }, { width: 3840, height: 2160 }),
    { x: 200, y: 400, width: 600, height: 300 });
});

test('selection crops at display edges and rejects invalid coordinates', () => {
  assert.deepEqual(cropRectangle({ x: -5, y: 90, width: 15, height: 20 }, { width: 100, height: 100 }, { width: 100, height: 100 }),
    { x: 0, y: 90, width: 10, height: 10 });
  assert.equal(cropRectangle({ x: NaN, y: 0, width: 10, height: 10 }, { width: 100, height: 100 }, { width: 100, height: 100 }), null);
  assert.equal(cropRectangle({ x: 110, y: 0, width: 10, height: 10 }, { width: 100, height: 100 }, { width: 100, height: 100 }), null);
});
