const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const preload = path.resolve(__dirname, '../../desktop-electron/src/preload-chrome.cjs');
for (const platform of ['win32', 'darwin']) {
  test(`chrome preload publishes desktop identity and ${platform} before rendering`, () => {
    const exposed = new Map();
    vm.runInNewContext(fs.readFileSync(preload, 'utf8'), {
      process: { platform },
      require(name) {
        assert.equal(name, 'electron');
        return { contextBridge: { exposeInMainWorld: (key, value) => exposed.set(key, value) } };
      }
    }, { filename: preload });
    assert.equal(exposed.get('__ASTRION_DESKTOP__'), true);
    assert.equal(exposed.get('__ASTRION_CHROME__'), true);
    assert.equal(exposed.get('__ASTRION_PLATFORM__'), platform);
    assert.equal(exposed.size, 3, 'The chrome renderer receives only its identity markers');
  });
}
