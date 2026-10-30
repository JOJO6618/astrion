const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const vue = require('../../node_modules/vue');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { renderToString } = require('../../node_modules/@vue/server-renderer');
const { compile, deferred, pinia, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const cases = [];
const test = (name, fn) => cases.push([name, fn]);

function chatFixture() {
  const requests = [];
  let operation = 0;
  const request = (url, body) => {
    const pending = deferred();
    requests.push({ url, body, ...pending });
    return pending.promise;
  };
  const api = { command: request, chatApi: request, loadView: request,
    operationId: () => `operation-${++operation}`, releasePresentation() {} };
  const chat = compile('static/src/chat/useChat.ts', {
    vue: { ...vue, onBeforeUnmount() {} }, './api': api,
    './types': compile('static/src/chat/types.ts')
  }, { window: { setTimeout() {}, clearTimeout() {}, addEventListener() {}, removeEventListener() {} } }).useChat();
  return { chat, requests };
}
function view(id, enabled) {
  return { document: { id, title: id, messages: [], metadata: {
    chatgpt_fast_mode: enabled, network_enabled: true,
    chat: { latest_leaf_id: null, turns: [] }
  } }, display: { messages: [], task: null, state: {}, next_event_idx: 0 } };
}
async function load(f, id, enabled) {
  const pending = f.chat.load(id);
  f.requests.at(-1).resolve(view(id, enabled));
  await pending;
}

test('Chat inherits on/off from departed view and sends its explicit selection', async () => {
  const f = chatFixture();
  await load(f, 'A', true);
  await f.chat.load('');
  assert.equal(f.chat.fastMode.value, true);
  const sending = f.chat.send('hello', []);
  assert.equal(f.requests.at(-1).body.settings.chatgpt_fast_mode, true);
  f.requests.at(-1).reject(new Error('synthetic cancellation'));
  await assert.rejects(sending);
  await load(f, 'B', false);
  await f.chat.load('');
  assert.equal(f.chat.fastMode.value, false);
});
test('Chat missing historical field is off; stale setting response cannot leak', async () => {
  const f = chatFixture();
  await load(f, 'A', true);
  const saving = f.chat.configure({ chatgpt_fast_mode: false });
  const request = f.requests.at(-1);
  await load(f, 'B', undefined);
  assert.equal(f.chat.fastMode.value, false);
  request.resolve({});
  await saving;
  assert.equal(f.chat.fastMode.value, false);
  assert.equal(f.chat.configuring.value, false);
});
test('Chat failed save preserves value and releases switch', async () => {
  const f = chatFixture();
  await load(f, 'A', false);
  const saving = f.chat.configure({ chatgpt_fast_mode: true });
  assert.equal(f.chat.configuring.value, true);
  f.requests.at(-1).reject(new Error('synthetic write failure'));
  await assert.rejects(saving);
  assert.equal(f.chat.fastMode.value, false);
  assert.equal(f.chat.configuring.value, false);
});
test('Agent local choice does not create a conversation or perform a request', async () => {
  let calls = 0;
  const methods = compile('static/src/app/methods/ui/chatgptFastMode.ts', {
    '@/locales': locale, '../auxiliaryOwnership': { beginAuxiliaryRequest: () => () => true }
  }, { fetch: () => { calls++; } }).chatgptFastModeMethods;
  const host = { isConnected: true, isChatgptModel: true, supportsChatgptFastMode: true,
    chatgptFastMode: false, currentConversationId: null };
  await methods.toggleChatgptFastMode.call(host);
  assert.equal(host.chatgptFastMode, true);
  assert.equal(calls, 0);
});
test('Agent saves explicit conversation identity; stale completion cannot touch a new view', async () => {
  const response = deferred();
  let owner = true, sent;
  const methods = compile('static/src/app/methods/ui/chatgptFastMode.ts', {
    '@/locales': locale, '../auxiliaryOwnership': { beginAuxiliaryRequest: () => () => owner }
  }, { fetch: (url, options) => { sent = { url, body: JSON.parse(options.body) }; return response.promise; } }).chatgptFastModeMethods;
  const host = { isConnected: true, isChatgptModel: true, supportsChatgptFastMode: true,
    chatgptFastMode: false, currentConversationId: 'A' };
  const saving = methods.toggleChatgptFastMode.call(host);
  assert.deepEqual(sent.body, { enabled: true, conversation_id: 'A' });
  owner = false;
  host.currentConversationId = 'B';
  host.chatgptFastModeSaving = false;
  response.resolve({ ok: true, json: async () => ({ success: true, data: { chatgpt_fast_mode: true } }) });
  await saving;
  assert.equal(host.chatgptFastMode, false);
  assert.equal(host.chatgptFastModeSaving, false);
});
test('model catalog preserves OAuth identity separately from support', async () => {
  const models = compile('static/src/stores/model.ts', { pinia, '@/locales': locale }, {
    fetch: async () => ({ ok: true, json: async () => ({ success: true, items: [
      { model_key: 'oauth/available', is_chatgpt_model: true, supports_chatgpt_fast_mode: true },
      { model_key: 'oauth/unavailable', is_chatgpt_model: true, supports_chatgpt_fast_mode: false },
      { model_key: 'api/gpt', is_chatgpt_model: false }
    ] }) })
  }).useModelStore();
  await models.fetchModels();
  assert.equal(models.allModels[0].supportsChatgptFastMode, true);
  assert.equal(models.allModels[1].isChatgptModel, true);
  assert.equal(models.allModels[1].supportsChatgptFastMode, false);
  assert.equal(models.allModels[2].isChatgptModel, false);
});

function component(file, overrides = {}) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const { descriptor, errors } = compiler.parse(source, { filename: file });
  assert.deepEqual(errors, []);
  const script = compiler.compileScript(descriptor, { id: 'fast-section', inlineTemplate: true });
  const code = ts.transpileModule(script.content, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS
  } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, require(key) {
    if (key === 'vue') return vue;
    if (key in overrides) return { default: overrides[key] };
    throw Error(`Unexpected import: ${key}`);
  } });
  return module.exports.default;
}
test('real shared menu renders section only for OAuth, beneath effort, with one switch', async () => {
  const pill = component('static/src/components/settings/tabs/providers/PillSwitch.vue');
  const section = component('static/src/components/chat/ChatgptFastModeSection.vue', {
    '@/components/settings/tabs/providers/PillSwitch.vue': pill
  });
  const effort = vue.defineComponent({ render: () => vue.h('div', { class: 'effort-marker' }) });
  const header = component('static/src/components/chat/ConversationModelHeader.vue', {
    '@/components/input/EffortSlider.vue': effort, './ChatgptFastModeSection.vue': section
  });
  for (const mobile of [false, true]) for (const isChatgptModel of [false, true]) {
    const noop = () => {};
    const props = { mobile, headerMenuOpen: true, isConnected: true, currentModelLabel: 'Model',
      headerEffortLabel: 'high', currentConversationTitle: 'Test', modelMenuSearchQuery: '',
      filteredGroupedModelOptions: [], isModelGroupCollapsed: () => false, toggleModelGroup: noop,
      currentModelKey: 'synthetic', streamingMessage: false, toggleHeaderMenu: noop,
      handleHeaderModelSelect: noop, hasAnyModels: true, openSettingsProviders: noop,
      openManageModels: noop, headerRunModeOptions: [], resolvedRunMode: 'thinking',
      handleHeaderRunModeSelect: noop, showEffortSlider: true, reasoningEffort: 'high',
      setReasoningEffort: noop, isChatgptModel, supportsChatgptFastMode: true,
      chatgptFastMode: true, toggleChatgptFastMode: noop };
    const app = vue.createSSRApp(header, props);
    app.config.globalProperties.$t = key => ({ 'appCore.chatgptFastMode': 'ChatGPT高速模式',
      'appCore.chatgptFastModeDesc': '模型输出更快，但会消耗更多额度' })[key] || key;
    const html = await renderToString(app);
    assert.equal(html.includes('ChatGPT高速模式'), isChatgptModel);
    assert.equal((html.match(/role="switch"/g) || []).length, isChatgptModel ? 1 : 0);
    if (isChatgptModel) {
      assert.ok(html.indexOf('effort-marker') < html.indexOf('chatgpt-fast-mode'));
      assert.ok(html.includes('aria-checked="true"'));
      assert.ok(html.includes('模型输出更快，但会消耗更多额度'));
    }
  }
});

(async () => {
  for (const [name, run] of cases) { await run(); console.log(`PASS ${name}`); }
  console.log(`${cases.length} frontend regression groups passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
