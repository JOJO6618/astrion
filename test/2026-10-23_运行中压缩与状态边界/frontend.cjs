const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../node_modules/typescript');
const { compile, deferred, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const stores = { subAgents: [], commands: [], form: { tool_intent_enabled: true } };
const computedSource = fs.readFileSync(path.join(root, 'static/src/app/computed.ts'), 'utf8');
const compiled = ts.transpileModule(computedSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
const avatarModule = {};
new Function('require', 'exports', compiled)((name) => {
  if (name === '@/locales') return { ...locale, currentLocale: { value: 'zh-CN' } };
  if (name === 'pinia') return { mapState: () => ({}), mapWritableState: () => ({}) };
  if (name.includes('avatarFace')) return { toolFaceKey: name => name };
  if (name.includes('messageVisibility')) return { messageStartsWork: () => true };
  if (name.includes('avatarStatus')) return compile('static/src/utils/avatarStatus.ts', {
    '@/locales': { ...locale, currentLocale: { value: 'zh-CN' } },
    './avatarFace': { toolFaceKey: name => name },
    './messageVisibility': { messageStartsWork: () => true }
  });
  return new Proxy({}, { get: () => () => stores });
}, avatarModule);
const avatar = avatarModule.computed.avatarStatus;
const tool = (intent, status = 'completed', complete = true) => ({ type: 'tool', tool: {
  name: 'read_file', status, intent_full: intent, intent_rendered: '', intent_complete: complete
} });
const host = (actions, extra = {}) => ({
  messages: [{ role: 'assistant', actions, currentStreamingType: null, activeThinkingId: null }],
  taskInProgress: true, streamingMessage: true, apiRequestPending: false, stopRequested: false,
  preparingTools: new Map(), activeTools: new Map(), ...extra
});
const old = tool('old intent');
assert.equal(avatar.call(host([old])).text, '');
for (const status of ['preparing', 'pending', 'queued', 'awaiting_approval']) {
  assert.equal(avatar.call(host([old, tool('next intent', status)])).text, '');
}
assert.equal(avatar.call(host([old, tool('current intent', 'running')])).text, 'current intent');
assert.equal(avatar.call(host([old, tool('partial intent', 'running', false)])).text, '');
assert.equal(avatar.call(host([old, tool('', 'running')])).text, '');
const thinking = host([old]);
thinking.messages[0].currentStreamingType = 'thinking';
thinking.messages[0].activeThinkingId = 'thought';
assert.equal(avatar.call(thinking).text, 'appCore.thinking');
const waiting = avatar.call(host([old], { apiRequestPending: true }));
assert.equal(waiting.text, 'appCore.waitingApiResponse');
assert.equal(waiting.apiWaiting, true);
assert.equal(avatar.call(host([tool('executing', 'running'), tool('preparing', 'preparing')])).text, 'executing');

// Evaluate the actual SFC computed so the final displayed text follows the same contract.
const sfc = fs.readFileSync(path.join(root, 'static/src/components/input/InputComposer.vue'), 'utf8');
const snippet = sfc.slice(sfc.indexOf('const composerAvatarText = computed'), sfc.indexOf('const projectGitMenuOpen'));
const props = { avatarStatus: waiting };
const text = new Function('props', 'computed', snippet + '\nreturn composerAvatarText;')(
  props, fn => ({ get value() { return fn(); } })
);
assert.equal(text.value, 'appCore.waitingApiResponse');
props.avatarStatus = avatar.call(host([old, tool('next intent', 'preparing')]));
assert.equal(text.value, '');
props.avatarStatus = avatar.call(host([tool('current intent', 'running')]));
assert.equal(text.value, 'current intent');
console.log('Avatar: current thinking/tool only, blank preparation/gaps, API waiting preserved.');

async function compressionCase({ queued, lateStage, taskMatches = true, leave = false }) {
  let owns = true;
  const request = deferred();
  const taskStore = { currentTaskId: taskMatches ? 'existing-task' : null, attachments: [],
    attachSnapshot(...args) { this.attachments.push(args); } };
  const imports = {
    '@/locales': locale,
    '../../../stores/task': { useTaskStore: () => taskStore },
    '../conversation/session': { ownsConversationSession: () => owns },
    './send': { ensureMessageSession: () => ({ controller: new AbortController() }), createOwnedMessageTask: noop }
  };
  const { chatMethods } = compile('static/src/app/methods/message/chat.ts', imports, {
    fetch: () => request.promise
  });
  const { compressionMethods } = compile('static/src/app/methods/taskPolling/compression.ts', {
    '@/locales': locale, '../common': { debugLog: noop }
  });
  const toasts = [];
  const state = {
    currentConversationId: 'conv_synthetic', compressionActiveForCurrentConversation: false,
    mainChatIdle: false, stopRequested: false, compressionStage: '', snapshots: 0,
    uiPushToast: data => (toasts.push(data), toasts.length), uiDismissToast: noop,
    refreshRunningWorkspaceTasks: async () => {},
    async refreshConversationSnapshot() { this.snapshots++; }
  };
  state.handleCompressionState = compressionMethods.handleCompressionState.bind(state);
  const run = chatMethods.compressConversation.call(state);
  assert.equal(state.compressing, true);
  if (lateStage !== undefined) {
    state.handleCompressionState({ conversation_id: state.currentConversationId,
      in_progress: !!lateStage, stage: lateStage, mode: 'requested' });
  }
  if (leave) { owns = false; state.compressionStage = 'other-conversation'; }
  request.resolve({ ok: true, json: async () => ({ success: true, data: {
    task_id: 'existing-task', queued,
    compression: { conversation_id: 'conv_synthetic', in_progress: true, mode: 'requested', stage: 'waiting_for_tools' }
  } }) });
  await run;
  return { state, taskStore, toasts };
}
(async () => {
  let result = await compressionCase({ queued: true });
  assert.equal(result.state.compressionStage, 'waiting_for_tools');
  assert.equal(result.taskStore.attachments.length, 0);
  assert.equal(result.toasts.at(-1).title, 'appTasks.compressionWaiting');
  result = await compressionCase({ queued: true, lateStage: 'generating_summary' });
  assert.equal(result.state.compressionStage, 'generating_summary');
  result = await compressionCase({ queued: true, lateStage: '' });
  assert.equal(result.state.compressionStage, '');
  assert.equal(result.state.compressionInProgress, false);
  result = await compressionCase({ queued: true, taskMatches: false });
  assert.equal(result.taskStore.attachments.length, 0);
  assert.equal(result.state.snapshots, 1);
  result = await compressionCase({ queued: false });
  assert.equal(result.taskStore.attachments.length, 1);
  assert.equal(result.taskStore.attachments[0][1], 0);
  result = await compressionCase({ queued: true, leave: true });
  assert.equal(result.state.compressionStage, 'other-conversation');
  assert.equal(result.taskStore.attachments.length, 0);
  console.log('Compression: running admission, no event replay, late ACK, snapshot fallback, navigation ownership.');
})().catch(error => { console.error(error); process.exitCode = 1; });
