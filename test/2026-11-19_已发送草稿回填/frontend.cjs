const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');
const { nextTick, reactive, watch } = require('vue');
const { compile, locale, noop } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');

const methods = 'static/src/app/methods/';
const component = fs.readFileSync(
  path.join(__dirname, '../../static/src/components/input/InputComposer.vue'),
  'utf8'
);
const restoreComposer = ts.transpileModule(
  component.slice(component.indexOf('const restoreComposerDraftMeta ='), component.indexOf('const triggerQuickUpload =')),
  { compilerOptions: { target: ts.ScriptTarget.ES2022 } }
).outputText;
const draftUrl = '/api/input-draft?workspace_id=ws&conversation_id=conv_A';
const draftKey = 'astrion:composer:v2:["account","ws","conv_A"]';

function documentFor(text = '') {
  return {
    type: 'doc',
    content: [{ type: 'paragraph', ...(text && { content: [{ type: 'text', text }] }) }]
  };
}

function openComposer({ local, remote, offline = false }) {
  const timers = new Set();
  const setTimeout = (callback) => { timers.add(callback); return callback; };
  const globals = {
    window: { setTimeout },
    setTimeout,
    clearTimeout: (callback) => timers.delete(callback),
    localStorage: { getItem: (key) => local.get(key), setItem: (key, value) => local.set(key, value) },
    async fetch(url, init) {
      if (offline) throw new Error('Offline');
      const previous = remote.get(url) || {};
      if (!init.method) return { ok: true, json: async () => ({ success: true, data: previous }) };
      const body = JSON.parse(init.body);
      assert.equal(body.base_revision, previous.revision || 0);
      const updated = { ...body, revision: body.base_revision + 1 };
      remote.set(url, updated);
      return { ok: true, json: async () => ({ success: true, data: { revision: updated.revision } }) };
    }
  };
  const session = compile(methods + 'conversation/session.ts');
  const storage = compile(methods + 'ui/draftStorage.ts', {}, globals);
  const { composerDraftMethods } = compile(methods + 'ui/composerDrafts.ts', {
    '@/locales': locale,
    '../conversation/session': session,
    './draftStorage': storage
  }, globals);

  let editorDocument = documentFor();
  const getEditorPlainText = () => editorDocument.content[0].content?.[0]?.text || '';
  const editor = { value: { commands: { setContent: (value) => { editorDocument = value; } } } };
  const host = reactive({
    ...composerDraftMethods,
    composerDraftOwner: 'account',
    currentHostWorkspaceId: 'ws',
    currentConversationId: 'conv_A',
    inputMessage: '',
    inputSetMessage(value) { this.inputMessage = value; },
    inputClearMessage() { this.inputMessage = ''; },
    $nextTick: nextTick,
    getInputComposerRef: () => ({
      getComposerDraftMeta: () => ({ editor_json: editorDocument }),
      restoreComposerDraftMeta
    })
  });
  const restoreComposerDraftMeta = vm.runInNewContext(
    `let syncingEditorFromProps = false;\n${restoreComposer}\nrestoreComposerDraftMeta`,
    {
      editor,
      getEditorPlainText,
      textToTiptapContent: documentFor,
      emit: (_event, value) => host.inputSetMessage(value),
      nextTick,
      adjustTextareaSize: noop
    }
  );
  // The parent watcher can run before the child receives the new prop.
  watch(() => host.inputMessage, () => host.scheduleComposerDraftPersist());
  watch(() => host.inputMessage, (value) => {
    if (getEditorPlainText() !== value) editorDocument = documentFor(value);
  });
  session.beginConversationSession(host, 'conv_A', 'ws');

  return {
    host,
    getEditorPlainText,
    type(value) {
      editorDocument = documentFor(value);
      host.inputSetMessage(value);
    },
    async settle() {
      await nextTick();
      for (const callback of timers) {
        timers.delete(callback);
        callback();
      }
      await new Promise(setImmediate);
    }
  };
}

test('accepted submission stays blank after the draft is saved and the conversation reopens', async () => {
  const stores = { remote: new Map(), local: new Map() };
  const first = openComposer(stores);
  await first.host.restoreComposerDraftState();
  await first.settle();
  first.type('already sent');
  await first.settle();
  assert.equal(stores.remote.get(draftUrl).content, 'already sent');

  const submission = first.host.captureComposerDraftSubmission();
  assert.equal(first.host.completeComposerDraftSubmission(submission), true);
  const editorAfterAcceptance = first.getEditorPlainText();
  await first.settle();

  const reopened = openComposer(stores);
  await reopened.host.restoreComposerDraftState();
  await reopened.settle();
  assert.equal(reopened.host.inputMessage, '');
  assert.equal(reopened.getEditorPlainText(), '');
  assert.equal(editorAfterAcceptance, '');
  assert.equal(JSON.parse(stores.local.get(draftKey)).payload.editor_json, null);
  assert.equal(stores.remote.get(draftUrl).editor_json, null);
});

for (const offline of [false, true]) {
  test(`old inconsistent draft keeps attachments without restoring sent text (${offline ? 'local only' : 'from server'})`, async () => {
    const payload = {
      content: '',
      editor_json: documentFor('already sent'),
      skill_refs: [{ name: 'sent skill', path: 'skills/example/SKILL.md' }],
      files: ['unsent.txt'],
      images: [],
      videos: [],
      recovered_ids: ['already-recovered']
    };
    const local = new Map();
    const remote = new Map();
    if (offline) {
      const acknowledged = JSON.stringify(payload);
      local.set(draftKey, JSON.stringify({ payload, acknowledged, serverRevision: 1 }));
    } else {
      remote.set(draftUrl, { ...payload, revision: 1 });
    }
    const composer = openComposer({ local, remote, offline });
    await composer.host.restoreComposerDraftState();
    await composer.settle();

    assert.equal(composer.host.inputMessage, '');
    assert.equal(composer.getEditorPlainText(), '');
    assert.deepEqual([...composer.host.selectedFiles], ['unsent.txt']);
    const saved = JSON.parse(local.get(draftKey)).payload;
    assert.equal(saved.editor_json, null);
    assert.deepEqual(saved.skill_refs, []);
    assert.deepEqual(saved.recovered_ids, ['already-recovered']);
    if (!offline) assert.equal(remote.get(draftUrl).editor_json, null);
  });
}
