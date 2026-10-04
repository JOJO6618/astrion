export class QuickVisibility {
  constructor(window, beforeHide = () => {}) {
    this.window = window;
    this.beforeHide = beforeHide;
    this.requested = false;
    this.timer = null;
  }
  show() {
    clearTimeout(this.timer);
    this.requested = true;
    this.window.show();
    this.window.focus();
    this.window.webContents.send('quick:show');
  }
  hide() {
    if (!this.requested) return;
    this.requested = false;
    this.beforeHide();
    if (this.window.isDestroyed()) return;
    this.window.webContents.send('quick:will-hide');
    // Normally the renderer acknowledges animationend. Still allow Escape to
    // close the window if a renderer crashes or an animation is interrupted.
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.finish(), 300);
  }
  finish() {
    if (this.requested || this.window.isDestroyed()) return;
    clearTimeout(this.timer);
    this.window.hide();
  }
  dispose() { clearTimeout(this.timer); }
}
