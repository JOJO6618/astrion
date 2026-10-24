/* 非视觉自检：在 Node 中用记录型假 Canvas 上下文跑完整画家逻辑。 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '..');
const ctx = { console: console, Math: Math, JSON: JSON };
ctx.globalThis = ctx;
vm.createContext(ctx);
for (const f of ['js/archive.js', 'js/painter.js']) {
  vm.runInContext(fs.readFileSync(path.join(rootDir, f), 'utf8'), ctx, { filename: f });
}
const A = ctx.TidalArchive;
const Painter = ctx.TidalPainter.Painter;

function makeFakeCtx() {
  const st = { calls: 0, strokes: 0, fills: 0, texts: 0, gradients: 0, bad: [], outOfRange: [], minR: Infinity };
  const c = {
    font: '10px serif', fillStyle: '#000', strokeStyle: '#000', lineWidth: 1,
    lineCap: 'butt', lineJoin: 'miter', globalAlpha: 1,
    globalCompositeOperation: 'source-over', textAlign: 'left', textBaseline: 'alphabetic'
  };
  function bad(name, v) {
    if (typeof v !== 'number' || !Number.isFinite(v)) st.bad.push(name + '=' + String(v));
  }
  function clampCheck(name, v, lo, hi) {
    if (typeof v === 'number' && Number.isFinite(v) && (v < lo || v > hi)) {
      st.outOfRange.push(name + '=' + v.toFixed(3));
    }
  }
  const noop = () => { st.calls++; };
  c.save = () => { st.calls++; };
  c.restore = () => { st.calls++; };
  c.beginPath = () => { st.calls++; };
  c.closePath = () => { st.calls++; };
  c.translate = (x, y) => { st.calls++; bad('translate.x', x); bad('translate.y', y); };
  c.rotate = (a) => { st.calls++; bad('rotate', a); };
  c.scale = (x, y) => { st.calls++; bad('scale.x', x); bad('scale.y', y); };
  c.moveTo = (x, y) => { st.calls++; bad('moveTo.x', x); bad('moveTo.y', y); };
  c.lineTo = (x, y) => { st.calls++; bad('lineTo.x', x); bad('lineTo.y', y); };
  c.stroke = () => { st.calls++; st.strokes++; };
  c.fill = () => { st.calls++; st.fills++; };
  c.fillRect = (x, y, w, h) => {
    st.calls++;
    bad('fillRect.x', x); bad('fillRect.y', y); bad('fillRect.w', w); bad('fillRect.h', h);
  };
  c.strokeRect = (x, y, w, h) => {
    st.calls++;
    bad('strokeRect.x', x); bad('strokeRect.y', y); bad('strokeRect.w', w); bad('strokeRect.h', h);
  };
  c.arc = (x, y, r, a0, a1) => {
    st.calls++;
    bad('arc.x', x); bad('arc.y', y); bad('arc.r', r);
    if (typeof r === 'number' && r < 0) st.bad.push('arc.r<0');
    st.minR = Math.min(st.minR, r);
  };
  c.ellipse = (x, y, rx, ry, rot, a0, a1) => {
    st.calls++;
    bad('ellipse.x', x); bad('ellipse.y', y); bad('ellipse.rx', rx); bad('ellipse.ry', ry); bad('ellipse.rot', rot);
    if (typeof rx === 'number' && (rx < 0 || ry < 0)) st.bad.push('ellipse radius<0');
  };
  c.measureText = (s) => {
    const m = /(\d+(?:\.\d+)?)px/.exec(c.font);
    const size = m ? parseFloat(m[1]) : 10;
    return { width: String(s).length * size * 0.55 };
  };
  c.fillText = (s, x, y) => {
    st.calls++; st.texts++;
    bad('fillText.x', x); bad('fillText.y', y);
    if (typeof s !== 'string') st.bad.push('fillText non-string');
  };
  c.createLinearGradient = (x0, y0, x1, y1) => {
    st.calls++; st.gradients++;
    bad('linGrad.x0', x0); bad('linGrad.y0', y0); bad('linGrad.x1', x1); bad('linGrad.y1', y1);
    return {
      addColorStop(o, col) {
        if (!Number.isFinite(o) || o < 0 || o > 1) st.bad.push('linGrad.offset=' + o);
        if (!/^(#|rgba?\()/.test(String(col))) st.bad.push('linGrad.color=' + col);
      }
    };
  };
  c.createRadialGradient = (x0, y0, r0, x1, y1, r1) => {
    st.calls++; st.gradients++;
    bad('radGrad.x0', x0); bad('radGrad.y0', y0); bad('radGrad.r0', r0);
    bad('radGrad.x1', x1); bad('radGrad.y1', y1); bad('radGrad.r1', r1);
    if (r0 < 0 || r1 < 0) st.bad.push('radGrad radius<0');
    return {
      addColorStop(o, col) {
        if (!Number.isFinite(o) || o < 0 || o > 1) st.bad.push('radGrad.offset=' + o);
        if (!/^(#|rgba?\()/.test(String(col))) st.bad.push('radGrad.color=' + col);
      }
    };
  };
  Object.assign(c, { _st: st, noop: noop });
  return c;
}

const results = {};

/* 1. 场景生成 */
const sceneA = A.build({ seed: 'tangent-01', tideRange: 1, tides: 9, density: 1 });
results.scene = {
  lines: sceneA.lines.length,
  grains: sceneA.grainCount,
  version: sceneA.version
};
const coordBad = [];
for (const line of sceneA.lines) {
  for (const pt of line.samples) {
    if (!Number.isFinite(pt.x) || !Number.isFinite(pt.y)) coordBad.push('sample');
    if (pt.x < 0 || pt.x > 1) coordBad.push('sample.x=' + pt.x);
    if (pt.y < -0.1 || pt.y > 1.1) coordBad.push('sample.y=' + pt.y);
  }
  for (const g of line.grains) {
    if (!Number.isFinite(g.x) || !Number.isFinite(g.y) || !Number.isFinite(g.size)) coordBad.push('grain');
    if (g.x < 0 || g.x > 1) coordBad.push('grain.x=' + g.x);
  }
}
results.coords = { issueCount: coordBad.length, examples: coordBad.slice(0, 5) };

/* 2. 确定性 */
const sceneB = A.build({ seed: 'tangent-01', tideRange: 1, tides: 9, density: 1 });
const sceneC = A.build({ seed: 'tangent-02', tideRange: 1, tides: 9, density: 1 });
const sceneD = A.build({ seed: 'tangent-01', tideRange: 1.4, tides: 9, density: 1 });
const jsonA = JSON.stringify(sceneA);
results.determinism = {
  sameSeedSameScene: jsonA === JSON.stringify(sceneB),
  differentSeedDifferentScene: jsonA !== JSON.stringify(sceneC),
  differentParamDifferentScene: jsonA !== JSON.stringify(sceneD),
  sceneFingerprint: require('crypto').createHash('sha1').update(jsonA).digest('hex')
};

/* 3. 画家全量绘制（两种尺寸） */
function paintAll(W, H, scene) {
  const fc = makeFakeCtx();
  const painter = new Painter({ drawingContext: fc }, W, H, scene);
  const steps = [0, 0.05, 0.2, 0.4, 0.6, 0.8, 0.95, 1];
  for (const p of steps) painter.paintUpTo(p);
  return { st: fc._st, fc, painter };
}
function completeness(painter) {
  const issues = [];
  painter.scene.lines.forEach((line, i) => {
    const s = painter.states[i];
    if (s.grain !== line.grains.length) issues.push('line' + i + '.grains ' + s.grain + '/' + line.grains.length);
    if (s.salt !== line.salts.length) issues.push('line' + i + '.salts');
    if (s.shell !== line.shells.length) issues.push('line' + i + '.shells');
    if (s.bloom !== line.blooms.length) issues.push('line' + i + '.blooms');
    if (!s.gauge) issues.push('line' + i + '.gauge');
    if (Math.abs(s.ink - line.totalLen) > 1e-6) issues.push('line' + i + '.ink');
    if (Math.abs(s.band - line.totalLen) > 1e-6) issues.push('line' + i + '.band');
  });
  if (!painter.footerDone) issues.push('footer');
  return issues;
}
const small = paintAll(585, 702, sceneA);
const large = paintAll(1170, 1404, sceneA);
const incomplete = completeness(small.painter).concat(completeness(large.painter));

const strokesBefore = small.st.strokes;
small.painter.paintUpTo(1);
const strokesAfter = small.st.strokes;

results.painter = {
  small: { calls: small.st.calls, strokes: small.st.strokes, fills: small.st.fills, texts: small.st.texts, gradients: small.st.gradients, bad: small.st.bad.length, outOfRange: small.st.outOfRange.length },
  large: { calls: large.st.calls, strokes: large.st.strokes, fills: large.st.fills, texts: large.st.texts, gradients: large.st.gradients, bad: large.st.bad.length, outOfRange: large.st.outOfRange.length },
  badExamples: small.st.bad.concat(large.st.bad).slice(0, 8),
  completenessIssues: incomplete.slice(0, 8),
  idempotentRepaint: strokesBefore === strokesAfter
};

/* 4. 初始状态只有纸面（progress 0 不画水线） */
const fc0 = makeFakeCtx();
const p0 = new Painter({ drawingContext: fc0 }, 585, 702, sceneA);
p0.paintUpTo(0);
const fc1 = makeFakeCtx();
const p1 = new Painter({ drawingContext: fc1 }, 585, 702, sceneA);
p1.paintUpTo(1);
results.stages = {
  strokeAtZero: fc0._st.strokes,
  strokeAtOne: fc1._st.strokes,
  textAtZero: fc0._st.texts,
  textAtOne: fc1._st.texts,
  zeroFewerStrokes: fc0._st.strokes < fc1._st.strokes
};

/* 5. 不同参数改变结构 */
results.paramEffects = {
  tideRange: A.build({ seed: 'x', tideRange: 1.8, tides: 9, density: 1 }).lines[0].amp !== A.build({ seed: 'x', tideRange: 0.4, tides: 9, density: 1 }).lines[0].amp,
  tides: A.build({ seed: 'x', tideRange: 1, tides: 14, density: 1 }).lines.length === 14,
  density: A.build({ seed: 'x', tideRange: 1, tides: 9, density: 1.8 }).grainCount > A.build({ seed: 'x', tideRange: 1, tides: 9, density: 0.35 }).grainCount
};

/* 6. 几何压力测试：多组参数下水线不相交且保持在版心内 */
const stress = [];
let minSep = Infinity, maxY = -Infinity, minY = Infinity, sepFail = 0, boundsFail = 0;
const seeds = ['tangent-01', 'spring-2048', 'silt-7', '潮汐', 'x', 'moon-9911'];
const combos = [];
for (const s of seeds) {
  for (const tr of [0.4, 1, 1.8]) {
    for (const n of [5, 9, 14, 16]) combos.push({ seed: s, tideRange: tr, tides: n, density: 1 });
  }
}
for (const c of combos) {
  const sc = A.build(c);
  for (let i = 0; i < sc.lines.length; i++) {
    for (const pt of sc.lines[i].samples) {
      maxY = Math.max(maxY, pt.y);
      minY = Math.min(minY, pt.y);
      if (pt.y > 0.885 || pt.y < 0.15) boundsFail++;
    }
    if (i > 0) {
      const prev = sc.lines[i - 1].samples, cur = sc.lines[i].samples;
      for (let k = 0; k < cur.length; k++) {
        const d = cur[k].y - prev[k].y;
        minSep = Math.min(minSep, d);
        if (d < 0.0015) sepFail++;
      }
    }
  }
}
results.geometry = {
  combos: combos.length,
  minSeparation: Number(minSep.toFixed(5)),
  maxY: Number(maxY.toFixed(4)),
  minY: Number(minY.toFixed(4)),
  separationFailures: sepFail,
  boundsFailures: boundsFail
};

console.log(JSON.stringify(results, null, 2));
const failed = results.painter.badExamples.length || !results.determinism.sameSeedSameScene ||
  results.coords.issueCount || incomplete.length || !results.painter.idempotentRepaint ||
  !results.stages.zeroFewerStrokes || results.geometry.separationFailures > 0 ||
  results.geometry.boundsFailures > 0 ||
  !results.paramEffects.tideRange || !results.paramEffects.tides || !results.paramEffects.density;
process.exitCode = failed ? 1 : 0;
