/*
 * Tidal Archive / 潮汐档案
 * 场景生成器：只依赖种子与参数，在归一化坐标中构建全部沉积结构。
 * 不含 DOM、p5 或时间依赖，可在相同输入下完全复现。
 */
(function (root) {
  'use strict';

  var FIELD = { left: 0.088, right: 0.912, top: 0.196, bottom: 0.832 };
  var TAU = Math.PI * 2;

  var PALETTE = {
    ink: '#1b2a33',
    tide: '#3c5c6b',
    slate: '#5f7274',
    silt: '#a9977a',
    sand: '#c9b795',
    ochre: '#b0773f',
    rust: '#8d4a30',
    shell: '#f7f1e2'
  };

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function num(v, d) { var n = Number(v); return isFinite(n) ? n : d; }

  /* FNV-1a + 雪崩混合，任意字符串 -> 32 位无符号种子 */
  function hashSeed(str) {
    var s = String(str == null ? '' : str);
    var h = 2166136261 >>> 0;
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    h ^= h >>> 16; h = Math.imul(h, 2246822507);
    h ^= h >>> 13; h = Math.imul(h, 3266489909);
    h ^= h >>> 16;
    return h >>> 0;
  }

  /* mulberry32 */
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* 哈希值噪声 + 分形叠加，替代 p5.noise，保证与画布尺寸无关 */
  function makeNoise(seed) {
    var s = seed >>> 0;
    function hash2(ix, iy) {
      var h = s ^ Math.imul(ix | 0, 374761393) ^ Math.imul(iy | 0, 668265263);
      h = Math.imul(h ^ (h >>> 13), 1274126177);
      h ^= h >>> 16;
      return (h >>> 0) / 4294967296;
    }
    function smooth(t) { return t * t * (3 - 2 * t); }
    function noise2(x, y) {
      var x0 = Math.floor(x), y0 = Math.floor(y);
      var fx = smooth(x - x0), fy = smooth(y - y0);
      var v00 = hash2(x0, y0), v10 = hash2(x0 + 1, y0);
      var v01 = hash2(x0, y0 + 1), v11 = hash2(x0 + 1, y0 + 1);
      var a = v00 + (v10 - v00) * fx;
      var b = v01 + (v11 - v01) * fx;
      return a + (b - a) * fy;
    }
    function fbm(x, y, oct) {
      var amp = 0.5, freq = 1, sum = 0, norm = 0;
      var n = oct || 3;
      for (var i = 0; i < n; i++) {
        sum += amp * noise2(x * freq + i * 31.7, y * freq + i * 17.3);
        norm += amp;
        amp *= 0.5;
        freq *= 2.03;
      }
      return sum / norm;
    }
    return { noise2: noise2, fbm: fbm };
  }

  function parseHex(hex) {
    var h = hex.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function mixHex(a, b, t) {
    var pa = parseHex(a), pb = parseHex(b);
    var r = Math.round(pa[0] + (pb[0] - pa[0]) * t);
    var g = Math.round(pa[1] + (pb[1] - pa[1]) * t);
    var bl = Math.round(pa[2] + (pb[2] - pa[2]) * t);
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + bl).toString(16).slice(1);
  }

  function weightedPick(rand, list) {
    var total = 0, i;
    for (i = 0; i < list.length; i++) total += list[i][1];
    var x = rand() * total;
    for (i = 0; i < list.length; i++) {
      x -= list[i][1];
      if (x <= 0) return list[i][0];
    }
    return list[list.length - 1][0];
  }

  var COLOR_NEAR = [
    ['#27414d', 3], ['#3f5c64', 3], ['#5b6f72', 2], ['#8a5a3c', 1], ['#a99878', 1]
  ];
  var COLOR_MID = [
    ['#2c4652', 1], ['#6d7c74', 2], ['#a9977a', 3], ['#b0773f', 2.5], ['#c9b795', 1.5], ['#8d4a30', 1]
  ];
  var COLOR_DEEP = [
    ['#c9b795', 3], ['#b3a181', 3], ['#a9977a', 2], ['#8d4a30', 1.2], ['#7d6a52', 1]
  ];

  function grainColor(rand, depth) {
    if (rand() < 0.035) return PALETTE.shell;
    if (depth < 0.18) return weightedPick(rand, COLOR_NEAR);
    if (depth < 0.55) return weightedPick(rand, COLOR_MID);
    return weightedPick(rand, COLOR_DEEP);
  }

  function build(input) {
    input = input || {};
    var seed = String(input.seed == null || input.seed === '' ? 'tidal-01' : input.seed);
    var tideRange = clamp(num(input.tideRange, 1), 0.35, 2.2);
    var tides = Math.round(clamp(num(input.tides, 9), 4, 16));
    var density = clamp(num(input.density, 1), 0.2, 2);

    var rand = rng(hashSeed(seed));
    var nz = makeNoise(hashSeed(seed + '::tide-noise'));

    var span = FIELD.bottom - FIELD.top;
    var gapBase = span / Math.max(1, tides - 1);

    /* 不规则的水线间距：更接近真实退潮留下的疏密 */
    var weights = [], totalW = 0, i;
    for (i = 0; i < tides; i++) {
      var w = 0.78 + rand() * 0.62;
      if (i === 0) w += 0.2;
      if (i === tides - 1) w += 0.24;
      weights.push(w);
      totalW += w;
    }

    var N = 220;
    var minGap = Math.max(0.0035, gapBase * 0.1);
    var lines = [];
    var cumW = 0;
    var prevSamples = null;

    for (i = 0; i < tides; i++) {
      cumW += weights[i];
      var rel = tides === 1 ? 0 : (cumW - weights[i] * 0.5) / totalW;
      var baseY = FIELD.top + span * rel;
      var t = tides === 1 ? 0 : i / (tides - 1);

      var ampFactor = 0.5 + rand() * 0.42;
      if (rand() < 0.16) ampFactor *= 1.4;
      var amp = ampFactor * gapBase * tideRange * (1 - 0.3 * t);
      /* 上限仅防止极端参数把版心压穿，实际分离由逐点约束保证 */
      amp = Math.min(amp, gapBase * 1.05);

      var freq = (1.7 + rand() * 1.7) * (0.82 + 0.3 * tideRange);
      var phase = rand() * 400;
      var tilt = (rand() - 0.5) * 0.016;
      var alpha = clamp(0.34 + 0.5 * t + rand() * 0.1, 0, 0.95);
      var color = mixHex('#4a6370', '#1b2a33', clamp(t * 0.9 + rand() * 0.12, 0, 1));
      var isLast = i === tides - 1;
      var emph = isLast ? 1.36 : (rand() < 0.18 ? 1.2 : 1);
      var bandScale = (0.6 + 0.8 * rand()) * (0.6 + 0.7 * t);
      var bandDepth = gapBase * (0.14 + 0.42 * rand()) * (0.7 + 0.7 * density) * (0.75 + 0.5 * t);

      var samples = [];
      for (var k = 0; k <= N; k++) {
        var u = k / N;
        var x = FIELD.left + (FIELD.right - FIELD.left) * u;
        var n1 = nz.fbm(u * freq + phase, phase * 0.37, 3);
        var n2 = nz.fbm(u * freq * 2.7 + phase * 0.19 + 11.3, phase * 0.11 + 3.7, 2);
        var wave = (n1 - 0.5) * 1.75 + (n2 - 0.5) * 0.55;
        wave += Math.sin(u * TAU * (1 + Math.floor(freq * 0.6)) + phase) * 0.16;
        var y = baseY + amp * wave + tilt * (u - 0.5);
        y = clamp(y, FIELD.top - 0.03, FIELD.bottom + 0.025);
        if (prevSamples) {
          y = Math.max(y, prevSamples[k].y + minGap);
        }
        samples.push({ x: x, y: y, u: u });
      }
      prevSamples = samples;

      lines.push({
        index: i,
        t: t,
        base: baseY,
        amp: amp,
        samples: samples,
        color: color,
        alpha: alpha,
        emph: emph,
        bandScale: bandScale,
        bandDepth: bandDepth,
        isLast: isLast,
        freq: freq
      });
    }

    /* 若整体被分离约束推得过低，则将全部水线整体上移 */
    var maxY = -Infinity, minY = Infinity;
    for (i = 0; i < lines.length; i++) {
      for (var s = 0; s < lines[i].samples.length; s++) {
        maxY = Math.max(maxY, lines[i].samples[s].y);
        minY = Math.min(minY, lines[i].samples[s].y);
      }
    }
    var shift = 0;
    if (maxY > FIELD.bottom + 0.008) shift = -(maxY - (FIELD.bottom + 0.008));
    if (minY + shift < FIELD.top - 0.03) shift += (FIELD.top - 0.03) - (minY + shift);
    if (shift !== 0) {
      for (i = 0; i < lines.length; i++) {
        for (var q = 0; q < lines[i].samples.length; q++) lines[i].samples[q].y += shift;
        lines[i].base += shift;
      }
    }

    var grainTotal = 0;

    for (i = 0; i < lines.length; i++) {
      var line = lines[i];
      var t = line.t;
      var cum = [];
      var totalLen = 0;
      var prev = null;
      for (var c = 0; c < line.samples.length; c++) {
        var pt = line.samples[c];
        if (prev) {
          var dx = pt.x - prev.x;
          var dy = pt.y - prev.y;
          totalLen += Math.sqrt(dx * dx + dy * dy);
        }
        cum.push(totalLen);
        prev = pt;
      }

      (function (line, t, cum, totalLen) {
        var samples = line.samples;

        function idxAt(u) { return clamp(u, 0, 1) * N; }
        function yAt(u) {
          var f = idxAt(u), i0 = Math.floor(f), i1 = Math.min(N, i0 + 1), m = f - i0;
          return samples[i0].y + (samples[i1].y - samples[i0].y) * m;
        }
        function xAt(u) { return FIELD.left + (FIELD.right - FIELD.left) * clamp(u, 0, 1); }
        function tangentAt(u) {
          var u0 = clamp(u - 0.012, 0, 1), u1 = clamp(u + 0.012, 0, 1);
          return Math.atan2(yAt(u1) - yAt(u0), xAt(u1) - xAt(u0));
        }
        function lenAt(u) {
          var f = idxAt(u), i0 = Math.floor(f), i1 = Math.min(N, i0 + 1), m = f - i0;
          return cum[i0] + (cum[i1] - cum[i0]) * m;
        }

        /* 断口：浪破碎的位置，水线变淡或消失 */
        var gaps = [];
        var gapCount = Math.floor(rand() * 3.2);
        for (var gi = 0; gi < gapCount; gi++) {
          var gc = 0.08 + rand() * 0.84;
          var gw = 0.008 + rand() * 0.045;
          gaps.push({ a: clamp(gc - gw / 2, 0.02, 0.98), b: clamp(gc + gw / 2, 0.02, 0.98) });
        }

        /* 沉积颗粒 */
        var count = Math.round(300 * density * (0.6 + 0.85 * rand()) * (0.7 + 0.7 * t) * (0.8 + 0.5 * density));
        var grains = [];
        var clusterCount = 2 + Math.floor(rand() * 3);
        var clusters = [];
        for (var ci = 0; ci < clusterCount; ci++) clusters.push(0.1 + rand() * 0.8);

        for (var g = 0; g < count; g++) {
          var gu;
          if (rand() < 0.42) {
            var center = clusters[Math.floor(rand() * clusters.length)];
            var spread = 0.03 + rand() * 0.11;
            gu = clamp(center + (rand() + rand() - 1) * spread, 0.005, 0.995);
          } else {
            gu = rand();
          }
          var gx = xAt(gu);
          var gyBase = yAt(gu);
          var depth = Math.pow(rand(), 2);
          var above = rand() < 0.06;
          var off = above ? -0.25 * rand() : depth * line.bandDepth;
          var gy = Math.min(gyBase + off, FIELD.bottom + 0.028);
          var size = 0.55 + Math.pow(rand(), 1.7) * 2.3;
          var kindRoll = rand();
          var kind = kindRoll < 0.62 ? 'dot' : (kindRoll < 0.88 ? 'dash' : 'pebble');
          var ang = tangentAt(gu) + (rand() - 0.5) * 0.3;
          var th = Math.max(0.04 + rand() * 0.9, 0.12 + 0.86 * gu);
          var col = grainColor(rand, above ? 0 : depth);
          var ga = (0.22 + rand() * 0.52) * (0.85 + 0.3 * t);
          grains.push({
            x: gx, y: gy, size: size, kind: kind, angle: ang,
            color: col, alpha: clamp(ga, 0, 0.95), th: th
          });
        }
        grains.sort(function (a, b) { return a.th - b.th; });

        /* 积水斑 */
        var pools = [];
        var poolCount = 1 + Math.floor(rand() * 2);
        for (var pi = 0; pi < poolCount; pi++) {
          var pu = 0.1 + rand() * 0.8;
          pools.push({
            x: xAt(pu), y: yAt(pu),
            r: gapBase * (0.13 + rand() * 0.24),
            len: lenAt(pu),
            alpha: 0.09 + rand() * 0.11
          });
        }
        pools.sort(function (a, b) { return a.len - b.len; });

        /* 湿晕 */
        var blooms = [];
        var bloomCount = 1 + Math.floor(rand() * 3);
        for (var bi = 0; bi < bloomCount; bi++) {
          var bu = 0.06 + rand() * 0.88;
          blooms.push({
            x: xAt(bu),
            y: Math.min(yAt(bu) + line.bandDepth * (0.15 + rand() * 0.8), FIELD.bottom + 0.02),
            r: 0.024 + rand() * 0.055,
            alpha: 0.04 + rand() * 0.065,
            th: Math.max(0.3 + rand() * 0.4, 0.4 + 0.5 * bu)
          });
        }
        blooms.sort(function (a, b) { return a.th - b.th; });

        /* 盐霜 */
        var salts = [];
        var saltCount = 10 + Math.floor(rand() * 16);
        for (var si = 0; si < saltCount; si++) {
          var su = rand();
          salts.push({
            x: xAt(su),
            y: Math.min(yAt(su) + Math.pow(rand(), 1.5) * line.bandDepth * 0.9, FIELD.bottom + 0.026),
            size: 0.35 + rand() * 0.8,
            alpha: 0.2 + rand() * 0.45,
            th: Math.max(0.45 + rand() * 0.45, 0.5 + 0.45 * su)
          });
        }
        salts.sort(function (a, b) { return a.th - b.th; });

        /* 贝壳碎屑 */
        var shells = [];
        var shellCount = rand() < 0.55 ? 1 : 2;
        for (var hi = 0; hi < shellCount; hi++) {
          var hu = 0.08 + rand() * 0.84;
          shells.push({
            x: xAt(hu),
            y: Math.min(yAt(hu) + line.bandDepth * (0.15 + rand() * 0.7), FIELD.bottom + 0.02),
            r: 0.005 + rand() * 0.011,
            rot: rand() * TAU,
            alpha: 0.28 + rand() * 0.35,
            th: Math.max(0.55 + rand() * 0.35, 0.6 + 0.35 * hu)
          });
        }
        shells.sort(function (a, b) { return a.th - b.th; });

        var level = clamp((1 - (line.base - FIELD.top) / span) * 5.9 + 0.36, 0.2, 6.4);

        line.cum = cum;
        line.totalLen = totalLen;
        line.gaps = gaps;
        line.grains = grains;
        line.pools = pools;
        line.blooms = blooms;
        line.salts = salts;
        line.shells = shells;
        line.level = level.toFixed(2);
        grainTotal += grains.length;
      })(line, t, cum, totalLen);
    }

    return {
      version: 1,
      seed: seed,
      params: { seed: seed, tideRange: tideRange, tides: tides, density: density },
      field: FIELD,
      lines: lines,
      grainCount: grainTotal,
      dial: { angle: rand() * TAU, phase: rand() }
    };
  }

  root.TidalArchive = {
    FIELD: FIELD,
    PALETTE: PALETTE,
    clamp: clamp,
    hashSeed: hashSeed,
    rng: rng,
    makeNoise: makeNoise,
    mixHex: mixHex,
    build: build
  };
})(typeof window !== 'undefined' ? window : globalThis);
