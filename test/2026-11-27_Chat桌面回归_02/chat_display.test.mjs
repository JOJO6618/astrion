import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../../static/src/chat/display.ts', import.meta.url))],
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node'
});
const { toDisplayMessages } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].contents).toString('base64')}`
);

function projection(status, extra = {}) {
  const tools = [{
    id: 'task:10:tool',
    type: 'tool',
    toolBatchId: 'msg_round',
    tool: { id: 'call_one', name: 'update_memory', status, ...extra }
  }];
  return { id: 'msg_round', role: 'assistant', actions: tools };
}

test('Chat renders waiting and preparing even before a response row exists', () => {
  const [waiting] = toDisplayMessages([], [{
    id: 'msg_round', role: 'assistant', actions: [], awaitingFirstContent: true
  }]);
  assert.equal(waiting.id, 'msg_round');
  assert.equal(waiting.awaitingFirstContent, true);
  const [assistant] = toDisplayMessages([], [projection('preparing', { intent_full: 'keep note' })]);
  assert.equal(assistant.id, waiting.id);
  assert.equal(assistant.actions[0].tool.status, 'preparing');
  assert.equal(assistant.actions[0].tool.intent_full, 'keep note');
});

test('Chat attaches streamed text and tools to one message and preserves IDs after save', () => {
  const identities = new Map();
  const live = { message_id: 'msg_round', role: 'assistant', content: 'plain text', live: true };
  const progress = projection('preparing');
  progress.actions.unshift({
    id: 'task:5:text', type: 'text', content: 'plain text', responseMessageId: live.message_id
  });
  const inFlight = toDisplayMessages([live], [progress], identities);
  assert.equal(inFlight.length, 1);
  assert.deepEqual(inFlight[0].actions.map(({ type }) => type), ['text', 'tool']);
  const toolKey = inFlight[0].actions[1].id;
  const persisted = {
    ...live,
    live: false,
    tool_calls: [{ id: 'call_one', function: { name: 'update_memory', arguments: '{}' } }]
  };
  const running = toDisplayMessages([persisted], [projection('running')], identities);
  assert.equal(running.length, 1);
  assert.equal(running[0].id, inFlight[0].id);
  assert.equal(running[0].actions[1].id, toolKey);
  assert.equal(running[0].actions[1].tool.status, 'running');
  const result = {
    message_id: 'msg_result', role: 'tool', tool_call_id: 'call_one', content: '{"success":true}'
  };
  const finished = toDisplayMessages([persisted, result], [], identities);
  assert.equal(finished.length, 1);
  assert.equal(finished[0].actions[1].id, toolKey);
  assert.equal(finished[0].actions[1].tool.status, 'completed');
});
