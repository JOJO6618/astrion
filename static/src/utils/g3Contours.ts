// Quartic superellipse: independent contours for dialogs and smaller buttons.
export function g3Masks(width: number, height: number, radius: number) {
  const scale = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  const w = Math.max(1, Math.round(width * scale));
  const h = Math.max(1, Math.round(height * scale));
  const r = Math.min(radius * scale, w / 2, h / 2);
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.fillStyle = 'white';
  ctx.fillRect(0, 0, w, h);
  const fill = ctx.getImageData(0, 0, w, h);
  const outline = ctx.getImageData(0, 0, w, h);
  const clamp = (value: number) => Math.max(0, Math.min(1, value));
  const edge = (y: number, inset: number) => {
    if (y < inset) return Infinity;
    const run = r - inset;
    if (run <= 0 || y >= r) return inset;
    return inset + run * (1 - Math.pow(Math.max(0, 1 - Math.pow(1 - (y - inset) / run, 4)), 0.25));
  };
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const index = (y * w + x) * 4 + 3;
      outline.data[index] = Math.round(
        255 *
          (1 -
            clamp(Math.min(x + 1 - scale, w - scale - x)) *
              clamp(Math.min(y + 1 - scale, h - scale - y)))
      );
    }
  }
  for (let y = 0; y < Math.ceil(r); y++) {
    for (let x = 0; x < Math.ceil(r); x++) {
      let outer = 0;
      let inner = 0;
      for (let i = 0; i < 8; i++) {
        outer += clamp(x + 1 - edge(y + (i + 0.5) / 8, 0));
        inner += clamp(x + 1 - edge(y + (i + 0.5) / 8, scale));
      }
      for (const [px, py] of [
        [x, y],
        [w - 1 - x, y],
        [x, h - 1 - y],
        [w - 1 - x, h - 1 - y]
      ]) {
        const index = (py * w + px) * 4 + 3;
        fill.data[index] = Math.round((255 * outer) / 8);
        outline.data[index] = Math.round((255 * Math.max(0, outer - inner)) / 8);
      }
    }
  }
  ctx.putImageData(fill, 0, 0);
  const mask = canvas.toDataURL();
  ctx.putImageData(outline, 0, 0);
  return { mask, outline: canvas.toDataURL() };
}

export function observeG3(root: HTMLElement) {
  const sizes = new WeakMap<Element, string>();
  const paint = (element: HTMLElement) => {
    const width = element.offsetWidth;
    const height = element.offsetHeight;
    const radius = Number(element.dataset.g3 || 18) * 2;
    const key = `${width}:${height}:${radius}:${window.devicePixelRatio}`;
    if (!width || !height || sizes.get(element) === key) return;
    const images = g3Masks(width, height, radius);
    if (!images) return;
    element.style.setProperty('--g3-mask', `url("${images.mask}")`);
    element.style.setProperty('--g3-outline', `url("${images.outline}")`);
    element.dataset.g3Ready = 'true';
    sizes.set(element, key);
  };
  const elements = [root, ...root.querySelectorAll<HTMLElement>('[data-g3]')];
  const observer = new ResizeObserver((entries) =>
    entries.forEach((entry) => paint(entry.target as HTMLElement))
  );
  elements.forEach((element) => {
    paint(element);
    observer.observe(element);
  });
  return () => observer.disconnect();
}
