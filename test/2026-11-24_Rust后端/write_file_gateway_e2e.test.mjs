import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import {
  CANCELED, COMPLETED, closeServer, createTask, listenOnLoopback, startGatewayFixture,
} from './gateway_harness.mjs';

const MODEL = 'write-file-offline-fixture';
const READ_AND_EDIT = 'Read and then edit the fixture in one tool batch.';
const WITHOUT_READ = 'Try replacing the fixture in this new conversation.';
const CANCEL_WRITE = 'Try appending to the fixture, then wait for approval.';
const FILE = 'fixture.txt';
const BEFORE = 'top\nold\nbottom';
const AFTER = 'top\n新\nbottom';
const ON_DISK_AFTER = process.platform === 'win32' ? AFTER.replaceAll('\n', '\r\n') : AFTER;

const calls = new Map([
  [READ_AND_EDIT, [
    ['read-before-edit', 'read_file', { path: FILE, type: 'read' }],
    ['exact-edit', 'edit_file', { file_path: FILE, replacements: [{ old_string: BEFORE, new_string: AFTER }] }],
  ]],
  [WITHOUT_READ, [['overwrite-without-visit', 'write_file', { file_path: FILE, content: 'unexpected' }]]],
  [CANCEL_WRITE, [['canceled-append', 'write_file', { file_path: FILE, content: 'unexpected', append: true }]]],
]);

function sendChunk(response, delta, reason = null) {
  response.write(`data: ${JSON.stringify({
    id: 'fixture-provider', object: 'chat.completion.chunk', model: MODEL,
    choices: [{ index: 0, delta, finish_reason: reason }],
  })}\n\n`);
}

async function startMock() {
  const requests = new Map();
  const errors = [];
  const server = createServer(async (request, response) => {
    try {
      assert.equal(request.method, 'POST');
      assert.equal(request.url, '/v1/chat/completions');
      assert.equal(request.headers.authorization, undefined);
      const bytes = [];
      for await (const chunk of request) bytes.push(chunk);
      const body = JSON.parse(Buffer.concat(bytes).toString('utf8'));
      const prompt = body.messages.findLast((message) => message.role === 'user')?.content;
      assert.ok(calls.has(prompt), `Unexpected prompt ${prompt}`);
      assert.equal(body.model, MODEL);
      assert.ok(body.tools.find((tool) => tool.function?.name === 'edit_file'));
      assert.ok(body.tools.find((tool) => tool.function?.name === 'write_file'));
      const previous = requests.get(prompt) ?? [];
      requests.set(prompt, [...previous, body]);
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      sendChunk(response, { role: 'assistant' });
      if (!previous.length) {
        sendChunk(response, { tool_calls: calls.get(prompt).map(([id, name, args], index) => ({
          index, id, type: 'function', function: { name, arguments: JSON.stringify(args) },
        })) });
        sendChunk(response, {}, 'tool_calls');
      } else {
        assert.equal(previous.length, 1);
        assert.notEqual(prompt, CANCEL_WRITE);
        sendChunk(response, { content: 'Fixture processed.' });
        sendChunk(response, {}, 'stop');
      }
      response.end('data: [DONE]\n\n');
    } catch (error) {
      errors.push(error);
      if (!response.headersSent) response.writeHead(500);
      response.end(error.message);
    }
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, requests, errors, close: () => closeServer(server) };
}

async function pendingApproval(task, name) {
  await task.until(`${name} to wait for approval`, () => task.events.some((event) => event.type === 'tool_approval_required'));
  const { approval, conversation_id } = task.events.find((event) => event.type === 'tool_approval_required').data;
  assert.equal(conversation_id, task.conversationId);
  assert.equal(approval.tool_name, name);
  assert.equal(approval.status, 'pending');
  return approval.approval_id;
}

function toolResults(mock, prompt) {
  const body = mock.requests.get(prompt)[1];
  return Object.fromEntries(body.messages.filter((message) => message.role === 'tool')
    .map((message) => [message.tool_call_id, JSON.parse(message.content)]));
}

test('Gateway scopes approved file mutations to the current conversation, workspace and task', { timeout: 30_000 }, async (t) => {
  const { gateway, mock, workspace, dataDir } = await startGatewayFixture(t, { model: MODEL, startMock, workspaceIds: ['default', 'other'] });
  await writeFile(join(dataDir, 'personalization.json'), JSON.stringify({ default_permission_mode: 'approval', default_work_mode: 'execute', require_read_before_edit: true }));
  const path = join(workspace, FILE);
  await writeFile(path, BEFORE);
  const begin = async (prompt) => {
    const { conversation_id: conversationId } = await gateway.createSession();
    return createTask(gateway, conversationId, prompt);
  };

  try {
    const permitted = await begin(READ_AND_EDIT);
    const editId = await pendingApproval(permitted, 'edit_file');
    assert.equal(await readFile(path, 'utf8'), BEFORE);
    gateway.workspaceId = 'other';
    try {
      await assert.rejects(gateway.decideApproval(editId, 'approved'));
    } finally {
      gateway.workspaceId = 'default';
    }
    assert.equal((await gateway.decideApproval(editId, 'approved')).status, 'approved');
    await permitted.until('approved edit result', (poll) => COMPLETED.has(poll.status));
    assert.equal(await readFile(path, 'utf8'), ON_DISK_AFTER);
    const results = toolResults(mock, READ_AND_EDIT);
    assert.equal(results['read-before-edit'].content, BEFORE);
    assert.equal(results['exact-edit'].write_performed, true);

    const noVisit = await begin(WITHOUT_READ);
    const writeId = await pendingApproval(noVisit, 'write_file');
    assert.equal((await gateway.decideApproval(writeId, 'approved')).status, 'approved');
    await noVisit.until('write result in a new conversation', (poll) => COMPLETED.has(poll.status));
    assert.equal(toolResults(mock, WITHOUT_READ)['overwrite-without-visit'].enforcement, 'read_before_edit_required');
    assert.equal(await readFile(path, 'utf8'), ON_DISK_AFTER);

    const canceled = await begin(CANCEL_WRITE);
    const canceledId = await pendingApproval(canceled, 'write_file');
    await gateway.cancelTask(canceled.taskId);
    assert.equal((await gateway.decideApproval(canceledId, 'approved')).status, 'expired');
    await canceled.until('canceled task to end', (poll) => CANCELED.has(poll.status));
    assert.equal(await readFile(path, 'utf8'), ON_DISK_AFTER);
    assert.equal(mock.requests.get(CANCEL_WRITE).length, 1);
    assert.deepEqual(mock.errors, []);
  } catch (error) {
    if (mock.errors.length) throw new AggregateError([error, ...mock.errors], mock.errors[0].message);
    throw error;
  }
});
