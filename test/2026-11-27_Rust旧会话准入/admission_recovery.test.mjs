import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import { migrate, readDocument } from '../2026-11-27_Rust对话格式迁移/fixture.mjs';
import { COMPLETED, closeServer, listenOnLoopback, startGatewayFixture, trackTask } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const model = 'isolated-legacy-recovery';
const recovered = 'conv_recovered_old_agent';
const unpaired = 'conv_unpaired_old_agent';
const missing = 'conv_missing_old_compact';
const compact = '.astrion/compact_result/compact_20261126_190000_001.md';
const toolCall = { id: 'legacy-call', type: 'function', function: { name: 'read_file', arguments: '{"path":"fixture.txt"}' } };
const toolResult = { role: 'tool', tool_call_id: 'legacy-call', content: 'original tool result', metadata: { future_tool_field: 'retained' } };

async function startMock() {
  const calls = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const bytes of request) chunks.push(bytes);
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    calls.push(body.messages);
    response.writeHead(200, { 'content-type': 'text/event-stream' });
    for (const [delta, finish_reason] of [[{ role: 'assistant', content: 'resumed once' }, null], [{}, 'stop']]) {
      response.write(`data: ${JSON.stringify({ id: 'legacy_recovery', model, object: 'chat.completion.chunk', choices: [{ index: 0, delta, finish_reason }] })}\n\n`);
    }
    response.end('data: [DONE]\n\n');
  });
  const port = await listenOnLoopback(server);
  return { calls, endpoint: `http://127.0.0.1:${port}/v1/chat/completions`, close: () => closeServer(server) };
}

test('explicit migration preserves old bytes and an idle Agent applies safe recovery on its next admitted turn', { timeout: 40_000 }, async (t) => {
  const originals = new Map();
  const fixture = await startGatewayFixture(t, { model, startMock, web: true, async prepare({ workspace, dataDir }) {
    const directory = join(dataDir, 'conversations', 'default');
    await mkdir(directory, { recursive: true });
    await mkdir(join(workspace, '.astrion', 'compact_result'), { recursive: true });
    await writeFile(join(workspace, compact), '# 对话已被第1次压缩\n\n## 历次压缩总结\n原始压缩材料。\n');
    for (const id of [recovered, unpaired, missing]) {
      const hasCompaction = id !== unpaired;
      const path = id === missing ? compact.replace('001.md', '999.md') : compact;
      const record = { count: 1, compact_file: path, source_conversation_id: id, compressed_conversation_id: id, summary: 'old compact summary' };
      const raw = {
        id, title: 'Python 原始会话', updated_at: '2026-11-26T19:00:00+00:00', active_task_id: 'task_legacy_idle',
        metadata: {
          project_path: workspace, frozen_system_prompt: 'Old frozen Agent prompt.', status: 'active',
          work_mode: 'execute', permission_mode: 'approval', execution_mode: 'sandbox', network_permission: 'restricted',
          pending_permission_mode: 'readonly', pending_execution_mode: 'direct', pending_network_permission: 'none',
          active_goal_id: 'goal_old_marker', active_workflow_id: 'workflow_old_marker',
          compression_in_progress: true, compression_stage: 'generating_summary', compression_mode: 'requested',
          compression_job_id: 'cmp_old_job', compression_pid: 99_999,
          compression_resume_payload: { conversation_id: id, mode: 'requested' }, future_metadata: { retained: true },
          ...(hasCompaction && { compression_count: 1, last_deep_compression_record: record, deep_compression_records: [record] }),
        },
        messages: [
          ...(hasCompaction ? [{ role: 'user', content: 'already compacted user input', metadata: { deep_compacted: true } },
            { role: 'user', content: `压缩记录文件：${path}\n本次压缩摘要：old compact summary`, metadata: { message_source: 'compression_handoff' } }] : []),
          { role: 'assistant', content: null, tool_calls: [toolCall] },
          ...(id === unpaired ? [] : [toolResult]),
        ],
      };
      const bytes = JSON.stringify(raw, null, 2) + '\n';
      originals.set(id, bytes);
      await writeFile(join(directory, `${id}.json`), bytes);
    }
  } });
  const { gateway, dataDir, mock } = fixture;
  const headers = { authorization: `Bearer ${await gateway.readToken()}`, 'x-astrion-workspace-id': 'default', 'content-type': 'application/json' };
  const submit = id => fetch(gateway.baseUrl + '/api/tasks', {
    method: 'POST', headers, body: JSON.stringify({ conversation_id: id, message: 'continue from the old Agent', model_key: model }),
  });
  assert.equal((await submit(recovered)).status, 409, 'ordinary admission requires explicit migration');
  const origin = gateway.baseUrl;
  const challenge = await fetch(origin + '/api/csrf-token');
  let cookie = challenge.headers.get('set-cookie').split(';', 1)[0];
  const login = await fetch(origin + '/host-login', { method: 'POST', headers: {
    cookie, origin, 'content-type': 'application/json', 'x-csrf-token': (await challenge.json()).token,
  }, body: '{}' });
  assert.equal(login.status, 200);
  cookie = login.headers.get('set-cookie').split(';', 1)[0];
  const csrf = await fetch(origin + '/api/csrf-token', { headers: { cookie } }).then(result => result.json());
  const report = await migrate(origin, { cookie, origin, 'x-csrf-token': csrf.token });
  assert.equal(report.migrated, 3);
  const directory = join(dataDir, 'conversations', 'default');
  for (const [id, bytes] of originals) {
    assert.equal(await readFile(join(directory, `${id}.json`), 'utf8'), bytes);
    assert.deepEqual(readDocument(directory, id), JSON.parse(bytes), 'migration preserves fields before admission');
  }

  for (const id of [unpaired, missing]) {
    const response = await submit(id);
    assert.equal(response.status, 422, await response.text());
    assert.deepEqual(readDocument(directory, id), JSON.parse(originals.get(id)), 'a refused admission does not change the saved document');
  }
  const start = await submit(recovered);
  assert.equal(start.status, 202);
  const { task_id } = (await start.json()).data;
  await trackTask(gateway, task_id, recovered).until('the recovered task completes', status => COMPLETED.has(status.status));
  assert.equal(mock.calls.length, 1, 'old compression and workflow markers never trigger new work');
  const projected = mock.calls[0];
  assert(projected.some(message => message.role === 'tool' && message.tool_call_id === toolCall.id && message.content === toolResult.content));
  assert(!projected.some(message => message.content === 'already compacted user input'));
  const final = readDocument(directory, recovered);
  assert.equal(final.metadata.permission_mode, 'readonly');
  assert.equal(final.metadata.execution_mode, 'sandbox');
  if (process.platform === 'darwin') assert.equal(final.metadata.network_permission, 'none');
  assert.equal(final.metadata.compression_in_progress, false);
  assert.equal(final.metadata.compression_job_id, null);
  assert.equal(final.metadata.active_goal_id, null);
  assert.equal(final.metadata.active_workflow_id, null);
  assert.equal(final.active_task_id, null);
  assert.deepEqual(final.metadata.future_metadata, { retained: true });
  assert.deepEqual(final.messages.slice(0, 4), JSON.parse(originals.get(recovered)).messages);
  assert.equal(await readFile(join(directory, `${recovered}.json`), 'utf8'), originals.get(recovered));
});
