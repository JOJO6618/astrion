import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { closeServer, COMPLETED, createTask, listenOnLoopback } from './gateway_harness.mjs';

const baseline = fileURLToPath(new URL('./with_legacy_source.py', import.meta.url));
const fixture = fileURLToPath(new URL('./cli_python_fixture.py', import.meta.url));
const previous = ['From Python', 'Preserved Python response'];
const next = ['Continue in Rust', 'Reply written by Rust'];

function python(...args) {
  const output = execFileSync(process.env.ASTRION_TEST_PYTHON || 'python3.11', ['-B', baseline, fixture, ...args], {
    encoding: 'utf8', timeout: 15_000, env: { PATH: process.env.PATH, ...(process.platform === 'win32' && { SystemRoot: process.env.SystemRoot }) }
  });
  return JSON.parse(output.trim().split('\n').at(-1));
}

export async function pythonFixture(t, root, workspace) {
  const { id, path } = python('seed', root, workspace);
  const before = JSON.parse(await readFile(path, 'utf8'));
  const mock = createServer(async (request, response) => {
    try {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const { messages } = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      assert.equal(messages[0].role, 'system');
      assert.equal(messages[0].content, before.metadata.frozen_system_prompt);
      assert.deepEqual(messages.filter(message => ['user', 'assistant'].includes(message.role)).map(message => message.content), [...previous, next[0]]);
      response.writeHead(200, { 'Content-Type': 'text/event-stream' });
      const chunk = (delta, finish_reason = null) => ({ choices: [{ index: 0, delta, finish_reason }] });
      response.end(`data: ${JSON.stringify(chunk({ role: 'assistant', content: next[1] }))}\n\ndata: ${JSON.stringify(chunk({}, 'stop'))}\n\ndata: [DONE]\n\n`);
    } catch (error) {
      response.writeHead(400).end(String(error));
    }
  });
  const port = await listenOnLoopback(mock);
  t.after(() => closeServer(mock));
  return {
    id, path, endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, model: 'offline-model',
    async append(gateway) {
      gateway.workspaceId = 'python-workspace';
      const before = await gateway.getSessionHistory(id);
      assert.deepEqual(before.messages.map(message => message.content), previous);
      const task = await createTask(gateway, id, next[0]);
      const final = await task.until('the Rust turn to finish', poll => COMPLETED.has(poll.status) || poll.status === 'failed');
      assert.ok(COMPLETED.has(final.status), final.error || JSON.stringify(task.events));
    },
    async verify(gateway) {
      gateway.workspaceId = 'python-workspace';
      const document = await gateway.getSessionHistory(id);
      assert.deepEqual(document.messages.map(message => message.content), [...previous, ...next]);
    }
  };
}
