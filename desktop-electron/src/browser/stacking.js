// 每个宿主窗口各自的叠层：网页可见时宿主 renderer 透明铺底，DOM 浮层出现时把宿主抬到网页之上。
export class BrowserStacking {
  constructor(host) { this.host = host; this.raised = false; this.transparent = false; this.background = null; this.capturing = false; this.restoreFocus = null; this.findOpen = false; this.findFocus = null; }
  apply(active) {
    const host = this.host;
    if (host.window.isDestroyed() || host.view.webContents.isDestroyed()) return;
    const pageVisible = !!active?.view && !active.view.webContents.isDestroyed() && active.view.getVisible();
    const raised = pageVisible && host.overlayRaised;
    const capturing = raised && host.overlayCapturesInput !== false;
    const findOpen = pageVisible && host.focusFind === true;
    if (findOpen && !this.findOpen) {
      this.findFocus = { id: active.id, focused: active.view.webContents.isFocused() };
      host.view.webContents.focus();
    } else if (!findOpen && this.findOpen) {
      if (!capturing && pageVisible && this.findFocus?.focused && this.findFocus.id === active.id && host.view.webContents.isFocused()) active.view.webContents.focus();
      this.findFocus = null;
    }
    this.findOpen = findOpen;
    if (capturing && !this.capturing) {
      this.restoreFocus = { id: active.id, focused: active.view.webContents.isFocused() };
      host.view.webContents.focus();
    }
    const target = pageVisible && !raised ? active.view : host.view;
    if (host.window.contentView.children.at(-1) !== target) host.window.contentView.addChildView(target);
    const background = pageVisible ? '#00000000' : host.manager.surfaceColor;
    if (background !== this.background) {
      host.view.setBackgroundColor(background);
      this.background = background;
    }
    this.transparent = pageVisible;
    if (!capturing && this.capturing) {
      if (pageVisible && this.restoreFocus?.focused && this.restoreFocus.id === active.id) active.view.webContents.focus();
      this.restoreFocus = null;
    }
    this.raised = raised;
    this.capturing = capturing;
    if (!raised) host.relayPointer = null;
  }
}
