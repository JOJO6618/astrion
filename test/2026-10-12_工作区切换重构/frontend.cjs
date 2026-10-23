const assert = require('node:assert/strict');
const { fixture, flush, respond, bootstrap, drain } = require('./navigation_fixture.cjs');
const { compile, noop } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const tests = [];
const test = (name, run) => tests.push([name, run]);
const plain = (value) => JSON.parse(JSON.stringify(value));

test('cross-workspace snapshot commits identity, draft, subscription and cursor together', async () => {
  const f = fixture(); f.host.inputMessage = 'unsaved-source';
  f.host.pendingPermissionMode = 'readonly'; f.host.pendingExecutionMode = 'direct';
  const navigation = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws2', conversationId: 'B' });
  await flush();
  assert.equal(f.host.currentHostWorkspaceId, 'ws1');
  assert.equal(f.host.currentConversationId, 'A');
  assert.equal(f.calls.includes('clearTask'), false);
  await drain(f); assert.equal((await navigation).success, true);
  assert.equal(f.host.currentHostWorkspaceId, 'ws2');
  assert.equal(f.host.currentConversationId, 'B');
  assert.equal(f.store.currentConversationId, 'B');
  assert.equal(f.host.pendingPermissionMode, ''); assert.equal(f.host.pendingExecutionMode, '');
  assert.equal(f.host.inputMessage, 'draft:ws2');
  assert.equal(f.attachments.length, 1);
  assert.equal(f.attachments[0][1], 512);
  const saves = f.requests.filter((r) => r.url.includes('input-draft') && r.options.method === 'POST');
  assert.equal(saves.length, 1);
  assert.equal(JSON.parse(saves[0].options.body).workspace_id, 'ws1');
  for (const step of ['subAgent', 'terminals', 'tokens', 'versioning:B', 'git', 'tasks']) {
    assert.equal(f.calls.filter((value) => value === step).length, 1, step);
  }
});

test('same-workspace navigation reuses the composer and never GETs its draft', async () => {
  const f = fixture(); f.host.inputMessage = 'keep-editor';
  const navigation = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws1', conversationId: 'B' });
  await drain(f); await navigation;
  assert.equal(f.host.inputMessage, 'keep-editor');
  assert.equal(f.requests.filter((r) => r.url.includes('input-draft') && !r.options.method).length, 0);
  assert.equal(f.requests.filter((r) => r.url.endsWith('/select')).length, 0);
});

test('A to B to C: late B response cannot commit or reset C loading', async () => {
  const f = fixture();
  const first = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws2', conversationId: 'B' });
  await flush();
  const oldSnapshot = f.requests.find((r) => r.url.includes('/B/bootstrap'));
  const second = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws3', conversationId: 'C' });
  await drain(f, { B: null }); await second;
  respond(oldSnapshot, bootstrap('B', 'ws2')); await first; await flush();
  assert.equal(f.host.currentHostWorkspaceId, 'ws3'); assert.equal(f.host.currentConversationId, 'C');
  assert.equal(f.host.historyLoading, false); assert.equal(f.host.hostWorkspaceSwitching, false);
  assert.equal(f.attachments.length, 1); assert.equal(f.attachments[0][0].task_id, 'T-C');
  const selections = f.requests.filter((r) => r.url.includes('/select?')).map((r) => new URL(r.url, 'http://localhost').searchParams.get('workspace_id'));
  assert.deepEqual(selections, ['ws2', 'ws3']);
});

test('A to B to A: pending B selection completes before the final A selection', async () => {
  const f = fixture();
  const first = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws2', conversationId: 'B' });
  await flush();
  const second = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws1', conversationId: 'A' });
  await drain(f); await Promise.all([first, second]);
  assert.equal(f.host.currentHostWorkspaceId, 'ws1'); assert.equal(f.host.currentConversationId, 'A');
  const selections = f.requests.filter((r) => r.url.includes('/select?')).map((r) => new URL(r.url, 'http://localhost').searchParams.get('workspace_id'));
  assert.deepEqual(selections, ['ws2', 'ws1']);
});

test('failed target snapshot restores source through the authoritative entry', async () => {
  const f = fixture(); f.host.inputMessage = 'source-draft';
  const navigation = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws2', conversationId: 'B' });
  await drain(f, { B: { success: false, error: 'synthetic target failure' } });
  assert.equal((await navigation).success, false);
  assert.equal(f.host.currentHostWorkspaceId, 'ws1'); assert.equal(f.host.currentConversationId, 'A');
  assert.equal(f.host.inputMessage, 'source-draft'); assert.equal(f.host.historyLoading, false);
  assert.equal(f.attachments.length, 1); assert.equal(f.attachments[0][0].task_id, 'T-A');
  assert.equal(f.requests.filter((r) => r.url.includes('/A/bootstrap')).length, 1);
});

test('failed recovery is bounded and leaves the visible conversation guarded', async () => {
  const f = fixture();
  const navigation = f.host.handleSelectWorkspaceConversation({ workspaceId: 'ws2', conversationId: 'B' });
  await drain(f, { A: { success: false, error: 'offline' }, B: { success: false, error: 'offline' } });
  await navigation;
  assert.equal(f.host.currentConversationId, 'A'); assert.equal(f.host.historyLoading, true);
  assert.equal(f.session.currentConversationSession(f.host).conversationId, '');
  assert.equal(f.requests.filter((r) => r.url.includes('/bootstrap')).length, 2);
});

test('old draft acknowledgment does not clear newer input or target scope', async () => {
  const f = fixture({ realSnapshot: false }); f.host.inputMessage = 'one';
  const save = f.host.persistComposerDraftNow(); await flush();
  f.host.inputMessage = 'two'; f.host.scheduleComposerDraftPersist();
  respond(f.requests[0], { success: true, data: { saved: true } }); await save;
  assert.equal(f.host.inputMessage, 'two'); assert.equal(f.host.composerDraftDirty, true);
  const retry = f.host.persistComposerDraftNow(); await flush();
  respond(f.requests[1], { success: true, data: { saved: true } }); await retry;
  assert.equal(f.host.composerDraftDirty, false);
  assert.equal(JSON.parse(f.requests[1].options.body).content, 'two');
});

test('failed save retains scoped edits across navigation preparation', async () => {
  const f = fixture({ realSnapshot: false }); f.host.inputMessage = 'kept';
  const save = f.host.persistComposerDraftNow(); await flush();
  respond(f.requests[0], { success: false, error: 'synthetic disk error' }, false);
  await assert.rejects(save);
  f.host.currentHostWorkspaceId = 'ws2'; f.host.inputMessage = '';
  const source = await f.host.prepareComposerDraftState('ws1');
  assert.equal(source.payload.content, 'kept'); assert.equal(f.requests.length, 1);
  assert.equal(f.toasts.length, 1);
});

test('initial/late draft restore cannot overwrite user input or a newer epoch', async () => {
  const f = fixture({ realSnapshot: false }); f.host.inputMessage = 'already-typing';
  await f.host.restoreComposerDraftState(); assert.equal(f.requests.length, 0);
  f.host.inputMessage = '';
  const restore = f.host.restoreComposerDraftState(); await flush();
  f.host.inputMessage = 'new-input';
  respond(f.requests[0], { success: true, data: { content: 'stored' } }); await restore;
  assert.equal(f.host.inputMessage, 'new-input');
  const entry = await f.host.prepareComposerDraftState('ws1');
  f.host.applyComposerDraftState(entry, 'ws1', null);
  f.session.leaveConversationSession(f.host);
  for (const tick of f.ticks) tick();
  assert.equal(f.calls.includes('draft-meta'), false);
});

test('workspace cache reuses empty lists and rejects invalidated late responses', async () => {
  const f = fixture({ realSnapshot: false });
  const page = f.caching.fetchConversationPage(f.store, 'ws2'); await flush();
  respond(f.requests[0], { success: true, data: { conversations: [], has_more: false } }); await page;
  await f.caching.fetchConversationPage(f.store, 'ws2'); assert.equal(f.requests.length, 1);
  const late = f.caching.fetchConversationPage(f.store, 'ws2', { refresh: true }); await flush();
  f.store.invalidateWorkspaceCache('ws2');
  respond(f.requests[1], { success: true, data: { conversations: [{ id: 'late' }] } }); await late;
  assert.equal(f.store.getWorkspaceCache('ws2').list.length, 0);
  assert.equal(f.store.conversationCacheWorkspaceId, 'ws1');
});

test('all direct fetch callers capture visible workspace and preserve explicit identity', async () => {
  const sent = [];
  const scope = compile('static/src/app/methods/workspaceRequestScope.ts', {}, {
    Headers, URL, Request, location: { href: 'http://localhost/', origin: 'http://localhost' },
    fetch: (url, init) => { sent.push({ url, init }); return Promise.resolve({}); }
  });
  const host = { currentHostWorkspaceId: 'ws1' }; scope.bindWorkspaceRequestHost(host);
  await scope.workspaceScopedFetch('/api/tasks', { method: 'POST' }); host.currentHostWorkspaceId = 'ws2';
  await scope.workspaceScopedFetch('/api/tasks', { headers: { 'X-Astrion-Workspace-Id': 'explicit' } });
  await scope.workspaceScopedFetch('https://example.test/data');
  assert.equal(sent[0].init.headers.get('X-Astrion-Workspace-Id'), 'ws1');
  assert.equal(sent[1].init.headers.get('X-Astrion-Workspace-Id'), 'explicit');
  assert.equal(sent[2].init, undefined);
});

test('pure workspace switch commits blank state and does not trust a stale running cache', async () => {
  const f = fixture(); f.host.pendingPermissionMode = 'readonly';
  f.host.runningWorkspaceTasks = [{ workspace_id: 'ws2', conversation_id: 'B', status: 'running' }];
  const pending = f.host.handleHostWorkspaceSwitch('ws2');
  await drain(f); await pending;
  assert.equal(f.host.currentHostWorkspaceId, 'ws2'); assert.equal(f.host.currentConversationId, null);
  assert.equal(f.host.historyLoading, false); assert.equal(f.host.inputMessage, 'draft:ws2');
  assert.equal(f.host.reasoningEffort, 'high'); assert.equal(f.host.pendingPermissionMode, '');
  assert.equal(f.requests.some(r => r.url.includes('/B/bootstrap')), false);
});

test('pure workspace switch opens the authoritative active conversation with one preparation', async () => {
  const f = fixture();
  const pending = f.host.handleHostWorkspaceSwitch('ws2');
  await drain(f, { workspaceView: { active_conversation_id: 'C' } }); await pending;
  assert.equal(f.host.currentHostWorkspaceId, 'ws2'); assert.equal(f.host.currentConversationId, 'C');
  assert.equal(f.attachments.length, 1); assert.equal(f.attachments[0][1], 512);
  assert.equal(f.requests.filter(r => r.url.includes('/select?')).length, 1);
  assert.equal(f.requests.filter(r => r.url.includes('input-draft') && !r.options.method).length, 1);
});

test('draft restore cannot capture source editor metadata into the target draft', async () => {
  const f = fixture({ realSnapshot: false });
  let meta = { skill_refs: [{ id: 'source' }] };
  f.host.getInputComposerRef = () => ({ getComposerDraftMeta: () => meta,
    restoreComposerDraftMeta: (value) => { meta = value; } });
  const prepared = f.host.prepareComposerDraftState('ws2'); await flush();
  respond(f.requests[0], { success: true, data: { content: 'target-text', skill_refs: [{ id: 'target' }] } });
  const entry = await prepared;
  f.host.currentHostWorkspaceId = 'ws2'; f.host.applyComposerDraftState(entry, 'ws2', null);
  f.host.scheduleComposerDraftPersist('input-watch');
  assert.equal(entry.payload.skill_refs[0].id, 'target');
  f.ticks.forEach(fn => fn());
  assert.equal((await f.host.persistComposerDraftNow()).skipped, true);
  assert.equal(f.requests.length, 1);
});

(async () => {
  for (const [name, run] of tests) { await run(); console.log('PASS ' + name); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
