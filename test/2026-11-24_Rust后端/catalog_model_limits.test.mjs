import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { test } from 'node:test';
import { modelContextCapacity, modelInputCapacity, readModelLimits } from '../../static/src/utils/modelLimits.ts';
import { startGatewayFixture } from './gateway_harness.mjs';

const fixture = JSON.parse(await readFile(new URL('./catalog_model_limits.json', import.meta.url), 'utf8'));
const fields = ['context_window', 'default_context_window', 'max_context_window', 'max_input_tokens', 'max_output_tokens'];

async function store(directory, name, value) {
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, name), JSON.stringify(value));
}

test('Rust model API supplies the total context and the frontend applies the distinct caps', async t => {
  const app = await startGatewayFixture(t, {
    model: 'unused',
    apiKey: 'fixture-test-key',
    startMock: async () => ({ endpoint: 'http://127.0.0.1:1/v1/chat/completions', close() {} }),
  });
  const source = join(app.workspace, 'config');
  await store(source, 'providers_catalog.json', fixture.definitions);
  await store(app.dataDir, 'providers.json', { version: 2, providers: { fixture: {
    catalog_id: 'fixture-provider',
    base_name: 'Fixture Provider',
    name: 'Fixture Provider',
    base_url: 'https://provider.invalid/v1',
    api_key: 'secret-provider-key',
    models: fixture.stored_models,
  } } });
  await store(app.dataDir, 'modelsdev_cache.json', fixture.cache);
  await store(app.dataDir, 'codex_auth.json', { tokens: { access_token: 'secret-codex-key', refresh_token: 'secret-codex-refresh' } });
  await store(app.dataDir, 'codex_models.json', fixture.codex);

  const response = await fetch(`${app.gateway.baseUrl}/api/v1/models`);
  assert.equal(response.status, 200);
  const directory = await response.json();
  assert.equal(directory.success, true);
  assert.doesNotMatch(JSON.stringify(directory), /secret-|provider\.invalid/);
  const items = Object.fromEntries(directory.items.map(item => [item.model_key, item]));
  assert.deepEqual(Object.fromEntries(Object.entries(items).map(([key, item]) => [key, fields.map(field => item[field])])), fixture.expected);

  const expanded = readModelLimits(items['fixture/expanded']);
  assert.equal(expanded.contextWindow, 1000000);
  assert.equal(expanded.defaultContextWindow, 128000);
  assert.equal(expanded.maxOutputTokens, 64000);
  assert.equal(modelContextCapacity(expanded), 1000000);
  assert.equal(modelInputCapacity(expanded), 900000);
  const registry = readModelLimits(items['fixture/gpt-5.4']);
  assert.equal(modelContextCapacity(registry), 1050000);
  assert.equal(modelInputCapacity(registry), 922000);
  const partial = readModelLimits(items['fixture/input-output-only']);
  assert.equal(modelContextCapacity(partial), null);
  assert.equal(modelInputCapacity(partial), 200000);
  assert.equal(partial.maxOutputTokens, 32000);
  const codex = readModelLimits(items['openai-codex/gpt-5.6-sol']);
  assert.equal(modelContextCapacity(codex), 872000);
  assert.equal(modelInputCapacity(codex), 872000);
  const legacyCodex = readModelLimits(items['openai-codex/legacy-codex']);
  assert.equal(modelContextCapacity(legacyCodex), null);
  assert.equal(modelInputCapacity(legacyCodex), 200000);
  assert.equal(modelContextCapacity(readModelLimits({ context_window: 65536 })), 65536);

  await store(app.dataDir, 'modelsdev_cache.json', { version: 2, providers: { openai: { models: {
    'gpt-5.4': { context: 1050000, max_output: 128000 },
  } } } });
  const legacy = await (await fetch(`${app.gateway.baseUrl}/api/v1/models`)).json();
  const entry = legacy.items.find(item => item.model_key === 'fixture/gpt-5.4');
  assert.deepEqual(fields.map(field => entry[field]), [1050000, null, 1050000, null, 128000]);
  assert.equal(modelInputCapacity(readModelLimits(entry)), 1050000);
});
