// A fixed transparent canvas must not consume clicks in its empty area.
// Cursor checks also cover macOS ignoring events before the first hover.
export class InputRegions {
  constructor(window, screen) {
    this.window = window;
    this.screen = screen;
    this.regions = [];
    this.interactive = false;
    this.lastPointer = '';
    window.setIgnoreMouseEvents(true, { forward: true });
    this.timer = setInterval(() => this.syncCursor(), 24);
    this.timer.unref();
  }
  update(regions) {
    if (!Array.isArray(regions)) return false;
    const next = regions.slice(0, 64).filter(rect => rect && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(rect[key])) && rect.width > 0 && rect.height > 0)
      .map(({ x, y, width, height }) => ({ x, y, width, height }));
    if (JSON.stringify(next) === JSON.stringify(this.regions)) return false;
    this.regions = next;
    this.syncCursor();
    return true;
  }
  syncCursor() {
    if (this.window.isDestroyed() || !this.window.isVisible()) return;
    const bounds = this.window.getBounds(), cursor = this.screen.getCursorScreenPoint();
    const x = cursor.x - bounds.x, y = cursor.y - bounds.y;
    const pointerKey = `${x},${y}`;
    if (pointerKey !== this.lastPointer) {
      this.lastPointer = pointerKey;
      this.window.webContents?.send('quick:pointer-position', { x, y });
    }
    const inside = this.regions.some(rect => x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height);
    if (inside === this.interactive) return;
    this.interactive = inside;
    this.window.setIgnoreMouseEvents(!inside, { forward: true });
  }
  dispose() { clearInterval(this.timer); }
}
