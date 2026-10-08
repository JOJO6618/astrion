const assert = require('node:assert/strict');
const { compile, deferred, noop, pinia, locale, visibility } = require('./ts_fixture.cjs');
const base = 'static/src/app/methods/';
function fixture() {
  const session = compile(`${base}conversation/session.ts`);
  const display = compile(`${base}conversation/display.ts`);
  const chat = compile('static/src/stores/chat.ts', {
    pinia, '@/locales': locale, '@/utils/messageVisibility': visibility
  }).useChatStore();
  const stream = compile(`${base}taskPolling/aiStream.ts`, {
    '../common': { debugLog: noop }, '@/locales': locale, '../conversation/session': session
  });
  const shared = compile(`${base}taskPolling/shared.ts`, {
    '../../../utils/messageVisibility': visibility
  });
  const messaging = compile(`${base}taskPolling/messaging.ts`, {
    '../conversation/display': display, '../common': { debugLog: noop }, '../ui/shared': { parseSystemNoticeLabel: (value) => value }, './shared': shared
  }).messagingMethods;
  const aliases = new Map();
  const approvals = compile(`${base}taskPolling/approvals.ts`, {
    '@/locales': locale, '../auxiliaryOwnership': { invalidateAuxiliaryRequest: noop },
    '../../../stores/personalization': {}, '@/components/input/approvalModel': {}
  }).approvalMethods;
  const tools = compile(`${base}taskPolling/tool.ts`, {
    './approvals': { approvalMethods: approvals },
    '../common': { debugLog: noop }, '../auxiliaryOwnership': { invalidateAuxiliaryRequest: noop },
    '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
    '@/locales': locale, './aiStream': stream, '../conversation/session': session,
    '@/components/input/approvalModel': {}
  }).toolMethods;
  const host = { ...stream.aiStreamMethods, ...messaging, ...tools, messages: chat.messages,
    preparingTools: new Map(), runMode: 'thinking', thinkingMode: true,
    _summaryToolBatchId: 'request-1', currentConversationId: 'A', currentHostWorkspaceId: 'ws',
    toolRegisterAction: (action, alias) => aliases.set(String(alias), action),
    toolFindAction: (alias) => aliases.get(String(alias)), toolTrackAction: noop,
    toolUnregisterAction: (action) => { for (const [key, item] of aliases) if (item === action) aliases.delete(key); },
    cloneToolArguments: (args) => ({ ...args }), buildToolLabel: () => '',
    monitorShowThinking: noop, monitorEndModelOutput: noop, monitorResetSpeech: noop,
    monitorShowSpeech: noop, scrollThinkingToBottom: noop, $forceUpdate: noop,
    conditionalScrollToBottom: noop, moveTrailingEmptyAssistantPlaceholderAfterUserInsert: () => false,
    markLatestUserWorkCompleted: noop
  };
  Object.defineProperty(host, 'currentMessageIndex', { get: () => chat.currentMessageIndex,
    set: (value) => { chat.currentMessageIndex = value; } });
  const methods = { chatEnsureAssistantMessage: 'ensureAssistantMessage', chatStartAssistantMessage: 'startAssistantMessage',
    chatStartThinkingAction: 'startThinkingAction', chatAppendThinkingChunk: 'appendThinkingChunk',
    chatCompleteThinkingAction: 'completeThinking', chatStartTextAction: 'startTextAction',
    chatAppendTextChunk: 'appendTextChunk', chatCompleteTextAction: 'completeText',
    chatResetStreamingAttemptActions: 'resetStreamingAttemptActions', chatClearThinkingLocks: 'clearThinkingLocks',
    chatExpandBlock: 'expandBlock', chatSetThinkingLock: 'setThinkingLock', chatCollapseBlock: 'collapseBlock',
    chatAddUserMessage: 'addUserMessage' };
  for (const [alias, key] of Object.entries(methods)) host[alias] = chat[key].bind(chat);
  const requests = [], attachments = [];
  const payload = compile('static/src/stores/taskPolling.ts');
  const send = compile(`${base}message/ownership.ts`, {
    '../common': { debugLog: noop, goalModeDebugLog: noop }, '@/locales': locale,
    '../../../stores/model': {}, '../../../stores/personalization': {}, './shared': {},
    '../../../stores/task': { useTaskStore: () => ({ attachSnapshot: (...args) => attachments.push(args) }) },
    '../../../stores/preview': {}, '../../../stores/taskPolling': payload, '../conversation/session': session
  }, { fetch: () => { const req = deferred(); requests.push(req); return req.promise; } });
  return { host, chat, session, aliases, send, requests, attachments };
}
const tests = [];
function test(name, fn) { tests.push([name, fn]); }
const data = { task_id: 'T', conversation_id: 'A' };
function assistant(f, overrides = {}) {
  const msg = f.host.chatStartAssistantMessage();
  Object.assign(msg, { id: 'T:1:assistant', taskId: 'T', streamAttemptBatchId: 'request-1' }, overrides);
  return msg;
}
test('accepted task binds optimistic input and assistant exactly once', async () => {
  const f = fixture(); const session = f.session.beginConversationSession(f.host, 'A', 'ws');
  const user = f.host.chatAddUserMessage('same'); const reply = f.host.chatStartAssistantMessage();
  f.host.handleTaskEvent = (event) => {
    if (event.type === 'user_message') f.host.handleUserMessage(event.data);
    else f.host.handleAiMessageStart(event.data, event.idx);
  };
  const pending = f.send.createOwnedMessageTask(f.host, session, user, 'same', [], [], 'A', {}, reply);
  f.requests[0].resolve({ ok: true, json: async () => ({ success: true, data: { task_id: 'T', status: 'running' } }) });
  await pending;
  assert.equal(reply.taskId, 'T'); assert.equal(f.attachments[0][1], 0);
  const receive = f.attachments[0][2];
  receive({ type: 'user_message', data: { ...data, message: 'same', message_id: 'U', is_task_input: true } });
  receive({ type: 'ai_message_start', data, idx: 2 });
  assert.equal(f.host.messages.length, 2); assert.equal(user.id, 'U');
  assert.equal(f.host.messages[1], reply); assert.equal(reply.id, 'T:2:assistant');
});
test('late task POST cannot attach or bind a departed view', async () => {
  const f = fixture(); const session = f.session.beginConversationSession(f.host, 'A', 'ws');
  const user = f.host.chatAddUserMessage('same'); const reply = f.host.chatStartAssistantMessage();
  const pending = f.send.createOwnedMessageTask(f.host, session, user, 'same', [], [], 'A', {}, reply);
  f.session.beginConversationSession(f.host, 'B', 'ws');
  f.requests[0].resolve({ ok: true, json: async () => ({ success: true, data: { task_id: 'T' } }) });
  assert.equal(await pending, null); assert.equal(f.attachments.length, 0); assert.equal(reply.taskId, undefined);
});
test('same text with different message identities stays separate', () => {
  const f = fixture();
  f.host.handleUserMessage({ ...data, message: 'same', message_id: 'U1', timestamp: 1760000000 });
  assert.equal(f.host.messages[0].created_at, '2025-10-09T08:53:20.000Z');
  f.host.handleUserMessage({ ...data, message: 'same', message_id: 'U2' });
  f.host.handleUserMessage({ ...data, message: 'updated', message_id: 'U1' });
  assert.equal(f.host.messages.length, 2); assert.equal(f.host.messages[0].content, 'updated');
});
test('hydrated thinking continues and closes the original action', () => {
  const f = fixture(); const action = { id: 'T:3:thinking', blockId: 'T:3:thinking', type: 'thinking', content: 'old', streaming: true };
  const msg = assistant(f, { actions: [action], activeThinkingId: action.id, streamingThinking: 'old', currentStreamingType: 'thinking' });
  f.host.handleThinkingChunk({ ...data, content: '+new' }, 4);
  f.host.handleThinkingEnd({ ...data, full_content: 'old+new' });
  assert.equal(msg.actions.length, 1); assert.equal(action.content, 'old+new'); assert.equal(action.streaming, false);
});
test('hydrated text delta updates the same action', () => {
  const f = fixture(); const action = { id: 'T:3:text', type: 'text', content: 'old', streaming: true };
  const msg = assistant(f, { actions: [action], streamingText: 'old', currentStreamingType: 'text' });
  f.host.handleTextChunk({ ...data, content: '+new' }, 4);
  f.host.handleTextEnd({ ...data, full_content: 'old+new' });
  assert.equal(msg.actions.length, 1); assert.equal(action.content, 'old+new'); assert.equal(action.id, 'T:3:text');
});
test('hydrated frozen show_html remains frozen while suffix continues', () => {
  const f = fixture(); const html = '<show_html ratio="1:1" js="off">card</show_html>';
  const card = { id: 'T:3:text', type: 'text', content: html, streaming: false, frozenByShowHtml: true };
  const msg = assistant(f, { actions: [card], streamingText: html, currentStreamingType: 'text' });
  f.host.handleTextChunk({ ...data, content: 'suffix' }, 4);
  f.host.handleTextEnd({ ...data, full_content: html + 'suffix' });
  assert.equal(msg.actions.length, 2); assert.equal(card.content, html);
  assert.equal(msg.actions[1].content, 'suffix'); assert.equal(msg.actions[1].id, 'T:4:text');
});
test('hydrated tool preparing alias updates original block', () => {
  const f = fixture(); const action = { id: 'T:3:tool', type: 'tool', tool: { id: 'prep', preparingId: 'prep', name: 'read_file', status: 'preparing' } };
  const msg = assistant(f, { actions: [action] });
  f.aliases.set('prep', action);
  f.host.handleToolStart({ ...data, id: 'exec', preparing_id: 'prep', execution_id: 'exec', name: 'read_file', arguments: { path: 'a' } }, 4);
  assert.equal(msg.actions.length, 1); assert.equal(action.id, 'T:3:tool');
  assert.equal(action.tool.status, 'running'); assert.equal(f.aliases.get('exec'), action);
});
test('new tool uses the same task/event identity as snapshot projection', () => {
  const f = fixture(); const msg = assistant(f);
  f.host.handleToolPreparing({ ...data, id: 'prep', name: 'read_file' }, 5);
  f.host.handleToolStart({ ...data, id: 'exec', preparing_id: 'prep', name: 'read_file', arguments: {} }, 6);
  assert.equal(msg.actions.length, 1); assert.equal(msg.actions[0].id, 'T:5:tool');
});
test('stream reset removes only the explicitly marked request actions', () => {
  const f = fixture(); const kept = { id: 'T:3:text', type: 'text', content: 'committed', streaming: false };
  const msg = assistant(f, { actions: [kept], streamAttemptStart: 1 });
  f.host.handleTextStart(data, 8); f.host.handleTextChunk({ ...data, content: 'retry me' }, 9);
  f.host.handleStreamReset({ attempt: 2, max_attempts: 3 });
  assert.equal(msg.actions.length, 1); assert.equal(msg.actions[0], kept);
});
test('inline input establishes a new assistant identity for following delta', () => {
  const f = fixture(); assistant(f, { actions: [{ type: 'text', content: 'first', streaming: false }] });
  f.host.handleUserMessage({ ...data, message: 'guide', message_id: 'guide' });
  f.host.handleTextChunk({ ...data, content: 'next' }, 12);
  assert.equal(f.host.messages.length, 3); assert.equal(f.host.messages[2].taskId, 'T');
  assert.equal(f.host.messages[2].id, 'T:12:assistant');
});
(async () => { for (const [name, fn] of tests) { await fn(); console.log(`PASS ${name}`); } })()
  .catch((error) => { console.error(error); process.exitCode = 1; });
