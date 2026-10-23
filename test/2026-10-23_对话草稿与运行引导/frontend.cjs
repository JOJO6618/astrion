const assert = require('node:assert/strict');
const { compile, deferred, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const base = 'static/src/app/methods/';
const flush = async () => { for (let n = 0; n < 60; n++) await Promise.resolve(); };
function fixture({ local = new Map(), remote = new Map(), offline = false } = {}) {
  const ticks = [], timers = [], toasts = [], requests = [];
  const flags = { offline, hold: null, failAfterSave: false };
  const storage = { getItem: (key) => local.get(key), setItem: (key, value) => local.set(key, value) };
  const fetch = async (url, init = {}) => {
    requests.push({ url, init });
    if (flags.offline) throw new Error('Failed to fetch');
    if (flags.hold) { const hold = flags.hold; flags.hold = null; await hold.promise; }
    if (url.includes('runtime_guidance')) return { ok: true, json: async () => ({ success: true }) };
    const previous = remote.get(url) || {};
    if (!init.method) return { ok: true, json: async () => ({ success: true, data: previous }) };
    const body = JSON.parse(init.body);
    if (body.base_revision !== (previous.revision || 0)) return { ok: false, status: 409,
      json: async () => ({ success: false, error: 'conflict' }) };
    remote.set(url, { ...body, revision: (previous.revision || 0) + 1 });
    if (flags.failAfterSave) { flags.failAfterSave = false; throw new Error('lost acknowledgement'); }
    return { ok: true, json: async () => ({ success: true, data: { saved: true, revision: body.base_revision + 1 } }) };
  };
  const globals = { fetch, localStorage: storage, AbortSignal,
    window: { setTimeout: (fn) => { timers.push(fn); return timers.length; } },
    setTimeout: (fn) => { timers.push(fn); return timers.length; }, clearTimeout: noop };
  const session = compile(base + 'conversation/session.ts');
  const storageModule = compile(base + 'ui/draftStorage.ts', {}, globals);
  const imports = { '@/locales': locale, '../conversation/session': session, './draftStorage': storageModule };
  const drafts = compile(base + 'ui/composerDrafts.ts', imports, globals).composerDraftMethods;
  const task = { currentTaskId: 'task-A' };
  const guidance = compile(base + 'message/guidance.ts', {
    '@/locales': locale, '../../../stores/task': { useTaskStore: () => task },
    '../conversation/session': session, './ownership': { ensureMessageSession: (host) => session.currentConversationSession(host) }
  }, globals).guidanceMethods;
  let editorMeta = {};
  const host = { ...drafts, ...guidance, composerDraftOwner: 'account', currentHostWorkspaceId: 'ws',
    currentConversationId: 'conv_A', inputMessage: '', selectedImages: [], selectedVideos: [], selectedFiles: [],
    getInputComposerRef: () => ({ getComposerDraftMeta: () => editorMeta,
      restoreComposerDraftMeta: (value) => { editorMeta = { skill_refs: value.skill_refs, editor_json: value.editor_json }; } }),
    inputSetMessage(value) { this.inputMessage = value; }, inputClearMessage() { this.inputMessage = ''; },
    inputClearSelectedImages() { this.selectedImages = []; }, inputClearSelectedVideos() { this.selectedVideos = []; },
    inputClearSelectedFiles() { this.selectedFiles = []; }, inputSetLineCount: noop, inputSetMultiline: noop,
    isConversationIndependentRoute: () => false, autoResizeInput: noop,
    $nextTick: (fn) => ticks.push(fn), uiPushToast: (toast) => toasts.push(toast) };
  session.beginConversationSession(host, 'conv_A', 'ws');
  async function tick() { for (let i = 0; i < 20 && ticks.length; i++) { ticks.splice(0).forEach((fn) => fn()); await flush(); } }
  async function enter(cid, ws = 'ws') {
    const entry = await host.prepareComposerDraftState(ws, cid);
    const current = session.beginConversationSession(host, cid || '', ws);
    host.currentHostWorkspaceId = ws; host.currentConversationId = cid;
    host.applyComposerDraftState(entry, ws, current); await tick();
    return entry;
  }
  return { host, flags, local, remote, requests, toasts, timers, session, task, tick, enter, storageModule,
    setMeta: (value) => { editorMeta = value; } };
}
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
test('sync snapshot recovers unconsumed attachments into the already visible draft', async () => {
  const nav = require('../2026-10-12_工作区切换重构/navigation_fixture.cjs');
  const f = nav.fixture();
  const pending = f.host.enterConversation('A', { workspaceId: 'ws1', source: 'sync', urlMode: 'none' });
  await nav.flush();
  const body = nav.bootstrap('A', 'ws1');
  body.data.runtime_queue = { messages: [{ id: 'unconsumed', text: 'recover image', images: ['image.png'] }] };
  nav.respond(f.requests.find((item) => item.url.includes('/A/bootstrap')), body);
  assert.equal((await pending).success, true);
  assert.ok(f.ticks.length > 0);
  for (let turn = 0; turn < 4; turn++) {
    f.ticks.splice(0).forEach((callback) => callback());
    await nav.drain(f);
  }
  assert.ok(f.host.inputMessage.includes('recover image'), JSON.stringify({ input: f.host.inputMessage, images: f.host.selectedImages, toasts: f.toasts, pending: f.host.pendingDraftRecoveryMessages }));
  assert.deepEqual(Array.from(f.host.selectedImages), ['image.png']);
  assert.equal(f.host.pendingDraftRecoveryMessages?.length || 0, 0);
});
test('early typing and restored pending messages both survive initial draft binding', async () => {
  const f = fixture();
  f.host.inputMessage = 'typed before loading';
  f.host.pendingDraftRecoveryMessages = [{ id: 'early', text: 'unconsumed text', images: ['early.png'] }];
  await f.host.restoreComposerDraftState();
  await f.tick();
  assert.equal(f.host.inputMessage, 'typed before loading\n\nunconsumed text');
  assert.deepEqual(Array.from(f.host.selectedImages), ['early.png']);
});
test('conversation drafts isolate text, rich editor metadata and attachments', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.inputMessage = 'draft A';
  f.setMeta({ editor_json: { type: 'doc', content: [] }, skill_refs: [{ name: 'one', path: '/one' }] });
  f.host.selectedImages = ['a.png']; f.host.selectedFiles = ['a.txt'];
  await f.host.persistComposerDraftNow(); await f.enter('conv_B');
  assert.equal(f.host.inputMessage, ''); assert.equal(f.host.selectedImages.length, 0);
  f.host.inputMessage = 'draft B'; await f.host.persistComposerDraftNow(); await f.enter('conv_A');
  assert.equal(f.host.inputMessage, 'draft A'); assert.equal(f.host.selectedFiles[0], 'a.txt');
  assert.equal(f.host.selectedImages[0], 'a.png');
});
test('offline edit survives full page destruction and settings round trip', async () => {
  const f = fixture(); await f.enter('conv_A'); f.flags.offline = true;
  f.host.inputMessage = 'unsynced text'; f.host.scheduleComposerDraftPersist();
  f.host.handleBeforeUnloadDraftPersist();
  const next = fixture({ local: f.local, remote: f.remote, offline: true }); await next.enter('conv_A');
  assert.equal(next.host.inputMessage, 'unsynced text'); assert.equal(next.toasts.length, 0);
  next.flags.offline = false; await next.host.persistComposerDraftNow();
  assert.ok([...next.remote.values()].some((value) => value.content === 'unsynced text'));
});
test('late save acknowledgement cannot erase edits or a different conversation', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.inputMessage = 'first';
  const hold = deferred(); f.flags.hold = hold; const save = f.host.persistComposerDraftNow(); await flush();
  f.host.inputMessage = 'newer'; f.host.scheduleComposerDraftPersist(); await f.enter('conv_B');
  f.host.inputMessage = 'other conversation'; hold.resolve(); await save;
  assert.equal(f.host.inputMessage, 'other conversation'); await f.enter('conv_A');
  assert.equal(f.host.inputMessage, 'newer');
});
test('accepted guidance only clears the submitted version', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.inputMessage = 'guidance';
  f.host.selectedImages = ['diagram.png']; f.host.selectedFiles = ['spec.md'];
  const hold = deferred(); f.flags.hold = hold; const send = f.host.sendRuntimeGuidance(); await flush();
  f.host.inputMessage = 'next edit'; f.host.scheduleComposerDraftPersist(); hold.resolve();
  assert.equal(await send, true); assert.equal(f.host.inputMessage, 'next edit');
  const body = JSON.parse(f.requests.find((req) => req.url.includes('runtime_guidance')).init.body);
  assert.equal(body.media_paths_only, true); assert.equal(body.images[0], 'diagram.png'); assert.equal(body.files[0], 'spec.md');
});
test('attachment-only guidance sends; failures keep all input', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.selectedImages = ['diagram.png'];
  assert.equal(await f.host.sendRuntimeGuidance(), true); assert.equal(f.host.selectedImages.length, 0);
  await f.tick(); f.host.inputMessage = 'keep'; f.host.selectedFiles = ['doc.txt']; f.flags.offline = true;
  assert.equal(await f.host.sendRuntimeGuidance(), false); assert.equal(f.host.inputMessage, 'keep');
  assert.equal(f.host.selectedFiles[0], 'doc.txt');
});
test('late guidance response never clears the newly opened conversation', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.inputMessage = 'guidance';
  const hold = deferred(); f.flags.hold = hold; const send = f.host.sendRuntimeGuidance(); await flush();
  await f.enter('conv_B'); f.host.inputMessage = 'B draft'; hold.resolve(); await send;
  assert.equal(f.host.inputMessage, 'B draft'); await f.enter('conv_A'); assert.equal(f.host.inputMessage, '');
});
test('new conversation draft transfers ownership and clears only after acceptance', async () => {
  const f = fixture(); await f.enter(null); f.host.inputMessage = 'first message';
  const submission = f.host.captureComposerDraftSubmission();
  f.host.adoptComposerDraftConversation('conv_created'); f.host.currentConversationId = 'conv_created';
  f.host.completeComposerDraftSubmission(submission); await flush();
  await f.enter(null); assert.equal(f.host.inputMessage, '');
  await f.enter('conv_created'); assert.equal(f.host.inputMessage, '');
});
test('unconsumed messages recover once and are never automatically sent', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.inputMessage = 'existing';
  const items = [{ id: 'old-1', text: 'unconsumed', files: ['a.txt'], images: ['a.png'] }];
  f.host.recoverRuntimeMessagesToDraft(items); await f.tick();
  assert.equal(f.host.inputMessage, 'existing\n\nunconsumed');
  f.host.recoverRuntimeMessagesToDraft(items); assert.equal(f.host.inputMessage, 'existing\n\nunconsumed');
  f.host.completeComposerDraftSubmission(f.host.captureComposerDraftSubmission()); await f.tick();
  f.host.recoverRuntimeMessagesToDraft(items); assert.equal(f.host.inputMessage, '');
  assert.equal(f.requests.some((req) => req.url.includes('/api/tasks')), false);
});
test('lost save response reconciles committed content before retrying', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.inputMessage = 'saved remotely'; f.flags.failAfterSave = true;
  await f.host.persistComposerDraftNow(); await f.host.persistComposerDraftNow();
  const entry = [...f.remote.values()][0]; assert.equal(entry.content, 'saved remotely');
  assert.equal(f.toasts.length, 0);
});
test('another writer causes a conflict without overwriting either copy', async () => {
  const f = fixture(); await f.enter('conv_A'); f.host.inputMessage = 'local';
  const url = f.requests[0].url; f.remote.set(url, { content: 'other window', revision: 10 });
  await f.host.persistComposerDraftNow(); assert.equal(f.remote.get(url).content, 'other window');
  assert.equal(f.host.inputMessage, 'local'); assert.equal(f.toasts.length, 1);
});
test('local storage isolates accounts, workspaces and blank views', async () => {
  const f = fixture(); await f.enter(null); f.host.inputMessage = 'new draft'; f.host.handleBeforeUnloadDraftPersist();
  await f.enter('conv_A'); assert.equal(f.host.inputMessage, '');
  await f.enter(null, 'ws2'); assert.equal(f.host.inputMessage, '');
  f.host.composerDraftOwner = 'other-account'; await f.enter(null); assert.equal(f.host.inputMessage, '');
});
test('a clean persist cannot block subsequent dirty saves', async () => {
  const f = fixture(); await f.enter('conv_A'); await f.host.persistComposerDraftNow();
  f.host.inputMessage = 'after clean save'; await f.host.persistComposerDraftNow();
  assert.ok([...f.remote.values()].some((value) => value.content === 'after clean save'));
});
(async () => {
  for (const [name, fn] of tests) {
    let timer;
    try { await Promise.race([fn(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Timed out: ' + name)), 3000); })]); }
    finally { clearTimeout(timer); }
    console.log('PASS', name);
  }
  console.log(`${tests.length} frontend regressions passed`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
