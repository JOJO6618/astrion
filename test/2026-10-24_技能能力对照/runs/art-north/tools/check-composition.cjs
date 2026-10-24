'use strict';
/* 非视觉检查：把标记序列近似栅格化为墨量密度，用于核对构图分布（不是截图）。 */
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'js', 'core.js'), 'utf8');
const mod = { exports: {} };
const T = new Function('module', 'exports', src + '\nreturn module.exports;')(mod, mod.exports);

const W = 1400, H = 1400;
const G = 70;               // 网格
const cell = W / G;
const ink = new Float32Array(G * G);

function add(x, y, a, area) {
  if (x < 0 || y < 0 || x >= W || y >= H) return;
  const cx = Math.min(G - 1, Math.floor(x / cell));
  const cy = Math.min(G - 1, Math.floor(y / cell));
  const idx = cy * G + cx;
  const cov = Math.min(1, area / (cell * cell));
  const alpha = (a / 255) * cov;
  ink[idx] = ink[idx] + alpha * (1 - ink[idx]);
}

function render(plan) {
  ink.fill(0);
  for (const m of plan.ground.concat(plan.marks)) {
    if (m.t === 'seg') {
      const dx = m.x2 - m.x1, dy = m.y2 - m.y1;
      const len = Math.hypot(dx, dy);
      const steps = Math.max(1, Math.ceil(len));
      for (let s = 0; s <= steps; s++) {
        const x = m.x1 + (dx * s) / steps;
        const y = m.y1 + (dy * s) / steps;
        add(x, y, m.a, m.w * len / steps);
      }
    } else if (m.t === 'dot') {
      const area = Math.PI * m.rx * m.ry;
      add(m.x, m.y, m.a, area);
    }
  }
}

function stats(plan) {
  render(plan);
  let sum = 0, max = 0, covered = 0;
  for (let i = 0; i < ink.length; i++) {
    sum += ink[i];
    if (ink[i] > max) max = ink[i];
    if (ink[i] > 0.03) covered++;
  }
  const rowMeans = [];
  for (let r = 0; r < G; r++) {
    let s = 0;
    for (let c = 0; c < G; c++) s += ink[r * G + c];
    rowMeans.push(s / G);
  }
  const colMeans = [];
  for (let c = 0; c < G; c++) {
    let s = 0;
    for (let r = 0; r < G; r++) s += ink[r * G + c];
    colMeans.push(s / G);
  }
  return {
    mean: sum / ink.length,
    max,
    covered: covered / ink.length,
    rowMeans,
    colMeans
  };
}

const plan = T.createPlan({ seed: 6271, layers: 9, tidalRange: 0.62, sediment: 0.85, erosion: 0.45, palette: 'ebb' });
const st = stats(plan);

console.log('seed', plan.seed, 'hash', plan.hash, 'marks', plan.marks.length);
console.log('mean ink', st.mean.toFixed(4), 'max cell ink', st.max.toFixed(3), 'cells>3% =', (st.covered * 100).toFixed(1) + '%');

const ramp = ' .:-=+*#%@';
console.log('\n墨量分布图（每格 20px，左侧为画布顶部）');
for (let r = 0; r < G; r++) {
  let line = '';
  for (let c = 0; c < G; c++) {
    const v = Math.min(1, ink[r * G + c] * 3.2);
    line += ramp[Math.min(ramp.length - 1, Math.floor(v * ramp.length))];
  }
  console.log(String(r).padStart(2, '0') + '|' + line + '|');
}

console.log('\n逐行墨量（y=0..1400，每行约 20px）');
const bars = st.rowMeans.map((v, i) => `${String(i * 20).padStart(4)} ${'#'.repeat(Math.round(v * 90)).padEnd(60)} ${v.toFixed(3)}`);
console.log(bars.join('\n'));

console.log('\n逐列墨量（x=0..1400）');
console.log(st.colMeans.map((v, i) => `${String(i * 20).padStart(4)} ${'#'.repeat(Math.round(v * 90))}`).join('\n'));

const min = Math.min(...st.rowMeans.slice(4, 66));
const maxRow = Math.max(...st.rowMeans.slice(4, 66));
console.log('\n行墨量区间（去掉上下留白）min', min.toFixed(3), 'max', maxRow.toFixed(3), 'ratio', (maxRow / Math.max(min, 1e-6)).toFixed(2));
