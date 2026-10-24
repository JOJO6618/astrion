/*
 * 潮汐档案 / Tidal Archive — 交互与呈现层
 * p5 只负责绘制；几何与随机性全部来自 js/core.js（自带种子，跨运行确定）。
 */
'use strict';

(function () {
  var CANVAS = 1400;
  var FONT_SANS = '"PingFang SC", "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif';
  var FONT_MONO = 'Menlo, Consolas, "Courier New", monospace';
  var SEED_MIN = 1;
  var SEED_MAX = 2147483647;

  var DEFAULTS = {
    seed: 6271,
    layers: 9,
    tidalRange: 0.62,
    sediment: 0.85,
    erosion: 0.45,
    palette: 'ebb'
  };

  var params = Object.assign({}, DEFAULTS);
  var plan = null;
  var plate = null;
  var sketch = null;
  var frameIndex = 0;
  var cursor = 0;
  var completed = false;
  var paused = false;
  var sheenPhase = 0;
  var lastLayerShown = -1;
  var manualPlay = false;
  var instantPending = false;
  var rebuildQueued = false;

  var motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  var reducedMotion = motionQuery.matches;

  var els = {
    status: document.getElementById('art-status'),
    seedValue: document.getElementById('seed-value'),
    seedInput: document.getElementById('seed-input'),
    seedJump: document.getElementById('seed-jump'),
    seedPrev: document.getElementById('seed-prev'),
    seedNext: document.getElementById('seed-next'),
    seedRandom: document.getElementById('seed-random'),
    replay: document.getElementById('replay'),
    reset: document.getElementById('reset'),
    pause: document.getElementById('pause'),
    exportBtn: document.getElementById('export'),
    canvas: document.getElementById('canvas-container'),
    sliders: Array.prototype.slice.call(document.querySelectorAll('[data-param]')),
    palettes: Array.prototype.slice.call(document.querySelectorAll('[data-palette]'))
  };

  /* ── 状态文字 ─────────────────────────────────────────── */
  function currentLayer() {
    if (!plan) return 1;
    if (completed) return plan.layers;
    return Math.min(plan.layers, Math.floor(frameIndex / plan.framesPerLayer) + 1);
  }

  function updateStatus(force) {
    if (!plan) return;
    var text;
    if (!sketch) {
      text = '正在载入本地 p5 运行时…';
    } else if (completed && paused) {
      text = '已暂停 · 沉积完成 · 种子 ' + plan.seed + ' · 共 ' + plan.layers + ' 层';
    } else if (completed) {
      text = (reducedMotion && !manualPlay)
        ? '已按系统「减弱动态」偏好直接呈现完整档案 · 可按「重新沉积」观看生成'
        : '沉积完成 · 种子 ' + plan.seed + ' · 共 ' + plan.layers + ' 层 · 可导出 PNG';
    } else if (paused) {
      text = '已暂停 · 第 ' + currentLayer() + '/' + plan.layers + ' 层 · 种子 ' + plan.seed;
    } else {
      text = '沉积中 · 第 ' + currentLayer() + '/' + plan.layers + ' 层 · 种子 ' + plan.seed;
    }
    if (force || els.status.textContent !== text) els.status.textContent = text;
  }

  function updateCanvasLabel() {
    if (!plan) return;
    els.canvas.setAttribute('aria-label',
      '潮汐档案：种子 ' + plan.seed + '，' + plan.layers + ' 层潮线，' +
      plan.paletteName + '色调，共 ' + plan.stats.marks + ' 个沉积标记');
  }

  /* ── 控件同步 ─────────────────────────────────────────── */
  function formatValue(slider, value) {
    var decimals = Number(slider.dataset.decimals || 0);
    var unit = slider.dataset.unit || '';
    return value.toFixed(decimals) + unit;
  }

  function syncControls() {
    els.seedValue.textContent = String(params.seed);
    if (document.activeElement !== els.seedInput) els.seedInput.value = String(params.seed);

    els.sliders.forEach(function (slider) {
      var name = slider.dataset.param;
      var value = params[name];
      var min = Number(slider.getAttribute('aria-valuemin'));
      var max = Number(slider.getAttribute('aria-valuemax'));
      var pos = max === min ? 0 : ((value - min) / (max - min)) * 100;
      slider.setAttribute('aria-valuenow', String(value));
      slider.setAttribute('aria-valuetext', formatValue(slider, value).trim());
      slider.style.setProperty('--pos', pos + '%');
      var out = document.getElementById(name + '-value');
      if (out) out.textContent = formatValue(slider, value);
    });

    els.palettes.forEach(function (btn) {
      btn.setAttribute('aria-pressed', btn.dataset.palette === params.palette ? 'true' : 'false');
    });

    els.pause.textContent = paused ? '继续' : '暂停';
    els.pause.setAttribute('aria-pressed', paused ? 'true' : 'false');
  }

  /* ── 计划与画面 ───────────────────────────────────────── */
  function buildPlan() {
    plan = window.TidalCore.createPlan({
      seed: params.seed,
      layers: params.layers,
      tidalRange: params.tidalRange,
      sediment: params.sediment,
      erosion: params.erosion,
      palette: params.palette
    });
    updateCanvasLabel();
  }

  function drawMark(g, m) {
    if (m.t === 'seg') {
      g.noFill();
      g.stroke(m.c[0], m.c[1], m.c[2], m.a);
      g.strokeWeight(m.w);
      g.line(m.x1, m.y1, m.x2, m.y2);
    } else if (m.t === 'dot') {
      g.noStroke();
      g.fill(m.c[0], m.c[1], m.c[2], m.a);
      g.ellipse(m.x, m.y, m.rx * 2, m.ry * 2);
    } else {
      g.push();
      g.noStroke();
      g.fill(m.c[0], m.c[1], m.c[2], m.a);
      g.textFont(m.mono ? FONT_MONO : FONT_SANS);
      g.textSize(m.size);
      g.textAlign(m.align === 'right' ? 'right' : 'left', 'alphabetic');
      g.text(m.s, m.x, m.y);
      g.pop();
    }
  }

  function paintPlate() {
    if (!plate || !plan) return;
    plate.clear();
    plate.background(plan.paper[0], plan.paper[1], plan.paper[2]);
    // 纸面自浅入深：越往下越接近埋藏层的暖色，给平面一点深度
    var bands = 48;
    var bandH = CANVAS / bands;
    plate.noStroke();
    for (var b = 0; b < bands; b++) {
      var depth = b / (bands - 1);
      plate.fill(plan.sepia[0], plan.sepia[1], plan.sepia[2], 9 * Math.pow(depth, 1.5));
      plate.rect(0, b * bandH, CANVAS, bandH + 1);
    }
    for (var i = 0; i < plan.ground.length; i++) drawMark(plate, plan.ground[i]);
  }

  function resetDeposit() {
    frameIndex = 0;
    cursor = 0;
    completed = false;
    lastLayerShown = -1;
    sheenPhase = 0;
  }

  function completeInstantly(instance) {
    if (!plan || !plate) return;
    for (var i = 0; i < plan.marks.length; i++) drawMark(plate, plan.marks[i]);
    cursor = plan.marks.length;
    frameIndex = plan.totalFrames;
    completed = true;
    if (instance) {
      instance.redraw();
      instance.noLoop();
    }
  }

  function rebuild() {
    buildPlan();
    if (!sketch) return;
    paintPlate();
    resetDeposit();
    if (reducedMotion && !manualPlay) {
      completeInstantly(sketch);
    } else if (paused) {
      sketch.redraw();
    }
    updateStatus(true);
  }

  function scheduleRebuild() {
    if (rebuildQueued) return;
    rebuildQueued = true;
    window.requestAnimationFrame(function () {
      rebuildQueued = false;
      rebuild();
    });
  }

  /* ── 动画 ─────────────────────────────────────────────── */
  function reveal() {
    var marks = plan.marks;
    while (cursor < marks.length && marks[cursor].rev <= frameIndex) {
      drawMark(plate, marks[cursor]);
      cursor++;
    }
    if (cursor >= marks.length) {
      completed = true;
      if (sketch) sketch.frameRate(30);
      updateStatus(true);
      return;
    }
    frameIndex++;
    var layerNow = currentLayer();
    if (layerNow !== lastLayerShown) {
      lastLayerShown = layerNow;
      updateStatus(true);
    }
  }

  function drawSheen(p) {
    var t = ((sheenPhase % 1) + 1) % 1;
    var center = CANVAS + 340 - t * (CANVAS + 680);
    var half = 200;
    var steps = 64;
    var band = (2 * half) / steps;
    var col = plan.sheen;
    p.noStroke();
    for (var i = 0; i < steps; i++) {
      var y = center - half + (i + 0.5) * band;
      if (y < -band || y > CANVAS + band) continue;
      var d = (y - center) / half;
      var a = Math.max(0, 1 - d * d);
      if (a <= 0.002) continue;
      p.fill(col[0], col[1], col[2], a * 9);
      p.rect(0, y - band / 2 - 0.5, CANVAS, band + 1);
    }
  }

  /* ── 操作 ─────────────────────────────────────────────── */
  function setPaused(next) {
    paused = next;
    if (sketch) {
      if (paused) sketch.noLoop();
      else sketch.loop();
    }
    syncControls();
    updateStatus(true);
  }

  function replay() {
    manualPlay = true;
    paused = false;
    resetDeposit();
    if (sketch) {
      paintPlate();
      sketch.frameRate(60);
      sketch.loop();
    }
    syncControls();
    updateStatus(true);
  }

  function applySeed(value) {
    if (!Number.isSafeInteger(value) || value < SEED_MIN || value > SEED_MAX) {
      els.seedInput.setAttribute('aria-invalid', 'true');
      els.status.textContent = '种子需为 1 至 2147483647 之间的整数。';
      return;
    }
    els.seedInput.removeAttribute('aria-invalid');
    params.seed = value;
    els.seedInput.value = String(value);
    manualPlay = false;
    rebuild();
    syncControls();
  }

  function setSliderValue(slider, raw) {
    var min = Number(slider.getAttribute('aria-valuemin'));
    var max = Number(slider.getAttribute('aria-valuemax'));
    var step = Number(slider.dataset.step);
    var clamped = Math.min(max, Math.max(min, raw));
    var stepped = min + Math.round((clamped - min) / step) * step;
    params[slider.dataset.param] = Number(stepped.toFixed(4));
    syncControls();
    scheduleRebuild();
  }

  function exportPNG() {
    if (!sketch || !plan || !plate) {
      els.status.textContent = '作品尚未就绪，暂时无法导出。';
      return;
    }
    var g = sketch.createGraphics(CANVAS, CANVAS);
    g.pixelDensity(1);
    g.background(plan.paper[0], plan.paper[1], plan.paper[2]);
    g.image(plate, 0, 0);
    var url;
    try {
      url = g.canvas.toDataURL('image/png');
    } catch (err) {
      g.remove();
      els.status.textContent = '导出失败：当前浏览器拒绝了画布导出。';
      return;
    }
    var a = document.createElement('a');
    a.href = url;
    a.download = 'tidal-archive-seed-' + plan.seed + (completed ? '' : '-layer' + currentLayer()) + '.png';
    document.body.appendChild(a);
    a.click();
    a.remove();
    g.remove();
    els.status.textContent = 'PNG 已导出 · 种子 ' + plan.seed + ' · ' +
      (completed ? '完整档案' : '当前进度第 ' + currentLayer() + '/' + plan.layers + ' 层');
  }

  /* ── 事件绑定 ─────────────────────────────────────────── */
  els.sliders.forEach(function (slider) {
    var fromPointer = function (event) {
      var rect = slider.getBoundingClientRect();
      var min = Number(slider.getAttribute('aria-valuemin'));
      var max = Number(slider.getAttribute('aria-valuemax'));
      var ratio = rect.width ? (event.clientX - rect.left) / rect.width : 0;
      setSliderValue(slider, min + (max - min) * ratio);
    };
    slider.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      slider.setPointerCapture(event.pointerId);
      slider.focus();
      fromPointer(event);
    });
    slider.addEventListener('pointermove', function (event) {
      if (slider.hasPointerCapture(event.pointerId)) fromPointer(event);
    });
    slider.addEventListener('pointerup', function (event) {
      if (slider.hasPointerCapture(event.pointerId)) slider.releasePointerCapture(event.pointerId);
    });
    slider.addEventListener('keydown', function (event) {
      var step = Number(slider.dataset.step);
      var current = params[slider.dataset.param];
      var next = null;
      switch (event.key) {
        case 'ArrowLeft': case 'ArrowDown': next = current - step; break;
        case 'ArrowRight': case 'ArrowUp': next = current + step; break;
        case 'PageDown': next = current - step * 10; break;
        case 'PageUp': next = current + step * 10; break;
        case 'Home': next = Number(slider.getAttribute('aria-valuemin')); break;
        case 'End': next = Number(slider.getAttribute('aria-valuemax')); break;
        default: return;
      }
      event.preventDefault();
      setSliderValue(slider, next);
    });
  });

  els.palettes.forEach(function (btn) {
    btn.addEventListener('click', function () {
      params.palette = btn.dataset.palette;
      syncControls();
      scheduleRebuild();
    });
  });

  els.seedJump.addEventListener('click', function () {
    applySeed(Number(els.seedInput.value));
  });
  els.seedInput.addEventListener('change', function () {
    applySeed(Number(els.seedInput.value));
  });
  els.seedInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      applySeed(Number(els.seedInput.value));
    }
  });
  els.seedPrev.addEventListener('click', function () {
    applySeed(params.seed <= SEED_MIN ? SEED_MAX : params.seed - 1);
  });
  els.seedNext.addEventListener('click', function () {
    applySeed(params.seed >= SEED_MAX ? SEED_MIN : params.seed + 1);
  });
  els.seedRandom.addEventListener('click', function () {
    applySeed(Math.floor(Math.random() * SEED_MAX) + 1);
  });

  els.replay.addEventListener('click', replay);
  els.reset.addEventListener('click', function () {
    params = Object.assign({}, DEFAULTS, { seed: params.seed });
    manualPlay = false;
    syncControls();
    rebuild();
    els.status.textContent = '参数已重置 · 种子 ' + params.seed + ' 保留';
  });
  els.pause.addEventListener('click', function () {
    if (!sketch) return;
    setPaused(!paused);
  });
  els.exportBtn.addEventListener('click', exportPNG);

  motionQuery.addEventListener('change', function (event) {
    reducedMotion = event.matches;
    updateStatus(true);
  });

  /* ── 启动 ─────────────────────────────────────────────── */
  syncControls();

  if (typeof window.p5 !== 'function' || typeof window.TidalCore === 'undefined') {
    els.status.textContent = '本地 p5 运行时或生成核心缺失，请确认 vendor/ 与 js/ 目录完整。';
    document.querySelectorAll('button, [role="slider"]').forEach(function (control) {
      control.setAttribute('aria-disabled', 'true');
      if (control.tagName === 'BUTTON') control.disabled = true;
    });
    return;
  }

  buildPlan();

  sketch = new window.p5(function (p) {
    p.setup = function () {
      var canvas = p.createCanvas(CANVAS, CANVAS);
      canvas.parent('canvas-container');
      p.pixelDensity(1);
      p.frameRate(60);
      p.strokeCap('round');
      p.strokeJoin('round');

      plate = p.createGraphics(CANVAS, CANVAS);
      plate.pixelDensity(1);
      plate.strokeCap('round');
      plate.strokeJoin('round');
      paintPlate();

      if (reducedMotion) {
        // 尊重系统偏好：不自动播放沉积过程，直接呈现完整档案
        completeInstantly(null);
        instantPending = true;
        paused = false;
      } else {
        resetDeposit();
      }
      syncControls();
      updateStatus(true);
    };

    p.draw = function () {
      if (!plan) return;
      if (!completed) reveal();
      p.background(plan.paper[0], plan.paper[1], plan.paper[2]);
      p.image(plate, 0, 0);
      if (completed && !reducedMotion) {
        sheenPhase += 0.0016;
        drawSheen(p);
      }
      if (instantPending) {
        instantPending = false;
        p.noLoop();
      }
    };
  });
})();
