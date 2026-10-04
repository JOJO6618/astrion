import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHistoryScrollSession } from '../../static/src/app/methods/ui/historyScrollSession.ts';

function fixture() {
  const state = { followState: 'escaped', scrollEscapeVersion: 0 };
  const writes = [];
  const area = {};
  const controller = {
    getStickState: () => ({ ...state }),
    scrollToBottom(options) {
      if (options.force) state.followState = 'locked';
      writes.push(options);
    }
  };
  const host = {
    currentConversationId: 'first',
    getChatAreaController: () => controller,
    getMessagesAreaElement: () => area,
    chatSetScrollState() {}
  };
  return { host, state, writes };
}

test('history initially locks once and follows later layout without forcing', () => {
  const { host, writes } = fixture();
  const session = createHistoryScrollSession(host);
  assert.equal(session.jump(), true);
  assert.equal(session.jump(), true);
  assert.equal(writes.length, 3);
  assert.equal(writes.filter(write => write.force).length, 1);
});

test('upward input cancels pending writes even after the user relocks', () => {
  const { host, state, writes } = fixture();
  const session = createHistoryScrollSession(host);
  state.scrollEscapeVersion++;
  state.followState = 'escaped';
  assert.equal(session.jump(), false);
  state.followState = 'locked';
  assert.equal(session.jump(), false);
  assert.equal(writes.length, 1);
});

test('escape and relock between frames still invalidate the pending history write', () => {
  const { host, state, writes } = fixture();
  const session = createHistoryScrollSession(host);
  state.scrollEscapeVersion++;
  assert.equal(session.jump(), false);
  assert.equal(writes.length, 1);
});

test('navigation cancels the old history scroll session', () => {
  const { host, writes } = fixture();
  const session = createHistoryScrollSession(host);
  host.currentConversationId = 'second';
  assert.equal(session.jump(), false);
  host.currentConversationId = 'first';
  assert.equal(session.jump(), false);
  assert.equal(writes.length, 1);
});

test('a newer load supersedes old callbacks for the same conversation', () => {
  const { host, writes } = fixture();
  const first = createHistoryScrollSession(host);
  const second = createHistoryScrollSession(host);
  assert.equal(first.jump(), false);
  assert.equal(second.jump(), true);
  assert.equal(writes.length, 3);
});

test('replacing the scroll container or controller cancels pending writes', () => {
  for (const getter of ['getMessagesAreaElement', 'getChatAreaController']) {
    const { host, writes } = fixture();
    const session = createHistoryScrollSession(host);
    host[getter] = () => ({});
    assert.equal(session.jump(), false);
    assert.equal(writes.length, 1);
  }
});
