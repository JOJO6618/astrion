// Rectangular WindowServer bounds share Electron's top-left desktop coordinates.
// Subtract every foreground window from both painting and the hit-test region.
function intersection(a, b) {
  const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.width, b.x + b.width);
  const bottom = Math.min(a.y + a.height, b.y + b.height);
  return right > x && bottom > y ? { x, y, width: right - x, height: bottom - y } : null;
}
function subtract(rect, obstacle) {
  const overlap = intersection(rect, obstacle);
  if (!overlap) return [rect];
  return [
    { x: rect.x, y: rect.y, width: rect.width, height: overlap.y - rect.y },
    { x: rect.x, y: overlap.y + overlap.height, width: rect.width,
      height: rect.y + rect.height - overlap.y - overlap.height },
    { x: rect.x, y: overlap.y, width: overlap.x - rect.x, height: overlap.height },
    { x: overlap.x + overlap.width, y: overlap.y,
      width: rect.x + rect.width - overlap.x - overlap.width, height: overlap.height }
  ].filter(item => item.width > 0 && item.height > 0);
}
function validRect(rect) {
  return rect && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key]))
    && rect.width > 0 && rect.height > 0;
}

class WindowCaptureButtons {
  constructor() {
    this.root = document.getElementById('window-buttons');
    this.buttons = new Map();
    this.locked = false;
    this.capturing = false;
    this.snapshot = null;
    this.scheduled = 0;
    this.entryCount = 0;
    this.dismissing = false;
  }
  update(snapshot) {
    if (this.dismissing) return;
    this.snapshot = snapshot;
    this.capturing = Boolean(snapshot.capturing);
    document.body.classList.toggle('capturing', this.capturing);
    if (!this.locked) this.render();
  }
  select(id) {
    if (this.capturing || this.dismissing) return;
    this.capturing = true;
    document.body.classList.add('capturing');
    window.capture.selectWindow(id);
  }
  lock() { this.locked = true; }
  unlock() {
    this.locked = false;
    cancelAnimationFrame(this.scheduled);
    this.scheduled = requestAnimationFrame(() => { if (!this.locked) this.render(); });
  }
  create(id) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'window-capture';
    let press = null;
    button.addEventListener('pointerdown', event => {
      event.stopPropagation();
      if (event.button !== 0 || this.capturing) return;
      this.lock();
      press = { x: event.clientX, y: event.clientY, pointer: event.pointerId };
      button.setPointerCapture(event.pointerId);
    });
    button.addEventListener('pointerup', event => {
      event.stopPropagation();
      const previous = press;
      press = null;
      if (previous && previous.pointer === event.pointerId
        && Math.hypot(event.clientX - previous.x, event.clientY - previous.y) < 5
        && document.elementFromPoint(event.clientX, event.clientY) === button) {
        this.select(id);
      }
      this.unlock();
    });
    button.addEventListener('pointercancel', () => { press = null; this.unlock(); });
    button.addEventListener('click', event => {
      event.preventDefault(); event.stopPropagation();
      if (event.detail === 0) this.select(id);
    });
    button.addEventListener('animationend', event => {
      if (event.animationName === 'window-button-reveal') button.classList.remove('revealing');
    });
    this.root.append(button);
    this.buttons.set(id, button);
    return button;
  }
  dismiss(ticket) {
    if (this.dismissing) return;
    this.dismissing = true;
    this.locked = true;
    cancelAnimationFrame(this.scheduled);
    document.body.classList.add('dismissing');
    const buttons = [...this.buttons.values()]
      .filter(button => !button.hidden && button.dataset.entered)
      .sort((left, right) => Number(left.dataset.entryOrder) - Number(right.dataset.entryOrder));
    let pending = buttons.length;
    let finished = false;
    let timer;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      window.capture.dismissed(ticket);
    };
    if (!pending) { finish(); return; }
    buttons.forEach((button, rank) => {
      // Freeze each button's current opacity and geometry; the prompt's scale
      // animation must not move or shrink screenshot buttons toward its center.
      button.style.setProperty('--exit-opacity', getComputedStyle(button).opacity);
      button.style.setProperty('--exit-delay', `${rank * 50}ms`);
      button.disabled = true;
      button.tabIndex = -1;
      button.addEventListener('animationend', event => {
        if (event.animationName === 'window-button-dismiss' && --pending === 0) finish();
      });
      button.classList.remove('revealing');
      button.classList.add('dismissing');
    });
    timer = setTimeout(finish, (buttons.length - 1) * 50 + 210);
  }
  render() {
    if (!this.snapshot || this.locked || this.dismissing) return;
    const { windows = [], display, exclude = [], label = '', colors = {} } = this.snapshot;
    if (!validRect(display)) return;
    for (const [key, value] of Object.entries(colors)) document.documentElement.style.setProperty(key, value);
    const kept = new Set();
    const foreground = [];
    let visibleRank = 0;
    for (const target of windows) {
      if (!validRect(target)) continue;
      if (target.candidate && label && intersection(target, display)) {
        const button = this.buttons.get(target.id) || this.create(target.id);
        kept.add(target.id);
        button.textContent = label.replace('{app}', target.app);
        button.setAttribute('aria-label', button.textContent);
        button.hidden = false;
        const x = target.x + 10 - display.x, y = target.y + 6 - display.y;
        Object.assign(button.style, { left: `${x}px`, top: `${y}px`, maxWidth: `${Math.min(280, target.width - 20)}px` });
        const bounds = { x, y, width: button.offsetWidth, height: button.offsetHeight };
        let fragments = [intersection(bounds, { x: 0, y: 0, width: display.width, height: display.height })].filter(Boolean);
        const obstacles = foreground.map(rect => ({ ...rect, x: rect.x - display.x, y: rect.y - display.y }));
        for (const obstacle of [...obstacles, ...exclude]) {
          fragments = fragments.flatMap(rect => subtract(rect, obstacle));
          if (!fragments.length) break;
        }
        button.hidden = !fragments.length;
        button.tabIndex = fragments.length ? 0 : -1;
        if (fragments.length) {
          if (!button.dataset.entered) {
            button.dataset.entered = 'true';
            button.dataset.entryOrder = String(this.entryCount++);
            button.style.setProperty('--entry-delay', `${50 + visibleRank * 50}ms`);
            button.classList.add('revealing');
          }
          visibleRank++;
        }
        const paths = fragments.map(rect => {
          const left = rect.x - x, top = rect.y - y;
          return `M ${left} ${top} h ${rect.width} v ${rect.height} h ${-rect.width} Z`;
        }).join(' ');
        // Chromium applies clip-path to pointer hit testing as well as painting.
        button.style.clipPath = paths ? `path("${paths}")` : 'inset(100%)';
      }
      foreground.push(target);
    }
    for (const [id, button] of this.buttons) {
      if (!kept.has(id)) { button.remove(); this.buttons.delete(id); }
    }
  }
}
window.captureButtons = new WindowCaptureButtons();
window.capture.windows(snapshot => window.captureButtons.update(snapshot));
window.capture.onHide(ticket => window.captureButtons.dismiss(ticket));
