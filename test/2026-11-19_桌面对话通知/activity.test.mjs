import assert from 'node:assert/strict';
import { test } from 'node:test';
import { conversationKey, createConversationActivityTracker, notificationBounds,
  shouldShowConversationNotification } from '../../desktop-electron/src/conversation-activity.js';

const row = (overrides = {}) => ({
  conversation_kind: 'agent', workspace_id: 'ws', conversation_id: 'conv_1',
  run_id: 'run_1', run_status: 'running', running: true, pending: {}, ...overrides,
});
const idle = (overrides = {}) => row({ run_status: 'succeeded', running: false, ...overrides });
const types = (items) => items.map(({ type }) => type);

test('seed existing completions, and wait for background work and handoffs before reporting a new completion', () => {
  const update = createConversationActivityTracker();
  const old = idle({ conversation_id: 'conv_old' });
  assert.deepEqual(update([old, row()]), []);
  assert.deepEqual(update([old, row({ run_status: 'succeeded' })]), []);
  assert.deepEqual(update([old, idle()]), []);
  assert.deepEqual(update([old, row({ run_id: 'notice' })]), []);
  assert.deepEqual(update([old, idle({ run_id: 'notice' })]), []);
  assert.deepEqual(types(update([old, idle({ run_id: 'notice' })])), ['completed']);
  assert.deepEqual(update([old, idle({ run_id: 'notice' })]), []);
});

test('detect fast runs and leave canceled or failed runs silent', () => {
  const update = createConversationActivityTracker();
  update([]);
  const completed = idle();
  const failed = idle({ conversation_id: 'conv_failed', run_status: 'failed' });
  const stopped = idle({ conversation_id: 'conv_stopped', run_status: 'stopped' });
  assert.deepEqual(update([completed, failed, stopped]), []);
  assert.deepEqual(types(update([completed, failed, stopped])), ['completed']);
  assert.deepEqual(update([completed, failed, stopped]), []);
  const next = idle({ run_id: 'run_2' });
  assert.deepEqual(update([next]), []);
  assert.deepEqual(types(update([next])), ['completed']);
});

test('each approval and question batch is reported once even across repeated snapshots', () => {
  const update = createConversationActivityTracker();
  update([row()]);
  const first = row({ pending: { approval: ['a1'], question: ['batch1'] } });
  assert.deepEqual(types(update([first])), ['approval', 'question']);
  assert.deepEqual(update([first]), []);
  assert.deepEqual(update([row()]), []);
  const second = row({ pending: { approval: ['a2'], question: ['batch2'] } });
  assert.deepEqual(types(update([second])), ['approval', 'question']);
  assert.deepEqual(update([second]), []);
});

test('a detached conversation only suppresses notifications about itself', () => {
  const target = row();
  assert.equal(shouldShowConversationNotification({ kind: 'external' }, target), true);
  assert.equal(shouldShowConversationNotification({ kind: 'astrion' }, target), false);
  const focus = { kind: 'detached', conversationKey: conversationKey(target) };
  assert.equal(shouldShowConversationNotification(focus, target), false);
  assert.equal(shouldShowConversationNotification(focus, row({ workspace_id: 'other' })), true);
  assert.equal(shouldShowConversationNotification(focus, row({ conversation_id: 'conv_2' })), true);
  assert.equal(shouldShowConversationNotification(focus, row({ conversation_kind: 'chat', workspace_id: '' })), true);
});

test('the animation viewport aligns to the display edge and caps visible rows', () => {
  assert.deepEqual(notificationBounds({ x: 100, y: 30, width: 800, height: 600 }, 5), {
    visible: 4, bounds: { x: 520, y: 42, width: 380, height: 320 },
  });
  assert.deepEqual(notificationBounds({ x: 0, y: 0, width: 300, height: 200 }, 5), {
    visible: 2, bounds: { x: 12, y: 12, width: 288, height: 168 },
  });
});
