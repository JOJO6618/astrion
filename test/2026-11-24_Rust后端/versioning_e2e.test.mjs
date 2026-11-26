// Run with a current astrion-headless binary and the CLI's installed development dependencies.
import assert from 'node:assert/strict';
import { mkdir, readFile, readdir, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { startGatewayFixture } from './gateway_harness.mjs';

const SHALLOW = 'conv_python_shallow';
const DEEP = 'conv_python_deep';
const DISABLED = 'conv_python_disabled';
const TRACKING = 'conversation_only';
const INITIAL_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904';
const versionPath = (id) => `/api/conversations/${id}/versioning`;
const checkpointPath = (id, seq) => `${versionPath(id)}/checkpoints${seq === undefined ? '' : `/${seq}`}`;

function jsonl(rows) {
  return rows.map((row) => `${JSON.stringify(row)}\n`).join('');
}

async function conversation(dataDir, workspaceId, id, versioning, kind = 'agent') {
  const directory = join(dataDir, 'conversations', workspaceId);
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, `${id}.json`), JSON.stringify({
    id, title: 'Python fixture', messages: [], metadata: { conversation_kind: kind, versioning },
  }));
}

async function prepare({ dataDir, workspace }) {
  for (const [id, enabled, mode, trackingMode] of [
    [SHALLOW, true, 'shallow', TRACKING],
    [DISABLED, false, 'shallow', TRACKING],
    [DEEP, true, 'full', 'workspace_and_conversation'],
  ]) {
    await conversation(dataDir, 'default', id, { enabled, backup_mode: mode, tracking_mode: trackingMode });
    await mkdir(join(dataDir, 'save', id), { recursive: true });
  }
  await conversation(dataDir, 'default', 'conv_chat_fixture', { enabled: true }, 'chat');
  await conversation(dataDir, 'default', 'conv_empty_fixture', { enabled: true });
  const shallow = join(dataDir, 'save', SHALLOW);
  const blobs = join(shallow, 'shallow_backups');
  await mkdir(join(shallow, 'snapshots'));
  await mkdir(blobs);
  const older = { seq: 1, message: 'before', tracking_mode: TRACKING, shallow_message_id: 'before' };
  const current = {
    seq: 2, message: 'after', tree_hash: INITIAL_TREE, workspace_path: workspace,
    tracking_mode: TRACKING, snapshot_file: 'snapshots\\2.json', insertions: 999,
    deletions: 999, files_changed: 1, files: [{ path: 'stale-summary.txt' }], future_field: 'preserve',
  };
  const initial = { seq: 0, message: 'initial', tracking_mode: TRACKING, files: [] };
  await writeFile(join(shallow, 'inputs.jsonl'), `${jsonl([older, current, initial])}partially-written-row\n`);
  await writeFile(join(shallow, 'snapshots', '2.json'), JSON.stringify({
    shallow_message_id: 'after', messages: [{ role: 'user', content: 'snapshot contents' }],
  }));
  const backup = async (stem, version, content) => {
    const name = `${stem.repeat(16)}@v${version}`;
    await writeFile(join(blobs, name), content);
    return { backup_file_name: name, version };
  };
  const before = {
    'changed.txt': await backup('a', 1, 'shared\r\nold\r\ntail\r\n'),
    'removed.txt': await backup('b', 1, 'removed\n'),
  };
  const after = {
    'added.txt': await backup('c', 2, 'first\nsecond\n'),
    'changed.txt': await backup('a', 2, 'shared\nnew\ntail\n'),
    'first-edit.txt': await backup('d', 2, 'new first edit\n'),
    'removed.txt': { backup_file_name: null, version: 2 },
    'large.txt': await backup('e', 2, Array.from({ length: 605 }, (_, i) => `added ${i}\n`).join('')),
    'unchanged.txt': await backup('f', 1, 'same\n'),
  };
  await backup('d', 1, 'old first edit\n');
  await writeFile(join(blobs, 'state.jsonl'), jsonl([
    { message_id: 'before', tracked_file_backups: before },
    { message_id: 'after', tracked_file_backups: { 'added.txt': after['added.txt'] } },
    { message_id: 'after', tracked_file_backups: after },
  ]));
  const legacy = {
    seq: 7, message: 'legacy', tree_hash: 'legacy-tree-hash', parent_tree_hash: 'legacy-parent',
    tracking_mode: 'workspace_and_conversation', files_changed: 1, insertions: 2, deletions: 1,
    files: [{ path: 'renamed.txt', old_path: 'before.txt', status: 'renamed', insertions: 2, deletions: 1 }],
  };
  await writeFile(join(dataDir, 'save', DEEP, 'inputs.jsonl'), jsonl([legacy]));
  await writeFile(join(dataDir, 'save', DISABLED, 'inputs.jsonl'), jsonl([initial]));
}

function fetchRoute(gateway, path, init = {}) {
  return fetch(gateway.baseUrl + path, { signal: AbortSignal.timeout(3_000), ...init });
}

async function api(gateway, path, status, headers, method = 'GET') {
  const response = await fetchRoute(gateway, path, { headers, method });
  const text = await response.text();
  assert.equal(response.status, status, `${method} ${path}: ${text}`);
  if (method === 'HEAD' || text.length === 0) return text;
  return JSON.parse(text);
}

async function browserSession(gateway) {
  const cookie = (reply) => reply.headers.get('set-cookie').split(';', 1)[0];
  const challenge = await fetchRoute(gateway, '/api/csrf-token');
  assert.equal(challenge.status, 200);
  const login = await fetchRoute(gateway, '/host-login', {
    method: 'POST', body: '{}', headers: {
      cookie: cookie(challenge), origin: gateway.baseUrl, 'content-type': 'application/json',
      'x-csrf-token': (await challenge.json()).token,
    },
  });
  assert.equal(login.status, 200);
  return { cookie: cookie(login) };
}

test('Agent versioning reads Python shallow and legacy deep records with the correct Web authentication', { timeout: 30_000 }, async (t) => {
  const { gateway, dataDir } = await startGatewayFixture(t, { web: true, workspaceIds: ['default', 'other'], prepare });
  const browser = await browserSession(gateway);
  const bearer = { authorization: `Bearer ${await gateway.readToken()}` };
  const deep = join(dataDir, 'save', DEEP);
  const deepBefore = await readFile(join(deep, 'inputs.jsonl'), 'utf8');
  const deepEntries = await readdir(deep);
  for (const path of [versionPath(SHALLOW), checkpointPath(SHALLOW), checkpointPath(SHALLOW, 2)]) {
    await api(gateway, path, 401);
    assert.equal(await api(gateway, path, 200, browser, 'HEAD'), '');
  }
  await api(gateway, versionPath(SHALLOW), 401, bearer);
  await api(gateway, checkpointPath(SHALLOW, 2), 401, bearer);
  await api(gateway, checkpointPath(SHALLOW), 401, { ...browser, authorization: 'Bearer invalid' });
  const status = await api(gateway, versionPath(SHALLOW.slice(5)), 200, browser);
  assert.deepEqual(status, { success: true, data: {
    conversation_id: SHALLOW, host_mode: true, supports_workspace_tracking: false,
    supports_conversation_only: true, enabled: true, mode: 'overwrite', tracking_mode: TRACKING,
    latest_seq: 2, latest_commit: INITIAL_TREE,
  } });
  const list = await api(gateway, checkpointPath(SHALLOW), 200, browser);
  assert.deepEqual(await api(gateway, checkpointPath(SHALLOW), 200, bearer), list);
  assert.deepEqual(list.data.items.map((row) => row.seq), [2, 1, 0]);
  const [latest] = list.data.items;
  assert.equal(latest.future_field, 'preserve');
  assert.deepEqual([latest.files_changed, latest.insertions, latest.deletions], [5, 609, 3]);
  assert.ok(latest.files.every((file) => !Object.hasOwn(file, 'patch_lines')));
  assert.deepEqual(Object.fromEntries(latest.files.map(({ path, status }) => [path, status])), {
    'added.txt': 'added', 'changed.txt': 'modified', 'first-edit.txt': 'modified',
    'large.txt': 'added', 'removed.txt': 'deleted',
  });
  const detail = (await api(gateway, checkpointPath(SHALLOW, '02'), 200, browser)).data;
  const changes = Object.fromEntries(detail.files.map((file) => [file.path, file]));
  assert.deepEqual(changes['changed.txt'].patch_lines, [
    { type: 'remove', content: 'old' }, { type: 'add', content: 'new' },
  ]);
  assert.deepEqual(changes['first-edit.txt'].patch_lines, [
    { type: 'remove', content: 'old first edit' }, { type: 'add', content: 'new first edit' },
  ]);
  assert.deepEqual(changes['removed.txt'].patch_lines, [{ type: 'remove', content: 'removed' }]);
  assert.equal(changes['large.txt'].patch_lines.length, 600);
  assert.equal(changes['large.txt'].patch_truncated, true);
  for (const seq of [-1, '+2', 'bad', 3]) {
    await api(gateway, checkpointPath(SHALLOW, seq), 404, browser);
    assert.equal(await api(gateway, checkpointPath(SHALLOW, seq), 404, browser, 'HEAD'), '');
  }

  const disabled = (await api(gateway, versionPath(DISABLED), 200, browser)).data;
  assert.deepEqual([disabled.enabled, disabled.latest_seq, disabled.latest_commit], [false, null, null]);
  assert.deepEqual((await api(gateway, checkpointPath(DISABLED), 200, browser)).data.items, []);
  assert.equal((await api(gateway, checkpointPath(DISABLED, 0), 200, browser)).data.seq, 0);
  assert.deepEqual((await api(gateway, checkpointPath('conv_empty_fixture'), 200, browser)).data.items, []);
  await api(gateway, checkpointPath('conv_chat_fixture'), 404, browser);
  await api(gateway, checkpointPath('conv_missing'), 404, browser);
  await api(gateway, checkpointPath(SHALLOW), 404, { ...bearer, 'x-astrion-workspace-id': 'other' });
  await api(gateway, versionPath(SHALLOW), 404, { ...browser, 'x-astrion-workspace-id': 'other' });

  const deepStatus = (await api(gateway, versionPath(DEEP), 200, browser)).data;
  assert.equal(deepStatus.tracking_mode, 'workspace_and_conversation');
  assert.equal(deepStatus.latest_commit, 'legacy-tree-hash');
  assert.equal(deepStatus.supports_workspace_tracking, false);
  const legacy = (await api(gateway, checkpointPath(DEEP, 7), 200, browser)).data;
  assert.deepEqual(legacy.files, [{
    path: 'renamed.txt', old_path: 'before.txt', status: 'renamed', insertions: 2, deletions: 1,
    patch_lines: [], patch_truncated: false,
  }]);
  assert.equal(await readFile(join(deep, 'inputs.jsonl'), 'utf8'), deepBefore);
  assert.deepEqual(await readdir(deep), deepEntries);

  const untrusted = 'conv_untrusted_versioning_refs';
  await conversation(dataDir, 'default', untrusted, { enabled: true });
  const untrustedDir = join(dataDir, 'save', untrusted);
  const untrustedBlobs = join(untrustedDir, 'shallow_backups');
  await mkdir(untrustedBlobs, { recursive: true });
  await writeFile(join(dataDir, 'outside.json'), JSON.stringify({ shallow_message_id: 'forged' }));
  await writeFile(join(untrustedDir, 'inputs.jsonl'), jsonl([
    { seq: 1, snapshot_file: '../../outside.json', files: [] },
    { seq: 2, shallow_message_id: 'forged', files: [] },
  ]));
  await writeFile(join(untrustedBlobs, 'state.jsonl'), jsonl([{
    message_id: 'forged', tracked_file_backups: {
      'forged.txt': { backup_file_name: '../../../outside.json', version: 2 },
    },
  }]));
  assert.deepEqual((await api(gateway, checkpointPath(untrusted, 1), 200, browser)).data.files, []);
  assert.equal((await api(gateway, checkpointPath(untrusted, 2), 500, browser)).success, false);

  if (process.platform !== 'win32') {
    const linked = 'conv_linked_versioning';
    await conversation(dataDir, 'default', linked, { enabled: true });
    const linkedDir = join(dataDir, 'save', linked);
    await mkdir(linkedDir);
    await symlink(join(deep, 'inputs.jsonl'), join(linkedDir, 'inputs.jsonl'));
    const denied = await api(gateway, checkpointPath(linked), 500, bearer);
    assert.equal(denied.success, false);
    assert.equal(Object.hasOwn(denied, 'data'), false);
  }
  await conversation(dataDir, 'other', SHALLOW, { enabled: true });
  for (const workspaceId of ['default', 'other']) {
    await api(gateway, checkpointPath(SHALLOW), 409, { ...bearer, 'x-astrion-workspace-id': workspaceId });
  }
});

test('Agent versioning checkpoint list is available to headless Host Bearer', { timeout: 30_000 }, async (t) => {
  const { gateway } = await startGatewayFixture(t, { prepare });
  const bearer = { authorization: `Bearer ${await gateway.readToken()}` };
  await api(gateway, checkpointPath(SHALLOW), 401);
  const list = await api(gateway, checkpointPath(SHALLOW), 200, bearer);
  assert.deepEqual(list.data.items.map((row) => row.seq), [2, 1, 0]);
});
