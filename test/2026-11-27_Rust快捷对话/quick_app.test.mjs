import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import { closeServer, listenOnLoopback, waitFor } from '../2026-11-24_Rust后端/gateway_harness.mjs';
import { startAppRuntimeFixture } from '../2026-11-26_Rust提问计划审批/app_runtime_fixture.mjs';

const app = process.env.ASTRION_TEST_APP;
const model = 'local-quick-model';
const workspace = 'quick-verified';
const message = 'Message from the packaged Quick Chat';
const reply = 'Reply from the bundled Rust backend';

async function startMock() {
  const messages = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks));
    messages.push({ model: body.model, text: body.messages.at(-1).content });
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    const chunk = { choices: [{ index: 0, delta: { content: reply }, finish_reason: 'stop' }] };
    response.end('data: ' + JSON.stringify(chunk) + '\n\ndata: [DONE]\n\n');
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, messages, close: () => closeServer(server) };
}

test('signed directory app opens Quick Chat and shares the same bundled Rust Gateway', {
  timeout: 60_000, skip: !app,
}, async t => {
  execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', app]);
  for (const executable of ['quick-entry-listener', 'quick-entry-capture', 'runtime/rust/astrion-headless']) {
    execFileSync('/usr/bin/codesign', ['--verify', '--strict', join(app, 'Contents/Resources', executable)]);
  }
  const fixture = await startAppRuntimeFixture(t, app, {
    model, startMock, workspaceIds: [workspace], modelOptions: { multimodal: 'image' },
    dataSubdirectory: 'isolated-data-override', async prepare() {},
  });
  const tab = await waitFor('desktop content tab', () => fixture.view('/chrome', `(async () => {
    const { token } = await fetch('/api/csrf-token').then(response => response.json());
    const result = await fetch('/api/conversation-tabs/commands', {
      method: 'POST', headers: { 'x-csrf-token': token, 'content-type': 'application/json' },
      body: JSON.stringify({ command_id: 'quick-app-settings-tab', action: 'create',
        payload: { key: 'tab_quick_app', conversation_kind: 'agent', workspace_id: ${JSON.stringify(workspace)} } }),
    });
    return { status: result.status, body: await result.json() };
  })()`));
  assert.equal(tab.status, 200, JSON.stringify(tab));
  const opened = await waitFor('trusted desktop Quick Chat settings', () => fixture.view('/', `(async () => {
    if (!window.astrionQuickSettings) return false;
    await window.astrionQuickSettings.configure({
      enabled: true, workspace: ${JSON.stringify(workspace)}, model: ${JSON.stringify(model)}
    });
    await window.astrionQuickSettings.open();
    return true;
  })()`));
  assert.ok(opened);
  const gateway = await waitFor('visible Quick Chat and authenticated workspace', () => fixture.quickView(`(async () => {
    const composer = document.querySelector('.quick-prompt textarea');
    if (!composer || !window.astrionQuick || document.querySelector('.setup-panel')) return false;
    const workspaces = await window.astrionQuick.request('/api/host/workspaces');
    const config = await window.astrionQuick.info();
    const models = await window.astrionQuick.request('/api/v1/models', 'GET', undefined, config.workspace);
    return { origin: location.origin, workspaces, config, models };
  })()`));
  assert.equal(gateway.origin, 'astrion-quick://app');
  assert.equal(gateway.config.workspace, workspace);
  assert.match(JSON.stringify(gateway.workspaces), /quick-verified/);
  assert.ok(gateway.models.items.some(item => item.model_key === model), JSON.stringify(gateway.models));
  await waitFor('Quick Chat composer sends', () => fixture.quickView(`(() => {
    const composer = document.querySelector('.quick-prompt textarea');
    const send = document.querySelector('.send-button');
    if (!composer || !send) return false;
    composer.value = ${JSON.stringify(message)};
    composer.dispatchEvent(new Event('input', { bubbles: true }));
    if (send.disabled) return false;
    send.click();
    return true;
  })()`));
  await waitFor('Quick Chat displays its streamed reply', () => fixture.quickView(
    `document.body.innerText.includes(${JSON.stringify(reply)})`), 15_000);
  const history = await fixture.quickView(`window.astrionQuick.request(
    '/api/runtime/sessions?quick_entry=1', 'GET', undefined, ${JSON.stringify(workspace)})`);
  assert.match(JSON.stringify(history), /"quick_entry":true/);
  assert.deepEqual(fixture.mock.messages, [{ model, text: message }]);
  assert.equal(await fixture.quickView("(() => { window.astrionQuick.hide(); return true; })()"), true);
  await waitFor('Quick Chat can hide while desktop stays open', async () => (
    !await fixture.quickView('true') && await fixture.view('/chrome', 'document.readyState === "complete"')
  ));
  console.log(JSON.stringify({ backendPid: fixture.backendPid, quickChat: true, overriddenDataDir: true, signed: true }));
});
