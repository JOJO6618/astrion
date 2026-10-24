/*
 * Tidal Archive / 潮汐档案
 * 交互层：p5 画布、沉积动画、参数与导出。
 */
(function () {
  'use strict';

  var A = window.TidalArchive;
  var Painter = window.TidalPainter && window.TidalPainter.Painter;
  var DURATION = 9000;

  var params = { seed: 'tangent-01', tideRange: 1, tides: 9, density: 1 };

  var state = {
    W: 0, H: 0, dpr: 1,
    progress: 0,
    playing: false,
    scene: null,
    painter: null,
    buffer: null,
    ready: false,
    lastStatusPct: -1,
    exporting: false
  };

  var inst = null;
  var ui = {};
  var resizeTimer = 0;
  var paramTimer = 0;
  var statusTimer = 0;

  function $(id) { return document.getElementById(id); }
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function measure() {
    var stage = $('stage');
    if (!stage) return { w: 600, h: 720 };
    var styles = window.getComputedStyle(stage);
    var padX = (parseFloat(styles.paddingLeft) || 0) + (parseFloat(styles.paddingRight) || 0);
    var availW = Math.max(250, stage.clientWidth - padX);
    var narrow = window.innerWidth < 1020;
    var availH = Math.max(320, window.innerHeight * (narrow ? 0.56 : 0.76));
    var w = Math.min(availW, availH * (5 / 6));
    w = Math.max(250, Math.floor(w));
    return { w: w, h: Math.round(w * 1.2) };
  }

  /* ----------------------------- 画布与状态 ----------------------------- */

  function setup() {
    var size = measure();
    var dpr = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    inst.pixelDensity(dpr);
    var c = inst.createCanvas(size.w, size.h);
    c.parent('canvas-mount');
    state.W = size.w;
    state.H = size.h;
    state.dpr = dpr;
    state.ready = true;
    build({ animate: !prefersReducedMotion() });
  }

  function draw() {
    if (!state.buffer) return;
    if (state.playing) advance(inst.deltaTime);
    inst.image(state.buffer, 0, 0, state.W, state.H);
  }

  function advance(dt) {
    var step = Math.min(90, Math.max(0, dt)) / DURATION;
    var next = Math.min(1, state.progress + step);
    if (next <= state.progress) return;
    state.progress = next;
    state.painter.paintUpTo(next);
    updateStatus(false);
    if (next >= 1) {
      state.playing = false;
      inst.noLoop();
      updatePlayLabel();
      updateStatus(true);
    }
  }

  function build(opts) {
    opts = opts || {};
    state.scene = A.build(params);
    if (!state.ready) return;
    state.progress = opts.animate ? 0 : 1;
    state.playing = !!opts.animate;
    recreateBuffer();
    state.painter = new Painter(state.buffer, state.W, state.H, state.scene);
    state.painter.paintUpTo(state.progress);
    updateReadout();
    updateStatus(true);
    updatePlayLabel();
    updateCanvasLabel();
    if (state.playing) {
      inst.loop();
    } else {
      inst.noLoop();
      inst.redraw();
    }
  }

  function recreateBuffer() {
    if (state.buffer) {
      state.buffer.remove();
      state.buffer = null;
    }
    var g = inst.createGraphics(state.W, state.H);
    g.pixelDensity(state.dpr);
    g.clear();
    state.buffer = g;
  }

  function updateCanvasLabel() {
    if (!state.scene || !inst || !inst.canvas) return;
    inst.canvas.setAttribute('role', 'img');
    inst.canvas.setAttribute('aria-label',
      '潮汐档案生成作品：种子 ' + state.scene.seed + '，' + state.scene.lines.length +
      ' 道水线，' + state.scene.grainCount + ' 粒沉积。可用右侧控件或快捷键操作。');
  }

  /* ----------------------------- 状态文字 ----------------------------- */

  function setStatusText(text) {
    if (ui.status) ui.status.textContent = text;
  }

  function updateStatus(force) {
    var pct = Math.round(state.progress * 100);
    if (!force) {
      if (!state.playing) return;
      if (Math.abs(pct - state.lastStatusPct) < 4) return;
    }
    state.lastStatusPct = pct;
    var text;
    if (state.progress >= 1) text = '档案完成';
    else if (state.playing) text = '正在沉积 ' + pct + '%';
    else text = '已暂停 ' + pct + '%';
    setStatusText(text);
    updateReadoutState();
  }

  function updateReadoutState() {
    if (!ui.statState) return;
    ui.statState.textContent = state.progress >= 1 ? '完成' : (state.playing ? '沉积中' : '暂停');
  }

  function updateReadout() {
    if (!state.scene) return;
    if (ui.statLines) ui.statLines.textContent = String(state.scene.lines.length);
    if (ui.statGrains) ui.statGrains.textContent = state.scene.grainCount.toLocaleString('zh-CN');
    updateReadoutState();
  }

  function updatePlayLabel() {
    if (!ui.togglePlay) return;
    var label = state.progress >= 1 ? '重播' : (state.playing ? '暂停' : '继续');
    ui.togglePlay.textContent = label;
    ui.togglePlay.setAttribute('aria-label',
      label === '重播' ? '重播沉积动画' : (label === '暂停' ? '暂停沉积动画' : '继续沉积动画'));
  }

  /* ----------------------------- 动作 ----------------------------- */

  function togglePlay() {
    if (!state.scene || !state.ready) return;
    if (state.progress >= 1) {
      replay();
      return;
    }
    state.playing = !state.playing;
    updatePlayLabel();
    updateStatus(true);
    if (state.playing) inst.loop();
    else {
      inst.noLoop();
      inst.redraw();
    }
  }

  function replay() {
    if (!state.scene || !state.ready) return;
    state.progress = 0;
    state.playing = true;
    state.buffer.clear();
    state.painter = new Painter(state.buffer, state.W, state.H, state.scene);
    state.painter.paintUpTo(0);
    updatePlayLabel();
    updateStatus(true);
    inst.loop();
  }

  function randomSeed() {
    var words = ['spring', 'neap', 'ebb', 'silt', 'salt', 'moon', 'shoal',
      'drift', 'wrack', 'foam', 'oyster', 'marram'];
    var n;
    if (window.crypto && window.crypto.getRandomValues) {
      var arr = new Uint32Array(1);
      window.crypto.getRandomValues(arr);
      n = arr[0];
    } else {
      n = Math.floor(Math.random() * 4294967296);
    }
    var word = words[n % words.length];
    var digits = String(1000 + (Math.floor(n / words.length) % 9000));
    return word + '-' + digits;
  }

  function applySeed(animate) {
    var v = ui.seedInput ? ui.seedInput.value.trim() : params.seed;
    params.seed = v === '' ? 'tidal-01' : v;
    if (ui.seedInput) ui.seedInput.value = params.seed;
    build({ animate: animate !== false });
  }

  function regenerate() {
    params.seed = randomSeed();
    if (ui.seedInput) ui.seedInput.value = params.seed;
    build({ animate: true });
  }

  function fileName() {
    var seed = (state.scene && state.scene.seed) || 'seed';
    var slug = seed.toLowerCase().replace(/[^\w\u4e00-\u9fa5-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 26) || 'seed';
    var p = state.scene ? state.scene.params : params;
    return 'tidal-archive_' + slug + '_' + p.tides + 'lines_' +
      Math.round(p.tideRange * 100) + '-' + Math.round(p.density * 100) + '.png';
  }

  function exportPNG() {
    if (!state.scene || !state.ready || state.exporting) return;
    state.exporting = true;
    setStatusText('正在导出…');
    var scale = 2;
    var eg = null;
    try {
      eg = inst.createGraphics(state.W * scale, state.H * scale);
      eg.pixelDensity(1);
      var painter = new Painter(eg, state.W * scale, state.H * scale, state.scene);
      painter.paintUpTo(1);
    } catch (err) {
      state.exporting = false;
      setStatusText('导出失败：无法创建导出画布');
      if (eg) eg.remove();
      return;
    }
    var name = fileName();
    var finish = function () {
      state.exporting = false;
      if (eg) eg.remove();
      clearTimeout(statusTimer);
      statusTimer = setTimeout(function () { state.lastStatusPct = -1; updateStatus(true); }, 3200);
    };
    var download = function (href, revoke) {
      var a = document.createElement('a');
      a.href = href;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      if (revoke) setTimeout(function () { URL.revokeObjectURL(href); }, 5000);
      setStatusText('已导出 ' + name);
      finish();
    };
    if (eg.canvas.toBlob) {
      eg.canvas.toBlob(function (blob) {
        if (!blob) {
          state.exporting = false;
          setStatusText('导出失败，请重试');
          if (eg) eg.remove();
          return;
        }
        download(URL.createObjectURL(blob), true);
      }, 'image/png');
    } else {
      try {
        download(eg.canvas.toDataURL('image/png'), false);
      } catch (err2) {
        state.exporting = false;
        setStatusText('导出失败：浏览器限制了画布导出');
        if (eg) eg.remove();
      }
    }
  }

  /* ----------------------------- 参数与界面 ----------------------------- */

  function readParams() {
    if (ui.tideRange) {
      params.tideRange = parseFloat(ui.tideRange.value);
      ui.tideRangeVal.textContent = params.tideRange.toFixed(2);
    }
    if (ui.tideCount) {
      params.tides = parseInt(ui.tideCount.value, 10);
      ui.tideCountVal.textContent = String(params.tides);
    }
    if (ui.density) {
      params.density = parseFloat(ui.density.value);
      ui.densityVal.textContent = params.density.toFixed(2);
    }
    updateSliderFill();
  }

  function updateSliderFill() {
    var pairs = [
      [ui.tideRange, 0.4, 1.8],
      [ui.tideCount, 5, 14],
      [ui.density, 0.35, 1.8]
    ];
    for (var i = 0; i < pairs.length; i++) {
      var el = pairs[i][0];
      if (!el) continue;
      var v = parseFloat(el.value);
      var pct = clamp((v - pairs[i][1]) / (pairs[i][2] - pairs[i][1]), 0, 1) * 100;
      el.style.setProperty('--fill', pct.toFixed(2) + '%');
    }
  }

  function scheduleParamApply() {
    readParams();
    clearTimeout(paramTimer);
    paramTimer = setTimeout(function () {
      build({ animate: false });
    }, 90);
  }

  function handleResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (!state.ready) return;
      var size = measure();
      if (size.w === state.W && size.h === state.H) return;
      state.W = size.w;
      state.H = size.h;
      inst.resizeCanvas(size.w, size.h);
      recreateBuffer();
      state.painter = new Painter(state.buffer, state.W, state.H, state.scene);
      state.painter.paintUpTo(state.progress);
      inst.redraw();
    }, 140);
  }

  function onKeyDown(e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target && e.target.tagName ? e.target.tagName : '').toLowerCase();
    var typing = tag === 'input' || tag === 'textarea' || tag === 'select';
    if (e.code === 'Space' && !typing && tag !== 'button') {
      e.preventDefault();
      togglePlay();
      return;
    }
    if (typing) return;
    if (e.key === 'r' || e.key === 'R') {
      e.preventDefault();
      replay();
    } else if (e.key === 'e' || e.key === 'E') {
      e.preventDefault();
      exportPNG();
    }
  }

  function cacheElements() {
    ui.status = $('status');
    ui.seedInput = $('seed-input');
    ui.applySeed = $('apply-seed');
    ui.regenerate = $('regenerate');
    ui.togglePlay = $('toggle-play');
    ui.replay = $('replay');
    ui.export = $('export');
    ui.tideRange = $('tide-range');
    ui.tideRangeVal = $('tide-range-val');
    ui.tideCount = $('tide-count');
    ui.tideCountVal = $('tide-count-val');
    ui.density = $('density');
    ui.densityVal = $('density-val');
    ui.statLines = $('stat-lines');
    ui.statGrains = $('stat-grains');
    ui.statState = $('stat-state');
  }

  function wire() {
    if (ui.applySeed) ui.applySeed.addEventListener('click', function () { applySeed(true); });
    if (ui.regenerate) ui.regenerate.addEventListener('click', regenerate);
    if (ui.togglePlay) ui.togglePlay.addEventListener('click', togglePlay);
    if (ui.replay) ui.replay.addEventListener('click', replay);
    if (ui.export) ui.export.addEventListener('click', exportPNG);

    [ui.tideRange, ui.tideCount, ui.density].forEach(function (el) {
      if (el) el.addEventListener('input', scheduleParamApply);
    });

    if (ui.seedInput) {
      ui.seedInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          applySeed(true);
        }
      });
    }

    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('resize', handleResize);
    if (typeof ResizeObserver !== 'undefined' && $('stage')) {
      var ro = new ResizeObserver(handleResize);
      ro.observe($('stage'));
    }
  }

  function fail(message) {
    var mount = $('canvas-mount');
    if (mount) {
      var p = document.createElement('p');
      p.className = 'stage__fallback';
      p.textContent = message;
      mount.appendChild(p);
    }
    setStatusText('无法开始生成');
  }

  function init() {
    cacheElements();
    wire();
    readParams();
    if (!A || !Painter) {
      fail('生成器脚本未能加载，请检查 js/archive.js 与 js/painter.js。');
      return;
    }
    if (!window.p5) {
      fail('p5.js 未能加载，请检查 js/p5.min.js 是否存在。');
      return;
    }
    var sketch = function (p) {
      inst = p;
      p.setup = setup;
      p.draw = draw;
    };
    new window.p5(sketch);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
