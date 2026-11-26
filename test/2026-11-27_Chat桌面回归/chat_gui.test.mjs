import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import { closeServer, listenOnLoopback, waitFor } from '../2026-11-24_Rust后端/gateway_harness.mjs';
import { startAppRuntimeFixture } from '../2026-11-26_Rust提问计划审批/app_runtime_fixture.mjs';

const app = process.env.ASTRION_TEST_APP;
const model = 'isolated-chat-vision-model';
const workspace = 'chat-shell-fixture';
const first = 'The original Chat branch';
const edited = 'The edited Chat branch';
const answer = 'Original Chat reply';
const fork = 'Edited Chat reply';
const generatedTitle = 'Generated desktop Chat title';
const imageReply = 'Chat image received';
const videoReply = 'Chat video received';
const mp4 = (await readFile(new URL('./fixture.mp4', import.meta.url))).toString('base64');
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGOsceBjYGBgYgADAAoQAM74E/+sAAAAAElFTkSuQmCC';

async function startMock() {
  const requests = [];
  const titleRequests = [];
  const errors = [];
  const titleGate = Promise.withResolvers();
  const server = createServer(async (request, response) => {
    try {
      assert.equal(request.url, '/v1/chat/completions');
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks));
      assert.equal(body.model, model);
      const latest = body.messages.at(-1);
      assert.equal(latest.role, 'user');
      let reply;
      if (typeof latest.content === 'string' && latest.content.startsWith('请为这个对话首条消息起标题:')) {
        assert.ok(latest.content.includes(first));
        assert.equal(body.tools?.length || 0, 0);
        titleRequests.push(latest.content);
        await titleGate.promise;
        reply = generatedTitle;
      } else if (latest.content === first) {
        reply = answer;
      } else if (latest.content === edited) {
        assert.ok(!body.messages.some(message => [first, answer].includes(message.content)));
        reply = fork;
      } else if (latest.content.some(part => part.type === 'image_url')) {
        assert.equal(latest.content.find(part => part.type === 'image_url').image_url.url, 'data:image/png;base64,' + png);
        assert.ok(body.messages.some(message => message.content === fork));
        reply = imageReply;
      } else {
        assert.equal(latest.content.find(part => part.type === 'video_url')?.video_url.url, 'data:video/mp4;base64,' + mp4);
        assert.ok(body.messages.some(message => message.content === imageReply));
        reply = videoReply;
      }
      if (reply !== generatedTitle) requests.push(latest.content);
      response.writeHead(200, { 'content-type': 'text/event-stream' });
      const frames = [reply.slice(0, 7), reply.slice(7)].map(content => ({ delta: { content }, finish_reason: null }));
      frames.push({ delta: {}, finish_reason: 'stop' });
      for (const chunk of frames) {
        response.write(`data: ${JSON.stringify({ id: 'chat-fixture', model, choices: [{ index: 0, ...chunk }] })}\n\n`);
      }
      response.end('data: [DONE]\n\n');
    } catch (error) {
      errors.push(error.message);
      if (!response.headersSent) response.writeHead(400);
      response.end();
    }
  });
  const port = await listenOnLoopback(server);
  return {
    endpoint: `http://127.0.0.1:${port}/v1/chat/completions`,
    requests, titleRequests, errors, releaseTitle: titleGate.resolve,
    close: () => {
      titleGate.resolve();
      return closeServer(server);
    },
  };
}

function waitView(fixture, path, description, script) {
  return waitFor(description, async () => {
    assert.deepEqual(fixture.mock.errors, []);
    return fixture.view(path, script);
  }, 20_000);
}

function replyVisible(initial, previous = '') {
  return `(() => {
    const main = document.querySelector('main');
    const text = main?.innerText || '';
    return text.includes(${JSON.stringify(initial)}) &&
      (!${JSON.stringify(previous)} || !text.includes(${JSON.stringify(previous)})) &&
      !document.querySelector('.send-btn.is-stopping');
  })()`;
}

async function startFixture(t, automaticTitle = false) {
  const fixture = await startAppRuntimeFixture(t, app, {
    model, startMock, workspaceIds: [workspace], modelOptions: { multimodal: 'image,video' },
    backendOverride: process.env.ASTRION_TEST_BACKEND_BIN,
    async prepare({ dataDir }) {
      await writeFile(join(dataDir, 'personalization.json'), JSON.stringify({
        chat_default_model: model, chat_default_thinking_mode: false,
        auto_generate_title: automaticTitle, chat_network_enabled: false,
      }));
    },
  });
  await waitView(fixture, '/chrome', 'desktop navigation renderer', "document.readyState === 'complete'");
  const created = await fixture.view('/chrome', `(async () => {
    const { token } = await fetch('/api/csrf-token').then(response => response.json());
    const result = await fetch('/api/conversation-tabs/commands', {
      method: 'POST', headers: { 'x-csrf-token': token, 'content-type': 'application/json' },
      body: JSON.stringify({ command_id: 'chat-gui-new-tab', action: 'create',
        payload: { key: 'tab_chat_gui', conversation_kind: 'chat', workspace_id: '' } }),
    });
    return { status: result.status, body: await result.json() };
  })()`);
  assert.equal(created.status, 200, JSON.stringify(created));
  return fixture;
}

async function clickSend(fixture, path) {
  await waitView(fixture, path, 'enabled Chat send button', `(() => {
    const send = document.querySelector('.send-btn');
    if (!send || send.disabled || send.classList.contains('is-stopping')) return false;
    send.click();
    return true;
  })()`);
}

async function sendText(fixture, path, value) {
  await waitView(fixture, path, 'Chat editor accepts typed text', `(() => {
    const composer = document.querySelector('.stadium-input-rich [contenteditable="true"]');
    if (!composer) return false;
    composer.focus();
    return document.execCommand('insertText', false, ${JSON.stringify(value)});
  })()`);
  await clickSend(fixture, path);
}

async function activeConversation(fixture) {
  try {
    return await waitView(fixture, '/chat/', 'Chat route opens the conversation after acceptance', `(() => {
      if (location.pathname === '/chat/new') return false;
      return location.pathname.startsWith('/chat/') && location.pathname;
    })()`);
  } catch (error) {
    throw new Error(`${error.message}: ${JSON.stringify(await fixture.diagnostics())}`);
  }
}

test('real Chat composer creates a late title, edits a branch, and reopens the latest path', {
  timeout: 90_000, skip: !app,
}, async t => {
  const fixture = await startFixture(t, true);
  await sendText(fixture, '/chat/new', first);
  const path = await activeConversation(fixture);
  await waitView(fixture, path, 'first Chat reply rendered', replyVisible(answer));
  await waitFor('auxiliary SSE after the first Chat message', () => fixture.mock.titleRequests.length === 1);
  fixture.mock.releaseTitle();
  await waitView(fixture, '/chrome', 'generated Chat title in the desktop tab',
    `document.querySelector('[role="tab"][aria-selected="true"] .tab-title')?.textContent?.trim()
      === ${JSON.stringify(generatedTitle)}`);
  await waitView(fixture, path, 'first Chat user editor opens', `(() => {
    const row = [...document.querySelectorAll('.user-message')].find(node => node.innerText.includes(${JSON.stringify(first)}));
    const button = row?.querySelector('button[aria-label="编辑"],button[aria-label="Edit"]')
      || document.querySelector('.user-message button[aria-label="编辑"],.user-message button[aria-label="Edit"]');
    if (!button) return false;
    button.click();
    return true;
  })()`);
  await waitView(fixture, path, 'Chat editor resends changed text', `(() => {
    const input = document.querySelector('.chat-message-editor__input');
    const send = [...document.querySelectorAll('.chat-message-editor__actions button')].at(-1);
    if (!input || !send || send.disabled) return false;
    input.value = ${JSON.stringify(edited)};
    input.dispatchEvent(new Event('input', { bubbles: true }));
    send.click();
    return true;
  })()`);
  await waitView(fixture, path, 'edited branch reply rendered', replyVisible(fork, answer));
  await waitView(fixture, path, 'previous Chat branch selected', `(() => {
    const button = document.querySelector('button[aria-label="上一个版本"],button[aria-label="Previous version"]');
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`);
  await waitView(fixture, path, 'old Chat reply can still be viewed', replyVisible(answer, fork));
  const pids = await fixture.restart();
  await waitView(fixture, path, 'reopened Chat shows the latest edited branch', replyVisible(fork, answer));
  await waitView(fixture, '/chrome', 'reopened tab displays the durable title',
    `document.querySelector('[role="tab"][aria-selected="true"] .tab-title')?.textContent?.trim()
      === ${JSON.stringify(generatedTitle)}`);
  assert.deepEqual(fixture.mock.requests, [first, edited]);
  assert.equal(fixture.mock.titleRequests.length, 1);
  console.log(JSON.stringify({ pids, firstReply: answer, latestAfterRestart: fork, generatedTitle, requests: 2 }));
});

async function upload(fixture, path, media) {
  const response = await fixture.view(path, `(async () => {
    const input = ${JSON.stringify(media)};
    const csrf = await fetch('/api/csrf-token').then(response => response.json());
    const form = new FormData();
    const content = Uint8Array.from(atob(input.raw), ch => ch.charCodeAt(0));
    form.append('media', new File([content], input.name, { type: input.mime }));
    const uploaded = await fetch('/api/chat/media', {
      method: 'POST', headers: { 'x-csrf-token': csrf.token }, body: form,
    });
    const media = await uploaded.json();
    if (!uploaded.ok) return media;
    const id = 'conv_' + location.pathname.split('/').at(-1).replace(/^conv_/, '');
    const opened = await fetch('/api/chat/conversations/' + id).then(response => response.json());
    const document = opened.data?.document;
    if (!document) return opened;
    const sent = await fetch('/api/chat/tasks', {
      method: 'POST',
      headers: { 'x-csrf-token': csrf.token, 'content-type': 'application/json' },
      body: JSON.stringify({ operation_id: crypto.randomUUID(), conversation_id: id,
        parent_id: document.metadata.chat.latest_leaf_id, model_key: ${JSON.stringify(model)},
        thinking_mode: false, message: '',
        images: media.data.kind === 'image' ? [media.data] : [],
        videos: media.data.kind === 'video' ? [media.data] : [] }),
    });
    return { status: sent.status, body: await sent.json(), id };
  })()`);
  assert.ok(response.status === 200 || response.status === 202, JSON.stringify(response));
  await waitView(fixture, path, 'Chat document stores the uploaded reference with no inline bytes', `(async () => {
    const id = ${JSON.stringify(response.id)};
    const response = await fetch('/api/chat/conversations/' + id).then(response => response.json());
    const document = response.data?.document;
    return document?.messages.some(message => message.content === ${JSON.stringify(media.reply)}) &&
      document.messages.some(message => message.media_refs?.[0]?.mime_type === ${JSON.stringify(media.mime)} && message.content === '');
  })()`);
}

test('Chat renderer sends photo-only and video-only messages using account media references', {
  timeout: 90_000, skip: !app,
}, async t => {
  const fixture = await startFixture(t);
  await sendText(fixture, '/chat/new', edited);
  const path = await activeConversation(fixture);
  await waitView(fixture, path, 'Chat reply before upload', replyVisible(fork));
  await upload(fixture, path, { raw: png, mime: 'image/png', name: 'fixture.png', reply: imageReply });
  await upload(fixture, path, { raw: mp4, mime: 'video/mp4', name: 'fixture.mp4', reply: videoReply });
  assert.equal(fixture.mock.requests.length, 3);
  console.log(JSON.stringify({ pid: fixture.backendPid, imageOnly: true, videoOnly: true, protocols: ['image_url', 'video_url'] }));
});
