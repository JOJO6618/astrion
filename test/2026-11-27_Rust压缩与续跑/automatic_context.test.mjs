import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { startGatewayFixture, listenOnLoopback, closeServer, createTask } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const summary = 'Fixture compressed notes.';
async function startMock() {
  const requests = [];
  const steps = [];
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks));
    const summarizing = body.messages.at(-1).content?.includes?.('对用户最新输入的响应进度') ?? false;
    requests.push({ body, summarizing });
    const step = steps.shift();
    if (!step) {
      res.writeHead(400).end('unexpected fixture request');
      return;
    }
    const [input, cached = 0] = Array.isArray(step) ? step : [step];
    const send = (choices, usage) => res.write(`data: ${JSON.stringify({ id: 'fixture', model: body.model, object: 'chat.completion.chunk', choices, usage })}\n\n`);
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    send([{ index: 0, delta: { content: summarizing ? summary : 'Fixture reply.' }, finish_reason: null }]);
    send([{ index: 0, delta: {}, finish_reason: 'stop' }]);
    send([], { prompt_tokens: input, completion_tokens: 5, total_tokens: input + 5, prompt_tokens_details: { cached_tokens: cached } });
    res.end('data: [DONE]\n\n');
  });
  const port = await listenOnLoopback(server);
  return { endpoint: `http://127.0.0.1:${port}/v1`, requests, steps, close: () => closeServer(server) };
}

test('Agent measures the prefix, preflights long saved history, and measures again after compression or a model change', { timeout: 30_000 }, async (t) => {
  const setup = async (dataDir, adaptive) => writeFile(join(dataDir, 'personalization.json'), JSON.stringify({
    auto_generate_title: false,
    auto_deep_compress_enabled: true,
    adaptive_compress_enabled: adaptive,
    deep_compress_trigger_tokens: 1000,
  }));
  const { gateway, mock, dataDir } = await startGatewayFixture(t, {
    startMock,
    async prepare({ dataDir, mock }) {
      const config = join(dataDir, 'config');
      await mkdir(config, { recursive: true });
      await writeFile(join(config, 'custom_models.json'), JSON.stringify(['fixture-a', 'fixture-b'].map(name => ({
        model_name: name, model_id: name, url: mock.endpoint,
        apikey: 'isolated-fixture-key', reasoning_capability: 'fast'
      }))));
      await setup(dataDir, true);
    },
  });
  let id;
  async function run(message, steps, model = 'fixture-a') {
    const start = mock.requests.length;
    mock.steps.push(...steps);
    const task = await createTask(gateway, id, message, { model_key: model });
    id = task.conversationId;
    await task.until(message, row => row.status === 'succeeded');
    assert.equal(mock.steps.length, 0);
    return { requests: mock.requests.slice(start), events: task.events };
  }
  const baselines = events => events.filter(e => e.type === 'token_update').map(e => e.data.context_baseline_tokens);
  const lastUsage = ({events}) => events.findLast(e => e.type === 'token_update').data;

  const first = await run('first input', [800]);
  assert.deepEqual(first.requests.map(r => r.summarizing), [false]);
  assert.deepEqual(baselines(first.events), [800]);
  assert.equal(lastUsage(first).cache_exempt_input_tokens, 800);
  assert.equal(lastUsage(first).cumulative_total_tokens, 805);
  const second = await run('second input', [[1500, 500]]);
  assert.deepEqual(baselines(second.events), [800]);
  assert.equal(lastUsage(second).cache_exempt_input_tokens, 800);
  // Stored occupancy 1500 exceeds 1000, but not the effective threshold 1800.
  assert.deepEqual((await run('third input', [2100])).requests.map(r => r.summarizing), [false]);
  const preflight = await run('new input must be summarized before an ordinary request', [2300, 600]);
  assert.deepEqual(preflight.requests.map(r => r.summarizing), [true, false]);
  assert.match(preflight.requests[0].body.messages.at(-1).content, /new input must be summarized/);
  assert.ok(preflight.requests[1].body.messages.some(m => m.content?.includes?.(summary)));
  assert.ok(!preflight.requests[1].body.messages.some(m => m.content === 'first input'));
  assert.deepEqual(baselines(preflight.events), [800, 600], 'summary usage is not a fresh baseline');
  assert.equal(lastUsage(preflight).cache_exempt_input_tokens, 1400, 'the post-compaction miss is excluded exactly once');
  assert.equal((await gateway.getTokenStats(id)).cache_exempt_input_tokens, 1400, 'refresh reads the same denominator');
  assert.deepEqual(preflight.events.filter(e => e.type === 'compression_state').map(e => [e.data.stage, e.data.mode]), [
    ['queued', 'auto'], ['generating_summary', 'auto'],
  ]);

  const switched = await run('switch model', [650], 'fixture-b');
  assert.equal(switched.requests[0].body.model, 'fixture-b');
  assert.deepEqual(baselines(switched.events), [650]);
  await run('build context on model B', [1200], 'fixture-b');
  await setup(dataDir, false);
  // Disabling adaptive ignores the saved model-B baseline immediately.
  const warm = await run('bare threshold', [1300, [500, 200]], 'fixture-b');
  assert.deepEqual(warm.requests.map(r => r.summarizing), [true, false]);
  assert.equal(lastUsage(warm).cache_exempt_input_tokens, 1400, 'a warm post-compaction request enters the denominator');
  const next = await run('normal miss after warm request', [600], 'fixture-b');
  assert.equal(lastUsage(next).cache_exempt_input_tokens, 1400, 'a warm request consumes the cold-start flag too');
});
