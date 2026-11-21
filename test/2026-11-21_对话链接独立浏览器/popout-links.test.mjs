import assert from 'node:assert/strict';
import test from 'node:test';
import { load } from '../2026-11-13_WindowsElectron/loader.mjs';
import { popoutScopeId, profileKey, trustedFrame } from '../../desktop-electron/src/browser/policy.js';

const origin = 'http://127.0.0.1:8091';
const outside = 'https://example.com/reference#section';
const { NativeBrowser } = await load('browser/index.js');

function fixture({ selected = true } = {}) {
  const events = [];
  const tab = { key: 'first', kind: 'conv', conversation_kind: 'agent', workspace_id: 'work', conversation_id: 'conv_20261121_200000_001' };
  const entry = { key: tab.key, shown: true };
  const profile = { key: profileKey('signed-in'), searchEngine: () => 'google' };
  const owner = `${profile.key}:${popoutScopeId(tab)}`;
  const mainFrame = { url: `${origin}/${tab.conversation_id}` };
  const sender = { mainFrame, isDestroyed: () => false };
  const view = { webContents: sender };
  const event = { sender, senderFrame: mainFrame };
  const appHost = { view, lease: '', trusted: (caller) => trustedFrame(caller, view, origin) };
  const popout = {
    owner, profile, window: {
      isDestroyed: () => false, isMinimized: () => true,
      restore: () => events.push('restore'), show: () => events.push('show'), focus: () => events.push('focus')
    },
    view: { webContents: { isDestroyed: () => false, focus() {} } },
    trusted: () => false,
    active: () => browserTab
  };
  const browserTab = selected ? { id: 'previous-page', owner, profile, suspended: false, section: '', view: popout.view } : undefined;
  const competingOwner = `${profileKey('another-login')}:${popoutScopeId(tab)}`;
  const browser = Object.assign(Object.create(NativeBrowser.prototype), {
    origin,
    views: { entryForContents: (contents) => contents === sender ? entry : null, tabForKey: () => tab },
    profiles: new Map([[profile.key, profile]]),
    viewHosts: new Map([[view, appHost]]),
    popouts: new Map([[competingOwner, { ...popout, owner: competingOwner }], [owner, popout]]),
    tabs: browserTab ? new Map([[browserTab.id, browserTab]]) : new Map(),
    selected: browserTab ? new Map([[owner, browserTab.id]]) : new Map(),
    features: new Map(),
    retention: { resume() {} },
    registry: {
      clearDownloadAttribution() {},
      load: (current, url) => events.push(['navigate', current.id, url]),
      create: (url, options) => events.push(['create', options.owner, url])
    },
    snapshot() {},
    layout() {}
  });
  sender.session = { fetch: async () => ({ ok: true, json: async () => ({ success: true, session: { logged_in: true, username: 'signed-in' } }) }) };
  return { browser, event, events, entry, owner, sender };
}

test('a conversation without an embedded browser lease uses its own minimized popout', async () => {
  const { browser, event, events } = fixture();
  assert.equal(await browser.openConversationLink(event, outside), true);
  assert.deepEqual(events, ['restore', 'show', 'focus', ['navigate', 'previous-page', outside]]);
});

test('a popout with no selected page opens the link in a browser tab', async () => {
  const { browser, event, events, owner } = fixture({ selected: false });
  assert.equal(await browser.openConversationLink(event, outside), true);
  assert.deepEqual(events.at(-1), ['create', owner, outside]);
});

test('other profiles cannot handle the link, and a hidden view cannot finish a pending route', async () => {
  const other = fixture();
  other.browser.popouts.delete(other.owner);
  assert.equal(await other.browser.openConversationLink(other.event, outside), false);
  assert.deepEqual(other.events, []);

  const pending = fixture();
  const pendingOpen = pending.browser.openConversationLink(pending.event, outside);
  pending.entry.shown = false;
  assert.equal(await pendingOpen, false);
  assert.deepEqual(pending.events, []);
});

test('rejects iframe, same-origin, credentialed, and malformed destinations', async () => {
  const { browser, event, events } = fixture();
  await assert.rejects(browser.openConversationLink({ ...event, senderFrame: { url: event.senderFrame.url } }, outside), /untrusted_browser_sender/);
  for (const url of [`${origin}/settings`, 'https://user:password@example.com/', 'https://', 'file:///tmp/secret']) {
    assert.equal(await browser.openConversationLink(event, url), false);
  }
  assert.deepEqual(events, []);
});
