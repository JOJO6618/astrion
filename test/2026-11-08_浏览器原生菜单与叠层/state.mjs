import assert from 'node:assert/strict';
import { BrowserStacking } from '../../desktop-electron/src/browser/stacking.js';
import { relayBrowserInput } from '../../desktop-electron/src/browser/input.js';
import { displayBrowserAddress } from '../../static/src/components/browser/address.ts';

const colors = [], inputs = [];
let focused = true, mainFocuses = 0, pageFocuses = 0;
const main = {
  setBackgroundColor: value => colors.push(value),
  webContents: { focus: () => { mainFocuses++; focused = false; }, getZoomFactor: () => 1, isDestroyed: () => false },
  getBounds: () => ({ x: 0, y: 46, width: 1200, height: 754 })
};
const page = {
  visible: true,
  getVisible() { return this.visible; },
  getBounds: () => ({ x: 600, y: 130, width: 600, height: 670 }),
  webContents: {
    isDestroyed: () => false, isFocused: () => focused,
    focus: () => { pageFocuses++; focused = true; },
    sendInputEvent: value => inputs.push(value)
  }
};
const children = [main, page];
const tab = { id: 'fixture-tab', owner: 'fixture-owner', view: page };
// 2026-11-11 起叠层与输入转发按宿主（主窗口/对话独立窗口）计算，夹具即一个宿主。
const manager = {
  view: main, window: { isDestroyed: () => false, contentView: { children, addChildView(view) { children.splice(children.indexOf(view), 1); children.push(view); } } },
  owner: tab.owner, active: () => tab, visible: true, manager: { surfaceColor: '#ffffffff' },
  geometry: { x: 600, y: 84, width: 600, height: 670 },
  overlayRaised: false, overlayCapturesInput: true
};
const stack = new BrowserStacking(manager);
stack.apply(tab);
assert.deepEqual(colors, ['#00000000']);
assert.equal(children.at(-1), page);
manager.overlayRaised = true;
stack.apply(tab);
assert.equal(children.at(-1), main);
assert.equal(page.visible, true);
assert.equal(mainFocuses, 1);
stack.apply(tab);
assert.equal(mainFocuses, 1);
manager.overlayCapturesInput = false;
stack.apply(tab);
assert.equal(pageFocuses, 1);
relayBrowserInput(manager, { tabId: tab.id, type: 'mouseDown', x: 610, y: 100, button: 0 });
assert.equal(inputs.length, 1);
assert.deepEqual({ x: inputs[0].x, y: inputs[0].y }, { x: 10, y: 16 });
relayBrowserInput(manager, { tabId: 'stale-tab', type: 'mouseUp', x: 610, y: 100 });
assert.equal(inputs.length, 1);
manager.overlayRaised = false;
stack.apply(tab);
assert.equal(children.at(-1), page);
assert.deepEqual(colors, ['#00000000']);
page.visible = false;
stack.apply(tab);
assert.equal(children.at(-1), main);
assert.deepEqual(colors, ['#00000000', '#ffffffff']);
assert.equal(stack.transparent, false);
assert.equal(displayBrowserAddress('https://github.com/'), 'github.com');
assert.equal(displayBrowserAddress('https://github.com/a/?q=1#b'), 'github.com/a?q=1#b');
assert.equal(displayBrowserAddress('http://localhost:8000/'), 'http://localhost:8000');
assert.equal(displayBrowserAddress('about:blank'), '');
console.log('PASS: layer ordering, stable transparency, focus, input ownership/coordinates, address display (state simulation; not native compositing).');
