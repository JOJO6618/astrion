// 行内引用悬停卡：悬停／点击状态机（citationChips.ts）。
// 重点回归「打开第一个后快速移到第二个，旧卡片原地换成第二个的内容」：
// 每次打开必须是新的卡片会话（新 id），旧卡片由 Transition 沿原路收回。
// 运行：node test/2026-11-18_引用悬停卡/citation_hover.cjs
const assert = require('node:assert/strict');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');

/* ---------- 假时钟 ---------- */
function createClock() {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  return {
    setTimeout: (fn, ms) => {
      const id = ++seq;
      timers.set(id, { fn, at: now + (ms || 0) });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    performance: { now: () => now },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()]
          .filter(([, t]) => t.at <= end)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
      }
      now = end;
    }
  };
}

function load() {
  const clock = createClock();
  const mod = compile(
    'static/src/components/chat/citationChips.ts',
    {
      vue: { reactive: (o) => o },
      './citationFavicon': { faviconFallbackHtml: () => '', upgradeCitationFavicons: () => {} }
    },
    { setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout, performance: clock.performance }
  );
  return { ...mod, clock };
}

function chip(name) {
  const classes = new Set();
  return {
    name,
    _citationAnnotations: [{ id: name, type: 'url_citation', domain: `${name}.example` }],
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
    isOpen: () => classes.has('is-open')
  };
}

const cases = [];
const test = (name, fn) => cases.push([name, fn]);

test('cold hover opens after 250ms, leaving closes after 300ms', () => {
  const m = load();
  const a = chip('a');
  m.hoverCitationChip(a);
  m.clock.advance(249);
  assert.equal(m.citationPopover.current, null);
  m.clock.advance(1);
  assert.equal(m.citationPopover.current.anchor, a);
  assert.ok(a.isOpen());
  m.leaveCitationChip(a);
  m.clock.advance(299);
  assert.ok(m.citationPopover.current);
  m.clock.advance(1);
  assert.equal(m.citationPopover.current, null);
  assert.ok(!a.isOpen());
});

test('moving to another chip opens a NEW card session at the new anchor (bug regression)', () => {
  const m = load();
  const a = chip('a');
  const b = chip('b');
  m.hoverCitationChip(a);
  m.clock.advance(250);
  const first = m.citationPopover.current;
  m.leaveCitationChip(a);
  m.hoverCitationChip(b);
  // 热状态：80ms 内旧卡片保持不变，不能被改写成 b 的内容
  m.clock.advance(79);
  assert.equal(m.citationPopover.current, first);
  assert.equal(first.anchor, a);
  assert.equal(first.annotations[0].id, 'a');
  m.clock.advance(1);
  const second = m.citationPopover.current;
  assert.notEqual(second, first);
  assert.notEqual(second.id, first.id);
  assert.equal(second.anchor, b);
  assert.equal(second.annotations[0].id, 'b');
  // 旧会话对象本身不被改写（离场动画中的旧卡片保持 a 的内容）
  assert.equal(first.anchor, a);
  assert.equal(first.annotations[0].id, 'a');
  assert.ok(!a.isOpen());
  assert.ok(b.isOpen());
});

test('entering another chip does not keep the old card alive', () => {
  const m = load();
  const a = chip('a');
  const b = chip('b');
  m.hoverCitationChip(a);
  m.clock.advance(250);
  m.leaveCitationChip(a);
  m.hoverCitationChip(b);
  m.leaveCitationChip(b); // 只是扫过 b，没有停留到 80ms
  m.clock.advance(300);
  assert.equal(m.citationPopover.current, null);
});

test('passing over another chip on the way into the card does not switch', () => {
  const m = load();
  const a = chip('a');
  const b = chip('b');
  m.hoverCitationChip(a);
  m.clock.advance(250);
  const first = m.citationPopover.current;
  m.leaveCitationChip(a);
  m.hoverCitationChip(b);
  m.clock.advance(30);
  m.keepCitationPopover(); // 指针进入卡片
  m.clock.advance(1000);
  assert.equal(m.citationPopover.current, first);
});

test('warm window: shortly after closing, the next hover opens in 80ms; later it is cold again', () => {
  const m = load();
  const a = chip('a');
  const b = chip('b');
  m.hoverCitationChip(a);
  m.clock.advance(250);
  m.closeCitationPopover();
  m.clock.advance(100);
  m.hoverCitationChip(b);
  m.clock.advance(80);
  assert.equal(m.citationPopover.current.anchor, b);
  m.closeCitationPopover();
  m.clock.advance(1000);
  m.hoverCitationChip(a);
  m.clock.advance(80);
  assert.equal(m.citationPopover.current, null);
  m.clock.advance(170);
  assert.equal(m.citationPopover.current.anchor, a);
});

test('click pins in place; pinned card ignores hover on other chips; click again closes', () => {
  const m = load();
  const a = chip('a');
  const b = chip('b');
  m.hoverCitationChip(a);
  m.clock.advance(250);
  const card = m.citationPopover.current;
  m.clickCitationChip(a);
  assert.equal(m.citationPopover.current, card, 'hover-opened card is pinned in place, not reopened');
  assert.equal(card.pinned, true);
  m.leaveCitationChip(a);
  m.hoverCitationChip(b);
  m.clock.advance(1000);
  assert.equal(m.citationPopover.current, card);
  m.clickCitationChip(a);
  assert.equal(m.citationPopover.current, null);
});

test('clicking another chip while pinned switches to a new pinned card', () => {
  const m = load();
  const a = chip('a');
  const b = chip('b');
  m.clickCitationChip(a);
  const first = m.citationPopover.current;
  assert.equal(first.pinned, true);
  m.clickCitationChip(b);
  const second = m.citationPopover.current;
  assert.notEqual(second.id, first.id);
  assert.equal(second.anchor, b);
  assert.equal(second.pinned, true);
  assert.ok(!a.isOpen());
});

let failed = 0;
for (const [name, fn] of cases) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    failed++;
    console.log(`FAIL ${name}\n     ${err.message}`);
  }
}
console.log(`\n${cases.length - failed}/${cases.length} passed`);
process.exit(failed ? 1 : 0);
