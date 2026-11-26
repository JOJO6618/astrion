import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, readdir, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { startGatewayFixture } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const SHARED = 'conv_duplicate_shared';
const FALLBACK = 'conv_duplicate_fallback';
const FOREIGN = 'conv_duplicate_foreign';
const CHAT = 'conv_duplicate_native_chat';
const OTHER = 'conv_duplicate_other_only';
const DAMAGED = 'conv_duplicate_damaged';
const TITLE = '作业与记录 🧪';
const PERSONAL = { ui_locale: 'en-US', deep_compress_trigger_tokens: 340_000, git_worktree_enabled: true };
const ORACLE = fileURLToPath(new URL('./python_duplicate_oracle.py', import.meta.url));
const RECORD_TIME = '2026-08-11T12:00:00.123456';
const MESSAGES = [
  { role: 'user', content: '原始请求，请读取文件。', message_id: 'msg_original', metadata: { custom_payload: { order: [3, 2, 1] } } },
  { role: 'assistant', content: '当前记录。', unknown_message_field: { unicode: '新增', raw: ' \n ' }, message_id: 'msg_reply',
    metadata: { model_key: 'legacy-assistant-model' },
    tool_calls: [{ id: 'call_record', type: 'function', function: { name: 'read_file', arguments: '{"path":"file.txt"}' } }],
    actions: [{ type: 'tool', tool: { name: 'read_file', result: '输出', private_extension: { revision: 9 } } }],
  },
  { role: 'tool', content: '原样保存', tool_call_id: 'call_record', message_id: 'msg_tool' },
];

function original(id, project, title = TITLE) {
  return {
    id, title, created_at: RECORD_TIME, updated_at: RECORD_TIME, messages: MESSAGES,
    todo_list: { items: [{ text: '旧待办', completed: false }] },
    unknown_top_level: { future: 1 },
    metadata: {
      project_path: project, project_relative_path: null, thinking_mode: false, run_mode: 'deep',
      has_images: true, has_videos: true, permission_mode: 'approval', execution_mode: 'direct',
      pending_permission_mode: 'unrestricted', frozen_system_prompt: 'old source prompt', quick_entry: true,
      total_messages: 11, total_tools: 100, status: 'archived', reasoning_effort: 'high',
      compress_limit_tokens: 700_000, compress_limit_source: 'custom',
      tool_loading: { enabled: true, defer: ['read_file'], known: [], internal: { arbitrary: [1, 'tool'] } },
      git_worktree: { active: true, path: 'unsafe-to-copy' },
      versioning: { enabled: true, mode: 'shallow' }, unknown_metadata_field: true,
    },
    token_statistics: { total_input_tokens: ' +20_001 ', total_output_tokens: 46.8, total_tokens: true,
      cache_cold_start_pending: 'legacy-truthy', custom_usage: { existing: ['原样复制', 2] }, updated_at: RECORD_TIME },
  };
}

function fallback(project) {
  return {
    id: FALLBACK,
    messages: [
      { role: 'user', content: 'hidden', metadata: { hidden: true } },
      { role: 'user', content: 'from a skill', metadata: { source: 'SKILL' } },
      { role: 'user', content: `  ${'中'.repeat(51)}  ` },
    ],
    metadata: { project_path: join(project, 'no-longer-present'), thinking_mode: true },
  };
}

async function store(path, value) {
  return writeFile(path, JSON.stringify(value));
}

function directory(dataDir, workspaceId) {
  return join(dataDir, 'conversations', workspaceId);
}

async function prepare({ dataDir, workspace }) {
  const second = join(workspace, '..', 'workspace-2');
  await store(join(dataDir, 'personalization.json'), PERSONAL);
  const config = join(dataDir, 'config');
  await mkdir(config, { recursive: true });
  await store(join(config, 'custom_models.json'), { models: [] });
  const defaults = directory(dataDir, 'default');
  const other = directory(dataDir, 'other');
  await mkdir(defaults, { recursive: true });
  await mkdir(other, { recursive: true });
  for (const [dir, source] of [
    [defaults, original(SHARED, workspace)],
    [other, original(SHARED, second, '工作区 B 的同 ID 对话')],
    [other, original(OTHER, second, '仅属于 B')],
    [defaults, original(FOREIGN, second, '错误的所属项目')],
    [defaults, { ...original(CHAT, workspace), metadata: { conversation_kind: 'chat' } }],
    [defaults, fallback(workspace)],
  ]) {
    await store(join(dir, `${source.id}.json`), source);
  }
  await writeFile(join(defaults, `${DAMAGED}.json`), '{"id":');
  await store(join(defaults, 'index.json'), { [SHARED]: { title: TITLE, untouched_index_field: { old: 2 } } });
}

async function api(base, path, { method = 'GET', body, headers, status = 200 } = {}) {
  const response = await fetch(base + path, {
    method, signal: AbortSignal.timeout(5_000),
    headers: { origin: base, ...headers, ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  assert.equal(response.status, status, `${method} ${path}: ${text}`);
  return { json: text ? JSON.parse(text) : null, headers: response.headers };
}

function duplicate(base, id, { scope, ...options } = {}) {
  const query = scope === undefined ? '' : `?workspace_id=${encodeURIComponent(scope)}`;
  return api(base, `/api/conversations/${encodeURIComponent(id)}/duplicate${query}`, { method: 'POST', ...options });
}

async function challenge(base, headers) {
  const response = await api(base, '/api/csrf-token', { headers });
  return {
    cookie: response.headers.get('set-cookie')?.split(';', 1)[0] || headers?.cookie,
    'x-csrf-token': response.json.token,
  };
}

async function login(base, challengeHeaders) {
  const accepted = await api(base, '/host-login', { method: 'POST', headers: challengeHeaders, body: {} });
  assert.equal(accepted.json.success, true);
  return challenge(base, { cookie: accepted.headers.get('set-cookie').split(';', 1)[0] });
}

function oracle(source, workspace, workspaceCatalog, dataDir, personalization = PERSONAL) {
  const python = process.env.ASTRION_LEGACY_TEST_PYTHON || process.env.ASTRION_TEST_PYTHON || 'python3';
  return JSON.parse(execFileSync(python, ['-B', ORACLE], {
    input: JSON.stringify({ source, workspace, id: source.id, temporary_root: dataDir, personalization }),
    env: { ...process.env, ASTRION_IGNORE_DOTENV: '1', ASTRION_DATA_ROOT: join(dataDir, 'python-runtime'),
      HOST_WORKSPACES_FILE: workspaceCatalog },
    encoding: 'utf8', timeout: 15_000,
  }));
}

function normalize(copy) {
  const object = structuredClone(copy);
  for (const field of ['created_at', 'updated_at']) {
    assert.ok(Number.isFinite(Date.parse(object[field])), object[field]);
    delete object[field];
  }
  if (object.id !== undefined) {
    assert.match(object.id, /^conv_\d{8}_\d{6}_\d{3}$/);
    delete object.id;
  }
  if (object.token_statistics?.updated_at !== undefined && object.token_statistics.updated_at !== RECORD_TIME) {
    assert.ok(Number.isFinite(Date.parse(object.token_statistics.updated_at)), object.token_statistics.updated_at);
    delete object.token_statistics.updated_at;
  }
  return object;
}

async function checkCopy(dataDir, workspaceId, response, reference, expectedMode, expectedModel, english = true) {
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const { duplicate_conversation_id: id, load_result: loaded, success } = response.json;
  assert.equal(success, true);
  const record = JSON.parse(await readFile(join(directory(dataDir, workspaceId), `${id}.json`), 'utf8'));
  const index = JSON.parse(await readFile(join(directory(dataDir, workspaceId), 'index.json'), 'utf8'));
  assert.equal(record.id, id);
  assert.deepEqual(normalize(record), normalize(reference.document));
  assert.deepEqual(normalize(index[id]), normalize(reference.index));
  assert.deepEqual(loaded, {
    success: true, conversation_id: id, title: record.title, messages_count: record.messages.length,
    run_mode: expectedMode, thinking_mode: expectedMode === 'thinking', model_key: expectedModel,
    message: english ? `Conversation loaded: ${id}` : `对话已加载: ${id}`,
  });
  return id;
}

async function files(dataDir) {
  return Promise.all(['default', 'other'].map(id => readdir(directory(dataDir, id))));
}

test('Agent conversation duplication matches Python history and index under the registered workspace', { timeout: 55_000 }, async (t) => {
  const { gateway, dataDir, workspaces, workspaceCatalog } = await startGatewayFixture(t, {
    web: true, workspaceIds: ['default', 'other'], prepare,
  });
  const [main, other] = workspaces.map(workspace => workspace.path);
  const base = gateway.baseUrl;
  const bearer = { authorization: `Bearer ${await gateway.readToken()}` };
  const anonymous = await challenge(base);
  await duplicate(base, SHARED, { status: 403 });
  await duplicate(base, SHARED, { headers: bearer, status: 403 });
  await duplicate(base, SHARED, { headers: { ...anonymous, ...bearer }, status: 401 });
  const browser = await login(base, anonymous);
  await duplicate(base, SHARED, { headers: { cookie: browser.cookie }, status: 403 });
  const originalFiles = await files(dataDir);
  const rejected = [
    [OTHER, { status: 404 }], [FOREIGN, { status: 404 }], [CHAT, { status: 404 }],
    ['conv_missing', { status: 404 }], ['conv_../hidden', { status: 404 }],
    [DAMAGED, { status: 503 }],
    [SHARED, { scope: 'default', headers: { 'x-astrion-workspace-id': 'other' }, status: 400 }],
    [SHARED, { scope: 'default', body: { workspace_id: 'other' }, status: 404 }],
    [SHARED, { body: { workspace_id: 'other' }, headers: { 'x-astrion-workspace-id': 'default' }, status: 404 }],
    [SHARED, { body: { workspace_id: 7 }, status: 400 }],
  ];
  for (const [id, { headers, ...options }] of rejected) {
    await duplicate(base, id, { headers: { ...browser, ...headers }, ...options });
  }
  assert.deepEqual(await files(dataDir), originalFiles);
  const preferencesFile = join(dataDir, 'personalization.json');
  const preferences = await readFile(preferencesFile);
  await writeFile(preferencesFile, '{"incomplete":');
  await duplicate(base, SHARED, { headers: browser, status: 503 });
  assert.equal(await readFile(preferencesFile, 'utf8'), '{"incomplete":');
  assert.deepEqual(await files(dataDir), originalFiles);
  await writeFile(preferencesFile, preferences);
  await unlink(join(directory(dataDir, 'default'), `${DAMAGED}.json`));

  const mainFile = join(directory(dataDir, 'default'), `${SHARED}.json`);
  const otherFile = join(directory(dataDir, 'other'), `${SHARED}.json`);
  const originals = await Promise.all([mainFile, otherFile].map(file => readFile(file)));
  const reference = oracle(JSON.parse(originals[0]), main, workspaceCatalog, dataDir);
  const referenceOther = oracle(JSON.parse(originals[1]), other, workspaceCatalog, dataDir);
  const [mainCopy, otherCopy, secondCopy] = await Promise.all([
    duplicate(base, SHARED, { headers: browser }),
    duplicate(base, SHARED, { headers: browser, body: { workspace_id: 'other' } }),
    duplicate(base, SHARED, { headers: { ...browser, 'x-astrion-workspace-id': 'default' }, scope: 'default', body: { workspace_id: 'default' } }),
  ]);
  const ids = await Promise.all([
    checkCopy(dataDir, 'default', mainCopy, reference, 'thinking', 'legacy-assistant-model'),
    checkCopy(dataDir, 'other', otherCopy, referenceOther, 'thinking', 'legacy-assistant-model'),
    checkCopy(dataDir, 'default', secondCopy, reference, 'thinking', 'legacy-assistant-model'),
  ]);
  assert.equal(new Set(ids).size, 3);
  assert.deepEqual(await Promise.all([mainFile, otherFile].map(file => readFile(file))), originals);
  const mainIndex = JSON.parse(await readFile(join(directory(dataDir, 'default'), 'index.json'), 'utf8'));
  assert.deepEqual(mainIndex[SHARED], { title: TITLE, untouched_index_field: { old: 2 } });
  const list = await api(base, '/api/conversations?workspace_id=default', { headers: browser });
  assert.ok(list.json.data.conversations.some(row => row.id === ids[0] && row.title === `Copy of ${TITLE}`));
  assert.ok(!list.json.data.conversations.some(row => row.id === ids[1]));
  await api(base, `/api/runtime/sessions/${ids[0]}/history?workspace_id=default`, { headers: bearer });
  await api(base, `/api/runtime/sessions/${ids[0]}/history?workspace_id=other`, { headers: bearer, status: 404 });

  const chinese = { ...PERSONAL, ui_locale: 'zh-CN' };
  await store(join(dataDir, 'personalization.json'), chinese);
  const fallbackReference = oracle(fallback(main), main, workspaceCatalog, dataDir, chinese);
  const fallbackCopy = await duplicate(base, FALLBACK, { headers: browser });
  await checkCopy(dataDir, 'default', fallbackCopy, fallbackReference, 'thinking', null, false);
  assert.equal(fallbackCopy.json.load_result.title, `${'中'.repeat(50)}...`);
  const chineseCopy = await duplicate(base, SHARED, { headers: browser });
  assert.equal(chineseCopy.json.load_result.title, `${TITLE} 的副本`);
  await store(workspaceCatalog, { workspaces: [], default_workspace_id: null });
  const noWorkspace = await duplicate(base, SHARED, { headers: browser });
  assert.equal(noWorkspace.json.code, 'no_workspace');
  assert.equal(noWorkspace.json.success, false);
});
