/* 交互逻辑集成自检：用极简 DOM / p5 桩件执行 sketch.js，不启动浏览器。 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(rootDir, 'index.html'), 'utf8');
const results = { steps: {}, failures: [], errors: [] };

/* ---------------- 假 DOM ---------------- */
function El(id, tag) {
  this.id = id;
  this.tagName = (tag || 'div').toUpperCase();
  this.value = '';
  this.textContent = '';
  this.attributes = {};
  this.listeners = {};
  this.clicked = 0;
  this.style = {
    _props: {},
    setProperty(k, v) { this._props[k] = v; }
  };
}
El.prototype.addEventListener = function (type, fn) {
  (this.listeners[type] = this.listeners[type] || []).push(fn);
};
El.prototype.setAttribute = function (k, v) { this.attributes[k] = String(v); };
El.prototype.getAttribute = function (k) { return this.attributes[k]; };
El.prototype.appendChild = function () {};
El.prototype.removeChild = function () {};
El.prototype.remove = function () { this.removed = true; };
El.prototype.click = function () {
  this.clicked++;
  (this.listeners.click || []).forEach((fn) => fn({ target: this }));
};
El.prototype.fire = function (type, ev) {
  (this.listeners[type] || []).forEach((fn) => fn(ev || { target: this }));
};

const registry = {};
const tagById = {};
for (const m of html.matchAll(/<(\w+)([^>]*?)\sid="([^"]+)"/g)) tagById[m[3]] = m[1];
for (const m of html.matchAll(/\sid="([^"]+)"/g)) {
  if (!registry[m[1]]) registry[m[1]] = new El(m[1], tagById[m[1]]);
}
const stage = registry['stage'];
stage.clientWidth = 912;
registry['seed-input'].value = 'tangent-01';
registry['tide-range'].value = '1';
registry['tide-count'].value = '9';
registry['density'].value = '1';

const created = [];
const documentStub = {
  readyState: 'complete',
  body: new El('body', 'body'),
  getElementById: (id) => registry[id] || null,
  createElement: (tag) => {
    const el = new El('created-' + tag, tag);
    created.push(el);
    return el;
  },
  addEventListener: function (type, fn) {
    this._listeners = this._listeners || {};
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  },
  fire: function (type, ev) {
    ((this._listeners || {})[type] || []).forEach((fn) => fn(ev));
  }
};
let objectUrlRevoked = 0;
const URLstub = {
  createObjectURL: () => 'blob:fake',
  revokeObjectURL: () => { objectUrlRevoked++; }
};

/* ---------------- 假 p5 ---------------- */
function fakeCtx() {
  const c = {
    font: '10px serif', fillStyle: '', strokeStyle: '', lineWidth: 1,
    lineCap: 'butt', lineJoin: 'miter', globalAlpha: 1,
    globalCompositeOperation: 'source-over', textAlign: 'left', textBaseline: 'alphabetic'
  };
  const noop = () => {};
  for (const m of ['save', 'restore', 'beginPath', 'closePath', 'moveTo', 'lineTo', 'stroke',
    'fill', 'fillRect', 'strokeRect', 'arc', 'ellipse', 'translate', 'rotate', 'scale', 'fillText']) {
    c[m] = noop;
  }
  c.measureText = (s) => ({ width: String(s).length * 5 });
  const grad = () => ({ addColorStop: noop });
  c.createLinearGradient = grad;
  c.createRadialGradient = grad;
  return c;
}

let graphicsCreated = 0;
let p5instance = null;
function makeCanvas(w, h) {
  return {
    width: w, height: h,
    attributes: {},
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k]; },
    toBlob(cb) { setTimeout(() => cb({ size: 12345, type: 'image/png' }), 0); },
    toDataURL() { return 'data:image/png;base64,AAAA'; },
    style: {}
  };
}

function P5(sketch) {
  p5instance = this;
  this.deltaTime = 16;
  this.canvas = null;
  this._looping = false;
  this._imageCalls = 0;
  this._redraws = 0;
  sketch(this);
  if (typeof this.setup === 'function') this.setup();
}
P5.prototype.pixelDensity = function (d) { this._density = d; };
P5.prototype.resizeCanvas = function (w, h) {
  this._canvasW = w;
  this._canvasH = h;
  if (this.canvas) { this.canvas.width = w; this.canvas.height = h; }
  this._resizes = (this._resizes || 0) + 1;
};
P5.prototype.createCanvas = function (w, h) {
  this.canvas = makeCanvas(w, h);
  this._canvasW = w;
  this._canvasH = h;
  return { elt: this.canvas, parent: function () { return this; } };
};
P5.prototype.createGraphics = function (w, h) {
  graphicsCreated++;
  const cv = makeCanvas(w, h);
  return {
    canvas: cv,
    drawingContext: fakeCtx(),
    pixelDensity: function () {},
    clear: function () {},
    remove: function () { this.removed = true; }
  };
};
P5.prototype.loop = function () { this._looping = true; };
P5.prototype.noLoop = function () { this._looping = false; };
P5.prototype.redraw = function () { this._redraws++; if (this.draw) this.draw(); };
P5.prototype.image = function () { this._imageCalls++; };

/* ---------------- 上下文 ---------------- */
const sandbox = {
  console, Math, JSON, Date, setTimeout, clearTimeout,
  URL: URLstub,
  document: documentStub,
  devicePixelRatio: 2,
  innerWidth: 1440,
  innerHeight: 900,
  p5: P5,
  matchMedia: () => ({ matches: false, addListener() {}, removeListener() {} }),
  getComputedStyle: () => ({ paddingLeft: '0px', paddingRight: '0px' }),
  crypto: { getRandomValues(arr) { arr[0] = 987654321; return arr; } },
  addEventListener: function (type, fn) {
    this._listeners = this._listeners || {};
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  },
  removeEventListener: function () {},
  fire: function (type, ev) {
    ((this._listeners || {})[type] || []).forEach((fn) => fn(ev));
  }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

try {
  for (const f of ['js/archive.js', 'js/painter.js', 'js/sketch.js']) {
    vm.runInContext(fs.readFileSync(path.join(rootDir, f), 'utf8'), sandbox, { filename: f });
  }
} catch (err) {
  results.errors.push('load: ' + err.stack);
}

function check(name, cond, detail) {
  if (!cond) results.failures.push({ name, detail: detail === undefined ? null : detail });
}
function tick(n, dt) {
  for (let i = 0; i < n; i++) {
    p5instance.deltaTime = dt;
    p5instance.draw();
  }
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

(async function run() {
  try {
    const inst = p5instance;
    results.steps.init = {
      canvas: inst._canvasW + '×' + inst._canvasH,
      pixelDensity: inst._density,
      looping: inst._looping,
      status: registry['status'].textContent,
      playLabel: registry['toggle-play'].textContent,
      lines: registry['stat-lines'].textContent,
      grains: registry['stat-grains'].textContent,
      ariaLabel: inst.canvas ? inst.canvas.getAttribute('aria-label') : null,
      sliderFill: registry['density'].style._props['--fill']
    };
    check('init.canvas', inst._canvasW === 570 && inst._canvasH === 684, results.steps.init.canvas);
    check('init.autoplay', inst._looping === true);
    check('init.readout', registry['stat-lines'].textContent === '9' && Number(registry['stat-grains'].textContent.replace(/,/g, '')) > 1000, registry['stat-grains'].textContent);
    check('init.aria', /种子 tangent-01/.test(inst.canvas.getAttribute('aria-label') || ''));

    /* 播完 */
    tick(101, 90);
    results.steps.completed = {
      status: registry['status'].textContent,
      playLabel: registry['toggle-play'].textContent,
      looping: inst._looping,
      stateText: registry['stat-state'].textContent,
      imageDraws: inst._imageCalls
    };
    check('anim.completes', registry['status'].textContent === '档案完成', registry['status'].textContent);
    check('anim.stopsLoop', inst._looping === false);
    check('anim.labelReplay', registry['toggle-play'].textContent === '重播');

    /* 重绘 */
    registry['replay'].click();
    results.steps.replay = {
      status: registry['status'].textContent,
      looping: inst._looping,
      playLabel: registry['toggle-play'].textContent
    };
    check('replay.restarts', /正在沉积 0%/.test(registry['status'].textContent) && inst._looping === true);
    tick(45, 90);

    /* 暂停/继续 */
    registry['toggle-play'].click();
    const paused = { status: registry['status'].textContent, looping: inst._looping, label: registry['toggle-play'].textContent };
    tick(3, 90); /* 暂停时不应推进 */
    const pausedAfterTicks = registry['status'].textContent;
    registry['toggle-play'].click();
    results.steps.pauseResume = { paused, afterTicks: pausedAfterTicks, resumedStatus: registry['status'].textContent, resumedLoop: inst._looping, resumedLabel: registry['toggle-play'].textContent };
    check('pause.stops', paused.looping === false && /已暂停/.test(paused.status), paused.status);
    check('pause.holds', pausedAfterTicks === paused.status, pausedAfterTicks);
    check('resume.starts', inst._looping === true && /正在沉积/.test(registry['status'].textContent), registry['status'].textContent);
    check('resume.label', registry['toggle-play'].textContent === '暂停');
    tick(70, 90);
    check('resume.completes', registry['status'].textContent === '档案完成', registry['status'].textContent);

    /* 种子应用（回车） */
    registry['seed-input'].value = 'spring-2048';
    registry['seed-input'].fire('keydown', { key: 'Enter', preventDefault() {} });
    results.steps.seedApply = {
      status: registry['status'].textContent,
      aria: inst.canvas.getAttribute('aria-label')
    };
    check('seed.applies', /种子 spring-2048/.test(inst.canvas.getAttribute('aria-label') || ''));
    tick(101, 90);

    /* 参数变更（滑块 + 防抖） */
    registry['tide-count'].value = '14';
    registry['tide-count'].fire('input', { target: registry['tide-count'] });
    const immediate = registry['tide-count-val'].textContent;
    await wait(220);
    results.steps.slider = {
      outputImmediate: immediate,
      status: registry['status'].textContent,
      aria: inst.canvas.getAttribute('aria-label'),
      lines: registry['stat-lines'].textContent,
      looping: inst._looping
    };
    check('slider.output', immediate === '14');
    check('slider.rebuilds', /14 道水线/.test(inst.canvas.getAttribute('aria-label') || ''));
    check('slider.fullRender', registry['status'].textContent === '档案完成');

    /* 导出 */
    const beforeGraphics = graphicsCreated;
    registry['export'].click();
    await wait(120);
    const anchor = created.filter((el) => el.tagName === 'A').pop();
    results.steps.export = {
      graphicsCreated: graphicsCreated - beforeGraphics,
      anchorClicked: anchor ? anchor.clicked : 0,
      filename: anchor ? anchor.download : null,
      status: registry['status'].textContent
    };
    check('export.starts', graphicsCreated - beforeGraphics >= 1);
    check('export.downloads', !!anchor && anchor.clicked === 1 && /^tidal-archive_.+\.png$/.test(anchor.download || ''));
    check('export.status', /已导出/.test(registry['status'].textContent));

    /* 导出后的状态恢复 */
    await wait(3400);
    results.steps.exportSettled = { status: registry['status'].textContent };
    check('export.statusRestores', registry['status'].textContent === '档案完成');

    /* 键盘：空格重播 */
    documentStub.fire('keydown', { code: 'Space', key: ' ', target: { tagName: 'BODY' }, preventDefault() {} });
    results.steps.keyboard = { status: registry['status'].textContent, looping: inst._looping };
    check('keyboard.space', /正在沉积 0%/.test(registry['status'].textContent), registry['status'].textContent);

    /* 输入框内空格不应触发暂停 */
    registry['seed-input'].fire('keydown', { code: 'Space', key: ' ', target: { tagName: 'INPUT' }, preventDefault() {} });
    results.steps.keyboardTyping = { status: registry['status'].textContent };
    check('keyboard.ignoresTyping', /正在沉积 0%/.test(registry['status'].textContent), registry['status'].textContent);

    /* 窄屏尺寸 */
    stage.clientWidth = 354;
    sandbox.innerWidth = 390;
    sandbox.innerHeight = 844;
    sandbox.fire('resize', {});
    await wait(320);
    results.steps.resize = {
      canvas: inst._canvasW + '×' + inst._canvasH,
      status: registry['status'].textContent
    };
    check('resize.narrow', inst._canvasW === 354 && inst._canvasH === 425, results.steps.resize.canvas);
  } catch (err) {
    results.errors.push('run: ' + err.stack);
  }
  console.log(JSON.stringify(results, null, 2));
  process.exitCode = (results.errors.length || results.failures.length) ? 1 : 0;
})();
