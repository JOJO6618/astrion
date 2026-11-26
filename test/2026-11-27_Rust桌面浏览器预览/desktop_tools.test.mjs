import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import {
  COMPLETED, closeServer, listenOnLoopback, trackTask, waitFor,
} from '../2026-11-24_Rust后端/gateway_harness.mjs';
import { startAppRuntimeFixture } from '../2026-11-26_Rust提问计划审批/app_runtime_fixture.mjs';

const app = process.env.ASTRION_TEST_APP;
const model = 'isolated-native-browser-model';
const workspaceId = 'desktop-browser-workspace';
const first = 'verify native browser and preview';
const disabled = 'verify browser disabled';
const scheduled = 'verify scheduled Agent';
const scheduleResult = 'isolated schedule result';
const htmlContent = '<main><h1>Preview tool output</h1></main>';
const art = '<show_html ratio="16:9" js="off"><div>Fixture HTML canvas</div></show_html>';
const chart = '<show_ui js="off"><div>Fixture inline visualization</div></show_ui>';

function tool(id, name, args) {
  return { id, type: 'function', function: { name, arguments: JSON.stringify(args) } };
}

function finish(response, reply) {
  response.writeHead(200, { 'content-type': 'text/event-stream' });
  const send = (delta, reason = null) => response.write(`data: ${JSON.stringify({
    id: 'native-browser-fixture', model, object: 'chat.completion.chunk',
    choices: [{ index: 0, delta, finish_reason: reason }],
  })}\n\n`);
  send({ role: 'assistant' });
  if (Array.isArray(reply)) {
    send({ tool_calls: reply.map((call, index) => ({ ...call, index })) });
    send({}, 'tool_calls');
  } else {
    for (const content of reply.match(/.{1,33}|\n/gs)) send({ content });
    send({}, 'stop');
  }
  response.end('data: [DONE]\n\n');
}

async function startMock() {
  const requests = [];
  const errors = [];
  let clicks = 0;
  let origin;
  const server = createServer(async (request, response) => {
    try {
      if (request.url === '/page') {
        response.writeHead(200, { 'content-type': 'text/html' });
        response.end('<!doctype html><html><title>Fixture Native Page</title><body><h1>Native browser fixture</h1><button onclick="this.textContent=\'Clicked\';fetch(\'/clicked\',{method:\'POST\'})">Run fixture</button></body></html>');
        return;
      }
      if (request.url === '/clicked') {
        clicks++;
        response.writeHead(204).end();
        return;
      }
      if (request.url === '/favicon.ico') {
        response.writeHead(404).end();
        return;
      }
      assert.equal(request.url, '/v1/chat/completions');
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks));
      const latest = body.messages.at(-1);
      const names = new Set(body.tools.map(schema => schema.function.name));
      requests.push(latest.tool_call_id || latest.content);
      if (latest.content === first) {
        assert.equal([...names].filter(name => name.startsWith('browser_')).length, 24);
        assert.match(body.messages[0].content, /内置浏览器：开启/);
        finish(response, [tool('navigate', 'browser_navigate', { url: origin + '/page' })]);
      } else if (latest.content === disabled) {
        assert.ok(names.has('browser_tabs'), 'definitions remain stable when browser is disabled');
        finish(response, [tool('disabled', 'browser_tabs', { action: 'list' })]);
      } else if (body.messages.some(message => message.content === scheduled)) {
        assert.equal(latest.role, 'user');
        assert.match(latest.content, /不要调用 ask_user/);
        finish(response, scheduleResult);
      } else {
        assert.equal(latest.role, 'tool');
        const result = JSON.parse(latest.content);
        switch (latest.tool_call_id) {
          case 'navigate': {
            assert.equal(result.success, true, JSON.stringify(result));
            assert.match(result.snapshot.text, /Native browser fixture/);
            const target = result.snapshot.text.match(/Run fixture[^\n]*\[ref=(e\d+)\]/)?.[1];
            assert.ok(target, result.snapshot.text);
            finish(response, [tool('click', 'browser_click', { target, element: 'Run fixture' })]);
            break;
          }
          case 'click':
            assert.equal(result.success, true, JSON.stringify(result));
            finish(response, [tool('screenshot', 'browser_take_screenshot', {})]);
            break;
          case 'screenshot':
            assert.equal(result.success, true, JSON.stringify(result));
            assert.match(result.screenshot_path, /^\.astrion\/browser_screenshots\/.+\.png$/);
            assert.equal(result.image_base64, undefined);
            finish(response, [tool('write', 'write_file', { file_path: 'preview.html', content: htmlContent })]);
            break;
          case 'write':
            assert.equal(result.success, true, JSON.stringify(result));
            finish(response, `${chart}\n\n${art}\n\nPreview server: ${origin}/page\n`);
            break;
          case 'disabled':
            assert.equal(result.code, 'browser_disabled', JSON.stringify(result));
            finish(response, 'isolated browser disabled result');
            break;
          default:
            throw new Error(`Unexpected fixture step: ${JSON.stringify(latest)}`);
        }
      }
    } catch (error) {
      errors.push(error.message);
      if (!response.headersSent) response.writeHead(400);
      response.end();
    }
  });
  const port = await listenOnLoopback(server);
  origin = `http://127.0.0.1:${port}`;
  return {
    endpoint: origin + '/v1/chat/completions', origin, requests, errors,
    clicks: () => clicks, close: () => closeServer(server),
  };
}

async function renderRequest(fixture, url, method = 'GET', body, expected = 200) {
  const response = await fixture.view('/chrome', `(async () => {
    const csrf = await fetch('/api/csrf-token').then(response => response.json());
    const response = await fetch(${JSON.stringify(url)}, {
      method: ${JSON.stringify(method)},
      headers: { 'x-csrf-token': csrf.token, 'x-astrion-workspace-id': ${JSON.stringify(workspaceId)}, 'content-type': 'application/json' },
      body: ${JSON.stringify(body === undefined ? null : JSON.stringify(body))},
    });
    return { status: response.status, value: await response.json() };
  })()`);
  assert.equal(response?.status, expected, JSON.stringify(response));
  return response.value;
}

test('packaged Rust main Agent drives Electron, renders inline tags and records previews and scheduled output', {
  timeout: 100_000, skip: !app,
}, async t => {
  let dataDir;
  const fixture = await startAppRuntimeFixture(t, app, {
    model, startMock, workspaceIds: [workspaceId],
    async prepare(context) { dataDir = context.dataDir; },
  });
  await waitFor('authenticated desktop renderer', () => fixture.view('/chrome', "document.readyState === 'complete'"));
  const created = await renderRequest(fixture, '/api/conversations', 'POST', {
    settings: {
      model_key: model, run_mode: 'fast', thinking_mode: false, reasoning_effort: null, work_mode: 'execute',
      permission_mode: 'approval', execution_mode: 'sandbox', network_permission: 'restricted',
      browser_tools: true,
    },
  }, 201);
  const id = created.conversation_id;
  assert.ok(id, JSON.stringify(created));
  const scope = `?conversation_id=${encodeURIComponent(id)}`;
  assert.deepEqual(await renderRequest(fixture, '/api/browser-tools' + scope), {
    success: true, supported: true, enabled: true, pending: null,
  });

  const started = await fixture.gateway.createTask({ conversation_id: id, message: first, run_mode: 'fast' });
  const pending = async () => (await renderRequest(fixture, '/api/tool-approvals/pending' + scope)).items;
  const click = await waitFor('browser site approval', async () => {
    assert.deepEqual(fixture.mock.errors, []);
    return (await pending()).find(item => item.tool_name === 'browser_click');
  });
  assert.equal(click.approval_type, 'browser_action');
  assert.equal(click.browser_site, new URL(fixture.mock.origin).host);
  assert.equal(fixture.mock.clicks(), 0);
  const approve = row => renderRequest(fixture, '/api/tool-approvals/' + row.approval_id + '/decision',
    'POST', { decision: 'approved' });
  await approve(click);
  const completed = await waitFor('native browser and preview task success', async () => {
    assert.deepEqual(fixture.mock.errors, []);
    for (const row of await pending()) {
      assert.equal(row.tool_name, 'write_file');
      await approve(row);
    }
    const poll = await fixture.gateway.pollTask(started.task_id, 0);
    if (poll.status === 'failed') throw new Error(poll.error);
    return COMPLETED.has(poll.status) && poll;
  });
  assert.equal(fixture.mock.clicks(), 1);
  assert.ok(completed.events.some(event => event.type === 'preview_targets_updated'));
  assert.equal(await readFile(join(fixture.workspace, 'preview.html'), 'utf8'), htmlContent);
  const screenshots = join(fixture.workspace, '.astrion/browser_screenshots');
  const images = await readdir(screenshots);
  assert.equal(images.length, 1);
  assert.equal((await readFile(join(screenshots, images[0]))).subarray(1, 4).toString(), 'PNG');

  const previews = await renderRequest(fixture, '/api/preview/targets' + scope);
  assert.ok(previews.preview_targets.some(target => target.type === 'file' && target.path === 'preview.html'), JSON.stringify(previews));
  assert.ok(previews.preview_targets.some(target => target.type === 'server' && target.url === fixture.mock.origin + '/page'));
  const fileResponse = await fetch(`${previews.preview_base}/${previews.preview_token}/file/preview.html`);
  assert.equal(fileResponse.status, 200);
  assert.match(await fileResponse.text(), /Preview tool output/);

  const off = await renderRequest(fixture, '/api/browser-tools' + scope, 'POST', { enabled: false });
  assert.equal(off.enabled, false);
  assert.equal(off.release_failed, undefined);
  const after = await fixture.gateway.createTask({ conversation_id: id, message: disabled, run_mode: 'fast' });
  await trackTask(fixture.gateway, after.task_id, id).until('disabled browser task completed', poll => {
    assert.deepEqual(fixture.mock.errors, []);
    return COMPLETED.has(poll.status);
  });

  const plan = await renderRequest(fixture, '/api/schedules', 'POST', { definition: {
    schema_version: 1, id: 'native-fixture-plan', name: 'Native fixture plan', message: scheduled,
    target: { workspace_id: workspaceId },
    schedule: { type: 'daily', timezone: 'UTC', times: ['09:00'] },
    configuration: { mode: 'custom', work_mode: 'plan', model_key: model, thinking: false },
  } });
  await renderRequest(fixture, '/api/schedules/' + plan.data.definition.id + '/actions', 'POST', {
    action: 'run', request_id: 'one-off-native-fixture',
  });
  await waitFor('real scheduler records completed output', async () => {
    assert.deepEqual(fixture.mock.errors, []);
    const response = await renderRequest(fixture, '/api/schedules/' + plan.data.definition.id + '/runs');
    return response.data.some(row => row.state === 'success' && row.summary === scheduleResult);
  });

  await renderRequest(fixture, '/api/conversation-tabs/commands', 'POST', {
    command_id: 'desktop-tools-check-conversation', action: 'open', payload: {
      conversation_id: id, workspace_id: workspaceId, conversation_kind: 'agent',
    },
  });
  const path = '/' + id.replace(/^conv_/, '');
  await waitFor('both inline drawing modes rendered by the conversation view', () => fixture.view(path, `(() => {
    const main = document.querySelector('main');
    if (!main) return false;
    const inline = main.querySelector('.inline-ui__static')?.shadowRoot?.textContent ?? '';
    const html = main.querySelector('.chat-inline-html__host')?.shadowRoot?.textContent ?? '';
    const frames = Array.from(main.querySelectorAll('iframe'), frame => frame.getAttribute('srcdoc') || '').join('');
    return inline.includes('Fixture inline visualization')
      && (html.includes('Fixture HTML canvas') || frames.includes('Fixture HTML canvas'));
  })()`), 20_000);
  console.log(JSON.stringify({
    backendPid: fixture.backendPid, nativeBrowserCalls: fixture.mock.requests.length,
    approval: click.approval_type, previewTargets: previews.preview_targets.map(target => target.type),
    scheduledResult: scheduleResult, inlineUi: true, inlineHtml: true,
  }));
});
