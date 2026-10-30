const assert = require('node:assert/strict');
const test = require('node:test');
const {
  Element, Observer, flush, mainList, createElementScrollFollow,
  scrollThinkingToBottom, handleThinkingScroll, disposeThinkingScrollFollow
} = require('./helpers.cjs');

function thinking() {
  const el = new Element();
  const ctx = {
    thinkingScrollLocks: new Map([['thought', true]]),
    getThinkingContentElement: () => el,
    chatSetThinkingLock(id, locked) { this.thinkingScrollLocks.set(id, locked); }
  };
  const follow = () => scrollThinkingToBottom(ctx, 'thought');
  return { el, ctx, follow, locked: () => ctx.thinkingScrollLocks.get('thought'),
    dispose: () => disposeThinkingScrollFollow(el) };
}

test('streaming follows the DOM committed after the chunk, without false escape', async () => {
  const h = thinking();
  h.follow();
  h.el.scrollHeight = 1300;
  await flush();
  assert.equal(h.el.scrollTop, 1100);
  for (let i = 0; i < 100; i++) {
    h.follow();
    h.el.scrollHeight += 27;
    h.el.fire('scroll');
    handleThinkingScroll(h.ctx, 'thought', { target: h.el });
    await flush();
    assert.equal(h.locked(), true);
    assert.equal(h.el.scrollTop, h.el.scrollHeight - h.el.clientHeight);
  }
  h.dispose();
});

test('async Markdown layout continues following without another model chunk', async () => {
  const h = thinking();
  h.el.firstElementChild = new Element(h.el.ownerDocument);
  h.follow(); await flush();
  const observer = Observer.instances.at(-1);
  assert.ok(observer.targets.has(h.el.firstElementChild));
  h.el.scrollHeight += 250;
  observer.notify();
  await flush();
  assert.equal(h.el.scrollTop, 1050);
  assert.equal(h.locked(), true);
  h.dispose();
});

test('wheel-up cancels an already queued follow and growth preserves reading position', async () => {
  const h = thinking();
  h.follow(); await flush();
  h.follow();
  h.el.fire('wheel', { deltaY: -60 });
  h.el.scrollTop = 600;
  h.el.fire('scroll');
  h.el.scrollHeight += 300;
  await flush();
  assert.equal(h.locked(), false);
  assert.equal(h.el.scrollTop, 600);
  h.dispose();
});

test('user downward scroll near the bottom restores thinking follow', async () => {
  const h = thinking();
  h.follow(); await flush();
  h.el.fire('wheel', { deltaY: -60 });
  h.el.scrollTop = 600; h.el.fire('scroll');
  h.el.fire('wheel', { deltaY: 190 });
  h.el.scrollTop = 790; h.el.fire('scroll');
  await flush();
  assert.equal(h.locked(), true);
  assert.equal(h.el.scrollTop, 800);
  h.dispose();
});

test('layout shrink does not release thinking follow', async () => {
  const h = thinking();
  h.follow(); await flush();
  h.el.scrollHeight = 650; h.el.scrollTop = 430;
  h.el.fire('scroll');
  assert.equal(h.locked(), true);
  Observer.instances.at(-1).notify();
  await flush();
  assert.equal(h.el.scrollTop, 450);
  h.dispose();
});

test('nested scrolling keeps the wrapper follow and disposal removes all listeners', async () => {
  const wrapper = new Element();
  const child = new Element(wrapper.ownerDocument); child.parentElement = wrapper;
  const follow = createElementScrollFollow(wrapper);
  wrapper.fire('wheel', { target: child, deltaY: -80 });
  wrapper.scrollHeight += 300;
  follow.requestFollow(); await flush();
  assert.equal(wrapper.scrollTop, 1100);
  follow.dispose();
  assert.equal(wrapper.count(), 0);
  assert.equal(wrapper.ownerDocument.count(), 0);
  assert.equal(Observer.instances.at(-1).targets.size, 0);
});

test('touch reading stops following, reset restores it, detached callbacks stop writing', async () => {
  const wrapper = new Element();
  const follow = createElementScrollFollow(wrapper);
  wrapper.fire('touchstart', { touches: [{ clientY: 100 }] });
  wrapper.fire('touchmove', { touches: [{ clientY: 140 }] });
  wrapper.scrollTop = 600;
  wrapper.scrollHeight += 200;
  follow.requestFollow(); await flush();
  assert.equal(wrapper.scrollTop, 600);
  follow.reset(); await flush();
  assert.equal(wrapper.scrollTop, 1000);
  follow.requestFollow(); wrapper.isConnected = false; wrapper.scrollHeight += 300;
  await flush(); assert.equal(wrapper.scrollTop, 1000);
  follow.dispose();
});

test('switching the thought owner before Vue flush discards the old write', async () => {
  const h = thinking();
  h.follow();
  h.ctx.getThinkingContentElement = () => null;
  h.el.scrollHeight += 500;
  await flush(); assert.equal(h.el.scrollTop, 800);
  h.dispose();
});

test('main follow observes both message content and approval bottom spacer', async () => {
  const h = mainList();
  assert.ok(h.observer.targets.has(h.content));
  assert.ok(h.observer.targets.has(h.spacer));
  h.el.scrollHeight += 300;
  h.observer.notify(); await flush();
  assert.equal(h.el.scrollTop, 1100);
  assert.equal(h.read(), 'locked');
});

test('approval spacer shrink and viewport resize cannot trigger a large-up escape', async () => {
  const h = mainList();
  h.spacer.offsetHeight = 0;
  h.move(440, 700);
  await flush();
  assert.equal(h.read(), 'locked');
  assert.equal(h.el.scrollTop, 500);
  h.clearHint(); h.move(370, 700, 280);
  await flush();
  assert.equal(h.read(), 'locked');
  assert.equal(h.el.scrollTop, 420);
});

test('programmatic virtualizer correction keeps main follow locked', async () => {
  const h = mainList();
  h.observer.notify(); await flush();
  h.move(650);
  await flush();
  assert.equal(h.read(), 'locked');
  assert.equal(h.el.scrollTop, 800);
});

test('escaped main reading is not pulled to bottom by approval layout changes', async () => {
  const h = mainList();
  h.el.fire('wheel', { deltaY: -80 });
  h.move(450);
  h.spacer.offsetHeight = 0;
  h.el.scrollHeight = 900;
  h.observer.notify(); await flush();
  assert.equal(h.read(), 'escaped');
  assert.equal(h.el.scrollTop, 450);
});

test('nested thought wheel does not unlock the main conversation', () => {
  const h = mainList();
  h.el.fire('wheel', { target: new Element(h.el.ownerDocument), deltaY: -80 });
  assert.equal(h.read(), 'locked');
});

test('main scrollbar drag explicitly escapes even while streaming writes are recent', () => {
  const h = mainList();
  h.el.fire('pointerdown', { button: 0, clientX: 308 });
  assert.equal(h.read(), 'escaped');
});

const { minimalGroup } = require('./helpers.cjs');

test('Vue wrapper ref updates do not duplicate listeners and replacement cleans the old element', () => {
  const h = minimalGroup();
  const first = new Element();
  h.registerStepsWrapper('group', first);
  const count = first.count();
  h.registerStepsWrapper('group', first);
  h.registerStepsWrapper('group', first);
  assert.equal(first.count(), count);
  const replacement = new Element();
  h.registerStepsWrapper('group', replacement);
  assert.equal(first.count(), 0);
  assert.equal(first.ownerDocument.count(), 0);
  h.registerStepsWrapper('group', null);
  assert.equal(replacement.count(), 0);
  assert.equal(replacement.ownerDocument.count(), 0);
});

test('expanding a completed group preserves its previous reading position', async () => {
  const h = minimalGroup();
  const wrapper = new Element(); wrapper.scrollTop = 300;
  h.setRunning(false);
  h.registerStepsWrapper('group', wrapper);
  Observer.instances.at(-1).notify();
  await flush();
  assert.equal(wrapper.scrollTop, 300);
  h.registerStepsWrapper('group', null);
});

test('keyboard up still escapes the main follow during streaming', async () => {
  const h = mainList();
  h.observer.notify(); await flush();
  h.el.ownerDocument.fire('keydown', { target: h.el, key: 'PageUp' });
  assert.equal(h.read(), 'escaped');
});

test('inner keyboard up pauses thinking follow before the DOM write', async () => {
  const h = thinking();
  h.follow(); await flush();
  h.follow();
  h.el.fire('keydown', { key: 'ArrowUp' });
  h.el.scrollTop = 600;
  await flush();
  assert.equal(h.locked(), false);
  assert.equal(h.el.scrollTop, 600);
  h.dispose();
});

test('spacer-only layout movement keeps main follow even if total scrollHeight is unchanged', async () => {
  const h = mainList();
  h.spacer.offsetHeight = 0;
  h.move(620);
  await flush();
  assert.equal(h.read(), 'locked');
  assert.equal(h.el.scrollTop, 800);
});

test('main scrollbar can return to locked immediately after a recent programmatic write', async () => {
  const h = mainList();
  h.observer.notify(); await flush();
  h.el.fire('pointerdown', { button: 0, clientX: 308 });
  h.move(600);
  assert.equal(h.read(), 'escaped');
  h.move(800);
  assert.equal(h.read(), 'locked');
});

test('inner scrollbar drag resumes follow after release at the bottom', async () => {
  const h = thinking();
  h.follow(); await flush();
  h.el.fire('pointerdown', { button: 0, clientX: 308 });
  h.el.scrollTop = 500; h.el.fire('scroll');
  assert.equal(h.locked(), false);
  h.el.scrollTop = 795; h.el.fire('scroll');
  await flush(); assert.equal(h.el.scrollTop, 795);
  h.el.ownerDocument.fire('pointerup');
  await flush();
  assert.equal(h.locked(), true);
  assert.equal(h.el.scrollTop, 800);
  h.dispose();
});
