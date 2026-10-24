/* p5 scaffolding: replace the flow-field algorithm with the artwork's concept. */
'use strict';
const initialParams = {
  seed: 12345, particleCount: 1500, flowSpeed: 0.5,
  noiseScale: 0.005, trailLength: 8,
  colorPalette: ['#db7448', '#3677aa', '#657e49'],
};
let params = structuredClone(initialParams);
let particles = [];
const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
let paused = motionQuery.matches;
let sketch;
const status = document.getElementById('art-status');
const seedInput = document.getElementById('seed-input');
const pauseButton = document.getElementById('pause');

function synchronizeControls() {
  seedInput.value = String(params.seed);
  document.querySelectorAll('[data-param]').forEach((slider) => {
    const value = params[slider.dataset.param];
    const min = Number(slider.getAttribute('aria-valuemin'));
    const max = Number(slider.getAttribute('aria-valuemax'));
    slider.setAttribute('aria-valuenow', String(value));
    slider.style.setProperty('--position', `${100 * (value - min) / (max - min)}%`);
    document.getElementById(`${slider.dataset.param}-value`).value = String(value);
  });
  params.colorPalette.forEach((value, index) => {
    const input = document.getElementById(`color${index + 1}`);
    input.value = value;
    input.removeAttribute('aria-invalid');
  });
  pauseButton.textContent = paused ? 'Resume' : 'Pause';
  pauseButton.setAttribute('aria-pressed', String(paused));
}

function initializeSystem(p) {
  p.randomSeed(params.seed);
  p.noiseSeed(params.seed);
  particles = Array.from({ length: params.particleCount }, () => ({
    x: p.random(p.width), y: p.random(p.height),
    ink: p.color(p.random(params.colorPalette)),
  }));
  p.background('#f4f0e7');
  status.textContent = `Seed ${params.seed}${paused ? ' · paused' : ''}`;
}

function regenerate() {
  synchronizeControls();
  if (sketch) {
    initializeSystem(sketch);
    if (paused) sketch.redraw();
  }
}

function changeSeed(value) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 2147483647) {
    seedInput.setAttribute('aria-invalid', 'true');
    status.textContent = 'Enter a whole seed from 1 to 2147483647.';
    return;
  }
  seedInput.removeAttribute('aria-invalid');
  params.seed = value;
  regenerate();
}

function setSliderValue(slider, value) {
  const min = Number(slider.getAttribute('aria-valuemin'));
  const max = Number(slider.getAttribute('aria-valuemax'));
  const step = Number(slider.dataset.step);
  const clamped = Math.min(max, Math.max(min, value));
  params[slider.dataset.param] = Number((min + Math.round((clamped - min) / step) * step).toFixed(6));
  regenerate();
}

document.querySelectorAll('[data-param]').forEach((slider) => {
  const fromPointer = (event) => {
    const rect = slider.getBoundingClientRect();
    const min = Number(slider.getAttribute('aria-valuemin'));
    const max = Number(slider.getAttribute('aria-valuemax'));
    setSliderValue(slider, min + (max - min) * (event.clientX - rect.left) / rect.width);
  };
  slider.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    slider.setPointerCapture(event.pointerId);
    slider.focus();
    fromPointer(event);
  });
  slider.addEventListener('pointermove', (event) => {
    if (slider.hasPointerCapture(event.pointerId)) fromPointer(event);
  });
  slider.addEventListener('keydown', (event) => {
    const step = Number(slider.dataset.step);
    const current = params[slider.dataset.param];
    const values = {
      ArrowLeft: current - step, ArrowDown: current - step,
      ArrowRight: current + step, ArrowUp: current + step,
      PageDown: current - 10 * step, PageUp: current + 10 * step,
      Home: Number(slider.getAttribute('aria-valuemin')),
      End: Number(slider.getAttribute('aria-valuemax')),
    };
    if (event.key in values) {
      event.preventDefault();
      setSliderValue(slider, values[event.key]);
    }
  });
});

seedInput.addEventListener('change', () => changeSeed(Number(seedInput.value)));
document.getElementById('previous-seed').addEventListener('click', () => changeSeed(Math.max(1, params.seed - 1)));
document.getElementById('next-seed').addEventListener('click', () => changeSeed(params.seed === 2147483647 ? 1 : params.seed + 1));
document.getElementById('random-seed').addEventListener('click', () => changeSeed(Math.floor(Math.random() * 2147483647) + 1));
document.getElementById('regenerate').addEventListener('click', regenerate);
document.getElementById('reset').addEventListener('click', () => {
  params = structuredClone(initialParams);
  regenerate();
});
for (let index = 0; index < 3; index++) {
  const input = document.getElementById(`color${index + 1}`);
  input.addEventListener('change', () => {
    if (!/^#[0-9a-f]{6}$/i.test(input.value)) {
      input.setAttribute('aria-invalid', 'true');
      status.textContent = 'Use a six-digit hex color, for example #3677aa.';
      return;
    }
    params.colorPalette[index] = input.value;
    regenerate();
  });
}
function updatePause() {
  synchronizeControls();
  if (!sketch) return;
  if (paused) sketch.noLoop();
  else sketch.loop();
  status.textContent = `Seed ${params.seed}${paused ? ' · paused' : ''}`;
}
pauseButton.addEventListener('click', () => { paused = !paused; updatePause(); });
motionQuery.addEventListener('change', (event) => { paused = event.matches; updatePause(); });
document.getElementById('download').addEventListener('click', () => {
  if (sketch) sketch.saveCanvas(`art-seed-${params.seed}`, 'png');
});

synchronizeControls();
if (typeof window.p5 !== 'function') {
  status.textContent = 'Local p5 runtime is missing. Copy vendor/p5.min.js with the viewer.';
  document.querySelectorAll('button, [role="slider"]').forEach((control) => {
    control.setAttribute('aria-disabled', 'true');
    if (control.tagName === 'BUTTON') control.disabled = true;
  });
} else {
  sketch = new window.p5((p) => {
    p.setup = () => {
      p.createCanvas(1200, 1200).parent('canvas-container');
      p.pixelDensity(1);
      initializeSystem(p);
      if (paused) p.noLoop();
    };
    p.draw = () => {
      p.noStroke();
      p.fill(244, 240, 231, params.trailLength);
      p.rect(0, 0, p.width, p.height);
      p.strokeWeight(1.2);
      particles.forEach((particle) => {
        const angle = p.noise(particle.x * params.noiseScale, particle.y * params.noiseScale) * p.TWO_PI * 2;
        const nextX = particle.x + Math.cos(angle) * params.flowSpeed * 3;
        const nextY = particle.y + Math.sin(angle) * params.flowSpeed * 3;
        p.stroke(particle.ink);
        p.line(particle.x, particle.y, nextX, nextY);
        particle.x = (nextX + p.width) % p.width;
        particle.y = (nextY + p.height) % p.height;
      });
    };
  });
}
