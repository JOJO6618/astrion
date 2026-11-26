import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import test from 'node:test';
import {
  COMPLETED, closeServer, createTask, listenOnLoopback, startGatewayFixture, streamedText,
} from './gateway_harness.mjs';

const MODEL = 'read-file-offline-model';
const PROMPT = 'Read the second line of note.txt and try to read the outside fixture.';
const ANSWER = 'The workspace read succeeded and the outside read was denied.';
const OUTSIDE_TEXT = 'synthetic text beyond the authorized workspace';
const INSIDE_TEXT = 'inside work area 🦀\n';
const CALLS = [
  ['read_inside', { path: 'note.txt', type: 'read', start_line: 2, end_line: 2 }],
  ['read_outside', { path: '../outside-fixture.txt', type: 'read' }],
];

function writeChunk(response, delta, finishReason = null) {
  response.write(`data: ${JSON.stringify({
    id: 'chatcmpl-read-file-fixture',
    object: 'chat.completion.chunk',
    model: MODEL,
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  })}\n\n`);
}

function assertToolResults(messages) {
  const [assistant, inside, outside] = messages.slice(-3);
  assert.equal(assistant.role, 'assistant');
  assert.deepEqual(assistant.tool_calls.map((call) => [
    call.id, call.function.name, JSON.parse(call.function.arguments),
  ]), CALLS.map(([id, args]) => [id, 'read_file', args]));
  assert.equal(inside.role, 'tool');
  assert.equal(inside.tool_call_id, CALLS[0][0]);
  assert.equal(outside.role, 'tool');
  assert.equal(outside.tool_call_id, CALLS[1][0]);

  const readable = JSON.parse(inside.content);
  assert.equal(readable.success, true);
  assert.equal(readable.path, 'note.txt');
  assert.equal(readable.content, INSIDE_TEXT);
  assert.equal(readable.line_start, 2);
  assert.equal(readable.line_end, 2);
  assert.equal(readable.total_lines, 3);
  const denied = JSON.parse(outside.content);
  assert.equal(denied.success, false);
  assert.equal(typeof denied.error, 'string');
  assert.ok(!Object.hasOwn(denied, 'content'));
  assert.ok(!JSON.stringify(messages).includes(OUTSIDE_TEXT));
}

async function startMock() {
  const errors = [];
  const requests = [];
  const server = createServer(async (request, response) => {
    try {
      assert.equal(request.method, 'POST');
      assert.equal(request.url, '/v1/chat/completions');
      assert.equal(request.headers.authorization, undefined);
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const index = requests.push(body);
      assert.equal(body.model, MODEL);
      assert.equal(body.stream, true);
      assert.ok(body.messages.some((message) => message.role === 'user' && message.content === PROMPT));
      const tool = body.tools?.find((tool) => tool.function?.name === 'read_file');
      assert.equal(tool?.type, 'function');
      assert.deepEqual(tool.function.parameters.required, ['path', 'type']);
      assert.deepEqual(tool.function.parameters.properties.type.enum, ['read', 'search', 'extract']);
      if (index === 2) assertToolResults(body.messages);
      else assert.equal(index, 1, 'only one tool round should be needed');

      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      writeChunk(response, { role: 'assistant' });
      if (index === 1) {
        writeChunk(response, { tool_calls: CALLS.map(([id, args], i) => ({
          index: i, id, type: 'function', function: { name: 'read_file', arguments: JSON.stringify(args) },
        })) });
        writeChunk(response, {}, 'tool_calls');
      } else {
        writeChunk(response, { content: ANSWER });
        writeChunk(response, {}, 'stop');
      }
      response.end('data: [DONE]\n\n');
    } catch (error) {
      errors.push(error);
      if (!response.headersSent) response.writeHead(500, { 'Content-Type': 'text/plain' });
      response.end(error.message);
    }
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, requests, errors, close: () => closeServer(server) };
}

test('Gateway executes a real workspace read and reports a traversal denial to the model', { timeout: 30_000 }, async (t) => {
  const { gateway, mock, workspace } = await startGatewayFixture(t, { model: MODEL, startMock });
  await writeFile(join(workspace, 'note.txt'), `first line\n${INSIDE_TEXT}last line`);
  await writeFile(join(dirname(workspace), 'outside-fixture.txt'), OUTSIDE_TEXT);
  try {
    const { conversation_id: conversationId } = await gateway.createSession();
    const task = await createTask(gateway, conversationId, PROMPT);
    await task.until('model response after both read_file calls', (poll) => COMPLETED.has(poll.status));
    assert.equal(streamedText(task.events), ANSWER);
    assert.equal(mock.requests.length, 2);
    assert.deepEqual(task.events.filter((event) => event.type === 'tool_start').map((event) => [
      event.data.id, event.data.name,
    ]), CALLS.map(([id]) => [id, 'read_file']));
    assert.ok(!JSON.stringify(task.events).includes(OUTSIDE_TEXT));
    assert.deepEqual(mock.errors, []);
  } catch (error) {
    if (mock.errors.length) throw new AggregateError([error, ...mock.errors], mock.errors[0].message);
    throw error;
  }
});
