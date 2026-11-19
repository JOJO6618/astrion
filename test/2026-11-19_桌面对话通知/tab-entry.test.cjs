const assert = require('node:assert/strict');
const { test } = require('node:test');
const { fixture, answer, commandRequest, tab, flush } = require('../2026-11-14_桌面多视图/frontend/fixture.cjs');

test('the polling snapshot projects and clears the answer flag without a navigation change', () => {
  const { store, state } = fixture({ chrome: true });
  const initial = tab('a', 'conv_a', 'ws1', { running: true, awaiting_user_answer: true });
  assert.equal(store.applyServerSnapshot(state(2, [initial], 'a'), 1), true);
  assert.equal(store.tabs[0].awaitingUserAnswer, true);
  assert.equal(store.tabs[0].awaitingHumanApproval, false);
  const answered = { ...initial, awaiting_user_answer: false };
  assert.equal(store.applyServerSnapshot(state(2, [answered], 'a'), 2), true);
  assert.equal(store.tabs[0].awaitingUserAnswer, false);
  assert.equal(store.tabs[0].running, true);
});

test('a notification opens by conversation identity and forwards the resolved tab before requesting focus', async () => {
  const f = fixture({ chrome: true });
  const bridge = f.window.astrionShell;
  const calls = [];
  let receive;
  bridge.onNotificationOpen = (callback) => { receive = callback; return () => { receive = null; }; };
  bridge.forwardTabsState = (state) => calls.push(['snapshot', state.revision]);
  bridge.focusNotificationTab = (key) => calls.push(['focus', key]);
  const stop = f.store.installShellRequests();

  receive({ conversationId: 'conv_chat', conversationKind: 'chat', workspaceId: '', title: 'Sample' });
  const request = await commandRequest(f, 'open');
  const { payload } = JSON.parse(request.options.body);
  assert.equal(payload.conversation_id, 'conv_chat');
  assert.equal(payload.conversation_kind, 'chat');
  assert.equal(payload.workspace_id, '');
  const target = tab('detached-key', 'conv_chat', '', { conversation_kind: 'chat', placement: 'detached' });
  answer(request, { success: true, state: f.state(4, [target]), result: { key: target.key, focus: 'detached' } });
  await flush();
  assert.deepEqual(calls, [['snapshot', 4], ['focus', 'detached-key']]);
  stop();
  assert.equal(receive, null);
});
