/*
 * Tidal Archive / 潮汐档案
 * 画家：把生成器的归一化场景逐段画到任意 Canvas 2D 上下文。
 * 只使用 drawingContext，因此可复用于屏幕缓冲、2 倍导出与离线自检。
 */
(function (root) {
  'use strict';

  var A = root.TidalArchive;
  var TAU = Math.PI * 2;
  var FIELD = A.FIELD;

  var SERIF = '"Songti SC","STSong","Noto Serif SC","Source Han Serif SC",Georgia,serif';
  var SANS = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif';
  var MONO = '"SF Mono","JetBrains Mono",Menlo,Consolas,monospace';
  var LATIN = 'Georgia,"Times New Roman",serif';

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
  function rgba(hex, alpha) {
    var h = hex.replace('#', '');
    return 'rgba(' + parseInt(h.slice(0, 2), 16) + ',' + parseInt(h.slice(2, 4), 16) + ',' +
      parseInt(h.slice(4, 6), 16) + ',' + alpha + ')';
  }

  function drawText(ctx, text, x, y, opt) {
    opt = opt || {};
    var size = opt.size || 10;
    ctx.save();
    ctx.font = (opt.weight || '400') + ' ' + size.toFixed(2) + 'px ' + (opt.family || SERIF);
    ctx.fillStyle = opt.color || '#1b2a33';
    ctx.globalAlpha = opt.alpha == null ? 1 : opt.alpha;
    var align = opt.align || 'left';
    ctx.textAlign = align;
    ctx.textBaseline = opt.baseline || 'alphabetic';
    var str = String(text);
    if (opt.tracking) {
      var chars = str.split('');
      var widths = [], total = 0, i;
      for (i = 0; i < chars.length; i++) {
        widths.push(ctx.measureText(chars[i]).width + opt.tracking);
        total += widths[i];
      }
      total -= opt.tracking;
      var startX = x;
      if (align === 'right') startX = x - total;
      else if (align === 'center') startX = x - total / 2;
      ctx.textAlign = 'left';
      var cursor = startX;
      for (i = 0; i < chars.length; i++) {
        ctx.fillText(chars[i], cursor, y);
        cursor += widths[i];
      }
    } else {
      ctx.fillText(str, x, y);
    }
    ctx.restore();
  }

  function lenToPoint(line, len) {
    var cum = line.cum, samples = line.samples;
    var hi = cum.length - 1;
    if (len <= 0) return samples[0];
    if (len >= cum[hi]) return samples[hi];
    var lo = 0;
    while (lo < hi - 1) {
      var mid = (lo + hi) >> 1;
      if (cum[mid] <= len) lo = mid; else hi = mid;
    }
    var seg = cum[hi] - cum[lo] || 1e-9;
    var f = (len - cum[lo]) / seg;
    var a = samples[lo], b = samples[hi];
    return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, u: a.u + (b.u - a.u) * f };
  }

  function inGap(gaps, u) {
    for (var i = 0; i < gaps.length; i++) {
      if (u > gaps[i].a && u < gaps[i].b) return true;
    }
    return false;
  }

  /* ------------------------------------------------------------------ */

  function Painter(g, W, H, scene) {
    this.g = g;
    this.ctx = g.drawingContext;
    this.W = W;
    this.H = H;
    this.scene = scene;
    this.unit = Math.min(W, H);
    this.baseDone = false;
    this.footerDone = false;
    this.states = scene.lines.map(function () {
      return { band: 0, ink: 0, pools: 0, grain: 0, bloom: 0, salt: 0, shell: 0, gauge: false };
    });
  }

  Painter.prototype.paintUpTo = function (progress) {
    var p = clamp(progress, 0, 1);
    if (!this.baseDone) {
      this.paintPaper();
      this.paintFrame();
      this.paintHeader();
      this.paintDial();
      this.baseDone = true;
    }
    var lines = this.scene.lines;
    var n = lines.length;
    for (var i = 0; i < n; i++) {
      var start = 0.02 + (i / n) * 0.8;
      var end = Math.min(1.0001, start + 0.34);
      var t = clamp((p - start) / (end - start), 0, 1);
      if (t <= 0) continue;
      this.paintLine(lines[i], i, t);
    }
    if (p >= 0.999 && !this.footerDone) {
      this.paintFooter();
      this.footerDone = true;
    }
  };

  Painter.prototype.paintPaper = function () {
    var ctx = this.ctx, W = this.W, H = this.H, u = this.unit;
    var grad = ctx.createLinearGradient(W * 0.12, -H * 0.05, W * 0.88, H * 1.02);
    grad.addColorStop(0, '#f5eee0');
    grad.addColorStop(0.48, '#ece2cf');
    grad.addColorStop(1, '#e0d3ba');
    ctx.save();
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);
    ctx.restore();

    var rand = A.rng(A.hashSeed(this.scene.seed + '::paper'));

    /* 纸浆云斑 */
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    var mottles = 18;
    for (var m = 0; m < mottles; m++) {
      var mx = rand() * W, my = rand() * H;
      var mr = (0.12 + rand() * 0.3) * u;
      var grd = ctx.createRadialGradient(mx, my, 0, mx, my, mr);
      grd.addColorStop(0, 'rgba(146,120,88,' + (0.012 + rand() * 0.022) + ')');
      grd.addColorStop(0.7, 'rgba(146,120,88,0.006)');
      grd.addColorStop(1, 'rgba(146,120,88,0)');
      ctx.fillStyle = grd;
      ctx.fillRect(mx - mr, my - mr, mr * 2, mr * 2);
    }
    ctx.restore();

    /* 纸纤维 */
    var fibers = Math.round(clamp(W * H / 1100, 480, 2400));
    ctx.save();
    ctx.lineCap = 'round';
    for (var f = 0; f < fibers; f++) {
      var fx = rand() * W, fy = rand() * H;
      var len = (1.5 + rand() * 7) * (u / 700);
      var ang = (rand() - 0.5) * 0.9 + (rand() < 0.3 ? Math.PI / 2 : 0);
      var dark = rand() < 0.55;
      ctx.strokeStyle = dark
        ? 'rgba(104,86,60,' + (0.02 + rand() * 0.035) + ')'
        : 'rgba(255,250,238,' + (0.03 + rand() * 0.05) + ')';
      ctx.lineWidth = Math.max(0.35, u * 0.0007 * (0.6 + rand()));
      ctx.beginPath();
      ctx.moveTo(fx, fy);
      ctx.lineTo(fx + Math.cos(ang) * len, fy + Math.sin(ang) * len);
      ctx.stroke();
    }
    ctx.restore();

    /* 细颗粒 */
    var specks = Math.round(clamp(W * H / 2200, 260, 1200));
    ctx.save();
    for (var s = 0; s < specks; s++) {
      ctx.fillStyle = 'rgba(92,74,52,' + (0.02 + rand() * 0.05) + ')';
      var ss = Math.max(0.3, u * 0.0011 * (0.4 + rand()));
      ctx.fillRect(rand() * W, rand() * H, ss, ss);
    }
    ctx.restore();

    /* 边缘压暗 */
    var vg = ctx.createRadialGradient(W * 0.5, H * 0.46, u * 0.28, W * 0.5, H * 0.5, Math.max(W, H) * 0.82);
    vg.addColorStop(0, 'rgba(84,66,44,0)');
    vg.addColorStop(0.72, 'rgba(84,66,44,0.05)');
    vg.addColorStop(1, 'rgba(84,66,44,0.16)');
    ctx.save();
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
  };

  Painter.prototype.paintFrame = function () {
    var ctx = this.ctx, W = this.W, H = this.H, u = this.unit;
    var inset = u * 0.026;
    ctx.save();
    ctx.strokeStyle = 'rgba(27,42,51,0.22)';
    ctx.lineWidth = Math.max(0.5, u * 0.0011);
    ctx.strokeRect(inset, inset, W - inset * 2, H - inset * 2);
    /* 四角登记记号 */
    var cm = u * 0.012;
    var corners = [[inset, inset, 1, 1], [W - inset, inset, -1, 1], [inset, H - inset, 1, -1], [W - inset, H - inset, -1, -1]];
    ctx.strokeStyle = 'rgba(27,42,51,0.32)';
    ctx.lineWidth = Math.max(0.5, u * 0.0014);
    for (var i = 0; i < corners.length; i++) {
      var c = corners[i];
      ctx.beginPath();
      ctx.moveTo(c[0] + c[2] * cm, c[1]);
      ctx.lineTo(c[0], c[1]);
      ctx.lineTo(c[0], c[1] + c[3] * cm);
      ctx.stroke();
    }
    ctx.restore();
  };

  Painter.prototype.paintHeader = function () {
    var ctx = this.ctx, W = this.W, H = this.H;
    var left = W * FIELD.left;
    var titleSize = Math.max(15, H * 0.051);
    drawText(ctx, '生成档案 · TIDAL ARCHIVE', left, H * 0.047, {
      size: Math.max(7.5, H * 0.0118), family: SANS, tracking: Math.max(0.6, H * 0.0014),
      color: '#4c5b60', alpha: 0.92
    });
    drawText(ctx, '潮汐档案', left, H * 0.112, {
      size: titleSize, family: SERIF, weight: '500', color: '#16242e', alpha: 0.96
    });
    drawText(ctx, 'SEDIMENT RECORD / 沉积剖面', left, H * 0.143, {
      size: Math.max(6.5, H * 0.0102), family: SANS, tracking: Math.max(0.5, H * 0.0016),
      color: '#59696c', alpha: 0.88
    });
  };

  Painter.prototype.paintDial = function () {
    var ctx = this.ctx, W = this.W, H = this.H, u = this.unit;
    var cx = W * 0.858, cy = H * 0.094, r = u * 0.05;
    var dial = this.scene.dial;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.strokeStyle = 'rgba(27,42,51,0.3)';
    ctx.lineWidth = Math.max(0.5, r * 0.02);
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, TAU);
    ctx.stroke();
    for (var i = 0; i < 24; i++) {
      var big = i % 6 === 0;
      var a = (i / 24) * TAU;
      var r0 = r * (big ? 0.72 : 0.84);
      ctx.strokeStyle = big ? 'rgba(27,42,51,0.45)' : 'rgba(27,42,51,0.22)';
      ctx.lineWidth = Math.max(0.4, r * (big ? 0.028 : 0.016));
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
      ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r);
      ctx.stroke();
    }
    /* 指针：退潮角 */
    ctx.strokeStyle = 'rgba(141,74,48,0.72)';
    ctx.lineWidth = Math.max(0.6, r * 0.035);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    ctx.lineTo(Math.cos(dial.angle) * r * 0.78, Math.sin(dial.angle) * r * 0.78);
    ctx.stroke();
    ctx.fillStyle = 'rgba(27,42,51,0.55)';
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(0.7, r * 0.05), 0, TAU);
    ctx.fill();
    ctx.restore();

    var labelSize = Math.max(5.5, H * 0.0088);
    drawText(ctx, '潮汐轮', cx, cy + r + labelSize * 1.9, {
      size: labelSize, family: SANS, align: 'center', color: '#59696c', alpha: 0.8, tracking: 0.6
    });
  };

  Painter.prototype.strokeRun = function (line, from, to, offsetPx) {
    var ctx = this.ctx, W = this.W, H = this.H;
    var step = 2.4 / Math.max(W, H);
    var len = from;
    var active = false;
    var guard = 0;
    while (len < to && guard++ < 8000) {
      var pt = lenToPoint(line, len);
      if (inGap(line.gaps, pt.u)) {
        if (active) { ctx.stroke(); active = false; }
      } else {
        var px = pt.x * W, py = pt.y * H + offsetPx;
        if (!active) { ctx.beginPath(); ctx.moveTo(px, py); active = true; }
        else ctx.lineTo(px, py);
      }
      len += step;
    }
    if (active) {
      var last = lenToPoint(line, to);
      if (!inGap(line.gaps, last.u)) ctx.lineTo(last.x * W, last.y * H + offsetPx);
      ctx.stroke();
    }
  };

  Painter.prototype.drawBand = function (line, from, to) {
    var ctx = this.ctx, H = this.H;
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (var k = 1; k <= 4; k++) {
      ctx.strokeStyle = 'rgba(118,148,148,' + (0.055 / k) + ')';
      ctx.lineWidth = H * 0.0085 * k * line.bandScale;
      this.strokeRun(line, from, to, H * 0.0055 * k * line.bandScale);
    }
    ctx.restore();
  };

  Painter.prototype.drawInk = function (line, from, to) {
    var ctx = this.ctx, H = this.H;
    var base = clamp(H * 0.0019, 0.55, 2.6) * line.emph;
    var passes = [
      { off: -1.2, w: base * 2.4, a: 0.1 },
      { off: 0, w: base, a: 0.52 },
      { off: 0.5, w: base * 0.5, a: 0.62 }
    ];
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (var i = 0; i < passes.length; i++) {
      var pass = passes[i];
      ctx.strokeStyle = rgba(line.color, clamp(line.alpha * pass.a, 0, 1));
      ctx.lineWidth = pass.w;
      this.strokeRun(line, from, to, pass.off);
    }
    ctx.restore();
  };

  Painter.prototype.drawPool = function (pool) {
    var ctx = this.ctx, W = this.W, H = this.H;
    var cx = pool.x * W, cy = pool.y * H, r = Math.max(2, pool.r * H);
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    var grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    grd.addColorStop(0, 'rgba(56,86,98,' + pool.alpha + ')');
    grd.addColorStop(0.55, 'rgba(78,108,114,' + (pool.alpha * 0.5) + ')');
    grd.addColorStop(1, 'rgba(78,108,114,0)');
    ctx.fillStyle = grd;
    ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
    ctx.restore();
  };

  Painter.prototype.drawBloom = function (bloom) {
    var ctx = this.ctx, W = this.W, H = this.H;
    var cx = bloom.x * W, cy = bloom.y * H, r = Math.max(3, bloom.r * H);
    ctx.save();
    ctx.globalCompositeOperation = 'multiply';
    var grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    grd.addColorStop(0, 'rgba(84,116,120,' + bloom.alpha + ')');
    grd.addColorStop(0.6, 'rgba(96,126,128,' + (bloom.alpha * 0.55) + ')');
    grd.addColorStop(1, 'rgba(96,126,128,0)');
    ctx.fillStyle = grd;
    ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
    ctx.restore();
  };

  Painter.prototype.drawGrain = function (grain, line) {
    var ctx = this.ctx, W = this.W, H = this.H;
    var x = grain.x * W, y = grain.y * H;
    var scale = 0.66 + 0.34 * (H / 900);
    var size = Math.max(0.5, grain.size * scale);
    var alpha = clamp(grain.alpha, 0, 1);
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.fillStyle = grain.color;
    if (grain.kind === 'dash') {
      ctx.strokeStyle = grain.color;
      ctx.lineWidth = Math.max(0.4, size * 0.85);
      ctx.lineCap = 'round';
      var len = size * 2.6;
      ctx.beginPath();
      ctx.moveTo(x - Math.cos(grain.angle) * len * 0.5, y - Math.sin(grain.angle) * len * 0.5);
      ctx.lineTo(x + Math.cos(grain.angle) * len * 0.5, y + Math.sin(grain.angle) * len * 0.5);
      ctx.stroke();
    } else if (grain.kind === 'pebble') {
      ctx.beginPath();
      ctx.ellipse(x, y, size * 1.15, size * 0.78, grain.angle, 0, TAU);
      ctx.fill();
      ctx.globalAlpha = alpha * 0.45;
      ctx.fillStyle = '#f3ead8';
      ctx.beginPath();
      ctx.ellipse(x - size * 0.25, y - size * 0.25, size * 0.45, size * 0.3, grain.angle, 0, TAU);
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.arc(x, y, Math.max(0.35, size * 0.55), 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  };

  Painter.prototype.drawSalt = function (salt) {
    var ctx = this.ctx, W = this.W, H = this.H;
    var r = Math.max(0.3, salt.size * (0.6 + 0.4 * (H / 900)) * 0.5);
    ctx.save();
    ctx.globalAlpha = salt.alpha;
    ctx.fillStyle = '#fbf5e7';
    ctx.beginPath();
    ctx.arc(salt.x * W, salt.y * H, r, 0, TAU);
    ctx.fill();
    ctx.restore();
  };

  Painter.prototype.drawShell = function (shell) {
    var ctx = this.ctx, W = this.W, H = this.H;
    var r = Math.max(1.6, shell.r * H);
    var x = shell.x * W, y = shell.y * H;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(shell.rot);
    ctx.globalAlpha = shell.alpha;
    ctx.strokeStyle = '#f2e6d0';
    ctx.lineWidth = Math.max(0.5, r * 0.22);
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.arc(0, 0, r, Math.PI * 0.15, Math.PI * 0.95);
    ctx.stroke();
    ctx.globalAlpha = shell.alpha * 0.5;
    ctx.strokeStyle = '#8d6a4b';
    ctx.lineWidth = Math.max(0.4, r * 0.12);
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.62, Math.PI * 0.2, Math.PI * 0.9);
    ctx.stroke();
    ctx.restore();
  };

  Painter.prototype.drawGauge = function (line) {
    var ctx = this.ctx, W = this.W, H = this.H, u = this.unit;
    var y = line.base * H;
    var x0 = (FIELD.left - 0.03) * W, x1 = (FIELD.left - 0.011) * W;
    var size = Math.max(5.8, H * 0.0092);
    ctx.save();
    ctx.strokeStyle = 'rgba(35,52,60,0.42)';
    ctx.lineWidth = Math.max(0.5, u * 0.0011);
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x1, y);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x0, y - u * 0.0032);
    ctx.lineTo(x0, y + u * 0.0032);
    ctx.stroke();
    ctx.restore();
    drawText(ctx, line.level, x1, y - size * 0.5, {
      size: size, family: MONO, align: 'right', color: '#42545b', alpha: 0.82
    });
  };

  Painter.prototype.paintLine = function (line, index, t) {
    var st = this.states[index];
    var target = easeOutCubic(t) * line.totalLen;

    if (target > st.band + 1e-9) {
      this.drawBand(line, st.band, target);
      st.band = target;
    }
    if (target > st.ink + 1e-9) {
      this.drawInk(line, st.ink, target);
      st.ink = target;
    }
    while (st.pools < line.pools.length && line.pools[st.pools].len <= st.ink) {
      this.drawPool(line.pools[st.pools]);
      st.pools++;
    }
    while (st.grain < line.grains.length && line.grains[st.grain].th <= t) {
      this.drawGrain(line.grains[st.grain], line);
      st.grain++;
    }
    while (st.bloom < line.blooms.length && line.blooms[st.bloom].th <= t) {
      this.drawBloom(line.blooms[st.bloom]);
      st.bloom++;
    }
    while (st.salt < line.salts.length && line.salts[st.salt].th <= t) {
      this.drawSalt(line.salts[st.salt]);
      st.salt++;
    }
    while (st.shell < line.shells.length && line.shells[st.shell].th <= t) {
      this.drawShell(line.shells[st.shell]);
      st.shell++;
    }
    if (!st.gauge && t > 0.72) {
      this.drawGauge(line);
      st.gauge = true;
    }
  };

  Painter.prototype.paintFooter = function () {
    var ctx = this.ctx, W = this.W, H = this.H, u = this.unit;
    var left = W * FIELD.left, right = W * FIELD.right;
    var ruleY = H * 0.893;
    ctx.save();
    ctx.strokeStyle = 'rgba(27,42,51,0.26)';
    ctx.lineWidth = Math.max(0.5, u * 0.0011);
    ctx.beginPath();
    ctx.moveTo(left, ruleY);
    ctx.lineTo(right, ruleY);
    ctx.stroke();
    ctx.restore();

    var size = Math.max(6.2, H * 0.0102);
    var seed = this.scene.seed.length > 18 ? this.scene.seed.slice(0, 17) + '…' : this.scene.seed;
    drawText(ctx, '退潮剖面 · ' + this.scene.lines.length + ' 道水线 · ' + this.scene.grainCount + ' 粒沉积',
      left, H * 0.925, {
      size: size, family: SANS, tracking: 0.3, color: '#41525a', alpha: 0.88
    });
    drawText(ctx, 'SEED ' + seed.toUpperCase(), right, H * 0.925, {
      size: size, family: MONO, align: 'right', color: '#41525a', alpha: 0.82
    });
    drawText(ctx, '潮间带沉积模型 · 本地生成', left, H * 0.948, {
      size: Math.max(5.6, H * 0.0088), family: SANS, tracking: 0.3, color: '#5b6a6d', alpha: 0.7
    });
  };

  root.TidalPainter = { Painter: Painter };
})(typeof window !== 'undefined' ? window : globalThis);
