/*
 * 潮汐档案 / Tidal Archive — 生成核心
 * 纯数据层：不依赖 p5，也不依赖 DOM。
 * 同一 seed + 同一参数 + 同一调色板 => 完全相同的标记序列（可哈希验证）。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.TidalCore = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var W = 1400;
  var H = 1400;

  /* ── 调色板 ─────────────────────────────────────────────── */
  var PALETTES = {
    ebb: {
      id: 'ebb',
      name: '退潮赭',
      paper: '#efe6d5',
      ink: '#2c322e',
      sepia: '#6b5238',
      rust: '#8e4a2f',
      rule: '#9a8d75',
      sediment: ['#a87a44', '#8f4e30', '#6f7060', '#cbb18a'],
      sheen: [255, 250, 238]
    },
    moon: {
      id: 'moon',
      name: '月夜砂',
      paper: '#151b1e',
      ink: '#ccd6cf',
      sepia: '#8b7a60',
      rust: '#b3815a',
      rule: '#54615d',
      sediment: ['#b99a6d', '#8fa79c', '#6f7d76', '#d6c8a6'],
      sheen: [206, 228, 232]
    },
    salt: {
      id: 'salt',
      name: '盐霜白',
      paper: '#e9e8e2',
      ink: '#33393a',
      sepia: '#7b7568',
      rust: '#9a7358',
      rule: '#8f9793',
      sediment: ['#93a3a0', '#b6c2bd', '#6f7d7b', '#c8ad86'],
      sheen: [255, 255, 252]
    }
  };

  /* ── 工具 ───────────────────────────────────────────────── */
  function clamp(v, lo, hi) {
    return v < lo ? lo : v > hi ? hi : v;
  }

  function hexToRgb(hex) {
    var h = String(hex).replace('#', '');
    return [
      parseInt(h.slice(0, 2), 16),
      parseInt(h.slice(2, 4), 16),
      parseInt(h.slice(4, 6), 16)
    ];
  }

  function mixRgb(a, b, t) {
    var k = clamp(t, 0, 1);
    return [
      Math.round(a[0] + (b[0] - a[0]) * k),
      Math.round(a[1] + (b[1] - a[1]) * k),
      Math.round(a[2] + (b[2] - a[2]) * k)
    ];
  }

  function mixSeed(seed) {
    var h = (seed >>> 0) ^ 0x9e3779b9;
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return (h ^ (h >>> 16)) >>> 0;
  }

  function mulberry32(a) {
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* 值噪声：自带种子，跨运行完全确定 */
  function makeNoise(seed) {
    var rng = mulberry32(mixSeed(seed ^ 0x51ed270b));
    var base = new Uint8Array(256);
    var i, j, k, t;
    for (i = 0; i < 256; i++) base[i] = i;
    for (j = 255; j > 0; j--) {
      k = Math.floor(rng() * (j + 1));
      t = base[j]; base[j] = base[k]; base[k] = t;
    }
    var perm = new Uint8Array(512);
    for (i = 0; i < 512; i++) perm[i] = base[i & 255];

    function hash2(ix, iy) {
      return perm[(perm[ix & 255] + (iy & 255)) & 255] / 255;
    }
    function fade(v) {
      return v * v * (3 - 2 * v);
    }
    function noise2(x, y) {
      var x0 = Math.floor(x), y0 = Math.floor(y);
      var fx = fade(x - x0), fy = fade(y - y0);
      var v00 = hash2(x0, y0);
      var v10 = hash2(x0 + 1, y0);
      var v01 = hash2(x0, y0 + 1);
      var v11 = hash2(x0 + 1, y0 + 1);
      var top = v00 + (v10 - v00) * fx;
      var bot = v01 + (v11 - v01) * fx;
      return top + (bot - top) * fy;
    }
    function fbm(x, y, octaves) {
      var amp = 0.5, freq = 1, sum = 0, norm = 0, o;
      for (o = 0; o < octaves; o++) {
        sum += amp * noise2(x * freq + o * 13.7, y * freq + o * 7.3);
        norm += amp;
        amp *= 0.5;
        freq *= 2.07;
      }
      return sum / norm;
    }
    return { noise2: noise2, fbm: fbm };
  }

  /* ── 标记工厂 ───────────────────────────────────────────── */
  function seg(x1, y1, x2, y2, w, c, a) {
    return { t: 'seg', x1: x1, y1: y1, x2: x2, y2: y2, w: w, c: c, a: a, rev: 0 };
  }
  function dot(x, y, rx, ry, c, a) {
    return { t: 'dot', x: x, y: y, rx: rx, ry: ry, c: c, a: a, rev: 0 };
  }
  function txt(x, y, s, size, c, a, align, mono) {
    return { t: 'txt', x: x, y: y, s: s, size: size, c: c, a: a, align: align || 'left', mono: !!mono, rev: 0 };
  }

  /* ── 主计划生成 ─────────────────────────────────────────── */
  function createPlan(options) {
    var opts = options || {};
    var seed = Math.max(1, Math.floor(opts.seed || 1)) >>> 0;
    var N = Math.max(2, Math.min(16, Math.round(opts.layers || 9)));
    var tidalRange = clamp(opts.tidalRange == null ? 0.62 : opts.tidalRange, 0, 1);
    var sediment = clamp(opts.sediment == null ? 0.85 : opts.sediment, 0.05, 2);
    var erosion = clamp(opts.erosion == null ? 0.45 : opts.erosion, 0, 1);

    var pal = PALETTES[opts.palette] || PALETTES.ebb;
    var paper = hexToRgb(pal.paper);
    var ink = hexToRgb(pal.ink);
    var sepia = hexToRgb(pal.sepia);
    var rust = hexToRgb(pal.rust);
    var rule = hexToRgb(pal.rule);
    var sedimentCols = pal.sediment.map(hexToRgb);
    var isDark = (paper[0] * 0.299 + paper[1] * 0.587 + paper[2] * 0.114) < 110;

    var rng = mulberry32(mixSeed(seed));
    var noise = makeNoise(seed);

    var ground = [];
    var marks = [];
    var i, j, k, x, y;

    var xLeft = 92;
    var xRight = W - 84;
    var yTop = 196;
    var yBottom = 1212;
    var span = yBottom - yTop;
    var slot = N > 1 ? span / (N - 1) : span;
    var tilt = 0.028 + rng() * 0.012;
    var framesPerLayer = 74;

    /* 1. 纸面：纤维颗粒 + 极淡横纹 */
    var fiberColor = isDark ? [255, 255, 255] : ink;
    for (i = 0; i < 5200; i++) {
      var fx = rng() * W;
      var fy = rng() * H;
      var fr = 0.5 + rng() * 1.5;
      ground.push(dot(fx, fy, fr, fr * (0.55 + rng() * 0.7), fiberColor, 3 + rng() * 12));
    }
    for (i = 0; i < 58; i++) {
      var ly = rng() * H;
      var lx = rng() * W * 0.55;
      var llen = 180 + rng() * 980;
      ground.push(seg(lx, ly, Math.min(W, lx + llen), ly + (rng() - 0.5) * 2, 0.5 + rng() * 0.7, ink, 2 + rng() * 4));
    }

    /* 2. 潮痕：自最旧（底部）向最新（顶部）沉积 */
    for (var li = 0; li < N; li++) {
      var recency = N > 1 ? li / (N - 1) : 1;
      var baseY = yBottom - li * slot;
      var layerStart = marks.length;

      var strandCount = 1 + (rng() < 0.52 ? 1 : 0) + (rng() < 0.15 ? 1 : 0);
      var spread = 6 + 30 * tidalRange;
      var amp1 = (9 + 46 * tidalRange) * (0.62 + 0.5 * rng());
      var amp2 = amp1 * (0.2 + 0.18 * rng());
      var nscale = 0.0013 + 0.0011 * rng();
      var offsetSeed = rng() * 120;
      var tiltL = tilt * (0.7 + 0.6 * rng());
      var lineAlpha = 26 + 196 * Math.pow(recency, 1.25);
      var lineColor = mixRgb(sepia, ink, Math.pow(recency, 1.05));
      var lineWeight = 0.5 + 1.3 * recency;
      var gapChance = 0.05 + 0.26 * erosion;
      var band = Math.max(46, slot * 0.95);

      /* 潮痕高程：噪声叠加 + 整幅岸坡微倾 */
      function wrackAt(px, s) {
        var n1 = noise.fbm(px * nscale + offsetSeed, li * 8.7 + s * 5.3, 3);
        var n2 = noise.fbm(px * nscale * 4.7 + 41.3, li * 4.9 + s * 2.9, 2);
        var off = (s - (strandCount - 1) / 2) * spread;
        return baseY + tiltL * (px - W / 2) + off +
          (n1 - 0.5) * 2 * amp1 + (n2 - 0.5) * 2 * amp2;
      }

      /* 2a. 新潮痕的湿润晕边 */
      if (recency > 0.5) {
        for (var hs = 0; hs < strandCount; hs++) {
          for (x = xLeft - 6; x <= xRight + 4; x += 9) {
            marks.push(seg(x, wrackAt(x, hs), x + 9, wrackAt(x + 9, hs),
              lineWeight * 3.2, lineColor, lineAlpha * 0.13));
          }
        }
      }

      /* 2b. 主潮痕：分段手绘感，逐段浓淡与断口 */
      for (var s = 0; s < strandCount; s++) {
        var step = 4;
        var runPoints = 24;
        for (var runStart = xLeft - 12; runStart < xRight + 10; runStart += step * runPoints) {
          if (rng() < gapChance) continue;
          var runEnd = Math.min(xRight + 10, runStart + step * runPoints);
          var shade = 0.32 + 0.82 * noise.noise2(runStart * 0.004 + s * 9.1, li * 2.3 + 0.7);
          var a = Math.min(255, lineAlpha * shade);
          var w = lineWeight * (0.72 + 0.55 * rng());
          var pts = [];
          for (x = runStart; x <= runEnd; x += step) pts.push([x, wrackAt(x, s)]);
          for (k = 0; k < pts.length - 1; k++) {
            marks.push(seg(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], w, lineColor, a));
          }
        }
      }

      /* 2c. 细砂纹（退潮后的波纹痕迹） */
      var rippleCount = rng() < 0.62 ? 3 + Math.floor(rng() * 6) : 0;
      for (var r = 0; r < rippleCount; r++) {
        var roff = 8 + rng() * (band * 0.8);
        var ramp = amp1 * (0.18 + 0.24 * rng());
        var rphase = rng() * 100;
        var ra = (16 + 40 * (1 - recency * 0.35)) * (0.55 + 0.65 * rng());
        var rc = mixRgb(sepia, sedimentCols[2], 0.35 + 0.4 * rng());
        for (x = xLeft; x <= xRight - 10; x += 10) {
          if (rng() < 0.08 + 0.22 * erosion) continue;
          var ry1 = wrackAt(x, 0) + roff +
            (noise.noise2(x * 0.004 + rphase, li * 3.1 + r * 2.2) - 0.5) * 2 * ramp;
          var ry2 = wrackAt(x + 10, 0) + roff +
            (noise.noise2((x + 10) * 0.004 + rphase, li * 3.1 + r * 2.2) - 0.5) * 2 * ramp;
          marks.push(seg(x, ry1, x + 10, ry2, 0.42 + 0.5 * rng(), rc, ra));
        }
      }

      /* 2d. 沉积颗粒：近线粗重、远线细淡，密度随池洼聚集 */
      var grainTarget = Math.min(9000, Math.round(4200 * sediment * (1.35 - 0.75 * recency) * (9 / N)));
      for (j = 0; j < grainTarget; j++) {
        var gx = xLeft + rng() * (xRight - xLeft);
        var pool = noise.noise2(gx * 0.0035 + 11.7, li * 2.1 + j * 0.0002);
        if (rng() > 0.22 + 1.15 * pool) continue;
        var u = rng();
        var off = -Math.log(1 - u * 0.985) * band * 0.32;
        var gy = wrackAt(gx, 0) + off + (rng() - 0.5) * 4;
        if (gy > H - 58 || gy < 62) continue;
        var near = Math.min(1, off / (band * 0.55));
        var sz = (2.4 - 1.55 * near) * (0.6 + 0.75 * rng());
        var pick = rng();
        var gcol = near < 0.4
          ? (pick < 0.55 ? sedimentCols[0] : sedimentCols[1])
          : (pick < 0.5 ? sedimentCols[2] : sedimentCols[3]);
        var ga = (24 + 118 * (0.45 + 0.55 * recency)) * (0.32 + 0.82 * rng());
        marks.push(dot(gx, gy, sz * (1 + rng() * 0.5), sz * (0.55 + rng() * 0.5), gcol, ga));
      }

      /* 2d2. 潮线残屑带：紧贴潮线的高密度碎屑，像退潮后留下的漂浮物沉降 */
      var litterCount = Math.round(520 + 380 * (1 - recency * 0.5));
      for (j = 0; j < litterCount; j++) {
        var lx = xLeft + rng() * (xRight - xLeft);
        var ly = wrackAt(lx, 0) + (rng() - 0.5) * (6 + 12 * tidalRange);
        if (ly < 62 || ly > H - 60) continue;
        var lsz = 0.4 + rng() * 0.85;
        var lcol = rng() < 0.55 ? mixRgb(ink, sepia, 0.3) : sedimentCols[1];
        marks.push(dot(lx, ly, lsz * (0.9 + rng() * 0.5), lsz * (0.6 + rng() * 0.5), lcol, (48 + 44 * recency) + rng() * 78));
      }

      /* 2e. 盐霜：低洼处的细密结晶 */
      if (rng() < 0.55) {
        var clusters = 1 + (rng() < 0.32 ? 1 : 0);
        for (var ci = 0; ci < clusters; ci++) {
          var cx = xLeft + rng() * (xRight - xLeft);
          var cy = wrackAt(cx, 0) + band * (0.3 + rng() * 0.45);
          var cRad = 12 + rng() * 26;
          var cDots = 12 + Math.floor(rng() * 14);
          var cCol = mixRgb(sedimentCols[3], isDark ? [255, 255, 255] : [255, 255, 255], 0.45);
          for (j = 0; j < cDots; j++) {
            var ang = rng() * Math.PI * 2;
            var rr = Math.sqrt(rng()) * cRad;
            marks.push(dot(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr * 0.55,
              0.35 + rng() * 0.8, 0.35 + rng() * 0.8, cCol, 22 + rng() * 46));
          }
        }
      }

      /* 2f. 粗砾：贴着潮线的深色碎屑，给线条以"落点" */
      var debris = 6 + Math.floor(rng() * 11);
      for (j = 0; j < debris; j++) {
        var dx = xLeft + rng() * (xRight - xLeft);
        var dy = wrackAt(dx, 0) + 1 + rng() * 5;
        var dcol = rng() < 0.5 ? sedimentCols[1] : mixRgb(ink, rust, 0.4);
        var dn = 2 + Math.floor(rng() * 3);
        for (k = 0; k < dn; k++) {
          marks.push(dot(dx + (rng() - 0.5) * 6, dy + (rng() - 0.5) * 3,
            0.9 + rng() * 1.5, 0.6 + rng() * 1.1, dcol, 60 + rng() * 70));
        }
      }

      /* 2g. 侵蚀细沟：从潮线向下渗流，切穿老沉积 */
      var channels = Math.round(34 * erosion / N);
      for (var ch = 0; ch < channels; ch++) {
        var chx = xLeft + 30 + rng() * (xRight - xLeft - 60);
        var chy = wrackAt(chx, 0) + 3;
        var chLen = 60 + rng() * 210;
        var chPh = rng() * 90;
        var chW = 0.45 + rng() * 0.65;
        var chCol = mixRgb(ink, sepia, 0.35 + 0.3 * rng());
        var travelled = 0;
        var dashOn = true;
        var dashLen = 0;
        while (travelled < chLen) {
          var segLen = 5;
          var nx = chx + (noise.noise2(chx * 0.02, chy * 0.02 + chPh) - 0.5) * 2.6;
          var ny = chy + segLen;
          if (dashOn && ny < H - 60) {
            var prog = travelled / chLen;
            marks.push(seg(chx, chy, nx, ny, chW * (1 - 0.5 * prog), chCol,
              62 * Math.pow(1 - prog, 1.25)));
          }
          chx = nx;
          chy = ny;
          travelled += segLen;
          dashLen += segLen;
          if (dashOn && dashLen > 8 + rng() * 14) { dashOn = false; dashLen = 0; }
          else if (!dashOn && dashLen > 3 + rng() * 7) { dashOn = true; dashLen = 0; }
        }
      }

      /* 2h. 本层的显影帧分配 */
      var layerEnd = marks.length;
      var layerMarks = layerEnd - layerStart;
      for (var m = layerStart; m < layerEnd; m++) {
        marks[m].rev = li * framesPerLayer +
          Math.floor(((m - layerStart) / Math.max(1, layerMarks)) * (framesPerLayer - 1));
      }
    }

    /* 3. 档案标尺：左侧潮位刻度 + 编号，右侧刻度 */
    ground.push(seg(58, yTop - 26, 58, yBottom + 30, 0.9, rule, 110));
    ground.push(seg(W - 46, yTop - 26, W - 46, yBottom + 30, 0.9, rule, 80));
    for (i = 0; i < N; i++) {
      var tickY = yBottom - i * slot;
      ground.push(seg(48, tickY, 58, tickY, 0.9, rule, 130));
      ground.push(seg(58, tickY, 68, tickY, 0.9, rule, 60));
      ground.push(seg(W - 46, tickY, W - 36, tickY, 0.8, rule, 90));
      ground.push(txt(42, tickY + 3.5, String(i + 1), 10, rule, 150, 'right', true));
    }

    /* 4. 档案著录：标题与种子信息 */
    ground.push(txt(94, 132, '潮汐档案 / TIDAL ARCHIVE', 17, ink, 170, 'left', false));
    ground.push(txt(94, 154, '沉积序列 DEPOSITION SEQUENCE', 10.5, rule, 165, 'left', true));
    ground.push(txt(xRight, H - 58, '种子 SEED ' + seed, 12, ink, 150, 'right', true));
    ground.push(txt(xRight, H - 38, N + ' 层潮痕 · ' + pal.name, 10.5, rule, 150, 'right', true));
    ground.push(txt(W - 46, yTop - 34, '潮位', 10, rule, 130, 'right', false));

    /* 5. 统计与哈希 */
    var counts = { seg: 0, dot: 0, txt: 0 };
    for (i = 0; i < marks.length; i++) counts[marks[i].t]++;
    for (i = 0; i < ground.length; i++) counts.ground = (counts.ground || 0) + 1;

    var hash = hashLists([ground, marks]);

    return {
      width: W,
      height: H,
      paper: paper,
      paperHex: pal.paper,
      sepia: sepia,
      sheen: pal.sheen,
      palette: pal.id,
      paletteName: pal.name,
      seed: seed,
      layers: N,
      framesPerLayer: framesPerLayer,
      totalFrames: N * framesPerLayer,
      ground: ground,
      marks: marks,
      stats: {
        groundMarks: ground.length,
        marks: marks.length,
        segments: counts.seg,
        dots: counts.dot,
        texts: counts.txt,
        layers: N
      },
      hash: hash
    };
  }

  /* 稳定哈希：把几何量化后做 FNV-1a，用于跨运行比对 */
  function hashLists(lists) {
    var h = 2166136261 >>> 0;
    function mix(v) {
      h = (h ^ (v & 0xffff)) >>> 0;
      h = Math.imul(h, 16777619) >>> 0;
      h = (h ^ ((v >>> 16) & 0xffff)) >>> 0;
      h = Math.imul(h, 16777619) >>> 0;
    }
    function code(t) { return t === 'seg' ? 1 : t === 'dot' ? 2 : 3; }
    for (var l = 0; l < lists.length; l++) {
      var list = lists[l];
      mix(list.length);
      for (var i = 0; i < list.length; i++) {
        var m = list[i];
        mix(code(m.t));
        mix(Math.round(m.x1 != null ? m.x1 : m.x) * 8);
        mix(Math.round(m.y1 != null ? m.y1 : m.y) * 8);
        if (m.t === 'seg') { mix(Math.round(m.x2 * 8)); mix(Math.round(m.y2 * 8)); mix(Math.round(m.w * 20)); }
        if (m.t === 'dot') { mix(Math.round(m.rx * 20)); mix(Math.round(m.ry * 20)); }
        mix(Math.round(m.a * 4));
        mix(m.c[0] * 3 + m.c[1] * 5 + m.c[2] * 7);
        if (m.t === 'txt') mix(m.s.length * 31 + m.size);
        mix(m.rev || 0);
      }
    }
    return ('00000000' + h.toString(16)).slice(-8);
  }

  return {
    WIDTH: W,
    HEIGHT: H,
    PALETTES: PALETTES,
    createPlan: createPlan,
    hashLists: hashLists
  };
});
