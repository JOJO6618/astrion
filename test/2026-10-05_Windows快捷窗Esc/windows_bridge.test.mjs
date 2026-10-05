import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../../static/quick-capture/windows-bridge.js', import.meta.url), 'utf8');
function fixture() {
  const events = [], timers = [], listeners = new Map();
  const input = { tagName: 'TEXTAREA' };
  const document = {
    activeElement: input, hasFocus: () => true,
    addEventListener: (name, callback) => listeners.set(name, callback),
    dispatchEvent: event => events.push(event)
  };
  const window = {};
  vm.runInNewContext(source, {
    window, document, location: { pathname: '/quick' }, console,
    performance: { now: () => 1000 }, setTimeout: callback => timers.push(callback),
    fetch: async () => ({ ok: true, json: async () => ({ ok: true }) }),
    KeyboardEvent: class { constructor(type, options) { this.type = type; Object.assign(this, options); } }
  });
  return { window, events, listeners, flush: () => timers.splice(0).forEach(fn => fn()) };
}
test('Falls back even when WebView2 claims focused textarea but no real Esc arrived', () => {
  const f = fixture();
  f.window.__astrionQuickEmit('native-escape'); f.flush();
  assert.equal(f.events.length, 1); assert.equal(f.events[0].key, 'Escape');
});
test('Real DOM Esc prevents duplicate fallback and preserves menu handling', () => {
  const f = fixture();
  f.window.__astrionQuickEmit('native-escape');
  f.listeners.get('keydown')({ key: 'Escape' }); f.flush();
  assert.equal(f.events.length, 0);
});
test('IME composition blocks fallback Escape', () => {
  const f = fixture(); f.listeners.get('compositionstart')();
  f.window.__astrionQuickEmit('native-escape'); f.flush();
  assert.equal(f.events.length, 0);
  f.listeners.get('compositionend')();
  f.window.__astrionQuickEmit('native-escape'); f.flush();
  assert.equal(f.events.length, 1);
});
