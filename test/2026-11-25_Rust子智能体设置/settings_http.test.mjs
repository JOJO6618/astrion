import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { startGatewayFixture } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const SETTINGS = '/api/sub_agents/settings';
const MODELS = '/api/sub_agents/models';
const PERSONAL = '/api/personalization';
const CHAT = '/api/chat/settings';
const THRESHOLD = 'sub_agent_compress_threshold_tokens';
const CAPACITY = 'sub_agent_max_instances';
const DEFAULTS = { [THRESHOLD]: 250_000, [CAPACITY]: 20 };
const PRIVATE = { revision: 3, content: ['保留', null] };
const SAVED = { [THRESHOLD]: 310_000, [CAPACITY]: 21 };
const SEED = { ui_locale: 'en-US', hidden_models: ['custom-mixed'], sub_agent_max_active: 2, private_extension: PRIVATE };
const STRUCTURE_ERROR = 'settings必须为对象。';
const INTEGER_ERROR = '子智能体压缩阈值必须是不小于10000的整数。';
const CAPACITY_ERROR = '允许同时存在的子智能体数量必须是大于等于1的有效整数。';

const custom = (key, options = {}) => ({
  model_name: key, model_id: 'private-upstream-id', url: 'https://private-upstream.invalid/v1',
  apikey: 'private-custom-model-key', ...options,
});
const customModels = [
  custom('custom-mixed', {
    display_name: '混合模型', description: '可处理图片和视频', reasoning_capability: 'fast, thinking',
    multimodal: 'Video,Image', context_window: '65536', max_output_tokens: 4096,
  }),
  custom('custom-hidden', { visible: false }),
  custom('custom-fast'),
];
const codexModel = {
  slug: 'fixture-luna', visibility: 'list', supported_in_api: true, display_name: 'Fixture Luna',
  description: 'Subscription fixture', input_modalities: ['image'], context_window: 128_000,
  max_context_window: 272_000, supported_reasoning_levels: [{ effort: 'low', description: 'Fast' }],
  default_reasoning_level: 'low',
};
const baseModel = {
  description: '', provider_type: '', provider_id: null, provider_name: null, api_protocol: null,
  supports_thinking: false, fast_only: true, thinking_only: false, multimodal: 'none',
  context_window: null, max_output: null, supports_reasoning_effort: false,
  supported_reasoning_levels: [], default_reasoning_level: null,
};
const expectedModels = [
  { ...baseModel, key: 'custom-mixed', name: '混合模型', description: '可处理图片和视频',
    supports_thinking: true, fast_only: false, multimodal: 'image,video',
    context_window: 65_536, max_output: 4096, supports_reasoning_effort: true },
  { ...baseModel, key: 'custom-fast', name: 'custom-fast' },
  { ...baseModel, key: 'openai-codex/fixture-luna', name: 'Fixture Luna', description: 'Subscription fixture',
    provider_type: 'provider', provider_id: 'openai-codex', provider_name: 'OpenAI（ChatGPT 订阅）-779335',
    api_protocol: 'responses', supports_thinking: true, fast_only: false, thinking_only: true,
    multimodal: 'image', context_window: 272_000, supports_reasoning_effort: true,
    supported_reasoning_levels: [{ effort: 'low', description: 'Fast' }], default_reasoning_level: 'low' },
];

function store(path, value) {
  return writeFile(path, JSON.stringify(value));
}

async function prepare({ dataDir }) {
  const config = join(dataDir, 'config');
  await mkdir(config, { recursive: true });
  await store(join(config, 'custom_models.json'), { models: customModels });
  await store(join(dataDir, 'personalization.json'), SEED);
  await store(join(dataDir, 'service_config.json'), { search_provider: 'exa' });
  await store(join(dataDir, 'secrets.json'), { exa_api_key: 'private-search-key' });
  await store(join(dataDir, 'codex_auth.json'), {
    tokens: { access_token: 'private-codex-token', refresh_token: 'private-codex-refresh-token' },
  });
  await store(join(dataDir, 'codex_models.json'), {
    models: [codexModel, { ...codexModel, slug: 'unlisted', visibility: 'hidden' }],
  });
  await store(join(dataDir, 'sub_agent_models.json'), { models: [{ key: 'ignored-legacy-model' }] });
}

async function api(base, path, { status = 200, method = 'GET', headers, body, raw } = {}) {
  const encoded = raw ?? (body === undefined ? undefined : JSON.stringify(body));
  const response = await fetch(base + path, {
    method, signal: AbortSignal.timeout(5000),
    headers: { origin: base, ...headers, ...(encoded === undefined ? {} : { 'content-type': 'application/json' }) },
    body: encoded,
  });
  const text = await response.text();
  assert.equal(response.status, status, `${method} ${path}: ${text}`);
  return { headers: response.headers, json: text ? JSON.parse(text) : null };
}

async function getSettings(base, browser, path = SETTINGS, override = {}) {
  const response = await api(base, path, { headers: browser, ...override });
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.json.success, true);
  return response.json.settings;
}

function putSettings(base, browser, values, path = SETTINGS, workspaceId) {
  return api(base, path, {
    headers: browser, method: 'PUT', body: { settings: values, workspace_id: workspaceId },
  });
}

async function challenge(base, headers) {
  const response = await api(base, '/api/csrf-token', { headers });
  return { cookie: response.headers.get('set-cookie')?.split(';', 1)[0] || headers?.cookie,
    'x-csrf-token': response.json.token };
}

async function login(base, anonymous) {
  const response = await api(base, '/host-login', { method: 'POST', headers: anonymous, body: {} });
  assert.equal(response.json.success, true);
  const cookie = response.headers.get('set-cookie').split(';', 1)[0];
  assert.notEqual(cookie, anonymous.cookie);
  return challenge(base, { cookie });
}

async function checkInvalidSettings(base, browser, preferencePath) {
  const old = await readFile(preferencePath);
  const examples = [
    ...['null', '[1]', '{broken'].map(raw => [raw, STRUCTURE_ERROR]),
    ...[undefined, true, [], '10000', 9_999, 11_000.3].map(value => [
      JSON.stringify({ settings: { [THRESHOLD]: value, [CAPACITY]: 7 } }), INTEGER_ERROR,
    ]),
    ...[undefined, true, 0, -2, [], '21', 1.5, 2 ** 53].map(value => [
      JSON.stringify({ settings: { [THRESHOLD]: 100_000, [CAPACITY]: value } }), CAPACITY_ERROR,
    ]),
    [`{"settings":{"${THRESHOLD}":10000.0,"${CAPACITY}":1}}`, INTEGER_ERROR],
    [`{"settings":{"${THRESHOLD}":10000,"${CAPACITY}":1e0}}`, CAPACITY_ERROR],
  ];
  for (const [raw, expected] of examples) {
    const response = await api(base, SETTINGS, { method: 'PUT', headers: browser, raw, status: 400 });
    assert.deepEqual(response.json, { success: false, error: expected }, raw);
    assert.deepEqual(await readFile(preferencePath), old);
  }
}

async function checkConcurrentWrites(base, browser, bearer, preferencePath) {
  for (let round = 1; round <= 12; round += 1) {
    const agent = { user_name: `Agent ${round}`, recent_conversations_prompt_limit: round };
    const chat = { chat_instructions: `聊天设置 ${round}`, chat_network_enabled: round % 2 === 0 };
    const child = { [THRESHOLD]: 300_000 + round, [CAPACITY]: 20 + round };
    const [agentWrite, chatWrite, childWrite] = await Promise.all([
      api(base, PERSONAL, { method: 'POST', body: agent, headers: round % 2 ? browser : bearer }),
      api(base, CHAT, { method: 'PATCH', body: chat, headers: browser }),
      putSettings(base, browser, child, `${SETTINGS}?workspace_id=other`),
    ]);
    assert.equal(agentWrite.json.success, true);
    assert.equal(chatWrite.json.success, true);
    assert.deepEqual(childWrite.json, { success: true, settings: child });
    const persisted = JSON.parse(await readFile(preferencePath, 'utf8'));
    const expected = { ...agent, ...chat, ...child, private_extension: PRIVATE };
    for (const [key, value] of Object.entries(expected)) assert.deepEqual(persisted[key], value, key);
    assert.deepEqual(await getSettings(base, browser, `${SETTINGS}?workspace_id=default`), child);
  }
}

test('Host child-agent settings and model choices use browser auth and the shared account transaction', { timeout: 40_000 }, async (t) => {
  const { gateway, dataDir, workspaceCatalog } = await startGatewayFixture(t, {
    web: true, workspaceIds: ['default', 'other'], prepare,
  });
  const base = gateway.baseUrl;
  const path = join(dataDir, 'personalization.json');
  const bearer = { authorization: `Bearer ${await gateway.readToken()}` };
  await api(base, PERSONAL, { headers: bearer });
  for (const route of [SETTINGS, MODELS]) {
    await api(base, route, { status: 401 });
    await api(base, route, { status: 401, headers: bearer });
  }
  const anonymous = await challenge(base);
  await api(base, SETTINGS, { status: 403, method: 'PUT', headers: bearer, body: { settings: SAVED } });
  await api(base, SETTINGS, { status: 401, method: 'PUT', headers: { ...anonymous, ...bearer }, body: { settings: SAVED } });
  const browser = await login(base, anonymous);
  await api(base, SETTINGS, { status: 403, method: 'PUT', headers: { cookie: browser.cookie }, body: { settings: SAVED } });
  assert.equal((await api(base, SETTINGS, { method: 'HEAD', headers: browser })).json, null);
  assert.equal((await api(base, MODELS, { method: 'HEAD', headers: browser })).json, null);

  const modelList = await api(base, `${MODELS}?workspace_id=unknown`, { headers: browser });
  assert.deepEqual(modelList.json, { success: true, models: expectedModels });
  assert.equal(modelList.headers.get('cache-control'), 'no-store');
  assert.doesNotMatch(JSON.stringify(modelList.json), /private-|ignored-legacy/);
  assert.deepEqual(await getSettings(base, browser), DEFAULTS);
  assert.deepEqual(JSON.parse(await readFile(path, 'utf8')), SEED);
  await store(path, { ...SEED, [THRESHOLD]: '  +275_000  ', [CAPACITY]: '21' });
  assert.deepEqual(await getSettings(base, browser), { [THRESHOLD]: 275_000, [CAPACITY]: 20 });
  await store(path, { ...SEED, [THRESHOLD]: false, [CAPACITY]: 0 });
  assert.deepEqual(await getSettings(base, browser), DEFAULTS);
  await store(path, { ...SEED, [THRESHOLD]: 9999.9, [CAPACITY]: 42 });
  assert.deepEqual(await getSettings(base, browser), { [THRESHOLD]: 10_000, [CAPACITY]: 42 });

  await checkInvalidSettings(base, browser, path);
  const tracked = [join(dataDir, 'service_config.json'), join(dataDir, 'secrets.json')];
  const splitBefore = await Promise.all(tracked.map(file => readFile(file)));
  const clamped = await api(base, SETTINGS, { method: 'PUT', headers: browser,
    raw: `{"settings":{"${THRESHOLD}":100000000000000000000000000000000,"${CAPACITY}":9007199254740991}}` });
  assert.deepEqual(clamped.json.settings, { [THRESHOLD]: 10_000_000, [CAPACITY]: Number.MAX_SAFE_INTEGER });
  const bodyOnly = await putSettings(base, browser, { ...SAVED, private_extension: 'ignored' }, SETTINGS, 'other');
  assert.deepEqual(bodyOnly.json.settings, SAVED);
  assert.deepEqual(await getSettings(base, browser, `${SETTINGS}?workspace_id=default`), SAVED);
  assert.deepEqual(await getSettings(base, browser, SETTINGS, { headers: { ...browser, 'x-astrion-workspace-id': 'other' } }), SAVED);
  const header = { ...browser, 'x-astrion-workspace-id': 'unknown' };
  await api(base, SETTINGS, { method: 'PUT', headers: header, body: { settings: SAVED, workspace_id: 'other' }, status: 404 });
  await api(base, `${SETTINGS}?workspace_id=`, { headers: header, status: 404 });
  assert.deepEqual(await getSettings(base, header, `${SETTINGS}?workspace_id=%20`), SAVED);
  assert.deepEqual((await putSettings(base, header, SAVED, `${SETTINGS}?workspace_id=default`, 'unknown')).json.settings, SAVED);
  const unknownBody = await api(base, SETTINGS, { method: 'PUT', headers: browser,
    body: { settings: SAVED, workspace_id: 'unknown' }, status: 404 });
  assert.deepEqual(unknownBody.json, { success: false, error: 'Workspace not found' });
  assert.deepEqual(await Promise.all(tracked.map(file => readFile(file))), splitBefore);
  assert.deepEqual(JSON.parse(await readFile(path, 'utf8')).private_extension, PRIVATE);

  await checkConcurrentWrites(base, browser, bearer, path);
  const finalSettings = { [THRESHOLD]: 300_012, [CAPACITY]: 32 };
  const personalization = await api(base, PERSONAL, { headers: bearer });
  const chat = await api(base, CHAT, { headers: browser });
  for (const [field, value] of Object.entries(finalSettings)) {
    assert.equal(personalization.json.data[field], value);
    assert.equal(chat.json.data.personalization[field], value);
  }

  await store(workspaceCatalog, { workspaces: [], default_workspace_id: null });
  const lastPreferences = await readFile(path);
  for (const route of [SETTINGS, `${SETTINGS}?workspace_id=other`]) {
    const response = await api(base, route, { headers: browser });
    assert.equal(response.json.success, false);
    assert.equal(response.json.code, 'no_workspace');
  }
  const missing = await putSettings(base, browser, SAVED, SETTINGS, 'other');
  assert.equal(missing.json.code, 'no_workspace');
  assert.deepEqual(await readFile(path), lastPreferences);
  assert.deepEqual((await api(base, MODELS, { headers: browser })).json.models, expectedModels);
  await store(join(dataDir, 'config', 'custom_models.json'), { models: [] });
  await store(join(dataDir, 'codex_models.json'), { models: [] });
  assert.deepEqual((await api(base, MODELS, { headers: browser })).json, { success: true, models: [] });
});
