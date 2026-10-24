'use strict';
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'js', 'core.js'), 'utf8');
const mod = { exports: {} };
const load = new Function('module', 'exports', 'globalThis', 'window', src + '\nreturn module.exports;');
const T = load(mod, mod.exports, undefined, undefined);

const base = { seed: 12345, layers: 9, tidalRange: 0.62, sediment: 0.85, erosion: 0.45, palette: 'ebb' };
const t0 = Date.now();
const a = T.createPlan(base);
const t1 = Date.now();
const b = T.createPlan(base);
const t2 = Date.now();

console.log('hash A =', a.hash, ' hash B =', b.hash, ' equal =', a.hash === b.hash);
console.log('marks =', a.marks.length, ' ground =', a.ground.length);
console.log('stats =', JSON.stringify(a.stats));
console.log('plan ms =', t1 - t0, t2 - t1);

let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity, bad = 0;
for (const m of a.marks.concat(a.ground)) {
  const xs = [m.x1 != null ? m.x1 : m.x, m.x2 != null ? m.x2 : m.x];
  const ys = [m.y1 != null ? m.y1 : m.y, m.y2 != null ? m.y2 : m.y];
  for (const v of xs.concat(ys)) if (!Number.isFinite(v)) bad++;
  minx = Math.min(minx, ...xs); maxx = Math.max(maxx, ...xs);
  miny = Math.min(miny, ...ys); maxy = Math.max(maxy, ...ys);
}
console.log('bounds x', minx.toFixed(1), maxx.toFixed(1), ' y', miny.toFixed(1), maxy.toFixed(1), ' nonfinite =', bad);

const variants = {
  seedDiffers: Object.assign({}, base, { seed: 12346 }),
  rangeDiffers: Object.assign({}, base, { tidalRange: 0.3 }),
  densityDiffers: Object.assign({}, base, { sediment: 1.2 }),
  erosionDiffers: Object.assign({}, base, { erosion: 0.9 }),
  layers14: Object.assign({}, base, { layers: 14 }),
  layers3: Object.assign({}, base, { layers: 3 }),
  palMoon: Object.assign({}, base, { palette: 'moon' }),
  palSalt: Object.assign({}, base, { palette: 'salt' })
};
for (const k of Object.keys(variants)) {
  const p = T.createPlan(variants[k]);
  console.log(k, 'hash', p.hash, 'differsFromBase', p.hash !== a.hash, 'marks', p.marks.length, 'frames', p.totalFrames);
}

const extremes = [];
for (const layers of [2, 3, 9, 16]) {
  for (const tidalRange of [0, 1]) {
    for (const sediment of [0.05, 2]) {
      for (const erosion of [0, 1]) {
        for (const palette of ['ebb', 'moon', 'salt']) {
          extremes.push({ seed: 7, layers, tidalRange, sediment, erosion, palette });
        }
      }
    }
  }
}
let problems = 0;
for (const o of extremes) {
  const p = T.createPlan(o);
  if (!p.marks.length) { problems++; console.log('EMPTY', JSON.stringify(o)); }
  for (const m of p.marks) {
    const vals = [m.x1, m.y1, m.x2, m.y2, m.x, m.y];
    if (vals.some(v => v != null && !Number.isFinite(v))) { problems++; break; }
    if (m.a != null && (m.a < 0 || m.a > 255)) { problems++; break; }
  }
  if (!/^[0-9a-f]{8}$/.test(p.hash)) problems++;
}
console.log('extreme combos =', extremes.length, ' problems =', problems);

console.log('reproducibility across 5 runs =',
  [1, 2, 3, 4, 5].map(() => T.createPlan(base).hash).every((h, _, arr) => h === arr[0]));
console.log('seed samples =', [1, 2, 99, 2147483647].map(s => s + ':' + T.createPlan(Object.assign({}, base, { seed: s })).hash).join(' '));

// 显影顺序必须单调（逐层沉积）
let monotone = true;
for (let i = 1; i < a.marks.length; i++) if (a.marks[i].rev < a.marks[i - 1].rev) monotone = false;
const revMax = a.marks[a.marks.length - 1].rev;
console.log('reveal monotonic =', monotone, ' revMax =', revMax, ' totalFrames =', a.totalFrames);
