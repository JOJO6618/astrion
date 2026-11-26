import assert from 'node:assert/strict';
import { migrate } from '../2026-11-27_Rust对话格式迁移/fixture.mjs';
import { execFileSync } from 'node:child_process';
import { scryptSync } from 'node:crypto';
import { access, mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { startGatewayFixture } from '../2026-11-24_Rust后端/gateway_harness.mjs';

const LOCALE = 'en-US';
const MAIN = 'conv_review_python';
const OTHER = 'conv_review_other_workspace';
const TITLE = '  回顾😀:旧/动作?  ';
const OTHER_TITLE = 'Other workspace';
const PASSWORD = 'Review fixture 合成口令 🔑';
const MESSAGES = [
  { role: 'system', content: { text: 'system instruction  ' } },
  { role: 'user', content: ['首行😀\n', { type: 'image_url', image_url: { url: 'data:image/png;base64,fixture' } }, { type: 'text', text: '次行  ' }] },
  { role: 'assistant', content: ' \n ', actions: [
    { type: 'thinking', content: 'ignored private reasoning' },
    { type: 'tool', tool: { name: 'original_action', arguments: { order: [1, true, null], path: '资料.txt' }, content: 'direct content  ', result: 'ignored overridden result' } },
    { type: 'tool', tool: { name: 'snapshot_action', arguments: null, argumentSnapshot: { text: '快照 😀', data: { count: 2, ready: false } }, result: { success: true, lines: ['旧', '新'] } } },
    { type: 'tool', tool: { name: 'string_result', arguments: ['first', 'second'], result: 'legacy tool result' } },
    { type: 'tool', tool: { name: 'message_fallback', message: 'older message field' } },
    { type: 'tool', tool: { content: { nested: true, label: '文字' } } },
    { type: 'tool', tool: {} },
  ] },
  { role: 'assistant', content: [
    { type: 'text', text: 'Assistant answer ' },
    { type: 'tool_call', function: { name: 'embedded_call', arguments: '{"embedded":"中文","num":2}' } },
    'with plain part  ',
  ], tool_calls: [
    { function: { name: 'api_json_args', arguments: '{"first":"你","second":[1,2]}' } },
    { function: { name: 'api_invalid_args', arguments: '{not JSON}' } },
    { function: { name: 'api_object_args', arguments: { a: false, extra: '松' } } },
  ] },
  { role: 'tool', content: { text: 'separate result\nsecond line ' } },
  { role: 'user', content: null, text: 'using the old top-level text field' },
  { role: 'developer', content: 'unsupported role' },
  { role: 'assistant', content: { text: 'last assistant answer' } },
];
const OTHER_MESSAGES = [{ role: 'user', content: 'Other workspace transcript 独有' }];

const PREVIEWS = [
  {},
  { mode: 'full' },
  { mode: 'full', limit: 0 },
  { mode: 'full', limit: 1 },
  { mode: 'full', limit: 4 },
  { mode: 'full', limit: ' +6 ' },
  { mode: 'full', limit: -2 },
  { mode: 'full', limit: -999 },
  { mode: 'full', limit: 999 },
  { mode: 'full', limit: 'invalid' },
  { mode: ' FuLl ', limit: 999 },
  { mode: 'dialogue', limit: 2 },
  { mode: 'dialogue', limit: -1 },
  { mode: 'unexpected', limit: 999 },
];
const EXPORTS = [' FuLl ', 'dialogue', 'unknown'];
const oracleScript = fileURLToPath(new URL('./python_review_oracle.py', import.meta.url));

function oracle(messages, title, previews, exports) {
  const python = process.env.ASTRION_LEGACY_TEST_PYTHON || process.env.ASTRION_TEST_PYTHON || 'python3';
  return JSON.parse(execFileSync(python, ['-B', oracleScript], {
    input: JSON.stringify({ messages, title, locale: LOCALE, previews, exports }), encoding: 'utf8',
  }));
}

async function writeConversation(dataDir, workspaceId, id, title, messages, projectPath) {
  const dir = join(dataDir, 'conversations', workspaceId);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, `${id}.json`), JSON.stringify({
    id, title, messages, metadata: { conversation_kind: 'agent', project_path: projectPath },
    created_at: '2026-11-25T00:00:00', updated_at: '2026-11-25T00:00:00',
  }));
}

async function prepare({ dataDir, workspace }) {
  const salt = 'reviewFixtureSalt';
  const hash = scryptSync(PASSWORD, salt, 64, { N: 32768, r: 8, p: 1, maxmem: 64 << 20 }).toString('hex');
  await writeFile(join(dataDir, 'host_auth.json'), JSON.stringify({
    version: 1, enabled: true, generation: 'review-fixture-generation', password_hash: `scrypt:32768:8:1$${salt}$${hash}`,
  }));
  await writeFile(join(dataDir, 'personalization.json'), JSON.stringify({ ui_locale: LOCALE }));
  await writeConversation(dataDir, 'default', MAIN, TITLE, MESSAGES, workspace);
  await writeConversation(dataDir, 'other', OTHER, OTHER_TITLE, OTHER_MESSAGES, join(workspace, '..', 'workspace-2'));
}

function request(gateway, path, options = {}) {
  return fetch(gateway.baseUrl + path, { signal: AbortSignal.timeout(5_000), ...options });
}

async function api(gateway, path, status, { headers, method = 'GET', body } = {}) {
  const response = await request(gateway, path, {
    method, headers: body === undefined ? headers : { ...headers, 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  assert.equal(response.status, status, `${method} ${path}: ${text}`);
  return text ? JSON.parse(text) : null;
}

function readCookie(response) {
  return response.headers.get('set-cookie').split(';', 1)[0];
}

async function anonymousChallenge(gateway) {
  const response = await request(gateway, '/api/csrf-token');
  assert.equal(response.status, 200);
  return { cookie: readCookie(response), origin: gateway.baseUrl, 'x-csrf-token': (await response.json()).token };
}

async function login(gateway, challenge) {
  const submit = (password) => request(gateway, '/host-login', {
    method: 'POST', body: JSON.stringify({ password }), headers: { ...challenge, 'content-type': 'application/json' },
  });
  const denied = await submit('Wrong fixture password');
  assert.equal(denied.status, 401);
  await denied.text();
  const accepted = await submit(PASSWORD);
  assert.equal(accepted.status, 200);
  assert.equal((await accepted.json()).success, true);
  const cookie = readCookie(accepted);
  assert.notEqual(cookie, challenge.cookie);
  const headers = { cookie, origin: gateway.baseUrl };
  const session = (await api(gateway, '/api/session-status', 200, { headers })).session;
  assert.equal(session.logged_in, true);
  assert.equal(session.username, 'host');
  assert.equal(session.role, 'admin');
  const csrfAfterLogin = await api(gateway, '/api/csrf-token', 200, { headers });
  return { headers, posting: { ...headers, 'x-csrf-token': csrfAfterLogin.token } };
}

function previewPath(id, { mode, limit } = {}) {
  const query = new URLSearchParams();
  if (mode !== undefined) query.set('content_mode', mode);
  if (limit !== undefined) query.set('limit', String(limit));
  return `/api/conversations/${id}/review_preview${query.size ? `?${query}` : ''}`;
}

const exportPath = (id) => `/api/conversations/${id}/review`;

async function checkExport(gateway, project, id, mode, headers, expected, safeTitle) {
  const response = await api(gateway, exportPath(id), 200, { method: 'POST', headers, body: { content_mode: mode } });
  assert.equal(response.success, true);
  assert.equal(response.data.content_mode, expected.mode);
  assert.equal(response.data.char_count, expected.char_count);
  const prefix = `.astrion/review/review_${safeTitle}_`;
  assert.ok(response.data.path.startsWith(prefix), response.data.path);
  assert.match(response.data.path.slice(prefix.length), /^\d{14}(?:_[0-9a-f]{32})?\.md$/);
  assert.equal(await readFile(join(project, response.data.path), 'utf8'), expected.markdown);
}

test('Agent review preview and Markdown export match the Python formatter with real Host login', { timeout: 30_000 }, async (t) => {
  const expected = oracle(MESSAGES, TITLE, PREVIEWS, EXPORTS);
  const otherExpected = oracle(OTHER_MESSAGES, OTHER_TITLE, [{}], ['dialogue']);
  const { gateway, dataDir, workspaces } = await startGatewayFixture(t, { web: true, workspaceIds: ['default', 'other'], prepare });
  const [main, other] = workspaces;
  const reviewDirectory = (workspace) => join(workspace.path, '.astrion', 'review');
  await api(gateway, previewPath(MAIN), 401);
  const missingCsrf = await api(gateway, exportPath(MAIN), 403, { method: 'POST', body: {} });
  assert.equal(missingCsrf.error, 'CSRF validation failed');
  const challenge = await anonymousChallenge(gateway);
  await api(gateway, exportPath(MAIN), 401, { headers: challenge, method: 'POST', body: {} });
  const bearer = { authorization: `Bearer ${await gateway.readToken()}` };
  await api(gateway, previewPath(MAIN), 401, { headers: bearer });
  await api(gateway, exportPath(MAIN), 401, { headers: { ...challenge, ...bearer }, method: 'POST', body: {} });
  const { headers, posting } = await login(gateway, challenge);
  await migrate(gateway.baseUrl,posting);
  await api(gateway, exportPath(MAIN), 403, { headers, method: 'POST', body: {} });
  await api(gateway, previewPath(MAIN), 200, { headers, method: 'HEAD' });

  const policyPath = join(dataDir, 'admin_policy.json');
  const global = { ui_blocks: { block_conversation_review: true } };
  await writeFile(policyPath, JSON.stringify({ global }));
  for (const [path, method] of [[previewPath(MAIN), 'GET'], [exportPath(MAIN), 'POST']]) {
    const blocked = await api(gateway, path, 403, { method, headers: posting });
    assert.deepEqual(blocked, { success: false, error: expected.blocked_error });
  }
  await assert.rejects(access(reviewDirectory(main)), { code: 'ENOENT' });
  await writeFile(policyPath, JSON.stringify({ global, users: { host: { ui_blocks: { block_conversation_review: false } } } }));

  for (const [i, preview] of PREVIEWS.entries()) {
    const response = await api(gateway, previewPath(MAIN, preview), 200, { headers });
    assert.deepEqual(response, { success: true, data: expected.previews[i] }, JSON.stringify(preview));
  }
  await assert.rejects(access(reviewDirectory(main)), { code: 'ENOENT' });
  const scope = (source, workspace) => ({ ...source, 'x-astrion-workspace-id': workspace.workspace_id });
  await api(gateway, previewPath(MAIN), 404, { headers: scope(headers, other) });
  await api(gateway, previewPath(OTHER), 404, { headers: scope(headers, main) });
  await api(gateway, exportPath(MAIN), 404, { headers: scope(posting, other), method: 'POST', body: {} });
  await api(gateway, exportPath(OTHER), 404, { headers: scope(posting, main), method: 'POST', body: {} });
  await assert.rejects(access(reviewDirectory(other)), { code: 'ENOENT' });
  assert.deepEqual((await api(gateway, previewPath(OTHER), 200, { headers: scope(headers, other) })).data, otherExpected.previews[0]);

  for (const [i, mode] of EXPORTS.entries()) {
    await checkExport(gateway, main.path, MAIN, mode, posting, expected.exports[i], expected.safe_title);
  }
  await checkExport(gateway, other.path, OTHER, 'dialogue', scope(posting, other), otherExpected.exports[0], otherExpected.safe_title);
});
